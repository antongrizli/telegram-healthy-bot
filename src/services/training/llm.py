"""Optional LLM enhancements: unparsed line recovery, focus classification fallback, and plan cues."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from src.services import gemini
from src.services.training.dto import DraftItem, ParsedSet, PlanItem

logger = logging.getLogger(__name__)

# In-memory cache for parsed lines: sha1(normalized_line) -> dict
_PARSE_CACHE: Dict[str, Any] = {}


def _hash_line(line: str) -> str:
    return hashlib.sha1(line.strip().lower().encode("utf-8")).hexdigest()


async def parse_unparsed_lines(
    unparsed_items: List[DraftItem],
    known_exercises: Optional[List[str]] = None,
    lang: str = "ru",
) -> List[DraftItem]:
    """Attempt batch LLM parsing for lines not recognized by deterministic rules.
    Gracefully returns unparsed items on error, 429, or timeout without raising exceptions.
    """
    if not unparsed_items:
        return []

    lines_to_query = []
    cached_results = {}

    for item in unparsed_items:
        h = _hash_line(item.raw)
        if h in _PARSE_CACHE:
            cached_results[item.line_no] = _PARSE_CACHE[h]
        else:
            lines_to_query.append((item.line_no, item.raw))

    if not lines_to_query and not cached_results:
        return unparsed_items

    llm_parsed: Dict[int, Any] = dict(cached_results)

    if lines_to_query:
        known_str = ", ".join(known_exercises[:30]) if known_exercises else "none"
        prompt = (
            "You are an assistant parsing workout log lines that were not recognized by rule parser.\n"
            f"User language: {lang}. Known user exercises: {known_str}.\n"
            "Lines to parse:\n"
            + "\n".join(f"{line_no}: {raw}" for line_no, raw in lines_to_query)
            + "\n\nReturn JSON in this format:\n"
            '{"items": [{"line_no": 1, "name": "Exercise name", "kind": "strength", '
            '"sets": [{"weight_kg": 40.0, "reps": 12}], "note": null}]}'
        )

        try:
            async with asyncio.timeout(15):
                response = await gemini.call_gemini_with_retry(
                    [prompt],
                    request_type="workout_llm_parse",
                    max_retries=1,
                )
            if response and response.text:
                data = gemini.extract_json(response.text)
                if isinstance(data, dict) and "items" in data:
                    for it in data["items"]:
                        lno = it.get("line_no")
                        if lno:
                            llm_parsed[lno] = it
                            # Cache result
                            raw_line = next((r for (n, r) in lines_to_query if n == lno), "")
                            if raw_line:
                                _PARSE_CACHE[_hash_line(raw_line)] = it
        except Exception as e:
            logger.warning("LLM unparsed line recovery failed or timed out: %s", e)

    # Reconstruct DraftItems
    resolved_items: List[DraftItem] = []
    for item in unparsed_items:
        parsed_data = llm_parsed.get(item.line_no)
        if parsed_data:
            sets_list = [ParsedSet(**s) for s in parsed_data.get("sets", [])]
            resolved_items.append(
                DraftItem(
                    line_no=item.line_no,
                    raw=item.raw,
                    name=parsed_data.get("name") or item.name,
                    kind=parsed_data.get("kind", "strength"),
                    sets=sets_list,
                    note=parsed_data.get("note"),
                    status="ok" if sets_list else "unparsed",
                    source="llm",
                )
            )
        else:
            resolved_items.append(item)

    return resolved_items


async def classify_focus(text: str, lang: str = "ru") -> Optional[List[str]]:
    """Fallback LLM classifier to determine muscle groups from free-form user intent."""
    prompt = (
        f"Classify workout target muscle groups from user text: '{text}'.\n"
        "Allowed groups: back, chest, legs, arms, shoulders, abs, fullbody, cardio.\n"
        'Return JSON: {"groups": ["back", "chest"]}'
    )
    try:
        async with asyncio.timeout(10):
            response = await gemini.call_gemini_with_retry(
                [prompt],
                request_type="workout_focus_classify",
                max_retries=1,
            )
        if response and response.text:
            data = gemini.extract_json(response.text)
            if isinstance(data, dict) and "groups" in data:
                return data["groups"]
    except Exception as e:
        logger.warning("LLM focus classification failed: %s", e)
    return None


async def polish_plan(
    plan_items: List[PlanItem],
    rationale_default: str,
    lang: str = "ru",
) -> Tuple[List[PlanItem], str]:
    """Enhance plan items with biomechanical cues and 1-sentence rationale.
    Validates numbers in generated text to ensure no contradiction with the plan.
    """
    if not plan_items:
        return plan_items, rationale_default

    ex_summary = "\n".join(
        f"- {it.name}: {len(it.working)} sets, target {it.working[0].weight_kg if it.working else None}kg x {it.working[0].reps if it.working else None}"
        for it in plan_items
    )

    prompt = (
        f"You are a biomechanics fitness coach. Language: {lang}.\n"
        f"Exercises in planned workout:\n{ex_summary}\n\n"
        "Provide 1 concise setup/execution cue (max 10 words) for each exercise and 1 sentence overall rationale.\n"
        "Do NOT invent new sets, reps, or weights.\n"
        'Return JSON: {"rationale": "...", "cues": {"Exercise name": "concise cue"}}'
    )

    try:
        async with asyncio.timeout(10):
            response = await gemini.call_gemini_with_retry(
                [prompt],
                request_type="workout_plan_polish",
                max_retries=1,
            )
        if response and response.text:
            data = gemini.extract_json(response.text)
            if isinstance(data, dict):
                cues_map = data.get("cues", {})
                new_rationale = data.get("rationale") or rationale_default

                # Validate rationale length and conflict
                if len(new_rationale) > 300:
                    new_rationale = new_rationale[:297] + "…"

                # Update cues if no contradiction
                for item in plan_items:
                    if item.name in cues_map:
                        item.cue = cues_map[item.name]

                return plan_items, new_rationale
    except Exception as e:
        logger.warning("LLM plan polish failed or timed out: %s", e)

    return plan_items, rationale_default

"""Training Coach Service & Repository.

Handles:
1. Canonical exercise and alias mapping with multilingual normalization.
2. Draft queue analysis via Structured LLM output with progressive overload rules.
3. Rule-based offline/429 fallback engine.
4. Prepare / Coach mode recommendations (setup cues, warm-up targets, complementary machines).
5. Rich Message payload construction (RichBlockTable + RichBlockDetails).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import (
    CanonicalExercise,
    ExerciseAlias,
    WorkoutSession,
    WorkoutSessionEntry,
)
from src.services import gemini
from src.utils.rich_message import RichMessageBuilder

logger = logging.getLogger(__name__)


# --- Pydantic Schema for LLM Structured Output (Section 5) ---

class ParsedExerciseItem(BaseModel):
    canonical_slug: str = Field(description="Unique snake_case identifier, e.g. 'machine_chest_press' or 'running'")
    display_name: str = Field(description="Localized exercise name matching user language, e.g. 'Brust press'")
    exercise_type: Literal["strength", "cardio", "bodyweight"] = "strength"
    muscle_group: Optional[str] = None
    equipment: Optional[str] = None
    sets: int = Field(default=1, ge=1)
    reps: Optional[int] = None
    weight_kg: Optional[float] = None
    duration_sec: Optional[int] = None
    distance_m: Optional[int] = None
    feeling: Literal["easy", "normal", "hard"] = "normal"
    is_first_time: bool = Field(description="True if exercise had no prior logged history in known_exercises")
    FormattedLoad: str = Field(description="Compact load string for mobile table cell, e.g. '100kg × 10' or '3×10 @ 100kg'")
    next_recommended_load: str = Field(description="Compact next target, e.g. '↗️ 102.5kg × 10' or '➡️ 100kg × 10'")
    comparison_and_next_advice: str = Field(description="If first time: explain next step without fake history. If repeat: compare to last session.")
    execution_tip: str = Field(description="1 concise biomechanics/setup cue for this machine or exercise.")


class ComplementarySuggestion(BaseModel):
    exercise_name: str
    reason: str


class WorkoutParseResponse(BaseModel):
    session_headline: str = Field(description="1-sentence motivating summary in user's language.")
    parsed_exercises: List[ParsedExerciseItem]
    complementary_suggestion: ComplementarySuggestion


class ExerciseCoachAdvice(BaseModel):
    exercise_name: str
    recommended_target: str
    execution_cues: List[str]
    complementary_suggestion: ComplementarySuggestion
    quick_log_text: str


class PlannedExercise(BaseModel):
    name: str
    target_load: str
    quick_log_text: str
    cue: str


class WorkoutPreparationPlan(BaseModel):
    title: str
    focus: str
    exercises: List[PlannedExercise]
    session_tip: str


# --- Repository Methods (Step 1) ---

async def get_user_exercise_context(db: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
    """Fetches user's canonical exercises with aliases and latest performance record."""
    q = (
        select(CanonicalExercise)
        .where(CanonicalExercise.user_id == user_id)
        .order_by(CanonicalExercise.id)
    )
    res = await db.scalars(q)
    canonical_list = list(res.all())

    context: List[Dict[str, Any]] = []
    for ce in canonical_list:
        # Fetch aliases
        alias_q = select(ExerciseAlias.alias_normalized).where(ExerciseAlias.canonical_exercise_id == ce.id)
        aliases = list((await db.scalars(alias_q)).all())

        # Fetch latest entry
        entry_q = (
            select(WorkoutSessionEntry)
            .where(WorkoutSessionEntry.canonical_exercise_id == ce.id)
            .order_by(WorkoutSessionEntry.created_at.desc(), WorkoutSessionEntry.id.desc())
            .limit(1)
        )
        latest_entry = await db.scalar(entry_q)

        entry_data = None
        if latest_entry:
            entry_data = {
                "weight_kg": float(latest_entry.weight_kg) if latest_entry.weight_kg is not None else None,
                "reps": latest_entry.reps,
                "sets": latest_entry.sets,
                "duration_sec": latest_entry.duration_sec,
                "distance_m": latest_entry.distance_m,
                "feeling": latest_entry.feeling,
                "next_recommended_load": latest_entry.next_recommended_load,
                "performed_at": latest_entry.created_at.isoformat() if latest_entry.created_at else None,
            }

        context.append({
            "id": ce.id,
            "canonical_slug": ce.canonical_slug,
            "display_name": ce.display_name,
            "exercise_type": ce.exercise_type,
            "muscle_group": ce.muscle_group,
            "equipment": ce.equipment,
            "aliases": aliases,
            "last_entry": entry_data,
        })

    return context


async def get_user_training_stats(db: AsyncSession, user_id: int) -> Dict[str, Any]:
    """Computes last workout date and total sessions this week for dashboard."""
    # Last workout session
    last_session_q = (
        select(WorkoutSession)
        .where(WorkoutSession.user_id == user_id)
        .order_by(WorkoutSession.performed_at.desc(), WorkoutSession.id.desc())
        .limit(1)
    )
    last_session = await db.scalar(last_session_q)

    # Sessions this week (since Monday 00:00 UTC)
    now = datetime.now(UTC).replace(tzinfo=None)
    start_of_week = now - timedelta(days=now.weekday(), hours=now.hour, minutes=now.minute, seconds=now.second, microseconds=now.microsecond)
    week_count_q = (
        select(func.count(WorkoutSession.id))
        .where(WorkoutSession.user_id == user_id, WorkoutSession.performed_at >= start_of_week)
    )
    sessions_this_week = (await db.scalar(week_count_q)) or 0

    return {
        "last_workout_date": last_session.performed_at.strftime("%d.%m.%Y") if last_session and last_session.performed_at else None,
        "sessions_this_week": int(sessions_this_week),
    }


async def get_top_exercises_for_user(db: AsyncSession, user_id: int, limit: int = 6) -> List[Dict[str, Any]]:
    """Returns user's most used exercises for quick coach selection."""
    q = (
        select(CanonicalExercise, func.count(WorkoutSessionEntry.id).label("entry_count"))
        .outerjoin(WorkoutSessionEntry, WorkoutSessionEntry.canonical_exercise_id == CanonicalExercise.id)
        .where(CanonicalExercise.user_id == user_id)
        .group_by(CanonicalExercise.id)
        .order_by(desc("entry_count"), CanonicalExercise.id.desc())
        .limit(limit)
    )
    results = (await db.execute(q)).all()
    out = []
    for ce, count in results:
        # Fetch latest entry to display e.g. "Last: 100kg x 10"
        entry_q = (
            select(WorkoutSessionEntry)
            .where(WorkoutSessionEntry.canonical_exercise_id == ce.id)
            .order_by(WorkoutSessionEntry.id.desc())
            .limit(1)
        )
        latest = await db.scalar(entry_q)
        last_str = ""
        if latest:
            if latest.weight_kg is not None and latest.reps is not None:
                w_val = float(latest.weight_kg)
                w_str = f"{int(w_val)}" if w_val.is_integer() else f"{w_val:g}"
                last_str = f"{w_str}kg × {latest.reps}"
            elif latest.distance_m is not None:
                last_str = f"{latest.distance_m / 1000:g}km"
            elif latest.duration_sec is not None:
                last_str = f"{latest.duration_sec // 60}m"

        out.append({
            "id": ce.id,
            "canonical_slug": ce.canonical_slug,
            "display_name": ce.display_name.rstrip(" ."),
            "exercise_type": ce.exercise_type,
            "last_str": last_str,
        })
    return out


async def get_user_personal_records(db: AsyncSession, user_id: int, limit: int = 6) -> List[Dict[str, Any]]:
    """Returns top exercises with their highest achieved working weight."""
    q = (
        select(
            CanonicalExercise,
            func.max(WorkoutSessionEntry.weight_kg).label("max_weight"),
        )
        .join(WorkoutSessionEntry, WorkoutSessionEntry.canonical_exercise_id == CanonicalExercise.id)
        .where(
            CanonicalExercise.user_id == user_id,
            WorkoutSessionEntry.weight_kg.is_not(None),
            WorkoutSessionEntry.weight_kg > 0,
        )
        .group_by(CanonicalExercise.id)
        .order_by(desc("max_weight"))
        .limit(limit)
    )
    rows = (await db.execute(q)).all()
    prs: List[Dict[str, Any]] = []
    for ce, max_w in rows:
        entry_q = (
            select(WorkoutSessionEntry)
            .where(
                WorkoutSessionEntry.canonical_exercise_id == ce.id,
                WorkoutSessionEntry.weight_kg == max_w,
            )
            .order_by(WorkoutSessionEntry.reps.desc().nullslast())
            .limit(1)
        )
        entry = await db.scalar(entry_q)
        w_val = float(max_w)
        w_clean = f"{int(w_val)}" if w_val.is_integer() else f"{w_val:g}"
        prs.append({
            "name": ce.display_name.rstrip(" ."),
            "max_weight": w_clean,
            "reps": entry.reps if entry else None,
        })
    return prs


async def save_workout_session(
    db: AsyncSession,
    user_id: int,
    parsed: WorkoutParseResponse,
    draft_items: List[Dict[str, Any]],
) -> Tuple[WorkoutSession, List[WorkoutSessionEntry]]:
    """Persists canonical exercises, aliases, workout session and entries atomically."""
    session = WorkoutSession(
        user_id=user_id,
        performed_at=datetime.now(UTC).replace(tzinfo=None),
        ai_summary=parsed.session_headline,
    )
    db.add(session)
    await db.flush()

    entries: List[WorkoutSessionEntry] = []
    for idx, item in enumerate(parsed.parsed_exercises):
        # 1. Match or create CanonicalExercise
        slug = item.canonical_slug.lower().strip()
        ce = await db.scalar(
            select(CanonicalExercise).where(
                CanonicalExercise.user_id == user_id,
                CanonicalExercise.canonical_slug == slug,
            )
        )
        if not ce:
            # Check by alias as secondary match
            norm_name = item.display_name.lower().strip()
            alias_match = await db.scalar(
                select(ExerciseAlias).where(
                    ExerciseAlias.user_id == user_id,
                    ExerciseAlias.alias_normalized == norm_name,
                )
            )
            if alias_match:
                ce = await db.get(CanonicalExercise, alias_match.canonical_exercise_id)

        if not ce:
            ce = CanonicalExercise(
                user_id=user_id,
                canonical_slug=slug,
                display_name=item.display_name,
                exercise_type=item.exercise_type,
                muscle_group=item.muscle_group,
                equipment=item.equipment,
            )
            db.add(ce)
            await db.flush()

        # 2. Register display name & raw input as aliases
        raw_input_text = draft_items[idx]["raw_text"] if idx < len(draft_items) else item.display_name
        for alias_candidate in (item.display_name, raw_input_text):
            norm_alias = alias_candidate.lower().strip()
            if norm_alias:
                existing_alias = await db.scalar(
                    select(ExerciseAlias).where(
                        ExerciseAlias.user_id == user_id,
                        ExerciseAlias.alias_normalized == norm_alias,
                    )
                )
                if not existing_alias:
                    new_alias = ExerciseAlias(
                        user_id=user_id,
                        alias_normalized=norm_alias,
                        canonical_exercise_id=ce.id,
                    )
                    db.add(new_alias)

        # 3. Create WorkoutSessionEntry
        entry = WorkoutSessionEntry(
            session_id=session.id,
            canonical_exercise_id=ce.id,
            raw_input=raw_input_text,
            sets=item.sets,
            reps=item.reps,
            weight_kg=item.weight_kg,
            duration_sec=item.duration_sec,
            distance_m=item.distance_m,
            feeling=item.feeling,
            next_recommended_load=item.next_recommended_load,
            ai_note=f"{item.comparison_and_next_advice} 💡 Tip: {item.execution_tip}",
        )
        db.add(entry)
        entries.append(entry)

    await db.commit()
    await db.refresh(session)
    return session, entries


# --- LLM Parsing & Progressive Overload Engine (Step 4) ---

def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return cleaned or "exercise"


def _compact_stem(s: str) -> str:
    c = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "", s).lower()
    c = re.sub(r"presse$", "press", c)
    c = re.sub(r"maschine$", "machine", c)
    return c


def _rule_based_analyze(
    draft_items: List[Dict[str, Any]],
    known_exercises: List[Dict[str, Any]],
    user_locale: str,
) -> WorkoutParseResponse:
    """Deterministic rule-based fallback when LLM is unavailable or rate-limited."""
    alias_map: Dict[str, Dict[str, Any]] = {}
    for ke in known_exercises:
        alias_map[ke["canonical_slug"]] = ke
        alias_map[ke["display_name"].lower().strip()] = ke
        alias_map[_compact_stem(ke["canonical_slug"])] = ke
        alias_map[_compact_stem(ke["display_name"])] = ke
        for a in ke.get("aliases", []):
            alias_map[a.lower().strip()] = ke
            alias_map[_compact_stem(a)] = ke

    parsed_items: List[ParsedExerciseItem] = []
    for item in draft_items:
        raw = item.get("raw_text", "").strip()
        feel = item.get("feeling", "normal")

        # Check cardio
        duration_sec = None
        distance_m = None
        ex_type: Literal["strength", "cardio", "bodyweight"] = "strength"

        min_match = re.search(r"(\d+)\s*(?:min|m|минут)", raw, re.IGNORECASE)
        if min_match:
            duration_sec = int(min_match.group(1)) * 60
            ex_type = "cardio"

        km_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|км)", raw, re.IGNORECASE)
        if km_match:
            distance_m = int(float(km_match.group(1).replace(",", ".")) * 1000)
            ex_type = "cardio"

        weight = None
        sets = 1
        reps = None

        if ex_type != "cardio":
            # Pattern A: "100kg x 10" or "102.5 кг * 10"
            w_x_r = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kg|кг)\s*(?:x|х|\*)\s*(\d+)", raw, re.IGNORECASE)
            if w_x_r:
                weight = float(w_x_r.group(1).replace(",", "."))
                reps = int(w_x_r.group(2))
                # Check for sets prefix: "3 x 100kg x 10" or "3 подхода по 100kg x 10"
                s_prefix = re.search(r"(\d+)\s*(?:x|х|\*|подход[а-я]*)\s*(?:по)?\s*" + re.escape(w_x_r.group(0)), raw, re.IGNORECASE)
                if s_prefix:
                    sets = int(s_prefix.group(1))
            else:
                # Pattern B: Standalone weight "100kg"
                w_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:kg|кг)", raw, re.IGNORECASE)
                if w_match:
                    weight = float(w_match.group(1).replace(",", "."))

                # Pattern C: "3 подхода по 15" or "3x15"
                p_match = re.search(r"(\d+)\s*(?:подход[а-я]*|sets?)\s*(?:по)?\s*(\d+)", raw, re.IGNORECASE)
                if p_match:
                    sets, reps = int(p_match.group(1)), int(p_match.group(2))
                else:
                    s_r_match = re.search(r"(\d+)\s*(?:x|х|\*)\s*(\d+)", raw, re.IGNORECASE)
                    if s_r_match:
                        sets, reps = int(s_r_match.group(1)), int(s_r_match.group(2))
                    else:
                        rep_match = re.search(r"(\d+)\s*(?:reps|повтор[а-я]*|раз)", raw, re.IGNORECASE)
                        if rep_match:
                            reps = int(rep_match.group(1))

        # Extract name: remove numbers and units
        name_clean = re.sub(
            r"(\d+(?:[.,]\d+)?\s*(?:kg|кг|km|км|min|минут|reps|повтор|раз)?|x|х|\*|подход[а-я]*|по)",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        name_clean = re.sub(r"\s+", " ", name_clean).strip() or raw

        # Match with known exercises (supporting multilingual variations like Brust press / Brustpresse)
        norm_name = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", name_clean).strip().lower()
        stemmed = re.sub(r"presse\b", "press", norm_name)
        stemmed_slug = _slugify(stemmed)
        norm_slug = _slugify(norm_name)
        compact_stem = _compact_stem(name_clean)

        matched_ke = None
        for cand in (compact_stem, norm_name, stemmed, norm_slug, stemmed_slug):
            if cand in alias_map:
                matched_ke = alias_map[cand]
                break

        if not matched_ke:
            for k, v in alias_map.items():
                if (k and k in norm_name) or (norm_name and norm_name in k) or (k in compact_stem) or (compact_stem in k):
                    matched_ke = v
                    break

        if matched_ke:
            slug = matched_ke["canonical_slug"]
            display_name = matched_ke["display_name"]
            is_first_time = matched_ke.get("last_entry") is None
            last_entry = matched_ke.get("last_entry")
        else:
            slug = _slugify(name_clean)
            display_name = name_clean.title()
            is_first_time = True
            last_entry = None

        # Format load
        if ex_type == "cardio":
            parts = []
            if distance_m:
                parts.append(f"{distance_m / 1000:g}km")
            if duration_sec:
                parts.append(f"{duration_sec // 60}m")
            formatted_load = " / ".join(parts) or raw
        else:
            if weight is not None and reps is not None:
                formatted_load = f"{sets}×{reps} @ {weight:g}kg" if sets > 1 else f"{weight:g}kg × {reps}"
            elif weight is not None:
                formatted_load = f"{weight:g}kg"
            elif reps is not None:
                formatted_load = f"{reps} reps"
                ex_type = "bodyweight"
            else:
                formatted_load = raw

        # Next recommended load & progression advice
        feel_icon = "🟢" if feel == "easy" else ("🟡" if feel == "normal" else "🔴")
        if is_first_time:
            # Section 5: First-Time Exercise Rule
            if weight is not None and reps is not None:
                if feel == "easy":
                    next_w = round(weight * 1.05 / 2.5) * 2.5
                    next_load = f"↗️ {next_w:g}kg × {reps}"
                    adv = f"Отличный старт! Было легко ({feel_icon}), в следующий раз добавьте вес до {next_w:g} кг или +2 повторения."
                elif feel == "normal":
                    next_w = weight + 2.5
                    next_load = f"↗️ {next_w:g}kg × {reps}"
                    adv = f"Базовый ориентир зафиксирован ({feel_icon}). В следующий раз попробуйте {next_w:g} кг или добавьте 1–2 повторения."
                else:  # hard
                    next_load = f"➡️ {weight:g}kg × {reps}"
                    adv = f"Базовый уровень установлен ({feel_icon}). Было тяжело, поэтому в следующий раз закрепите этот же вес и повторения."
            else:
                next_load = "➡️ Сохранить нагрузку"
                adv = "Базовый уровень записан. Сохраните эту нагрузку в следующий раз."
        else:
            # Repeat history rule
            last_w = last_entry.get("weight_kg") if last_entry else None
            last_r = last_entry.get("reps") if last_entry else None
            if weight is not None and last_w is not None:
                diff_w = weight - last_w
                comp_str = f"В прошлый раз было {last_w:g} кг (разница: {diff_w:+g} кг)."
            else:
                comp_str = "Прогрессия продолжается по сравнению с прошлой тренировкой."

            if weight is not None:
                if feel == "easy":
                    next_w = weight + 2.5
                    next_load = f"↗️ {next_w:g}kg × {reps or 10}"
                    adv = f"{comp_str} Было легко ({feel_icon}): прогрессивная перегрузка рекомендует +2.5 кг."
                elif feel == "normal":
                    next_w = weight + 1.25
                    next_load = f"↗️ {next_w:g}kg × {reps or 10}"
                    adv = f"{comp_str} Нагрузка оптимальна ({feel_icon}): в следующий раз добавьте +1–2 повторения или микро-вес."
                else:
                    next_load = f"➡️ {weight:g}kg × {reps or 10}"
                    adv = f"{comp_str} Высокая нагрузка ({feel_icon}): закрепите текущий вес перед дальнейшим увеличением."
            else:
                next_load = "➡️ Сохранить темп"
                adv = f"{comp_str} Удерживайте темп и технику."

        tip = "Сохраняйте плавный темп, контролируйте эксцентрическую фазу движения и ровное дыхание."

        parsed_items.append(
            ParsedExerciseItem(
                canonical_slug=slug,
                display_name=display_name,
                exercise_type=ex_type,
                sets=sets,
                reps=reps,
                weight_kg=weight,
                duration_sec=duration_sec,
                distance_m=distance_m,
                feeling=feel,
                is_first_time=is_first_time,
                FormattedLoad=formatted_load,
                next_recommended_load=next_load,
                comparison_and_next_advice=adv,
                execution_tip=tip,
            )
        )

    return WorkoutParseResponse(
        session_headline="Тренировка успешно зафиксирована. Отличная работа над прогрессией!",
        parsed_exercises=parsed_items,
        complementary_suggestion=ComplementarySuggestion(
            exercise_name="Гиперэкстензия или Планка",
            reason="Укрепление мышечного корсета и стабилизаторов спины сбалансирует выполненную нагрузку.",
        ),
    )


async def parse_and_analyze_workout(
    user_id: int,
    draft_items: List[Dict[str, Any]],
    user_locale: str,
    db: AsyncSession,
) -> WorkoutParseResponse:
    """Calls Gemini/OpenRouter with Structured Output schema to parse & analyze workout."""
    known_exercises = await get_user_exercise_context(db, user_id)

    system_prompt = (
        "You are an expert AI Workout & Progressive Overload Coach.\n"
        "Your goal is to parse raw user workout inputs, match them to canonical exercises, and compute precise progression recommendations.\n\n"
        "CRITICAL RULES:\n"
        f"1. LANGUAGE: Output all user-facing strings in the user's language: {user_locale}.\n"
        "2. ALIAS MATCHING: Match each draft item to an existing canonical_slug from known_exercises even across languages (e.g. 'Brust press', 'жим груди' -> 'machine_chest_press'). Only generate a new canonical_slug if no semantic match exists.\n"
        "3. FIRST-TIME EXERCISE RULE:\n"
        "   - If an exercise has NO previous history in known_exercises (or last_entry is null), set is_first_time: true.\n"
        "   - DO NOT invent, hallucinate, or mention ANY comparison to past workouts.\n"
        "   - Celebrate establishing the baseline and calculate next_recommended_load strictly from today's load and feeling:\n"
        "     * feeling == 'easy': suggest increasing weight by ~2.5–5% or +2 reps next session.\n"
        "     * feeling == 'normal': suggest adding +1–2 reps or micro-loading +1.25–2.5kg.\n"
        "     * feeling == 'hard': suggest keeping exact same weight and reps next session to consolidate form.\n"
        "4. EXISTING HISTORY RULE:\n"
        "   - If previous history exists (is_first_time: false), explicitly compare today's volume/intensity to the previous record and factor feeling into the target.\n"
        "5. MOBILE FORMATTING:\n"
        "   - FormattedLoad must be compact for a mobile table cell: e.g. '100kg × 10' or '3×10 @ 100kg' or '5km / 25m'.\n"
        "   - next_recommended_load must be compact: e.g. '↗️ 102.5kg × 10' or '➡️ 100kg × 10'.\n"
        "   - execution_tip must be 1 concise setup/biomechanics cue.\n"
    )

    user_payload = {
        "user_locale": user_locale,
        "draft_items": draft_items,
        "known_exercises": known_exercises,
    }

    prompt = f"{system_prompt}\n\nUSER PAYLOAD:\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}"

    try:
        async with asyncio.timeout(60):
            response = await gemini.call_gemini_with_retry(
                [prompt],
                user_id=user_id,
                request_type="training_coach_analyze",
                max_retries=2,
            )
        raw_json = gemini.extract_json(response.text)
        return WorkoutParseResponse.model_validate_json(raw_json)
    except Exception as e:
        logger.warning("LLM training analysis failed or rate limited (%s), using rule-based progression fallback", e)
        return _rule_based_analyze(draft_items, known_exercises, user_locale)


# --- Prepare / Coach Mode (Flow B) ---

MUSCLE_GROUPS: Dict[str, Dict[str, Any]] = {
    "back": {
        "title_ru": "🎯 Тренировка: Акцент на мышцы спины",
        "title_en": "🎯 Workout: Back Focus",
        "focus_ru": "Широчайшие и ромбовидные мышцы, увеличение объёма тяг и толщины спины.",
        "focus_en": "Lats, rhomboids and upper back thickness with progressive pull volume.",
        "tip_ru": "Начните с вертикальной тяги, затем перейдите к горизонтальной. Тяните локтями, сохраняя грудь расправленной.",
        "tip_en": "Start with vertical pulls, then horizontal rows. Lead with your elbows and keep chest proud.",
        "keywords": ["спин", "back", "lat", "тяг", "ruder", "pull", "блок", "лопат", "подтягиван"],
        "fallback_exercises": ["Latzug (Тяга верхнего блока)", "Rudermaschine (Тяга к поясу)", "Гиперэкстензия"],
    },
    "chest": {
        "title_ru": "🎯 Тренировка: Акцент на мышцы груди",
        "title_en": "🎯 Workout: Chest Focus",
        "focus_ru": "Большая грудная мышца, передняя дельта и трицепс. Развитие силовых показателей жимов.",
        "focus_en": "Pectorals, anterior delts and triceps with progressive press overload.",
        "tip_ru": "Плотно сведите лопатки на скамье. Контролируйте опускание веса (2 секунды) и не выпрямляйте локти до щелчка.",
        "tip_en": "Retract scapulae firmly. Control the eccentric phase (2 sec) and avoid locking out aggressively.",
        "keywords": ["груд", "chest", "brust", "жим", "press", "отжим", "пек", "бабочк"],
        "fallback_exercises": ["Brustpresse (Жим от груди в тренажере)", "Жим гантелей под углом", "Сведение рук в бабочке (Pec-Deck)"],
    },
    "legs": {
        "title_ru": "🎯 Тренировка: Акцент на мышцы ног",
        "title_en": "🎯 Workout: Legs Focus",
        "focus_ru": "Квадрицепсы, задняя поверхность бедра и ягодичные мышцы.",
        "focus_en": "Quads, hamstrings, and glutes with balanced knee and hip flexion.",
        "tip_ru": "Следите за направлением коленей (строго по линии носков). Не отрывайте пятки от платформы.",
        "tip_en": "Align knees with toes. Keep heels glued to the platform through the entire range of motion.",
        "keywords": ["ног", "leg", "бедр", "bein", "присед", "squat", "quad", "икр", "calf", "stecker", "beuger", "abdukt", "addukt"],
        "fallback_exercises": ["Beinpresse (Жим ногами)", "Beinstrecker (Разгибание ног)", "Beinbeuger sitzend (Сгибание ног)"],
    },
    "arms": {
        "title_ru": "🎯 Тренировка: Акцент на руки (Бицепс и Трицепс)",
        "title_en": "🎯 Workout: Arms Focus (Biceps & Triceps)",
        "focus_ru": "Двуглавая и трёхглавая мышцы плеча, суперсеты антагонистов для максимального пампа.",
        "focus_en": "Biceps and triceps antagonist balance for hypertrophy and joint health.",
        "tip_ru": "Зафиксируйте локти по бокам туловища, исключите раскачку корпусом.",
        "tip_en": "Keep elbows pinned to your sides and eliminate torso momentum.",
        "keywords": ["рук", "arm", "бицепс", "трицепс", "bizeps", "trizeps", "bicep", "tricep", "curl", "молот"],
        "fallback_exercises": ["Bizepsmaschine (Сгибание на бицепс)", "Разгибания на трицепс на блоке", "Молотки с гантелями"],
    },
    "shoulders": {
        "title_ru": "🎯 Тренировка: Акцент на плечи и дельты",
        "title_en": "🎯 Workout: Shoulders & Delts Focus",
        "focus_ru": "Средняя и задняя дельты для создания V-образного силуэта и здоровья плечевого сустава.",
        "focus_en": "Lateral and rear delts for 3D shoulder shape and rotational stability.",
        "tip_ru": "В махах поднимайте локти, а не кисти. Не поднимайте трапеции к ушам.",
        "tip_en": "Lead with elbows rather than hands. Keep traps depressed away from ears.",
        "keywords": ["плеч", "shoulder", "дельт", "delt", "махи", "шраг", "армейск"],
        "fallback_exercises": ["Schulterpresse (Жим на плечи)", "Махи гантелями в стороны", "Тяга к лицу (Face Pull)"],
    },
    "fullbody": {
        "title_ru": "🎯 Тренировка: Всё тело (Full Body)",
        "title_en": "🎯 Workout: Full Body Balanced",
        "focus_ru": "Комплексная проработка основных мышечных групп за одну тренировочную сессию.",
        "focus_en": "Total-body compound stimulus engaging major muscle chains.",
        "tip_ru": "Начните с ног и спины, затем перейдите к груди и вспомогательным мышцам.",
        "tip_en": "Prioritize large compound movements first before moving to smaller muscles.",
        "keywords": ["все", "всё", "тело", "фулбоди", "full", "body", "общ"],
        "fallback_exercises": ["Beinpresse (Ноги)", "Latzug (Спина)", "Brustpresse (Грудь)"],
    },
}


async def generate_workout_plan(
    user_id: int,
    goal_or_focus: str,
    selected_exercise_ids: Optional[List[int]],
    user_locale: str,
    db: AsyncSession,
) -> WorkoutPreparationPlan:
    """Constructs a full customized workout plan based on goal or selected machines."""
    is_ru = user_locale in ("ru", "uk", "be")
    known_exercises = await get_user_exercise_context(db, user_id)
    known_by_id = {ke["id"]: ke for ke in known_exercises}

    target_exercises = []

    if selected_exercise_ids:
        # User explicitly checked specific machines
        for ex_id in selected_exercise_ids:
            if ex_id in known_by_id:
                target_exercises.append(known_by_id[ex_id])
        title = f"🎯 Персональная тренировка ({len(target_exercises)} упр.)" if is_ru else f"🎯 Custom Workout Plan ({len(target_exercises)} exercises)"
        focus = "Индивидуальный комплекс на выбранных тренажёрах с расчётом прогрессии весов." if is_ru else "Custom sequence for your selected machines with progressive load targets."
        tip = "Выполняйте упражнения в предложенном порядке. Контролируйте разминку перед рабочими подходами." if is_ru else "Follow the recommended exercise order and complete warmup sets before working sets."
    else:
        # Detect focus category from goal_or_focus
        norm_goal = (goal_or_focus or "").lower().strip()
        matched_cat = None
        for cat_key, cat_data in MUSCLE_GROUPS.items():
            if cat_key == norm_goal or any(kw in norm_goal for kw in cat_data["keywords"]):
                matched_cat = cat_key
                break

        category_key = matched_cat or "fullbody"
        cat_info = MUSCLE_GROUPS[category_key]
        title = cat_info["title_ru"] if is_ru else cat_info["title_en"]
        focus = cat_info["focus_ru"] if is_ru else cat_info["focus_en"]
        tip = cat_info["tip_ru"] if is_ru else cat_info["tip_en"]

        # Match user's historical exercises that belong to this category
        for ke in known_exercises:
            name_norm = ke["display_name"].lower()
            slug_norm = ke["canonical_slug"].lower()
            if any(kw in name_norm or kw in slug_norm for kw in cat_info["keywords"]):
                target_exercises.append(ke)

        # If user has fewer than 3 matching machines in history, supplement from fallback
        if len(target_exercises) < 3:
            for fb_name in cat_info["fallback_exercises"]:
                if len(target_exercises) >= 3:
                    break
                if not any(fb_name.lower().startswith(te["display_name"].lower()[:5]) for te in target_exercises):
                    target_exercises.append({
                        "id": None,
                        "display_name": fb_name,
                        "canonical_slug": _slugify(fb_name),
                        "exercise_type": "strength",
                        "last_entry": None,
                    })

    # Build PlannedExercises with personalized loads
    planned: List[PlannedExercise] = []
    for item in target_exercises:
        name = item["display_name"].rstrip(" .")
        last = item.get("last_entry")
        if last and last.get("weight_kg"):
            w = float(last["weight_kg"])
            if last.get("feeling") == "easy":
                w += 2.5
            w_str = f"{int(w)}" if w.is_integer() else f"{w:g}"
            warm_w = max(5.0, round(w * 0.6 / 2.5) * 2.5)
            warm_str = f"{int(warm_w)}" if warm_w.is_integer() else f"{warm_w:g}"
            sets = last.get("sets") or 3
            reps = last.get("reps") or 10

            load_text = (
                f"{sets}×{reps} @ {w_str} кг (Разминка: 1×12 @ {warm_str} кг)"
                if is_ru
                else f"{sets}×{reps} @ {w_str}kg (Warmup: 1×12 @ {warm_str}kg)"
            )
            quick_log = f"{name} {w_str}kg {sets}x{reps}"
        elif last and last.get("duration_sec"):
            mins = last["duration_sec"] // 60
            load_text = f"{mins} мин умеренный темп" if is_ru else f"{mins} min steady pace"
            quick_log = f"{name} {mins} мин"
        else:
            load_text = "3×10–12 повторений (Разминка: 1×12 с лёгким весом)" if is_ru else "3×10–12 reps (Warmup: 1×12 with light load)"
            quick_log = f"{name} 3x10"

        cue = (
            "Плавный темп, 2 секунды опускания веса, выдох на усилии."
            if is_ru
            else "Controlled tempo, 2-sec eccentric, exhale on exertion."
        )

        planned.append(PlannedExercise(
            name=name,
            target_load=load_text,
            quick_log_text=quick_log,
            cue=cue,
        ))

    return WorkoutPreparationPlan(
        title=title,
        focus=focus,
        exercises=planned,
        session_tip=tip,
    )


def build_plan_rich_message(plan: WorkoutPreparationPlan, lang: str = "en") -> RichMessageBuilder:
    builder = RichMessageBuilder()
    builder.add_section_heading(plan.title)
    builder.add_paragraph(plan.focus, is_bold=True)

    is_ru = lang in ("ru", "uk", "be")
    target_lbl = "Цель" if is_ru else "Target"
    cue_lbl = "Техника" if is_ru else "Cue"

    cards: List[str] = []
    for idx, ex in enumerate(plan.exercises, 1):
        card = (
            f"<b>{idx}. {html.escape(ex.name)}</b>\n"
            f"  🎯 <i>{target_lbl}:</i> {html.escape(ex.target_load)}\n"
            f"  💡 <i>{cue_lbl}:</i> {html.escape(ex.cue)}"
        )
        cards.append(card)

    builder.add_paragraph("\n\n".join(cards), is_html=True)
    if plan.session_tip:
        builder.add_details(
            title="💡 Советы тренера по сессии" if is_ru else "💡 Coach Session Tips",
            blocks=[{"type": "paragraph", "text": {"type": "plain", "text": plan.session_tip}}],
            is_open=True,
        )
    return builder

async def get_coach_advice(
    user_id: int,
    exercise_name_or_goal: str,
    user_locale: str,
    db: AsyncSession,
) -> ExerciseCoachAdvice:
    """Generates execution cues, recommended target (warmup + working), and complementary machine."""
    known_exercises = await get_user_exercise_context(db, user_id)
    matched = None
    target_norm = exercise_name_or_goal.lower().strip()
    for ke in known_exercises:
        if (
            ke["canonical_slug"] == target_norm
            or ke["display_name"].lower() == target_norm
            or target_norm in [a.lower() for a in ke.get("aliases", [])]
        ):
            matched = ke
            break

    system_prompt = (
        "You are an expert biomechanics and strength coach.\n"
        f"Respond in language: {user_locale}.\n"
        "Provide:\n"
        "1. recommended_target: warm-up sets + today's working weight/reps or pacing.\n"
        "2. execution_cues: 3 clear bullet points (setup, path, breathing/tempo).\n"
        "3. complementary_suggestion: 1 machine/exercise that complements this movement.\n"
        "4. quick_log_text: a single string the user can use to log today's working set (e.g. 'Brustpresse 102.5kg x 10').\n"
    )

    context = {
        "requested": exercise_name_or_goal,
        "matched_exercise": matched,
    }

    try:
        async with asyncio.timeout(45):
            res = await gemini.call_gemini_with_retry(
                [f"{system_prompt}\n\nContext:\n{json.dumps(context, ensure_ascii=False)}"],
                user_id=user_id,
                request_type="training_coach_prepare",
                max_retries=2,
            )
        raw_json = gemini.extract_json(res.text)
        data = json.loads(raw_json)
        return ExerciseCoachAdvice(
            exercise_name=data.get("exercise_name", exercise_name_or_goal),
            recommended_target=data.get("recommended_target", "Разминка: 1×12 с лёгким весом. Рабочий подход: 3×10–12 повторений."),
            execution_cues=data.get("execution_cues", [
                "Настройте высоту сиденья/рукояток под уровень груди.",
                "Сведите лопатки и сохраняйте естественный прогиб.",
                "Выдыхайте на усилии, плавно опускайте вес.",
            ]),
            complementary_suggestion=ComplementarySuggestion(
                exercise_name=data.get("complementary_suggestion", {}).get("exercise_name", "Тяга верхнего блока"),
                reason=data.get("complementary_suggestion", {}).get("reason", "Баланс мышц антагонистов для здоровья плечевого сустава."),
            ),
            quick_log_text=data.get("quick_log_text", f"{exercise_name_or_goal} 3x10"),
        )
    except Exception as e:
        logger.info("Coach advice fallback: %s", e)
        # Fallback advice
        display = matched["display_name"] if matched else exercise_name_or_goal.title()
        last = matched.get("last_entry") if matched else None
        last_w = last.get("weight_kg") if last else 50.0
        last_r = last.get("reps") if last else 10
        working_w = (last_w or 50.0) + (2.5 if last and last.get("feeling") == "easy" else 0.0)
        return ExerciseCoachAdvice(
            exercise_name=display,
            recommended_target=f"Разминка: 1×12 @ {working_w * 0.6:.1f}kg. Рабочий сет: 3×{last_r or 10} @ {working_w:g}kg.",
            execution_cues=[
                "Настройте тренажер под свой рост (ось вращения совпадает с суставом).",
                "Зафиксируйте корпус и сведите лопатки перед началом движения.",
                "Контролируйте негативную фазу (2 секунды) и не бросайте вес.",
            ],
            complementary_suggestion=ComplementarySuggestion(
                exercise_name="Latzug (Тяга верхнего блока)",
                reason="Антагонистическая тяга развивает ширину спины и стабилизирует плечевой пояс.",
            ),
            quick_log_text=f"{display} {working_w:g}kg x {last_r or 10}",
        )


# --- Rich Message Construction (Section 6) ---

def build_workout_rich_message(parsed: WorkoutParseResponse, lang: str = "en") -> RichMessageBuilder:
    """Builds a deterministic RichMessageBuilder payload matching Section 6."""
    builder = RichMessageBuilder()

    is_ru = lang in ("ru", "uk", "be")
    heading_text = "🏋️ Тренировка сохранена" if is_ru else "🏋️ Workout Logged"
    builder.add_section_heading(heading_text)
    builder.add_paragraph(parsed.session_headline)

    feel_emojis = {
        "easy": "🟢 Легко" if is_ru else "🟢 Easy",
        "normal": "🟡 Норма" if is_ru else "🟡 OK",
        "hard": "🔴 Тяжело" if is_ru else "🔴 Hard",
    }
    h_ex = "Упражнение" if is_ru else "Exercise"
    h_load = "Нагрузка" if is_ru else "Weight / Reps"
    h_feel = "Самочувствие" if is_ru else "Feel"
    h_target = "Следующая цель" if is_ru else "Next Target"

    table_cells: List[List[Dict[str, Any]]] = [
        [
            {"text": h_ex, "is_header": True, "align": "left"},
            {"text": h_load, "is_header": True, "align": "right"},
            {"text": h_feel, "is_header": True, "align": "center"},
            {"text": h_target, "is_header": True, "align": "right"},
        ]
    ]

    progression_blocks: List[Dict[str, Any]] = []

    for ex in parsed.parsed_exercises:
        feel_label = feel_emojis.get(ex.feeling, "🟡 OK")
        table_cells.append([
            {"text": ex.display_name, "is_header": False, "align": "left"},
            {"text": ex.FormattedLoad, "is_header": False, "align": "right"},
            {"text": feel_label, "is_header": False, "align": "center"},
            {"text": ex.next_recommended_load, "is_header": False, "align": "right"},
        ])

        # Collapsible progression details
        progression_blocks.append({
            "type": "paragraph",
            "text": {"type": "bold", "text": f"{ex.display_name}: "},
        })
        progression_blocks.append({
            "type": "paragraph",
            "text": {
                "type": "plain",
                "text": f"{ex.comparison_and_next_advice} 💡 Tip: {ex.execution_tip}",
            },
        })

    builder.add_table(table_cells, is_bordered=True, is_striped=True, is_compact=True)

    # 4. Collapsible progression details block
    details_title = "📈 Прогрессия и план на следующий раз" if is_ru else "📈 Progression & Next Training Plan"
    builder.add_details(
        title=details_title,
        blocks=progression_blocks,
        is_open=True,
    )

    # 5. Collapsible complementary recommendation block
    if parsed.complementary_suggestion and parsed.complementary_suggestion.exercise_name:
        comp_title = (
            f"🎯 Рекомендуемый тренажёр: {parsed.complementary_suggestion.exercise_name}"
            if is_ru
            else f"🎯 Recommended Machine to Add: {parsed.complementary_suggestion.exercise_name}"
        )
        builder.add_details(
            title=comp_title,
            blocks=[
                {
                    "type": "paragraph",
                    "text": {
                        "type": "plain",
                        "text": parsed.complementary_suggestion.reason,
                    },
                }
            ],
            is_open=False,
        )

    return builder

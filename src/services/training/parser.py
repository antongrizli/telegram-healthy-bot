"""Deterministic workout parser implementing rules R1 to R14, conjunction splitting, and shorthand."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.services.training.catalog import get_catalog
from src.services.training.dto import DraftItem, ParsedSet, Target
from src.services.training.normalize import clean_name, normalize_units_and_cross, replace_verbal_numbers

# Sets / Reps keywords patterns
RE_SETS_WORD = r"(?:подход\w*|сет\w*|sets?|sätze?|satz|serie\w*|сери\w*)"
RE_REPS_WORD = r"(?:раз\w*|повтор\w*|повт\w*|reps?|rep|wdh\w*|wiederholung\w*|mal\b)"
RE_WEIGHT_UNIT = r"(?:kg|кг|кгс|килограмм\w*|kilo\w*)"
RE_TIME_MIN = r"(?:мин\w*|min\w*)"
RE_TIME_SEC = r"(?:сек\w*|sec\w*|с\b|s\b)"
RE_DIST_KM = r"(?:км|km)\b"
RE_DIST_M = r"(?:м|m)\b"

RE_CONJUNCTION = re.compile(r"\s+(?:и|und|and|&|\+)\s+", re.IGNORECASE)


def _parse_time_str(time_str: str) -> Optional[int]:
    """Parse MM:SS or HH:MM:SS string to total seconds."""
    parts = time_str.split(":")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def split_conjunction(name: str, catalog=None, library_names: Optional[List[str]] = None) -> List[str]:
    """Split name by conjunction (и / und / and / & / +) if both parts match library or catalog."""
    if not RE_CONJUNCTION.search(name):
        return [name]

    parts = [p.strip() for p in RE_CONJUNCTION.split(name) if p.strip()]
    if len(parts) < 2:
        return [name]

    catalog = catalog or get_catalog()
    known = set(k.lower() for k in (library_names or []))

    def _matches(p: str) -> bool:
        plow = p.lower()
        if plow in known:
            return True
        return catalog.find_by_name(p) is not None

    if all(_matches(p) for p in parts):
        return [clean_name(p) for p in parts]

    return [name]


def parse_live_shorthand(text: str, current: Optional[Target] = None) -> Optional[List[ParsedSet]]:
    """Parse shorthand text in live logging scenario A.
    Examples:
    '40 10' -> 40kg x 10
    '12' -> current target weight x 12
    '40x10 40x8' -> two sets
    """
    s = normalize_units_and_cross(text.strip())
    # 1. Multiple sets like '40x10 40x8' or '40x10, 40x8'
    multi_matches = re.findall(r"(\d+(?:\.\d+)?)\s*x\s*(\d+)", s)
    if multi_matches:
        sets = []
        for w_str, r_str in multi_matches:
            sets.append(ParsedSet(weight_kg=float(w_str), reps=int(r_str)))
        return sets

    # 2. '40 10' (weight reps)
    match_two = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+)$", s)
    if match_two:
        return [ParsedSet(weight_kg=float(match_two.group(1)), reps=int(match_two.group(2)))]

    # 3. Single number '12' (reps with current target weight)
    if s.isdigit():
        reps = int(s)
        weight = current.weight if current else None
        return [ParsedSet(weight_kg=weight, reps=reps)]

    return None


def _extract_name_and_tail(raw_line: str, match_start: int, match_end: int) -> Tuple[str, Optional[str]]:
    """Split raw line into exercise name before match and note after match."""
    prefix = raw_line[:match_start].strip()
    suffix = raw_line[match_end:].strip()
    name = clean_name(prefix)
    note = clean_name(suffix) if suffix else None
    return name, note


def _hist_get(hist: Any, key: str, default: Any = None) -> Any:
    """Read a field from a history entry that may be a dict or an ExerciseHistory object."""
    if isinstance(hist, dict):
        return hist.get(key, default)
    return getattr(hist, key, default)


def parse_line(
    raw_line: str,
    line_no: int = 1,
    catalog=None,
    library_names: Optional[List[str]] = None,
    history_index: Optional[Dict[str, Any]] = None,
) -> List[DraftItem]:
    """Parse a single text line into one or more DraftItems according to rules R1..R14."""
    catalog = catalog or get_catalog()
    history_index = history_index or {}

    line = raw_line.strip()
    if not line:
        return []

    # Pre-normalization: verbal numbers, crosses, decimal comma, lbs conversion
    normalized = replace_verbal_numbers(line)
    normalized = normalize_units_and_cross(normalized)

    # -------------------------------------------------------------
    # Rule R0: the bot's own report format: "Brustpresse 40 кг · 3×15 Стабильно" (comment is optional)
    # -------------------------------------------------------------
    r0_match = re.match(
        r"^(.+?)\s+(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*[·•|]\s*(\d+)\s*x\s*(\d+)\b\s*(.*)$",
        normalized,
        re.IGNORECASE,
    )
    if r0_match:
        name = clean_name(r0_match.group(1))
        sets = [ParsedSet(weight_kg=float(r0_match.group(2)), reps=int(r0_match.group(4))) for _ in range(int(r0_match.group(3)))]
        note = r0_match.group(5).strip() or None
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R12: "Latzug как в прошлый раз" / "wie letztes Mal" / "same as last"
    # -------------------------------------------------------------
    r12_match = re.search(r"^(.*?)\s+(?:как в прошлый раз|wie letztes mal|same as last)\b", normalized, re.IGNORECASE)
    if r12_match:
        ex_name = clean_name(r12_match.group(1))
        hist = history_index.get(ex_name.lower())
        if hist and _hist_get(hist, "working_sets"):
            return [
                DraftItem(
                    line_no=line_no,
                    raw=raw_line,
                    name=ex_name,
                    kind="strength",
                    sets=[s for s in _hist_get(hist, "working_sets")],
                    status="ok",
                    source="rules",
                )
            ]

    # -------------------------------------------------------------
    # Rule R13: "Latzug +2.5" / "-5"
    # -------------------------------------------------------------
    r13_match = re.search(r"^(.*?)\s+([+-]\d+(?:\.\d+)?)$", normalized)
    if r13_match:
        ex_name = clean_name(r13_match.group(1))
        delta = float(r13_match.group(2))
        hist = history_index.get(ex_name.lower())
        if hist and _hist_get(hist, "working_sets"):
            shifted = [
                ParsedSet(
                    weight_kg=max(0.0, (s.weight_kg or 0.0) + delta),
                    reps=s.reps,
                    set_type=s.set_type,
                )
                for s in _hist_get(hist, "working_sets")
            ]
            return [
                DraftItem(
                    line_no=line_no,
                    raw=raw_line,
                    name=ex_name,
                    kind="strength",
                    sets=shifted,
                    status="ok",
                    source="rules",
                )
            ]

    # -------------------------------------------------------------
    # Rule R10: Bodyweight + added weight: "Подтягивания +10кг 3x8"
    # -------------------------------------------------------------
    r10_match = re.search(
        r"^(.*?)\s+\+(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")?\s*(?:x\s*)?(\d+)\s*x\s*(\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r10_match:
        name = clean_name(r10_match.group(1))
        added_w = float(r10_match.group(2))
        n_sets = int(r10_match.group(3))
        reps = int(r10_match.group(4))
        sets = [ParsedSet(weight_kg=None, added_weight_kg=added_w, reps=reps) for _ in range(n_sets)]
        return [
            DraftItem(
                line_no=line_no,
                raw=raw_line,
                name=name,
                kind="bodyweight",
                sets=sets,
                status="ok",
                source="rules",
            )
        ]

    # -------------------------------------------------------------
    # Rule R11: Dumbbells 2x20kg 3x10: "Жим гантелей 2x20кг 3x10"
    # -------------------------------------------------------------
    r11_match = re.search(
        r"^(.*?)\s+2\s*x\s*(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s+(\d+)\s*x\s*(\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r11_match:
        name = clean_name(r11_match.group(1))
        w = float(r11_match.group(2))
        n_sets = int(r11_match.group(3))
        reps = int(r11_match.group(4))
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return [
            DraftItem(
                line_no=line_no,
                raw=raw_line,
                name=name,
                kind="strength",
                sets=sets,
                status="ok",
                source="rules",
            )
        ]

    # -------------------------------------------------------------
    # Rule R1: "35кг x 4 подхода по 15 раз" / "35кг 4 подхода по 15" / "4 подхода по 15 раз 35кг"
    # -------------------------------------------------------------
    r1_pattern = (
        r"(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*(?:x\s*)?"
        r"(\d+)\s*" + RE_SETS_WORD + r"\s*(?:по\s*(\d+)(?:\s*" + RE_REPS_WORD + r")?|(\d+)\s*" + RE_REPS_WORD + r")"
    )
    r1_match = re.search(r1_pattern, normalized, re.IGNORECASE)
    if r1_match:
        w = float(r1_match.group(1))
        n_sets = int(r1_match.group(2))
        reps = int(r1_match.group(3) or r1_match.group(4))
        name, note = _extract_name_and_tail(normalized, r1_match.start(), r1_match.end())
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    r1b_pattern = (
        r"(\d+)\s*" + RE_SETS_WORD + r"\s*(?:по\s*(\d+)(?:\s*" + RE_REPS_WORD + r")?|(\d+)\s*" + RE_REPS_WORD + r")"
        r"\s*(?:x\s*|по\s*|@\s*)?(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")"
    )
    r1b_match = re.search(r1b_pattern, normalized, re.IGNORECASE)
    if r1b_match:
        n_sets = int(r1b_match.group(1))
        reps = int(r1b_match.group(2) or r1b_match.group(3))
        w = float(r1b_match.group(4))
        name, note = _extract_name_and_tail(normalized, r1b_match.start(), r1b_match.end())
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    r1c_match = re.search(
        r"^(.*?)\s+(\d+)\s*" + RE_SETS_WORD + r"\s*(?:по\s*(\d+)(?:\s*" + RE_REPS_WORD + r")?|(\d+)\s*" + RE_REPS_WORD + r")$",
        normalized,
        re.IGNORECASE,
    )
    if r1c_match:
        name = clean_name(r1c_match.group(1))
        n_sets = int(r1c_match.group(2))
        reps = int(r1c_match.group(3) or r1c_match.group(4))
        cat_entry = catalog.find_by_name(name)
        is_bodyweight = bool(cat_entry and cat_entry.get("kind") == "bodyweight")
        if is_bodyweight:
            sets = [ParsedSet(weight_kg=None, reps=reps) for _ in range(n_sets)]
            return _make_draft_items(name, sets, raw_line, line_no, None, "bodyweight", catalog, library_names)
        hist = history_index.get(name.lower())
        weight = _hist_get(hist, "top_weight") if hist else None
        sets = [ParsedSet(weight_kg=weight, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    # Weight + comma-separated reps: "Жим 20 кг: 10, 10, 8 повторений"
    comma_reps_match = re.search(
        r"^(.*?)\s+(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*[:\-]?\s*(\d+(?:\s*,\s*\d+)+)\s*(?:" + RE_REPS_WORD + r")?$",
        normalized,
        re.IGNORECASE,
    )
    if comma_reps_match:
        name = clean_name(comma_reps_match.group(1))
        w = float(comma_reps_match.group(2))
        reps_vals = [int(r.strip()) for r in comma_reps_match.group(3).split(",")]
        sets = [ParsedSet(weight_kg=w, reps=r) for r in reps_vals]
        return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R4: List of W x R: "60x10, 70x8, 80x6"
    # -------------------------------------------------------------
    r4_match = re.search(
        r"^(.*?)\s+((?:\d+(?:\.\d+)?\s*x\s*\d+\s*(?:,\s*|\s+)){2,}\d+(?:\.\d+)?\s*x\s*\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r4_match:
        name = clean_name(r4_match.group(1))
        pairs_str = r4_match.group(2)
        pairs = re.findall(r"(\d+(?:\.\d+)?)\s*x\s*(\d+)", pairs_str)
        if len(pairs) >= 2:
            sets = [ParsedSet(weight_kg=float(p[0]), reps=int(p[1])) for p in pairs]
            return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R5: W/W/W x R/R/R: "40/45/50 x 12/10/8"
    # -------------------------------------------------------------
    r5_match = re.search(
        r"^(.*?)\s+((?:\d+(?:\.\d+)?/)+\d+(?:\.\d+)?)\s*x\s*((?:\d+/)+\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r5_match:
        name = clean_name(r5_match.group(1))
        weights = [float(w) for w in r5_match.group(2).split("/")]
        reps_list = [int(r) for r in r5_match.group(3).split("/")]
        if len(weights) == len(reps_list):
            sets = [ParsedSet(weight_kg=w, reps=r) for w, r in zip(weights, reps_list)]
            return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R6: "3x12 @ 45" or "3x12 по 45"
    # -------------------------------------------------------------
    r6_match = re.search(
        r"^(.*?)\s+(\d+)\s*x\s*(\d+)\s*(?:@|по)\s*(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")?",
        normalized,
        re.IGNORECASE,
    )
    if r6_match:
        name = clean_name(r6_match.group(1))
        n_sets = int(r6_match.group(2))
        reps = int(r6_match.group(3))
        w = float(r6_match.group(4))
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R3: NUM x NUM x NUM UNIT_W (e.g. "Bizepsmaschine 15x3x15kg." or "40 kg x 3 x 15")
    # -------------------------------------------------------------
    # Subvariant 3a: Weight at start: "40 kg x 3 x 15"
    r3a_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*x\s*(\d+)\s*x\s*(\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r3a_match:
        w = float(r3a_match.group(1))
        v1 = int(r3a_match.group(2))
        v2 = int(r3a_match.group(3))
        n_sets = min(v1, v2) if min(v1, v2) <= 6 else v1
        reps = max(v1, v2) if n_sets == min(v1, v2) else v2
        name, note = _extract_name_and_tail(raw_line, r3a_match.start(), r3a_match.end())
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    # Subvariant 3b: Weight at end: "15x3x15kg"
    r3b_match = re.search(
        r"(\d+)\s*x\s*(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")",
        normalized,
        re.IGNORECASE,
    )
    if r3b_match:
        v1 = int(r3b_match.group(1))
        v2 = int(r3b_match.group(2))
        w = float(r3b_match.group(3))
        n_sets = min(v1, v2) if min(v1, v2) <= 6 else v1
        reps = max(v1, v2) if n_sets == min(v1, v2) else v2
        name, note = _extract_name_and_tail(raw_line, r3b_match.start(), r3b_match.end())
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R2: NUM UNIT_W + NUM x NUM: "Присед 100кг 5x5" or "Brustpresse 35 kg 4x15"
    # -------------------------------------------------------------
    r2_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*(\d+)\s*x\s*(\d+)",
        normalized,
        re.IGNORECASE,
    )
    if r2_match:
        w = float(r2_match.group(1))
        n_sets = int(r2_match.group(2))
        reps = int(r2_match.group(3))
        name, note = _extract_name_and_tail(raw_line, r2_match.start(), r2_match.end())
        sets = [ParsedSet(weight_kg=w, reps=reps) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "strength", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R8: Static timed: "Планка 3x60с"
    # -------------------------------------------------------------
    r8_match = re.search(
        r"^(.*?)\s+(\d+)\s*x\s*(\d+)\s*" + RE_TIME_SEC + r"\b",
        normalized,
        re.IGNORECASE,
    )
    if r8_match:
        name = clean_name(r8_match.group(1))
        n_sets = int(r8_match.group(2))
        sec = int(r8_match.group(3))
        sets = [ParsedSet(duration_sec=sec) for _ in range(n_sets)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "timed", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R7: Cardio: "Ellipsentrainer 20 минут кросс тренинг" / "Бег 5 км 27:30"
    # -------------------------------------------------------------
    # Try matching distance + time
    r7_dist_time = re.search(
        r"^(.*?)\s+(\d+(?:\.\d+)?)\s*" + RE_DIST_KM + r"\s+(\d{1,2}:\d{2}(?::\d{2})?|\d+\s*" + RE_TIME_MIN + r")",
        normalized,
        re.IGNORECASE,
    )
    if r7_dist_time:
        name = clean_name(r7_dist_time.group(1))
        km = float(r7_dist_time.group(2))
        dist_m = int(km * 1000)
        time_token = r7_dist_time.group(3).strip()
        sec = _parse_time_str(time_token)
        if sec is None:
            m_min = re.search(r"(\d+)", time_token)
            sec = int(m_min.group(1)) * 60 if m_min else None
        sets = [ParsedSet(duration_sec=sec, distance_m=dist_m)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "cardio", catalog, library_names)

    # Try matching duration only: "Ellipsentrainer 20 минут кросс тренинг"
    r7_time_only = re.search(
        r"^(.*?)\s+(\d+(?:\.\d+)?)\s*" + RE_TIME_MIN + r"\s*(.*)$",
        normalized,
        re.IGNORECASE,
    )
    if r7_time_only:
        name = clean_name(r7_time_only.group(1))
        minutes = float(r7_time_only.group(2))
        note = clean_name(r7_time_only.group(3)) or None
        sec = int(minutes * 60)
        sets = [ParsedSet(duration_sec=sec)]
        return _make_draft_items(name, sets, raw_line, line_no, note, "cardio", catalog, library_names)

    # -------------------------------------------------------------
    # Rule R9: "Подтягивания 3x10" / "Жим 20x10" without units
    # -------------------------------------------------------------
    r9_match = re.search(r"^(.*?)\s+(\d+(?:\.\d+)?)\s*x\s*(\d+)$", normalized, re.IGNORECASE)
    if r9_match:
        name = clean_name(r9_match.group(1))
        v1 = float(r9_match.group(2))
        v2 = int(r9_match.group(3))

        # Check catalog kind
        cat_entry = catalog.find_by_name(name)
        is_bodyweight = cat_entry and cat_entry.get("kind") == "bodyweight"

        # If bodyweight: 3x10 is 3 sets of 10 reps
        if is_bodyweight:
            n_sets = int(v1)
            reps = v2
            sets = [ParsedSet(weight_kg=None, reps=reps) for _ in range(n_sets)]
            return _make_draft_items(name, sets, raw_line, line_no, None, "bodyweight", catalog, library_names)

        # Disambiguate: if one <= 6 and the other >= 8 -> sets x reps
        if v1 <= 6 and v2 >= 8 and v1.is_integer():
            n_sets = int(v1)
            reps = v2
            # Check history to see if there was a previous weight
            hist = history_index.get(name.lower())
            weight = _hist_get(hist, "top_weight") if hist else None
            sets = [ParsedSet(weight_kg=weight, reps=reps) for _ in range(n_sets)]
            return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

        # Ambiguous: e.g. "Жим 20x10" (20kg x 10 vs 10kg x 20 or 20 sets of 10)
        opt1 = [ParsedSet(weight_kg=v1, reps=v2)]
        opt2 = [ParsedSet(weight_kg=float(v2), reps=int(v1))]
        return [
            DraftItem(
                line_no=line_no,
                raw=raw_line,
                name=name,
                kind="strength",
                sets=opt1,
                status="ambiguous",
                options=[opt1, opt2],
                source="rules",
            )
        ]

    # -------------------------------------------------------------
    # Rule R14: "Brustpresse 35 кг x 12" or "Жим лежа 50 кг 10 раз" -> 1 set
    # -------------------------------------------------------------
    r14_match = re.search(
        r"^(.*?)\s+(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*x\s*(\d+)$",
        normalized,
        re.IGNORECASE,
    )
    if r14_match:
        name = clean_name(r14_match.group(1))
        w = float(r14_match.group(2))
        reps = int(r14_match.group(3))
        sets = [ParsedSet(weight_kg=w, reps=reps)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    r14b_match = re.search(
        r"^(.*?)\s+(\d+(?:\.\d+)?)\s*(?:" + RE_WEIGHT_UNIT + r")\s*(?:на\s*)?(\d+)\s*" + RE_REPS_WORD + r"$",
        normalized,
        re.IGNORECASE,
    )
    if r14b_match:
        name = clean_name(r14b_match.group(1))
        w = float(r14b_match.group(2))
        reps = int(r14b_match.group(3))
        sets = [ParsedSet(weight_kg=w, reps=reps)]
        return _make_draft_items(name, sets, raw_line, line_no, None, "strength", catalog, library_names)

    r14c_match = re.search(
        r"^(.*?)\s+(\d+)\s*" + RE_REPS_WORD + r"$",
        normalized,
        re.IGNORECASE,
    )
    if r14c_match:
        name = clean_name(r14c_match.group(1))
        reps = int(r14c_match.group(2))
        cat_entry = catalog.find_by_name(name)
        is_bodyweight = bool(cat_entry and cat_entry.get("kind") == "bodyweight")
        kind = "bodyweight" if is_bodyweight else "strength"
        sets = [ParsedSet(weight_kg=None, reps=reps)]
        return _make_draft_items(name, sets, raw_line, line_no, None, kind, catalog, library_names)

    # -------------------------------------------------------------
    # Fallback unparsed
    # -------------------------------------------------------------
    return [
        DraftItem(
            line_no=line_no,
            raw=raw_line,
            name=clean_name(raw_line),
            kind="strength",
            sets=[],
            status="unparsed",
            source="rules",
        )
    ]


def _make_draft_items(
    name: str,
    sets: List[ParsedSet],
    raw: str,
    line_no: int,
    note: Optional[str],
    kind: str,
    catalog,
    library_names: Optional[List[str]],
) -> List[DraftItem]:
    """Check conjunction splitting, match catalog, and return DraftItems."""
    sub_names = split_conjunction(name, catalog=catalog, library_names=library_names)

    items: List[DraftItem] = []
    for s_name in sub_names:
        cat_match = catalog.find_by_name(s_name)
        cat_key = cat_match.get("key") if cat_match else None
        item_kind = cat_match.get("kind", kind) if cat_match else kind

        # Clone sets for each exercise
        cloned_sets = [ParsedSet(**s.model_dump()) for s in sets]
        item_note = note
        if len(sub_names) > 1:
            item_note = (item_note + " · ↔ разделено") if item_note else "↔ разделено"

        items.append(
            DraftItem(
                line_no=line_no,
                raw=raw,
                name=s_name,
                catalog_key=cat_key,
                kind=item_kind,
                sets=cloned_sets,
                note=item_note,
                status="ok",
                source="rules",
            )
        )
    return items


def parse_text(
    text: str,
    lang: str = "ru",
    library_names: Optional[List[str]] = None,
    history_index: Optional[Dict[str, Any]] = None,
) -> List[DraftItem]:
    """Parse multi-line text input into a list of DraftItems."""
    catalog = get_catalog()
    # Split text into lines or semicolon/comma-delimited items
    raw_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Split by ';' if multiple exercises are separated by semicolon
        if ";" in line:
            for sub in line.split(";"):
                sub = sub.strip()
                if sub:
                    raw_lines.append(sub)
        else:
            raw_lines.append(line)

    draft_items: List[DraftItem] = []
    for idx, r_line in enumerate(raw_lines, 1):
        parsed = parse_line(
            r_line,
            line_no=idx,
            catalog=catalog,
            library_names=library_names,
            history_index=history_index,
        )
        draft_items.extend(parsed)

    return draft_items

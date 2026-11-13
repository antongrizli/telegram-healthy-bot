"""Deterministic workout planner: exercise selection, time budgeting, progression integration, and resizing."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from src.services.training.catalog import get_catalog
from src.services.training.focus import FOCUS_GROUPS
from src.services.training.dto import Focus, ParsedSet, PlanItem, Target
from src.services.training.progression import (
    calculate_warmup_sets,
    next_target,
    rep_range,
)

PATTERN_ORDER = {
    "cardio": 0,
    "squat": 1,
    "hinge": 2,
    "push_h": 3,
    "pull_h": 4,
    "push_v": 5,
    "pull_v": 6,
    "lunge": 7,
    "isolation": 8,
    "core": 9,
    "carry": 10,
}

GOAL_DEFAULTS = {
    "hypertrophy": {"rest_sec": 75, "sets": 3},
    "strength": {"rest_sec": 150, "sets": 4},
    "endurance": {"rest_sec": 45, "sets": 3},
    "fat_loss": {"rest_sec": 45, "sets": 3},
    "health": {"rest_sec": 60, "sets": 3},
}


def exercise_duration_seconds(num_sets: int, rest_sec: int) -> int:
    """Calculate expected duration for an exercise: sets * (40s work + rest) + 60s transition."""
    return num_sets * (40 + rest_sec) + 60


def score_candidate(
    candidate: Dict[str, Any],
    focus: Focus,
    has_history: bool,
    is_favorite: bool,
    used_recently: bool,
) -> float:
    """Score exercise suitability for the current plan."""
    score = 0.0
    primary = candidate.get("muscle_primary")
    secondary = candidate.get("muscles_secondary") or []
    pattern = candidate.get("pattern")

    # Conflict with limitations
    for lim in focus.limitations:
        if lim == "lower_back":
            if primary == "lower_back" or pattern in ("hinge", "squat"):
                score -= 10.0
        elif lim == "knees":
            if primary in ("quads", "calves") or pattern in ("squat", "lunge"):
                score -= 10.0
        elif lim == "shoulders":
            if "shoulder" in (primary or "") or pattern in ("push_v", "push_h"):
                score -= 10.0

    # Muscle match
    if primary in focus.primary_muscles:
        score += 3.0
    elif any(s in focus.primary_muscles for s in secondary):
        score += 1.0

    # Accent bonus
    if focus.accent and primary in focus.primary_muscles:
        score += 2.0

    # Familiarity and preference
    if has_history:
        score += 1.5
    if is_favorite:
        score += 1.0
    if used_recently:
        score -= 1.0

    return score


def find_coverage_gaps(library_exercises: List[Dict[str, Any]], groups: List[str]) -> List[str]:
    """Return goal groups for which the library has no machine. fullbody expands to chest/back/legs."""
    expanded: List[str] = []
    for g in groups:
        expanded.extend(["chest", "back", "legs"] if g == "fullbody" else [g])

    gaps: List[str] = []
    for g in expanded:
        if g in gaps:
            continue
        if g == "cardio":
            covered = any(e.get("kind") == "cardio" for e in library_exercises)
        else:
            muscles = FOCUS_GROUPS.get(g, set())
            covered = any(e.get("muscle_primary") in muscles for e in library_exercises)
        if not covered:
            gaps.append(g)
    return gaps


def build_plan(
    profile: Optional[Any],
    library_exercises: List[Dict[str, Any]],
    history_index: Dict[str, Any],
    focus: Focus,
    minutes: Optional[int] = None,
    energy: Optional[str] = None,
) -> List[PlanItem]:
    """Build a deterministic, customized workout plan matching budget and focus."""
    catalog = get_catalog()
    goal = getattr(profile, "goal", "hypertrophy") or "hypertrophy"
    goal_defaults = GOAL_DEFAULTS.get(goal.lower(), GOAL_DEFAULTS["hypertrophy"])
    rest_sec = goal_defaults["rest_sec"]
    base_sets = goal_defaults["sets"]

    target_mins = minutes or focus.target_minutes or 45
    budget_sec = target_mins * 60
    current_energy = energy or focus.energy or "normal"

    if current_energy == "low":
        base_sets = max(2, base_sets - 1)

    # 1. Candidates come strictly from the user's library (never from the catalog)
    candidates: List[Dict[str, Any]] = []
    for ex in library_exercises:
        candidates.append(ex)


    # 2. Score candidates
    scored = []
    for cand in candidates:
        name = cand.get("display_name") or cand.get("key", "")
        h_info = history_index.get(name.lower())
        has_hist = h_info is not None
        fav = cand.get("is_favorite", False)
        if h_info is None:
            recent = False
        elif isinstance(h_info, dict):
            recent = h_info.get("days_ago", 999) < 4
        else:
            recent = getattr(h_info, "days_ago", 999) < 4

        sc = score_candidate(cand, focus, has_hist, fav, recent)
        if sc > 0.0:  # Only positive matches
            scored.append((sc, cand))

    scored.sort(key=lambda x: x[0], reverse=True)

    # 3. Select exercises respecting pattern limit (max 2 per pattern)
    selected: List[Dict[str, Any]] = []
    pattern_counts: Dict[str, int] = {}

    for sc, cand in scored:
        pat = cand.get("pattern", "isolation")
        if pattern_counts.get(pat, 0) < 2:
            selected.append(cand)
            pattern_counts[pat] = pattern_counts.get(pat, 0) + 1

    # Sort selected by exercise execution order
    selected.sort(key=lambda c: PATTERN_ORDER.get(c.get("pattern", "isolation"), 8))

    # 4. Budget fit: adjust number of exercises and sets
    total_time = sum(exercise_duration_seconds(base_sets, rest_sec) for _ in selected)
    while total_time > budget_sec and len(selected) > 3:
        # Drop exercise with lowest score
        lowest = min(selected, key=lambda c: score_candidate(c, focus, False, False, False))
        selected.remove(lowest)
        total_time = sum(exercise_duration_seconds(base_sets, rest_sec) for _ in selected)

    return _build_plan_items_from_selected(
        selected=selected,
        catalog=catalog,
        goal=goal,
        rest_sec=rest_sec,
        base_sets=base_sets,
        current_energy=current_energy,
        focus=focus,
        history_index=history_index,
    )


def build_fallback_plan(
    profile: Optional[Any],
    library_exercises: List[Dict[str, Any]],
    history_index: Dict[str, Any],
    focus: Focus,
    minutes: Optional[int] = None,
    energy: Optional[str] = None,
) -> List[PlanItem]:
    """Build a plan using whatever non-conflicting machines exist in the user's library."""
    if not library_exercises:
        return []

    from src.services.training.focus import FOCUS_GROUPS

    # Try expanding focus to fullbody + cardio while keeping user's limitations
    broad_muscles = sorted(
        {m for grp in FOCUS_GROUPS.values() for m in grp}
    )
    broad_focus = focus.model_copy(update={"primary_muscles": broad_muscles, "accent": None})
    items = build_plan(
        profile=profile,
        library_exercises=library_exercises,
        history_index=history_index,
        focus=broad_focus,
        minutes=minutes,
        energy=energy,
    )
    if items:
        return items

    # Last resort when library machines have no muscle tags yet: pick non-conflicting items directly.
    catalog = get_catalog()
    goal = getattr(profile, "goal", "hypertrophy") or "hypertrophy"
    goal_defaults = GOAL_DEFAULTS.get(goal.lower(), GOAL_DEFAULTS["hypertrophy"])
    rest_sec = goal_defaults["rest_sec"]
    base_sets = goal_defaults["sets"]
    current_energy = energy or focus.energy or "normal"
    if current_energy == "low":
        base_sets = max(2, base_sets - 1)

    selected = [
        cand for cand in library_exercises
        if score_candidate(cand, focus, False, False, False) >= 0.0
    ][:5]
    if not selected:
        return []

    selected.sort(key=lambda c: PATTERN_ORDER.get(c.get("pattern") or "isolation", 8))
    return _build_plan_items_from_selected(
        selected=selected,
        catalog=catalog,
        goal=goal,
        rest_sec=rest_sec,
        base_sets=base_sets,
        current_energy=current_energy,
        focus=focus,
        history_index=history_index,
    )


def _build_plan_items_from_selected(
    selected: List[Dict[str, Any]],
    catalog,
    goal: str,
    rest_sec: int,
    base_sets: int,
    current_energy: str,
    focus: Focus,
    history_index: Dict[str, Any],
) -> List[PlanItem]:
    """Build concrete PlanItems with targets, warmups and cues from selected library candidates."""
    plan_items: List[PlanItem] = []
    first_in_group_seen: Set[str] = set()

    for cand in selected:
        name = cand.get("display_name") or cand.get("key")
        kind = cand.get("kind", "strength")
        primary = cand.get("muscle_primary") or ""
        pattern = cand.get("pattern")
        cat_key = cand.get("catalog_key") or cand.get("key")

        # History and progression
        h_info = history_index.get(name.lower())
        is_first_in_group = primary not in first_in_group_seen
        first_in_group_seen.add(primary)

        target = next_target(
            h_info,
            pattern=pattern,
            goal=goal,
            energy=current_energy,
            weight_step=float(cand.get("weight_step") or 2.5),
            min_weight=float(cand.get("min_weight")) if cand.get("min_weight") is not None else None,
        )

        # Working sets
        num_sets = base_sets
        if focus.accent and primary in focus.primary_muscles:
            num_sets += 1

        working_sets = []
        if kind == "cardio":
            working_sets = [ParsedSet(duration_sec=8 * 60)]
        else:
            for _ in range(num_sets):
                working_sets.append(ParsedSet(weight_kg=target.weight, reps=target.reps))

        # Warmup sets
        warmup_sets = []
        if kind == "strength" and target.weight and target.weight >= 20.0:
            warmup_sets = calculate_warmup_sets(
                target.weight,
                is_first_in_group=is_first_in_group,
                is_isolation=(pattern == "isolation"),
                weight_step=float(cand.get("weight_step") or 2.5),
            )

        # Reason & cue
        reason = ""
        if h_info:
            top_w = h_info.top_weight
            med_r = h_info.median_reps
            feel_icon = "🟢" if h_info.feeling == "easy" else ("🔴" if h_info.feeling == "hard" else "🟡")
            if target.weight and target.weight > top_w:
                reason = f"было {top_w:.1f}×{med_r} {feel_icon} → +{target.weight - top_w:.1f} кг"
            else:
                reason = f"было {top_w:.1f}×{med_r} {feel_icon}, держим"
        elif target.calibrate:
            reason = "новое: подберите вес на RPE 7"

        # Cues
        cues = catalog.get_cues(cat_key, lang="ru") if cat_key else []
        cue_text = cues[0] if cues else ""

        plan_items.append(
            PlanItem(
                exercise_id=cand.get("id"),
                catalog_key=cat_key,
                name=name,
                kind=kind,
                warmup=warmup_sets,
                working=working_sets,
                rest_sec=rest_sec,
                reason=reason,
                cue=cue_text,
                calibrate=target.calibrate,
            )
        )

    return plan_items


def get_alternatives(
    plan: List[PlanItem],
    position: int,
    catalog=None,
    k: int = 3,
) -> List[PlanItem]:
    """Find alternative exercises with matching primary muscle and movement pattern."""
    if position < 0 or position >= len(plan):
        return []

    item = plan[position]
    catalog = catalog or get_catalog()
    cat_entry = catalog.get(item.catalog_key) if item.catalog_key else None
    if not cat_entry:
        return []

    alt_dicts = catalog.get_alternatives(item.catalog_key)
    res: List[PlanItem] = []
    for cand in alt_dicts[:k]:
        name = cand.get("names", {}).get("ru", [cand["key"]])[0]
        cues = catalog.get_cues(cand["key"], lang="ru")
        cue_text = cues[0] if cues else ""
        res.append(
            PlanItem(
                catalog_key=cand["key"],
                name=name,
                kind=cand.get("kind", "strength"),
                working=[ParsedSet(**s.model_dump()) for s in item.working],
                warmup=[ParsedSet(**s.model_dump()) for s in item.warmup],
                rest_sec=item.rest_sec,
                reason=f"альтернатива для {item.name}",
                cue=cue_text,
            )
        )
    return res


def resize_plan(plan: List[PlanItem], delta_minutes: int) -> List[PlanItem]:
    """Resize plan by -15 or +15 minutes: drop a set (never below 2) or an exercise; or add a set to two exercises."""
    if not plan:
        return []

    resized = [PlanItem(**i.model_dump()) for i in plan]

    if delta_minutes < 0:
        # Shorten: first reduce sets on the last strength exercise that has more than 2
        reduced = False
        for item in reversed(resized):
            if item.kind != "cardio" and len(item.working) > 2:
                item.working.pop()
                reduced = True
                break
        if not reduced and len(resized) > 2:
            resized.pop()
    else:
        # Lengthen: add +1 working set to the first two strength exercises with room
        added = 0
        for item in resized:
            if item.kind != "cardio" and item.working and len(item.working) < 4:
                item.working.append(ParsedSet(**item.working[-1].model_dump()))
                added += 1
                if added == 2:
                    break

    return resized

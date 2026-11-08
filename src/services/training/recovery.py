"""Recovery tracking and focus recommendation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from src.utils.training_locales import t

EXPERIENCE_TARGET_SETS = {
    "beginner": 8,
    "intermediate": 12,
    "advanced": 16,
}

SPLIT_ROTATIONS = {
    "upper_lower": ["upper", "lower"],
    "ppl": ["push", "pull", "legs"],
    "fullbody": ["fullbody"],
}

GROUP_TO_SPLIT = {
    "chest": "push",
    "shoulders_front": "push",
    "shoulders_side": "push",
    "triceps": "push",
    "back_lats": "pull",
    "back_upper": "pull",
    "biceps": "pull",
    "shoulders_rear": "pull",
    "quads": "legs",
    "hamstrings": "legs",
    "glutes": "legs",
    "calves": "legs",
    "adductors": "legs",
    "abductors": "legs",
}

GROUP_LABELS = {
    "legs": "🦵 Ноги",
    "back": "🪵 Спина",
    "chest": "🛡 Грудь",
    "arms": "💪 Руки",
    "shoulders": "⚡ Плечи",
    "abs": "🧱 Пресс",
    "fullbody": "🔥 Всё тело",
    "cardio": "🏃 Кардио",
}


def compute_muscle_status(days_since: float) -> str:
    """Return status: fresh (>= 3 days), ok (2-3 days), tired (< 2 days)."""
    if days_since >= 3.0:
        return "fresh"
    elif days_since >= 2.0:
        return "ok"
    return "tired"


def suggest_focus(
    profile: Optional[Any],
    muscle_stats: Dict[str, Dict[str, Any]],
    last_session_focus: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Suggest focus group and rationale for today's session based on split or recovery.
    Returns: (group_key, reason_text)
    """
    experience = getattr(profile, "experience", "intermediate") or "intermediate"
    split = getattr(profile, "split", "auto") or "auto"
    target_sets = EXPERIENCE_TARGET_SETS.get(experience, 12)

    # 1. Preset split rotation
    if split in ("upper_lower", "ppl"):
        rotation = SPLIT_ROTATIONS[split]
        # Determine last split day
        last_day = None
        if last_session_focus:
            for g in last_session_focus:
                if g in GROUP_TO_SPLIT:
                    last_day = GROUP_TO_SPLIT[g]
                    break
        if last_day in rotation:
            next_idx = (rotation.index(last_day) + 1) % len(rotation)
            next_day = rotation[next_idx]
        else:
            next_day = rotation[0]

        reason = t("rec_split", x=split.upper())
        return next_day, reason

    # 2. Auto split based on fresh muscle groups with biggest deficit
    groups_priority = ["legs", "back", "chest", "arms", "shoulders"]
    best_group = "fullbody"
    best_deficit = -999.0
    best_days_since = 0.0

    for grp in groups_priority:
        stats = muscle_stats.get(grp, {})
        days_since = stats.get("days_since", 99.0)
        weekly_sets = stats.get("weekly_sets", 0)
        status = compute_muscle_status(days_since)

        deficit = target_sets - weekly_sets
        if status == "fresh":
            deficit += 10.0  # priority boost for well-rested muscles

        if deficit > best_deficit:
            best_deficit = deficit
            best_group = grp
            best_days_since = days_since

    # Build human-readable rationale
    if best_days_since >= 90:
        reason = t("rec_long")
    elif best_days_since >= 1:
        reason = t("rec_days", n=int(best_days_since))
    else:
        reason = t("rec_fresh")

    return best_group, reason

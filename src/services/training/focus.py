"""Focus determination from buttons or user free text."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from src.services.training.dto import Focus

FOCUS_GROUPS: Dict[str, Set[str]] = {
    "back": {"back_lats", "back_upper", "lower_back"},
    "chest": {"chest"},
    "legs": {"quads", "hamstrings", "glutes", "adductors", "abductors", "calves"},
    "arms": {"biceps", "triceps"},
    "shoulders": {"shoulders_front", "shoulders_side", "shoulders_rear"},
    "abs": {"abs"},
    "fullbody": {
        "chest", "back_lats", "back_upper", "quads", "hamstrings",
        "glutes", "shoulders_side", "biceps", "triceps", "abs",
    },
    "cardio": {"cardio"},
}

KEYWORD_MAP = {
    "back": [r"\bспин\w*", r"\bширочайш\w*", r"\brücken\w*", r"\bback\w*", r"\blat\w*"],
    "chest": [r"\bгруд\w*", r"\bbrust\w*", r"\bchest\w*"],
    "legs": [r"\bног\w*", r"\bbein\w*", r"\bleg\w*", r"\bягодиц\w*", r"\bpo\b", r"\bglute\w*", r"\bквадрицепс\w*"],
    "arms": [r"\bрук\w*", r"\barm\w*", r"\bбицепс\w*", r"\bтрицепс\w*", r"\bbizeps\w*", r"\btrizeps\w*", r"\bbicep\w*", r"\btricep\w*"],
    "shoulders": [r"\bплеч\w*", r"\bschulter\w*", r"\bshoulder\w*", r"\bдельт\w*"],
    "abs": [r"\bпресс\w*", r"\bbauch\w*", r"\babs\b", r"\bживот\w*", r"\bcore\b"],
    "fullbody": [r"\bвс[её]\s*тело\w*", r"\bfull\s*body\b", r"\bganzkörper\w*"],
    "cardio": [r"\bкардио\w*", r"\bcardio\w*", r"\bбег\w*", r"\blauf\w*"],
}

LIMITATION_PATTERNS = {
    "lower_back": [
        r"без\s+(?:нагрузки\s+на\s+)?(?:поясниц\w*|поясницу|спину|низ\s+спины)",
        r"(?:ohne|keine)\s+(?:unterer\s+rücken|rückenbelastung|lws)",
        r"(?:no|without)\s+(?:lower\s+back|spinal\s+loading)",
    ],
    "knees": [
        r"без\s+(?:нагрузки\s+на\s+)?колен\w*",
        r"(?:ohne|keine)\s+knie\w*",
        r"(?:no|without)\s+knee\w*",
    ],
    "shoulders": [
        r"без\s+(?:нагрузки\s+на\s+)?плеч\w*",
        r"(?:ohne|keine)\s+schulter\w*",
        r"(?:no|without)\s+shoulder\w*",
    ],
}

MODIFIERS_ACCENT = [r"\bупор\b", r"\bакцент\b", r"\bfocus\b", r"\bschwerpunkt\b"]
MODIFIERS_LIGHT = [r"\bлегк\w*", r"\blight\b", r"\bleicht\b"]


def resolve_focus(
    selected_groups: Optional[List[str]] = None,
    text: Optional[str] = None,
    minutes: int = 45,
    energy: str = "normal",
) -> Focus:
    """Resolve user selected buttons or free-text goal into a structured Focus."""
    primary_muscles: Set[str] = set()
    secondary_muscles: Set[str] = set()
    limitations: Set[str] = set()
    modifiers: List[str] = []
    accent_group: Optional[str] = None

    # 1. From buttons
    if selected_groups:
        for g in selected_groups:
            if g in FOCUS_GROUPS:
                primary_muscles.update(FOCUS_GROUPS[g])

    # 2. From text
    if text:
        text_lower = text.lower()

        # Check limitations
        for lim_key, patterns in LIMITATION_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, text_lower):
                    limitations.add(lim_key)
                    break

        # Check energy modifier
        for pat in MODIFIERS_LIGHT:
            if re.search(pat, text_lower):
                energy = "low"
                modifiers.append("light")
                break

        # Check accent / modifier
        has_accent = any(re.search(pat, text_lower) for pat in MODIFIERS_ACCENT)
        if has_accent:
            modifiers.append("accent")

        # Check duration
        m_match = re.search(r"(\d+)\s*(?:мин\w*|min\w*|m\b)", text_lower)
        if m_match:
            parsed_mins = int(m_match.group(1))
            if 15 <= parsed_mins <= 180:
                minutes = parsed_mins

        # Match muscle groups from keywords
        for group_name, patterns in KEYWORD_MAP.items():
            for pat in patterns:
                if re.search(pat, text_lower):
                    group_muscles = FOCUS_GROUPS.get(group_name, set())
                    if has_accent and accent_group is None:
                        accent_group = group_name
                    primary_muscles.update(group_muscles)
                    break

    # Default to full body if nothing selected or found
    if not primary_muscles:
        primary_muscles = set(FOCUS_GROUPS["fullbody"])

    return Focus(
        primary_muscles=sorted(primary_muscles),
        secondary_muscles=sorted(secondary_muscles),
        modifiers=modifiers,
        limitations=sorted(limitations),
        target_minutes=minutes,
        energy=energy,
        accent=accent_group,
        raw_text=text,
    )

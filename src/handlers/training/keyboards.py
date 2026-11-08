"""Inline keyboards and callback helpers for Training v2."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.services.training.summary import format_weight
from src.utils.training_locales import group_label, t


def make_cb(action: str, session_id: int = 0, rev: int = 0, arg: str = "") -> str:
    """Format callback_data strictly within 64-byte limit: t2:<action>:<session_id>:<rev>[:<arg>]."""
    if arg:
        cb = f"t2:{action}:{session_id}:{rev}:{arg}"
    else:
        cb = f"t2:{action}:{session_id}:{rev}"
    return cb[:64]


def get_hub_keyboard(
    is_active: bool = False,
    session_id: int = 0,
    rev: int = 0,
    is_empty_library: bool = False,
    rec_focus_str: str = "",
    rec_mins: int = 45,
    active_exercise: str = "",
    active_progress: str = "",
) -> InlineKeyboardMarkup:
    """Hub keyboard matching Section 3.2."""
    rows: List[List[InlineKeyboardButton]] = []

    if is_active:
        text = t("kb_continue", ex=active_exercise[:12], prog=active_progress)
        rows.append([InlineKeyboardButton(text=text, callback_data=make_cb("resume", session_id, rev))])
    elif is_empty_library:
        rows.append([InlineKeyboardButton(text=t("kb_log_first"), callback_data=make_cb("log_first", 0, 0))])
    else:
        text = t("kb_start", focus=rec_focus_str or group_label("legs"), mins=rec_mins)
        rows.append([InlineKeyboardButton(text=text, callback_data=make_cb("start_rec", session_id, rev))])

    rows.append([
        InlineKeyboardButton(text=t("kb_other_goal"), callback_data=make_cb("goal_menu", 0, 0)),
        InlineKeyboardButton(text=t("kb_repeat"), callback_data=make_cb("repeat_menu", 0, 0)),
    ])
    rows.append([
        InlineKeyboardButton(text=t("kb_write_done"), callback_data=make_cb("write_done", 0, 0)),
    ])
    rows.append([
        InlineKeyboardButton(text=t("kb_progress"), callback_data=make_cb("progress", 0, 0)),
        InlineKeyboardButton(text=t("kb_equipment"), callback_data=make_cb("settings", 0, 0)),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_goal_picker_keyboard(
    selected_muscles: List[str],
    current_mins: int = 45,
    current_energy: str = "normal",
) -> InlineKeyboardMarkup:
    """Goal picker keyboard matching Section 3.3."""
    sel = set(selected_muscles)

    def _btn(name: str, key: str) -> InlineKeyboardButton:
        prefix = "☑️ " if key in sel else ""
        return InlineKeyboardButton(text=f"{prefix}{name}", callback_data=make_cb("tgl_m", 0, 0, key))

    rows: List[List[InlineKeyboardButton]] = [
        [_btn(group_label("back"), "back"), _btn(group_label("chest"), "chest"), _btn(group_label("legs"), "legs")],
        [_btn(group_label("arms"), "arms"), _btn(group_label("shoulders"), "shoulders"), _btn(group_label("abs"), "abs")],
        [_btn(group_label("fullbody"), "fullbody"), _btn(group_label("cardio"), "cardio")],
    ]

    # Time row: 30, 45, 60, 90
    time_row = []
    for m in (30, 45, 60, 90):
        t_label = f"•{m}•" if m == current_mins else str(m)
        time_row.append(InlineKeyboardButton(text=t_label, callback_data=make_cb("set_time", 0, 0, str(m))))
    rows.append(time_row)

    # Energy row: 😴, 🙂, 💪
    energy_icons = [("😴", "low"), ("🙂", "normal"), ("💪", "high")]
    energy_row = []
    for icon, en_key in energy_icons:
        label = f"•{icon}•" if en_key == current_energy else icon
        energy_row.append(InlineKeyboardButton(text=label, callback_data=make_cb("set_en", 0, 0, en_key)))
    rows.append(energy_row)

    # Action row
    rows.append([
        InlineKeyboardButton(text=t("kb_make_plan"), callback_data=make_cb("make_plan", 0, 0)),
        InlineKeyboardButton(text=t("kb_back"), callback_data=make_cb("hub", 0, 0)),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_plan_card_keyboard(
    plan_id: int,
    rev: int = 0,
) -> InlineKeyboardMarkup:
    """Plan card keyboard matching Section 3.4."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_start_plan"), callback_data=make_cb("start_plan", plan_id, rev))],
        [
            InlineKeyboardButton(text=t("kb_length"), callback_data=make_cb("resize_menu", plan_id, rev)),
            InlineKeyboardButton(text=t("kb_back"), callback_data=make_cb("hub", 0, 0)),
        ],
    ])


def get_live_logging_keyboard(
    session_id: int,
    rev: int,
    current_weight: Optional[float],
    current_reps: Optional[int],
    weight_step: float = 2.5,
    is_cardio: bool = False,
    duration_mins: int = 8,
    rest_sec: int = 90,
) -> InlineKeyboardMarkup:
    """Live exercise set logging keyboard matching Section 3.5."""
    rows: List[List[InlineKeyboardButton]] = []

    if is_cardio:
        rows.append([InlineKeyboardButton(text=t("kb_done_min", n=duration_mins), callback_data=make_cb("ok_set", session_id, rev))])
        rows.append([
            InlineKeyboardButton(text=t("kb_min_delta", d="−2"), callback_data=make_cb("adj_c", session_id, rev, "-2")),
            InlineKeyboardButton(text=t("kb_min_delta", d="+2"), callback_data=make_cb("adj_c", session_id, rev, "+2")),
            InlineKeyboardButton(text=t("kb_other"), callback_data=make_cb("custom_live", session_id, rev)),
        ])
    else:
        w_str = format_weight(current_weight)
        btn_text = f"✅ {w_str} × {current_reps}" if current_weight else t("kb_done_reps", reps=current_reps)
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=make_cb("ok_set", session_id, rev))])

        step_str = format_weight(weight_step)
        rows.append([
            InlineKeyboardButton(text=t("kb_kg_delta", d=f"−{step_str}"), callback_data=make_cb("adj_w", session_id, rev, f"-{step_str}")),
            InlineKeyboardButton(text=t("kb_kg_delta", d=f"+{step_str}"), callback_data=make_cb("adj_w", session_id, rev, f"+{step_str}")),
            InlineKeyboardButton(text=t("kb_rep_delta", d="−1"), callback_data=make_cb("adj_r", session_id, rev, "-1")),
            InlineKeyboardButton(text=t("kb_rep_delta", d="+1"), callback_data=make_cb("adj_r", session_id, rev, "+1")),
        ])

    rows.append([
        InlineKeyboardButton(text=t("kb_other"), callback_data=make_cb("custom_live", session_id, rev)),
        InlineKeyboardButton(text=t("kb_skip"), callback_data=make_cb("skip_ex", session_id, rev)),
    ])
    rows.append([
        InlineKeyboardButton(text=t("kb_rest", n=rest_sec), callback_data=make_cb("rest_timer", session_id, rev, str(rest_sec))),
        InlineKeyboardButton(text=t("kb_finish"), callback_data=make_cb("finish_live", session_id, rev)),
    ])
    rows.append([
        InlineKeyboardButton(text=t("kb_undo_set"), callback_data=make_cb("undo_set", session_id, rev)),
        InlineKeyboardButton(text=t("kb_abort"), callback_data=make_cb("abort_live", session_id, rev)),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_abort_confirm_keyboard(session_id: int, rev: int) -> InlineKeyboardMarkup:
    """Confirm discarding a live workout."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_abort_yes"), callback_data=make_cb("abort_yes", session_id, rev))],
        [InlineKeyboardButton(text=t("kb_abort_no"), callback_data=make_cb("resume", session_id, rev))],
    ])


def get_live_feeling_keyboard(
    session_id: int,
    rev: int,
) -> InlineKeyboardMarkup:
    """Prompt user feeling after the last set of an exercise."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("feel_easy"), callback_data=make_cb("feel_ex", session_id, rev, "easy")),
            InlineKeyboardButton(text=t("feel_normal"), callback_data=make_cb("feel_ex", session_id, rev, "normal")),
            InlineKeyboardButton(text=t("feel_hard"), callback_data=make_cb("feel_ex", session_id, rev, "hard")),
        ],
    ])


def get_draft_keyboard(
    session_id: int,
    rev: int,
    current_feeling: str = "normal",
    has_unparsed: bool = False,
) -> InlineKeyboardMarkup:
    """Draft card keyboard matching Section 3.6."""
    feel_row = []
    for icon, val in [("🟢", "easy"), ("🟡", "normal"), ("🔴", "hard")]:
        label = f"•{icon}•" if val == current_feeling else icon
        feel_row.append(InlineKeyboardButton(text=label, callback_data=make_cb("dfeel", session_id, rev, val)))

    rows = [
        feel_row,
        [InlineKeyboardButton(text=t("kb_save"), callback_data=make_cb("save_draft", session_id, rev))],
        [
            InlineKeyboardButton(text=t("kb_undo"), callback_data=make_cb("undo_draft", session_id, rev)),
            InlineKeyboardButton(text=t("kb_cancel"), callback_data=make_cb("cancel_draft", session_id, rev)),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_repeat_checklist_keyboard(
    items_state: List[Dict[str, Any]],
    session_id: int = 0,
    rev: int = 0,
) -> InlineKeyboardMarkup:
    """Repeat last workout checklist with toggles matching Section 3.7."""
    rows: List[List[InlineKeyboardButton]] = []
    for idx, item in enumerate(items_state):
        is_checked = item.get("checked", True)
        icon = "☑️" if is_checked else "⬜"
        name = item.get("name", "Exercise")
        load = item.get("load_str", "")
        btn_text = f"{icon} {name} — {load}"
        rows.append([InlineKeyboardButton(text=btn_text[:40], callback_data=make_cb("rep_tgl", session_id, rev, str(idx)))])

    rows.append([
        InlineKeyboardButton(text=t("kb_save_checked"), callback_data=make_cb("rep_save", session_id, rev)),
    ])
    rows.append([
        InlineKeyboardButton(text=t("kb_back"), callback_data=make_cb("hub", 0, 0)),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_summary_keyboard(session_id: int = 0) -> InlineKeyboardMarkup:
    """Summary card buttons matching Section 3.8."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("kb_to_menu"), callback_data=make_cb("hub", 0, 0)),
        ]
    ])


def get_rest_pref_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    """Single toggle for the automatic rest timer."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("rest_pref_on" if enabled else "rest_pref_off"), callback_data=make_cb("tgl_rest", 0, 0)),
    ]])

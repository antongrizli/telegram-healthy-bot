"""Training progress, history view, and WebApp integration."""
from __future__ import annotations

import html
import logging
from typing import Optional, Tuple

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings
from src.database.connection import AsyncSessionLocal
from src.database.models import WorkoutSession, WorkoutSessionEntry
from src.handlers.training.keyboards import make_cb
from src.handlers.training.ui import edit_or_send
from src.services.training import history
from src.utils.training_locales import group_label, t

logger = logging.getLogger(__name__)
router = Router(name="training_progress_router")

_PAGE_SIZE = 5
_FEELING_ICONS = {"easy": "🟢", "normal": "🟡", "hard": "🔴", "pain": "⚠️"}


def _format_entry_line(entry: WorkoutSessionEntry) -> str:
    ex_name = entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input
    w_sets = [s for s in entry.workout_sets if s.set_type == "working"] or entry.workout_sets
    n_sets = len(w_sets)
    w = w_sets[0].weight_kg if w_sets and w_sets[0].weight_kg else 0
    r = w_sets[0].reps if w_sets and w_sets[0].reps else 0
    dur = w_sets[0].duration_sec if w_sets and w_sets[0].duration_sec else 0

    if dur:
        return f"• {html.escape(ex_name)}: {int(dur / 60)} {t('unit_min')}"
    if w:
        w_str = f"{w:.1f}".rstrip("0").rstrip(".")
        return f"• {html.escape(ex_name)}: {n_sets}×{r} @ {w_str} {t('unit_kg')}"
    return f"• {html.escape(ex_name)}: {n_sets}×{r}"


def _format_session_entry_detail(entry: WorkoutSessionEntry) -> str:
    ex_name = entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input
    completed_sets = [s for s in entry.workout_sets if s.completed] or entry.workout_sets
    working_sets = [s for s in completed_sets if s.set_type == "working"] or completed_sets
    feel_icon = _FEELING_ICONS.get(entry.feeling or "", "")
    feel_part = f" {feel_icon}" if feel_icon else ""
    if not working_sets:
        return f"• <b>{html.escape(ex_name)}</b>{feel_part}"

    parts = []
    for ws in working_sets:
        if ws.duration_sec:
            parts.append(f"{int(ws.duration_sec / 60)} {t('unit_min')}")
        elif ws.weight_kg:
            w_str = f"{ws.weight_kg:.1f}".rstrip("0").rstrip(".")
            parts.append(f"{w_str}×{ws.reps or 0}")
        else:
            parts.append(f"{ws.reps or 0} {t('unit_reps')}")
    return f"• <b>{html.escape(ex_name)}</b>{feel_part}: {', '.join(parts)}"


async def build_progress_screen(db, user_id: int, page: int = 0) -> Tuple[str, InlineKeyboardMarkup]:
    """Build workout history & progress text and inline keyboard."""
    weekly_count = await history.get_user_weekly_workout_count(db, user_id)
    total_count = await history.get_total_finished_count(db, user_id)
    page = max(0, min(page, max(0, (total_count - 1) // _PAGE_SIZE)))
    recent = await history.get_finished_sessions(db, user_id, limit=_PAGE_SIZE, offset=page * _PAGE_SIZE)

    lines = [
        t("prog_title"),
        t("prog_week", n=weekly_count),
        t("prog_total", n=total_count),
    ]

    if not recent:
        lines.append("")
        lines.append(t("prog_empty"))
    else:
        if page == 0 and recent[0].entries:
            last_session = recent[0]
            date_str = last_session.performed_at.strftime("%d.%m.%Y") if last_session.performed_at else ""
            lines.append(t("prog_last", date=date_str))
            for entry in sorted(last_session.entries, key=lambda e: e.position):
                lines.append(_format_entry_line(entry))

        lines.append(t("prog_list_title"))

    rows = []
    for sess in recent:
        date_str = sess.performed_at.strftime("%d.%m") if sess.performed_at else "?"
        n_ex = len(sess.entries)
        focus_part = ""
        if sess.focus:
            focus_part = f" · {group_label(sess.focus[0])}"
        btn_label = f"🗓 {date_str}{focus_part} · {t('n_ex', n=n_ex)}"
        rows.append([InlineKeyboardButton(text=btn_label[:48], callback_data=make_cb("hist_view", sess.id, page))])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text=t("kb_hist_prev"), callback_data=make_cb("hist_page", page - 1, 0)))
    if (page + 1) * _PAGE_SIZE < total_count:
        nav_row.append(InlineKeyboardButton(text=t("kb_hist_next"), callback_data=make_cb("hist_page", page + 1, 0)))
    if nav_row:
        rows.append(nav_row)

    if settings.WEBAPP_URL:
        from aiogram.types import WebAppInfo
        rows.append([InlineKeyboardButton(text=t("kb_webapp"), web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=workouts"))])
    rows.append([InlineKeyboardButton(text=t("kb_to_menu_arrow"), callback_data=make_cb("hub", 0, 0))])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def build_session_detail_screen(
    db,
    user_id: int,
    session_id: int,
    back_page: int = 0,
) -> Optional[Tuple[str, InlineKeyboardMarkup]]:
    """Build detailed card for a specific completed workout session."""
    sess = await history.get_finished_session_by_id(db, user_id, session_id)
    if not sess:
        return None

    date_str = sess.performed_at.strftime("%d.%m.%Y") if sess.performed_at else ""
    lines = [t("hist_detail_title", date=date_str)]
    if sess.focus:
        focus_labels = ", ".join(group_label(g) for g in sess.focus[:2])
        lines.append(f"🎯 {html.escape(focus_labels)}")

    total_vol = 0.0
    for entry in sess.entries:
        for ws in entry.workout_sets:
            if ws.completed and ws.weight_kg and ws.reps:
                total_vol += float(ws.weight_kg) * ws.reps

    if total_vol > 0:
        vol_str = f"{int(round(total_vol)):,} {t('unit_kg')}".replace(",", " ")
        lines.append(t("hist_volume", v=vol_str))

    lines.append("")
    for entry in sorted(sess.entries, key=lambda e: e.position):
        lines.append(_format_session_entry_detail(entry))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_hist_back"), callback_data=make_cb("hist_page", back_page, 0))],
        [InlineKeyboardButton(text=t("kb_to_menu_arrow"), callback_data=make_cb("hub", 0, 0))],
    ])
    return "\n".join(lines), kb


@router.callback_query(F.data.startswith("t2:progress"))
async def show_training_progress(callback: CallbackQuery, state: FSMContext):
    """Render workout history, personal records and WebApp charts button."""
    await callback.answer()
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        text, kb = await build_progress_screen(db, uid, page=0)

    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:hist_page:"))
async def handle_history_page(callback: CallbackQuery, state: FSMContext):
    """Paginate past workouts list in chat."""
    await callback.answer()
    parts = callback.data.split(":")
    page = max(0, int(parts[2])) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        text, kb = await build_progress_screen(db, uid, page=page)

    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:hist_view:"))
async def handle_history_detail(callback: CallbackQuery, state: FSMContext):
    """Show details of a single past workout session."""
    parts = callback.data.split(":")
    if len(parts) < 4 or not parts[2].isdigit():
        await callback.answer(t("stale"), show_alert=True)
        return
    session_id = int(parts[2])
    back_page = max(0, int(parts[3])) if parts[3].lstrip("-").isdigit() else 0
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        result = await build_session_detail_screen(db, uid, session_id, back_page=back_page)

    if not result:
        await callback.answer(t("stale"), show_alert=True)
        return

    await callback.answer()
    text, kb = result
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)

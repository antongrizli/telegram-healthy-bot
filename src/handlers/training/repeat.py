"""Scenario C: repeat previous workout with toggleable checklist."""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, List
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.utils.training_locales import t
from src.database.connection import AsyncSessionLocal
from src.database.models import WorkoutSession
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_repeat_checklist_keyboard, get_summary_keyboard
from src.handlers.training.states import TrainingFSM
from src.services.training import history, render, sessions, summary

logger = logging.getLogger(__name__)
router = Router(name="training_repeat_router")


@router.callback_query(F.data.startswith("t2:repeat_menu"))
async def open_repeat_menu(callback: CallbackQuery, state: FSMContext):
    """Open previous workout checklist screen."""
    await callback.answer()
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        last_session = await history.get_last_finished_session(db, uid)

    if not last_session or not last_session.entries:
        await callback.answer(t("rep_none"), show_alert=True)
        return

    # Build checklist items state
    items_state: List[Dict[str, Any]] = []
    for entry in last_session.entries:
        ex_name = entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input
        working_sets = [s for s in entry.workout_sets if s.set_type == "working"] or entry.workout_sets
        n_sets = len(working_sets)
        w = working_sets[0].weight_kg if working_sets and working_sets[0].weight_kg else None
        r = working_sets[0].reps if working_sets else 0
        dur = working_sets[0].duration_sec if working_sets else None

        if dur:
            load_str = f"{int(dur / 60)} {t('unit_min')}"
        elif w:
            w_str = f"{w:.1f}".rstrip("0").rstrip(".")
            load_str = f"{n_sets}×{r} @ {w_str}"
        else:
            load_str = f"{n_sets}×{r}"

        items_state.append({
            "canonical_id": entry.canonical_exercise_id,
            "name": ex_name,
            "load_str": load_str,
            "sets": [
                {
                    "weight_kg": float(s.weight_kg) if s.weight_kg is not None else None,
                    "reps": s.reps,
                    "duration_sec": s.duration_sec,
                    "distance_m": s.distance_m,
                    "set_type": s.set_type,
                }
                for s in working_sets
            ],
            "checked": True,
        })

    await state.set_state(TrainingFSM.repeat)
    await state.update_data(repeat_items=items_state)

    date_str = last_session.performed_at.strftime("%d.%m") if last_session.performed_at else ""
    text = t("rep_title_date", date=date_str)
    kb = get_repeat_checklist_keyboard(items_state)
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:rep_tgl:"))
async def handle_toggle_repeat_item(callback: CallbackQuery, state: FSMContext):
    """Toggle exercise checkbox (☑️/⬜)."""
    await callback.answer()
    parts = callback.data.split(":")
    idx = int(parts[4])

    data = await state.get_data()
    items_state = list(data.get("repeat_items") or [])
    if 0 <= idx < len(items_state):
        items_state[idx]["checked"] = not items_state[idx].get("checked", True)
        await state.update_data(repeat_items=items_state)

    text = t("rep_title")
    kb = get_repeat_checklist_keyboard(items_state)
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:rep_save:"))
async def handle_save_checked_repeat_items(callback: CallbackQuery, state: FSMContext):
    """Save only checked exercises directly as a completed session."""
    data = await state.get_data()
    items_state = data.get("repeat_items") or []
    checked = [i for i in items_state if i.get("checked", True)]

    if not checked:
        await callback.answer(t("rep_pick_one"), show_alert=True)
        return

    uid = callback.from_user.id
    from datetime import UTC, datetime
    from src.database.models import WorkoutSessionEntry, WorkoutSet

    now = datetime.now(UTC).replace(tzinfo=None)
    async with AsyncSessionLocal() as db:
        session = WorkoutSession(
            user_id=uid,
            status="finished",
            source="repeat",
            performed_at=now,
            started_at=now,
            finished_at=now,
            feeling="normal",
            revision=0,
        )
        db.add(session)
        await db.flush()

        for pos, item in enumerate(checked):
            entry = WorkoutSessionEntry(
                session_id=session.id,
                canonical_exercise_id=item["canonical_id"],
                raw_input=item["name"],
                position=pos,
                status="done",
                feeling="normal",
            )
            db.add(entry)
            await db.flush()

            for s_idx, s in enumerate(item.get("sets", []), 1):
                ws = WorkoutSet(
                    entry_id=entry.id,
                    idx=s_idx,
                    set_type=s.get("set_type", "working"),
                    weight_kg=s.get("weight_kg"),
                    reps=s.get("reps"),
                    duration_sec=s.get("duration_sec"),
                    distance_m=s.get("distance_m"),
                    completed=True,
                )
                db.add(ws)

        await db.flush()
        summary_data = await summary.build_session_summary(db, session)
        await db.commit()

    await callback.answer(t("workout_saved"))
    await state.set_state(TrainingFSM.hub)
    await state.set_data({})

    summary_html = render.render_summary_card(summary_data)
    kb = get_summary_keyboard(session.id)
    if callback.message:
        await edit_or_send(callback.message, summary_html, parse_mode="HTML", reply_markup=kb)

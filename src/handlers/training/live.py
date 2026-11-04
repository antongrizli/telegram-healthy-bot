"""Scenario A: live workout logging with 1-tap set completion, weights adjustments, and rest timer."""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, List, Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.database.connection import AsyncSessionLocal
from src.database.models import WorkoutSession, WorkoutSessionEntry, WorkoutSet
from src.handlers.training.keyboards import (
    get_live_feeling_keyboard,
    get_live_logging_keyboard,
    get_summary_keyboard,
    make_cb,
)
from src.handlers.training.states import TrainingFSM
from src.services.training import parser, render, sessions, summary

logger = logging.getLogger(__name__)
router = Router(name="training_live_router")


async def render_current_live_card(
    target_message: Message,
    session: WorkoutSession,
    state: FSMContext,
    edit: bool = True,
    temp_w: Optional[float] = None,
    temp_r: Optional[int] = None,
) -> None:
    """Find current active exercise and set, render live card, and update anchor message."""
    # Find current incomplete entry
    current_entry = None
    current_set = None
    pos_idx = 1
    total_entries = len(session.entries)

    for idx, entry in enumerate(session.entries, 1):
        incomplete = [s for s in entry.workout_sets if not s.completed]
        if incomplete and entry.status != "skipped":
            current_entry = entry
            current_set = incomplete[0]
            pos_idx = idx
            break

    if not current_entry or not current_set:
        # All sets completed! Finish session automatically
        async with AsyncSessionLocal() as db:
            finished = await sessions.finish_session(db, session.id, session.revision, feeling=session.feeling or "normal")
            await db.commit()
            summary_data = await summary.build_session_summary(db, finished)

        summary_html = render.render_summary_card(summary_data)
        kb = get_summary_keyboard(session.id)
        await state.clear()
        if edit:
            with contextlib.suppress(Exception):
                await target_message.edit_text(summary_html, parse_mode="HTML", reply_markup=kb)
        else:
            await target_message.answer(summary_html, parse_mode="HTML", reply_markup=kb)
        return

    # Determine current weight and reps
    cw = temp_w if temp_w is not None else (current_set.weight_kg or current_set.target_weight_kg or 0.0)
    cr = temp_r if temp_r is not None else (current_set.reps or current_set.target_reps or 10)
    await state.update_data(
        session_id=session.id,
        current_entry_id=current_entry.id,
        current_set_id=current_set.id,
        current_w=cw,
        current_r=cr,
    )

    # Format completed sets for this entry
    completed_sets = [
        {"idx": s.idx, "weight_kg": s.weight_kg, "reps": s.reps}
        for s in current_entry.workout_sets
        if s.completed
    ]

    ex_name = current_entry.canonical_exercise.display_name if current_entry.canonical_exercise else current_entry.raw_input
    card_html = render.render_live_card(
        workout_name=f"Сессия #{session.id}",
        exercise_pos=pos_idx,
        total_exercises=total_entries,
        duration_str="18:32",
        exercise_name=ex_name,
        set_idx=current_set.idx,
        total_sets=len(current_entry.workout_sets),
        target_weight=cw if cw > 0 else None,
        target_reps=cr,
        completed_sets=completed_sets,
        last_session_str="",
        machine_settings=current_entry.canonical_exercise.machine_settings if current_entry.canonical_exercise else None,
    )

    step = float(current_entry.canonical_exercise.weight_step) if current_entry.canonical_exercise and current_entry.canonical_exercise.weight_step else 2.5
    is_cardio = (current_entry.canonical_exercise.kind == "cardio") if current_entry.canonical_exercise else False

    kb = get_live_logging_keyboard(
        session_id=session.id,
        rev=session.revision,
        current_weight=cw if cw > 0 else None,
        current_reps=cr,
        weight_step=step,
        is_cardio=is_cardio,
        duration_mins=int((current_set.duration_sec or 480) / 60) if is_cardio else 8,
    )

    await state.set_state(TrainingFSM.live)
    if edit:
        with contextlib.suppress(Exception):
            await target_message.edit_text(card_html, parse_mode="HTML", reply_markup=kb)
    else:
        sent = await target_message.answer(card_html, parse_mode="HTML", reply_markup=kb)
        await state.update_data(anchor_message_id=sent.message_id)


@router.callback_query(F.data.startswith("t2:resume:"))
@router.callback_query(F.data.startswith("t2:start_plan:"))
async def handle_start_or_resume_live(callback: CallbackQuery, state: FSMContext):
    """Start live session from plan or resume existing active workout."""
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    ref_id = int(parts[2])
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        if action == "resume":
            session = await sessions.get_session_full(db, ref_id)
            if not session or session.status != "active":
                session = await sessions.get_active_session(db, uid)
        else:
            anchor_id = callback.message.message_id if callback.message else None
            session = await sessions.start_from_plan(db, uid, plan_id=ref_id, anchor_message_id=anchor_id)
        await db.commit()
        if session:
            session = await sessions.get_session_full(db, session.id)

    if not session:
        from src.handlers.training.hub import render_and_send_hub
        if callback.message:
            await render_and_send_hub(callback.message, uid, state, edit=True)
        return

    if callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:ok_set:"))
async def handle_log_set_ok(callback: CallbackQuery, state: FSMContext):
    """Log current set with preset weight and reps on 1 tap ✅."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    data = await state.get_data()
    entry_id = data.get("current_entry_id")
    cw = data.get("current_w")
    cr = data.get("current_r")

    set_dto = parser.ParsedSet(weight_kg=cw, reps=cr, set_type="working")

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.log_set(db, session_id, rev, entry_id, set_dto)
            await db.commit()
            session = await sessions.get_session_full(db, session_id)
        except sessions.StaleRevisionError:
            await callback.answer("Экран устарел, обновил")
            session = await sessions.get_session_full(db, session_id)
            if session and callback.message:
                await render_current_live_card(callback.message, session, state, edit=True)
            return

        with contextlib.suppress(Exception):
            from src.services.scheduler import scheduler
            scheduler.remove_job(f"rest_timer_{session_id}")

        # Check if the entry has any remaining incomplete sets
        entry = await db.get(WorkoutSessionEntry, entry_id)
        remaining = [s for s in entry.workout_sets if not s.completed] if entry else []

    await callback.answer("Подход записан ✓")

    if not remaining and callback.message:
        feel_kb = get_live_feeling_keyboard(session.id, session.revision)
        ex_name = entry.canonical_exercise.display_name if entry and entry.canonical_exercise else ""
        text = f"🏁 <b>{ex_name} завершено!</b>\n\nКак ощущение?"
        with contextlib.suppress(Exception):
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=feel_kb)
        return

    if callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:adj_w:"))
@router.callback_query(F.data.startswith("t2:adj_r:"))
async def handle_adjust_weight_reps(callback: CallbackQuery, state: FSMContext):
    """Adjust current weight or reps values on button without logging."""
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    session_id = int(parts[2])
    delta = float(parts[4])

    data = await state.get_data()
    cw = data.get("current_w") or 0.0
    cr = data.get("current_r") or 10

    if action == "adj_w":
        cw = max(0.0, round(cw + delta, 2))
        await state.update_data(current_w=cw)
    else:
        cr = max(1, int(cr + delta))
        await state.update_data(current_r=cr)

    async with AsyncSessionLocal() as db:
        session = await sessions.get_session_full(db, session_id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True, temp_w=cw, temp_r=cr)


@router.callback_query(F.data.startswith("t2:adj_c:"))
async def handle_adjust_cardio(callback: CallbackQuery, state: FSMContext):
    """Adjust cardio duration."""
    await callback.answer()
    parts = callback.data.split(":")
    session_id = int(parts[2])
    delta = int(parts[4])
    data = await state.get_data()
    set_id = data.get("current_set_id")

    async with AsyncSessionLocal() as db:
        if set_id:
            ws = await db.get(WorkoutSet, set_id)
            if ws:
                cur_sec = ws.duration_sec or 480
                ws.duration_sec = max(60, cur_sec + delta * 60)
                await db.commit()
        session = await sessions.get_session_full(db, session_id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:custom_live:"))
async def handle_custom_live(callback: CallbackQuery, state: FSMContext):
    """Prompt user to type custom reps and weight."""
    await callback.answer(
        "✍️ Отправьте вес и повторы сообщением в чат:\nНапример: 45 10 (45 кг на 10 повт) или 12 (12 повт)",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("t2:feel_ex:"))
async def handle_exercise_feeling(callback: CallbackQuery, state: FSMContext):
    """Record feeling for finished exercise and advance to next."""
    await callback.answer()
    parts = callback.data.split(":")
    session_id = int(parts[2])
    feel_val = parts[4]

    data = await state.get_data()
    entry_id = data.get("current_entry_id")

    async with AsyncSessionLocal() as db:
        entry = await db.get(WorkoutSessionEntry, entry_id)
        if entry:
            entry.feeling = feel_val
        await db.commit()
        session = await sessions.get_session_full(db, session_id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:skip_ex:"))
async def handle_skip_exercise(callback: CallbackQuery, state: FSMContext):
    """Skip remaining sets for current exercise and move to next."""
    await callback.answer("Упражнение пропущено")
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    data = await state.get_data()
    entry_id = data.get("current_entry_id")

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.skip_entry(db, session_id, rev, entry_id)
            await db.commit()
            session = await sessions.get_session_full(db, session_id)
        except sessions.StaleRevisionError:
            session = await sessions.get_session_full(db, session_id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:rest_timer:"))
async def handle_rest_timer(callback: CallbackQuery, state: FSMContext):
    """Handle rest timer: schedule APScheduler notification and alert user."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    sec = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 90

    data = await state.get_data()
    entry_id = data.get("current_entry_id")

    async with AsyncSessionLocal() as db:
        entry = await db.get(WorkoutSessionEntry, entry_id) if entry_id else None

    ex_name = entry.canonical_exercise.display_name if entry and entry.canonical_exercise else "Упражнение"

    from datetime import datetime, timedelta
    from src.services.scheduler import scheduler

    job_id = f"rest_timer_{session_id}"
    bot = getattr(callback, "bot", None) or (callback.message.bot if callback.message else None)
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id

    async def _send_rest_finished(b, c_id, name):
        try:
            await b.send_message(
                c_id,
                f"⏰ <b>Отдых окончен!</b> ({sec} с)\nПора переходить к следующему подходу в «{name}» 💪",
                parse_mode="HTML",
            )
        except Exception:
            pass

    run_date = datetime.now() + timedelta(seconds=sec)
    try:
        scheduler.add_job(
            _send_rest_finished,
            "date",
            run_date=run_date,
            args=[bot, chat_id, ex_name],
            id=job_id,
            replace_existing=True,
        )
    except Exception as e:
        logger.warning("Could not schedule rest timer: %s", e)

    await callback.answer(f"⏱ Таймер запущен ({sec} с) — пришлю сообщение, когда время выйдет!", show_alert=False)


@router.callback_query(F.data.startswith("t2:finish_live:"))
async def handle_finish_live(callback: CallbackQuery, state: FSMContext):
    """Finish live session early and show summary card."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.finish_session(db, session_id, rev, feeling="normal")
            await db.commit()
        except sessions.StaleRevisionError:
            sess_cur = await db.get(WorkoutSession, session_id)
            if sess_cur and sess_cur.status in ("active", "draft"):
                session = await sessions.finish_session(db, session_id, sess_cur.revision, feeling="normal")
                await db.commit()
            elif sess_cur and sess_cur.status == "finished":
                session = sess_cur
            else:
                await callback.answer("Сессия не найдена или уже завершена")
                return

        summary_data = await summary.build_session_summary(db, session)

    with contextlib.suppress(Exception):
        from src.services.scheduler import scheduler
        scheduler.remove_job(f"rest_timer_{session_id}")

    await callback.answer("Тренировка завершена ✓")
    await state.clear()

    summary_html = render.render_summary_card(summary_data)
    kb = get_summary_keyboard(session.id)
    if callback.message:
        await callback.message.edit_text(summary_html, parse_mode="HTML", reply_markup=kb)


@router.message(TrainingFSM.live)
async def handle_live_shorthand_text(message: Message, state: FSMContext):
    """Handle shorthand text input like '40 10' or '12' during live session."""
    text = message.text or ""
    data = await state.get_data()
    session_id = data.get("session_id")
    entry_id = data.get("current_entry_id")
    cw = data.get("current_w")
    cr = data.get("current_r")

    target = parser.Target(weight=cw, reps=cr)
    parsed_sets = parser.parse_live_shorthand(text, target)

    if not parsed_sets:
        await message.answer("Не понял запись. Пример: <code>45 10</code> или <code>12</code>", parse_mode="HTML")
        return

    with contextlib.suppress(Exception):
        await message.delete()

    async with AsyncSessionLocal() as db:
        session = await sessions.get_session_full(db, session_id)
        if session:
            for s in parsed_sets:
                session = await sessions.log_set(db, session.id, session.revision, entry_id, s)
            await db.commit()
            session = await sessions.get_session_full(db, session_id)

    anchor_id = data.get("anchor_message_id")
    if anchor_id and session:
        fake_msg = Message(
            message_id=anchor_id,
            chat=message.chat,
            date=message.date,
            bot=message.bot,
        )
        await render_current_live_card(fake_msg, session, state, edit=True)

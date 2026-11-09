"""Scenario A: live workout logging with 1-tap set completion, weights adjustments, and rest timer."""
from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.utils.training_locales import group_label, t
from src.database.connection import AsyncSessionLocal
from sqlalchemy import select

from src.database.models import CanonicalExercise, TrainingProfile, WorkoutSession, WorkoutSessionEntry, WorkoutSet
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import (
    get_abort_confirm_keyboard,
    get_live_feeling_keyboard,
    get_live_logging_keyboard,
    get_summary_keyboard,
    make_cb,
)
from src.handlers.training.states import TrainingFSM
from src.services.training import history, parser, recovery, render, sessions, summary

logger = logging.getLogger(__name__)
router = Router(name="training_live_router")


def _workout_title(session: WorkoutSession) -> str:
    """Human title from the session focus (e.g. '🪵 Спина · 🛡 Грудь'); never the internal id."""
    labels = [group_label(f) for f in (session.focus or []) if f][:2]
    return " · ".join(labels) or t("live_default_title")


async def _last_time_hint(state: FSMContext, user_id: int, canonical_id: Optional[int], entry_id: int) -> str:
    """'40 × 12' from the previous finished session; cached in FSM per entry to avoid re-querying on every tap."""
    if not canonical_id:
        return ""
    data = await state.get_data()
    cached = data.get("last_hint")
    if cached and cached.get("entry_id") == entry_id:
        return cached["text"]
    async with AsyncSessionLocal() as db:
        hist = await history.get_exercise_history(db, user_id, canonical_id)
    text = ""
    if hist and hist.working_sets:
        w = render._fmt_w(hist.top_weight)
        text = f"{w} × {hist.median_reps}" if hist.top_weight else f"{hist.median_reps} {t('unit_reps')}"
    await state.update_data(last_hint={"entry_id": entry_id, "text": text})
    return text


def _suggested_rest_sec(exercise) -> int:
    """Rest suggestion: isolation work recovers faster than compound lifts."""
    return 75 if exercise is not None and exercise.pattern == "isolation" else 120


def _schedule_rest(bot, chat_id: int, session_id: int, sec: int, text: str) -> bool:
    """Schedule the "rest is over" ping (one job per session, replacing any earlier one). True when scheduled."""
    from datetime import datetime, timedelta

    from src.services.scheduler import scheduler

    async def _send_rest_finished(b, c_id, message_text):
        try:
            await b.send_message(c_id, message_text, parse_mode="HTML")
        except Exception:
            logger.debug("Rest-over message failed", exc_info=True)

    try:
        scheduler.add_job(
            _send_rest_finished,
            "date",
            run_date=datetime.now() + timedelta(seconds=sec),
            args=[bot, chat_id, text],
            id=f"rest_timer_{session_id}",
            replace_existing=True,
        )
        return True
    except Exception as e:
        logger.warning("Could not schedule rest timer: %s", e)
        return False


def _elapsed_str(started_at: Optional[datetime]) -> str:
    """Format time since a naive-UTC start as M:SS or H:MM:SS."""
    if not started_at:
        return "0:00"
    total = max(0, int((datetime.now(UTC).replace(tzinfo=None) - started_at).total_seconds()))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


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
            try:
                finished = await sessions.finish_session(db, session.id, session.revision)
            except sessions.EmptyWorkoutError:
                await sessions.cancel_session(db, session.id, session.revision)
                await db.commit()
                await state.clear()
                from src.handlers.training.hub import render_and_send_hub
                await render_and_send_hub(target_message, session.user_id, state, edit=edit)
                return
            await db.commit()
            summary_data = await summary.build_session_summary(db, finished)

        summary_html = render.render_summary_card(summary_data)
        kb = get_summary_keyboard(session.id)
        await state.clear()
        if edit:
            await edit_or_send(target_message, summary_html, parse_mode="HTML", reply_markup=kb)
        else:
            await target_message.answer(summary_html, parse_mode="HTML", reply_markup=kb)
        return

    # Determine current weight and reps
    cw = float(temp_w if temp_w is not None else (current_set.weight_kg or current_set.target_weight_kg or 0.0))
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
        {"idx": s.idx, "weight_kg": float(s.weight_kg) if s.weight_kg is not None else None, "reps": s.reps}
        for s in current_entry.workout_sets
        if s.completed
    ]

    ex_name = current_entry.canonical_exercise.display_name if current_entry.canonical_exercise else current_entry.raw_input
    card_html = render.render_live_card(
        workout_name=_workout_title(session),
        exercise_pos=pos_idx,
        total_exercises=total_entries,
        duration_str=_elapsed_str(session.started_at),
        exercise_name=ex_name,
        set_idx=current_set.idx,
        total_sets=len(current_entry.workout_sets),
        target_weight=cw if cw > 0 else None,
        target_reps=cr,
        completed_sets=completed_sets,
        last_session_str=await _last_time_hint(
            state, session.user_id, current_entry.canonical_exercise_id, current_entry.id
        ),
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
        rest_sec=_suggested_rest_sec(current_entry.canonical_exercise),
        open_sets=len([s for s in current_entry.workout_sets if not s.completed]),
    )

    await state.set_state(TrainingFSM.live)
    if edit:
        shown_id = await edit_or_send(target_message, card_html, parse_mode="HTML", reply_markup=kb)
        if shown_id != target_message.message_id:
            await state.update_data(anchor_message_id=shown_id)
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
            session = await sessions.get_session_full(db, ref_id, user_id=callback.from_user.id)
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
            session = await sessions.log_set(db, session_id, rev, entry_id, set_dto, user_id=callback.from_user.id)
            await db.commit()
        except sessions.StaleRevisionError:
            await callback.answer(t("stale"))
            session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)
            if session and callback.message:
                await render_current_live_card(callback.message, session, state, edit=True)
            return

        with contextlib.suppress(Exception):
            from src.services.scheduler import scheduler
            scheduler.remove_job(f"rest_timer_{session_id}")

        # log_set already returned the full, fresh tree: no need to re-query the entry
        entry = next((e for e in session.entries if e.id == entry_id), None)
        remaining = [s for s in entry.workout_sets if not s.completed] if entry else []

        # Opt-in automatic rest timer: only between sets of the same exercise
        auto_rest_sec = 0
        if remaining and entry:
            profile = await db.get(TrainingProfile, callback.from_user.id)
            if profile and profile.rest_timer:
                auto_rest_sec = _suggested_rest_sec(entry.canonical_exercise)

    if auto_rest_sec and callback.message and _schedule_rest(
        callback.bot, callback.message.chat.id, session_id, auto_rest_sec,
        t("rest_over", sec=auto_rest_sec, name=entry.canonical_exercise.display_name if entry.canonical_exercise else t("ex_fallback")),
    ):
        await callback.answer(t("set_logged_rest", sec=auto_rest_sec))
    else:
        await callback.answer(t("set_logged"))

    if not remaining and callback.message:
        feel_kb = get_live_feeling_keyboard(session.id, session.revision)
        ex_name = entry.canonical_exercise.display_name if entry and entry.canonical_exercise else ""
        text = t("ex_done_feel", name=ex_name)
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=feel_kb)
        return

    if callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:ok_all:"))
async def handle_complete_as_planned(callback: CallbackQuery, state: FSMContext):
    """Complete all open sets of the current exercise at their planned values (1 tap)."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])
    entry_id = (await state.get_data()).get("current_entry_id")

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.complete_entry_as_planned(
                db, session_id, rev, entry_id, user_id=callback.from_user.id,
            )
            await db.commit()
        except (sessions.StaleRevisionError, ValueError):
            await callback.answer(t("stale"))
            session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)
            if session and callback.message:
                await render_current_live_card(callback.message, session, state, edit=True)
            return

        entry = next((e for e in session.entries if e.id == entry_id), None)
        still_open = bool(entry and any(not s.completed for s in entry.workout_sets))

    await callback.answer(t("set_logged"))
    if not still_open and callback.message:
        ex_name = entry.canonical_exercise.display_name if entry and entry.canonical_exercise else ""
        await edit_or_send(
            callback.message,
            t("ex_done_feel", name=ex_name),
            parse_mode="HTML",
            reply_markup=get_live_feeling_keyboard(session.id, session.revision),
        )
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
    cw = float(data.get("current_w") or 0.0)
    cr = data.get("current_r") or 10

    if action == "adj_w":
        cw = max(0.0, round(cw + delta, 2))
        await state.update_data(current_w=cw)
    else:
        cr = max(1, int(cr + delta))
        await state.update_data(current_r=cr)

    async with AsyncSessionLocal() as db:
        session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)

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
        session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:custom_live:"))
async def handle_custom_live(callback: CallbackQuery, state: FSMContext):
    """Prompt user to type custom reps and weight."""
    await callback.answer(
        t("custom_prompt"),
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
        session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:skip_ex:"))
async def handle_skip_exercise(callback: CallbackQuery, state: FSMContext):
    """Skip remaining sets for current exercise and move to next."""
    await callback.answer(t("ex_skipped"))
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    data = await state.get_data()
    entry_id = data.get("current_entry_id")

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.skip_entry(db, session_id, rev, entry_id, user_id=callback.from_user.id)
            await db.commit()
            session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)
        except sessions.StaleRevisionError:
            session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)

    if session and callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:undo_set:"))
async def handle_undo_set(callback: CallbackQuery, state: FSMContext):
    """Undo the most recently logged set and re-render the live card."""
    parts = callback.data.split(":")
    session_id, rev = int(parts[2]), int(parts[3])

    async with AsyncSessionLocal() as db:
        try:
            session = await sessions.undo_last_set(db, session_id, rev, callback.from_user.id)
            await db.commit()
        except sessions.StaleRevisionError:
            await callback.answer(t("stale"))
            session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)
            if session and session.user_id == callback.from_user.id and callback.message:
                await render_current_live_card(callback.message, session, state, edit=True)
            return
        except ValueError:
            await callback.answer(t("session_gone"))
            return

    if session is None:
        await callback.answer(t("nothing_to_undo"))
        return
    await callback.answer(t("set_undone"))
    if callback.message:
        await render_current_live_card(callback.message, session, state, edit=True)


@router.callback_query(F.data.startswith("t2:abort_live:"))
async def handle_abort_live(callback: CallbackQuery, state: FSMContext):
    """Ask for confirmation before discarding the live workout."""
    parts = callback.data.split(":")
    session_id, rev = int(parts[2]), int(parts[3])
    await callback.answer()
    if callback.message:
        await edit_or_send(callback.message, t("abort_confirm"), reply_markup=get_abort_confirm_keyboard(session_id, rev))


@router.callback_query(F.data.startswith("t2:abort_yes:"))
async def handle_abort_confirmed(callback: CallbackQuery, state: FSMContext):
    """Delete the live session (the plan becomes startable again) and return to the hub."""
    from src.handlers.training.hub import render_and_send_hub
    from src.services.scheduler import scheduler

    parts = callback.data.split(":")
    session_id = int(parts[2])

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != callback.from_user.id:
            await callback.answer(t("session_gone"))
            return
        try:
            await sessions.cancel_session(db, session_id, session.revision, user_id=callback.from_user.id)  # explicit confirmation: any revision
            await db.commit()
        except sessions.StaleRevisionError:
            await callback.answer(t("stale"))
            return

    with contextlib.suppress(Exception):
        scheduler.remove_job(f"rest_timer_{session_id}")
    await state.clear()
    await callback.answer(t("aborted"))
    if callback.message:
        await render_and_send_hub(callback.message, callback.from_user.id, state, edit=True)


@router.callback_query(F.data.startswith("t2:rest_timer:"))
async def handle_rest_timer(callback: CallbackQuery, state: FSMContext):
    """Handle rest timer: schedule APScheduler notification and alert user."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    sec = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 90

    data = await state.get_data()
    entry_id = data.get("current_entry_id")

    # One scalar query: avoids lazy-loading a relationship on an instance whose session is already closed, and
    # scopes the lookup to the caller's own session.
    ex_name = None
    if entry_id:
        async with AsyncSessionLocal() as db:
            ex_name = await db.scalar(
                select(CanonicalExercise.display_name)
                .join(WorkoutSessionEntry, WorkoutSessionEntry.canonical_exercise_id == CanonicalExercise.id)
                .join(WorkoutSession, WorkoutSession.id == WorkoutSessionEntry.session_id)
                .where(
                    WorkoutSessionEntry.id == entry_id,
                    WorkoutSession.id == session_id,
                    WorkoutSession.user_id == callback.from_user.id,
                )
            )
    ex_name = ex_name or t("ex_fallback")

    bot = getattr(callback, "bot", None) or (callback.message.bot if callback.message else None)
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    # Built now: the scheduled job runs outside this update, where the per-update language is no longer set.
    ok = _schedule_rest(bot, chat_id, session_id, sec, t("rest_over", sec=sec, name=ex_name))

    await callback.answer(t("timer_started", sec=sec) if ok else t("timer_failed"), show_alert=False)


@router.callback_query(F.data.startswith("t2:finish_live:"))
async def handle_finish_live(callback: CallbackQuery, state: FSMContext):
    """Finish live session early and show summary card."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        owned = await db.get(WorkoutSession, session_id)
        if not owned or owned.user_id != callback.from_user.id:
            await callback.answer(t("session_gone"))
            return
        try:
            try:
                session = await sessions.finish_session(db, session_id, rev, user_id=callback.from_user.id)
                await db.commit()
            except sessions.EmptyWorkoutError:
                await sessions.cancel_session(db, session_id, owned.revision)
                await db.commit()
                await state.clear()
                await callback.answer(t("nothing_logged"), show_alert=True)
                from src.handlers.training.hub import render_and_send_hub
                if callback.message:
                    await render_and_send_hub(callback.message, callback.from_user.id, state, edit=True)
                return
        except sessions.StaleRevisionError:
            sess_cur = await db.get(WorkoutSession, session_id)
            if sess_cur and sess_cur.status in ("active", "draft"):
                session = await sessions.finish_session(db, session_id, sess_cur.revision, user_id=callback.from_user.id)
                await db.commit()
            elif sess_cur and sess_cur.status == "finished":
                session = sess_cur
            else:
                await callback.answer(t("session_gone"))
                return

        summary_data = await summary.build_session_summary(db, session)

    with contextlib.suppress(Exception):
        from src.services.scheduler import scheduler
        scheduler.remove_job(f"rest_timer_{session_id}")

    await callback.answer(t("workout_finished"))
    await state.clear()

    summary_html = render.render_summary_card(summary_data)
    kb = get_summary_keyboard(session.id)
    if callback.message:
        await edit_or_send(callback.message, summary_html, parse_mode="HTML", reply_markup=kb)


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
        await message.answer(t("live_parse_fail"), parse_mode="HTML")
        return

    with contextlib.suppress(Exception):
        await message.delete()

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id) if session_id else None
        if session and session.user_id != message.from_user.id:
            session = None
        if session:
            for s in parsed_sets:
                session = await sessions.log_set(db, session.id, session.revision, entry_id, s, user_id=message.from_user.id)
            await db.commit()  # log_set returns the fresh full tree; expire_on_commit=False keeps it usable

    anchor_id = data.get("anchor_message_id")
    if anchor_id and session:
        fake_msg = Message(
            message_id=anchor_id,
            chat=message.chat,
            date=message.date,
            bot=message.bot,
        )
        await render_current_live_card(fake_msg, session, state, edit=True)

"""Scenario B: quick multi-line text input with parsed draft verification."""
from __future__ import annotations

import contextlib
import logging
from typing import Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.utils.training_locales import t
from src.database.connection import AsyncSessionLocal
from src.database.models import WorkoutSession, WorkoutSessionEntry
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_draft_keyboard, get_summary_keyboard, make_cb
from src.handlers.training.states import TrainingFSM
from src.services.training import library, llm, parser, render, sessions, summary

logger = logging.getLogger(__name__)
router = Router(name="training_log_text_router")


@router.callback_query(F.data.startswith("t2:write_done"))
@router.callback_query(F.data.startswith("t2:log_first"))
async def start_text_logging(callback: CallbackQuery, state: FSMContext):
    """Prompt user for multiline text input."""
    await callback.answer()
    uid = callback.from_user.id
    async with AsyncSessionLocal() as db:
        session = await sessions.get_or_create_draft(db, uid, source="text", anchor_message_id=callback.message.message_id)

    prompt_text = (
        t("log_prompt")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_cancel_back"), callback_data=make_cb("hub", 0, 0))]
    ])
    await state.set_state(TrainingFSM.draft)
    await state.update_data(session_id=session.id)
    if callback.message:
        await edit_or_send(callback.message, prompt_text, parse_mode="HTML", reply_markup=kb)


@router.message(TrainingFSM.draft)
async def handle_draft_text_input(message: Message, state: FSMContext):
    """Parse incoming multiline workout text, delete user message, and update anchor draft message."""
    text = message.text or ""
    uid = message.from_user.id
    data = await state.get_data()
    anchor_id = data.get("anchor_message_id")
    session_id = data.get("session_id")

    # 1. Fetch user known exercise names
    async with AsyncSessionLocal() as db:
        user_library = await library.get_user_library(db, uid)
        lib_names = [e.display_name for e in user_library]
        session = await db.get(WorkoutSession, session_id) if session_id else None
        if not session:
            session = await sessions.get_or_create_draft(db, uid, source="text", anchor_message_id=anchor_id)
            session_id = session.id
            await state.update_data(session_id=session_id)

    # 2. Deterministic parsing
    draft_items = parser.parse_text(text, lang="ru", library_names=lib_names)

    # 3. If any item is unparsed, attempt graceful LLM recovery
    unparsed = [it for it in draft_items if it.status == "unparsed"]
    if unparsed:
        recovered = await llm.parse_unparsed_lines(unparsed, known_exercises=lib_names, lang="ru")
        rec_map = {r.line_no: r for r in recovered}
        draft_items = [rec_map.get(it.line_no, it) for it in draft_items]

    # 4. Save items to draft session in DB
    async with AsyncSessionLocal() as db:
        session = await sessions.add_draft_items(db, session, draft_items)

    # 5. Delete user message if parsing succeeded
    with contextlib.suppress(Exception):
        await message.delete()

    # 6. Render updated draft card
    card_html = render.render_draft_card(draft_items, feeling=session.feeling or "normal")
    kb = get_draft_keyboard(session.id, session.revision, current_feeling=session.feeling or "normal")

    # Edit anchor message
    edited = False
    if anchor_id:
        with contextlib.suppress(Exception):
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=card_html,
                parse_mode="HTML",
                reply_markup=kb,
            )
            edited = True

    if not edited:
        sent = await message.answer(card_html, parse_mode="HTML", reply_markup=kb)
        await state.update_data(anchor_message_id=sent.message_id)


@router.callback_query(F.data.startswith("t2:dfeel:"))
async def handle_draft_feeling_toggle(callback: CallbackQuery, state: FSMContext):
    """Toggle overall draft feeling (🟢, 🟡, 🔴)."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])
    feel_val = parts[4]

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != callback.from_user.id or session.revision != rev:
            await callback.answer(t("stale"), show_alert=False)
            return

        session.feeling = feel_val
        session.revision += 1
        await db.flush()

        # Re-fetch session entries
        session = await sessions.get_or_create_draft(db, session.user_id)

    await callback.answer()
    # Re-render
    async with AsyncSessionLocal() as db:
        session = await sessions.get_or_create_draft(db, callback.from_user.id)
        # Build items representation
        items = []
        for idx, entry in enumerate(session.entries, 1):
            items.append(
                parser.DraftItem(
                    line_no=idx,
                    raw=entry.raw_input,
                    name=entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input,
                    sets=[parser.ParsedSet(weight_kg=s.weight_kg, reps=s.reps, duration_sec=s.duration_sec, distance_m=s.distance_m) for s in entry.workout_sets],
                    note=entry.note,
                    status="ok",
                )
            )

    card_html = render.render_draft_card(items, feeling=feel_val)
    kb = get_draft_keyboard(session.id, session.revision, current_feeling=feel_val)
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:save_draft:"))
async def handle_save_draft(callback: CallbackQuery, state: FSMContext):
    """Save draft workout session, calculate summary, and show final summary card."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != callback.from_user.id or session.revision != rev:
            await callback.answer(t("stale_alert"), show_alert=True)
            return

        feeling = session.feeling or "normal"
        finished_session = await sessions.finish_session(db, session_id, rev, feeling=feeling, user_id=callback.from_user.id)
        summary_data = await summary.build_session_summary(db, finished_session)

    await callback.answer(t("saved"))
    await state.clear()

    summary_html = render.render_summary_card(summary_data)
    kb = get_summary_keyboard(session_id)
    if callback.message:
        await edit_or_send(callback.message, summary_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:undo_draft:"))
async def handle_undo_last_draft_item(callback: CallbackQuery, state: FSMContext):
    """Remove the most recent entry from draft."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        # entries must be eagerly loaded: a lazy load on an async session raises MissingGreenlet
        session = await sessions.get_session_full(db, session_id, user_id=callback.from_user.id)
        if not session or session.revision != rev:
            await callback.answer(t("stale_alert"))
            return

        if session.entries:
            last_entry = session.entries[-1]
            await db.delete(last_entry)
            session.revision += 1
            await db.flush()

        session = await sessions.get_or_create_draft(db, callback.from_user.id)

    await callback.answer(t("removed"))
    # Redraw
    items = []
    for idx, entry in enumerate(session.entries, 1):
        items.append(
            parser.DraftItem(
                line_no=idx,
                raw=entry.raw_input,
                name=entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input,
                sets=[parser.ParsedSet(weight_kg=s.weight_kg, reps=s.reps, duration_sec=s.duration_sec, distance_m=s.distance_m) for s in entry.workout_sets],
                note=entry.note,
                status="ok",
            )
        )
    card_html = render.render_draft_card(items, feeling=session.feeling or "normal")
    kb = get_draft_keyboard(session.id, session.revision, current_feeling=session.feeling or "normal")
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:cancel_draft:"))
async def handle_cancel_draft(callback: CallbackQuery, state: FSMContext):
    """Discard draft session and return to hub."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        await sessions.cancel_session(db, session_id, rev, user_id=callback.from_user.id)

    await callback.answer(t("draft_cancelled"))
    from src.handlers.training.hub import render_and_send_hub
    if callback.message:
        await render_and_send_hub(callback.message, callback.from_user.id, state, edit=True)

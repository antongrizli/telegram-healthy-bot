"""Scenario B: quick multi-line text input with parsed draft verification."""
from __future__ import annotations

import contextlib
import difflib
import logging
from typing import Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.utils.training_locales import t
from src.database.connection import AsyncSessionLocal
from src.database.models import User, WorkoutSession, WorkoutSessionEntry
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_draft_keyboard, get_summary_keyboard, make_cb
from src.handlers.training.states import TrainingFSM
from src.services.training import history, library, llm, parser, render, sessions, summary
from src.services.training.plan_context import load_plan_context
from src.services.training.report_text import clean_report

logger = logging.getLogger(__name__)
router = Router(name="training_log_text_router")


@router.callback_query(F.data.startswith("t2:write_done"))
@router.callback_query(F.data.startswith("t2:log_first"))
async def start_text_logging(callback: CallbackQuery, state: FSMContext):
    """Prompt user for multiline text input, or resume existing non-empty draft."""
    await callback.answer()
    uid = callback.from_user.id
    anchor_id = callback.message.message_id if callback.message else None
    async with AsyncSessionLocal() as db:
        session = await sessions.get_or_create_draft(db, uid, source="text", anchor_message_id=anchor_id)
        await db.commit()

    await state.set_state(TrainingFSM.draft)
    await state.update_data(session_id=session.id, draft_pending=[], anchor_message_id=anchor_id)
    if session.entries and callback.message:
        await _render_draft_state(callback, state, session)
        return

    prompt_text = t("log_prompt")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_cancel_back"), callback_data=make_cb("hub", 0, 0))]
    ])
    if callback.message:
        await edit_or_send(callback.message, prompt_text, parse_mode="HTML", reply_markup=kb)


def _session_items(session: WorkoutSession) -> list:
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
    return items


def _pending_items(pending: list) -> list:
    return [parser.DraftItem(**p["item"]) for p in pending]


async def _render_draft_state(target, state: FSMContext, session: WorkoutSession, prior_items: list | None = None) -> None:
    """Redraw the draft card: saved entries, unresolved new machines (🆕) and their decision buttons."""
    data = await state.get_data()
    pending = data.get("draft_pending") or []
    items = (prior_items if prior_items is not None else _session_items(session)) + _pending_items(pending)
    feeling = session.feeling or "normal"
    card_html = render.render_draft_card(items, feeling=feeling)
    kb = get_draft_keyboard(
        session.id,
        session.revision,
        current_feeling=feeling,
        pending=[{"name": p["item"]["name"], "near": p.get("near")} for p in pending],
    )
    anchor_id = data.get("anchor_message_id")
    if isinstance(target, CallbackQuery):
        if target.message:
            await edit_or_send(target.message, card_html, parse_mode="HTML", reply_markup=kb)
        return
    edited = False
    if anchor_id:
        with contextlib.suppress(Exception):
            await target.bot.edit_message_text(
                chat_id=target.chat.id, message_id=anchor_id, text=card_html, parse_mode="HTML", reply_markup=kb,
            )
            edited = True
    if not edited:
        sent = await target.answer(card_html, parse_mode="HTML", reply_markup=kb)
        await state.update_data(anchor_message_id=sent.message_id)


@router.message(TrainingFSM.draft)
async def handle_draft_text_input(message: Message, state: FSMContext):
    """Clean and parse incoming workout text; unknown machines wait for the user's decision, never auto-added."""
    uid = message.from_user.id
    data = await state.get_data()
    anchor_id = data.get("anchor_message_id")
    session_id = data.get("session_id")
    report = clean_report(message.text or "")
    text = report.body

    # 1. Fetch user known exercise names
    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        ctx = await load_plan_context(db, uid)
        lib_names = [e["display_name"] for e in ctx.library]
        history_index = ctx.history_index
        session = await db.get(WorkoutSession, session_id) if session_id else None
        if not session or session.user_id != uid or session.status != "draft":
            session = await sessions.get_or_create_draft(db, uid, source="text", anchor_message_id=anchor_id)
            session_id = session.id
            await state.update_data(session_id=session_id)
            await db.commit()

    # 2. Deterministic parsing
    draft_items = parser.parse_text(text, lang=lang, library_names=lib_names, history_index=history_index)

    # 3. If any item is unparsed, attempt graceful LLM recovery
    unparsed = [it for it in draft_items if it.status == "unparsed"]
    if unparsed:
        recovered = await llm.parse_unparsed_lines(unparsed, known_exercises=lib_names, lang=lang)
        rec_map = {r.line_no: r for r in recovered}
        draft_items = [rec_map.get(it.line_no, it) for it in draft_items]

    # 4. Unknown machines (neither in library nor catalog) are held back for confirmation
    pending = list(data.get("draft_pending") or [])
    async with AsyncSessionLocal() as db:
        for it in draft_items:
            if it.status in ("unparsed", "new_name") or not it.name:
                continue
            _, how = await library.match_exercise(db, uid, it.name)
            if how == "unknown":
                it.status = "new_name"
                near = difflib.get_close_matches(it.name, lib_names, n=1, cutoff=0.6)
                pending.append({"item": it.model_dump(), "near": near[0] if near else None})
    await state.update_data(draft_pending=pending)

    # 5. Save the rest to the draft session in DB (unparsed/new_name rows never create library machines)
    async with AsyncSessionLocal() as db:
        session = await sessions.get_session_full(db, session_id, user_id=uid)
        if not session or session.status != "draft":
            session = await sessions.get_or_create_draft(db, uid, source="text", anchor_message_id=anchor_id)
        session = await sessions.add_draft_items(db, session, draft_items)
        if report.feeling:
            session.feeling = report.feeling
        if report.note:
            session.note = "\n".join(filter(None, [session.note, report.note]))[:2000]
        await db.commit()

    # 6. Delete user message if parsing succeeded
    with contextlib.suppress(Exception):
        await message.delete()

    # 7. Render updated draft card
    unparsed_left = [it for it in draft_items if it.status == "unparsed"]
    await _render_draft_state(message, state, session, prior_items=_session_items(session) + unparsed_left)


async def _resolve_pending(callback: CallbackQuery, state: FSMContext, action: str) -> None:
    parts = callback.data.split(":")
    session_id, rev, idx = int(parts[2]), int(parts[3]), int(parts[4])
    uid = callback.from_user.id
    data = await state.get_data()
    pending = list(data.get("draft_pending") or [])
    if idx >= len(pending):
        await callback.answer(t("stale"))
        return
    entry = pending.pop(idx)
    item = parser.DraftItem(**entry["item"])

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != uid:
            await callback.answer(t("stale_alert"), show_alert=True)
            return
        if action != "drop":
            if action == "map" and entry.get("near"):
                item.name = entry["near"]
            else:
                user = await db.get(User, uid)
                await library.get_or_create_canonical(
                    db, user_id=uid, name=item.name, kind=item.kind, tag_with_llm=True,
                    lang=user.language if user and user.language else "ru",
                )
            item.status = "ok"
            session = await db.get(WorkoutSession, session_id)
            full = await sessions.get_session_full(db, session_id, user_id=uid)
            session = await sessions.add_draft_items(db, full, [item])
        else:
            session.revision += 1
        await db.commit()
        session = await sessions.get_session_full(db, session_id, user_id=uid)

    await state.update_data(draft_pending=pending)
    await callback.answer()
    await _render_draft_state(callback, state, session)


@router.callback_query(F.data.startswith("t2:dn_add:"))
async def handle_new_name_add(callback: CallbackQuery, state: FSMContext):
    await _resolve_pending(callback, state, "add")


@router.callback_query(F.data.startswith("t2:dn_map:"))
async def handle_new_name_map(callback: CallbackQuery, state: FSMContext):
    await _resolve_pending(callback, state, "map")


@router.callback_query(F.data.startswith("t2:dn_drop:"))
async def handle_new_name_drop(callback: CallbackQuery, state: FSMContext):
    await _resolve_pending(callback, state, "drop")


@router.callback_query(F.data.startswith("t2:dfeel:"))
async def handle_draft_feeling_toggle(callback: CallbackQuery, state: FSMContext):
    """Toggle overall draft feeling (🟢, 🟡, 🔴)."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])
    feel_val = parts[4]
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != uid or session.revision != rev:
            await callback.answer(t("stale"), show_alert=False)
            return

        session.feeling = feel_val
        session.revision += 1
        await db.commit()
        session = await sessions.get_session_full(db, session_id, user_id=uid)

    await callback.answer()
    if session:
        await _render_draft_state(callback, state, session)


@router.callback_query(F.data.startswith("t2:save_draft:"))
async def handle_save_draft(callback: CallbackQuery, state: FSMContext):
    """Save draft workout session, calculate summary, and show final summary card."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    if (await state.get_data()).get("draft_pending"):
        await callback.answer(t("dn_pending"), show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        session = await db.get(WorkoutSession, session_id)
        if not session or session.user_id != callback.from_user.id or session.revision != rev:
            await callback.answer(t("stale_alert"), show_alert=True)
            return

        feeling = session.feeling or "normal"
        finished_session = await sessions.finish_session(db, session_id, rev, feeling=feeling, user_id=callback.from_user.id)
        summary_data = await summary.build_session_summary(db, finished_session)
        await db.commit()

    await callback.answer(t("saved"))
    await state.set_state(TrainingFSM.hub)
    await state.set_data({})

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
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        # entries must be eagerly loaded: a lazy load on an async session raises MissingGreenlet
        session = await sessions.get_session_full(db, session_id, user_id=uid)
        if not session or session.revision != rev:
            await callback.answer(t("stale_alert"))
            return

        if session.entries:
            last_entry = sorted(session.entries, key=lambda e: e.position)[-1]
            await db.delete(last_entry)
            session.revision += 1
            await db.flush()
            await db.commit()
            db.expire_all()
            session = await sessions.get_session_full(db, session_id, user_id=uid)

    await callback.answer(t("removed"))
    pending = (await state.get_data()).get("draft_pending") or []
    if session and (session.entries or pending):
        await _render_draft_state(callback, state, session)
    elif callback.message:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("kb_cancel_back"), callback_data=make_cb("hub", 0, 0))]
        ])
        await edit_or_send(callback.message, t("log_prompt"), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:cancel_draft:"))
async def handle_cancel_draft(callback: CallbackQuery, state: FSMContext):
    """Discard draft session and return to hub."""
    parts = callback.data.split(":")
    session_id = int(parts[2])
    rev = int(parts[3])

    async with AsyncSessionLocal() as db:
        try:
            await sessions.cancel_session(db, session_id, rev, user_id=callback.from_user.id)
        except sessions.StaleRevisionError:
            await callback.answer(t("stale_alert"), show_alert=True)
            return
        await db.commit()

    await state.set_data({})
    await callback.answer(t("draft_cancelled"))
    from src.handlers.training.hub import render_and_send_hub
    if callback.message:
        await render_and_send_hub(callback.message, callback.from_user.id, state, edit=True)

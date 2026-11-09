"""Scenario plan creation: goal picker, plan card, exercise replacement, and budget resizing."""
from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.utils.training_locales import current_lang, group_label, t
from src.database.connection import AsyncSessionLocal
from src.database.models import TrainingPlan, TrainingProfile, User
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_goal_picker_keyboard, get_plan_card_keyboard, make_cb
from src.handlers.training.states import TrainingFSM
from src.services.training.plan_context import load_plan_context
from src.services.training import history, library, llm, planner, render
from src.services.training.focus import groups_for, resolve_focus

logger = logging.getLogger(__name__)
router = Router(name="training_plan_router")


@router.callback_query(F.data.startswith("t2:goal_menu"))
async def open_goal_menu(callback: CallbackQuery, state: FSMContext):
    """Open '🎯 Другая цель' goal selection screen."""
    await callback.answer()
    await state.set_state(TrainingFSM.goal)
    await state.update_data(
        selected_muscles=["back"],
        target_mins=45,
        target_energy="normal",
        understood_note=None,
    )

    card_html = render.render_goal_picker(selected_muscles=["back"], minutes=45, energy="normal")
    kb = get_goal_picker_keyboard(selected_muscles=["back"], current_mins=45, current_energy="normal")
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:tgl_m:"))
async def toggle_goal_muscle(callback: CallbackQuery, state: FSMContext):
    """Toggle a muscle group button in goal picker."""
    await callback.answer()
    parts = callback.data.split(":")
    muscle_key = parts[4]

    data = await state.get_data()
    selected = list(data.get("selected_muscles") or [])
    if muscle_key in selected:
        selected.remove(muscle_key)
    else:
        selected.append(muscle_key)
    await state.update_data(selected_muscles=selected)

    mins = data.get("target_mins", 45)
    en = data.get("target_energy", "normal")
    note = data.get("understood_note")

    card_html = render.render_goal_picker(selected, minutes=mins, energy=en, understood_note=note)
    kb = get_goal_picker_keyboard(selected, current_mins=mins, current_energy=en)
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:set_time:"))
async def set_goal_time(callback: CallbackQuery, state: FSMContext):
    """Set workout budget time (30, 45, 60, 90 mins)."""
    await callback.answer()
    parts = callback.data.split(":")
    mins = int(parts[4])
    await state.update_data(target_mins=mins)

    data = await state.get_data()
    selected = list(data.get("selected_muscles") or [])
    en = data.get("target_energy", "normal")
    note = data.get("understood_note")

    card_html = render.render_goal_picker(selected, minutes=mins, energy=en, understood_note=note)
    kb = get_goal_picker_keyboard(selected, current_mins=mins, current_energy=en)
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:set_en:"))
async def set_goal_energy(callback: CallbackQuery, state: FSMContext):
    """Set energy / feeling before workout (😴, 🙂, 💪)."""
    await callback.answer()
    parts = callback.data.split(":")
    en = parts[4]
    await state.update_data(target_energy=en)

    data = await state.get_data()
    selected = list(data.get("selected_muscles") or [])
    mins = data.get("target_mins", 45)
    note = data.get("understood_note")

    card_html = render.render_goal_picker(selected, minutes=mins, energy=en, understood_note=note)
    kb = get_goal_picker_keyboard(selected, current_mins=mins, current_energy=en)
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


@router.message(TrainingFSM.goal)
async def handle_goal_text_input(message: Message, state: FSMContext):
    """Parse user natural language goal description (e.g. 'упор на спину без нагрузки на поясницу')."""
    text = message.text or ""
    data = await state.get_data()
    mins = data.get("target_mins", 45)
    en = data.get("target_energy", "normal")
    anchor_id = data.get("anchor_message_id")

    # Resolve focus with natural language keyword extractor
    focus_obj = resolve_focus(text=text, minutes=mins, energy=en)

    # Format feedback string
    parts = []
    if focus_obj.accent:
        parts.append(t("note_accent", x=focus_obj.accent))
    elif focus_obj.primary_muscles:
        parts.append(", ".join(focus_obj.primary_muscles[:3]))
    if focus_obj.limitations:
        parts.append(t("note_exclude", x=", ".join(focus_obj.limitations)))
    note_str = "; ".join(parts) if parts else text

    # Update state
    await state.update_data(
        selected_muscles=focus_obj.primary_muscles,
        target_mins=focus_obj.target_minutes,
        target_energy=focus_obj.energy,
        understood_note=note_str,
        custom_focus_text=text,
    )

    with contextlib.suppress(Exception):
        await message.delete()

    card_html = render.render_goal_picker(
        focus_obj.primary_muscles,
        minutes=focus_obj.target_minutes,
        energy=focus_obj.energy,
        understood_note=note_str,
    )
    kb = get_goal_picker_keyboard(
        focus_obj.primary_muscles,
        current_mins=focus_obj.target_minutes,
        current_energy=focus_obj.energy,
    )

    if anchor_id:
        with contextlib.suppress(Exception):
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=card_html,
                parse_mode="HTML",
                reply_markup=kb,
            )


async def _show(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup, feedback: Optional[str] = None) -> None:
    """Render a screen: edit in place for callbacks, send a new message for text events."""
    if feedback:
        text = f"{feedback}\n\n{text}"
    if isinstance(event, CallbackQuery):
        if event.message:
            await edit_or_send(event.message, text, parse_mode="HTML", reply_markup=kb)
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)


def _gap_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("gap_add"), callback_data=make_cb("gap_add", 0, 0))],
        [InlineKeyboardButton(text=t("gap_ai"), callback_data=make_cb("gap_ai", 0, 0))],
        [InlineKeyboardButton(text=t("gap_skip"), callback_data=make_cb("gap_skip", 0, 0))],
    ])


@router.callback_query(F.data.startswith("t2:make_plan"))
async def handle_generate_plan(callback: CallbackQuery, state: FSMContext):
    """Generate plan from the user's library; ask what to do first if the goal has no machines."""
    await callback.answer(t("plan_building"))
    await state.update_data(gap_skip=False)
    await _generate_plan(callback, state)


async def rebuild_current_plan(event: Message | CallbackQuery, state: FSMContext, feedback: Optional[str] = None) -> None:
    """Rebuild the plan for the goal already stored in FSM (after machines were added)."""
    await _generate_plan(event, state, feedback=feedback)


async def _generate_plan(event: Message | CallbackQuery, state: FSMContext, feedback: Optional[str] = None) -> None:
    """Build, polish, store and show the plan. Library-only; coverage gaps are resolved with the user first."""
    uid = event.from_user.id
    data = await state.get_data()
    selected_muscles = data.get("selected_muscles") or ["back"]
    mins = data.get("target_mins", 45)
    en = data.get("target_energy", "normal")
    custom_text = data.get("custom_focus_text")
    skip_gaps = bool(data.get("gap_skip"))

    focus_obj = resolve_focus(
        selected_groups=selected_muscles,
        text=custom_text,
        minutes=mins,
        energy=en,
    )

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        if await library.tag_untagged(db, uid, lang=lang):
            await db.commit()
        ctx = await load_plan_context(db, uid)
    profile = ctx.profile
    lib_dicts = ctx.library
    history_index = ctx.history_index

    goal_groups = groups_for(selected_muscles)
    gaps = planner.find_coverage_gaps(lib_dicts, goal_groups)
    if gaps and not skip_gaps:
        await state.update_data(gap_groups=gaps)
        title = t("gap_title", groups=", ".join(group_label(g) for g in gaps))
        await _show(event, title, _gap_keyboard(), feedback)
        return

    # 1. Deterministic planner (library only)
    plan_items = planner.build_plan(
        profile=profile,
        library_exercises=lib_dicts,
        history_index=history_index,
        focus=focus_obj,
        minutes=mins,
        energy=en,
    )
    if not plan_items:
        await _show(event, t("gap_empty_plan"), _gap_keyboard(), feedback)
        return

    # 2. Polish with LLM (cues & rationale)
    default_rationale = t("plan_rationale", focus=", ".join(group_label(m) for m in selected_muscles[:2]), mins=mins)
    plan_items, final_rationale = await llm.polish_plan(plan_items, default_rationale, lang=current_lang.get())

    # 3. Store TrainingPlan in database
    now = datetime.now(UTC).replace(tzinfo=None)
    items_json = [i.model_dump() for i in plan_items]
    plan_record = TrainingPlan(
        user_id=uid,
        local_date=now.date(),
        status="proposed",
        focus=focus_obj.primary_muscles,
        constraints={"minutes": mins, "energy": en, "limitations": focus_obj.limitations},
        items=items_json,
        rationale=final_rationale,
        generator="rules+llm",
        revision=0,
    )
    async with AsyncSessionLocal() as db:
        db.add(plan_record)
        await db.commit()
        await db.refresh(plan_record)

    # 4. Render plan card
    focus_name = ", ".join(selected_muscles[:2]).capitalize()
    card_html = render.render_plan_card(plan_items, focus_name=focus_name, minutes=mins, rationale=final_rationale)
    kb = get_plan_card_keyboard(plan_record.id, plan_record.revision)

    await state.set_state(TrainingFSM.plan)
    await state.update_data(plan_id=plan_record.id)

    await _show(event, card_html, kb, feedback)



@router.callback_query(F.data.startswith("t2:gap_add:"))
async def handle_gap_add(callback: CallbackQuery, state: FSMContext):
    """User adds machines himself; after adding, the plan is rebuilt."""
    from src.handlers.training.settings import handle_add_equipment_start

    await handle_add_equipment_start(callback, state)
    await state.update_data(eq_ai_return="plan")


@router.callback_query(F.data.startswith("t2:gap_ai:"))
async def handle_gap_ai(callback: CallbackQuery, state: FSMContext):
    """Suggest machines for the uncovered goals (LLM with catalog fallback) and ask for confirmation."""
    from src.handlers.training.machines_confirm import dedupe_against_library, propose_machines

    await callback.answer(t("gap_ai_thinking"))
    uid = callback.from_user.id
    data = await state.get_data()
    groups = data.get("gap_groups") or groups_for(data.get("selected_muscles") or [])
    focus_obj = resolve_focus(selected_groups=groups, text=data.get("custom_focus_text"))

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        ctx = await load_plan_context(db, uid)

    existing = [e["display_name"] for e in ctx.library]
    goal = getattr(ctx.profile, "goal", None)
    names = await llm.pick_suggestions(groups, goal, focus_obj.limitations, existing, lang=lang)
    names = dedupe_against_library(names, ctx.library)
    if not names:
        await callback.message.answer(t("eq_ai_none"), reply_markup=_gap_keyboard())
        return
    await propose_machines(callback.message, state, names, return_to="plan")


@router.callback_query(F.data.startswith("t2:gap_skip:"))
async def handle_gap_skip(callback: CallbackQuery, state: FSMContext):
    """Build the plan from whatever the library has."""
    await callback.answer(t("plan_building"))
    await state.update_data(gap_skip=True)
    await _generate_plan(callback, state)


@router.callback_query(F.data.startswith("t2:resize_menu:"))
async def handle_resize_plan(callback: CallbackQuery, state: FSMContext):
    """Resize plan by -15 or +15 minutes."""
    await callback.answer()
    parts = callback.data.split(":")
    plan_id = int(parts[2])

    async with AsyncSessionLocal() as db:
        plan = await db.get(TrainingPlan, plan_id)
        if not plan:
            return

        items = [planner.PlanItem(**i) for i in (plan.items or [])]
        # Alternate: shorten by 15 mins
        resized = planner.resize_plan(items, delta_minutes=-15)
        plan.items = [i.model_dump() for i in resized]
        plan.revision += 1
        await db.flush()

    card_html = render.render_plan_card(resized, focus_name=t("plan_word"), minutes=30, rationale=plan.rationale)
    kb = get_plan_card_keyboard(plan.id, plan.revision)
    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)

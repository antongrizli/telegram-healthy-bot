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
from src.database.models import TrainingPlan, TrainingProfile
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_goal_picker_keyboard, get_plan_card_keyboard, make_cb
from src.handlers.training.states import TrainingFSM
from src.services.training import history, library, llm, planner, render
from src.services.training.focus import resolve_focus

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


@router.callback_query(F.data.startswith("t2:make_plan"))
async def handle_generate_plan(callback: CallbackQuery, state: FSMContext):
    """Generate plan, polish cues with LLM, store TrainingPlan in DB, and show plan card."""
    await callback.answer(t("plan_building"))
    uid = callback.from_user.id
    data = await state.get_data()
    selected_muscles = data.get("selected_muscles") or ["back"]
    mins = data.get("target_mins", 45)
    en = data.get("target_energy", "normal")
    custom_text = data.get("custom_focus_text")

    focus_obj = resolve_focus(
        selected_groups=selected_muscles,
        text=custom_text,
        minutes=mins,
        energy=en,
    )

    async with AsyncSessionLocal() as db:
        profile = await db.get(TrainingProfile, uid)
        user_library = await library.get_user_library(db, uid)
        lib_dicts = [
            {
                "id": ex.id,
                "display_name": ex.display_name,
                "kind": ex.kind,
                "muscle_primary": ex.muscle_primary,
                "muscles_secondary": ex.muscles_secondary,
                "pattern": ex.pattern,
                "equipment": ex.equipment,
                "weight_step": ex.weight_step,
                "min_weight": ex.min_weight,
                "catalog_key": ex.catalog_key,
                "is_favorite": ex.is_favorite,
            }
            for ex in user_library
        ]

        # Build history index for known exercises
        history_index = {}
        for ex in user_library:
            h_obj = await history.get_exercise_history(db, uid, ex.id)
            if h_obj:
                history_index[ex.display_name.lower()] = h_obj

    # 1. Deterministic planner
    plan_items = planner.build_plan(
        profile=profile,
        library_exercises=lib_dicts,
        history_index=history_index,
        focus=focus_obj,
        minutes=mins,
        energy=en,
    )

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

    if callback.message:
        await edit_or_send(callback.message, card_html, parse_mode="HTML", reply_markup=kb)


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

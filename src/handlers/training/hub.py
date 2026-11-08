"""Training Menu Hub handler: single anchor message, stats, and recovery recommendation."""
from __future__ import annotations

import contextlib
import html
import logging
from datetime import UTC, datetime
from typing import Optional
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.handlers.training.ui import edit_or_send
from src.utils.training_locales import group_label, t, weekdays
from src.database.connection import AsyncSessionLocal
from src.database.models import CanonicalExercise, TrainingPlan, TrainingProfile, User
from src.handlers.training.keyboards import (
    get_goal_picker_keyboard,
    get_hub_keyboard,
    get_plan_card_keyboard,
    get_repeat_checklist_keyboard,
    make_cb,
)
from src.handlers.training.states import TrainingFSM
from src.keyboards.reply import get_training_hub_reply_keyboard
from src.services.training import focus, history, library, planner, recovery, render, sessions
from src.utils.i18n_locales import get_all_translations

logger = logging.getLogger(__name__)
router = Router(name="training_hub_router")


async def render_and_send_hub(
    target_message: Message,
    user_id: int,
    state: FSMContext,
    edit: bool = False,
) -> None:
    """Render hub card and update anchor message with bottom reply keyboard."""
    async with AsyncSessionLocal() as db:
        active_session = await sessions.get_active_session(db, user_id)
        last_session = await history.get_last_finished_session(db, user_id)
        weekly_count = await history.get_user_weekly_workout_count(db, user_id)
        profile = await db.get(TrainingProfile, user_id)
        muscle_stats = await history.get_user_muscle_recovery_stats(db, user_id)
        db_user = await db.get(User, user_id)

    user_lang = db_user.language if db_user and db_user.language else "ru"
    weekly_target = profile.days_per_week if profile else 3
    last_str = t("hub_never")
    if last_session and last_session.performed_at:
        days = weekdays()
        day_name = days[last_session.performed_at.weekday()]
        date_str = last_session.performed_at.strftime("%d.%m")
        n_ex = len(last_session.entries)
        last_str = f"{day_name} {date_str} · {t('n_ex', n=n_ex)}"

    # Focus recommendation
    rec_group, rec_reason = recovery.suggest_focus(profile, muscle_stats)
    rec_label = group_label(rec_group)
    rec_str = f"{rec_label} ({rec_reason})" if not active_session else None

    is_active = active_session is not None
    active_ex = ""
    active_prog = ""

    if active_session and active_session.entries:
        entries = sorted(active_session.entries, key=lambda e: e.position)
        pos, current = next(
            (
                (i, e)
                for i, e in enumerate(entries, 1)
                if e.status != "skipped" and any(not s.completed for s in e.workout_sets)
            ),
            (len(entries), entries[-1]),
        )
        active_ex = current.canonical_exercise.display_name if current.canonical_exercise else current.raw_input
        active_prog = f"{pos}/{len(entries)}"

    text = render.render_hub(
        last_session_str=last_str,
        weekly_count=weekly_count,
        weekly_target=weekly_target,
        recommendation_str=rec_str,
        is_active=is_active,
        active_exercise=active_ex,
        active_progress=active_prog,
    )

    reply_kb = get_training_hub_reply_keyboard(lang=user_lang, is_active=is_active)

    await state.set_state(TrainingFSM.hub)

    if edit:
        try:
            await target_message.edit_text(text, parse_mode="HTML", reply_markup=None)
            await state.update_data(anchor_message_id=target_message.message_id)
            return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            logger.debug("Could not edit anchor message, falling back to new message: %s", e)

    sent = await target_message.answer(text, parse_mode="HTML", reply_markup=reply_kb)
    await state.update_data(anchor_message_id=sent.message_id)


@router.message(Command("training"))
@router.message(F.text.in_(get_all_translations("btn_workout")))
async def handle_workout_hub_entry(message: Message, state: FSMContext):
    """Handle bottom reply keyboard button or command."""
    uid = message.from_user.id
    async with AsyncSessionLocal() as db:
        profile = await db.get(TrainingProfile, uid)
    if profile is None:
        from src.handlers.training.settings import start_onboarding
        await start_onboarding(message, state)
        return

    await render_and_send_hub(message, uid, state, edit=False)


@router.callback_query(F.data.startswith("t2:hub"))
async def handle_hub_callback(callback: CallbackQuery, state: FSMContext):
    """Return to hub callback."""
    await callback.answer()
    if callback.message:
        await render_and_send_hub(callback.message, callback.from_user.id, state, edit=True)


@router.message(
    F.text.startswith("▶️ Начать")
    | F.text.startswith("▶️ Start")
    | F.text.startswith("▶️ Почати")
    | F.text.startswith("▶️ Starten")
    | F.text.startswith("▶️ Rozpocznij")
    | F.text.startswith("▶️ Başla")
    | F.text.startswith("▶️ Empezar")
    | F.text.in_(["Начать", "Start", "Почати", "Starten", "▶️ Начать", "▶️ Start"])
    | F.text.startswith("▶️ Продолжить")
    | F.text.startswith("▶️ Resume")
    | F.text.startswith("▶️ Продовжити")
    | F.text.startswith("▶️ Fortsetzen")
    | F.text.startswith("▶️ Wznów")
    | F.text.startswith("▶️ Devam et")
    | F.text.startswith("▶️ Reanudar")
    | F.text.in_(["Продолжить", "Resume", "Продовжити", "▶️ Продолжить", "▶️ Resume"])
)
@router.callback_query(F.data.startswith("t2:start_rec"))
async def handle_start_workout(event: Message | CallbackQuery, state: FSMContext):
    """Start workout from recommended plan or resume live workout."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        target_message = event.message
        user_id = event.from_user.id
    else:
        target_message = event
        user_id = event.from_user.id

    async with AsyncSessionLocal() as db:
        active_session = await sessions.get_active_session(db, user_id)
        profile = await db.get(TrainingProfile, user_id)

    if active_session:
        from src.handlers.training import live
        await live.render_current_live_card(target_message, active_session, state, edit=False)
        return

    if profile is None:
        from src.handlers.training.settings import start_onboarding
        await start_onboarding(target_message, state)
        return

    async with AsyncSessionLocal() as db:
        muscle_stats = await history.get_user_muscle_recovery_stats(db, user_id)
        rec_group, rec_reason = recovery.suggest_focus(profile, muscle_stats)
        user_library = await library.get_user_library(db, user_id)
        history_index = {}
        for ex in user_library:
            h = await history.get_exercise_history(db, user_id, ex.id)
            if h:
                history_index[ex.display_name.lower()] = h

    mins = profile.session_minutes or 45
    focus_obj = focus.resolve_focus(selected_groups=[rec_group], minutes=mins, energy="normal")
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
    plan_items = planner.build_plan(
        profile=profile,
        library_exercises=lib_dicts,
        history_index=history_index,
        focus=focus_obj,
        minutes=mins,
        energy="normal",
    )

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        plan_record = TrainingPlan(
            user_id=user_id,
            local_date=now.date(),
            status="proposed",
            focus=[rec_group],
            constraints={},
            items=[i.model_dump() for i in plan_items],
            rationale=rec_reason,
            generator="rules",
            revision=0,
        )
        db.add(plan_record)
        await db.commit()
        await db.refresh(plan_record)

    rec_label = group_label(rec_group)
    card_html = render.render_plan_card(plan_items, focus_name=rec_label, minutes=mins, rationale=rec_reason)
    kb = get_plan_card_keyboard(plan_record.id, plan_record.revision)

    await state.set_state(TrainingFSM.plan)
    await state.update_data(plan_id=plan_record.id)

    if isinstance(event, CallbackQuery) and event.message:
        await edit_or_send(event.message, card_html, parse_mode="HTML", reply_markup=kb)
    else:
        sent = await target_message.answer(card_html, parse_mode="HTML", reply_markup=kb)
        await state.update_data(anchor_message_id=sent.message_id)


@router.message(
    F.text.startswith("⚙️ Тренажёры")
    | F.text.startswith("⚙️ Тренажер")
    | F.text.startswith("⚙️ Equipment")
    | F.text.startswith("⚙️ Geräte")
    | F.text.startswith("⚙️ Maszyny")
    | F.text.startswith("⚙️ Makineler")
    | F.text.startswith("⚙️ Máquinas")
    | F.text.in_(["Тренажеры", "Тренажёры", "Equipment", "⚙️ Тренажёры", "⚙️ Треназеры", "⚙️ Тренажеры"])
)
@router.callback_query(F.data.startswith("t2:settings"))
async def handle_settings(event: Message | CallbackQuery, state: FSMContext):
    """Show training profile and gym equipment library."""
    from src.handlers.training import settings as training_settings
    await training_settings.render_equipment_settings(event, state)


@router.callback_query(F.data.startswith("t2:onb_restart"))
async def handle_onboarding_restart(callback: CallbackQuery, state: FSMContext):
    """Rerun 5-step onboarding wizard."""
    await callback.answer()
    from src.handlers.training import settings as training_settings
    if callback.message:
        await training_settings.start_onboarding(callback.message, state)


@router.message(
    F.text.startswith("🎯 Другая цель")
    | F.text.startswith("🎯 Other goal")
    | F.text.startswith("🎯 Інша ціль")
    | F.text.startswith("🎯 Anderes Ziel")
    | F.text.startswith("🎯 Inny cel")
    | F.text.startswith("🎯 Başka hedef")
    | F.text.startswith("🎯 Otro objetivo")
    | F.text.in_(["Другая цель", "Other goal", "🎯 Другая цель"])
)
async def handle_goal_menu_from_reply(message: Message, state: FSMContext):
    """Open goal picker screen from bottom reply keyboard."""
    await state.set_state(TrainingFSM.goal)
    await state.update_data(selected_muscles=["back"], target_mins=45, target_energy="normal", understood_note=None)
    card_html = render.render_goal_picker(selected_muscles=["back"], minutes=45, energy="normal")
    kb = get_goal_picker_keyboard(selected_muscles=["back"], current_mins=45, current_energy="normal")
    sent = await message.answer(card_html, parse_mode="HTML", reply_markup=kb)
    await state.update_data(anchor_message_id=sent.message_id)


@router.message(
    F.text.startswith("🔁 Повторить")
    | F.text.startswith("🔁 Как в прошлый раз")
    | F.text.startswith("🔁 Like last time")
    | F.text.startswith("🔁 Як минулого")
    | F.text.startswith("🔁 Wie letztes Mal")
    | F.text.startswith("🔁 Powtórz")
    | F.text.startswith("🔁 Geçen seferki")
    | F.text.startswith("🔁 Como la última")
    | F.text.in_(["Повторить прошлую", "Как в прошлый раз", "🔁 Повторить прошлую", "🔁 Как в прошлый раз"])
)
async def handle_repeat_from_reply(message: Message, state: FSMContext):
    """Open previous workout checklist from bottom reply keyboard."""
    uid = message.from_user.id
    async with AsyncSessionLocal() as db:
        last_session = await history.get_last_finished_session(db, uid)
    if not last_session or not last_session.entries:
        await message.answer(t("rep_none"))
        return

    items_state = []
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
    sent = await message.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.update_data(anchor_message_id=sent.message_id)


@router.message(
    F.text.startswith("✍️ Записать")
    | F.text.startswith("✍️ Log")
    | F.text.startswith("✍️ Записати")
    | F.text.startswith("✍️ Eintragen")
    | F.text.startswith("✍️ Zapisz")
    | F.text.startswith("✍️ Kaydet")
    | F.text.startswith("✍️ Registrar")
    | F.text.in_(["Записать", "Записать выполненное", "✍️ Записать", "✍️ Записать выполненное"])
)
async def handle_write_from_reply(message: Message, state: FSMContext):
    """Open draft input from bottom reply keyboard."""
    uid = message.from_user.id
    async with AsyncSessionLocal() as db:
        session = await sessions.get_or_create_draft(db, uid, source="text")
    prompt_text = t("log_prompt")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("kb_cancel_back"), callback_data=make_cb("hub", 0, 0))]
    ])
    await state.set_state(TrainingFSM.draft)
    await state.update_data(session_id=session.id)
    sent = await message.answer(prompt_text, parse_mode="HTML", reply_markup=kb)
    await state.update_data(anchor_message_id=sent.message_id)


@router.message(
    F.text.startswith("📈 Прогресс")
    | F.text.startswith("📈 Progress")
    | F.text.startswith("📈 Прогрес")
    | F.text.startswith("📈 Fortschritt")
    | F.text.startswith("📈 Postępy")
    | F.text.startswith("📈 Gelişim")
    | F.text.startswith("📈 Progreso")
    | F.text.in_(["Прогресс", "Progress", "📈 Прогресс"])
)
async def handle_progress_from_reply(message: Message, state: FSMContext):
    """Show training progress from bottom reply keyboard."""
    uid = message.from_user.id
    async with AsyncSessionLocal() as db:
        weekly_count = await history.get_user_weekly_workout_count(db, uid)
        last_session = await history.get_last_finished_session(db, uid)

    lines = [
        t("prog_title"),
        t("prog_week", n=weekly_count),
    ]

    if last_session and last_session.entries:
        date_str = last_session.performed_at.strftime("%d.%m.%Y") if last_session.performed_at else ""
        lines.append(t("prog_last", date=date_str))
        for entry in last_session.entries[:6]:
            ex_name = entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input
            w_sets = [s for s in entry.workout_sets if s.set_type == "working"] or entry.workout_sets
            n_sets = len(w_sets)
            w = w_sets[0].weight_kg if w_sets and w_sets[0].weight_kg else 0
            r = w_sets[0].reps if w_sets and w_sets[0].reps else 0
            dur = w_sets[0].duration_sec if w_sets and w_sets[0].duration_sec else 0
            if dur:
                lines.append(f"• {html.escape(ex_name)}: {int(dur/60)} {t('unit_min')}")
            elif w:
                w_str = f"{w:.1f}".rstrip("0").rstrip(".")
                lines.append(f"• {html.escape(ex_name)}: {n_sets}×{r} @ {w_str} {t('unit_kg')}")
            else:
                lines.append(f"• {html.escape(ex_name)}: {n_sets}×{r}")

    from src.config import settings
    from aiogram.types import WebAppInfo
    rows = []
    if settings.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text=t("kb_webapp"), web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=workouts"))])
    rows.append([InlineKeyboardButton(text=t("kb_to_menu_arrow"), callback_data=make_cb("hub", 0, 0))])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    sent = await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    await state.update_data(anchor_message_id=sent.message_id)


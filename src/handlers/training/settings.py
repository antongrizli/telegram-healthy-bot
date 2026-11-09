"""Training settings and 5-step onboarding wizard."""
from __future__ import annotations

import contextlib
import html
import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.utils.training_locales import t
from src.database.connection import AsyncSessionLocal
from src.database.models import CanonicalExercise, TrainingProfile, User
from src.handlers.training.ui import edit_or_send
from src.handlers.training.keyboards import get_rest_pref_keyboard, make_cb
from src.handlers.training.states import TrainingFSM
from src.keyboards.reply import get_equipment_reply_keyboard, get_equipment_sub_reply_keyboard
from src.services.training import library, llm
from src.services.training.catalog import get_catalog

logger = logging.getLogger(__name__)
router = Router(name="training_settings_router")

POPULAR_CATALOG_MACHINES = [
    ("chest_press_machine", "Жим в тренажёре", "Chest Press"),
    ("lat_pulldown", "Тяга верхнего блока", "Lat Pulldown"),
    ("leg_press", "Жим ногами", "Leg Press"),
    ("leg_extension", "Разгибания ног", "Leg Extension"),
    ("leg_curl", "Сгибания ног", "Leg Curl"),
    ("seated_cable_row", "Горизонтальная тяга", "Cable Row"),
    ("shoulder_press_machine", "Жим вверх", "Shoulder Press"),
    ("cable_crossover", "Кроссовер", "Cable Crossover"),
    ("bicep_curl_machine", "Бицепс в тренажёре", "Bicep Machine"),
    ("tricep_extension_machine", "Трицепс в тренажёре", "Tricep Machine"),
]

SHORT_MACHINE_NAME_MAX = 60

ADD_EQUIPMENT_TEXTS = {
    "➕ Добавить тренажёр", "➕ Add equipment", "➕ Додати тренажер",
    "➕ Gerät hinzufügen", "➕ Dodaj maszynę", "➕ Makine ekle", "➕ Añadir máquina",
    "Добавить тренажёр", "Добавить тренажер", "Add equipment",
}

DELETE_EQUIPMENT_TEXTS = {
    "🗑 Удалить тренажёр", "🗑 Remove equipment", "🗑 Видалити тренажер",
    "🗑 Gerät entfernen", "🗑 Usuń maszynę", "🗑 Makine sil", "🗑 Eliminar máquina",
    "Удалить тренажёр", "Удалить тренажер", "Remove equipment",
}

PROFILE_SETTINGS_TEXTS = {
    "✏️ Настроить профиль", "✏️ Edit profile", "✏️ Налаштувати профіль",
    "✏️ Profil bearbeiten", "✏️ Edytuj profil", "✏️ Profili düzenle", "✏️ Editar perfil",
    "Настроить профиль", "Edit profile",
}

BACK_TO_HUB_TEXTS = {
    "⬅️ К тренировкам", "⬅️ Back to workouts", "⬅️ До тренувань",
    "⬅️ Zurück zum Training", "⬅️ Do treningów", "⬅️ Antrenmanlara dön", "⬅️ Volver a entrenamientos",
    "К тренировкам", "Back to workouts",
}

BACK_TO_EQUIPMENT_TEXTS = {
    "⬅️ К тренажёрам", "⬅️ Back to equipment", "⬅️ До тренажерів",
    "⬅️ Zurück zu Geräten", "⬅️ Do maszyn", "⬅️ Makinelere dön", "⬅️ Volver a máquinas",
    "К тренажёрам", "К тренажерам", "Back to equipment",
}


async def start_onboarding(message: Message, state: FSMContext) -> None:
    """Start 5-question training profile wizard."""
    await state.set_state(TrainingFSM.onboarding)
    await state.update_data(onboarding_step=1, profile_draft={})

    text = t("onb_goal_title")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("goal_hypertrophy"), callback_data=make_cb("onb_goal", 0, 0, "hypertrophy"))],
        [InlineKeyboardButton(text=t("goal_strength"), callback_data=make_cb("onb_goal", 0, 0, "strength"))],
        [InlineKeyboardButton(text=t("goal_fat_loss"), callback_data=make_cb("onb_goal", 0, 0, "fat_loss"))],
        [InlineKeyboardButton(text=t("goal_endurance"), callback_data=make_cb("onb_goal", 0, 0, "endurance"))],
        [InlineKeyboardButton(text=t("goal_health"), callback_data=make_cb("onb_goal", 0, 0, "health"))],
        [InlineKeyboardButton(text=t("onb_skip"), callback_data=make_cb("onb_skip", 0, 0, "1"))],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:onb_goal:"))
@router.callback_query(F.data.startswith("t2:onb_skip:0:0:1"))
async def handle_onboarding_goal(callback: CallbackQuery, state: FSMContext):
    """Step 2: Experience."""
    await callback.answer()
    parts = callback.data.split(":")
    goal = parts[4] if len(parts) > 4 else "hypertrophy"

    data = await state.get_data()
    draft = data.get("profile_draft", {})
    draft["goal"] = goal
    await state.update_data(onboarding_step=2, profile_draft=draft)

    text = t("onb_exp_title")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("exp_beginner"), callback_data=make_cb("onb_exp", 0, 0, "beginner"))],
        [InlineKeyboardButton(text=t("exp_intermediate"), callback_data=make_cb("onb_exp", 0, 0, "intermediate"))],
        [InlineKeyboardButton(text=t("exp_advanced"), callback_data=make_cb("onb_exp", 0, 0, "advanced"))],
        [InlineKeyboardButton(text=t("onb_skip"), callback_data=make_cb("onb_skip", 0, 0, "2"))],
    ])
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:onb_exp:"))
@router.callback_query(F.data.startswith("t2:onb_skip:0:0:2"))
async def handle_onboarding_exp(callback: CallbackQuery, state: FSMContext):
    """Step 3: Days per week."""
    await callback.answer()
    parts = callback.data.split(":")
    exp = parts[4] if len(parts) > 4 else "intermediate"

    data = await state.get_data()
    draft = data.get("profile_draft", {})
    draft["experience"] = exp
    await state.update_data(onboarding_step=3, profile_draft=draft)

    text = t("onb_days_title")
    rows = [
        [InlineKeyboardButton(text=t("days_n", d=d), callback_data=make_cb("onb_days", 0, 0, str(d))) for d in (2, 3, 4)],
        [InlineKeyboardButton(text=t("days_n", d=d), callback_data=make_cb("onb_days", 0, 0, str(d))) for d in (1, 5, 6)],
        [InlineKeyboardButton(text=t("onb_skip"), callback_data=make_cb("onb_skip", 0, 0, "3"))],
    ]
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("t2:onb_days:"))
@router.callback_query(F.data.startswith("t2:onb_skip:0:0:3"))
async def handle_onboarding_days(callback: CallbackQuery, state: FSMContext):
    """Step 4: Session minutes."""
    await callback.answer()
    parts = callback.data.split(":")
    days = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 3

    data = await state.get_data()
    draft = data.get("profile_draft", {})
    draft["days_per_week"] = days
    await state.update_data(onboarding_step=4, profile_draft=draft)

    text = t("onb_time_title")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("min_n", n=30), callback_data=make_cb("onb_time", 0, 0, "30")),
            InlineKeyboardButton(text=t("min_n", n=45), callback_data=make_cb("onb_time", 0, 0, "45")),
        ],
        [
            InlineKeyboardButton(text=t("min_n", n=60), callback_data=make_cb("onb_time", 0, 0, "60")),
            InlineKeyboardButton(text=t("min_n", n=90), callback_data=make_cb("onb_time", 0, 0, "90")),
        ],
        [InlineKeyboardButton(text=t("onb_skip"), callback_data=make_cb("onb_skip", 0, 0, "4"))],
    ])
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:onb_time:"))
@router.callback_query(F.data.startswith("t2:onb_skip:0:0:4"))
async def handle_onboarding_time(callback: CallbackQuery, state: FSMContext):
    """Step 5: Limitations."""
    await callback.answer()
    parts = callback.data.split(":")
    time_val = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 45

    data = await state.get_data()
    draft = data.get("profile_draft", {})
    draft["session_minutes"] = time_val
    draft["limitations"] = draft.get("limitations", [])
    await state.update_data(onboarding_step=5, profile_draft=draft)

    text = (
        t("onb_lim_title")
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("lim_lower_back"), callback_data=make_cb("onb_lim", 0, 0, "lower_back"))],
        [InlineKeyboardButton(text=t("lim_knees"), callback_data=make_cb("onb_lim", 0, 0, "knees"))],
        [InlineKeyboardButton(text=t("lim_shoulders"), callback_data=make_cb("onb_lim", 0, 0, "shoulders"))],
        [InlineKeyboardButton(text=t("onb_lim_done"), callback_data=make_cb("onb_done", 0, 0))],
    ])
    if callback.message:
        await edit_or_send(callback.message, text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:onb_lim:"))
async def handle_onboarding_lim_toggle(callback: CallbackQuery, state: FSMContext):
    """Toggle a limitation option."""
    await callback.answer()
    parts = callback.data.split(":")
    lim = parts[4]

    data = await state.get_data()
    draft = data.get("profile_draft", {})
    lims = list(draft.get("limitations", []))
    if lim in lims:
        lims.remove(lim)
    else:
        lims.append(lim)
    draft["limitations"] = lims
    await state.update_data(profile_draft=draft)

    # Re-render with checkmarks
    def _lbl(name: str, key: str) -> str:
        return f"☑️ {name}" if key in lims else name

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_lbl(t("lim_lower_back"), "lower_back"), callback_data=make_cb("onb_lim", 0, 0, "lower_back"))],
        [InlineKeyboardButton(text=_lbl(t("lim_knees"), "knees"), callback_data=make_cb("onb_lim", 0, 0, "knees"))],
        [InlineKeyboardButton(text=_lbl(t("lim_shoulders"), "shoulders"), callback_data=make_cb("onb_lim", 0, 0, "shoulders"))],
        [InlineKeyboardButton(text=t("onb_save"), callback_data=make_cb("onb_done", 0, 0))],
    ])
    if callback.message:
        with contextlib.suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=kb)


@router.callback_query(F.data.startswith("t2:onb_done"))
async def handle_onboarding_finish(callback: CallbackQuery, state: FSMContext):
    """Save completed profile and redirect to Hub."""
    uid = callback.from_user.id
    data = await state.get_data()
    draft = data.get("profile_draft", {})

    goal = draft.get("goal", "hypertrophy")
    exp = draft.get("experience", "intermediate")
    days = draft.get("days_per_week", 3)
    mins = draft.get("session_minutes", 45)
    lims = draft.get("limitations", [])

    async with AsyncSessionLocal() as db:
        prof = await db.get(TrainingProfile, uid)
        if not prof:
            prof = TrainingProfile(
                user_id=uid,
                goal=goal,
                experience=exp,
                days_per_week=days,
                session_minutes=mins,
                split="auto",
                limitations=lims,
            )
            db.add(prof)
        else:
            prof.goal = goal
            prof.experience = exp
            prof.days_per_week = days
            prof.session_minutes = mins
            prof.limitations = lims
        await db.commit()

    await callback.answer(t("profile_saved"))
    from src.handlers.training.hub import render_and_send_hub
    if callback.message:
        await render_and_send_hub(callback.message, uid, state, edit=True)


async def render_equipment_settings(
    event: Message | CallbackQuery,
    state: FSMContext,
    user_id: int | None = None,
    feedback: str | None = None,
):
    """Render profile info and equipment library with docked reply keyboard."""
    if isinstance(event, CallbackQuery):
        target_message = event.message
        uid = event.from_user.id
    else:
        target_message = event
        uid = event.from_user.id if user_id is None else user_id

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        profile = await db.get(TrainingProfile, uid)
        exercises = await library.get_user_library(db, uid)

    if profile is None:
        if target_message:
            await start_onboarding(target_message, state)
        return

    await state.set_state(TrainingFSM.hub)

    goals_map = {
        "hypertrophy": t("goal_hypertrophy"),
        "strength": t("goal_strength"),
        "fat_loss": t("goal_fat_loss"),
        "endurance": t("goal_endurance"),
        "health": t("goal_health"),
    }
    exp_map = {
        "beginner": t("exp_beginner"),
        "intermediate": t("exp_intermediate"),
        "advanced": t("exp_advanced"),
    }

    goal_str = goals_map.get(profile.goal, profile.goal)
    exp_str = exp_map.get(profile.experience, profile.experience)
    lims_str = ", ".join(profile.limitations) if profile.limitations else t("prof_lims_none")

    ex_lines = []
    for ex in exercises:
        group = f" <i>({ex.muscle_primary})</i>" if ex.muscle_primary else ""
        ex_lines.append(f"• <b>{html.escape(ex.display_name)}</b>{group}")

    if not ex_lines:
        ex_text = t("prof_lib_empty")
    else:
        ex_text = "\n".join(ex_lines)

    prefix = f"{feedback}\n\n" if feedback else ""

    text = t(
        "prof_card", prefix=prefix, goal=goal_str, exp=exp_str, days=profile.days_per_week,
        mins=profile.session_minutes, lims=lims_str, n=len(exercises), ex=ex_text,
    )

    reply_kb = get_equipment_reply_keyboard(lang=lang)

    if isinstance(event, CallbackQuery) and event.message:
        with contextlib.suppress(Exception):
            await event.message.edit_reply_markup(reply_markup=None)
        await target_message.answer(text, parse_mode="HTML", reply_markup=reply_kb)
    else:
        await target_message.answer(text, parse_mode="HTML", reply_markup=reply_kb)
    # The card above carries the docked reply keyboard, so the inline toggle travels in its own small message.
    await target_message.answer(
        t("rest_pref_hint"), reply_markup=get_rest_pref_keyboard(bool(profile.rest_timer))
    )


@router.callback_query(F.data.startswith("t2:tgl_rest:"))
async def handle_toggle_rest_pref(callback: CallbackQuery, state: FSMContext):
    """Flip the automatic rest timer preference."""
    async with AsyncSessionLocal() as db:
        profile = await db.get(TrainingProfile, callback.from_user.id)
        if not profile:
            await callback.answer(t("stale"))
            return
        profile.rest_timer = not profile.rest_timer
        enabled = bool(profile.rest_timer)
        await db.commit()
    await callback.answer()
    if callback.message:
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=get_rest_pref_keyboard(enabled))


@router.message(F.text.in_(ADD_EQUIPMENT_TEXTS))
@router.callback_query(F.data.startswith("t2:eq_add:"))
async def handle_add_equipment_start(event: Message | CallbackQuery, state: FSMContext):
    """Start equipment add flow."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        target_message = event.message
        uid = event.from_user.id
    else:
        target_message = event
        uid = event.from_user.id

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"

    await state.set_state(TrainingFSM.equipment_add)
    sub_reply_kb = get_equipment_sub_reply_keyboard(lang=lang)

    inline_rows = []
    for i in range(0, len(POPULAR_CATALOG_MACHINES), 2):
        pair = POPULAR_CATALOG_MACHINES[i : i + 2]
        row = []
        for cat_key, ru_name, en_name in pair:
            lbl = ru_name if lang == "ru" else en_name
            row.append(
                InlineKeyboardButton(
                    text=f"➕ {lbl}",
                    callback_data=make_cb("eq_add_cat", 0, 0, cat_key),
                )
            )
        inline_rows.append(row)
    inline_rows.append([
        InlineKeyboardButton(
            text=t("eq_back"),
            callback_data=make_cb("settings", 0, 0),
        )
    ])
    inline_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)

    text = (
        t("eq_add_title")
    )

    await target_message.answer(text, parse_mode="HTML", reply_markup=sub_reply_kb)
    await target_message.answer(t("eq_quick_pick"), reply_markup=inline_kb)


@router.callback_query(F.data.startswith("t2:eq_add_cat:"))
async def handle_add_catalog_machine(callback: CallbackQuery, state: FSMContext):
    """Add machine chosen from popular catalog list."""
    await callback.answer()
    parts = callback.data.split(":")
    cat_key = parts[4] if len(parts) > 4 else None
    if not cat_key:
        return

    uid = callback.from_user.id
    catalog = get_catalog()
    cat_item = catalog.get(cat_key)
    if not cat_item:
        return

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        disp_name = (
            cat_item.get("names", {}).get(lang, [])
            or cat_item.get("names", {}).get("ru", [])
            or cat_item.get("names", {}).get("en", [cat_key])
        )[0]
        await library.get_or_create_canonical(
            db,
            user_id=uid,
            name=disp_name,
            catalog_key=cat_key,
        )
        await db.commit()

    feedback = t("eq_added", name=html.escape(disp_name))
    await render_equipment_settings(callback, state, user_id=uid, feedback=feedback)


@router.message(TrainingFSM.equipment_add, F.text)
async def handle_add_custom_machine(message: Message, state: FSMContext):
    """Handle text input when adding custom equipment."""
    text = (message.text or "").strip()
    uid = message.from_user.id

    if text in BACK_TO_EQUIPMENT_TEXTS:
        await render_equipment_settings(message, state, user_id=uid)
        return
    if text in BACK_TO_HUB_TEXTS:
        from src.handlers.training.hub import render_and_send_hub
        await render_and_send_hub(message, uid, state, edit=False)
        return
    if text in DELETE_EQUIPMENT_TEXTS:
        await handle_delete_equipment_start(message, state)
        return
    if text in PROFILE_SETTINGS_TEXTS:
        await start_onboarding(message, state)
        return
    if text.startswith("⬅️") or text.startswith("/"):
        await render_equipment_settings(message, state, user_id=uid)
        return

    if len(text) > SHORT_MACHINE_NAME_MAX or "\n" in text:
        await _propose_extracted_machines(message, state, uid, text)
        return

    async with AsyncSessionLocal() as db:
        ex = await library.get_or_create_canonical(db, user_id=uid, name=text)
        await db.commit()

    feedback = t("eq_added_lib", name=html.escape(ex.display_name))
    await render_equipment_settings(message, state, user_id=uid, feedback=feedback)


async def _propose_extracted_machines(message: Message, state: FSMContext, uid: int, text: str) -> None:
    """Long/multiline input: ask the AI backend for machine names and request confirmation."""
    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"

    names = await llm.extract_machines_from_text(text, lang=lang)
    if not names:
        await message.answer(t("eq_ai_none"))
        return

    await state.update_data(eq_ai_candidates=names)
    items = "\n".join(f"• {html.escape(n)}" for n in names)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("eq_ai_add_all"), callback_data=make_cb("eq_ai_ok", 0, 0)),
        InlineKeyboardButton(text=t("eq_ai_cancel"), callback_data=make_cb("eq_ai_no", 0, 0)),
    ]])
    await message.answer(t("eq_ai_found", items=items), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:eq_ai_ok:"))
async def handle_ai_machines_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    data = await state.get_data()
    names = data.get("eq_ai_candidates") or []
    await state.update_data(eq_ai_candidates=None)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    if not names:
        await callback.message.answer(t("eq_ai_expired"))
        return

    async with AsyncSessionLocal() as db:
        for name in names:
            await library.get_or_create_canonical(db, user_id=uid, name=name)
        await db.commit()

    feedback = t("eq_ai_added", count=len(names))
    await render_equipment_settings(callback, state, user_id=uid, feedback=feedback)


@router.callback_query(F.data.startswith("t2:eq_ai_no:"))
async def handle_ai_machines_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(eq_ai_candidates=None)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.delete()


@router.message(F.text.in_(DELETE_EQUIPMENT_TEXTS))
@router.callback_query(F.data.startswith("t2:eq_del:"))
async def handle_delete_equipment_start(event: Message | CallbackQuery, state: FSMContext):
    """Start equipment delete flow."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        target_message = event.message
        uid = event.from_user.id
    else:
        target_message = event
        uid = event.from_user.id

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        exercises = await library.get_user_library(db, uid)

    if not exercises:
        msg = t("eq_del_empty")
        reply_kb = get_equipment_reply_keyboard(lang=lang)
        await target_message.answer(msg, parse_mode="HTML", reply_markup=reply_kb)
        return

    await state.set_state(TrainingFSM.equipment_delete)
    sub_reply_kb = get_equipment_sub_reply_keyboard(lang=lang)

    del_rows = []
    for ex in exercises:
        del_rows.append([
            InlineKeyboardButton(
                text=f"🗑 {ex.display_name}",
                callback_data=make_cb("eq_del_id", 0, 0, str(ex.id)),
            )
        ])
    del_rows.append([
        InlineKeyboardButton(
            text=t("eq_done"),
            callback_data=make_cb("settings", 0, 0),
        )
    ])
    del_kb = InlineKeyboardMarkup(inline_keyboard=del_rows)

    text = (
        t("eq_del_title")
    )
    await target_message.answer(text, parse_mode="HTML", reply_markup=sub_reply_kb)
    await target_message.answer(t("eq_del_list"), reply_markup=del_kb)


@router.callback_query(F.data.startswith("t2:eq_del_id:"))
async def handle_delete_equipment_item(callback: CallbackQuery, state: FSMContext):
    """Handle deleting a specific equipment item via inline button."""
    parts = callback.data.split(":")
    ex_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
    if not ex_id:
        await callback.answer(t("eq_id_err"))
        return

    uid = callback.from_user.id
    async with AsyncSessionLocal() as db:
        removed = await library.remove_user_exercise(db, user_id=uid, exercise_id=ex_id)
        await db.commit()
        remaining = await library.get_user_library(db, uid)

    name = removed.display_name if removed else t("eq_fallback")
    await callback.answer(t("eq_removed_toast", name=name))

    if not remaining:
        await edit_or_send(callback.message, t("eq_all_removed"))
        return

    del_rows = []
    for ex in remaining:
        del_rows.append([
            InlineKeyboardButton(
                text=f"🗑 {ex.display_name}",
                callback_data=make_cb("eq_del_id", 0, 0, str(ex.id)),
            )
        ])
    del_rows.append([
        InlineKeyboardButton(
            text=t("eq_done"),
            callback_data=make_cb("settings", 0, 0),
        )
    ])
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=del_rows)
        )


@router.message(TrainingFSM.equipment_delete, F.text)
async def handle_delete_custom_machine(message: Message, state: FSMContext):
    """Handle text input when deleting custom equipment."""
    text = (message.text or "").strip()
    uid = message.from_user.id

    if text in BACK_TO_EQUIPMENT_TEXTS:
        await render_equipment_settings(message, state, user_id=uid)
        return
    if text in BACK_TO_HUB_TEXTS:
        from src.handlers.training.hub import render_and_send_hub
        await render_and_send_hub(message, uid, state, edit=False)
        return
    if text in ADD_EQUIPMENT_TEXTS:
        await handle_add_equipment_start(message, state)
        return
    if text in PROFILE_SETTINGS_TEXTS:
        await start_onboarding(message, state)
        return
    if text.startswith("⬅️") or text.startswith("/"):
        await render_equipment_settings(message, state, user_id=uid)
        return

    async with AsyncSessionLocal() as db:
        removed = await library.remove_user_exercise_by_name(db, user_id=uid, name=text)
        await db.commit()

    if removed:
        feedback = t("eq_removed_msg", name=html.escape(removed.display_name))
        await render_equipment_settings(message, state, user_id=uid, feedback=feedback)
    else:
        await message.answer(
            t("eq_not_found", name=html.escape(text)),
            parse_mode="HTML",
        )


@router.message(F.text.in_(BACK_TO_EQUIPMENT_TEXTS))
async def handle_back_to_equipment(message: Message, state: FSMContext):
    """Return to equipment screen from docked button."""
    await render_equipment_settings(message, state, user_id=message.from_user.id)


@router.message(F.text.in_(BACK_TO_HUB_TEXTS))
async def handle_back_to_hub(message: Message, state: FSMContext):
    """Return to workout hub from docked button."""
    from src.handlers.training.hub import render_and_send_hub
    await render_and_send_hub(message, message.from_user.id, state, edit=False)


@router.message(F.text.in_(PROFILE_SETTINGS_TEXTS))
async def handle_profile_settings_reply(message: Message, state: FSMContext):
    """Start profile onboarding wizard from docked button."""
    await start_onboarding(message, state)

"""Training settings and 5-step onboarding wizard."""
from __future__ import annotations

import contextlib
import html
import logging
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.database.connection import AsyncSessionLocal
from src.database.models import CanonicalExercise, TrainingProfile, User
from src.handlers.training.keyboards import make_cb
from src.handlers.training.states import TrainingFSM
from src.keyboards.reply import get_equipment_reply_keyboard, get_equipment_sub_reply_keyboard
from src.services.training import library
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

    text = "👋 <b>Настроим тренировочный профиль (1/5)</b>\n\nКакая у вас главная цель?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💪 Мышцы (гипертрофия)", callback_data=make_cb("onb_goal", 0, 0, "hypertrophy"))],
        [InlineKeyboardButton(text="🏋️ Сила", callback_data=make_cb("onb_goal", 0, 0, "strength"))],
        [InlineKeyboardButton(text="🔥 Похудение / рельеф", callback_data=make_cb("onb_goal", 0, 0, "fat_loss"))],
        [InlineKeyboardButton(text="🫀 Выносливость", callback_data=make_cb("onb_goal", 0, 0, "endurance"))],
        [InlineKeyboardButton(text="🌿 Здоровье и тонус", callback_data=make_cb("onb_goal", 0, 0, "health"))],
        [InlineKeyboardButton(text="Пропустить", callback_data=make_cb("onb_skip", 0, 0, "1"))],
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

    text = "🎯 <b>Опыт тренировок (2/5)</b>\n\nКак долго вы занимаетесь силовыми?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Менее 6 месяцев (новичок)", callback_data=make_cb("onb_exp", 0, 0, "beginner"))],
        [InlineKeyboardButton(text="6 месяцев – 2 года", callback_data=make_cb("onb_exp", 0, 0, "intermediate"))],
        [InlineKeyboardButton(text="Более 2 лет (опытный)", callback_data=make_cb("onb_exp", 0, 0, "advanced"))],
        [InlineKeyboardButton(text="Пропустить", callback_data=make_cb("onb_skip", 0, 0, "2"))],
    ])
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


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

    text = "📅 <b>Регулярность (3/5)</b>\n\nСколько дней в неделю планируете тренироваться?"
    rows = [
        [InlineKeyboardButton(text=f"{d} дн.", callback_data=make_cb("onb_days", 0, 0, str(d))) for d in (2, 3, 4)],
        [InlineKeyboardButton(text=f"{d} дн.", callback_data=make_cb("onb_days", 0, 0, str(d))) for d in (1, 5, 6)],
        [InlineKeyboardButton(text="Пропустить", callback_data=make_cb("onb_skip", 0, 0, "3"))],
    ]
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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

    text = "⏱ <b>Время на тренировку (4/5)</b>\n\nСколько минут обычно выделяете?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="30 мин", callback_data=make_cb("onb_time", 0, 0, "30")),
            InlineKeyboardButton(text="45 мин", callback_data=make_cb("onb_time", 0, 0, "45")),
        ],
        [
            InlineKeyboardButton(text="60 мин", callback_data=make_cb("onb_time", 0, 0, "60")),
            InlineKeyboardButton(text="90 мин", callback_data=make_cb("onb_time", 0, 0, "90")),
        ],
        [InlineKeyboardButton(text="Пропустить", callback_data=make_cb("onb_skip", 0, 0, "4"))],
    ])
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


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
        "🛡 <b>Ограничения по здоровью (5/5)</b>\n\n"
        "Отметьте зоны, на которые нежелательна осевая или ударная нагрузка:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Поясница / спина", callback_data=make_cb("onb_lim", 0, 0, "lower_back"))],
        [InlineKeyboardButton(text="Колени", callback_data=make_cb("onb_lim", 0, 0, "knees"))],
        [InlineKeyboardButton(text="Плечи", callback_data=make_cb("onb_lim", 0, 0, "shoulders"))],
        [InlineKeyboardButton(text="✅ Готово / ограничений нет", callback_data=make_cb("onb_done", 0, 0))],
    ])
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


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
        [InlineKeyboardButton(text=_lbl("Поясница / спина", "lower_back"), callback_data=make_cb("onb_lim", 0, 0, "lower_back"))],
        [InlineKeyboardButton(text=_lbl("Колени", "knees"), callback_data=make_cb("onb_lim", 0, 0, "knees"))],
        [InlineKeyboardButton(text=_lbl("Плечи", "shoulders"), callback_data=make_cb("onb_lim", 0, 0, "shoulders"))],
        [InlineKeyboardButton(text="✅ Сохранить профиль", callback_data=make_cb("onb_done", 0, 0))],
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

    await callback.answer("Профиль сохранён ✓")
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
        "hypertrophy": "💪 Мышцы (гипертрофия)",
        "strength": "🏋️ Сила",
        "fat_loss": "🔥 Похудение / рельеф",
        "endurance": "🫀 Выносливость",
        "health": "🌿 Здоровье и тонус",
    }
    exp_map = {
        "beginner": "Менее 6 месяцев",
        "intermediate": "6 месяцев – 2 года",
        "advanced": "Более 2 лет",
    }

    goal_str = goals_map.get(profile.goal, profile.goal)
    exp_str = exp_map.get(profile.experience, profile.experience)
    lims_str = ", ".join(profile.limitations) if profile.limitations else "нет"

    ex_lines = []
    for ex in exercises:
        group = f" <i>({ex.muscle_primary})</i>" if ex.muscle_primary else ""
        ex_lines.append(f"• <b>{html.escape(ex.display_name)}</b>{group}")

    if not ex_lines:
        ex_text = "<i>Библиотека пока пуста. Нажмите «➕ Добавить тренажёр» внизу, чтобы добавить тренажёры вашего зала.</i>"
    else:
        ex_text = "\n".join(ex_lines)

    prefix = f"{feedback}\n\n" if feedback else ""

    text = (
        f"{prefix}⚙️ <b>Профиль и тренажёры</b>\n\n"
        f"🎯 <b>Цель:</b> {goal_str}\n"
        f"🏋️ <b>Опыт:</b> {exp_str}\n"
        f"🗓 <b>Дней в неделю:</b> {profile.days_per_week}\n"
        f"⏱ <b>Время на тренировку:</b> {profile.session_minutes} мин\n"
        f"🛡 <b>Ограничения:</b> {lims_str}\n\n"
        f"🏋️ <b>Тренажёры в библиотеке ({len(exercises)}):</b>\n"
        f"{ex_text}\n\n"
        "<i>Используйте кнопки под экраном для добавления, удаления тренажёров или настройки профиля.</i>"
    )

    reply_kb = get_equipment_reply_keyboard(lang=lang)

    if isinstance(event, CallbackQuery) and event.message:
        with contextlib.suppress(Exception):
            await event.message.edit_reply_markup(reply_markup=None)
        await target_message.answer(text, parse_mode="HTML", reply_markup=reply_kb)
    else:
        await target_message.answer(text, parse_mode="HTML", reply_markup=reply_kb)


@router.message(F.text.in_(ADD_EQUIPMENT_TEXTS))
@router.callback_query(F.data.startswith("t2:eq_add"))
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
            text="⬅️ К тренажёрам",
            callback_data=make_cb("settings", 0, 0),
        )
    ])
    inline_kb = InlineKeyboardMarkup(inline_keyboard=inline_rows)

    text = (
        "➕ <b>Добавление тренажёра</b>\n\n"
        "Отправьте название тренажёра сообщением в чат (например, <i>Жим в Смите</i>, <i>Latzug</i>, <i>Грудной жим</i>), "
        "либо выберите готовый из каталога:"
    )

    await target_message.answer(text, parse_mode="HTML", reply_markup=sub_reply_kb)
    await target_message.answer("Быстрый выбор из каталога:", reply_markup=inline_kb)


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

    feedback = f"✅ Тренажёр «<b>{html.escape(disp_name)}</b>» добавлен!"
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

    async with AsyncSessionLocal() as db:
        ex = await library.get_or_create_canonical(db, user_id=uid, name=text)
        await db.commit()

    feedback = f"✅ Тренажёр «<b>{html.escape(ex.display_name)}</b>» добавлен в библиотеку!"
    await render_equipment_settings(message, state, user_id=uid, feedback=feedback)


@router.message(F.text.in_(DELETE_EQUIPMENT_TEXTS))
@router.callback_query(F.data.startswith("t2:eq_del"))
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
        msg = "<i>В вашей библиотеке пока нет активных тренажёров для удаления.</i>"
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
            text="⬅️ Готово",
            callback_data=make_cb("settings", 0, 0),
        )
    ])
    del_kb = InlineKeyboardMarkup(inline_keyboard=del_rows)

    text = (
        "🗑 <b>Удаление тренажёра</b>\n\n"
        "Нажмите на тренажёр, который хотите убрать из библиотеки, либо отправьте его название текстом:"
    )
    await target_message.answer(text, parse_mode="HTML", reply_markup=sub_reply_kb)
    await target_message.answer("Список активных тренажёров:", reply_markup=del_kb)


@router.callback_query(F.data.startswith("t2:eq_del_id:"))
async def handle_delete_equipment_item(callback: CallbackQuery, state: FSMContext):
    """Handle deleting a specific equipment item via inline button."""
    parts = callback.data.split(":")
    ex_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
    if not ex_id:
        await callback.answer("Ошибка ID тренажёра")
        return

    uid = callback.from_user.id
    async with AsyncSessionLocal() as db:
        removed = await library.remove_user_exercise(db, user_id=uid, exercise_id=ex_id)
        await db.commit()
        remaining = await library.get_user_library(db, uid)

    name = removed.display_name if removed else "тренажёр"
    await callback.answer(f"🗑 Удалено: {name}")

    if not remaining:
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "<i>Все тренажёры удалены из библиотеки.</i>\n\n"
                "Вы можете добавить новые кнопкой «➕ Добавить тренажёр» под экраном.",
                parse_mode="HTML",
                reply_markup=None,
            )
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
            text="⬅️ Готово",
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
        feedback = f"🗑 Тренажёр «<b>{html.escape(removed.display_name)}</b>» удалён из библиотеки."
        await render_equipment_settings(message, state, user_id=uid, feedback=feedback)
    else:
        await message.answer(
            f"Тренажёр «{html.escape(text)}» не найден в списке активных. Проверьте название или нажмите кнопку выше.",
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

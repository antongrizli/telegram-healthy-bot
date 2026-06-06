from datetime import datetime, time
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales, formulas
from src.keyboards import reply, inline
from src.services.scheduler import reschedule_user_jobs
from src.config import settings

router = Router()

class ProfileStatesGroup(StatesGroup):
    name = State()
    sex = State()
    age = State()
    height = State()
    weight = State()
    activity = State()
    goal = State()
    language = State()
    notifications = State()
    report_time = State()

@router.callback_query(F.data == "profile:start")
async def start_profile_setup(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    await state.set_state(ProfileStatesGroup.name)
    await callback.message.answer(
        i18n_locales.get_text("profile_prompt_name", user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.name)
async def process_name(message: Message, state: FSMContext, user_language: str):
    await state.update_data(name=message.text.strip())
    await state.set_state(ProfileStatesGroup.sex)
    await message.answer(
        i18n_locales.get_text("profile_prompt_sex", user_language),
        reply_markup=inline.get_sex_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(ProfileStatesGroup.sex, F.data.startswith("sex:"))
async def process_sex(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    selected_sex = callback.data.split(":")[1]
    await state.update_data(sex=selected_sex)
    await state.set_state(ProfileStatesGroup.age)
    await callback.message.edit_text(
        i18n_locales.get_text("profile_prompt_age", user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.age)
async def process_age(message: Message, state: FSMContext, user_language: str):
    try:
        age = int(message.text.strip())
        if age <= 0 or age > 120:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_age", user_language))
        return
        
    await state.update_data(age=age)
    await state.set_state(ProfileStatesGroup.height)
    await message.answer(
        i18n_locales.get_text("profile_prompt_height", user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.height)
async def process_height(message: Message, state: FSMContext, user_language: str):
    try:
        height = float(message.text.strip().replace(",", "."))
        if height <= 50 or height > 270:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_height", user_language))
        return
        
    await state.update_data(height=height)
    await state.set_state(ProfileStatesGroup.weight)
    await message.answer(
        i18n_locales.get_text("profile_prompt_weight", user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.weight)
async def process_weight(message: Message, state: FSMContext, user_language: str):
    try:
        weight = float(message.text.strip().replace(",", "."))
        if weight <= 20 or weight > 500:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_weight", user_language))
        return
        
    await state.update_data(weight=weight)
    await state.set_state(ProfileStatesGroup.activity)
    await message.answer(
        i18n_locales.get_text("profile_prompt_activity", user_language),
        reply_markup=inline.get_activity_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(ProfileStatesGroup.activity, F.data.startswith("activity:"))
async def process_activity(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    activity = callback.data.split(":")[1]
    await state.update_data(activity=activity)
    await state.set_state(ProfileStatesGroup.goal)
    await callback.message.edit_text(
        i18n_locales.get_text("profile_prompt_goal", user_language),
        reply_markup=inline.get_goal_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(ProfileStatesGroup.goal, F.data.startswith("goal:"))
async def process_goal(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    goal = callback.data.split(":")[1]
    await state.update_data(goal=goal)
    await state.set_state(ProfileStatesGroup.language)
    await callback.message.edit_text(
        i18n_locales.get_text("profile_prompt_language", user_language),
        reply_markup=inline.get_lang_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(ProfileStatesGroup.language, F.data.startswith("lang:"))
async def process_language(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    selected_lang = callback.data.split(":")[1]
    await state.update_data(language=selected_lang)
    
    # We update user_language variable for the prompt to reflect selected language immediately!
    await state.set_state(ProfileStatesGroup.notifications)
    await callback.message.edit_text(
        i18n_locales.get_text("profile_prompt_reminders", selected_lang),
        reply_markup=inline.get_notifications_keyboard(selected_lang),
        parse_mode="Markdown"
    )

@router.callback_query(ProfileStatesGroup.notifications, F.data.startswith("notify:"))
async def process_notifications(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    val = callback.data.split(":")[1]
    notify_enabled = (val == "yes")
    await state.update_data(notifications_enabled=notify_enabled)
    
    # Extract language from state so we use correct localization
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    
    await state.set_state(ProfileStatesGroup.report_time)
    await callback.message.edit_text(
        i18n_locales.get_text("profile_prompt_report_time", lang),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.report_time)
async def process_report_time(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    
    time_str = message.text.strip()
    try:
        parsed_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_time", lang))
        return
        
    # All inputs collected, proceed with calculation & database insertion
    # Calculate Mifflin-St Jeor daily allowances
    name = state_data["name"]
    sex = state_data["sex"]
    age = state_data["age"]
    height = state_data["height"]
    weight = state_data["weight"]
    activity = state_data["activity"]
    goal = state_data["goal"]
    notifications_enabled = state_data["notifications_enabled"]
    
    targets = formulas.calculate_targets(
        weight_kg=weight,
        height_cm=height,
        age=age,
        sex=sex,
        activity_level=activity,
        goal=goal
    )
    
    async with AsyncSessionLocal() as db:
        # 1. Update/Create User
        db_user = await crud.create_or_update_user(
            db,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            name=name,
            sex=sex,
            age=age,
            height_cm=height,
            weight_kg=weight,
            activity_level=activity,
            goal=goal,
            language=lang,
            notifications_enabled=notifications_enabled,
            daily_report_time=parsed_time,
            target_calories=targets["calories"],
            target_protein=targets["protein"],
            target_fat=targets["fat"],
            target_carb=targets["carb"]
        )
        
        # 2. Add Baseline Weight Log
        await crud.add_weight_log(db, user_id=message.from_user.id, weight=weight)
        
    # Configure scheduler cron timings
    reschedule_user_jobs(message.bot, db_user)
    
    # Complete state
    await state.clear()
    
    complete_msg = i18n_locales.get_text(
        "profile_complete",
        lang,
        calories=targets["calories"],
        protein=targets["protein"],
        fat=targets["fat"],
        carb=targets["carb"]
    )
    
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin
    await message.answer(
        complete_msg,
        reply_markup=reply.get_main_menu(lang, is_admin=is_admin),
        parse_mode="Markdown"
    )

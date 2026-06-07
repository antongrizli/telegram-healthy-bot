from datetime import datetime, time
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales, formulas
from src.keyboards import reply
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
    timezone = State()

@router.message(StateFilter(ProfileStatesGroup), F.text.in_(["❌ Cancel", "❌ Отмена"]))
async def cancel_profile_setup(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    if db_user and not db_user.is_blocked:
        is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin
        await message.answer(
            "Profile setup cancelled." if user_language == "en" else "Настройка профиля отменена.",
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            "Profile setup cancelled." if user_language == "en" else "Настройка профиля отменена.",
            reply_markup=reply.get_setup_profile_keyboard(user_language),
            parse_mode="Markdown"
        )

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_setup_profile"],
    i18n_locales.LOCALES["ru"]["btn_setup_profile"]
]))
async def start_profile_setup(message: Message, state: FSMContext, user_language: str):
    await state.set_state(ProfileStatesGroup.name)
    await message.answer(
        i18n_locales.get_text("profile_prompt_name", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.name)
async def process_name(message: Message, state: FSMContext, user_language: str):
    await state.update_data(name=message.text.strip())
    await state.set_state(ProfileStatesGroup.sex)
    await message.answer(
        i18n_locales.get_text("profile_prompt_sex", user_language),
        reply_markup=reply.get_sex_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.sex)
async def process_sex(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    if text in [i18n_locales.LOCALES["en"]["sex_male"], i18n_locales.LOCALES["ru"]["sex_male"]]:
        selected_sex = "male"
    elif text in [i18n_locales.LOCALES["en"]["sex_female"], i18n_locales.LOCALES["ru"]["sex_female"]]:
        selected_sex = "female"
    else:
        await message.answer(
            i18n_locales.get_text("profile_prompt_sex", user_language),
            reply_markup=reply.get_sex_keyboard(user_language),
            parse_mode="Markdown"
        )
        return

    await state.update_data(sex=selected_sex)
    await state.set_state(ProfileStatesGroup.age)
    await message.answer(
        i18n_locales.get_text("profile_prompt_age", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
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
        reply_markup=reply.get_cancel_keyboard(user_language),
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
        reply_markup=reply.get_cancel_keyboard(user_language),
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
        reply_markup=reply.get_activity_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.activity)
async def process_activity(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    if text in [i18n_locales.LOCALES["en"]["act_sedentary"], i18n_locales.LOCALES["ru"]["act_sedentary"]]:
        activity = "sedentary"
    elif text in [i18n_locales.LOCALES["en"]["act_light"], i18n_locales.LOCALES["ru"]["act_light"]]:
        activity = "light"
    elif text in [i18n_locales.LOCALES["en"]["act_moderate"], i18n_locales.LOCALES["ru"]["act_moderate"]]:
        activity = "moderate"
    elif text in [i18n_locales.LOCALES["en"]["act_active"], i18n_locales.LOCALES["ru"]["act_active"]]:
        activity = "active"
    else:
        await message.answer(
            i18n_locales.get_text("profile_prompt_activity", user_language),
            reply_markup=reply.get_activity_keyboard(user_language),
            parse_mode="Markdown"
        )
        return

    await state.update_data(activity=activity)
    await state.set_state(ProfileStatesGroup.goal)
    await message.answer(
        i18n_locales.get_text("profile_prompt_goal", user_language),
        reply_markup=reply.get_goal_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.goal)
async def process_goal(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    if text in [i18n_locales.LOCALES["en"]["goal_lose"], i18n_locales.LOCALES["ru"]["goal_lose"]]:
        goal = "lose_weight"
    elif text in [i18n_locales.LOCALES["en"]["goal_maintain"], i18n_locales.LOCALES["ru"]["goal_maintain"]]:
        goal = "maintain"
    elif text in [i18n_locales.LOCALES["en"]["goal_gain_w"], i18n_locales.LOCALES["ru"]["goal_gain_w"]]:
        goal = "gain_weight"
    elif text in [i18n_locales.LOCALES["en"]["goal_gain_m"], i18n_locales.LOCALES["ru"]["goal_gain_m"]]:
        goal = "gain_muscle"
    else:
        await message.answer(
            i18n_locales.get_text("profile_prompt_goal", user_language),
            reply_markup=reply.get_goal_keyboard(user_language),
            parse_mode="Markdown"
        )
        return

    await state.update_data(goal=goal)
    await state.set_state(ProfileStatesGroup.language)
    await message.answer(
        i18n_locales.get_text("profile_prompt_language", user_language),
        reply_markup=reply.get_lang_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.language)
async def process_language(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    if "English" in text or "en" in text.lower():
        selected_lang = "en"
    elif "Русский" in text or "ru" in text.lower():
        selected_lang = "ru"
    else:
        await message.answer(
            i18n_locales.get_text("profile_prompt_language", user_language),
            reply_markup=reply.get_lang_keyboard(user_language),
            parse_mode="Markdown"
        )
        return

    await state.update_data(language=selected_lang)
    await state.set_state(ProfileStatesGroup.notifications)
    await message.answer(
        i18n_locales.get_text("profile_prompt_reminders", selected_lang),
        reply_markup=reply.get_notifications_keyboard(selected_lang),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.notifications)
async def process_notifications(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    
    if text in [i18n_locales.LOCALES["en"]["yes"], i18n_locales.LOCALES["ru"]["yes"]]:
        notify_enabled = True
    elif text in [i18n_locales.LOCALES["en"]["no"], i18n_locales.LOCALES["ru"]["no"]]:
        notify_enabled = False
    else:
        await message.answer(
            i18n_locales.get_text("profile_prompt_reminders", lang),
            reply_markup=reply.get_notifications_keyboard(lang),
            parse_mode="Markdown"
        )
        return

    await state.update_data(notifications_enabled=notify_enabled)
    await state.set_state(ProfileStatesGroup.report_time)
    await message.answer(
        i18n_locales.get_text("profile_prompt_report_time", lang),
        reply_markup=reply.get_cancel_keyboard(lang),
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
        
    await state.update_data(daily_report_time=parsed_time)
    await state.set_state(ProfileStatesGroup.timezone)
    await message.answer(
        i18n_locales.get_text("profile_prompt_timezone", lang),
        reply_markup=reply.get_timezone_list_button_keyboard(lang),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.timezone)
async def process_timezone_message(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    text = message.text.strip()
    
    # 1. Check if user clicked "🌐 Select timezone" / "🌐 Выбрать часовой пояс"
    if text in [i18n_locales.LOCALES["en"]["btn_timezone_list"], i18n_locales.LOCALES["ru"]["btn_timezone_list"]]:
        msg_text = (
            "Select your geographical region:" if lang == "en"
            else "Выберите ваш географический регион:"
        )
        await message.answer(
            msg_text,
            reply_markup=reply.get_timezone_regions_keyboard(lang),
            parse_mode="Markdown"
        )
        return
        
    # 2. Check if user clicked "🔙 Back" / "🔙 Назад" (from region select)
    if text in ["🔙 Back", "🔙 Назад"]:
        await message.answer(
            i18n_locales.get_text("profile_prompt_timezone", lang),
            reply_markup=reply.get_timezone_list_button_keyboard(lang),
            parse_mode="Markdown"
        )
        return
        
    # 3. Check if user clicked "🔙 Back to Regions" / "🔙 К регионам" (from regional timezone list)
    if text in ["🔙 Back to Regions", "🔙 К регионам"]:
        msg_text = (
            "Select your geographical region:" if lang == "en"
            else "Выберите ваш географический регион:"
        )
        await message.answer(
            msg_text,
            reply_markup=reply.get_timezone_regions_keyboard(lang),
            parse_mode="Markdown"
        )
        return
        
    # 4. Check if user clicked a region name
    regions = ["Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "UTC"]
    if text in regions:
        await state.update_data(timezone_region=text, timezone_page=0)
        await show_regional_timezones(message, text, 0, lang)
        return
        
    # 5. Check if user clicked navigation buttons "⬅️" or "➡️"
    if text == "⬅️":
        data = await state.get_data()
        region = data.get("timezone_region")
        page = data.get("timezone_page", 0)
        if region and page > 0:
            page -= 1
            await state.update_data(timezone_page=page)
            await show_regional_timezones(message, region, page, lang)
        return
        
    if text == "➡️":
        data = await state.get_data()
        region = data.get("timezone_region")
        page = data.get("timezone_page", 0)
        if region:
            page += 1
            await state.update_data(timezone_page=page)
            await show_regional_timezones(message, region, page, lang)
        return
        
    # 6. Check if user clicked page indicator like "1/20"
    if "/" in text and text.replace("/", "").isdigit():
        return
        
    # 7. Check if user selected/typed a valid timezone name
    try:
        ZoneInfo(text)
    except Exception:
        await message.answer(
            i18n_locales.get_text("invalid_timezone", lang),
            reply_markup=reply.get_timezone_list_button_keyboard(lang),
            parse_mode="Markdown"
        )
        return
        
    await complete_profile_setup(
        message,
        state,
        user_language,
        text,
        message.from_user.id,
        message.from_user.username
    )

async def show_regional_timezones(message: Message, region: str, page: int, lang: str):
    msg_text = (
        f"Select timezone in *{region}* (by default UTC is used):" if lang == "en"
        else f"Выберите часовой пояс в регионе *{region}* (по умолчанию UTC):"
    )
    await message.answer(
        msg_text,
        reply_markup=reply.get_regional_timezone_keyboard(region, page, lang),
        parse_mode="Markdown"
    )


async def complete_profile_setup(
    message: Message,
    state: FSMContext,
    user_language: str,
    selected_tz: str,
    user_id: int,
    username: str = None
):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    
    name = state_data["name"]
    sex = state_data["sex"]
    age = state_data["age"]
    height = state_data["height"]
    weight = state_data["weight"]
    activity = state_data["activity"]
    goal = state_data["goal"]
    notifications_enabled = state_data["notifications_enabled"]
    daily_report_time = state_data["daily_report_time"]
    
    targets = formulas.calculate_targets(
        weight_kg=weight,
        height_cm=height,
        age=age,
        sex=sex,
        activity_level=activity,
        goal=goal
    )
    
    async with AsyncSessionLocal() as db:
        db_user = await crud.create_or_update_user(
            db,
            telegram_id=user_id,
            username=username,
            name=name,
            sex=sex,
            age=age,
            height_cm=height,
            weight_kg=weight,
            activity_level=activity,
            goal=goal,
            language=lang,
            timezone=selected_tz,
            notifications_enabled=notifications_enabled,
            daily_report_time=daily_report_time,
            target_calories=targets["calories"],
            target_protein=targets["protein"],
            target_fat=targets["fat"],
            target_carb=targets["carb"]
        )
        
        await crud.add_weight_log(db, user_id=user_id, weight=weight)
        
    reschedule_user_jobs(message.bot, db_user)
    
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

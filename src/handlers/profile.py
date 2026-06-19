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
    language = State()
    name = State()
    sex = State()
    age = State()
    height = State()
    weight = State()
    activity = State()
    goal = State()
    notifications = State()
    report_time = State()
    timezone = State()
    confirm_delete = State()

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_delete_profile")))
async def start_profile_deletion(message: Message, state: FSMContext, user_language: str):
    await state.set_state(ProfileStatesGroup.confirm_delete)
    await message.answer(
        i18n_locales.get_text("delete_profile_prompt", user_language),
        reply_markup=reply.get_delete_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.confirm_delete, F.text.in_(i18n_locales.get_all_translations("btn_confirm_delete")))
async def process_confirm_delete(message: Message, state: FSMContext, user_language: str):
    user_id = message.from_user.id
    
    # 1. Remove jobs from scheduler
    from src.services.scheduler import remove_user_jobs
    remove_user_jobs(user_id)
    
    # 2. Delete user and their logs from database
    async with AsyncSessionLocal() as db:
        await crud.delete_user(db, user_id)
        
    # 3. Clear state
    await state.clear()
    
    # 4. Answer to user
    await message.answer(
        i18n_locales.get_text("profile_deleted", user_language),
        reply_markup=reply.get_setup_profile_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.confirm_delete, F.text.in_(i18n_locales.get_all_translations("btn_cancel")))
async def cancel_profile_deletion(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        "Profile deletion cancelled." if user_language == "en" else "Удаление профиля отменено.",
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin) if db_user else reply.get_setup_profile_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.confirm_delete)
async def process_invalid_delete_confirm(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("delete_profile_prompt", user_language),
        reply_markup=reply.get_delete_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(StateFilter(ProfileStatesGroup), F.text.in_(i18n_locales.get_all_translations("btn_cancel")))
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

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_setup_profile")))
async def start_profile_setup(message: Message, state: FSMContext, user_language: str, db_user = None):
    current_profile = None
    if db_user:
        current_profile = {
            "name": db_user.name,
            "sex": db_user.sex,
            "age": db_user.age,
            "height": db_user.height_cm,
            "weight": db_user.weight_kg,
            "activity": db_user.activity_level,
            "goal": db_user.goal,
            "language": db_user.language,
            "notifications_enabled": db_user.notifications_enabled,
            "daily_report_time": db_user.daily_report_time.strftime("%H:%M") if db_user.daily_report_time else "21:00",
            "timezone": db_user.timezone or "UTC"
        }
    await state.update_data(current_profile=current_profile)
    
    await state.set_state(ProfileStatesGroup.language)
    current_val = i18n_locales.get_text(f"lang_{current_profile['language']}", user_language) if current_profile else None
    
    if not db_user:
        welcome_text = i18n_locales.get_text("welcome", user_language)
        lang_prompt = i18n_locales.get_text("profile_prompt_language", user_language)
        prompt_text = f"{welcome_text}\n\n{lang_prompt}"
    else:
        prompt_text = i18n_locales.get_text("profile_prompt_language", user_language)
        
    await message.answer(
        prompt_text,
        reply_markup=reply.get_lang_keyboard(user_language, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.name)
async def process_name(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=current_profile["name"]):
        name = current_profile["name"]
    else:
        name = text
        
    await state.update_data(name=name)
    await state.set_state(ProfileStatesGroup.sex)
    
    current_val = None
    if current_profile:
        current_val = i18n_locales.get_text(f"sex_{current_profile['sex']}", lang)
        
    await message.answer(
        i18n_locales.get_text("profile_prompt_sex", lang),
        reply_markup=reply.get_sex_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.sex)
async def process_sex(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=i18n_locales.get_text(f"sex_{current_profile['sex']}", lang)):
        selected_sex = current_profile["sex"]
    elif text in i18n_locales.get_all_translations("sex_male"):
        selected_sex = "male"
    elif text in i18n_locales.get_all_translations("sex_female"):
        selected_sex = "female"
    else:
        current_val = i18n_locales.get_text(f"sex_{current_profile['sex']}", lang) if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_sex", lang),
            reply_markup=reply.get_sex_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )
        return

    await state.update_data(sex=selected_sex)
    await state.set_state(ProfileStatesGroup.age)
    
    current_val = str(current_profile["age"]) if current_profile else None
    await message.answer(
        i18n_locales.get_text("profile_prompt_age", lang),
        reply_markup=reply.get_cancel_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.age)
async def process_age(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=str(current_profile["age"])):
        age = current_profile["age"]
    else:
        try:
            age = int(text)
            if age <= 0 or age > 120:
                raise ValueError()
        except ValueError:
            await message.answer(i18n_locales.get_text("invalid_age", lang))
            return
        
    await state.update_data(age=age)
    await state.set_state(ProfileStatesGroup.height)
    
    current_val = str(current_profile["height"]) if current_profile else None
    await message.answer(
        i18n_locales.get_text("profile_prompt_height", lang),
        reply_markup=reply.get_cancel_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.height)
async def process_height(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=str(current_profile["height"])):
        height = current_profile["height"]
    else:
        try:
            height = float(text.replace(",", "."))
            if height <= 50 or height > 270:
                raise ValueError()
        except ValueError:
            await message.answer(i18n_locales.get_text("invalid_height", lang))
            return
        
    await state.update_data(height=height)
    await state.set_state(ProfileStatesGroup.weight)
    
    current_val = str(current_profile["weight"]) if current_profile else None
    await message.answer(
        i18n_locales.get_text("profile_prompt_weight", lang),
        reply_markup=reply.get_cancel_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.weight)
async def process_weight(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=str(current_profile["weight"])):
        weight = current_profile["weight"]
    else:
        try:
            weight = float(text.replace(",", "."))
            if weight <= 20 or weight > 500:
                raise ValueError()
        except ValueError:
            await message.answer(i18n_locales.get_text("invalid_weight", lang))
            return
        
    await state.update_data(weight=weight)
    await state.set_state(ProfileStatesGroup.activity)
    
    current_val = None
    if current_profile:
        current_val = i18n_locales.get_text(f"act_{current_profile['activity']}", lang)
        
    await message.answer(
        i18n_locales.get_text("profile_prompt_activity", lang),
        reply_markup=reply.get_activity_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.activity)
async def process_activity(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=i18n_locales.get_text(f"act_{current_profile['activity']}", lang)):
        activity = current_profile["activity"]
    elif text in i18n_locales.get_all_translations("act_sedentary"):
        activity = "sedentary"
    elif text in i18n_locales.get_all_translations("act_light"):
        activity = "light"
    elif text in i18n_locales.get_all_translations("act_moderate"):
        activity = "moderate"
    elif text in i18n_locales.get_all_translations("act_active"):
        activity = "active"
    else:
        current_val = i18n_locales.get_text(f"act_{current_profile['activity']}", lang) if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_activity", lang),
            reply_markup=reply.get_activity_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )
        return

    await state.update_data(activity=activity)
    await state.set_state(ProfileStatesGroup.goal)
    
    current_val = None
    if current_profile:
        goal_key = (
            "goal_lose" if current_profile["goal"] == "lose_weight" else
            "goal_maintain" if current_profile["goal"] == "maintain" else
            "goal_gain_w" if current_profile["goal"] == "gain_weight" else "goal_gain_m"
        )
        current_val = i18n_locales.get_text(goal_key, lang)
        
    await message.answer(
        i18n_locales.get_text("profile_prompt_goal", lang),
        reply_markup=reply.get_goal_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.goal)
async def process_goal(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    
    goal_key = None
    if current_profile:
        goal_key = (
            "goal_lose" if current_profile["goal"] == "lose_weight" else
            "goal_maintain" if current_profile["goal"] == "maintain" else
            "goal_gain_w" if current_profile["goal"] == "gain_weight" else "goal_gain_m"
        )
        
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=i18n_locales.get_text(goal_key, lang)):
        goal = current_profile["goal"]
    elif text in i18n_locales.get_all_translations("goal_lose"):
        goal = "lose_weight"
    elif text in i18n_locales.get_all_translations("goal_maintain"):
        goal = "maintain"
    elif text in i18n_locales.get_all_translations("goal_gain_w"):
        goal = "gain_weight"
    elif text in i18n_locales.get_all_translations("goal_gain_m"):
        goal = "gain_muscle"
    else:
        current_val = i18n_locales.get_text(goal_key, lang) if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_goal", lang),
            reply_markup=reply.get_goal_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )
        return

    await state.update_data(goal=goal)
    await state.set_state(ProfileStatesGroup.notifications)
    
    current_val = None
    if current_profile:
        current_val = i18n_locales.get_text("yes" if current_profile["notifications_enabled"] else "no", lang)
        
    await message.answer(
        i18n_locales.get_text("profile_prompt_reminders", lang),
        reply_markup=reply.get_notifications_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.language)
async def process_language(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    current_profile = state_data.get("current_profile")
    
    text = message.text.strip()
    
    if current_profile and text == i18n_locales.get_text("btn_keep_current", user_language, value=i18n_locales.get_text(f"lang_{current_profile['language']}", user_language)):
        selected_lang = current_profile["language"]
    elif "English" in text or "en" in text.lower():
        selected_lang = "en"
    elif "Русский" in text or "ru" in text.lower():
        selected_lang = "ru"
    elif "Українська" in text or "uk" in text.lower():
        selected_lang = "uk"
    elif "Polski" in text or "pl" in text.lower():
        selected_lang = "pl"
    elif "Deutsch" in text or "de" in text.lower():
        selected_lang = "de"
    elif "Türkçe" in text or "tr" in text.lower():
        selected_lang = "tr"
    elif "Español" in text or "es" in text.lower():
        selected_lang = "es"
    else:
        current_val = i18n_locales.get_text(f"lang_{current_profile['language']}", user_language) if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_language", user_language),
            reply_markup=reply.get_lang_keyboard(user_language, current_val=current_val),
            parse_mode="Markdown"
        )
        return

    await state.update_data(language=selected_lang)
    await state.set_state(ProfileStatesGroup.name)
    
    await message.answer(
        i18n_locales.get_text("profile_setup_start", selected_lang),
        parse_mode="Markdown"
    )
    
    current_val = current_profile["name"] if current_profile else None
    await message.answer(
        i18n_locales.get_text("profile_prompt_name", selected_lang),
        reply_markup=reply.get_cancel_keyboard(selected_lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.notifications)
async def process_notifications(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=i18n_locales.get_text("yes" if current_profile["notifications_enabled"] else "no", lang)):
        notify_enabled = current_profile["notifications_enabled"]
    elif text in i18n_locales.get_all_translations("yes"):
        notify_enabled = True
    elif text in i18n_locales.get_all_translations("no"):
        notify_enabled = False
    else:
        current_val = i18n_locales.get_text("yes" if current_profile["notifications_enabled"] else "no", lang) if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_reminders", lang),
            reply_markup=reply.get_notifications_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )
        return

    await state.update_data(notifications_enabled=notify_enabled)
    if notify_enabled:
        await state.set_state(ProfileStatesGroup.report_time)
        current_val = current_profile["daily_report_time"] if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_report_time", lang),
            reply_markup=reply.get_cancel_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )
    else:
        # Skip report_time, proceed directly to timezone
        await state.set_state(ProfileStatesGroup.timezone)
        current_val = current_profile["timezone"] if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_timezone", lang),
            reply_markup=reply.get_timezone_list_button_keyboard(lang, current_val=current_val),
            parse_mode="Markdown"
        )

@router.message(ProfileStatesGroup.report_time)
async def process_report_time(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    
    time_str = message.text.strip()
    if current_profile and time_str == i18n_locales.get_text("btn_keep_current", lang, value=current_profile["daily_report_time"]):
        parsed_time = datetime.strptime(current_profile["daily_report_time"], "%H:%M").time()
    else:
        try:
            parsed_time = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            current_val = current_profile["daily_report_time"] if current_profile else None
            await message.answer(
                i18n_locales.get_text("invalid_time", lang),
                reply_markup=reply.get_cancel_keyboard(lang, current_val=current_val)
            )
            return
        
    await state.update_data(daily_report_time=parsed_time)
    await state.set_state(ProfileStatesGroup.timezone)
    
    current_val = current_profile["timezone"] if current_profile else None
    await message.answer(
        i18n_locales.get_text("profile_prompt_timezone", lang),
        reply_markup=reply.get_timezone_list_button_keyboard(lang, current_val=current_val),
        parse_mode="Markdown"
    )

@router.message(ProfileStatesGroup.timezone)
async def process_timezone_message(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    lang = state_data.get("language", user_language)
    current_profile = state_data.get("current_profile")
    text = message.text.strip()
    
    # Check if keeping existing timezone
    if current_profile and text == i18n_locales.get_text("btn_keep_current", lang, value=current_profile["timezone"]):
        selected_tz = current_profile["timezone"]
        await complete_profile_setup(
            message,
            state,
            user_language,
            selected_tz,
            message.from_user.id,
            message.from_user.username
        )
        return
        
    # 1. Check if user clicked "🌐 Select timezone" / "🌐 Выбрать часовой пояс"
    if text in i18n_locales.get_all_translations("btn_timezone_list"):
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
        current_val = current_profile["timezone"] if current_profile else None
        await message.answer(
            i18n_locales.get_text("profile_prompt_timezone", lang),
            reply_markup=reply.get_timezone_list_button_keyboard(lang, current_val=current_val),
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
        current_val = current_profile["timezone"] if current_profile else None
        await message.answer(
            i18n_locales.get_text("invalid_timezone", lang),
            reply_markup=reply.get_timezone_list_button_keyboard(lang, current_val=current_val),
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
    current_profile = state_data.get("current_profile")
    default_time = time(21, 0)
    if current_profile and current_profile.get("daily_report_time"):
        try:
            default_time = datetime.strptime(current_profile["daily_report_time"], "%H:%M").time()
        except Exception:
            pass
    daily_report_time = state_data.get("daily_report_time", default_time)
    
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

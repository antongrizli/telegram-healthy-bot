from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from src.utils import i18n_locales
from src.utils.escape import escape_markdown
from src.config import settings
from src.keyboards import reply, inline
from src.services.scheduler import send_daily_report, send_weekly_report

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, user_language: str, db_user):
    if db_user and not db_user.is_blocked:
        is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin
        await message.answer(
            i18n_locales.get_text("welcome", user_language),
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
            parse_mode="Markdown"
        )
    else:
        # Unregistered or blocked user starts fresh setup
        kb = [[InlineKeyboardButton(
            text=i18n_locales.get_text("btn_setup_profile", user_language),
            callback_data="profile:start"
        )]]
        markup = InlineKeyboardMarkup(inline_keyboard=kb)
        await message.answer(
            i18n_locales.get_text("welcome", user_language),
            reply_markup=markup,
            parse_mode="Markdown"
        )

@router.message(Command("help"))
@router.message(F.text.in_([
    LOCALES_EN := i18n_locales.LOCALES["en"]["btn_help"],
    LOCALES_RU := i18n_locales.LOCALES["ru"]["btn_help"]
]))
async def cmd_help(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("help_text", user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_my_profile"],
    i18n_locales.LOCALES["ru"]["btn_my_profile"]
]))
async def view_profile(message: Message, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, user_language, db_user)
        return
        
    notifications_str = i18n_locales.get_text("enabled" if db_user.notifications_enabled else "disabled", user_language)
    sex_str = i18n_locales.get_text(f"sex_{db_user.sex}", user_language)
    activity_str = i18n_locales.get_text(f"act_{db_user.activity_level}", user_language)
    
    goal_key = (
        "goal_lose" if db_user.goal == "lose_weight" else
        "goal_maintain" if db_user.goal == "maintain" else
        "goal_gain_w" if db_user.goal == "gain_weight" else "goal_gain_m"
    )
    goal_str = i18n_locales.get_text(goal_key, user_language)
    daily_time = db_user.daily_report_time.strftime("%H:%M")
    
    profile_text = i18n_locales.get_text(
        "profile_view",
        user_language,
        name=escape_markdown(db_user.name),
        sex=sex_str,
        age=db_user.age,
        height=db_user.height_cm,
        weight=db_user.weight_kg,
        activity=activity_str,
        goal=goal_str,
        language=i18n_locales.get_text(f"lang_{db_user.language}", user_language),
        target_calories=db_user.target_calories,
        target_protein=db_user.target_protein,
        target_fat=db_user.target_fat,
        target_carb=db_user.target_carb,
        notifications=notifications_str,
        report_time=daily_time
    )
    
    kb = [[InlineKeyboardButton(
        text=i18n_locales.get_text("btn_setup_profile", user_language),
        callback_data="profile:start"
    )]]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    await message.answer(profile_text, reply_markup=markup, parse_mode="Markdown")

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_daily_report"],
    i18n_locales.LOCALES["ru"]["btn_daily_report"]
]))
async def trigger_daily_report(message: Message, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, user_language, db_user)
        return
    await send_daily_report(message.bot, message.from_user.id)

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_weekly_report"],
    i18n_locales.LOCALES["ru"]["btn_weekly_report"]
]))
async def trigger_weekly_report(message: Message, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, user_language, db_user)
        return
    await send_weekly_report(message.bot, message.from_user.id)

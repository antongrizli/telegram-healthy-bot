from aiogram import Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from src.utils import i18n_locales
from src.utils.escape import escape_markdown
from src.config import settings
from src.keyboards import reply
from src.services.scheduler import send_daily_report, send_weekly_report
from src.database.connection import AsyncSessionLocal
from src.database import crud

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user_language: str, db_user):
    if db_user and not db_user.is_blocked:
        is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin
        await message.answer(
            i18n_locales.get_text("welcome", user_language),
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
            parse_mode="Markdown"
        )
    else:
        # Unregistered or blocked user starts fresh setup directly
        from src.handlers.profile import start_profile_setup
        await start_profile_setup(message, state, user_language, db_user=None)

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

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_my_profile")))
async def view_profile(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
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
        timezone=db_user.timezone or "UTC",
        target_calories=db_user.target_calories,
        target_protein=db_user.target_protein,
        target_fat=db_user.target_fat,
        target_carb=db_user.target_carb,
        notifications=notifications_str,
        report_time=daily_time
    )
    
    markup = reply.get_setup_profile_keyboard(user_language)
    await message.answer(profile_text, reply_markup=markup, parse_mode="Markdown")

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_daily_report")))
async def trigger_daily_report(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
        return
    await send_daily_report(message.bot, message.from_user.id)

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_weekly_report")))
async def trigger_weekly_report(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
        return
    await send_weekly_report(message.bot, message.from_user.id)

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_my_progress")))
@router.message(Command("streaks"))
@router.message(Command("achievements"))
async def view_progress(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
        return
        
    async with AsyncSessionLocal() as db:
        from src.services import gamification
        streaks = await crud.get_user_streaks(db, db_user.telegram_id)
        achievements = await crud.get_user_achievements(db, db_user.telegram_id)
        
    streak_text = ""
    if not streaks:
        streak_text = "No active streaks yet! Keep tracking to build a streak." if user_language == "en" else "Нет активных стриков! Начните записывать еду/вес, чтобы запустить стрик."
    else:
        for s in streaks:
            type_name = (
                "Food logging 🍽️" if s.streak_type == "food_logging" else
                "Weight logging ⚖️" if s.streak_type == "weight_logging" else
                "Calorie goal hit 🎯" if s.streak_type == "calorie_target_hit" else "Protein goal hit 🥩"
            )
            streak_text += f"• *{type_name}*: {s.current_count} days (Longest: {s.longest_count} days)\n"
            
    ach_text = ""
    if not achievements:
        ach_text = "No achievements unlocked yet." if user_language == "en" else "Достижений пока нет."
    else:
        total_ach = len(gamification.ACHIEVEMENTS)
        ach_text = f"Unlocked {len(achievements)}/{total_ach} achievements:\n" if user_language == "en" else f"Открыто {len(achievements)}/{total_ach} достижений:\n"
        for a in achievements[:5]:
            ach_def = gamification.ACHIEVEMENTS.get(a.achievement_key)
            if ach_def:
                name = i18n_locales.get_text(ach_def["name_key"], user_language)
                ach_text += f"- {ach_def['icon']} *{name}*\n"
        if len(achievements) > 5:
            ach_text += "...and more in the achievements tab!" if user_language == "en" else "...и другие во вкладке достижений!"
            
    msg = (
        f"📈 *{i18n_locales.get_text('btn_my_progress', user_language)}*:\n\n"
        f"🔥 *Streaks*:\n{streak_text}\n"
        f"❄️ *Streak Freezes left*: {db_user.streak_freezes_left}/1\n\n"
        f"🏆 *Achievements*:\n{ach_text}"
    )
    
    from src.keyboards import inline
    markup = inline.get_streak_inline(user_language)
    await message.answer(msg, reply_markup=markup, parse_mode="Markdown")

@router.message(StateFilter("*"), F.text.in_(["⬅️ Back to Main Menu", "⬅️ Главное меню"]))
async def cmd_back_to_main_menu(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        "Returning to main menu..." if user_language == "en" else "Возвращаюсь в главное меню...",
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin)
    )

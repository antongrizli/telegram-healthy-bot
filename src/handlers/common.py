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
recovery_router = Router()

@recovery_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, user_language: str, db_user):
    if db_user and not db_user.is_blocked:
        await state.clear()
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

@recovery_router.message(Command("help"))
@recovery_router.message(F.text.in_(i18n_locales.get_all_translations("btn_help")))
async def cmd_help(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("help_text", user_language),
        parse_mode="Markdown"
    )

@recovery_router.message(F.text.in_(i18n_locales.get_all_translations("btn_my_profile")))
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
    weekly_day_idx = db_user.weekly_report_day if db_user.weekly_report_day is not None else 6
    weekly_day_name = i18n_locales.get_text(f"weekday_{weekly_day_idx}", user_language)
    monthly_day = db_user.monthly_report_day if db_user.monthly_report_day is not None else 1
    
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
        report_time=daily_time,
        weekly_day=weekly_day_name,
        monthly_day=monthly_day
    )
    
    markup = reply.get_setup_profile_keyboard(user_language)
    await message.answer(profile_text, reply_markup=markup, parse_mode="Markdown")

@recovery_router.message(F.text.in_(i18n_locales.get_all_translations("btn_daily_report")))
async def trigger_daily_report(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
        return
    async with AsyncSessionLocal() as db:
        await crud.record_event(db, message.from_user.id, 'report_opened')
    await send_daily_report(message.bot, message.from_user.id)

@recovery_router.message(F.text.in_(i18n_locales.get_all_translations("btn_weekly_report")))
async def trigger_weekly_report(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await cmd_start(message, state, user_language, db_user)
        return
    async with AsyncSessionLocal() as db:
        await crud.record_event(db, message.from_user.id, 'report_opened')
    await send_weekly_report(message.bot, message.from_user.id)


def report_details_id(text: str | None) -> int | None:
    """Read the durable snapshot ID carried by a reply-keyboard details button."""
    if not text:
        return None
    for label in i18n_locales.get_all_translations('ux_details'):
        prefix = f'{label} · '
        if text.startswith(prefix):
            try:
                return int(text.removeprefix(prefix))
            except ValueError:
                return None
    return None


@recovery_router.message(F.text.func(lambda text: report_details_id(text) is not None))
async def view_report_details(message: Message, user_language: str, db_user):
    report_id = report_details_id(message.text)
    async with AsyncSessionLocal() as db:
        report = await crud.get_saved_report(db, db_user.telegram_id, report_id)
    if not report:
        await message.answer(i18n_locales.get_text('ux_invalid', user_language))
        return
    await message.answer(report.payload['text'], parse_mode=None)

@recovery_router.message(F.text.in_(i18n_locales.get_all_translations("btn_my_progress")))
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
        
    from src.services.ux import streak_text
    parts = [streak_text(db_user, streaks, user_language),
             i18n_locales.get_text('ux_badges', user_language, count=len(achievements), total=len(gamification.ACHIEVEMENTS))]
    if not achievements:
        parts.append(i18n_locales.get_text('ux_badge_empty', user_language))
    for achievement in achievements[:5]:
        definition = gamification.ACHIEVEMENTS.get(achievement.achievement_key)
        if definition:
            parts.append(definition['icon'] + ' ' + i18n_locales.get_text(definition['name_key'], user_language))
    if len(achievements) > 5:
        parts.append(i18n_locales.get_text('ux_badge_more', user_language))
    msg = '\n\n'.join(parts)

    from src.keyboards import inline
    markup = inline.get_streak_inline(user_language)
    await message.answer(msg, reply_markup=markup, parse_mode=None)

@recovery_router.message(StateFilter("*"), F.text.in_(i18n_locales.get_all_translations('ux_back')))
async def cmd_back_to_main_menu(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        i18n_locales.get_text('ux_quick_food', user_language),
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin)
    )


@recovery_router.message(Command("cancel"))
@recovery_router.message(F.text.in_(i18n_locales.get_all_translations('btn_cancel')))
async def recover_menu(message: Message, state: FSMContext, user_language: str, db_user):
    """Reset navigation without deleting durable meal drafts."""
    if not db_user or db_user.is_blocked:
        await cmd_start(message, state, user_language, db_user)
        return
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(i18n_locales.get_text("session_recovery", user_language),
                         reply_markup=reply.get_main_menu(user_language, is_admin=is_admin))


@router.message(StateFilter(None))
async def recover_stale_keyboard(message: Message, state: FSMContext, user_language: str, db_user, album=None):
    # Telegram keeps reply keyboards after MemoryStorage is lost on restart.
    # Never infer which pending meal a stale Accept button refers to.
    from src.services.ux import is_food_entry
    if db_user and not db_user.is_blocked and is_food_entry(message):
        from src.handlers.food import process_food_input, FoodLoggingState
        await state.clear()
        await state.set_state(FoodLoggingState.waiting_for_input)
        await process_food_input(message, state, user_language, album=album)
    else:
        await recover_menu(message, state, user_language, db_user)

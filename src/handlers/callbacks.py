import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.keyboards import reply

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "log_breakfast")
async def cb_log_breakfast(callback: CallbackQuery, state: FSMContext, user_language: str):
    """
    Shortcut callback to directly log breakfast.
    Fills in the meal_type state and jumps directly to waiting for food input.
    """
    await callback.answer()
    from src.handlers.food import FoodLoggingState
    await state.set_state(FoodLoggingState.waiting_for_input)
    await state.update_data(meal_type="breakfast")
    
    await callback.message.answer(
        i18n_locales.get_text("food_prompt", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("report_range:"))
async def cb_report_range(callback: CallbackQuery, user_language: str):
    """
    Triggers report generation for the chosen range from inline keyboard.
    """
    await callback.answer()
    range_type = callback.data.split(":")[1]
    
    from src.services.scheduler import send_daily_report, send_weekly_report, send_monthly_report
    if range_type == "daily":
        await send_daily_report(callback.bot, callback.from_user.id)
    elif range_type == "weekly":
        await send_weekly_report(callback.bot, callback.from_user.id)
    elif range_type == "monthly":
        await send_monthly_report(callback.bot, callback.from_user.id)

@router.callback_query(F.data == "view_streaks")
async def cb_view_streaks(callback: CallbackQuery, user_language: str):
    """
    Displays current user streak statistics.
    """
    await callback.answer()
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, callback.from_user.id)
        if not user:
            return
        
        from src.services import gamification
        streaks = await crud.get_user_streaks(db, user.telegram_id)
        
        msg = f"🔥 *{i18n_locales.get_text('btn_streak_status', user_language)}*:\n\n"
        if not streaks:
            msg += "No active streaks yet! Start tracking today to build a streak."
        else:
            for s in streaks:
                type_name = (
                    "Food logging 🍽️" if s.streak_type == "food_logging" else
                    "Weight logging ⚖️" if s.streak_type == "weight_logging" else
                    "Calorie goal hit 🎯" if s.streak_type == "calorie_target_hit" else "Protein goal hit 🥩"
                )
                msg += f"• *{type_name}*: {s.current_count} days (Longest: {s.longest_count} days)\n"
            
            msg += f"\n❄️ *Streak Freezes left*: {user.streak_freezes_left}/1"
            
        await callback.message.answer(msg, parse_mode="Markdown")

@router.callback_query(F.data.startswith("share_achievement:"))
async def cb_share_achievement(callback: CallbackQuery, user_language: str):
    """
    Generates copy-pasteable achievement share text.
    """
    ach_key = callback.data.split(":")[1]
    from src.services import gamification
    ach_def = gamification.ACHIEVEMENTS.get(ach_key)
    
    if ach_def:
        icon = ach_def["icon"]
        name = i18n_locales.get_text(ach_def["name_key"], user_language)
        desc = i18n_locales.get_text(ach_def["desc_key"], user_language)
        
        share_title = "I unlocked a new achievement in Healthy Bot! 🍏" if user_language == "en" else "Я открыл новое достижение в Healthy Bot! 🍏"
        share_text = f"🏆 *{share_title}*\n\n{icon} *{name}* — {desc}\n\nJoin me on @your_healthy_body_bot! 💚"
        
        await callback.answer("Share text generated!" if user_language == "en" else "Текст для отправки создан!")
        await callback.message.answer(
            f"`{share_text}`\n\n_(Tap on the text above to copy it!)_" if user_language == "en" else f"`{share_text}`\n\n_(Нажмите на текст выше, чтобы скопировать его!)_",
            parse_mode="Markdown"
        )
    else:
        await callback.answer("Achievement not found.")

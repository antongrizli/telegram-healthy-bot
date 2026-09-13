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
    await state.clear()
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
    if range_type not in ("daily", "weekly", "monthly"):
        return
    async with AsyncSessionLocal() as db:
        await crud.record_event(db, callback.from_user.id, 'report_opened')
    
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
        
        from src.services.ux import streak_text
        await callback.message.answer(streak_text(user, streaks, user_language), parse_mode=None)

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
        
        share_title = i18n_locales.get_text('ux_share_title', user_language)
        share_text = f"{share_title}\n\n{icon} {name} — {desc}\n\n@your_healthy_body_bot"
        await callback.answer()
        await callback.message.answer(share_text, parse_mode=None)
        await callback.message.answer(i18n_locales.get_text('ux_share_hint', user_language))
    else:
        await callback.answer(i18n_locales.get_text('ux_badge_missing', user_language))

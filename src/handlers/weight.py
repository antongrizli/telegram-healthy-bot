from datetime import datetime
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.services import gamification
from src.keyboards import reply
from src.config import settings

router = Router()

class WeightState(StatesGroup):
    waiting_for_weight = State()

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_log_weight")))
async def start_weight_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(WeightState.waiting_for_weight)
    await message.answer(
        i18n_locales.get_text("weight_prompt", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(WeightState.waiting_for_weight, Command("cancel"))
@router.message(WeightState.waiting_for_weight, F.text.in_(i18n_locales.get_all_translations("btn_cancel")))
async def cancel_weight_logging(message: Message, state: FSMContext, user_language: str, db_user = None):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        i18n_locales.get_text("weight_cancelled", user_language),
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
        parse_mode="Markdown"
    )

@router.message(WeightState.waiting_for_weight)
async def process_weight_input(message: Message, state: FSMContext, user_language: str):
    try:
        from src.services.ux import numeric
        weight = numeric(message.text, 20.01, 500)
        if weight <= 20 or weight > 500:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_weight", user_language))
        return
        
    user_id = message.from_user.id
    
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        await crud.save_weight_entry(db, user_id, weight)

        # Process gamification silently
        if user:
            await gamification.process_weight_log_streak(db, user)
            await gamification.check_new_achievements(db, user_id)
        
    response_msg = i18n_locales.format_weight_logged(weight, user_language)
    
    await state.clear()
    from src.keyboards.reply import get_main_menu
    from src.config import settings
    is_admin = bool(user and (user.is_admin or user.telegram_id in settings.ADMIN_USER_IDS))
    await message.answer(
        response_msg,
        reply_markup=get_main_menu(user_language, is_admin=is_admin)
    )

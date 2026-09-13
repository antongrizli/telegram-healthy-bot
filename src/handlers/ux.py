from datetime import datetime, UTC
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.services import ux
from src.keyboards import reply
from src.utils.i18n_locales import get_text as tr, get_all_translations
from src.config import settings

router = Router()

class WaterState(StatesGroup):
    amount = State()

def web_button(key, lang, target=''):
    return InlineKeyboardButton(text=tr(key, lang), web_app=WebAppInfo(url=f'{settings.WEBAPP_URL}{target}'))

def today_keyboard(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr('btn_log_food', lang), callback_data='ux:food')],
        [InlineKeyboardButton(text=tr('btn_daily_report', lang), callback_data='report_range:daily'),
         web_button('btn_my_meals', lang)],
        [InlineKeyboardButton(text=tr('btn_pending_meals', lang), callback_data='ux:pending')]])

@router.message(F.text.in_(get_all_translations('ux_add')))
async def add_menu(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await message.answer(tr('ux_quick_food', user_language), reply_markup=reply.get_section_menu('add', user_language))

@router.message(F.text.in_(get_all_translations('ux_more')))
async def more_menu(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await message.answer(tr('ux_more', user_language), reply_markup=reply.get_section_menu('more', user_language))
    await message.answer(tr('ux_settings', user_language), reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[web_button('ux_settings', user_language, '?panel=settings')]]))

@router.message(F.text.in_(get_all_translations('ux_today')))
async def today(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    async with AsyncSessionLocal() as db:
        data = await ux.today_data(db, db_user)
    await message.answer(data['summary'], reply_markup=today_keyboard(user_language))

@router.message(F.text.in_(get_all_translations('ux_progress')))
async def progress(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await message.answer(tr('ux_progress', user_language), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [web_button('ux_progress', user_language, '?tab=charts')],
        [web_button('btn_all_achievements', user_language, '?tab=achievements'), web_button('btn_view_card', user_language, '?tab=health-card')],
        [InlineKeyboardButton(text=tr('btn_weekly_report', user_language), callback_data='report_range:weekly')]]))

@router.message(F.text.in_(get_all_translations('ux_water')))
async def start_water(message: Message, state: FSMContext, user_language: str):
    await state.set_state(WaterState.amount)
    await message.answer(tr('ux_water_prompt', user_language), reply_markup=reply.get_cancel_keyboard(user_language))

@router.message(WaterState.amount)
async def water(message: Message, state: FSMContext, user_language: str, db_user):
    try:
        amount = int(ux.numeric(message.text, 1, 5000))
    except (ValueError, TypeError):
        await message.answer(tr('ux_invalid', user_language))
        return
    async with AsyncSessionLocal() as db:
        await crud.add_water_log(db, message.from_user.id, amount)
        data = await ux.today_data(db, db_user)
    await state.clear()
    await message.answer(tr('ux_saved', user_language) + '\n\n' + data['summary'], reply_markup=reply.get_main_menu(user_language, db_user.is_admin or db_user.telegram_id in settings.ADMIN_USER_IDS))

@router.callback_query(F.data.in_({'ux:food', 'ux:pending'}))
async def quick_callback(callback: CallbackQuery, state: FSMContext, user_language: str, db_user):
    await callback.answer()
    if callback.data == 'ux:pending':
        from src.handlers.food import show_pending_meals
        async with AsyncSessionLocal() as db:
            drafts = await crud.get_pending_meals(db, callback.from_user.id)
        await show_pending_meals(callback.message, user_language, drafts)
    else:
        from src.handlers.food import FoodLoggingState
        await state.clear()
        await state.set_state(FoodLoggingState.waiting_for_input)
        await callback.message.answer(tr('ux_quick_food', user_language), reply_markup=reply.get_cancel_keyboard(user_language))

@router.callback_query(F.data.startswith('ux:report:'))
async def report_details(callback: CallbackQuery, user_language: str):
    try:
        report_id = int(callback.data.rsplit(':', 1)[1])
    except ValueError:
        await callback.answer()
        return
    async with AsyncSessionLocal() as db:
        saved = await crud.get_saved_report(db, callback.from_user.id, report_id)
        if saved:
            await crud.record_event(db, callback.from_user.id, 'report_opened')
    if not saved:
        await callback.answer(tr('draft_unavailable', user_language), show_alert=True)
        return
    await callback.answer()
    from src.services.scheduler import send_multipart_message
    await send_multipart_message(callback.bot, callback.from_user.id, saved.payload['text'])

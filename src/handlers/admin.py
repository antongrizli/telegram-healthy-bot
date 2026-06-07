import re
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.database.models import User
from src.utils import i18n_locales
from src.keyboards import reply
from src.config import settings

router = Router()

class AdminStatesGroup(StatesGroup):
    waiting_for_broadcast = State()
    viewing_active = State()
    viewing_blocked = State()

@router.message(Command("admin"))
@router.message(F.text.in_(["👑 Admin Panel", "👑 Админ-панель"]))
async def cmd_admin(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("admin_welcome", user_language),
        reply_markup=reply.get_admin_menu(user_language),
        parse_mode="Markdown"
    )

@router.message(AdminStatesGroup.waiting_for_broadcast, F.text.in_(["❌ Cancel", "❌ Отмена"]))
async def cancel_admin_action(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await message.answer(
        i18n_locales.get_text("admin_welcome", user_language),
        reply_markup=reply.get_admin_menu(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(["📊 Stats", "📊 Статистика"]))
async def cmd_admin_stats(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)
        
    stats_text = (
        "📊 **Bot Statistics**:\n\n"
        f"👤 **Total Registered Users**: {stats['total_users']}\n"
        f"🔥 **Active Users (24h)**: {stats['active_users_24h']}\n"
        f"🗓️ **Active Users (7d)**: {stats['active_users_7d']}\n"
        f"✉️ **Messages Processed (24h)**: {stats['messages_24h']}\n"
        f"🍽️ **Food Logs Recorded (24h)**: {stats['food_logs_24h']}\n"
    )
    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_menu(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(["📢 Broadcast", "📢 Рассылка"]))
async def cmd_admin_broadcast(message: Message, state: FSMContext, user_language: str):
    await state.set_state(AdminStatesGroup.waiting_for_broadcast)
    
    prompt = (
        "Please send the broadcast message text:" 
        if user_language == "en" else 
        "Пожалуйста, отправьте текст сообщения для рассылки:"
    )
    await message.answer(
        prompt,
        reply_markup=reply.get_cancel_keyboard(user_language)
    )

@router.message(AdminStatesGroup.waiting_for_broadcast)
async def process_admin_broadcast(message: Message, state: FSMContext, user_language: str):
    broadcast_text = message.text
    await state.clear()
    
    async with AsyncSessionLocal() as db:
        users = await crud.get_all_users(db, include_blocked=False)
        
    success_count = 0
    fail_count = 0
    
    for u in users:
        try:
            await message.bot.send_message(u.telegram_id, broadcast_text, parse_mode="Markdown")
            success_count += 1
        except Exception:
            fail_count += 1
            
    result = i18n_locales.get_text("broadcast_sent", user_language, count=success_count)
    failed_str = f"\nFailed to reach {fail_count} users." if user_language == "en" else f"\nНе удалось доставить {fail_count} пользователям."
    
    await message.answer(result + failed_str)
    
    # Send the admin panel menu again
    await message.answer(
        i18n_locales.get_text("admin_welcome", user_language),
        reply_markup=reply.get_admin_menu(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(["👥 Active Users", "👥 Активные пользователи"]))
async def cmd_admin_active_users(message: Message, state: FSMContext, user_language: str):
    await state.set_state(AdminStatesGroup.viewing_active)
    async with AsyncSessionLocal() as db:
        users = await crud.get_all_users(db, include_blocked=False)
    
    if not users:
        empty_msg = "No active users found." if user_language == "en" else "Активные пользователи не найдены."
        await state.clear()
        await message.answer(empty_msg, reply_markup=reply.get_admin_menu(user_language))
        return
        
    prompt = (
        "👥 **Active Users**:\nClick a button to block the user:"
        if user_language == "en" else
        "👥 **Активные пользователи**:\nНажмите на кнопку, чтобы заблокировать пользователя:"
    )
    await message.answer(
        prompt,
        reply_markup=reply.get_active_users_keyboard(users, user_language),
        parse_mode="Markdown"
    )

@router.message(AdminStatesGroup.viewing_active)
async def process_active_users_view(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    
    if text in ["⬅️ Back to Menu", "⬅️ Назад в меню", "❌ Cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer(
            i18n_locales.get_text("admin_welcome", user_language),
            reply_markup=reply.get_admin_menu(user_language),
            parse_mode="Markdown"
        )
        return
        
    match = re.search(r"ID:\s*(\d+)", text)
    if match:
        target_id = int(match.group(1))
        async with AsyncSessionLocal() as db:
            success = await crud.block_user(db, target_id, block=True)
            users = await crud.get_all_users(db, include_blocked=False)
            
        if success:
            await message.answer(
                "User blocked successfully." if user_language == "en" else "Пользователь заблокирован."
            )
        else:
            await message.answer(
                "User not found." if user_language == "en" else "Пользователь не найден."
            )
            
        if not users:
            empty_msg = "No active users found." if user_language == "en" else "Активные пользователи не найдены."
            await state.clear()
            await message.answer(empty_msg, reply_markup=reply.get_admin_menu(user_language))
        else:
            prompt = (
                "👥 **Active Users**:\nClick a button to block the user:"
                if user_language == "en" else
                "👥 **Активные пользователи**:\nНажмите на кнопку, чтобы заблокировать пользователя:"
            )
            await message.answer(
                prompt,
                reply_markup=reply.get_active_users_keyboard(users, user_language),
                parse_mode="Markdown"
            )
    else:
        async with AsyncSessionLocal() as db:
            users = await crud.get_all_users(db, include_blocked=False)
        if not users:
            await state.clear()
            await message.answer(
                "No active users found." if user_language == "en" else "Активные пользователи не найдены.",
                reply_markup=reply.get_admin_menu(user_language)
            )
        else:
            await message.answer(
                "Please select a user from the keyboard menu below." if user_language == "en"
                else "Пожалуйста, выберите пользователя на клавиатуре ниже.",
                reply_markup=reply.get_active_users_keyboard(users, user_language)
            )

@router.message(F.text.in_(["🚫 Blocked Users", "🚫 Заблокированные"]))
async def cmd_admin_blocked_users(message: Message, state: FSMContext, user_language: str):
    await state.set_state(AdminStatesGroup.viewing_blocked)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_blocked == True))
        users = list(result.scalars().all())
        
    if not users:
        empty_msg = "No blocked users found." if user_language == "en" else "Заблокированных пользователей не найдено."
        await state.clear()
        await message.answer(empty_msg, reply_markup=reply.get_admin_menu(user_language))
        return
        
    prompt = (
        "🚫 **Blocked Users**:\nClick a button to unblock the user:"
        if user_language == "en" else
        "🚫 **Заблокированные пользователи**:\nНажмите на кнопку, чтобы разблокировать пользователя:"
    )
    await message.answer(
        prompt,
        reply_markup=reply.get_blocked_users_keyboard(users, user_language),
        parse_mode="Markdown"
    )

@router.message(AdminStatesGroup.viewing_blocked)
async def process_blocked_users_view(message: Message, state: FSMContext, user_language: str):
    text = message.text.strip()
    
    if text in ["⬅️ Back to Menu", "⬅️ Назад в меню", "❌ Cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer(
            i18n_locales.get_text("admin_welcome", user_language),
            reply_markup=reply.get_admin_menu(user_language),
            parse_mode="Markdown"
        )
        return
        
    match = re.search(r"ID:\s*(\d+)", text)
    if match:
        target_id = int(match.group(1))
        async with AsyncSessionLocal() as db:
            success = await crud.block_user(db, target_id, block=False)
            result = await db.execute(select(User).where(User.is_blocked == True))
            users = list(result.scalars().all())
            
        if success:
            await message.answer(
                "User unblocked successfully." if user_language == "en" else "Пользователь разблокирован."
            )
        else:
            await message.answer(
                "User not found." if user_language == "en" else "Пользователь не найден."
            )
            
        if not users:
            empty_msg = "No blocked users found." if user_language == "en" else "Заблокированных пользователей не найдено."
            await state.clear()
            await message.answer(empty_msg, reply_markup=reply.get_admin_menu(user_language))
        else:
            prompt = (
                "🚫 **Blocked Users**:\nClick a button to unblock the user:"
                if user_language == "en" else
                "🚫 **Заблокированные пользователи**:\nНажмите на кнопку, чтобы разблокировать пользователя:"
            )
            await message.answer(
                prompt,
                reply_markup=reply.get_blocked_users_keyboard(users, user_language),
                parse_mode="Markdown"
            )
    else:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.is_blocked == True))
            users = list(result.scalars().all())
        if not users:
            await state.clear()
            await message.answer(
                "No blocked users found." if user_language == "en" else "Заблокированных пользователей не найдено.",
                reply_markup=reply.get_admin_menu(user_language)
            )
        else:
            await message.answer(
                "Please select a user from the keyboard menu below." if user_language == "en"
                else "Пожалуйста, выберите пользователя на клавиатуре ниже.",
                reply_markup=reply.get_blocked_users_keyboard(users, user_language)
            )

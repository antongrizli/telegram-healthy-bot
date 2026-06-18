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

@router.message(StateFilter("*"), Command("admin"))
@router.message(StateFilter("*"), F.text.in_(["👑 Admin Panel", "👑 Админ-панель"]))
async def cmd_admin(message: Message, state: FSMContext, user_language: str):
    await state.clear()
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

def format_dict_stats(stats_dict: dict, label_map: dict = None) -> str:
    if not stats_dict:
        return "  • No data" if label_map else "  • No errors"
    lines = []
    for k, v in stats_dict.items():
        if k is None:
            continue
        label = label_map.get(k, k) if label_map else k
        lines.append(f"  • **{label}**: {v}")
    return "\n".join(lines)

@router.message(F.text.in_(["📊 Stats", "📊 Статистика"]))
async def cmd_admin_stats(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("admin_stats_menu_welcome", user_language),
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_demographics")))
async def cmd_admin_stats_demographics(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)
        
    goal_labels = {
        "lose_weight": i18n_locales.get_text("goal_lose", user_language),
        "maintain": i18n_locales.get_text("goal_maintain", user_language),
        "gain_weight": i18n_locales.get_text("goal_gain_w", user_language),
        "gain_muscle": i18n_locales.get_text("goal_gain_m", user_language)
    }
    sex_labels = {
        "male": i18n_locales.get_text("sex_male", user_language),
        "female": i18n_locales.get_text("sex_female", user_language)
    }
    lang_labels = {
        "en": i18n_locales.get_text("lang_en", user_language),
        "ru": i18n_locales.get_text("lang_ru", user_language),
        "uk": i18n_locales.get_text("lang_uk", user_language),
        "pl": i18n_locales.get_text("lang_pl", user_language),
        "de": i18n_locales.get_text("lang_de", user_language),
        "tr": i18n_locales.get_text("lang_tr", user_language),
        "es": i18n_locales.get_text("lang_es", user_language)
    }

    if user_language == "ru":
        stats_text = (
            "👥 **Демография пользователей**:\n\n"
            f"🌐 **Языки**:\n{format_dict_stats(stats['languages'], lang_labels)}\n\n"
            f"🎯 **Цели**:\n{format_dict_stats(stats['goals'], goal_labels)}\n\n"
            f"👤 **Пол**:\n{format_dict_stats(stats['genders'], sex_labels)}\n\n"
            f"🔔 **Уведомления отключены**: {stats['notifications_disabled_count']} пользователей\n"
        )
    else:
        stats_text = (
            "👥 **User Demographics**:\n\n"
            f"🌐 **Languages**:\n{format_dict_stats(stats['languages'], lang_labels)}\n\n"
            f"🎯 **Fitness Goals**:\n{format_dict_stats(stats['goals'], goal_labels)}\n\n"
            f"👤 **Genders**:\n{format_dict_stats(stats['genders'], sex_labels)}\n\n"
            f"🔔 **Notifications Disabled**: {stats['notifications_disabled_count']} users\n"
        )

    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_engagement")))
async def cmd_admin_stats_engagement(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)
        
    active_24h = stats['active_users_24h']
    avg_meals = (stats['food_logs_24h'] / active_24h) if active_24h > 0 else 0.0

    if user_language == "ru":
        stats_text = (
            "⚡ **Активность пользователей**:\n\n"
            f"👤 **Всего зарегистрированных**: {stats['total_users']}\n"
            f"🔥 **Активные (24ч / 7д / 30д)**: {stats['active_users_24h']} / {stats['active_users_7d']} / {stats['active_users_30d']}\n"
            f"📈 **Новые регистрации (24ч / 7д / 30д)**: {stats['new_users_24h']} / {stats['new_users_7d']} / {stats['new_users_30d']}\n"
            f"✉️ **Сообщений обработано (24ч)**: {stats['messages_24h']}\n"
            f"🍽️ **Записей еды (24ч)**: {stats['food_logs_24h']}\n"
            f"🍽️ **Ср. число приемов пищи (на активного 24ч)**: {avg_meals:.1f}\n"
            f"⚖️ **Записей веса (7д)**: {stats['weight_logs_7d']}\n"
        )
    else:
        stats_text = (
            "⚡ **User Engagement & Retention**:\n\n"
            f"👤 **Total Users**: {stats['total_users']}\n"
            f"🔥 **Active Users (24h / 7d / 30d)**: {stats['active_users_24h']} / {stats['active_users_7d']} / {stats['active_users_30d']}\n"
            f"📈 **New Registrations (24h / 7d / 30d)**: {stats['new_users_24h']} / {stats['new_users_7d']} / {stats['new_users_30d']}\n"
            f"✉️ **Messages Processed (24h)**: {stats['messages_24h']}\n"
            f"🍽️ **Food Logs Recorded (24h)**: {stats['food_logs_24h']}\n"
            f"🍽️ **Avg Meals Logged (per Active User 24h)**: {avg_meals:.1f}\n"
            f"⚖️ **Weight Logs Recorded (7d)**: {stats['weight_logs_7d']}\n"
        )

    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_ai")))
async def cmd_admin_stats_ai(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)

    ai_types_labels = {
        "analyze_food_input": "Analyze Food" if user_language == "en" else "Анализ еды",
        "adjust_food_analysis": "Adjust Analysis" if user_language == "en" else "Корректировка еды",
        "adjust_meal_edit": "Adjust Meal Edit" if user_language == "en" else "Редактирование приема пищи",
        "generate_report": "Generate Report" if user_language == "en" else "Генерация отчета"
    }

    if user_language == "ru":
        stats_text = (
            "🤖 **Статистика ИИ (24ч)**:\n\n"
            f"⚡ **Частота запросов (1м / 24ч)**: {stats['api_calls_1m']} / {stats['api_calls_24h']}\n"
            f"📸 **Типы ввода (Фото vs. Текст)**:\n"
            f"  • По фото: {stats['modality_photo_24h']}\n"
            f"  • Только текст: {stats['modality_text_24h']}\n"
            f"✏️ **Частота корректировок ИИ**: {stats['correction_rate_24h']:.1f}%\n"
            f"📋 **Типы запросов**:\n{format_dict_stats(stats['ai_request_types_24h'], ai_types_labels)}\n"
        )
    else:
        stats_text = (
            "🤖 **AI & Prompt Statistics (24h)**:\n\n"
            f"⚡ **API Call Rate (1m / 24h)**: {stats['api_calls_1m']} / {stats['api_calls_24h']}\n"
            f"📸 **Modality (Photo vs. Text logs)**:\n"
            f"  • Photo logs: {stats['modality_photo_24h']}\n"
            f"  • Text-only logs: {stats['modality_text_24h']}\n"
            f"✏️ **Correction Rate**: {stats['correction_rate_24h']:.1f}%\n"
            f"📋 **Request Types breakdown**:\n{format_dict_stats(stats['ai_request_types_24h'], ai_types_labels)}\n"
        )

    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_queue")))
async def cmd_admin_stats_queue(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)

    if user_language == "ru":
        stats_text = (
            "⚙️ **Состояние очереди и надежность (24ч)**:\n\n"
            f"⏳ **Запросов ИИ в очереди**: {stats['queued_requests']}\n"
            f"📊 **Статусы очереди**:\n{format_dict_stats(stats['queue_status_counts'])}\n"
            f"⏱️ **Среднее время ожидания в очереди**: {stats['queue_avg_latency_seconds']:.1f} сек.\n\n"
            f"⚠️ **Последние ошибки в очереди**:\n{format_dict_stats(stats['queue_errors'])}\n"
        )
    else:
        stats_text = (
            "⚙️ **Queue & Reliability (24h)**:\n\n"
            f"⏳ **Active / Pending Requests in Queue**: {stats['queued_requests']}\n"
            f"📊 **Queue Status counts**:\n{format_dict_stats(stats['queue_status_counts'])}\n"
            f"⏱️ **Average Queue Latency**: {stats['queue_avg_latency_seconds']:.1f} seconds\n\n"
            f"⚠️ **Recent Queue Errors**:\n{format_dict_stats(stats['queue_errors'])}\n"
        )

    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_back_admin")))
async def cmd_admin_stats_back(message: Message, user_language: str):
    await message.answer(
        i18n_locales.get_text("admin_welcome", user_language),
        reply_markup=reply.get_admin_menu(user_language),
        parse_mode="Markdown"
    )


@router.message(F.text.in_(["📢 Broadcast", "📢 Рассылка"]))
async def cmd_admin_broadcast(message: Message, state: FSMContext, user_language: str):
    await state.set_state(AdminStatesGroup.waiting_for_broadcast)
    
    prompt = i18n_locales.get_text("broadcast_prompt", user_language)
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
    failed_str = i18n_locales.get_text("admin_broadcast_failed_count", user_language, fail_count=fail_count)
    
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
        empty_msg = i18n_locales.get_text("admin_no_active_users", user_language)
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
                i18n_locales.get_text("admin_user_blocked", user_language)
            )
        else:
            await message.answer(
                i18n_locales.get_text("admin_user_not_found", user_language)
            )
            
        if not users:
            empty_msg = i18n_locales.get_text("admin_no_active_users", user_language)
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
                i18n_locales.get_text("admin_no_active_users", user_language),
                reply_markup=reply.get_admin_menu(user_language)
            )
        else:
            await message.answer(
                i18n_locales.get_text("admin_select_user", user_language),
                reply_markup=reply.get_active_users_keyboard(users, user_language)
            )

@router.message(F.text.in_(["🚫 Blocked Users", "🚫 Заблокированные"]))
async def cmd_admin_blocked_users(message: Message, state: FSMContext, user_language: str):
    await state.set_state(AdminStatesGroup.viewing_blocked)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.is_blocked == True))
        users = list(result.scalars().all())
        
    if not users:
        empty_msg = i18n_locales.get_text("admin_no_blocked_users", user_language)
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
                i18n_locales.get_text("admin_user_unblocked", user_language)
            )
        else:
            await message.answer(
                i18n_locales.get_text("admin_user_not_found", user_language)
            )
            
        if not users:
            empty_msg = i18n_locales.get_text("admin_no_blocked_users", user_language)
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
                i18n_locales.get_text("admin_no_blocked_users", user_language),
                reply_markup=reply.get_admin_menu(user_language)
            )
        else:
            await message.answer(
                i18n_locales.get_text("admin_select_user", user_language),
                reply_markup=reply.get_blocked_users_keyboard(users, user_language)
            )

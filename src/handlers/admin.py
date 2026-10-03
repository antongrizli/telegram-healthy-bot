import unicodedata
import re
from html import escape
from datetime import UTC
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, LinkPreviewOptions, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.keyboards import reply, inline
from src.config import settings
from src.utils.telegram_edit import try_edit

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

@router.message(AdminStatesGroup.waiting_for_broadcast, Command("cancel"))
@router.message(AdminStatesGroup.waiting_for_broadcast, F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
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


def format_queue_errors(queue_errors: dict, lang: str = "en") -> str:
    if not queue_errors:
        return "  • Ошибок нет" if lang == "ru" else "  • No errors"
    lines = []
    for err_msg, count in queue_errors.items():
        if err_msg is None:
            continue
        clean_err = escape(str(err_msg), quote=False)
        label = "Количество" if lang == "ru" else "Count"
        lines.append(f"  • <b>{label}</b>: {count}\n<pre>{clean_err}</pre>")
    return "\n".join(lines)


def format_queue_status_counts(stats_dict: dict) -> str:
    if not stats_dict:
        return "  • No data"
    return "\n".join(
        f"  • <b>{escape(str(status))}</b>: {count}"
        for status, count in stats_dict.items() if status is not None)

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

def format_recent_user_activity(users: list[dict], language: str) -> str:
    lines = [i18n_locales.get_text("admin_recent_users_header", language)]
    if not users:
        lines.append(i18n_locales.get_text("admin_recent_users_empty", language))
    for user in users:
        def timestamp(value):
            if value is None:
                return i18n_locales.get_text("admin_no_meals_yet", language)
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        name = " ".join(user["name"].split())[:80]
        lines.append(i18n_locales.get_text("admin_recent_user_row", language,
            name=name, user_id=user["telegram_id"],
            joined_at=timestamp(user["joined_at"]), last_meal_at=timestamp(user["last_meal_at"]),
            last_bot_use_at=timestamp(user.get("last_bot_use_at"))))
    return "\n\n".join(lines)


@router.message(F.text.in_(i18n_locales.get_all_translations("btn_stats_engagement")))
async def cmd_admin_stats_engagement(message: Message, user_language: str):
    async with AsyncSessionLocal() as db:
        stats = await crud.get_admin_stats(db)
        recent_users = await crud.get_recent_user_activity(db)
        metrics = await crud.get_product_metrics(db)
        
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

    await message.answer(format_recent_user_activity(recent_users, user_language), parse_mode=None)
    await message.answer(i18n_locales.get_text('ux_metrics', user_language,
        completed=metrics['onboarding_completed'], started=metrics['onboarding_started'],
        seconds=metrics['first_meal_median_seconds'] if metrics['first_meal_median_seconds'] is not None else '—',
        confirmed=metrics['confirmed'], analyzed=metrics['analyzed'],
        d1=f"{metrics['retention']['D1']['returned']}/{metrics['retention']['D1']['eligible']}",
        d7=f"{metrics['retention']['D7']['returned']}/{metrics['retention']['D7']['eligible']}",
        meal_days=metrics['active_days_with_meal'], active_days=metrics['active_days'],
        opened=metrics['events']['meal_opened'], submitted=metrics['events']['meal_submitted'], reports=metrics['events']['report_opened']), parse_mode=None)

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

    from src.services.rate_limiter import get_worker_health
    worker_health = get_worker_health()
    worker_status = worker_health.get("status", "unknown")
    worker_icon = "🟢" if worker_health.get("healthy") else ("🔴" if worker_status in ("crashed", "stalled") else "⚪")
    worker_heartbeat_age = worker_health.get("heartbeat_age_seconds")
    worker_hb_str = f" ({worker_heartbeat_age:.1f}s ago)" if worker_heartbeat_age is not None else ""

    oldest_pending_age = stats.get("queue_oldest_pending_age_seconds", 0.0)
    oldest_pending_str = f"{oldest_pending_age:.1f} сек." if oldest_pending_age > 0 else ("—" if user_language == "ru" else "none")
    error_rate = stats.get("queue_error_rate_24h", 0.0) * 100.0

    if user_language == "ru":
        stats_text = (
            "⚙️ <b>Состояние очереди и надежность (24ч)</b>:\n\n"
            f"🤖 <b>Воркер очереди</b>: {worker_icon} {worker_status}{worker_hb_str}\n"
            f"⏳ <b>Запросов ИИ в очереди</b>: {stats['queued_requests']}\n"
            f"⌛ <b>Возраст старейшего задания</b>: {oldest_pending_str}\n"
            f"📊 <b>Статусы очереди</b>:\n{format_queue_status_counts(stats['queue_status_counts'])}\n"
            f"⏱️ <b>Среднее время ожидания в очереди</b>: {stats['queue_avg_latency_seconds']:.1f} сек.\n"
            f"📉 <b>Доля ошибок (24ч)</b>: {error_rate:.1f}%\n\n"
            f"⚠️ <b>Последние ошибки в очереди</b>:\n{format_queue_errors(stats['queue_errors'], user_language)}\n"
        )
    else:
        stats_text = (
            "⚙️ <b>Queue & Reliability (24h)</b>:\n\n"
            f"🤖 <b>Queue Worker</b>: {worker_icon} {worker_status}{worker_hb_str}\n"
            f"⏳ <b>Active / Pending Requests in Queue</b>: {stats['queued_requests']}\n"
            f"⌛ <b>Oldest Pending Task Age</b>: {oldest_pending_str}\n"
            f"📊 <b>Queue Status counts</b>:\n{format_queue_status_counts(stats['queue_status_counts'])}\n"
            f"⏱️ <b>Average Queue Latency</b>: {stats['queue_avg_latency_seconds']:.1f} seconds\n"
            f"📉 <b>Error Rate (24h)</b>: {error_rate:.1f}%\n\n"
            f"⚠️ <b>Recent Queue Errors</b>:\n{format_queue_errors(stats['queue_errors'], user_language)}\n"
        )

    await message.answer(
        stats_text,
        reply_markup=reply.get_admin_stats_keyboard(user_language),
        parse_mode="HTML"
    )

    failed_count = stats.get("queue_status_counts", {}).get("failed", 0)
    if failed_count > 0:
        btn_text = f"⚠️ Управление сбоями ({failed_count})" if user_language == "ru" else f"⚠️ Manage Failures ({failed_count})"
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=btn_text, callback_data="dlq:page:0")]
        ])
        prompt_text = (
            f"⚠️ В очереди есть неудачные задания ({failed_count}). Нажмите ниже для просмотра и управления:"
            if user_language == "ru"
            else f"⚠️ There are failed tasks in the queue ({failed_count}). Tap below to inspect and manage:"
        )
        await message.answer(prompt_text, reply_markup=inline_kb)

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

@router.message(
    AdminStatesGroup.waiting_for_broadcast,
    lambda msg: not (msg.text and (msg.text.startswith("/") or msg.text in ["⬅️ Back to Main Menu", "⬅️ Главное меню"]))
)
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

def format_users_table(data, language, blocked=False):
    """Render mobile-readable user blocks without fixed-width columns."""
    def clean(value, size):
        value = ''.join(c for c in str(value or '—') if not unicodedata.category(c).startswith('C'))
        return ' '.join(value.split())[:size]

    def stamp(value):
        if value is None:
            return '—'
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime('%Y-%m-%d %H:%M')

    header = i18n_locales.get_text('admin_users_blocked' if blocked else 'admin_users_active', language)
    blocks = [f"<b>{escape(header)}</b> · {data['total']} · UTC"]
    for user in data['users']:
        username = user['username'] or ''
        url = (f'https://t.me/{username}' if re.fullmatch(r'[A-Za-z0-9_]{1,32}', username)
               else f"tg://user?id={int(user['telegram_id'])}")
        lines = [f'<b><a href="{escape(url, quote=True)}">{escape(clean(user["name"], 64))}</a></b>']
        if user['username']:
            lines.append('@' + escape(clean(user['username'], 32)))
        lines.append(f"ID: <code>{user['telegram_id']}</code>")
        for key, value in [('admin_users_joined', user['joined_at']), ('admin_users_activity', user['last_active_at'])]:
            lines.append(f"{escape(i18n_locales.get_text(key, language))}: {stamp(value)}")
        blocks.append('\n'.join(lines))
    if not data['users']:
        blocks.append(escape(i18n_locales.get_text('admin_no_blocked_users' if blocked else 'admin_no_active_users', language)))
    return '\n\n'.join(blocks)


async def show_users_page(message, language, actor_id, blocked=False, page=0, edit=False):
    async with AsyncSessionLocal() as db:
        data = await crud.get_admin_users_page(db, blocked, page)
    text = format_users_table(data, language, blocked)
    markup = inline.get_admin_users_inline(data, language, blocked, actor_id)
    if edit:
        from aiogram.exceptions import TelegramBadRequest
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode='HTML',
                                    link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise
    else:
        await message.answer(text, reply_markup=markup, parse_mode='HTML',
                             link_preview_options=LinkPreviewOptions(is_disabled=True))


@router.message(F.text.in_(["👥 Active Users", "👥 Активные пользователи"]))
async def cmd_admin_active_users(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await show_users_page(message, user_language, message.from_user.id)


@router.message(F.text.in_(["🚫 Blocked Users", "🚫 Заблокированные"]))
async def cmd_admin_blocked_users(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    await show_users_page(message, user_language, message.from_user.id, blocked=True)


@router.callback_query(F.data.startswith('adminusers:'))
async def admin_users_callback(callback: CallbackQuery, state: FSMContext, user_language: str):
    actor_id = callback.from_user.id
    async with AsyncSessionLocal() as db:
        actor = await crud.get_user(db, actor_id)
        if not (actor_id in settings.ADMIN_USER_IDS or (actor and actor.is_admin and not actor.is_blocked)):
            await callback.answer(i18n_locales.get_text('admin_only', user_language), show_alert=True)
            return
        if not isinstance(callback.message, Message) or callback.message.chat.id != actor_id:
            await callback.answer(i18n_locales.get_text('ux_invalid', user_language), show_alert=True)
            return
        parts = callback.data.split(':')
        if parts == ['adminusers', 'back']:
            await state.clear()
            await callback.answer()
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(i18n_locales.get_text('admin_welcome', user_language),
                                          reply_markup=reply.get_admin_menu(user_language))
            return
        if len(parts) != 4 or parts[1] not in ('page', 'block', 'unblock'):
            await callback.answer(i18n_locales.get_text('ux_invalid', user_language), show_alert=True)
            return
        try:
            page = int(parts[3])
            if not 0 <= page <= 1_000_000:
                raise ValueError()
            if parts[1] == 'page':
                if parts[2] not in ('active', 'blocked'):
                    raise ValueError()
                blocked = parts[2] == 'blocked'
            else:
                target_id = int(parts[2])
                target = await crud.get_user(db, target_id)
                if not target:
                    await callback.answer(i18n_locales.get_text('admin_user_not_found', user_language), show_alert=True)
                    return
                if target_id == actor_id or target.is_admin or target_id in settings.ADMIN_USER_IDS:
                    await callback.answer(i18n_locales.get_text('admin_users_protected', user_language), show_alert=True)
                    return
                blocked = parts[1] == 'unblock'
                await crud.block_user(db, target_id, block=not blocked)
        except ValueError:
            await callback.answer(i18n_locales.get_text('ux_invalid', user_language), show_alert=True)
            return
    await callback.answer()
    await show_users_page(callback.message, user_language, actor_id, blocked, page, edit=True)


def format_payload_summary(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "—"
    parts = []
    if payload.get("text_description"):
        text = str(payload["text_description"])
        parts.append(f"Текст: {text[:60]}..." if len(text) > 60 else f"Текст: {text}")
    elif payload.get("raw_text"):
        text = str(payload["raw_text"])
        parts.append(f"Текст: {text[:60]}..." if len(text) > 60 else f"Текст: {text}")
    if payload.get("image_file_id") or payload.get("image_file_ids"):
        parts.append("Фото прикреплено")
    if payload.get("report_type"):
        parts.append(f"Тип отчета: {payload['report_type']}")
    if payload.get("correction_text"):
        corr = str(payload["correction_text"])
        parts.append(f"Коррекция: {corr[:60]}..." if len(corr) > 60 else f"Коррекция: {corr}")
    if payload.get("result_draft_id"):
        parts.append(f"Draft ID: #{payload['result_draft_id']}")
    return escape("; ".join(parts), quote=False) if parts else "—"


async def render_failed_queue_page(db: AsyncSession, user_language: str, page: int = 0):
    page = max(0, page)
    tasks, total = await crud.get_failed_queue_tasks(db, limit=5, offset=page * 5)
    total_pages = max(1, (total + 4) // 5)
    requested_page = page
    page = max(0, min(page, total_pages - 1))
    if page != requested_page:
        tasks, total = await crud.get_failed_queue_tasks(db, limit=5, offset=page * 5)

    if total == 0:
        empty_text = (
            "✅ <b>В очереди нет неудачных заданий.</b>\nВсе задачи выполнены или находятся в обработке."
            if user_language == "ru"
            else "✅ <b>No failed tasks in queue.</b>\nAll tasks are completed or currently processing."
        )
        refresh_label = "🔄 Обновить" if user_language == "ru" else "🔄 Refresh"
        close_label = "❌ Закрыть" if user_language == "ru" else "❌ Close"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=refresh_label, callback_data="dlq:page:0"),
            InlineKeyboardButton(text=close_label, callback_data="dlq:close")
        ]])
        return empty_text, kb

    lines = []
    if user_language == "ru":
        lines.append(f"⚠️ <b>Неудачные задания очереди</b> (Всего: {total}, Стр. {page+1}/{total_pages}):\n")
        for t in tasks:
            created_str = t.created_at.strftime("%H:%M:%S") if t.created_at else "—"
            err = t.last_error or t.error_message or "Unknown error"
            clean_err = escape(str(err)[:80] + ("..." if len(str(err)) > 80 else ""), quote=False)
            lines.append(
                f"• <b>#{t.id}</b> · <code>{escape(t.request_type)}</code> · User: <code>{t.user_id}</code>\n"
                f"  Попыток: {t.retry_count} · Создано: {created_str}\n"
                f"  Ошибка: <i>{clean_err}</i>\n"
            )
    else:
        lines.append(f"⚠️ <b>Failed Queue Tasks</b> (Total: {total}, Page {page+1}/{total_pages}):\n")
        for t in tasks:
            created_str = t.created_at.strftime("%H:%M:%S") if t.created_at else "—"
            err = t.last_error or t.error_message or "Unknown error"
            clean_err = escape(str(err)[:80] + ("..." if len(str(err)) > 80 else ""), quote=False)
            lines.append(
                f"• <b>#{t.id}</b> · <code>{escape(t.request_type)}</code> · User: <code>{t.user_id}</code>\n"
                f"  Retries: {t.retry_count} · Created: {created_str}\n"
                f"  Error: <i>{clean_err}</i>\n"
            )

    text = "\n".join(lines)
    kb = inline.get_admin_dlq_inline(tasks, page, total_pages, user_language)
    return text, kb


async def render_failed_task_detail(db: AsyncSession, task_id: int, user_language: str, page: int = 0):
    task = await crud.get_queue_task(db, task_id)
    if not task:
        not_found_text = "❌ Задание не найдено." if user_language == "ru" else "❌ Task not found."
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ К списку" if user_language == "ru" else "◀️ Back", callback_data=f"dlq:page:{page}")
        ]])
        return not_found_text, kb

    created_str = task.created_at.strftime("%Y-%m-%d %H:%M:%S") if task.created_at else "—"
    proc_str = task.processed_at.strftime("%Y-%m-%d %H:%M:%S") if task.processed_at else "—"
    user = await crud.get_user(db, task.user_id)
    user_str = f"<code>{task.user_id}</code>"
    if user and user.username:
        user_str += f" (@{escape(user.username)})"
    elif user and user.name:
        user_str += f" ({escape(user.name)})"

    payload_summary = format_payload_summary(task.payload)
    clean_err = escape(str(task.error_message or '—'), quote=False)
    clean_last_err = escape(str(task.last_error or '—'), quote=False)

    if user_language == "ru":
        text = (
            f"📋 <b>Задание очереди #{task.id}</b>\n\n"
            f"• <b>Пользователь</b>: {user_str}\n"
            f"• <b>Тип запроса</b>: <code>{escape(task.request_type)}</code>\n"
            f"• <b>Статус</b>: <code>{escape(task.status)}</code>\n"
            f"• <b>Число попыток</b>: {task.retry_count}\n"
            f"• <b>Создано</b>: {created_str} UTC\n"
            f"• <b>Обработано</b>: {proc_str} UTC\n"
            f"• <b>Сообщение об ошибке</b>: <i>{clean_err}</i>\n"
            f"• <b>Последняя причина сбоя</b>: <code>{clean_last_err}</code>\n"
            f"• <b>Содержимое</b>: {payload_summary}\n"
        )
    else:
        text = (
            f"📋 <b>Queue Task #{task.id}</b>\n\n"
            f"• <b>User</b>: {user_str}\n"
            f"• <b>Request Type</b>: <code>{escape(task.request_type)}</code>\n"
            f"• <b>Status</b>: <code>{escape(task.status)}</code>\n"
            f"• <b>Retry Count</b>: {task.retry_count}\n"
            f"• <b>Created</b>: {created_str} UTC\n"
            f"• <b>Processed</b>: {proc_str} UTC\n"
            f"• <b>Error Message</b>: <i>{clean_err}</i>\n"
            f"• <b>Last Failure Cause</b>: <code>{clean_last_err}</code>\n"
            f"• <b>Payload</b>: {payload_summary}\n"
        )

    kb = inline.get_admin_dlq_task_inline(task.id, page, user_language)
    return text, kb


@router.message(Command("failed_queue"))
@router.message(Command("dlq"))
@router.message(F.text.in_(["⚠️ Failed Queue", "⚠️ Сбои очереди"]))
async def cmd_admin_failed_queue(message: Message, state: FSMContext, user_language: str):
    await state.clear()
    async with AsyncSessionLocal() as db:
        text, kb = await render_failed_queue_page(db, user_language, page=0)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith('dlq:'))
async def admin_dlq_callback(callback: CallbackQuery, state: FSMContext, user_language: str):
    actor_id = callback.from_user.id
    async with AsyncSessionLocal() as db:
        actor = await crud.get_user(db, actor_id)
        if not (actor_id in settings.ADMIN_USER_IDS or (actor and actor.is_admin and not actor.is_blocked)):
            await callback.answer(i18n_locales.get_text('admin_only', user_language), show_alert=True)
            return

        parts = callback.data.split(':')
        action = parts[1] if len(parts) > 1 else ""

        if action == "close":
            await callback.answer()
            if callback.message is not None and hasattr(callback.message, "delete"):
                try:
                    await callback.message.delete()
                except Exception:
                    if hasattr(callback.message, "edit_reply_markup"):
                        await callback.message.edit_reply_markup(reply_markup=None)
            return

        if action == "page":
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            text, kb = await render_failed_queue_page(db, user_language, page=page)
            await callback.answer()
            if callback.message is not None and hasattr(callback.message, "edit_text"):
                if not await try_edit(callback.message.edit_text, text, reply_markup=kb, parse_mode="HTML"):
                    if hasattr(callback.message, "answer"):
                        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

        if action == "view":
            task_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            text, kb = await render_failed_task_detail(db, task_id, user_language, page=page)
            await callback.answer()
            if callback.message is not None and hasattr(callback.message, "edit_text"):
                if not await try_edit(callback.message.edit_text, text, reply_markup=kb, parse_mode="HTML"):
                    if hasattr(callback.message, "answer"):
                        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

        if action == "retry":
            task_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            success, msg_str, task = await crud.retry_queue_task(db, task_id)
            if success:
                alert_text = (
                    f"✅ Задание #{task_id} возвращено в очередь со статусом pending."
                    if user_language == "ru"
                    else f"✅ Task #{task_id} rescheduled as pending."
                )
            else:
                alert_text = f"❌ Ошибка: {msg_str}" if user_language == "ru" else f"❌ Error: {msg_str}"
            await callback.answer(alert_text, show_alert=True)
            text, kb = await render_failed_task_detail(db, task_id, user_language, page=page)
            if callback.message is not None and hasattr(callback.message, "edit_text"):
                if not await try_edit(callback.message.edit_text, text, reply_markup=kb, parse_mode="HTML"):
                    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

        if action == "cancel":
            task_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            success, msg_str, task = await crud.cancel_queue_task(db, task_id)
            if success:
                alert_text = (
                    f"🚫 Задание #{task_id} отменено."
                    if user_language == "ru"
                    else f"🚫 Task #{task_id} cancelled."
                )
            else:
                alert_text = f"❌ Ошибка: {msg_str}" if user_language == "ru" else f"❌ Error: {msg_str}"
            await callback.answer(alert_text, show_alert=True)
            text, kb = await render_failed_task_detail(db, task_id, user_language, page=page)
            if callback.message is not None and hasattr(callback.message, "edit_text"):
                if not await try_edit(callback.message.edit_text, text, reply_markup=kb, parse_mode="HTML"):
                    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return

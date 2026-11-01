"""Run before registration checks so pending users cannot enter any bot flow."""
import logging
from html import escape

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove

from src.database import access
from src.utils.i18n_locales import get_text

logger = logging.getLogger(__name__)


class ApprovalMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from src.database.connection import AsyncSessionLocal
        actor = data.get('event_from_user') or getattr(event, 'from_user', None)
        if actor is None:
            return
        language = data.get('user_language', 'en')
        async with AsyncSessionLocal() as db:
            allowed = await access.has_access(db, actor.id, data.get('db_user'))
            if not allowed:
                user = data.get('db_user')
                if user is not None and user.is_blocked:
                    record, created, admins = None, False, {}
                else:
                    record, created = await access.request_access(db, actor, language)
                    allowed = record.status == 'approved'
                    admins = await access.admin_recipients(db) if created and not allowed else {}
                    pending_count = await access.pending_access_count(db) if created and not allowed else 0
        if allowed:
            data['access_approved'] = True
            return await handler(event, data)
        state = data.get('state')
        if state:
            await state.clear()
        if created:
            bot = data.get('bot') or getattr(event, 'bot', None)
            if bot:
                for admin_id, admin_language in admins.items():
                    text = get_text('access_admin_request', admin_language,
                        name=escape(record.display_name),
                        username=escape('@' + record.username if record.username else '—'),
                        id=actor.id)
                    try:
                        from src.keyboards.inline import get_access_notification_inline
                        await bot.send_message(admin_id, text, parse_mode='HTML',
                            reply_markup=get_access_notification_inline(admin_language, pending_count))
                    except TelegramAPIError:
                        logger.warning('Could not deliver access request to admin %s; available via /pending', admin_id)
        key = ('access_rejected' if record and record.status == 'rejected' else
               'access_blocked' if record is None else 'access_pending')
        if isinstance(event, CallbackQuery):
            await event.answer(get_text(key, language), show_alert=True)
        elif isinstance(event, Message):
            await event.answer(get_text(key, language), reply_markup=ReplyKeyboardRemove(), parse_mode=None)

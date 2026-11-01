"""Admin admission commands and keyboard navigation share authorization and decisions."""
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter

from src.config import settings
from src.services import admin_navigation
from src.database import access, crud
from src.database.models import UserAccess
from src.keyboards import inline, reply
from src.utils import i18n_locales
from src.utils.i18n_locales import get_text
from src.utils.telegram_edit import try_edit

router = Router()


async def is_admin(db, actor_id):
    admin = await crud.get_user(db, actor_id)
    return not (admin and admin.is_blocked) and bool(
        actor_id in settings.ADMIN_USER_IDS or (admin and admin.is_admin))


async def allowed_message(message, language):
    from src.database.connection import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        allowed = await is_admin(db, message.from_user.id)
    if not allowed:
        await message.answer(get_text('admin_only', language))
        return False
    if message.chat.type != 'private':
        await message.answer(get_text('access_usage', language))
        return False
    return True


async def show_pending(message, language, page=1, edit=False):
    from src.database.connection import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        data = await access.pending_access_page(db, page)
    rows = [f"{get_text('access_pending_title', language)} ({data['total']})"]
    for request in data['requests']:
        rows.append(f'<b>{escape(request.display_name[:64])}</b> · '
            f'{escape("@" + request.username if request.username else "—")}\n'
            f'ID: <code>{request.telegram_id}</code>')
    if not data['requests']:
        rows.append(get_text('access_pending_empty', language))
    text = '\n\n'.join(rows)
    kwargs = dict(parse_mode='HTML', reply_markup=inline.get_access_requests_inline(data, language))
    if not edit or not await try_edit(message.edit_text, text, **kwargs):
        await message.answer(text, **kwargs)


async def notify_decision(bot, user_id, language, approve):
    try:
        await bot.send_message(user_id, get_text(
            'access_approved' if approve else 'access_rejected', language), parse_mode=None)
        return True
    except TelegramAPIError:
        return False


def decision_text(user_id, language, approve):
    key = 'access_status_approved' if approve else 'access_status_rejected'
    return get_text('access_decision_done', language, id=user_id, status=get_text(key, language))


@router.message(StateFilter('*'), F.text.func(lambda text: reply.is_access_button(text, 'btn_access_menu')))
async def open_access_menu(message, state, user_language: str):
    if not await allowed_message(message, user_language):
        return
    await state.clear()
    await message.answer(get_text('access_menu_title', user_language),
        reply_markup=await admin_navigation.access_menu(user_language), parse_mode=None)


@router.message(StateFilter('*'), F.text.in_(i18n_locales.get_all_translations('btn_access_back')))
async def back_to_admin(message, state, user_language: str):
    if not await allowed_message(message, user_language):
        return
    await state.clear()
    await message.answer(get_text('admin_welcome', user_language),
        reply_markup=await admin_navigation.admin_menu(user_language), parse_mode='Markdown')


@router.message(StateFilter('*'), F.text.func(lambda text: reply.is_access_button(text, 'btn_access_pending')))
async def open_pending_requests(message, state, user_language: str):
    if not await allowed_message(message, user_language):
        return
    await state.clear()
    await show_pending(message, user_language)


@router.message(StateFilter('*'), Command('pending', 'approve', 'reject'))
async def admission_command(message, user_language: str):
    from src.database.connection import AsyncSessionLocal
    if not await allowed_message(message, user_language):
        return
    words = (message.text or '').split()
    command = words[0].split('@')[0] if words else ''
    try:
        if command == '/pending':
            if len(words) > 2:
                raise ValueError()
            page = int(words[1]) if len(words) == 2 else 1
            if not 1 <= page <= 1000000:
                raise ValueError()
            await show_pending(message, user_language, page)
            return
        if command not in ('/approve', '/reject') or len(words) != 2:
            raise ValueError()
        user_id = int(words[1])
        if not 0 < user_id < 2**63:
            raise ValueError()
    except ValueError:
        await message.answer(get_text('access_usage', user_language))
        return
    approve = command == '/approve'
    async with AsyncSessionLocal() as db:
        # Recheck authority in the transaction that writes the decision.
        if not await is_admin(db, message.from_user.id):
            await message.answer(get_text('admin_only', user_language))
            return
        changed = await access.decide_access(db, user_id, message.from_user.id, approve)
        record = await db.get(UserAccess, user_id)
    if not changed:
        await message.answer(get_text('access_not_pending', user_language))
        return
    await message.answer(decision_text(user_id, user_language, approve), parse_mode=None,
        reply_markup=await admin_navigation.access_menu(user_language))
    if not await notify_decision(message.bot, user_id, record.language, approve):
        await message.answer(get_text('access_notify_failed', user_language))


@router.callback_query(StateFilter('*'), F.data.startswith('accessreq:'))
async def admission_callback(callback, state, user_language: str):
    from src.database.connection import AsyncSessionLocal
    actor_id = callback.from_user.id
    message = callback.message
    async with AsyncSessionLocal() as db:
        if not await is_admin(db, actor_id):
            await callback.answer(get_text('admin_only', user_language), show_alert=True)
            return
        if (message is None or not callable(getattr(message, 'edit_text', None))
                or message.chat.type != 'private' or message.chat.id != actor_id):
            await callback.answer(get_text('access_usage', user_language), show_alert=True)
            return
        parts = (callback.data or '').split(':')
        try:
            if parts[0] != 'accessreq':
                raise ValueError()
            if parts == ['accessreq', 'menu']:
                action, page = 'menu', 1
            elif len(parts) == 3 and parts[1] == 'page':
                action, page = 'page', int(parts[2])
            elif len(parts) == 4 and parts[1] in ('approve', 'reject'):
                action, user_id, page = parts[1], int(parts[2]), int(parts[3])
                if not 0 < user_id < 2**63:
                    raise ValueError()
            else:
                raise ValueError()
            if not 1 <= page <= 1000000:
                raise ValueError()
        except ValueError:
            await callback.answer(get_text('ux_invalid', user_language), show_alert=True)
            return
        if action in ('approve', 'reject'):
            approve = action == 'approve'
            changed = await access.decide_access(db, user_id, actor_id, approve)
            record = await db.get(UserAccess, user_id)
    if action == 'menu':
        await callback.answer()
        await state.clear()
        await try_edit(message.edit_reply_markup, reply_markup=None)
        await message.answer(get_text('access_menu_title', user_language),
            reply_markup=await admin_navigation.access_menu(user_language), parse_mode=None)
        return
    if action == 'page':
        await callback.answer()
    else:
        await callback.answer(decision_text(user_id, user_language, approve) if changed
            else get_text('access_not_pending', user_language))
        if changed:
            await message.answer(decision_text(user_id, user_language, approve), parse_mode=None,
                reply_markup=await admin_navigation.access_menu(user_language))
            if not await notify_decision(callback.bot, user_id, record.language, approve):
                await message.answer(get_text('access_notify_failed', user_language))
    await show_pending(message, user_language, page, edit=True)

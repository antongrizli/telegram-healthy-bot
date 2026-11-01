from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiohttp import web
from sqlalchemy import select, func

from src.config import settings
from src.database import access, crud
from src.database.models import UserAccess, User
from src.handlers.admission import admission_command
from src.middlewares.approval import ApprovalMiddleware
from src.middlewares.registration_check import RegistrationCheckMiddleware
from src.utils.i18n_locales import LOCALES, get_text
from src.webapp.server import require_active_user
from tests.integration.test_registration_check_middleware import make_mock_message, make_mock_callback_query
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


def context(message, user=None):
    return {'db_user': user, 'event_from_user': message.from_user, 'user_language': 'ru',
            'bot': message.bot, 'state': SimpleNamespace(clear=AsyncMock(), get_state=AsyncMock(return_value=None))}


@pytest.mark.parametrize('text', ['/start', 'photo', '/approve 456'])
async def test_new_user_cannot_start_or_use_bot_and_request_is_durable(db_session, monkeypatch, text):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    message = make_mock_message(text, 12345)
    data = context(message)
    handler = AsyncMock()
    await ApprovalMiddleware()(handler, message, data)
    await ApprovalMiddleware()(handler, message, data)
    handler.assert_not_awaited()
    data['state'].clear.assert_awaited()
    assert await db_session.scalar(select(func.count()).select_from(UserAccess)) == 1
    assert await db_session.get(User, 12345) is None
    assert (await db_session.get(UserAccess, 12345)).status == 'pending'
    message.bot.send_message.assert_awaited_once()
    assert message.bot.send_message.call_args.args[0] == 999
    assert '/approve 12345' in message.bot.send_message.call_args.args[1]
    assert message.answer.call_args.args[0] == get_text('access_pending', 'ru')


async def test_pending_callback_checks_clicker_not_bot_author(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    callback = make_mock_callback_query('old button', 222)
    callback.bot = SimpleNamespace(send_message=AsyncMock())
    callback.message.from_user = SimpleNamespace(id=999, is_bot=True)
    data = context(callback)
    handler = AsyncMock()
    await ApprovalMiddleware()(handler, callback, data)
    handler.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs['show_alert'] is True
    assert await db_session.get(UserAccess, 222) is not None
    assert await db_session.get(UserAccess, 999) is None


async def test_admin_approves_then_user_can_start_setup_after_restart(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    new = make_mock_message('/start', 12345)
    await ApprovalMiddleware()(AsyncMock(), new, context(new))
    admin = make_mock_message('/approve 12345', 999)
    await admission_command(admin, 'ru')
    record = await db_session.get(UserAccess, 12345)
    assert record.status == 'approved' and record.decided_by == 999
    assert record.decided_at is not None
    admin.bot.send_message.assert_awaited_once_with(12345, get_text('access_approved', 'ru'), parse_mode=None)
    handler = AsyncMock(return_value='setup')
    data = context(new)
    async def registration(event, args):
        return await RegistrationCheckMiddleware()(handler, event, args)
    assert await ApprovalMiddleware()(registration, new, data) == 'setup'
    handler.assert_awaited_once()


async def test_pending_setup_state_cannot_bypass_approval(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    message = make_mock_message('John', 12345)
    data = context(message)
    data['state'].get_state.return_value = 'ProfileStatesGroup:name'
    handler = AsyncMock()
    await ApprovalMiddleware()(handler, message, data)
    handler.assert_not_awaited()
    data['state'].clear.assert_awaited_once()


async def test_rejection_is_persistent_and_does_not_resubmit(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    message = make_mock_message('/start', 12345)
    await ApprovalMiddleware()(AsyncMock(), message, context(message))
    await admission_command(make_mock_message('/reject 12345', 999), 'ru')
    message.bot.send_message.reset_mock()
    message.answer.reset_mock()
    await ApprovalMiddleware()(AsyncMock(), message, context(message))
    assert message.answer.call_args.args[0] == get_text('access_rejected', 'ru')
    message.bot.send_message.assert_not_awaited()
    assert not await access.decide_access(db_session, 12345, 999, True)


async def test_existing_users_keep_access_but_admin_block_still_wins(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [123])
    user = await make_user(db_session)
    message = make_mock_message('food', 123)
    handler = AsyncMock(return_value='allowed')
    assert await ApprovalMiddleware()(handler, message, context(message, user)) == 'allowed'
    user.is_blocked = True
    await db_session.commit()
    handler.reset_mock()
    await ApprovalMiddleware()(handler, message, context(message, user))
    handler.assert_not_awaited()
    assert await db_session.get(UserAccess, 123) is None


async def test_non_admin_cannot_approve_and_private_chat_required(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    new = make_mock_message('/start', 12345)
    await access.request_access(db_session, new.from_user, 'ru')
    command = make_mock_message('/approve 12345', 888)
    await admission_command(command, 'ru')
    assert (await db_session.get(UserAccess, 12345)).status == 'pending'
    assert command.answer.call_args.args[0] == get_text('admin_only', 'ru')
    command = make_mock_message('/approve 12345', 999)
    command.chat = SimpleNamespace(type='group')
    await admission_command(command, 'ru')
    assert (await db_session.get(UserAccess, 12345)).status == 'pending'


async def test_configured_admin_without_profile_can_process_command(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    message = make_mock_message('/pending', 999)
    data = context(message)
    handler = AsyncMock(return_value='admin')
    async def registration(event, args):
        return await RegistrationCheckMiddleware()(handler, event, args)
    assert await ApprovalMiddleware()(registration, message, data) == 'admin'
    assert await db_session.get(UserAccess, 999) is None


async def test_webapp_denies_pending_or_rejected_even_with_profile(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    user = await make_user(db_session)
    db_session.add(UserAccess(telegram_id=123, display_name='Test', language='en', status='pending'))
    await db_session.commit()
    with pytest.raises(web.HTTPForbidden):
        await require_active_user(123, db_session)
    assert await access.decide_access(db_session, 123, 999, True)
    assert await require_active_user(123, db_session) is user


async def test_duplicate_decision_does_not_notify_twice(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    new = make_mock_message('/start', 12345)
    await access.request_access(db_session, new.from_user, 'en')
    command = make_mock_message('/approve 12345', 999)
    await admission_command(command, 'en')
    await admission_command(command, 'en')
    command.bot.send_message.assert_awaited_once()


async def test_notification_failure_does_not_undo_approval(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    new = make_mock_message('/start', 12345)
    await access.request_access(db_session, new.from_user, 'en')
    command = make_mock_message('/approve 12345', 999)
    command.bot.send_message.side_effect = TelegramForbiddenError(method=SendMessage(chat_id=12345, text='x'), message='blocked')
    await admission_command(command, 'en')
    assert (await db_session.get(UserAccess, 12345)).status == 'approved'
    assert command.answer.call_args.args[0] == get_text('access_notify_failed', 'en')


async def test_pending_list_escapes_names_and_locales_are_complete(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    db_session.add(UserAccess(telegram_id=12345, display_name='<b>A&B</b>', language='ru', status='pending'))
    await db_session.commit()
    command = make_mock_message('/pending', 999)
    await admission_command(command, 'en')
    assert '&lt;b&gt;A&amp;B&lt;/b&gt;' in command.answer.call_args.args[0]
    assert command.answer.call_args.kwargs['parse_mode'] == 'HTML'
    for lang in LOCALES:
        for key in (key for key in LOCALES['en'] if key.startswith('access_')):
            assert key in LOCALES[lang]


async def test_failed_admin_notification_keeps_request_in_pending_list(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    message = make_mock_message('/start', 12345)
    message.bot.send_message.side_effect = TelegramForbiddenError(
        method=SendMessage(chat_id=999, text='x'), message='blocked')
    handler = AsyncMock()
    await ApprovalMiddleware()(handler, message, context(message))
    handler.assert_not_awaited()
    assert [row.telegram_id for row in await access.pending_access(db_session)] == [12345]
    message.answer.assert_awaited_once()


async def test_db_administrator_receives_and_can_approve_requests(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    admin = await make_user(db_session)
    admin.is_admin = True
    await db_session.commit()
    message = make_mock_message('/start', 12345)
    await ApprovalMiddleware()(AsyncMock(), message, context(message))
    assert message.bot.send_message.call_args.args[0] == 123
    command = make_mock_message('/approve 12345', 123)
    await admission_command(command, 'en')
    assert (await db_session.get(UserAccess, 12345)).status == 'approved'


async def test_pending_cannot_execute_existing_ai_queue_item(db_session, monkeypatch):
    from aiogram.fsm.storage.memory import MemoryStorage
    from src.database.models import AiRequestQueue
    from src.services.rate_limiter import execute_queued_item
    from src.services import gemini
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    await make_user(db_session)
    db_session.add(UserAccess(telegram_id=123, display_name='Test', language='en', status='pending'))
    await db_session.commit()
    analyze = AsyncMock()
    monkeypatch.setattr(gemini, 'analyze_food_input', analyze)
    item = AiRequestQueue(id=100, user_id=123, chat_id=123, request_type='analyze_food_input', payload={'text_description': 'egg'})
    assert not await execute_queued_item(SimpleNamespace(id=1), MemoryStorage(), db_session, item)
    analyze.assert_not_awaited()


async def test_pending_pages_are_stable_and_decisions_remove_entries(db_session):
    for index in range(12):
        db_session.add(UserAccess(telegram_id=1000 + index, display_name='Test', language='en', status='pending'))
    await db_session.commit()
    assert len(await access.pending_access(db_session, 1)) == 10
    assert len(await access.pending_access(db_session, 2)) == 2
    assert await access.decide_access(db_session, 1000, 999, False)
    assert 1000 not in [row.telegram_id for row in await access.pending_access(db_session, 1)]


async def test_auto_approval_defaults_off_approves_pending_and_keeps_rejected(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    assert not await access.auto_approval_enabled(db_session)
    pending = make_mock_message('/start', 8001)
    rejected = make_mock_message('/start', 8002)
    await access.request_access(db_session, pending.from_user, 'ru')
    await access.request_access(db_session, rejected.from_user, 'ru')
    await access.decide_access(db_session, 8002, 999, False)
    await access.set_auto_approval(db_session, True)
    assert await access.auto_approval_enabled(db_session)
    assert await access.has_access(db_session, 8001)
    for message in (rejected,):
        handler = AsyncMock()
        await ApprovalMiddleware()(handler, message, context(message))
        handler.assert_not_awaited()
    new = make_mock_message('/start', 8003)
    handler = AsyncMock()
    data = context(new)
    await ApprovalMiddleware()(handler, new, data)
    handler.assert_awaited_once_with(new, data)
    new.bot.send_message.assert_not_awaited()
    record = await db_session.get(UserAccess, 8003)
    assert record.status == 'approved' and record.decided_at is not None
    assert record.decided_by is None
    await access.set_auto_approval(db_session, False)
    assert await access.has_access(db_session, 8003)
    record, created = await access.request_access(db_session, make_mock_message('/start', 8004).from_user, 'ru')
    assert created and record.status == 'pending'

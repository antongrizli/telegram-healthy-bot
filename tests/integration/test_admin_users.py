from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update, CallbackQuery, Chat, User as TelegramUser
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import settings
from src.database import crud
from src.database.models import MessageStat, ProductEvent
from src.handlers import admin
from src.keyboards.inline import get_admin_users_inline
from src.middlewares.admin_check import AdminCheckMiddleware
from src.utils.i18n_locales import LOCALES
from tests.integration.test_security_and_meal_recovery import make_user


@pytest.mark.asyncio
async def test_users_page_has_latest_activity_pagination_and_escaped_table(db_session):
    now = datetime.now(UTC).replace(tzinfo=None)
    user = await make_user(db_session, username='anna', created_at=now)
    user.name = '<b>\nАнна</b>'
    await make_user(db_session, 456, created_at=now - timedelta(days=1))
    await make_user(db_session, 789, is_blocked=True)
    db_session.add_all([
        MessageStat(user_id=user.telegram_id, message_type='callback_query', sent_at=now - timedelta(hours=1)),
        ProductEvent(user_id=user.telegram_id, name='active', occurred_at=now),
    ])
    await db_session.commit()
    data = await crud.get_admin_users_page(db_session, page_size=1)
    assert data['total'] == 2 and data['pages'] == 2
    assert data['users'][0]['last_active_at'] == now
    second = await crud.get_admin_users_page(db_session, page=99, page_size=1)
    assert second['page'] == 1 and second['users'][0]['telegram_id'] == 456
    assert second['users'][0]['last_active_at'] is None
    assert (await crud.get_admin_users_page(db_session, blocked=True))['total'] == 1
    for lang in LOCALES:
        text = admin.format_users_table(data, lang)
        assert '&lt;b&gt;' in text and '@anna' in text and 'UTC' in text
        assert '<b>Анна</b>' not in text and len(text) < 4096
        keyboard = get_admin_users_inline(data, lang, actor_id=42)
        assert keyboard.inline_keyboard[0][0].callback_data == 'adminusers:block:123:0'
    db_session.add(MessageStat(user_id=123, message_type='text', sent_at=now + timedelta(minutes=1)))
    await db_session.commit()
    assert (await crud.get_admin_users_page(db_session))['users'][0]['last_active_at'] == now + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_admin_users_callbacks_authorization_block_unblock_and_stale_page(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    actor = await make_user(db_session, 42)
    actor.is_admin = True
    await db_session.commit()
    target = await make_user(db_session, 123)
    await make_user(db_session, 456)
    dp = Dispatcher(storage=MemoryStorage())
    parent = admin.router.parent_router
    admin.router._parent_router = None
    dp.include_router(admin.router)
    async def context(handler, event, data):
        data.update(db_user=await crud.get_user(db_session, event.from_user.id), user_language='ru')
        return await handler(event, data)
    dp.callback_query.outer_middleware(context)
    dp.callback_query.outer_middleware(AdminCheckMiddleware())
    bot = Bot('123456:test')
    bot.session = AsyncMock()
    async def click(payload, uid=42, chat_id=42):
        bot.session.reset_mock()
        msg = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=chat_id, type='private'),
                      from_user=TelegramUser(id=999, is_bot=True, first_name='Bot'), text='Users')
        event = CallbackQuery(id='cb', from_user=TelegramUser(id=uid, is_bot=False, first_name='Test'),
                              chat_instance='test', message=msg, data=payload)
        await dp.feed_update(bot, Update(update_id=1, callback_query=event))
    try:
        await click('adminusers:block:123:0', uid=456)
        assert target.is_blocked is False
        await click('adminusers:block:123:0', chat_id=456)
        assert target.is_blocked is False
        for payload in ['adminusers:block:42:0', 'adminusers:block:123:-1', 'adminusers:block:nope:0', 'adminusers:page:nope:0']:
            await click(payload)
        assert actor.is_blocked is False and target.is_blocked is False
        await click('adminusers:block:123:0')
        assert target.is_blocked is True
        assert any(call.args[1].__api_method__ == 'editMessageText' for call in bot.session.call_args_list)
        await click('adminusers:block:123:0')
        assert target.is_blocked is True  # Repeated stale button does not toggle.
        await click('adminusers:page:blocked:999')
        await click('adminusers:unblock:123:0')
        assert target.is_blocked is False
        await click('adminusers:back')
        assert any(call.args[1].__api_method__ == 'sendMessage' for call in bot.session.call_args_list)
    finally:
        admin.router._parent_router = parent
        await dp.storage.close()

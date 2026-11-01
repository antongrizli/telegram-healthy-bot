from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import settings
from src.database.models import UserAccess
from src.handlers.admission import (
    admission_callback, back_to_admin, open_access_menu, open_pending_requests,
)
from src.keyboards import inline, reply
from src.middlewares.registration_check import RegistrationCheckMiddleware
from src.utils.i18n_locales import LOCALES, get_text
from tests.integration.test_admission import context
from tests.integration.test_registration_check_middleware import make_mock_message, make_mock_callback_query
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


def callback_for(data, actor_id=999, chat_id=None, chat_type='private'):
    message = SimpleNamespace(chat=SimpleNamespace(id=chat_id or actor_id, type=chat_type),
        edit_text=AsyncMock(), edit_reply_markup=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=actor_id), message=message,
        answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()))


def state():
    return SimpleNamespace(clear=AsyncMock())


async def seed_requests(db, count=1):
    for index in range(count):
        db.add(UserAccess(telegram_id=1000 + index, display_name=f'Person {index}', language='ru', status='pending'))
    await db.commit()


@pytest.mark.parametrize('lang', list(LOCALES))
async def test_access_reply_layout_and_all_localized_labels(lang):
    admin_keyboard = reply.get_admin_menu(lang)
    assert [b.text for b in admin_keyboard.keyboard[2]] == [reply.access_count_label('btn_access_menu', lang, 0)]
    assert len(admin_keyboard.keyboard[1]) == 2  # Active / Blocked users remain neighbors.
    submenu = reply.get_access_menu(lang)
    assert submenu.resize_keyboard
    assert [[b.text for b in row] for row in submenu.keyboard] == [
        [reply.access_count_label('btn_access_pending', lang, 0)], [get_text('btn_access_back', lang)]]
    for key in ('btn_access_menu', 'btn_access_pending', 'btn_access_back',
                'btn_access_approve', 'btn_access_reject', 'access_list_back'):
        assert key in LOCALES[lang]


@pytest.mark.parametrize('approve', [True, False])
async def test_inline_decision_updates_list_and_notifies_only_once(db_session, monkeypatch, approve):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session)
    action = 'approve' if approve else 'reject'
    cb = callback_for(f'accessreq:{action}:1000:1')
    await admission_callback(cb, state(), 'ru')
    record = await db_session.get(UserAccess, 1000)
    assert record.status == ('approved' if approve else 'rejected')
    assert record.decided_by == 999
    cb.bot.send_message.assert_awaited_once_with(1000, get_text(
        'access_approved' if approve else 'access_rejected', 'ru'), parse_mode=None)
    assert cb.message.edit_text.call_args.kwargs['parse_mode'] == 'HTML'
    assert 'Person 0' not in cb.message.edit_text.call_args.args[0]
    await admission_callback(cb, state(), 'ru')
    assert cb.bot.send_message.await_count == 1
    assert cb.answer.call_args.args[0] == get_text('access_not_pending', 'ru')


async def test_last_decision_on_final_page_clamps_page(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session, 6)
    cb = callback_for('accessreq:approve:1005:2')
    await admission_callback(cb, state(), 'ru')
    keyboard = cb.message.edit_text.call_args.kwargs['reply_markup']
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert 'accessreq:page:1' in data
    assert 'accessreq:page:2' not in data
    assert not any(':1005:' in value for value in data)


@pytest.mark.parametrize('data', [
    'accessreq:approve:bad:1', 'accessreq:approve:1000:0',
    'accessreq:approve:-1:1', 'accessreq:approve:9223372036854775808:1',
    'accessreq:page:bad', 'accessreq:page:1000001', 'accessreq:unknown',
    'accessreq:approve:1000:1:extra', 'other:approve:1000:1',
])
async def test_malformed_callbacks_cannot_mutate(db_session, monkeypatch, data):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session)
    cb = callback_for(data)
    await admission_callback(cb, state(), 'ru')
    assert (await db_session.get(UserAccess, 1000)).status == 'pending'
    assert cb.answer.call_args.kwargs['show_alert']
    cb.bot.send_message.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()


@pytest.mark.parametrize('actor_id,chat_id,chat_type', [(888, 888, 'private'), (999, 888, 'private'), (999, 999, 'group')])
async def test_inline_admin_and_private_chat_checks(db_session, monkeypatch, actor_id, chat_id, chat_type):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session)
    cb = callback_for('accessreq:approve:1000:1', actor_id, chat_id, chat_type)
    await admission_callback(cb, state(), 'ru')
    assert (await db_session.get(UserAccess, 1000)).status == 'pending'
    cb.bot.send_message.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()


async def test_old_buttons_recheck_revoked_db_admin_rights(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    admin = await make_user(db_session)
    admin.is_admin = True
    await db_session.commit()
    await seed_requests(db_session)
    cb = callback_for('accessreq:page:1', actor_id=123)
    await admission_callback(cb, state(), 'ru')
    cb.message.edit_text.assert_awaited_once()
    admin.is_admin = False
    await db_session.commit()
    cb.data = 'accessreq:approve:1000:1'
    await admission_callback(cb, state(), 'ru')
    assert (await db_session.get(UserAccess, 1000)).status == 'pending'
    assert cb.answer.call_args.args[0] == get_text('admin_only', 'ru')


async def test_reply_menu_open_list_and_back(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    message = make_mock_message(get_text('btn_access_menu', 'ru'), 999)
    navigation = state()
    await open_access_menu(message, navigation, 'ru')
    assert message.answer.call_args.kwargs['reply_markup'] == reply.get_access_menu('ru')
    await seed_requests(db_session)
    await open_pending_requests(message, navigation, 'ru')
    markup = message.answer.call_args.kwargs['reply_markup']
    assert markup.inline_keyboard[0][0].callback_data == 'accessreq:approve:1000:1'
    assert markup.inline_keyboard[0][1].callback_data == 'accessreq:reject:1000:1'
    await back_to_admin(message, navigation, 'ru')
    assert message.answer.call_args.kwargs['reply_markup'] == reply.get_admin_menu('ru', 1)
    assert navigation.clear.await_count == 3


async def test_inline_back_returns_reply_submenu(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    cb = callback_for('accessreq:menu')
    navigation = state()
    await admission_callback(cb, navigation, 'ru')
    cb.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert cb.message.answer.call_args.kwargs['reply_markup'] == reply.get_access_menu('ru')
    navigation.clear.assert_awaited_once()


async def test_non_admin_cannot_open_submenu(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [])
    message = make_mock_message(get_text('btn_access_menu', 'ru'), 888)
    navigation = state()
    await open_access_menu(message, navigation, 'ru')
    assert message.answer.call_args.args[0] == get_text('admin_only', 'ru')
    assert 'reply_markup' not in message.answer.call_args.kwargs
    navigation.clear.assert_not_awaited()


@pytest.mark.parametrize('lang', list(LOCALES))
async def test_admin_without_profile_can_use_new_navigation(monkeypatch, lang):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    for key in ('btn_access_menu', 'btn_access_pending', 'btn_access_back'):
        message = make_mock_message(get_text(key, lang), 999)
        handler = AsyncMock(return_value='allowed')
        assert await RegistrationCheckMiddleware()(handler, message, context(message)) == 'allowed'
    cb = make_mock_callback_query('request', 999)
    cb.data = 'accessreq:page:1'
    cb.bot = SimpleNamespace()
    assert await RegistrationCheckMiddleware()(AsyncMock(return_value='allowed'), cb, context(cb)) == 'allowed'


async def test_inline_names_are_bounded_and_callback_data_fits_telegram():
    request = SimpleNamespace(telegram_id=2**63 - 1, display_name='A' * 128)
    keyboard = inline.get_access_requests_inline(dict(requests=[request], page=1000000, pages=1000000, total=1), 'ru')
    for row in keyboard.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
    assert 'A' * 25 not in keyboard.inline_keyboard[0][0].text


@pytest.mark.parametrize('reason, replacement', [('message is not modified', False), ('message to edit not found', True)])
async def test_pagination_handles_unchanged_and_deleted_messages(db_session, monkeypatch, reason, replacement):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session)
    cb = callback_for('accessreq:page:1')
    cb.message.edit_text.side_effect = TelegramBadRequest(
        method=EditMessageText(chat_id=999, message_id=1, text='x'), message=reason)
    await admission_callback(cb, state(), 'ru')
    assert cb.message.answer.await_count == int(replacement)
    if replacement:
        assert cb.message.answer.call_args.kwargs['parse_mode'] == 'HTML'


async def test_inaccessible_callback_message_does_not_mutate(db_session, monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session)
    cb = callback_for('accessreq:approve:1000:1')
    cb.message = SimpleNamespace(chat=SimpleNamespace(id=999, type='private'))
    await admission_callback(cb, state(), 'ru')
    assert (await db_session.get(UserAccess, 1000)).status == 'pending'
    assert cb.answer.call_args.kwargs['show_alert']
    cb.bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('lang', list(LOCALES))
async def test_counted_labels_remain_routable_and_old_labels_work(lang):
    for key in ('btn_access_menu', 'btn_access_pending'):
        assert reply.is_access_button(get_text(key, lang), key)
        for count in (0, 1, 23):
            assert reply.is_access_button(reply.access_count_label(key, lang, count), key)
        assert not reply.is_access_button(get_text(key, lang) + ' (bad)', key)
        assert not reply.is_access_button(get_text(key, lang) + ' (-1)', key)


async def test_menu_counters_count_all_pages_and_refresh_after_decision(db_session, monkeypatch):
    from src.services import admin_navigation
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    await seed_requests(db_session, 7)
    db_session.add(UserAccess(telegram_id=2000, display_name='Approved', language='en', status='approved'))
    db_session.add(UserAccess(telegram_id=2001, display_name='Rejected', language='en', status='rejected'))
    await db_session.commit()
    admin_menu = await admin_navigation.admin_menu('ru')
    assert admin_menu.keyboard[2][0].text.endswith('(7)')
    access_menu = await admin_navigation.access_menu('ru')
    assert access_menu.keyboard[0][0].text.endswith('(7)')
    cb = callback_for('accessreq:approve:1000:1')
    await admission_callback(cb, state(), 'ru')
    menu = cb.message.answer.call_args.kwargs['reply_markup']
    assert menu.keyboard[0][0].text.endswith('(6)')
    assert (await admin_navigation.admin_menu('ru')).keyboard[2][0].text.endswith('(6)')


async def test_new_user_notifies_all_admins_once_with_live_count(db_session, monkeypatch):
    from src.middlewares.approval import ApprovalMiddleware
    from tests.integration.test_admission import context
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [998, 999])
    first = make_mock_message('/start', 12345)
    await ApprovalMiddleware()(AsyncMock(), first, context(first))
    assert first.bot.send_message.await_count == 2
    for call in first.bot.send_message.call_args_list:
        button = call.kwargs['reply_markup'].inline_keyboard[0][0]
        assert button.text.endswith('(1)')
        assert button.callback_data == 'accessreq:page:1'
    await ApprovalMiddleware()(AsyncMock(), first, context(first))
    assert first.bot.send_message.await_count == 2
    second = make_mock_message('/start', 12346)
    await ApprovalMiddleware()(AsyncMock(), second, context(second))
    assert second.bot.send_message.await_count == 2
    assert second.bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].text.endswith('(2)')


async def test_counted_buttons_work_for_admin_without_profile(monkeypatch):
    monkeypatch.setattr(settings, 'ADMIN_USER_IDS', [999])
    for key in ('btn_access_menu', 'btn_access_pending'):
        message = make_mock_message(reply.access_count_label(key, 'ru', 23), 999)
        assert await RegistrationCheckMiddleware()(AsyncMock(return_value='allowed'), message, context(message)) == 'allowed'

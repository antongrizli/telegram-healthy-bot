from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from src.database import crud
from src.handlers import food
from src.services import gemini, rate_limiter
from src.services.meal_cards import deliver_card
from src.utils.telegram_edit import try_edit
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS

pytestmark = pytest.mark.asyncio


def error(cls, message):
    return cls(method=EditMessageText(chat_id=123, message_id=777, text='Test'), message=message)


@pytest.mark.parametrize('reason, expected', [('message is not modified', True), ('message to edit not found', False)])
async def test_edit_classifies_confirmed_telegram_errors(reason, expected):
    assert await try_edit(AsyncMock(side_effect=error(TelegramBadRequest, reason))) is expected


async def test_network_edit_never_falls_back(db_session):
    await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {'analysis': ANALYSIS, 'card_message_id': 777})
    draft = await crud.get_meal_draft(db_session, did, 123)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=error(TelegramNetworkError, 'timeout')),
                          send_message=AsyncMock())
    with pytest.raises(TelegramNetworkError): await deliver_card(bot, db_session, draft, 123, 'ru')
    bot.send_message.assert_not_awaited()


async def test_callback_replacement_uses_draft_owner_and_persists_id(db_session):
    await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {'analysis': ANALYSIS, 'card_message_id': 777})
    message = SimpleNamespace(message_id=777, from_user=SimpleNamespace(id=999),
        edit_text=AsyncMock(side_effect=error(TelegramBadRequest, 'message to edit not found')),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=888)))
    cb = SimpleNamespace(data=f'uxdraft:view:{did}:1', from_user=SimpleNamespace(id=123),
                         message=message, answer=AsyncMock())
    await food.uxdraft_view_card(cb, 'ru')
    await db_session.refresh(await crud.get_meal_draft(db_session, did, 123))
    assert (await crud.get_meal_draft(db_session, did, 123)).payload['card_message_id'] == 888


async def test_unchanged_meal_type_does_not_duplicate_card(db_session):
    await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {'analysis': ANALYSIS, 'card_message_id': 777,
                                                     'meal_type': 'breakfast'})
    message = SimpleNamespace(message_id=777,
        edit_text=AsyncMock(side_effect=error(TelegramBadRequest, 'message is not modified')),
        answer=AsyncMock())
    cb = SimpleNamespace(data=f'uxdraft:type:{did}:breakfast', from_user=SimpleNamespace(id=123),
                         message=message, answer=AsyncMock())
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=123, user_id=123))
    await food.quick_correction(cb, state, 'ru')
    message.answer.assert_not_awaited()


async def test_deleted_card_replacement_is_reused_after_reload(db_session):
    await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {'analysis': ANALYSIS, 'card_message_id': 777})
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=[
        error(TelegramBadRequest, 'message to edit not found'), None]),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=888)))
    draft = await crud.get_meal_draft(db_session, did, 123)
    await deliver_card(bot, db_session, draft, 123, 'ru')
    await db_session.refresh(draft)
    assert draft.payload['card_message_id'] == 888
    await deliver_card(bot, db_session, draft, 123, 'ru')
    assert bot.edit_message_text.call_args.kwargs['message_id'] == 888
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize('req_type', ['adjust_food_analysis', 'adjust_meal_edit'])
async def test_legacy_queue_reply_keyboard_uses_send_message_contract(db_session, monkeypatch, req_type):
    await make_user(db_session)
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))
    expected = (food.FoodLoggingState.waiting_for_correction if req_type == 'adjust_food_analysis'
                else food.MealEditingState.waiting_for_edit_text)
    await state.set_state(expected)
    async def edit(**kwargs):
        EditMessageText(**kwargs)  # Real aiogram validation rejects ReplyKeyboardMarkup.
    async def send(chat_id, text, **kwargs):
        SendMessage(chat_id=chat_id, text=text, **kwargs)
        return SimpleNamespace(message_id=901)
    bot = SimpleNamespace(id=1, edit_message_text=AsyncMock(side_effect=edit),
        send_message=AsyncMock(side_effect=send), delete_message=AsyncMock())
    monkeypatch.setattr(gemini, 'adjust_food_analysis', AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS)))
    from src.database.models import AiRequestQueue
    qid = await rate_limiter.add_to_queue(db_session, 123, 123, req_type,
        {'original_data': ANALYSIS, 'correction_text': 'two apples', 'status_message_id': 900,
         'edit_meal_id': 1, 'edit_date_str': '2026-10-03'})
    assert await rate_limiter.execute_queued_item(bot, storage, db_session, await db_session.get(AiRequestQueue, qid))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.call_args.kwargs.get('reply_markup') is not None
    for call in bot.edit_message_text.call_args_list:
        assert call.kwargs.get('reply_markup') is None
    bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=900)


@pytest.mark.parametrize('req_type', ['adjust_food_analysis', 'adjust_meal_edit'])
async def test_out_of_flow_edit_timeout_does_not_send_duplicate(db_session, monkeypatch, req_type):
    await make_user(db_session)
    monkeypatch.setattr(gemini, 'adjust_food_analysis', AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS)))
    async def edit(**kwargs):
        # Initial analyzing status succeeds; final result delivery is uncertain.
        if kwargs['text'].startswith('ℹ️'):
            raise error(TelegramNetworkError, 'timeout')
    bot = SimpleNamespace(id=1, edit_message_text=AsyncMock(side_effect=edit), send_message=AsyncMock())
    from src.database.models import AiRequestQueue
    qid = await rate_limiter.add_to_queue(db_session, 123, 123, req_type,
        {'original_data': ANALYSIS, 'correction_text': 'two apples', 'status_message_id': 900})
    with pytest.raises(TelegramNetworkError):
        await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, await db_session.get(AiRequestQueue, qid))
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize('queued', [False, True])
async def test_ai_correction_updates_original_card(db_session, monkeypatch, queued):
    await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {'analysis': ANALYSIS, 'card_message_id': 777,
        'meal_type': 'breakfast', 'logged_at': datetime.now(UTC).isoformat()})
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))
    await state.set_state(food.FoodLoggingState.waiting_for_correction)
    await state.update_data(analysis=ANALYSIS, draft_id=did, correction_action='add')
    bot = SimpleNamespace(id=1, edit_message_text=AsyncMock(), delete_message=AsyncMock(), send_message=AsyncMock())
    monkeypatch.setattr(gemini, 'adjust_food_analysis', AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS)))
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    if queued:
        from src.database.models import AiRequestQueue
        qid = await rate_limiter.add_to_queue(db_session, 123, 123, 'adjust_food_analysis',
            {'draft_id': did, 'original_data': ANALYSIS, 'correction_text': 'add apple', 'status_message_id': 900})
        assert await rate_limiter.execute_queued_item(bot, storage, db_session, await db_session.get(AiRequestQueue, qid))
    else:
        status = SimpleNamespace(message_id=900, edit_text=AsyncMock())
        message = SimpleNamespace(text='apple', from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123),
                                  bot=bot, answer=AsyncMock(return_value=status))
        await food.process_food_correction(message, state, 'ru')
    assert bot.edit_message_text.call_args.kwargs['message_id'] == 777
    bot.send_message.assert_not_awaited()
    bot.delete_message.assert_awaited_once_with(chat_id=123, message_id=900)
    assert (await crud.get_meal_draft(db_session, did, 123)).payload['card_message_id'] == 777

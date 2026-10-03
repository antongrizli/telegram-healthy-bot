from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, UTC
import pytest
from sqlalchemy import select
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from src.database import crud
from src.handlers import food
from src.utils import i18n_locales
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['portion', 'manual', 'add'])
async def test_correction_opens_from_persisted_card(db_session, action):
    await make_user(db_session)
    draft_id = await crud.save_meal_draft(db_session, 123, {
        'analysis': ANALYSIS, 'card_message_id': 777,
        'logged_at': datetime.now(UTC).isoformat(),
    })
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=123, user_id=123))
    message = SimpleNamespace(message_id=777, answer=AsyncMock())
    callback = SimpleNamespace(data=f'uxdraft:{action}:{draft_id}',
                               from_user=SimpleNamespace(id=123),
                               message=message, answer=AsyncMock())
    await food.quick_correction(callback, state, 'ru')
    expected = food.FoodLoggingState.waiting_for_correction if action == 'add' else food.LocalCorrectionState.value
    assert await state.get_state() == expected.state
    assert (await state.get_data())['card_message_id'] == 777
    message.answer.assert_awaited_once()
    assert not callback.answer.call_args.kwargs.get('show_alert')


@pytest.mark.asyncio
async def test_meal_type_updates_card_in_place_without_new_messages(db_session):
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": ANALYSIS,
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat(),
            "card_message_id": 777
        }
    )
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))

    mock_msg = SimpleNamespace(
        message_id=777,
        text="Analysis card",
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock()
    )

    # Step 1: Click "Тип еды"
    cb_open_type = SimpleNamespace(
        data=f"uxdraft:type:{draft_id}",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_open_type, state, "ru")
    # Swapped buttons in place on the card, no new message!
    mock_msg.edit_reply_markup.assert_called_once()
    assert mock_msg.answer.call_count == 0

    # Step 2: Select "Обед" (lunch)
    cb_select_lunch = SimpleNamespace(
        data=f"uxdraft:type:{draft_id}:lunch",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_select_lunch, state, "ru")
    # Card text edited in place to show lunch, no new message!
    mock_msg.edit_text.assert_called_once()
    assert "Обед" in mock_msg.edit_text.call_args.args[0]
    assert mock_msg.answer.call_count == 0

    # DB draft payload updated with lunch
    draft = await crud.get_meal_draft(db_session, draft_id, 123)
    assert draft.payload["meal_type"] == "lunch"

    # Step 3: Back button restores default draft keyboard
    mock_msg.edit_reply_markup.reset_mock()
    cb_back = SimpleNamespace(
        data=f"uxdraft:back:{draft_id}",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_back, state, "ru")
    mock_msg.edit_reply_markup.assert_called_once()


@pytest.mark.asyncio
async def test_remove_item_updates_card_in_place(db_session):
    user = await make_user(db_session)
    multi_item_analysis = {
        "food_items": [
            {"name": "Яйцо", "portion": "2 шт", "calories": 140, "protein": 12, "fat": 10, "carb": 1},
            {"name": "Тост", "portion": "1 шт", "calories": 80, "protein": 3, "fat": 1, "carb": 15}
        ],
        "total_calories": 220,
        "total_protein": 15,
        "total_fat": 11,
        "total_carb": 16
    }
    draft_id = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": multi_item_analysis,
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat(),
            "card_message_id": 888
        }
    )
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))

    mock_msg = SimpleNamespace(
        message_id=888,
        text="Card text",
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock()
    )

    # 1. Open remove menu
    cb_open_remove = SimpleNamespace(
        data=f"uxdraft:remove:{draft_id}",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_open_remove, state, "ru")
    mock_msg.edit_reply_markup.assert_called_once()
    assert mock_msg.answer.call_count == 0

    # 2. Remove first item (index 0: "Яйцо")
    cb_do_remove = SimpleNamespace(
        data=f"uxdraft:remove:{draft_id}:0",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_do_remove, state, "ru")
    mock_msg.edit_text.assert_called_once()
    cleaned_text = mock_msg.edit_text.call_args.args[0]
    assert "Тост" in cleaned_text
    assert "Яйцо" not in cleaned_text
    assert mock_msg.answer.call_count == 0

    # 3. Trying to remove the only remaining item is refused with alert
    cb_remove_last = SimpleNamespace(
        data=f"uxdraft:remove:{draft_id}",
        from_user=SimpleNamespace(id=123),
        message=mock_msg,
        answer=AsyncMock()
    )
    await food.quick_correction(cb_remove_last, state, "ru")
    assert cb_remove_last.answer.call_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_portion_correction_updates_card_in_place_after_restart(db_session):
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": ANALYSIS,
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat(),
            "card_message_id": 999
        }
    )
    # Simulate fresh state / restart where card_message_id is restored from DB
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))

    mock_bot = SimpleNamespace(
        edit_message_text=AsyncMock()
    )
    user_msg = SimpleNamespace(
        text="2.0",
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=123),
        bot=mock_bot,
        answer=AsyncMock()
    )

    # Set up state as if portion was requested
    await state.set_state(food.LocalCorrectionState.value)
    await state.update_data(draft_id=draft_id, correction_action="portion")

    await food.local_correction(user_msg, state, "ru")

    # 1. Existing card was edited in place via bot.edit_message_text
    mock_bot.edit_message_text.assert_called_once()
    call_args = mock_bot.edit_message_text.call_args
    assert call_args.kwargs["message_id"] == 999
    assert "200" in call_args.kwargs["text"]  # Calories doubled from 100 to 200

    # 2. User received a single confirmation message, NOT a duplicate full card
    assert user_msg.answer.call_count == 1
    assert "сохранено" in user_msg.answer.call_args.args[0].lower() or "saved" in user_msg.answer.call_args.args[0].lower()

    # 3. Ownership security: foreign user cannot correct the draft
    foreign_msg = SimpleNamespace(
        text="3.0",
        from_user=SimpleNamespace(id=999),
        chat=SimpleNamespace(id=999),
        bot=mock_bot,
        answer=AsyncMock()
    )
    await food.local_correction(foreign_msg, state, "ru")
    assert "недоступен" in foreign_msg.answer.call_args.args[0].lower() or "unavailable" in foreign_msg.answer.call_args.args[0].lower()

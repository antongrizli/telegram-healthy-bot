import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, UTC
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from src.database import crud
from src.database.models import AiRequestQueue
from src.services import rate_limiter, gemini
from src.handlers.food import process_food_input, FoodLoggingState
from tests.integration.test_handlers import make_mock_message

pytestmark = pytest.mark.asyncio

MOCK_ANALYSIS = {
    "food_items": [
        {"name": "Salmon", "portion": "150g", "calories": 300, "protein": 34.0, "fat": 18.0, "carb": 0.0}
    ],
    "total_calories": 300,
    "total_protein": 34.0,
    "total_fat": 18.0,
    "total_carb": 0.0
}


async def create_user(db_session, telegram_id, language="en"):
    return await crud.create_or_update_user(
        db_session,
        telegram_id=telegram_id,
        name=f"User {telegram_id}",
        sex="male",
        age=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="light",
        goal="lose_weight",
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200,
        language=language
    )


def make_food_message(text, user_id=12345):
    msg = make_mock_message(text, user_id=user_id)
    msg.photo = None
    return msg


async def test_worker_status_survives_database_reload_and_retry(db_session, monkeypatch):
    await create_user(db_session, 55555)
    queue_id = await rate_limiter.add_to_queue(
        db_session, 55555, 55555, 'analyze_food_input',
        {'text_description': 'Salmon', 'meal_type': 'dinner',
         'logged_at': datetime.now(UTC).isoformat()},
    )
    item = await db_session.get(AiRequestQueue, queue_id)
    bot = SimpleNamespace(id=1,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        edit_message_text=AsyncMock())
    monkeypatch.setattr(gemini, 'analyze_food_input', AsyncMock(side_effect=RuntimeError('provider unavailable')))
    for attempt in range(2):
        with pytest.raises(RuntimeError, match='provider unavailable'):
            await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
        await db_session.refresh(item)
        assert item.payload['status_message_id'] == 900
    bot.send_message.assert_awaited_once()


async def test_direct_analysis_unified_status_in_place(db_session, monkeypatch):
    """Direct analysis: exactly 1 message created, morphed in-place from analyzing to draft card."""
    await create_user(db_session, telegram_id=12345, language="en")

    mock_analysis = gemini.FoodAnalysisResponse(**MOCK_ANALYSIS)
    monkeypatch.setattr(gemini, "analyze_food_input", AsyncMock(return_value=mock_analysis))

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=12345, user_id=12345))
    await state.set_state(FoodLoggingState.waiting_for_input)
    await state.update_data(meal_type="dinner")

    message = make_food_message("150g salmon", user_id=12345)
    wait_msg = MagicMock()
    wait_msg.message_id = 999
    wait_msg.edit_text = AsyncMock()
    wait_msg.delete = AsyncMock()
    message.answer = AsyncMock(return_value=wait_msg)

    await process_food_input(message, state, "en")

    # Exactly 1 message sent by the handler (the initial status)
    assert message.answer.call_count == 1
    assert "look" in message.answer.call_args[0][0].lower() or "detective" in message.answer.call_args[0][0].lower()

    # The status message was edited in-place to the draft card
    assert wait_msg.edit_text.called
    assert not wait_msg.delete.called
    card_text = wait_msg.edit_text.call_args[0][0]
    assert "Salmon" in card_text
    assert "300 kcal" in card_text

    # The draft in DB stores the status message id as card_message_id
    state_data = await state.get_data()
    draft_id = state_data["draft_id"]
    draft = await crud.get_meal_draft(db_session, draft_id, 12345)
    assert draft.payload.get("card_message_id") == 999


async def test_rate_limited_queued_unified_status(db_session, monkeypatch):
    """Rate-limited queueing: 1 message (rate_limit_queued) morphed by worker into food_analyzing, then draft card."""
    await create_user(db_session, telegram_id=22222, language="en")

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=22222, user_id=22222))
    await state.set_state(FoodLoggingState.waiting_for_input)
    await state.update_data(meal_type="lunch")

    # Simulate rate limited
    monkeypatch.setattr(rate_limiter, "check_rate_limit", AsyncMock(return_value=(True, "minute")))

    message = make_food_message("Chicken and rice", user_id=22222)
    status_msg = MagicMock()
    status_msg.message_id = 777
    status_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=status_msg)

    await process_food_input(message, state, "en")

    # Initial queued status sent
    assert message.answer.call_count == 1
    assert "queue" in message.answer.call_args[0][0].lower() or "1" in message.answer.call_args[0][0]

    # Queue item in DB should contain status_message_id == 777
    item = await rate_limiter.get_next_pending_queue_item(db_session)
    assert item is not None
    assert item.payload.get("status_message_id") == 777

    # Worker execution
    mock_analysis = gemini.FoodAnalysisResponse(**MOCK_ANALYSIS)
    analyze_mock = AsyncMock(return_value=mock_analysis)
    monkeypatch.setattr(gemini, "analyze_food_input", analyze_mock)

    bot = SimpleNamespace(
        id=1,
        send_message=AsyncMock(),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock()
    )

    success = await rate_limiter.execute_queued_item(bot, storage, db_session, item)
    assert success

    # Worker updated status_message_id to food_analyzing, then to draft card
    assert bot.edit_message_text.call_count == 2
    first_edit_text = bot.edit_message_text.call_args_list[0].kwargs.get("text") or bot.edit_message_text.call_args_list[0].args[2]
    second_edit_text = bot.edit_message_text.call_args_list[1].kwargs.get("text") or bot.edit_message_text.call_args_list[1].args[2]

    assert "analyzing" in first_edit_text.lower() or "look" in first_edit_text.lower()
    assert "Salmon" in second_edit_text
    assert "300 kcal" in second_edit_text

    # No new messages sent by worker and no deletions
    assert bot.send_message.call_count == 0
    assert bot.delete_message.call_count == 0

    # Draft saved with card_message_id == 777
    draft_id = item.payload.get("result_draft_id")
    assert draft_id is not None
    draft = await crud.get_meal_draft(db_session, draft_id, 22222)
    assert draft.payload.get("card_message_id") == 777


async def test_fallback_queueing_unified_status(db_session, monkeypatch):
    """Direct failure fallback: initial wait_msg edited to ai_service_unavailable, then worker edits to card."""
    await create_user(db_session, telegram_id=33333, language="en")

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=33333, user_id=33333))
    await state.set_state(FoodLoggingState.waiting_for_input)
    await state.update_data(meal_type="dinner")

    # Direct call fails
    monkeypatch.setattr(rate_limiter, "check_rate_limit", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(gemini, "analyze_food_input", AsyncMock(side_effect=RuntimeError("AI Timeout")))

    message = make_food_message("Steak", user_id=33333)
    wait_msg = MagicMock()
    wait_msg.message_id = 888
    wait_msg.edit_text = AsyncMock()
    wait_msg.delete = AsyncMock()
    message.answer = AsyncMock(return_value=wait_msg)

    await process_food_input(message, state, "en")

    # Wait message is NOT deleted, it is edited to ai_service_unavailable
    assert not wait_msg.delete.called
    assert wait_msg.edit_text.called
    fallback_text = wait_msg.edit_text.call_args[0][0]
    assert "queue" in fallback_text.lower() or "overloaded" in fallback_text.lower()

    # Queue item contains status_message_id 888
    item = await rate_limiter.get_next_pending_queue_item(db_session)
    assert item is not None
    assert item.payload.get("status_message_id") == 888

    # Worker now succeeds
    mock_analysis = gemini.FoodAnalysisResponse(**MOCK_ANALYSIS)
    monkeypatch.setattr(gemini, "analyze_food_input", AsyncMock(return_value=mock_analysis))

    bot = SimpleNamespace(
        id=1,
        send_message=AsyncMock(),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock()
    )

    assert await rate_limiter.execute_queued_item(bot, storage, db_session, item)
    assert bot.edit_message_text.call_count == 2
    assert bot.send_message.call_count == 0


async def test_retry_delivery_resilience_no_duplicate_ai(db_session, monkeypatch):
    """Delivery failure reuses persisted result_draft_id and never calls Gemini again."""
    await create_user(db_session, telegram_id=44444, language="en")

    mock_analysis = gemini.FoodAnalysisResponse(**MOCK_ANALYSIS)
    analyze_mock = AsyncMock(return_value=mock_analysis)
    monkeypatch.setattr(gemini, "analyze_food_input", analyze_mock)

    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=44444,
        chat_id=44444,
        request_type="analyze_food_input",
        payload={"text_description": "Soup", "status_message_id": 555}
    )
    item = await db_session.get(AiRequestQueue, qid)

    # First attempt: Gemini succeeds, but editing raises error, fallback send_message also raises error
    bot_fail = SimpleNamespace(
        id=1,
        edit_message_text=AsyncMock(side_effect=[None, RuntimeError("Telegram 502 Bad Gateway")]),
        send_message=AsyncMock(side_effect=RuntimeError("Network Error")),
        delete_message=AsyncMock()
    )

    with pytest.raises(RuntimeError):
        await rate_limiter.execute_queued_item(bot_fail, MemoryStorage(), db_session, item)

    # result_draft_id was persisted before delivery error
    assert item.payload.get("result_draft_id") is not None
    assert analyze_mock.call_count == 1

    # Second attempt: delivery succeeds via edit
    bot_ok = SimpleNamespace(
        id=1,
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
        delete_message=AsyncMock()
    )

    assert await rate_limiter.execute_queued_item(bot_ok, MemoryStorage(), db_session, item)

    # Gemini was NOT called again!
    assert analyze_mock.call_count == 1
    assert bot_ok.edit_message_text.call_count == 1


async def test_terminal_failure_edits_status_message(db_session, monkeypatch):
    """Permanent failure edits status message in-place to err_analysis_failed."""
    await create_user(db_session, telegram_id=66666, language="en")

    # Gemini returns empty/failure
    monkeypatch.setattr(gemini, "analyze_food_input", AsyncMock(return_value=None))

    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=66666,
        chat_id=66666,
        request_type="analyze_food_input",
        payload={"text_description": "Gibberish", "status_message_id": 111}
    )

    bot = SimpleNamespace(
        id=1,
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
        delete_message=AsyncMock()
    )

    # Process item via process_next_queue_item
    await rate_limiter.process_next_queue_item(bot, MemoryStorage())

    # bot.edit_message_text should be called to notify error
    assert bot.edit_message_text.called
    last_call = bot.edit_message_text.call_args_list[-1]
    err_text = last_call.kwargs.get("text") or last_call.args[2]
    assert "fail" in err_text.lower() or "error" in err_text.lower() or "couldn't" in err_text.lower() or "не удалось" in err_text.lower()

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, UTC, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext

from src.database import crud
from src.database.models import User, AiRequestLog, AiRequestQueue
from src.services import rate_limiter, gemini
from src.handlers.food import FoodLoggingState, MealEditingState

pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture
async def setup_test_user(db_session: AsyncSession):
    # Ensure there is a user in the DB
    user = await crud.create_or_update_user(
        db_session,
        telegram_id=55555,
        name="Test User",
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
        language="en"
    )
    return user

async def test_check_rate_limit_minute(db_session: AsyncSession):
    # Initially, it shouldn't be limited
    is_limited, limit_type = await rate_limiter.check_rate_limit(db_session)
    assert not is_limited

    # Add 15 requests in the last 10 seconds
    now = datetime.now(UTC).replace(tzinfo=None)
    for _ in range(15):
        log = AiRequestLog(user_id=55555, request_type="test", executed_at=now)
        db_session.add(log)
    await db_session.commit()

    is_limited, limit_type = await rate_limiter.check_rate_limit(db_session)
    assert is_limited
    assert limit_type == "minute"

async def test_check_rate_limit_day(db_session: AsyncSession):
    # Clear logs first
    import sqlalchemy as sa
    await db_session.execute(sa.delete(AiRequestLog))
    await db_session.commit()

    # Add 1500 requests older than 1 minute but within 24 hours
    now = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
    for i in range(1500):
        log = AiRequestLog(user_id=55555, request_type="test", executed_at=now)
        db_session.add(log)
        # Commit periodically to keep memory low if needed, but in SQLite in-memory it's fine
        if i % 500 == 0:
            await db_session.commit()
    await db_session.commit()

    is_limited, limit_type = await rate_limiter.check_rate_limit(db_session)
    assert is_limited
    assert limit_type == "day"

async def test_queue_position(db_session: AsyncSession, setup_test_user):
    import sqlalchemy as sa
    await db_session.execute(sa.delete(AiRequestQueue))
    await db_session.commit()

    qid1 = await rate_limiter.add_to_queue(db_session, 55555, 55555, "analyze_food_input", {"test": 1})
    qid2 = await rate_limiter.add_to_queue(db_session, 55555, 55555, "analyze_food_input", {"test": 2})

    pos1 = await rate_limiter.get_queue_position(db_session, qid1)
    pos2 = await rate_limiter.get_queue_position(db_session, qid2)

    assert pos1 == 1
    assert pos2 == 2

async def test_execute_queued_analyze_food_input(db_session: AsyncSession, setup_test_user, mock_bot, monkeypatch):
    # Setup FSM Storage
    storage = MemoryStorage()
    key = StorageKey(bot_id=mock_bot.id, chat_id=55555, user_id=55555)
    fsm_ctx = FSMContext(storage=storage, key=key)
    await fsm_ctx.set_state(FoodLoggingState.waiting_for_input)
    await fsm_ctx.update_data(meal_type="breakfast")

    # Mock gemini call
    mock_analysis = MagicMock()
    mock_analysis.food_items = [
        MagicMock(name="Apple", portion="1 apple", calories=95, protein=0.5, fat=0.3, carb=25.0)
    ]
    mock_analysis.total_calories = 95
    mock_analysis.total_protein = 0.5
    mock_analysis.total_fat = 0.3
    mock_analysis.total_carb = 25.0
    mock_analysis.model_dump.return_value = {"food_items": []}

    async def mock_analyze(*args, **kwargs):
        return mock_analysis

    monkeypatch.setattr("src.services.gemini.analyze_food_input", mock_analyze)

    # Queue an item
    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=55555,
        chat_id=55555,
        request_type="analyze_food_input",
        payload={"text_description": "1 apple", "image_file_id": None, "meal_type": "breakfast"}
    )
    
    # Get the item from DB
    import sqlalchemy as sa
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item = res.scalar()

    success = await rate_limiter.execute_queued_item(mock_bot, storage, db_session, item)
    assert success

    # FSM state should change to waiting_for_confirm
    state = await fsm_ctx.get_state()
    assert state == FoodLoggingState.waiting_for_confirm

    # Bot should have sent the message
    mock_bot.send_message.assert_called()
    called_text = mock_bot.send_message.call_args[0][1]
    assert "95 kcal" in called_text

async def test_execute_queued_adjust_food_analysis(db_session: AsyncSession, setup_test_user, mock_bot, monkeypatch):
    storage = MemoryStorage()
    key = StorageKey(bot_id=mock_bot.id, chat_id=55555, user_id=55555)
    fsm_ctx = FSMContext(storage=storage, key=key)
    await fsm_ctx.set_state(FoodLoggingState.waiting_for_correction)

    mock_analysis = MagicMock()
    mock_analysis.food_items = []
    mock_analysis.total_calories = 100
    mock_analysis.total_protein = 1
    mock_analysis.total_fat = 2
    mock_analysis.total_carb = 3
    mock_analysis.model_dump.return_value = {}

    async def mock_adjust(*args, **kwargs):
        return mock_analysis

    monkeypatch.setattr("src.services.gemini.adjust_food_analysis", mock_adjust)

    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=55555,
        chat_id=55555,
        request_type="adjust_food_analysis",
        payload={"original_data": {}, "correction_text": "more cheese"}
    )
    
    import sqlalchemy as sa
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item = res.scalar()

    success = await rate_limiter.execute_queued_item(mock_bot, storage, db_session, item)
    assert success

    state = await fsm_ctx.get_state()
    assert state == FoodLoggingState.waiting_for_confirm

async def test_process_queue_item_retry_on_unavailability(db_session: AsyncSession, setup_test_user, mock_bot, monkeypatch):
    # Setup FSM Storage
    storage = MemoryStorage()

    # Mock execute_queued_item to raise an exception
    async def mock_execute(*args, **kwargs):
        raise Exception("Gemma API Timeout (503)")

    monkeypatch.setattr("src.services.rate_limiter.execute_queued_item", mock_execute)

    # Queue an item
    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=55555,
        chat_id=55555,
        request_type="analyze_food_input",
        payload={"text_description": "1 apple", "image_file_id": None, "meal_type": "breakfast"}
    )

    # Initially next_retry_at is None
    import sqlalchemy as sa
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item_before = res.scalar()
    assert item_before.retry_count == 0
    assert item_before.next_retry_at is None

    # Process item (this should fail and trigger retry mechanism)
    await rate_limiter.process_next_queue_item(mock_bot, storage)

    # Fetch item again to verify updates
    # We must expire the session cache or refresh
    await db_session.commit()
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item_after = res.scalar()

    assert item_after.status == "pending"
    assert item_after.retry_count == 1
    assert item_after.next_retry_at is not None
    assert "Timeout" in item_after.last_error

    # Verify that get_next_pending_queue_item does NOT return this item now because next_retry_at is in the future
    ready_item = await rate_limiter.get_next_pending_queue_item(db_session)
    assert ready_item is None

async def test_process_queue_item_retry_backoff_calculation(db_session: AsyncSession, setup_test_user, mock_bot, monkeypatch):
    storage = MemoryStorage()

    # Mock execute_queued_item to raise exception
    async def mock_execute(*args, **kwargs):
        raise Exception("500 Internal Error")

    monkeypatch.setattr("src.services.rate_limiter.execute_queued_item", mock_execute)

    # Queue an item
    qid = await rate_limiter.add_to_queue(
        db_session,
        user_id=55555,
        chat_id=55555,
        request_type="analyze_food_input",
        payload={"text_description": "1 apple"}
    )

    # Run multiple times to increase retry_count and verify exponential backoff calculation
    import sqlalchemy as sa

    # Attempt 1 -> retry count should become 1, delay 5s
    await rate_limiter.process_next_queue_item(mock_bot, storage)
    await db_session.commit()
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item = res.scalar()
    assert item.retry_count == 1
    assert "Failed at attempt 1, retrying in 5s" in item.error_message

    # Attempt 2 -> retry count should become 2, delay 10s
    # In order to process it, we mock get_next_pending_queue_item or temporarily reset next_retry_at
    item.next_retry_at = None
    await db_session.commit()

    await rate_limiter.process_next_queue_item(mock_bot, storage)
    await db_session.commit()
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item = res.scalar()
    assert item.retry_count == 2
    assert "Failed at attempt 2, retrying in 10s" in item.error_message

    # Attempt 7 -> retry count should become 7, delay capped at 240s
    item.retry_count = 6
    item.next_retry_at = None
    await db_session.commit()

    await rate_limiter.process_next_queue_item(mock_bot, storage)
    await db_session.commit()
    res = await db_session.execute(sa.select(AiRequestQueue).where(AiRequestQueue.id == qid))
    item = res.scalar()
    assert item.retry_count == 7
    assert "Failed at attempt 7, retrying in 240s" in item.error_message


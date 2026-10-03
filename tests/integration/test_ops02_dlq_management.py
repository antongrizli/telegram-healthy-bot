import pytest
import pytest_asyncio
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import Message, CallbackQuery, User as TgUser

from src.database import crud
from src.database.models import User, FoodLog, AiRequestQueue
from src.handlers.admin import cmd_admin_failed_queue, admin_dlq_callback
from src.services import rate_limiter
from src.config import settings

pytestmark = pytest.mark.asyncio


async def create_user(db_session: AsyncSession, telegram_id: int, name: str = "TestAdmin", is_admin: bool = False, is_blocked: bool = False):
    return await crud.create_or_update_user(
        db_session,
        telegram_id=telegram_id,
        name=name,
        sex="male",
        age=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="light",
        goal="maintain",
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200,
        language="en",
        is_admin=is_admin,
        is_blocked=is_blocked
    )


@pytest_asyncio.fixture(autouse=True)
async def cleanup(db_session: AsyncSession):
    await db_session.execute(delete(AiRequestQueue))
    await db_session.execute(delete(FoodLog))
    await db_session.commit()
    yield
    await db_session.execute(delete(AiRequestQueue))
    await db_session.execute(delete(FoodLog))
    await db_session.commit()


async def test_dlq_crud_pagination_and_retrieval(db_session: AsyncSession):
    """CRUD pagination for failed tasks works properly and orders by descending ID."""
    user = await create_user(db_session, 10001, "User1")
    now = datetime.now(UTC).replace(tzinfo=None)

    for i in range(1, 8):
        task = AiRequestQueue(
            user_id=user.telegram_id,
            chat_id=user.telegram_id,
            request_type="analyze_food_input",
            payload={"text": f"meal {i}"},
            status="failed",
            retry_count=3,
            error_message="Retry limit reached",
            last_error=f"Error {i}",
            created_at=now - timedelta(minutes=10 - i),
        )
        db_session.add(task)
    await db_session.commit()

    # Page 0 (5 items)
    tasks_p0, total = await crud.get_failed_queue_tasks(db_session, limit=5, offset=0)
    assert total == 7
    assert len(tasks_p0) == 5
    assert tasks_p0[0].id > tasks_p0[1].id  # Descending order

    # Page 1 (2 items)
    tasks_p1, total = await crud.get_failed_queue_tasks(db_session, limit=5, offset=5)
    assert total == 7
    assert len(tasks_p1) == 2

    # Specific task retrieval
    retrieved = await crud.get_queue_task(db_session, tasks_p0[0].id)
    assert retrieved is not None
    assert retrieved.id == tasks_p0[0].id


async def test_dlq_retry_safety_and_ownership(db_session: AsyncSession):
    """Retrying a task safely validates ownership, blocked status, and task state."""
    owner = await create_user(db_session, 10002, "Owner")
    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Successful retry
    failed_task = AiRequestQueue(
        user_id=owner.telegram_id,
        chat_id=owner.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "oats"},
        status="failed",
        retry_count=3,
        error_message="Fail",
        last_error="503 Overloaded",
        created_at=now,
    )
    db_session.add(failed_task)
    await db_session.commit()

    success, msg, task = await crud.retry_queue_task(db_session, failed_task.id)
    assert success is True
    assert task.status == "pending"
    assert task.retry_count == 0
    assert task.error_message is None
    assert task.last_error is None
    assert task.next_retry_at is not None

    # 2. Cannot retry completed task
    task.status = "completed"
    await db_session.commit()
    success, msg, _ = await crud.retry_queue_task(db_session, task.id)
    assert success is False
    assert "only failed/cancelled" in msg

    # 3. Cannot retry if owner user is blocked
    task.status = "failed"
    owner.is_blocked = True
    await db_session.commit()
    success, msg, _ = await crud.retry_queue_task(db_session, task.id)
    assert success is False
    assert "blocked" in msg

    # 4. Cannot retry non-existent task
    success, msg, _ = await crud.retry_queue_task(db_session, 999999)
    assert success is False
    assert "not found" in msg


async def test_dlq_cancel_safety(db_session: AsyncSession):
    """Cancelling a task marks it cancelled and prevents further execution."""
    user = await create_user(db_session, 10003, "CancelUser")
    now = datetime.now(UTC).replace(tzinfo=None)

    task = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="generate_report",
        payload={"report_type": "daily"},
        status="failed",
        created_at=now,
    )
    db_session.add(task)
    await db_session.commit()

    success, msg, task = await crud.cancel_queue_task(db_session, task.id)
    assert success is True
    assert task.status == "cancelled"
    assert task.error_message == "Cancelled by admin"
    assert task.next_retry_at is None

    # Cannot cancel already cancelled
    success, msg, _ = await crud.cancel_queue_task(db_session, task.id)
    assert success is False
    assert "already cancelled" in msg


async def test_dlq_retry_does_not_create_duplicate_food_log(db_session: AsyncSession, mock_bot):
    """Retrying a failed food logging task delivers the draft card without inserting duplicate food log."""
    user = await create_user(db_session, 10004, "DraftUser")
    now = datetime.now(UTC).replace(tzinfo=None)

    # User already has draft saved previously
    draft_id = await crud.save_meal_draft(
        db_session,
        user.telegram_id,
        {
            "analysis": {
                "food_items": [{"name": "Apple", "portion": "100g", "calories": 52, "protein": 0.3, "fat": 0.2, "carb": 14}],
                "total_calories": 52,
                "total_protein": 0.3,
                "total_fat": 0.2,
                "total_carb": 14
            },
            "raw_text": "apple",
            "meal_type": "snack"
        }
    )

    failed_task = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text_description": "apple", "result_draft_id": draft_id},
        status="failed",
        created_at=now,
    )
    db_session.add(failed_task)
    await db_session.commit()

    # Retry task
    success, _, _ = await crud.retry_queue_task(db_session, failed_task.id)
    assert success is True

    # Check FoodLog count before worker execution
    cnt_before = (await db_session.execute(select(func.count(FoodLog.id)).where(FoodLog.user_id == user.telegram_id))).scalar()
    assert cnt_before == 0

    # Execute task through worker
    storage = MagicMock()
    storage.get_data = AsyncMock(return_value={})
    storage.set_state = AsyncMock()
    storage.set_data = AsyncMock()

    await rate_limiter.process_next_queue_item(mock_bot, storage)

    # Check FoodLog count after retry execution
    cnt_after = (await db_session.execute(select(func.count(FoodLog.id)).where(FoodLog.user_id == user.telegram_id))).scalar()
    assert cnt_after == 0  # Still 0! It only delivered the draft card for confirmation!

    # Task is now completed
    updated_task = await crud.get_queue_task(db_session, failed_task.id)
    assert updated_task.status == "completed"


async def test_admin_cmd_failed_queue_views(db_session: AsyncSession):
    """Admin message command renders both empty and populated failed queue states."""
    admin_user = await create_user(db_session, 99001, "SuperAdmin", is_admin=True)
    msg = MagicMock()
    msg.from_user = TgUser(id=99001, is_bot=False, first_name="Admin")
    msg.chat.id = 99001
    msg.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()

    # 1. Empty queue
    await cmd_admin_failed_queue(msg, state, "ru")
    assert msg.answer.called
    text = msg.answer.call_args[0][0]
    assert "В очереди нет неудачных заданий" in text

    # 2. Populated queue
    msg.answer.reset_mock()
    task = AiRequestQueue(
        user_id=admin_user.telegram_id,
        chat_id=admin_user.telegram_id,
        request_type="adjust_food_analysis",
        payload={"text": "correction"},
        status="failed",
        retry_count=3,
        last_error="Gemini 500 Internal",
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(task)
    await db_session.commit()

    await cmd_admin_failed_queue(msg, state, "ru")
    assert msg.answer.called
    text = msg.answer.call_args[0][0]
    assert "Неудачные задания очереди" in text
    assert f"#{task.id}" in text
    assert "Gemini 500 Internal" in text


async def test_admin_dlq_callbacks_lifecycle(db_session: AsyncSession):
    """DLQ callbacks: page navigation, task detail view, retry, and cancellation."""
    admin_user = await create_user(db_session, 99002, "CallbackAdmin", is_admin=True)
    task = AiRequestQueue(
        user_id=admin_user.telegram_id,
        chat_id=admin_user.telegram_id,
        request_type="analyze_food_input",
        payload={"text_description": "protein shake"},
        status="failed",
        retry_count=3,
        error_message="Retry limit exceeded",
        last_error="Network timeout",
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(task)
    await db_session.commit()

    cb = MagicMock()
    cb.from_user = TgUser(id=99002, is_bot=False, first_name="Admin")
    cb.message = MagicMock()
    cb.message.chat.id = 99002
    cb.message.edit_text = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.delete = AsyncMock()
    cb.answer = AsyncMock()
    state = MagicMock()

    # 1. View task details
    cb.data = f"dlq:view:{task.id}:0"
    await admin_dlq_callback(cb, state, "ru")
    assert cb.message.edit_text.called
    view_text = cb.message.edit_text.call_args[0][0]
    assert f"Задание очереди #{task.id}" in view_text
    assert "protein shake" in view_text
    assert "Network timeout" in view_text

    # 2. Retry task
    cb.message.edit_text.reset_mock()
    cb.data = f"dlq:retry:{task.id}:0"
    await admin_dlq_callback(cb, state, "ru")
    assert cb.answer.called
    assert "возвращено в очередь" in cb.answer.call_args[0][0]
    refreshed = await crud.get_queue_task(db_session, task.id)
    assert refreshed.status == "pending"

    # 3. Cancel task
    cb.message.edit_text.reset_mock()
    cb.data = f"dlq:cancel:{task.id}:0"
    await admin_dlq_callback(cb, state, "ru")
    assert cb.answer.called
    assert "отменено" in cb.answer.call_args[0][0]
    refreshed = await crud.get_queue_task(db_session, task.id)
    assert refreshed.status == "cancelled"

    # 4. Non-admin access rejected
    non_admin_cb = MagicMock()
    non_admin_cb.from_user = TgUser(id=11111, is_bot=False, first_name="Regular")
    non_admin_cb.message = MagicMock()
    non_admin_cb.data = f"dlq:view:{task.id}:0"
    non_admin_cb.answer = AsyncMock()
    await admin_dlq_callback(non_admin_cb, state, "ru")
    assert non_admin_cb.answer.called
    from src.utils import i18n_locales
    assert i18n_locales.get_text('admin_only', 'ru') in non_admin_cb.answer.call_args[0][0]

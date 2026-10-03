import json
import pytest
import pytest_asyncio
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from src.database import crud
from src.database.models import User, AiRequestQueue
from src.services import rate_limiter, scheduler
from src.webapp.server import health_check
from src.handlers.admin import cmd_admin_stats_queue

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def cleanup_queue(db_session: AsyncSession):
    await db_session.execute(delete(AiRequestQueue))
    await db_session.commit()
    # Reset rate_limiter worker state
    rate_limiter._worker_running = False
    rate_limiter._worker_task = None
    rate_limiter._worker_started_at = None
    rate_limiter._worker_stopped_at = None
    rate_limiter._worker_last_heartbeat = None
    rate_limiter._worker_last_error = None
    yield
    await db_session.execute(delete(AiRequestQueue))
    await db_session.commit()
    rate_limiter._worker_running = False
    rate_limiter._worker_task = None
    rate_limiter._worker_last_error = None


async def test_worker_health_stopped():
    """Worker not started reports stopped and healthy=False."""
    health = rate_limiter.get_worker_health()
    assert health["status"] == "stopped"
    assert health["healthy"] is False
    assert health["running"] is False
    assert rate_limiter.is_worker_alive() is False


async def test_worker_health_running():
    """Running worker with fresh heartbeat reports running and healthy=True."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC)

    health = rate_limiter.get_worker_health()
    assert health["status"] == "running"
    assert health["healthy"] is True
    assert health["running"] is True
    assert health["heartbeat_age_seconds"] is not None
    assert health["heartbeat_age_seconds"] < 5.0
    assert rate_limiter.is_worker_alive() is True


async def test_worker_health_stalled_automatic_detection():
    """Worker marked running but with old heartbeat is automatically detected as stalled."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC) - timedelta(seconds=200)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC) - timedelta(seconds=90)

    health = rate_limiter.get_worker_health(stall_threshold_seconds=60.0)
    assert health["status"] == "stalled"
    assert health["healthy"] is False
    assert health["running"] is True
    assert health["heartbeat_age_seconds"] >= 89.0
    assert rate_limiter.is_worker_alive(stall_threshold_seconds=60.0) is False


async def test_worker_health_crashed_detection():
    """Worker with recorded fatal error reports crashed and healthy=False."""
    rate_limiter._worker_running = False
    rate_limiter._worker_last_error = "Connection dropped by host"
    rate_limiter._worker_stopped_at = datetime.now(UTC)

    health = rate_limiter.get_worker_health()
    assert health["status"] == "crashed"
    assert health["healthy"] is False
    assert health["error"] == "Connection dropped by host"


async def test_scheduler_health():
    """get_scheduler_health reports running status, job count, and summary counters."""
    health = scheduler.get_scheduler_health()
    assert "status" in health
    assert "healthy" in health
    assert "jobs_count" in health
    assert "summary" in health
    assert "successful" in health["summary"]
    assert "failed" in health["summary"]
    assert "missed" in health["summary"]


async def test_queue_health_empty(db_session: AsyncSession):
    """Empty queue reports zero counts and 0.0 latency/age."""
    q_health = await crud.get_queue_health(db_session)
    assert q_health["pending"] == 0
    assert q_health["processing"] == 0
    assert q_health["completed"] == 0
    assert q_health["failed"] == 0
    assert q_health["oldest_pending_age_seconds"] == 0.0
    assert q_health["oldest_processing_age_seconds"] == 0.0
    assert q_health["avg_latency_seconds"] == 0.0
    assert q_health["error_rate"] == 0.0


async def create_test_user(db_session: AsyncSession, telegram_id: int, name: str = "TestUser"):
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
        language="en"
    )


async def test_queue_health_with_items(db_session: AsyncSession):
    """Queue health calculates counts, oldest age, latency, and error rate accurately."""
    # Ensure test user
    user = await create_test_user(db_session, telegram_id=88888, name="QueueTest")

    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Pending item created 50s ago
    pending_item = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "apple"},
        status="pending",
        created_at=now - timedelta(seconds=50),
    )
    # 2. Completed item: created 20s ago, processed 10s ago (latency = 10s)
    completed_item = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "banana"},
        status="completed",
        created_at=now - timedelta(seconds=20),
        processed_at=now - timedelta(seconds=10),
    )
    # 3. Failed item created 30s ago
    failed_item = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "orange"},
        status="failed",
        last_error="Gemini 503 Overloaded",
        created_at=now - timedelta(seconds=30),
        processed_at=now - timedelta(seconds=25),
    )

    db_session.add_all([pending_item, completed_item, failed_item])
    await db_session.commit()

    q_health = await crud.get_queue_health(db_session, window_hours=24)
    assert q_health["pending"] == 1
    assert q_health["completed"] == 1
    assert q_health["failed"] == 1
    assert q_health["completed_24h"] == 1
    assert q_health["failed_24h"] == 1
    assert q_health["oldest_pending_age_seconds"] >= 48.0
    assert q_health["avg_latency_seconds"] == 10.0
    assert q_health["error_rate"] == 0.5
    assert "Gemini 503 Overloaded" in q_health["queue_errors"]


async def test_health_endpoint_healthy(db_session: AsyncSession, monkeypatch):
    """GET /health returns 200 and healthy when DB and worker are operational."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC)
    monkeypatch.setattr(scheduler, 'get_scheduler_health', lambda: {'status': 'running', 'healthy': True})

    req = make_mocked_request("GET", "/health")
    resp = await health_check(req)

    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["worker"]["status"] == "running"
    assert data["worker"]["healthy"] is True
    assert "scheduler" in data
    assert "queue" in data
    assert data["queue"]["pending"] == 0


async def test_health_endpoint_degraded_on_old_pending_task(db_session: AsyncSession):
    """GET /health returns 200 degraded when a pending task exceeds SLA (>300s)."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC)

    user = await create_test_user(db_session, telegram_id=88889, name="DegradedQueueUser")
    now = datetime.now(UTC).replace(tzinfo=None)
    stuck_task = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "stuck food"},
        status="pending",
        created_at=now - timedelta(seconds=450),
    )
    db_session.add(stuck_task)
    await db_session.commit()

    req = make_mocked_request("GET", "/health")
    resp = await health_check(req)

    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "degraded"
    assert data["queue"]["oldest_pending_age_seconds"] >= 440.0


async def test_health_endpoint_unhealthy_on_stalled_worker():
    """GET /health returns 503 unhealthy when worker is detected as stalled."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC) - timedelta(seconds=300)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC) - timedelta(seconds=120)

    req = make_mocked_request("GET", "/health")
    resp = await health_check(req)

    assert resp.status == 503
    data = json.loads(resp.text)
    assert data["status"] == "unhealthy"
    assert data["worker"]["status"] == "stalled"
    assert data["worker"]["healthy"] is False


async def test_health_endpoint_unhealthy_on_db_disconnect():
    """GET /health returns 503 unhealthy when database connection fails."""
    rate_limiter._worker_running = True
    rate_limiter._worker_started_at = datetime.now(UTC)
    rate_limiter._worker_last_heartbeat = datetime.now(UTC)

    req = make_mocked_request("GET", "/health")
    with patch("src.webapp.server.AsyncSessionLocal", side_effect=RuntimeError("DB down")):
        resp = await health_check(req)

    assert resp.status == 503
    data = json.loads(resp.text)
    assert data["status"] == "unhealthy"
    assert data["database"] == "disconnected"
    assert 'database_error' not in data
    assert 'DB down' not in resp.text


async def test_admin_stats_queue_shows_observability(db_session: AsyncSession):
    """Admin queue stats command renders worker status, oldest task age, and error rate."""
    user = await create_test_user(db_session, telegram_id=99999, name="AdminTest")
    now = datetime.now(UTC).replace(tzinfo=None)

    task = AiRequestQueue(
        user_id=user.telegram_id,
        chat_id=user.telegram_id,
        request_type="analyze_food_input",
        payload={"text": "admin check"},
        status="pending",
        created_at=now - timedelta(seconds=25),
    )
    db_session.add(task)
    await db_session.commit()

    rate_limiter._worker_running = True
    rate_limiter._worker_last_heartbeat = datetime.now(UTC)

    msg = AsyncMock()
    msg.from_user.id = 99999

    # Test Russian locale
    await cmd_admin_stats_queue(msg, "ru")
    assert msg.answer.called
    ru_text = msg.answer.call_args[0][0]
    assert "Воркер очереди" in ru_text
    assert "Возраст старейшего задания" in ru_text
    assert "25" in ru_text or "сек" in ru_text
    assert "Доля ошибок (24ч)" in ru_text

    # Test English locale
    msg.answer.reset_mock()
    await cmd_admin_stats_queue(msg, "en")
    assert msg.answer.called
    en_text = msg.answer.call_args[0][0]
    assert "Queue Worker" in en_text
    assert "Oldest Pending Task Age" in en_text
    assert "Error Rate (24h)" in en_text

"""Regression checks for startup, incomplete diaries, quotas and worker shutdown."""
import asyncio
import json
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings
from src.database import crud, init_db
from src.database.models import User, FoodLog, WeightLog, AiRequestAttempt, AiRequestLog, AiRequestQueue
from src.services import ai_quota, gamification, gemini, rate_limiter
from src.webapp.middlewares import safe_errors_middleware

pytestmark = pytest.mark.asyncio


async def make_user(db, timezone="UTC"):
    user = User(telegram_id=991, name="Test", sex="male", age=30, height_cm=180,
                weight_kg=80, activity_level="light", goal="lose_weight", language="ru",
                timezone=timezone, target_calories=2000, target_protein=150,
                target_fat=70, target_carb=200)
    db.add(user)
    await db.commit()
    return user


async def test_startup_preserves_monday_and_upgrades_legacy_schema(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(init_db, "engine", engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, name VARCHAR, weekly_report_day INTEGER)"))
            await conn.execute(sa.text("INSERT INTO users VALUES (991, 'Test', 0)"))
        await init_db.init_db()
        await init_db.init_db()
        async with engine.connect() as conn:
            assert (await conn.execute(sa.text("SELECT weekly_report_day, monthly_report_day, timezone FROM users"))).one() == (0, 1, "UTC")
            tables = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert "ai_request_attempts" in tables
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar() == "0001_baseline"
        # A database stamped with a revision this build does not know must refuse to start.
        async with engine.begin() as conn:
            await conn.execute(sa.text("UPDATE alembic_version SET version_num = 'from_the_future'"))
        with pytest.raises(Exception):
            await init_db.init_db()
    finally:
        await engine.dispose()


async def test_startup_creates_fresh_database_at_head(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(init_db, "engine", engine)
    try:
        await init_db.init_db()
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert {"users", "food_logs", "ai_request_queue", "alembic_version"} <= tables
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar() == "0001_baseline"
    finally:
        await engine.dispose()


async def test_empty_card_has_no_guessed_scores_or_ai(db_session, monkeypatch):
    user = await make_user(db_session)
    note = AsyncMock()
    monkeypatch.setattr(gamification, "gemini_generate_card_note", note)
    card = await gamification.generate_weekly_health_card(db_session, user.telegram_id)
    assert card.card_data["overall_score"] is None
    assert card.card_data["categories"]["nutrition"]["score"] is None
    assert card.card_data["categories"]["weight_progress"]["score"] is None
    assert card.card_data["coverage"]["logged_days"] == 0
    note.assert_not_awaited()


@pytest.mark.parametrize("weight_count", [0, 1, 2])
async def test_card_local_days_dst_partial_average_and_exclusive_end(db_session, monkeypatch, weight_count):
    user = await make_user(db_session, "Europe/Berlin")
    zone = ZoneInfo(user.timezone)
    now = datetime(2026, 3, 30, 12, tzinfo=UTC)
    # Two UTC dates that belong to the same local day, across the DST week.
    for day, hour, calories in [(23, 0, 1000), (23, 23, 1000), (24, 12, 2000), (29, 12, 2000), (30, 0, 9999)]:
        db_session.add(FoodLog(user_id=user.telegram_id, items_json=[], calories=calories,
            proteins=150, fats=70, carbs=200,
            logged_at=datetime(2026, 3, day, hour, tzinfo=zone).astimezone(UTC).replace(tzinfo=None)))
    for index in range(weight_count):
        db_session.add(WeightLog(user_id=user.telegram_id, weight=80-index,
            logged_at=datetime(2026, 3, 24+index, 12)))
    await db_session.commit()
    monkeypatch.setattr(gamification, "gemini_generate_card_note", AsyncMock(return_value="Note"))
    card = await gamification.generate_weekly_health_card(db_session, user.telegram_id, now)
    data = card.card_data
    assert data["coverage"]["logged_days"] == 3
    assert data["averages"]["calories"] == 2000
    assert data["categories"]["nutrition"]["score"] == 100
    assert data["categories"]["weight_progress"]["score"] == (100 if weight_count == 2 else None)
    assert data["overall_score"] == (76 if weight_count == 2 else 71)
    assert data["data_status"] == "partial"


async def test_concurrent_quota_reservations_and_no_double_count(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "AI_USER_REQUESTS_PER_MINUTE", 20)
    results = await asyncio.gather(*(ai_quota.reserve_attempt(991, "test") for _ in range(10)), return_exceptions=True)
    assert sum(result is None for result in results) == 2
    assert sum(isinstance(result, ai_quota.AIQuotaExceeded) for result in results) == 8
    await rate_limiter.log_ai_request(db_session, 991, "test")
    assert (await crud.ai_quota_usage(db_session, datetime.now(UTC).replace(tzinfo=None)-timedelta(minutes=1)))[0] == 2


async def test_failed_provider_attempts_and_retries_consume_quota(db_session, mock_gemini_client, monkeypatch):
    monkeypatch.setattr(settings, "AI_USER_REQUESTS_PER_MINUTE", 2)
    mock_gemini_client.models.generate_content.side_effect = RuntimeError("503 unavailable")
    with pytest.raises(ai_quota.AIQuotaExceeded) as error:
        await gemini.call_gemini_with_retry(["test"], user_id=991, initial_delay=0)
    assert error.value.scope == "user"
    assert mock_gemini_client.models.generate_content.call_count == 2
    assert (await db_session.execute(sa.select(sa.func.count(AiRequestAttempt.id)))).scalar_one() == 2


async def test_queue_retry_limit_and_quota_wait_are_distinct(db_session, monkeypatch, mock_bot):
    user = await make_user(db_session)
    monkeypatch.setattr(settings, "AI_QUEUE_MAX_RETRIES", 2)
    execute = AsyncMock(side_effect=ai_quota.AIQuotaExceeded("minute", 60))
    monkeypatch.setattr(rate_limiter, "execute_queued_item", execute)
    qid = await rate_limiter.add_to_queue(db_session, user.telegram_id, user.telegram_id, "generate_report", {})
    await rate_limiter.process_next_queue_item(mock_bot, None)
    row = await db_session.get(AiRequestQueue, qid)
    assert row.status == "pending" and row.retry_count == 0 and row.next_retry_at
    execute.side_effect = RuntimeError("provider error")
    for _ in range(2):
        row.next_retry_at = None
        await db_session.commit()
        await rate_limiter.process_next_queue_item(mock_bot, None)
    assert row.status == "failed" and row.retry_count == 2 and row.next_retry_at is None
    assert await rate_limiter.get_next_pending_queue_item(db_session) is None


async def test_worker_shutdown_awaits_in_flight_work(monkeypatch, mock_bot):
    entered, release = asyncio.Event(), asyncio.Event()
    async def process(*args):
        entered.set()
        await release.wait()
    monkeypatch.setattr(rate_limiter, "process_next_queue_item", process)
    worker = asyncio.create_task(rate_limiter.start_queue_worker(mock_bot, None))
    await asyncio.wait_for(entered.wait(), 2)
    stop = asyncio.create_task(rate_limiter.stop_queue_worker(worker))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(stop, 2)
    assert worker.done() and not rate_limiter._worker_running


async def test_worker_shutdown_cancels_unstarted_task(mock_bot):
    task = asyncio.create_task(rate_limiter.start_queue_worker(mock_bot, None))
    await rate_limiter.stop_queue_worker(task)
    assert task.cancelled()


@pytest.mark.parametrize("error", [RuntimeError("secret SQL /server/private.py"), web.HTTPInternalServerError(text="secret SQL")])
async def test_internal_error_is_generic_and_has_reference(error, caplog):
    async def handler(request):
        raise error
    response = await safe_errors_middleware(make_mocked_request("GET", "/api/test"), handler)
    data = json.loads(response.text)
    assert response.status == 500 and data["error"] == "internal_error"
    assert len(data["request_id"]) == 32
    assert "secret" not in response.text
    assert data["request_id"] in caplog.text


async def test_profile_deletion_anonymizes_quota_history(db_session):
    user = await make_user(db_session)
    await ai_quota.reserve_attempt(user.telegram_id)
    await rate_limiter.log_ai_request(db_session, user.telegram_id, "test")
    await crud.delete_user(db_session, user.telegram_id)
    for model in (AiRequestAttempt, AiRequestLog):
        assert (await db_session.execute(sa.select(model.user_id))).scalars().all() == [None]


async def test_legacy_quota_usage_survives_upgrade(db_session, monkeypatch):
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_MINUTE", 2)
    db_session.add(AiRequestLog(user_id=991, request_type="old", executed_at=datetime.now(UTC).replace(tzinfo=None)-timedelta(seconds=10)))
    await db_session.commit()
    await ai_quota.reserve_attempt(991)
    with pytest.raises(ai_quota.AIQuotaExceeded):
        await ai_quota.reserve_attempt(992)


async def test_cached_result_delivery_does_not_require_new_quota(db_session, monkeypatch, mock_bot):
    user = await make_user(db_session)
    await rate_limiter.add_to_queue(db_session, user.telegram_id, user.telegram_id, "medication_photo", {"result": {}})
    monkeypatch.setattr(rate_limiter, "check_rate_limit", AsyncMock(return_value=(True, "day")))
    execute = AsyncMock(return_value=True)
    monkeypatch.setattr(rate_limiter, "execute_queued_item", execute)
    await rate_limiter.process_next_queue_item(mock_bot, None)
    execute.assert_awaited_once()


async def test_medication_validation_error_never_returns_exception(db_session, monkeypatch):
    from src.webapp import medications as api
    await make_user(db_session)
    monkeypatch.setattr(api, "AsyncSessionLocal", rate_limiter.AsyncSessionLocal)
    monkeypatch.setattr(api, "validate_init_data", lambda request: 991)
    def invalid(data):
        raise ValueError("secret SQL /server/private.py")
    monkeypatch.setattr(api.meds, "medication_values", invalid)
    request = make_mocked_request("POST", "/api/medications/items", match_info={"kind": "items"})
    request.json = AsyncMock(return_value={"name": "test"})
    with pytest.raises(web.HTTPBadRequest) as error:
        await api.medication_api(request)
    assert "secret" not in error.value.text


async def test_ai_totals_are_recomputed_from_items():
    data = gemini.FoodAnalysisResponse(food_items=[dict(name="Eggs", portion="2", calories=140, protein=12, fat=10, carb=1)],
        total_calories=999, total_protein=999, total_fat=999, total_carb=999)
    assert (data.total_calories, data.total_protein, data.total_fat, data.total_carb) == (140, 12, 10, 1)


@pytest.mark.parametrize("value", [float('nan'), float('inf'), -1, 2001])
async def test_invalid_ai_nutrition_is_rejected(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        gemini.FoodItem(name="Test", portion="1", calories=100, protein=value, fat=1, carb=1)

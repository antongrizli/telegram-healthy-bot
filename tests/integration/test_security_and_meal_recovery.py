"""Regression coverage for authentication, durable meals, and report date boundaries."""
import hashlib
import hmac
import json
from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from src.config import settings
from src.database import crud
from src.database.models import AiRequestQueue, FoodLog
from src.services import gemini, rate_limiter, scheduler
from src.webapp import auth, server


ANALYSIS = dict(food_items=[dict(name="Apple", portion="1", calories=100, protein=1, fat=2, carb=20)],
                total_calories=100, total_protein=1, total_fat=2, total_carb=20)


def signed_data(user_id=123, age=0, token=None):
    data = {"auth_date": str(int(datetime.now(UTC).timestamp()) - age),
            "user": json.dumps({"id": user_id, "first_name": "Test"})}
    secret = hmac.new(b"WebAppData", (token or settings.TELEGRAM_BOT_TOKEN).encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(data.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def request(data, path="/"):
    return make_mocked_request("GET", path, headers={"X-Telegram-Init-Data": data})


@pytest.mark.parametrize("data", ["123", "", "invalid", "user=123&hash=bad"])
def test_reject_unsigned_auth(data):
    with pytest.raises(web.HTTPUnauthorized):
        auth.validate_init_data(request(data))


def test_accept_valid_signature():
    assert auth.validate_init_data(request(signed_data())) == 123


@pytest.mark.parametrize("age", [86401, -600])
def test_reject_invalid_auth_date(age):
    with pytest.raises(web.HTTPUnauthorized):
        auth.validate_init_data(request(signed_data(age=age)))


def test_reject_wrong_signature():
    with pytest.raises(web.HTTPUnauthorized):
        auth.validate_init_data(request(signed_data(token="wrong")))


async def make_user(db, user_id=123, **kwargs):
    return await crud.create_or_update_user(db, user_id, name="Test", sex="male", age=30,
        height_cm=180, weight_kg=80, activity_level="light", goal="maintain", language="en",
        target_calories=2000, target_protein=100, target_fat=70, target_carb=250, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [server.get_nutrition_data, server.get_weight_data,
    server.get_streaks_data, server.get_achievements_data, server.get_health_card_data, server.get_user_settings])
async def test_blocked_user_cannot_use_any_dashboard_endpoint(db_session, monkeypatch, endpoint):
    await make_user(db_session, is_blocked=True)
    class Session:
        async def __aenter__(self):
            return db_session
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(server, "AsyncSessionLocal", Session)
    with pytest.raises(web.HTTPForbidden):
        await endpoint(request(signed_data()))


@pytest.mark.asyncio
async def test_draft_survives_restart_and_saves_once_on_original_day(db_session):
    await make_user(db_session)
    payload = {"analysis": ANALYSIS, "logged_at": "2026-09-06T23:58:00+02:00", "meal_type": "dinner"}
    draft_id = await crud.save_meal_draft(db_session, 123, payload)
    # No FSM is needed to confirm after a restart; a different user cannot consume it.
    assert await crud.finish_meal_draft(db_session, draft_id, 456) is None
    meal = await crud.finish_meal_draft(db_session, draft_id, 123)
    assert meal.logged_at == datetime(2026, 9, 6, 21, 58)
    assert meal.calories == 100
    assert await crud.finish_meal_draft(db_session, draft_id, 123) is None
    assert len((await db_session.execute(select(FoodLog))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_cancelled_draft_cannot_be_saved_or_corrected(db_session):
    await make_user(db_session)
    payload = {"analysis": ANALYSIS, "logged_at": datetime.now(UTC).isoformat()}
    draft_id = await crud.save_meal_draft(db_session, 123, payload)
    assert await crud.finish_meal_draft(db_session, draft_id, 123, accept=False)
    assert await crud.finish_meal_draft(db_session, draft_id, 123) is None
    assert await crud.save_meal_draft(db_session, 123, payload, draft_id=draft_id) is None


@pytest.mark.asyncio
async def test_queued_meal_without_fsm_has_recoverable_confirmation(db_session, monkeypatch):
    await make_user(db_session)
    analyze = AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS))
    monkeypatch.setattr(gemini, "analyze_food_input", analyze)
    bot = SimpleNamespace(id=1, send_message=AsyncMock(), delete_message=AsyncMock())
    queue_id = await rate_limiter.add_to_queue(db_session, 123, 123, "analyze_food_input",
        {"text_description": "apple", "logged_at": "2026-09-06T21:58:00+00:00"})
    item = await db_session.get(AiRequestQueue, queue_id)
    assert await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
    draft_id = item.payload["result_draft_id"]
    button = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == f"meal_draft:accept:{draft_id}"
    # Retrying delivery reuses the same draft and never calls AI again.
    assert await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
    assert analyze.await_count == 1
    meal = await crud.finish_meal_draft(db_session, draft_id, 123)
    assert meal.logged_at == datetime(2026, 9, 6, 21, 58)


@pytest.mark.asyncio
async def test_queued_report_preserves_original_day(db_session, monkeypatch):
    await make_user(db_session, timezone="Europe/Berlin")
    yesterday = datetime.now(ZoneInfo("Europe/Berlin")).replace(hour=21, minute=0, second=0, microsecond=0) - timedelta(days=1)
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(crud, "get_food_logs", query)
    monkeypatch.setattr(crud, "get_weight_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(gemini, "generate_report", AsyncMock(return_value="Report"))
    monkeypatch.setattr(scheduler, "send_multipart_message", AsyncMock())
    queue_id = await rate_limiter.add_to_queue(db_session, 123, 123, "generate_report",
        {"report_type": "daily", "report_at": yesterday.isoformat()})
    item = await db_session.get(AiRequestQueue, queue_id)
    await rate_limiter.execute_queued_item(SimpleNamespace(id=1), MemoryStorage(), db_session, item)
    assert query.call_args.args[2] == yesterday.replace(hour=0).astimezone(UTC).replace(tzinfo=None)
    assert query.call_args.args[3] == yesterday.astimezone(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("local_day", [datetime(2026, 3, 29), datetime(2026, 10, 25)])
async def test_daily_report_dst_boundaries(db_session, monkeypatch, local_day):
    user = await make_user(db_session, timezone="Europe/Berlin")
    report_at = local_day.replace(hour=21, tzinfo=ZoneInfo("Europe/Berlin"))
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(crud, "get_food_logs", query)
    monkeypatch.setattr(crud, "get_weight_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(gemini, "generate_report", AsyncMock(return_value="Report"))
    monkeypatch.setattr(scheduler, "send_multipart_message", AsyncMock())
    await scheduler.generate_and_send_report_direct(None, db_session, user, "daily", report_at)
    assert query.call_args.args[2] == local_day.replace(tzinfo=ZoneInfo("Europe/Berlin")).astimezone(UTC).replace(tzinfo=None)
    assert query.call_args.args[3] == report_at.astimezone(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_chart_includes_first_day_early_meal(db_session, monkeypatch):
    await make_user(db_session, timezone="Europe/Berlin")
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    first = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
    await crud.add_food_log(db_session, 123, ANALYSIS["food_items"], 100, 1, 2, 20, logged_at=first + timedelta(minutes=5))
    class Session:
        async def __aenter__(self):
            return db_session
        async def __aexit__(self, *args):
            pass
    monkeypatch.setattr(server, "AsyncSessionLocal", Session)
    response = await server.get_nutrition_data(request(signed_data()))
    assert json.loads(response.text)["calories"][0] == 100


@pytest.mark.asyncio
async def test_confirmation_callback_after_restart(db_session):
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from src.handlers.food import handle_meal_draft
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(db_session, 123,
        {"analysis": ANALYSIS, "logged_at": "2026-09-06T23:58:00+02:00"})
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=123, user_id=123))
    callback = SimpleNamespace(data=f"meal_draft:accept:{draft_id}", from_user=SimpleNamespace(id=123),
        answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock(), edit_reply_markup=AsyncMock()))
    await handle_meal_draft(callback, state, "en", user)
    assert len((await db_session.execute(select(FoodLog))).scalars().all()) == 1
    assert "logged" in callback.message.answer.call_args.args[0].lower()
    await handle_meal_draft(callback, state, "en", user)
    assert len((await db_session.execute(select(FoodLog))).scalars().all()) == 1
    assert callback.answer.call_args.kwargs["show_alert"]


@pytest.mark.asyncio
async def test_queue_delivery_failure_reuses_persisted_draft(db_session, monkeypatch):
    await make_user(db_session)
    analyze = AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS))
    monkeypatch.setattr(gemini, "analyze_food_input", analyze)
    bot = SimpleNamespace(id=1, send_message=AsyncMock(side_effect=[SimpleNamespace(message_id=1), RuntimeError("delivery failed")]),
                          delete_message=AsyncMock())
    queue_id = await rate_limiter.add_to_queue(db_session, 123, 123, "analyze_food_input", {"text_description": "apple"})
    item = await db_session.get(AiRequestQueue, queue_id)
    with pytest.raises(RuntimeError):
        await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
    assert item.payload["result_draft_id"]
    bot.send_message = AsyncMock()
    await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
    assert analyze.await_count == 1
    assert len(await crud.get_pending_meals(db_session, 123)) == 1


@pytest.mark.asyncio
async def test_queued_correction_updates_original_draft_after_restart(db_session, monkeypatch):
    await make_user(db_session)
    draft_id = await crud.save_meal_draft(db_session, 123,
        {"analysis": ANALYSIS, "logged_at": "2026-09-06T23:58:00+02:00"})
    corrected = {**ANALYSIS, "total_calories": 200}
    monkeypatch.setattr(gemini, "adjust_food_analysis", AsyncMock(return_value=gemini.FoodAnalysisResponse(**corrected)))
    bot = SimpleNamespace(id=1, send_message=AsyncMock(), delete_message=AsyncMock())
    queue_id = await rate_limiter.add_to_queue(db_session, 123, 123, "adjust_food_analysis",
        {"original_data": ANALYSIS, "draft_id": draft_id, "correction_text": "two apples"})
    item = await db_session.get(AiRequestQueue, queue_id)
    await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, item)
    assert item.payload["result_draft_id"] == draft_id
    meal = await crud.finish_meal_draft(db_session, draft_id, 123)
    assert meal.calories == 200
    assert meal.logged_at == datetime(2026, 9, 6, 21, 58)


@pytest.mark.asyncio
async def test_recent_users_include_latest_meal_and_users_without_meals(db_session):
    now = datetime.now(UTC).replace(tzinfo=None)
    await make_user(db_session, 123, created_at=now - timedelta(days=1))
    await make_user(db_session, 124, created_at=now)
    await make_user(db_session, 125, created_at=now - timedelta(days=31))
    for age in [2, 1]:
        await crud.add_food_log(db_session, 123, [], 100, 1, 2, 3,
                                logged_at=(now - timedelta(hours=age)).replace(tzinfo=UTC))
    # Pending analyses are not tracked meals.
    await crud.save_meal_draft(db_session, 124, {"analysis": ANALYSIS, "logged_at": now.isoformat()})
    rows = await crud.get_recent_user_activity(db_session)
    assert [row["telegram_id"] for row in rows] == [124, 123]
    assert rows[0]["joined_at"] == now
    assert rows[0]["last_meal_at"] is None
    assert rows[1]["last_meal_at"] == now - timedelta(hours=1)


@pytest.mark.asyncio
async def test_recent_users_limit_and_engagement_output(db_session):
    from src.handlers.admin import cmd_admin_stats_engagement
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(12):
        await make_user(db_session, 100 + index, created_at=now - timedelta(hours=index))
    rows = await crud.get_recent_user_activity(db_session)
    assert len(rows) == 10
    assert [row["telegram_id"] for row in rows] == list(range(100, 110))
    message = SimpleNamespace(answer=AsyncMock())
    await cmd_admin_stats_engagement(message, "en")
    text = message.answer.call_args.args[0]
    assert "Joined:" in text
    assert "Last meal tracked: No meals yet" in text
    assert "UTC" in text
    assert message.answer.call_args.kwargs["parse_mode"] is None

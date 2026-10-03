import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText

from src.database import crud
from src.database.models import AiRequestQueue
from src.handlers import admin, food
from src.services import gemini, rate_limiter, scheduler
from src.webapp.server import health_check
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS

pytestmark = pytest.mark.asyncio


async def task(db, kind='generate_report', status='pending', payload=None):
    await make_user(db)
    row = AiRequestQueue(user_id=123, chat_id=123, request_type=kind,
                         status=status, payload=payload or {})
    db.add(row)
    await db.commit()
    return row


async def test_health_suppresses_all_raw_diagnostics(monkeypatch):
    monkeypatch.setattr(crud, 'get_queue_health', AsyncMock(return_value={'queue_errors': {'SQL secret': 1}}))
    monkeypatch.setattr(rate_limiter, 'get_worker_health', lambda: {'status': 'crashed', 'error': '/server/secret.py'})
    response = await health_check(make_mocked_request('GET', '/health'))
    assert response.status == 503
    assert 'SQL secret' not in response.text and '/server/secret.py' not in response.text
    assert 'queue_errors' not in json.loads(response.text)['queue']


async def test_health_metrics_failure_marks_database_unhealthy(monkeypatch):
    monkeypatch.setattr(crud, 'get_queue_health', AsyncMock(side_effect=RuntimeError('SQL password')))
    response = await health_check(make_mocked_request('GET', '/health'))
    assert response.status == 503
    assert json.loads(response.text)['database'] == 'disconnected'
    assert 'SQL password' not in response.text


async def test_stopped_scheduler_cannot_report_healthy(monkeypatch):
    monkeypatch.setattr(rate_limiter, 'get_worker_health', lambda: {'status': 'running', 'healthy': True})
    monkeypatch.setattr(scheduler, 'get_scheduler_health', lambda: {'status': 'stopped', 'healthy': False})
    response = await health_check(make_mocked_request('GET', '/health'))
    assert json.loads(response.text)['status'] == 'degraded'


@pytest.mark.parametrize('status', ['processing', 'saved', 'awaiting_confirm', 'completed'])
async def test_cancel_does_not_interrupt_running_or_stored_records(db_session, status):
    row = await task(db_session, status=status)
    assert not (await crud.cancel_queue_task(db_session, row.id))[0]
    assert row.status == status


@pytest.mark.parametrize('kind', ['meal_draft', 'report_snapshot', 'unknown'])
async def test_admin_cannot_retry_storage_records(db_session, kind):
    row = await task(db_session, kind=kind, status='failed')
    assert not (await crud.retry_queue_task(db_session, row.id))[0]
    assert not (await crud.cancel_queue_task(db_session, row.id))[0]


async def test_cancel_between_selection_and_claim_never_executes(db_session, monkeypatch):
    row = await task(db_session)
    async def cancel_before_claim(db):
        assert (await crud.cancel_queue_task(db, row.id))[0]
        return row
    monkeypatch.setattr(rate_limiter, 'get_next_pending_queue_item', cancel_before_claim)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    execute = AsyncMock()
    monkeypatch.setattr(rate_limiter, 'execute_queued_item', execute)
    await rate_limiter.process_next_queue_item(None, None)
    execute.assert_not_awaited()
    assert row.status == 'cancelled'


async def test_claim_is_single_use_and_blocks_admin_cancel(db_session):
    row = await task(db_session)
    assert await crud.claim_queue_task(db_session, row.id)
    assert not await crud.claim_queue_task(db_session, row.id)
    await db_session.refresh(row)
    assert not (await crud.cancel_queue_task(db_session, row.id))[0]


@pytest.mark.parametrize('period', ['daily', 'weekly', 'monthly'])
async def test_report_delivery_retry_uses_saved_result_without_quota(db_session, monkeypatch, period):
    row = await task(db_session, payload={'report_type': period})
    await crud.add_food_log(db_session, 123, [{'name': 'Toast'}], calories=100, proteins=5, fats=2, carbs=15)
    row.payload = {**row.payload, 'report_at': datetime.now(UTC).isoformat()}
    await db_session.commit()
    generate = AsyncMock(return_value='Saved report')
    monkeypatch.setattr(gemini, 'generate_report', generate)
    bot = SimpleNamespace(id=1, send_message=AsyncMock(side_effect=TimeoutError('delivery uncertain')))
    storage = MemoryStorage()
    with pytest.raises(TimeoutError):
        await rate_limiter.execute_queued_item(bot, storage, db_session, row)
    await db_session.refresh(row)
    assert row.payload['report_delivery']['report_id']
    bot.send_message.side_effect = None
    quota = AsyncMock(return_value=(True, 'quota exhausted'))
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', quota)
    await rate_limiter.process_next_queue_item(bot, storage)
    await db_session.refresh(row)
    assert row.status == 'completed'
    generate.assert_awaited_once()
    quota.assert_not_awaited()


@pytest.mark.parametrize('period', ['daily', 'weekly', 'monthly'])
async def test_direct_report_delivery_failure_enqueues_saved_result(db_session, monkeypatch, period):
    await make_user(db_session)
    await crud.add_food_log(db_session, 123, [{'name': 'Toast'}], calories=100, proteins=5, fats=2, carbs=15)
    generate = AsyncMock(return_value='Saved direct report')
    monkeypatch.setattr(gemini, 'generate_report', generate)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    async def send(*args, **kwargs):
        if kwargs.get('reply_markup'):
            raise TimeoutError('result delivery failed')
    bot = SimpleNamespace(id=1, send_message=AsyncMock(side_effect=send))
    await getattr(scheduler, f'send_{period}_report')(bot, 123)
    row = await rate_limiter.get_next_pending_queue_item(db_session)
    assert row.payload['report_delivery']['report_id']
    bot.send_message.side_effect = None
    assert await rate_limiter.execute_queued_item(bot, MemoryStorage(), db_session, row)
    generate.assert_awaited_once()


@pytest.mark.parametrize('kind', ['adjust_food_analysis', 'adjust_meal_edit'])
async def test_legacy_confirmation_survives_failed_delivery(db_session, monkeypatch, kind):
    row = await task(db_session, kind=kind, payload={'original_data': ANALYSIS,
        'correction_text': 'two apples', 'status_message_id': 900})
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))
    await state.set_state(food.FoodLoggingState.waiting_for_correction if kind == 'adjust_food_analysis'
                          else food.MealEditingState.waiting_for_edit_text)
    generate = AsyncMock(return_value=gemini.FoodAnalysisResponse(**ANALYSIS))
    monkeypatch.setattr(gemini, 'adjust_food_analysis', generate)
    bot = SimpleNamespace(id=1, send_message=AsyncMock(side_effect=TimeoutError()),
                          edit_message_text=AsyncMock(), delete_message=AsyncMock())
    with pytest.raises(TimeoutError):
        await rate_limiter.execute_queued_item(bot, storage, db_session, row)
    bot.send_message.side_effect = None
    assert await rate_limiter.execute_queued_item(bot, storage, db_session, row)
    assert bot.send_message.call_args.kwargs['reply_markup'] is not None
    generate.assert_awaited_once()


@pytest.mark.parametrize('kind', ['adjust_food_analysis', 'adjust_meal_edit'])
async def test_old_correction_does_not_overwrite_new_flow(db_session, monkeypatch, kind):
    row = await task(db_session, kind=kind, payload={'original_data': ANALYSIS,
        'cached_adjustment': ANALYSIS, 'correction_text': 'two apples',
        'status_message_id': 900, 'edit_meal_id': 1})
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))
    expected = (food.FoodLoggingState.waiting_for_correction if kind == 'adjust_food_analysis'
                else food.MealEditingState.waiting_for_edit_text)
    await state.set_state(expected)
    await state.update_data(draft_id=999, edit_meal_id=999, analysis={'new': 'meal'})
    before = await state.get_data()
    generate = AsyncMock()
    monkeypatch.setattr(gemini, 'adjust_food_analysis', generate)
    bot = SimpleNamespace(id=1, send_message=AsyncMock(), edit_message_text=AsyncMock())
    assert await rate_limiter.execute_queued_item(bot, storage, db_session, row)
    assert await state.get_data() == before
    assert await state.get_state() == expected.state
    generate.assert_not_awaited()
    bot.send_message.assert_not_awaited()


async def test_stale_dlq_page_loads_clamped_page(db_session):
    row = await task(db_session, status='failed')
    text, keyboard = await admin.render_failed_queue_page(db_session, 'ru', page=99)
    assert f'#{row.id}' in text
    assert '1/1' in text


@pytest.mark.parametrize('reason, duplicates', [('message is not modified', 0), ('message to edit not found', 1), ('timeout', 0)])
async def test_dlq_edit_classification(db_session, reason, duplicates):
    user = await make_user(db_session)
    user.is_admin = True
    await db_session.commit()
    method = EditMessageText(chat_id=123, message_id=1, text='Test')
    error = (TelegramNetworkError if reason == 'timeout' else TelegramBadRequest)(method=method, message=reason)
    message = SimpleNamespace(edit_text=AsyncMock(side_effect=error), answer=AsyncMock())
    callback = SimpleNamespace(data='dlq:page:0', from_user=SimpleNamespace(id=123),
                               message=message, answer=AsyncMock())
    if reason == 'timeout':
        with pytest.raises(TelegramNetworkError):
            await admin.admin_dlq_callback(callback, None, 'ru')
    else:
        await admin.admin_dlq_callback(callback, None, 'ru')
    assert message.answer.await_count == duplicates


async def test_metrics_exclude_snapshots_and_use_failure_time(db_session):
    row = await task(db_session, status='failed')
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3)
    row.processed_at = datetime.now(UTC).replace(tzinfo=None)
    await crud.save_report_snapshot(db_session, 123, 'Report')
    metrics = await crud.get_queue_health(db_session)
    assert metrics['failed_24h'] == 1
    assert metrics['completed'] == 0


async def test_worker_heartbeat_continues_during_provider_wait(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def work(*args):
        entered.set()
        await release.wait()
    monkeypatch.setattr(rate_limiter, 'process_next_queue_item', work)
    worker = asyncio.create_task(rate_limiter.start_queue_worker(None, None))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        rate_limiter._worker_last_heartbeat = datetime.now(UTC) - timedelta(seconds=90)
        # Allow the independent pulse's next tick; no provider completion needed.
        await asyncio.sleep(10.1)
        assert rate_limiter.get_worker_health()['healthy']
    finally:
        release.set()
        await rate_limiter.stop_queue_worker(worker)

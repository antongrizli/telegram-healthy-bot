import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.scheduler import generate_and_send_report_direct


@pytest.mark.asyncio
async def test_scheduler_hourly_summary(caplog):
    import logging
    from apscheduler.events import JobExecutionEvent, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
    from src.services.scheduler import SchedulerLogSummary
    summary = SchedulerLogSummary()
    for code, job_id in [(EVENT_JOB_EXECUTED, "medication_reminders")] * 120 + [
        (EVENT_JOB_ERROR, "report"), (EVENT_JOB_MISSED, "reminder"),
        (EVENT_JOB_EXECUTED, summary.job_id),
    ]:
        summary.record(JobExecutionEvent(code, job_id, "default", None))
    with caplog.at_level(logging.INFO, logger="src.services.scheduler"):
        await summary.emit()
        await summary.emit()
    assert "successful=120, failed=1, missed=1" in caplog.records[-2].message
    assert "successful=0, failed=0, missed=0" in caplog.records[-1].message


def test_scheduler_logging_keeps_warnings_and_errors(mocker, caplog):
    import logging
    from src.services import scheduler
    fake_scheduler = mocker.patch.object(scheduler, "scheduler")
    executor = logging.getLogger("apscheduler.executors.default")
    previous_level = executor.level
    parent = logging.getLogger("apscheduler.executors")
    previous_parent_level = parent.level
    try:
        scheduler.configure_scheduler_logging()
        executor.info("Running job")
        executor.info("Job executed successfully")
        executor.warning("Run missed")
        executor.error("Job failed")
        assert [record.message for record in caplog.records] == ["Run missed", "Job failed"]
        assert fake_scheduler.add_job.call_args.kwargs["hours"] == 1
        fake_scheduler.add_listener.assert_called_once()
    finally:
        executor.setLevel(previous_level)
        parent.setLevel(previous_parent_level)

@pytest.mark.asyncio
async def test_generate_and_send_report_direct_weekly_name_error_fix(mocker):
    # Mock bot, db, user
    bot = AsyncMock()
    db = AsyncMock()
    user = MagicMock()
    user.telegram_id = 12345
    user.timezone = "UTC"
    user.language = "en"
    user.name = "Test User"
    user.sex = "male"
    user.age = 30
    user.height_cm = 180
    user.weight_kg = 75
    user.activity_level = "sedentary"
    user.goal = "maintain"
    user.target_calories = 2000
    user.target_protein = 150
    user.target_fat = 70
    user.target_carb = 200

    mocker.patch("src.database.crud.list_medication_reminders", new_callable=AsyncMock, return_value=[])
    # Mock DB functions
    from datetime import datetime
    from types import SimpleNamespace
    mocker.patch("src.database.crud.get_food_logs", new_callable=AsyncMock, return_value=[SimpleNamespace(calories=400, proteins=30, logged_at=datetime.now())])
    mocker.patch("src.database.crud.get_weight_logs", new_callable=AsyncMock, return_value=[])
    mocker.patch("src.services.gemini.generate_report", new_callable=AsyncMock, return_value="AI Weekly Report")
    mocker.patch("src.services.rate_limiter.log_ai_request", new_callable=AsyncMock)
    mocker.patch("src.services.scheduler.send_multipart_message", new_callable=AsyncMock)
    mocker.patch('src.database.crud.save_report_snapshot', new_callable=AsyncMock, return_value=1)
    mocker.patch('src.database.crud.get_saved_report', new_callable=AsyncMock, return_value=SimpleNamespace(id=1))

    # Call the function for weekly report, which references settings.WEBAPP_URL
    await generate_and_send_report_direct(bot, db, user, "weekly")

    # Verify that the bot was called to send the inline keyboard message with WebAppInfo
    assert bot.send_message.await_count == 2  # Compact summary and chart link.
    kwargs = bot.send_message.call_args[1]
    assert "reply_markup" in kwargs
    # Check that settings.WEBAPP_URL is in the URL of the WebAppInfo button
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.web_app.url.startswith("http")  # Should be the configured URL, e.g. from settings


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["send_daily_report", "send_weekly_report", "send_monthly_report"])
async def test_forbidden_report_is_not_queued(name, mocker):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage
    from src.services import scheduler
    user = MagicMock(telegram_id=123, is_blocked=False, language="en")
    mocker.patch.object(scheduler.crud, "get_user", AsyncMock(return_value=user))
    mocker.patch.object(scheduler.rate_limiter, "check_rate_limit", AsyncMock(return_value=(False, "")))
    queue = mocker.patch.object(scheduler.rate_limiter, "add_to_queue", AsyncMock())
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(method=SendMessage(chat_id=123, text="x"), message="bot was blocked by the user")
    await getattr(scheduler, name)(bot, 123)
    queue.assert_not_awaited()
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_report_error_notice_failure_does_not_escape(mocker):
    from aiogram.exceptions import TelegramNetworkError, TelegramForbiddenError
    from aiogram.methods import SendMessage
    from src.services import scheduler
    mocker.patch.object(scheduler.crud, "get_user", AsyncMock(return_value=MagicMock(telegram_id=123, is_blocked=False, language="en")))
    mocker.patch.object(scheduler.rate_limiter, "check_rate_limit", AsyncMock(return_value=(False, "")))
    queue = mocker.patch.object(scheduler.rate_limiter, "add_to_queue", AsyncMock())
    method = SendMessage(chat_id=123, text="x")
    bot = AsyncMock()
    bot.send_message.side_effect = [TelegramNetworkError(method=method, message="timeout"),
                                   TelegramForbiddenError(method=method, message="blocked")]
    await scheduler.send_daily_report(bot, 123)
    queue.assert_awaited_once()


@pytest.mark.asyncio
async def test_multipart_does_not_swallow_delivery_failure():
    from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
    from aiogram.methods import SendMessage
    from src.services.scheduler import send_multipart_message
    method = SendMessage(chat_id=123, text="x")
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramForbiddenError(method=method, message="blocked")
    with pytest.raises(TelegramForbiddenError):
        await send_multipart_message(bot, 123, "hello")
    assert bot.send_message.await_count == 1
    bot.send_message.reset_mock()
    bot.send_message.side_effect = [TelegramBadRequest(method=method, message="can't parse entities"), True]
    await send_multipart_message(bot, 123, "*bad markdown")
    assert bot.send_message.call_args.kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_handle_user_blocked_bot(mocker):
    from src.services.scheduler import handle_user_blocked_bot
    mock_mark = mocker.patch("src.database.crud.mark_user_blocked", new_callable=AsyncMock)
    mock_remove = mocker.patch("src.services.scheduler.remove_user_jobs")
    fake_db = AsyncMock()

    await handle_user_blocked_bot(999, db=fake_db)
    mock_mark.assert_awaited_once_with(fake_db, 999)
    mock_remove.assert_called_once_with(999)


@pytest.mark.asyncio
async def test_purge_expired_blocked_users_job(mocker):
    from src.services.scheduler import purge_expired_blocked_users_job
    mock_purge = mocker.patch("src.database.crud.purge_blocked_users", new_callable=AsyncMock, return_value=3)

    await purge_expired_blocked_users_job()
    mock_purge.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_worker_watchdog_auto_recovery_and_throttle(mocker, caplog):
    import logging
    from src.services import scheduler
    fake_bot = AsyncMock()
    # Reset watchdog global state
    scheduler._last_watchdog_alert_time = None
    scheduler._last_watchdog_status = None

    # Case 1: worker is crashed -> triggers ensure_queue_worker_running
    mock_health = mocker.patch("src.services.rate_limiter.get_worker_health", return_value={"status": "crashed", "healthy": False})
    mock_ensure = mocker.patch("src.services.rate_limiter.ensure_queue_worker_running", return_value=MagicMock())

    with caplog.at_level(logging.INFO, logger="src.services.scheduler"):
        await scheduler.check_worker_watchdog(fake_bot)

    mock_ensure.assert_called_once_with(bot=fake_bot)
    assert any("auto-recovery task triggered" in r.message for r in caplog.records)

    # Case 2: worker is still unhealthy and ensure returns None -> alerts once, then throttles
    caplog.clear()
    mock_ensure.return_value = None
    mock_health.return_value = {"status": "stalled", "healthy": False}

    with caplog.at_level(logging.WARNING, logger="src.services.scheduler"):
        await scheduler.check_worker_watchdog(fake_bot)
        # Second call immediately after should be throttled
        await scheduler.check_worker_watchdog(fake_bot)

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING and "Queue worker watchdog alert" in r.message]
    assert len(warning_records) == 1


@pytest.mark.asyncio
async def test_cleanup_operational_data_job(mocker):
    from src.services.scheduler import cleanup_operational_data_job
    mock_cleanup = mocker.patch("src.database.crud.cleanup_operational_logs", new_callable=AsyncMock, return_value={"attempts": 5, "logs": 10, "stats": 2, "queue": 1})

    await cleanup_operational_data_job()
    mock_cleanup.assert_awaited_once()




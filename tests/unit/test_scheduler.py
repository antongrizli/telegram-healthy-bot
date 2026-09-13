import pytest
from unittest.mock import AsyncMock, MagicMock
from src.services.scheduler import generate_and_send_report_direct

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

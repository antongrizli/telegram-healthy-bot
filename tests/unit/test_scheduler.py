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

    # Mock DB functions
    mocker.patch("src.database.crud.get_food_logs", new_callable=AsyncMock, return_value=[])
    mocker.patch("src.database.crud.get_weight_logs", new_callable=AsyncMock, return_value=[])
    mocker.patch("src.services.gemini.generate_report", new_callable=AsyncMock, return_value="AI Weekly Report")
    mocker.patch("src.services.rate_limiter.log_ai_request", new_callable=AsyncMock)
    mocker.patch("src.services.scheduler.send_multipart_message", new_callable=AsyncMock)

    # Call the function for weekly report, which references settings.WEBAPP_URL
    await generate_and_send_report_direct(bot, db, user, "weekly")

    # Verify that the bot was called to send the inline keyboard message with WebAppInfo
    bot.send_message.assert_called_once()
    kwargs = bot.send_message.call_args[1]
    assert "reply_markup" in kwargs
    # Check that settings.WEBAPP_URL is in the URL of the WebAppInfo button
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.web_app.url.startswith("http")  # Should be the configured URL, e.g. from settings

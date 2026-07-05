import pytest
import socket
from unittest.mock import AsyncMock, MagicMock, patch
from src.config import settings

class StopTestException(Exception):
    pass

@pytest.mark.asyncio
async def test_bot_initialization_with_ipv6(monkeypatch):
    # Mock settings.FORCE_IPV6 to True
    monkeypatch.setattr(settings, "FORCE_IPV6", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "12345:fake_token")
    monkeypatch.setattr(settings, "WEBAPP_URL", "http://fake")

    # Mock dependencies to prevent database and web server side-effects
    mock_init_db = AsyncMock()
    mock_bot_class = MagicMock()
    mock_dispatcher_class = MagicMock()
    
    # Mock bot instance and session close
    mock_bot_instance = MagicMock()
    mock_bot_instance.session = AsyncMock()
    mock_bot_instance.set_chat_menu_button = AsyncMock()
    mock_bot_class.return_value = mock_bot_instance
    
    # Mock dispatcher start_polling to raise StopTestException
    mock_dispatcher_instance = MagicMock()
    mock_dispatcher_instance.start_polling = AsyncMock(side_effect=StopTestException("stop"))
    mock_dispatcher_class.return_value = mock_dispatcher_instance

    # Mock web AppRunner and TCPSite
    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.cleanup = AsyncMock()
    mock_runner_class = MagicMock(return_value=mock_runner)

    mock_site = MagicMock()
    mock_site.start = AsyncMock()
    mock_site_class = MagicMock(return_value=mock_site)

    # Mock scheduler
    mock_scheduler = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    # Patch modules imported inside main()
    with patch("src.database.init_db.init_db", mock_init_db), \
         patch("src.main.Bot", mock_bot_class), \
         patch("src.main.Dispatcher", mock_dispatcher_class), \
         patch("src.services.scheduler.init_scheduler", AsyncMock()), \
         patch("src.services.scheduler.scheduler", mock_scheduler), \
         patch("aiohttp.web.AppRunner", mock_runner_class), \
         patch("aiohttp.web.TCPSite", mock_site_class), \
         patch("src.services.rate_limiter.start_queue_worker", AsyncMock()), \
         patch("src.services.rate_limiter.stop_queue_worker", AsyncMock()):
         
         from src.main import main
         try:
             await main()
         except StopTestException:
             pass

    # Verify that Bot was called with a session that has socket.AF_INET6 configured
    assert mock_bot_class.called
    called_args, called_kwargs = mock_bot_class.call_args
    assert "session" in called_kwargs
    session = called_kwargs["session"]
    assert session._connector_init["family"] == socket.AF_INET6


@pytest.mark.asyncio
async def test_bot_initialization_without_ipv6(monkeypatch):
    # Mock settings.FORCE_IPV6 to False
    monkeypatch.setattr(settings, "FORCE_IPV6", False)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "12345:fake_token")
    monkeypatch.setattr(settings, "WEBAPP_URL", "http://fake")

    # Mock dependencies
    mock_init_db = AsyncMock()
    mock_bot_class = MagicMock()
    mock_dispatcher_class = MagicMock()
    
    # Mock bot instance and session close
    mock_bot_instance = MagicMock()
    mock_bot_instance.session = AsyncMock()
    mock_bot_instance.set_chat_menu_button = AsyncMock()
    mock_bot_class.return_value = mock_bot_instance
    
    # Mock dispatcher start_polling to raise StopTestException
    mock_dispatcher_instance = MagicMock()
    mock_dispatcher_instance.start_polling = AsyncMock(side_effect=StopTestException("stop"))
    mock_dispatcher_class.return_value = mock_dispatcher_instance

    # Mock web AppRunner and TCPSite
    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.cleanup = AsyncMock()
    mock_runner_class = MagicMock(return_value=mock_runner)

    mock_site = MagicMock()
    mock_site.start = AsyncMock()
    mock_site_class = MagicMock(return_value=mock_site)

    # Mock scheduler
    mock_scheduler = MagicMock()
    mock_scheduler.shutdown = MagicMock()

    # Patch modules
    with patch("src.database.init_db.init_db", mock_init_db), \
         patch("src.main.Bot", mock_bot_class), \
         patch("src.main.Dispatcher", mock_dispatcher_class), \
         patch("src.services.scheduler.init_scheduler", AsyncMock()), \
         patch("src.services.scheduler.scheduler", mock_scheduler), \
         patch("aiohttp.web.AppRunner", mock_runner_class), \
         patch("aiohttp.web.TCPSite", mock_site_class), \
         patch("src.services.rate_limiter.start_queue_worker", AsyncMock()), \
         patch("src.services.rate_limiter.stop_queue_worker", AsyncMock()):
         
         from src.main import main
         try:
             await main()
         except StopTestException:
             pass

    # Verify that Bot was called without a custom session
    assert mock_bot_class.called
    called_args, called_kwargs = mock_bot_class.call_args
    assert "session" not in called_kwargs

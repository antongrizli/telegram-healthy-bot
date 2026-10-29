import os
import logging
from datetime import datetime, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.log_manager import (
    HealthCheckAccessFilter,
    setup_logging,
    get_logs_for_last_24h,
)
from src.utils.telegram_action import send_typing_action


def test_health_check_access_filter():
    f = HealthCheckAccessFilter()

    # 200 OK healthcheck -> should be suppressed (return False)
    rec_suppressed = logging.LogRecord(
        name="aiohttp.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s [29/Oct/2026:09:19:24 +0000] "GET /health HTTP/1.1" 200 938 "-" "Python-urllib/3.12"',
        args=("127.0.0.1",),
        exc_info=None
    )
    assert f.filter(rec_suppressed) is False

    # 503 error healthcheck -> should NOT be suppressed (return True)
    rec_error = logging.LogRecord(
        name="aiohttp.access",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg='%s [29/Oct/2026:09:19:24 +0000] "GET /health HTTP/1.1" 503 120 "-" "Python-urllib/3.12"',
        args=("127.0.0.1",),
        exc_info=None
    )
    assert f.filter(rec_error) is True

    # Other endpoints -> should NOT be suppressed
    rec_other = logging.LogRecord(
        name="aiohttp.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s [29/Oct/2026:09:19:24 +0000] "GET /api/user/settings HTTP/1.1" 200 450 "-" "Mozilla/5.0"',
        args=("127.0.0.1",),
        exc_info=None
    )
    assert f.filter(rec_other) is True


def test_get_logs_for_last_24h(tmp_path):
    log_file = tmp_path / "test_bot.log"
    now = datetime(2026, 10, 29, 12, 0, 0)

    # 26 hours ago (should be excluded)
    t_old = (now - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
    # 2 hours ago (should be included)
    t_recent1 = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    # 1 hour ago (should be included, followed by traceback)
    t_recent2 = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    content = (
        f"{t_old},123 [INFO] src.main: Old event that should be filtered out\n"
        f"Traceback of old event:\n"
        f"  File 'old.py', line 1\n"
        f"{t_recent1},456 [INFO] src.main: Recent event 1\n"
        f"{t_recent2},789 [ERROR] src.main: Recent error with traceback\n"
        f"Traceback (most recent call last):\n"
        f"  File 'test.py', line 10, in foo\n"
        f"ValueError: test error\n"
    )
    log_file.write_text(content, encoding="utf-8")

    result_bytes = get_logs_for_last_24h(str(log_file), hours=24, now=now)
    result_text = result_bytes.decode("utf-8")

    # Old event must NOT be in result
    assert "Old event that should be filtered out" not in result_text
    assert "old.py" not in result_text

    # Recent events and traceback MUST be in result
    assert "Recent event 1" in result_text
    assert "Recent error with traceback" in result_text
    assert "ValueError: test error" in result_text
    assert "Total lines: 5" in result_text


def test_get_logs_missing_file():
    res = get_logs_for_last_24h("/non/existent/path/to/logfile.log")
    assert b"No log file found" in res


@pytest.mark.asyncio
async def test_send_typing_action_athrow_safe():
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()

    # When an exception is thrown inside send_typing_action, it must propagate cleanly
    # without raising RuntimeError: generator didn't stop after athrow()
    with pytest.raises(TimeoutError):
        async with send_typing_action(bot, 12345):
            raise TimeoutError("Simulated timeout")


@pytest.mark.asyncio
async def test_cmd_admin_download_logs(monkeypatch):
    from src.handlers.admin import cmd_admin_download_logs
    from src.config import settings

    # Setup admin user id
    monkeypatch.setattr(settings, "ADMIN_USER_IDS", [999])

    # 1. Non-admin user
    msg_non_admin = AsyncMock()
    msg_non_admin.from_user.id = 111
    state = AsyncMock()

    with patch("src.handlers.admin.crud.get_user", AsyncMock(return_value=None)):
        await cmd_admin_download_logs(msg_non_admin, state, "en")
        msg_non_admin.answer.assert_called_once()
        assert "Only admins" in msg_non_admin.answer.call_args[0][0]

    # 2. Admin user
    msg_admin = AsyncMock()
    msg_admin.from_user.id = 999
    status_msg = AsyncMock()
    msg_admin.answer = AsyncMock(return_value=status_msg)
    msg_admin.answer_document = AsyncMock()

    with patch("src.handlers.admin.crud.get_user", AsyncMock(return_value=None)), \
         patch("src.utils.log_manager.get_logs_for_last_24h", return_value=b"mock log bytes"):
        await cmd_admin_download_logs(msg_admin, state, "ru")

        # Must send preparing message, then answer_document with log file, and delete status message
        assert msg_admin.answer.called
        assert msg_admin.answer_document.called
        call_kwargs = msg_admin.answer_document.call_args.kwargs
        assert call_kwargs["document"].filename.startswith("healthy_bot_logs_24h_")
        status_msg.delete.assert_called_once()


def test_redact_secrets():
    from src.utils.log_manager import redact_secrets

    # Telegram bot token
    raw_tg = "Failed request to https://api.telegram.org/bot123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ012345678/sendMessage"
    redacted = redact_secrets(raw_tg)
    assert "123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ012345678" not in redacted
    assert "[REDACTED_TELEGRAM_TOKEN]" in redacted

    # Gemini API key
    raw_gemini = "Gemini API failed with key AIzaSyA12345678901234567890123456789012"
    redacted_gemini = redact_secrets(raw_gemini)
    assert "AIzaSyA12345678901234567890123456789012" not in redacted_gemini
    assert "[REDACTED_GEMINI_KEY]" in redacted_gemini

    # Database URL
    raw_db = "Connection failed: postgresql+asyncpg://botuser:super_secret_pw@localhost:5432/botdb"
    redacted_db = redact_secrets(raw_db)
    assert "super_secret_pw" not in redacted_db
    assert "botuser:***@localhost" in redacted_db


def test_secret_redaction_filter():
    from src.utils.log_manager import SecretRedactionFilter

    f = SecretRedactionFilter()
    rec = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="Error with token %s and key %s",
        args=("123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ012345678", "AIzaSyA12345678901234567890123456789012"),
        exc_info=None
    )
    assert f.filter(rec) is True
    assert "123456789:ABC" not in rec.args[0]
    assert "AIzaSy" not in rec.args[1]


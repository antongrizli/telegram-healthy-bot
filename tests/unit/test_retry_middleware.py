import pytest
from unittest.mock import AsyncMock
from aiogram import Bot
from aiogram.methods import SendMessage, GetUpdates
from aiogram.exceptions import TelegramNetworkError
from src.middlewares.retry import TelegramRetryRequestMiddleware

pytestmark = pytest.mark.asyncio


async def test_retry_middleware_success_on_first_try():
    mw = TelegramRetryRequestMiddleware(max_retries=2, delay=0.01)
    method = SendMessage(chat_id=123, text="hi")
    bot = AsyncMock(spec=Bot)
    make_request = AsyncMock(return_value="ok")

    res = await mw(make_request, bot, method)
    assert res == "ok"
    assert make_request.await_count == 1


async def test_retry_middleware_retries_transient_network_error():
    mw = TelegramRetryRequestMiddleware(max_retries=2, delay=0.01)
    method = SendMessage(chat_id=123, text="hi")
    bot = AsyncMock(spec=Bot)

    call_count = 0
    async def mock_request(b, m):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TelegramNetworkError(method=m, message="Request timeout error")
        return "success"

    res = await mw(mock_request, bot, method)
    assert res == "success"
    assert call_count == 2


async def test_retry_middleware_raises_after_max_retries():
    mw = TelegramRetryRequestMiddleware(max_retries=2, delay=0.01)
    method = SendMessage(chat_id=123, text="hi")
    bot = AsyncMock(spec=Bot)
    make_request = AsyncMock(side_effect=TelegramNetworkError(method=method, message="Request timeout error"))

    with pytest.raises(TelegramNetworkError):
        await mw(make_request, bot, method)
    assert make_request.await_count == 3  # initial + 2 retries


async def test_retry_middleware_skips_get_updates():
    mw = TelegramRetryRequestMiddleware(max_retries=2, delay=0.01)
    method = GetUpdates()
    bot = AsyncMock(spec=Bot)
    make_request = AsyncMock(return_value=[])

    res = await mw(make_request, bot, method)
    assert res == []
    assert make_request.await_count == 1

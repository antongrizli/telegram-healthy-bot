import asyncio
import logging
from typing import Any
from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    GetFile,
    GetMe,
    SendChatAction,
)
from aiogram.methods.get_updates import GetUpdates
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)

IDEMPOTENT_METHODS = (
    EditMessageText,
    EditMessageReplyMarkup,
    DeleteMessage,
    SendChatAction,
    AnswerCallbackQuery,
    GetFile,
    GetMe,
)


class TelegramRetryRequestMiddleware(BaseRequestMiddleware):
    """
    Middleware that intercepts outgoing Bot API requests and retries transient
    TelegramNetworkError (socket timeouts, connection resets) with exponential backoff
    strictly for idempotent methods. Non-idempotent methods (e.g. SendMessage) are not
    retried to prevent duplicate messages or double-actions.
    """

    def __init__(self, max_retries: int = 2, delay: float = 1.0, backoff: float = 2.0):
        self.max_retries = max_retries
        self.delay = delay
        self.backoff = backoff

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Response[Any]:
        # Do not retry GetUpdates polling requests or non-idempotent methods (like SendMessage)
        if isinstance(method, GetUpdates) or not isinstance(method, IDEMPOTENT_METHODS):
            return await make_request(bot, method)

        attempt = 0
        current_delay = self.delay
        while True:
            try:
                return await make_request(bot, method)
            except TelegramNetworkError as e:
                attempt += 1
                if attempt > self.max_retries:
                    logger.error(
                        "Telegram network error on %s after %d retries: %s",
                        method.__class__.__name__, self.max_retries, e
                    )
                    raise
                logger.warning(
                    "Transient Telegram network error on %s (attempt %d/%d): %s. Retrying in %.2fs...",
                    method.__class__.__name__, attempt, self.max_retries + 1, e, current_delay
                )
                await asyncio.sleep(current_delay)
                current_delay *= self.backoff

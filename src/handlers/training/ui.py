"""Shared Telegram UI helpers for Training v2 handlers."""
from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

logger = logging.getLogger(__name__)


async def edit_or_send(
    message: Message,
    text: str,
    reply_markup: Any = None,
    parse_mode: Optional[str] = "HTML",
) -> int:
    """Edit ``message`` in place; if Telegram refuses (deleted, too old, not editable) send a fresh one instead.

    Returns the id of the message now showing the screen, so callers can re-anchor on it. "Message is not modified"
    is not an error (the screen is already current). Only ``TelegramBadRequest`` is handled here: network errors and
    anything unexpected still propagate to the dispatcher's error handling instead of being silently swallowed.
    """
    try:
        await message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return message.message_id
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return message.message_id
        logger.info("Cannot edit message %s (%s); sending a new one", message.message_id, exc)
    sent = await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
    return getattr(sent, "message_id", message.message_id)

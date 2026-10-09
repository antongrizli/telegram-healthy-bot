"""Safe ChatAction helpers."""
import contextlib
from aiogram.utils.chat_action import ChatActionSender


@contextlib.asynccontextmanager
async def send_typing_action(bot, chat_id: int):
    """
    Safely sends periodic typing actions while processing, gracefully falling back
    if the bot client lacks chat action capabilities (e.g. in test stubs).
    """
    if bot is not None and hasattr(bot, "send_chat_action"):
        try:
            async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
                yield
                return
        except Exception:
            pass
    yield

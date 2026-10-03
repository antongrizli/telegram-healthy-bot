"""Only confirmed uneditable messages justify a replacement delivery."""
from aiogram.exceptions import TelegramBadRequest


async def try_edit(method, *args, **kwargs):
    try:
        await method(*args, **kwargs)
        return True
    except TelegramBadRequest as exc:
        reason = exc.message.lower()
        if 'message is not modified' in reason:
            return True
        if any(reason_part in reason for reason_part in (
            'message to edit not found', "message can't be edited",
            'message can not be edited', 'message_id_invalid',
        )):
            return False
        raise

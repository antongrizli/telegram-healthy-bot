from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from src.config import settings
from src.utils.i18n_locales import get_text

class AdminCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        lang = data.get("user_language", "en")
        
        is_admin = False
        if user and user.id in settings.ADMIN_USER_IDS:
            is_admin = True
            
        db_user = data.get("db_user")
        if db_user and db_user.is_admin:
            is_admin = True
            
        if is_admin:
            return await handler(event, data)
            
        if isinstance(event, Message):
            await event.answer(get_text("admin_only", lang))
        elif isinstance(event, CallbackQuery):
            await event.answer(get_text("admin_only", lang), show_alert=True)
        return

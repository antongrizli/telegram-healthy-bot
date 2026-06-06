from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from src.database.connection import AsyncSessionLocal
from src.database import crud

class InteractionLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        db_user = data.get("db_user")
        if user and db_user:
            message_type = "unknown"
            if isinstance(event, Message):
                if event.text:
                    message_type = "text"
                elif event.photo:
                    message_type = "photo"
                elif event.voice:
                    message_type = "voice"
                else:
                    message_type = "media"
            elif isinstance(event, CallbackQuery):
                message_type = "callback_query"
                
            async with AsyncSessionLocal() as db:
                # Log interaction for admin analytics
                await crud.log_message_stat(db, user.id, message_type)
                
        return await handler(event, data)

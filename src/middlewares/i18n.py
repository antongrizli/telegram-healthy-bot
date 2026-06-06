from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from src.database.connection import AsyncSessionLocal
from src.database import crud

class LanguageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        language = "en"
        db_user = None
        
        if user:
            async with AsyncSessionLocal() as db:
                db_user = await crud.get_user(db, user.id)
                if db_user:
                    language = db_user.language
                    
        data["db_user"] = db_user
        data["user_language"] = language
        
        return await handler(event, data)

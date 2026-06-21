from typing import Callable, Dict, Any, Awaitable
import logging
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, MenuButtonWebApp, WebAppInfo
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.config import settings

logger = logging.getLogger(__name__)

# Cache of user_id -> language to avoid repeated set_chat_menu_button calls
_user_menu_button_cache = {}

MENU_BUTTON_TEXTS = {
    "en": "Statistics",
    "ru": "Статистика",
    "uk": "Статистика",
    "pl": "Statystyki",
    "de": "Statistik",
    "tr": "İstatistikler",
    "es": "Estadísticas"
}

async def update_user_menu_button(bot, chat_id: int, language: str):
    cached_lang = _user_menu_button_cache.get(chat_id)
    if cached_lang == language:
        return
        
    text = MENU_BUTTON_TEXTS.get(language, "Statistics")
    try:
        await bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonWebApp(
                text=text,
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}")
            )
        )
        _user_menu_button_cache[chat_id] = language
        logger.info(f"Updated chat menu button for user {chat_id} to '{text}' ({language})")
    except Exception as e:
        logger.error(f"Failed to update chat menu button for user {chat_id}: {e}")

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
            # Fallback to Telegram user language if supported
            tg_lang = user.language_code
            if tg_lang and tg_lang in MENU_BUTTON_TEXTS:
                language = tg_lang

            async with AsyncSessionLocal() as db:
                db_user = await crud.get_user(db, user.id)
                if db_user:
                    language = db_user.language
            
            # Update menu button language
            bot = data.get("bot") or getattr(event, "bot", None)
            if bot:
                await update_user_menu_button(bot, user.id, language)
                    
        data["db_user"] = db_user
        data["user_language"] = language
        
        return await handler(event, data)


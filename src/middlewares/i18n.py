import asyncio
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
    "en": "Today",
    "ru": "Сегодня",
    "uk": "Сьогодні",
    "pl": "Dzisiaj",
    "de": "Heute",
    "tr": "Bugün",
    "es": "Hoy"
}

async def update_user_menu_button(bot, chat_id: int, language: str):
    cached_lang = _user_menu_button_cache.get(chat_id)
    if cached_lang == language:
        return
        
    text = MENU_BUTTON_TEXTS.get(language, "Today")
    try:
        await asyncio.wait_for(
            bot.set_chat_menu_button(
                chat_id=chat_id,
                menu_button=MenuButtonWebApp(
                    text=text,
                    web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}")
                )
            ),
            timeout=5.0
        )
        _user_menu_button_cache[chat_id] = language
        logger.info(f"Updated chat menu button for user {chat_id} to '{text}' ({language})")
    except Exception as e:
        logger.warning(f"Could not update chat menu button for user {chat_id}: {e}")

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
                    if crud.is_user_deleted(db_user):
                        logger.info("User %s blocked bot > 3 days ago; deleting user and records", user.id)
                        await crud.delete_user(db, user.id)
                        db_user = None
                    else:
                        if crud.is_user_bot_blocked(db_user):
                            await crud.mark_user_unblocked(db, user.id)
                            db_user.blocked_at = None
                            db_user.notifications_enabled = True
                            bot = data.get("bot") or getattr(event, "bot", None)
                            if bot:
                                from src.services.scheduler import reschedule_user_jobs
                                reschedule_user_jobs(bot, db_user)
                        language = db_user.language
            
            # Update menu button language in background without blocking message pipeline
            bot = data.get("bot") or getattr(event, "bot", None)
            if bot and (db_user is None or not db_user.is_blocked):
                try:
                    asyncio.create_task(update_user_menu_button(bot, user.id, language))
                except RuntimeError:
                    await update_user_menu_button(bot, user.id, language)
                    
        data["db_user"] = db_user
        data["user_language"] = language
        
        return await handler(event, data)

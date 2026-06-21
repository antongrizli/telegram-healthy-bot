import logging
from aiohttp import web
from aiogram.utils.web_app import safe_parse_webapp_init_data
from src.config import settings

logger = logging.getLogger(__name__)

def validate_init_data(request: web.Request) -> int:
    """
    Validates the Telegram WebApp initData query string sent in the X-Telegram-Init-Data header.
    Returns the user's telegram_id if valid, otherwise raises HTTPUnauthorized.
    """
    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        logger.warning("Missing X-Telegram-Init-Data header")
        raise web.HTTPUnauthorized(text="Missing init data header")
        
    try:
        # Validate init_data against the telegram bot token
        web_app_init_data = safe_parse_webapp_init_data(
            token=settings.TELEGRAM_BOT_TOKEN,
            init_data=init_data
        )
        
        # Check if auth_date is older than 24 hours (86400 seconds) to prevent replay attacks
        from datetime import datetime, UTC
        now = datetime.now(UTC)
        auth_date = web_app_init_data.auth_date
        if auth_date.tzinfo is None:
            auth_date = auth_date.replace(tzinfo=UTC)
            
        age = (now - auth_date).total_seconds()
        if age > 86400 or age < -300:  # Allow 5 minutes clock skew in the future
            logger.warning(f"Expired or invalid auth_date: {auth_date} (age: {age}s)")
            raise web.HTTPUnauthorized(text="Authentication data has expired")
            
        return web_app_init_data.user.id
    except web.HTTPUnauthorized:
        raise
    except ValueError as e:
        logger.warning(f"Invalid init data signature: {e}")
        raise web.HTTPUnauthorized(text="Invalid init data signature")
    except Exception as e:
        logger.error(f"Unexpected authentication error: {e}")
        raise web.HTTPUnauthorized(text="Authentication failed")


import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from src.config import settings
from src.handlers import common, profile, food, weight, admin, callbacks
from src.middlewares.i18n import LanguageMiddleware
from src.middlewares.logging import InteractionLoggingMiddleware
from src.middlewares.admin_check import AdminCheckMiddleware
from src.services import scheduler

async def main():
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    logger = logging.getLogger(__name__)

    from src.database.init_db import init_db
    await init_db()

    logger.info("Initializing Bot and Dispatcher...")
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Set persistent Menu Button next to the message input field
    try:
        from aiogram.types import MenuButtonWebApp, WebAppInfo
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Progress",
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}")
            )
        )
        logger.info("Chat menu button configured successfully.")
    except Exception as e:
        logger.error(f"Failed to set chat menu button: {e}")

    # Register Global Telemetry and Localization Middlewares
    from src.middlewares.media_group import MediaGroupMiddleware
    from src.middlewares.registration_check import RegistrationCheckMiddleware
    dp.message.outer_middleware(MediaGroupMiddleware())
    dp.message.outer_middleware(LanguageMiddleware())
    dp.callback_query.outer_middleware(LanguageMiddleware())
    dp.message.outer_middleware(RegistrationCheckMiddleware())
    dp.callback_query.outer_middleware(RegistrationCheckMiddleware())
    dp.message.outer_middleware(InteractionLoggingMiddleware())
    dp.callback_query.outer_middleware(InteractionLoggingMiddleware())

    # Attach Router-level Security middleware for Admin namespace
    admin.router.message.middleware(AdminCheckMiddleware())
    admin.router.callback_query.middleware(AdminCheckMiddleware())
    
    # Register handlers routers
    dp.include_router(admin.router)
    dp.include_router(profile.router)
    dp.include_router(food.router)
    dp.include_router(weight.router)
    dp.include_router(callbacks.router)
    dp.include_router(common.router)  # Handles fallbacks and start

    logger.info("Starting APScheduler background tasks...")
    await scheduler.init_scheduler(bot)

    logger.info("Starting aiohttp WebApp server on port 8080...")
    from aiohttp import web
    from src.webapp.server import create_app
    webapp = create_app(bot)
    webapp_runner = web.AppRunner(webapp)
    await webapp_runner.setup()
    webapp_site = web.TCPSite(webapp_runner, "0.0.0.0", 8080)
    await webapp_site.start()

    from src.services.rate_limiter import start_queue_worker, stop_queue_worker
    queue_worker_task = asyncio.create_task(start_queue_worker(bot, dp.storage))

    logger.info("Bot is starting polling...")
    from aiogram.exceptions import TelegramNetworkError
    
    try:
        retry_delay = 5
        while True:
            try:
                await dp.start_polling(bot)
                break  # Normal exit (e.g. shutdown signal received)
            except (TelegramNetworkError, asyncio.TimeoutError) as e:
                logger.error(f"Telegram connection timed out or failed: {e}. Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
    finally:
        await bot.session.close()
        scheduler.scheduler.shutdown()
        await stop_queue_worker()
        await webapp_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())

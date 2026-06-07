import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from src.config import settings
from src.database.connection import engine
from src.database.models import Base
from src.handlers import common, profile, food, weight, admin
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

    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        # Auto-create tables if they do not exist
        await conn.run_sync(Base.metadata.create_all)
        
        # Add timezone column to existing PostgreSQL users table if it doesn't exist
        import sqlalchemy as sa
        try:
            await conn.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) NOT NULL DEFAULT 'UTC'"))
        except Exception as e:
            logger.info(f"Timezone column check/migration skipped or handled: {e}")

    logger.info("Initializing Bot and Dispatcher...")
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Register Global Telemetry and Localization Middlewares
    dp.message.outer_middleware(LanguageMiddleware())
    dp.callback_query.outer_middleware(LanguageMiddleware())
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
    dp.include_router(common.router)  # Handles fallbacks and start

    logger.info("Starting APScheduler background tasks...")
    await scheduler.init_scheduler(bot)

    logger.info("Bot is starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.scheduler.shutdown()

if __name__ == "__main__":
    asyncio.run(main())

import logging
import sqlalchemy as sa
from src.database.connection import engine
from src.database.models import Base

logger = logging.getLogger(__name__)

async def init_db():
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        # Auto-create tables if they do not exist
        await conn.run_sync(Base.metadata.create_all)
        
        # Add timezone column to existing PostgreSQL users table if it doesn't exist
        try:
            await conn.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) NOT NULL DEFAULT 'UTC'"))
        except Exception as e:
            logger.info(f"Timezone column check/migration skipped or handled: {e}")

        # Add meal_type column to existing PostgreSQL food_logs table if it doesn't exist
        try:
            await conn.execute(sa.text("ALTER TABLE food_logs ADD COLUMN IF NOT EXISTS meal_type VARCHAR(20) NOT NULL DEFAULT 'food'"))
        except Exception as e:
            logger.info(f"meal_type column check/migration skipped or handled: {e}")

        # Add retry_count, next_retry_at, and last_error columns to existing PostgreSQL ai_request_queue table if they do not exist
        try:
            await conn.execute(sa.text("ALTER TABLE ai_request_queue ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0"))
            await conn.execute(sa.text("ALTER TABLE ai_request_queue ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP WITHOUT TIME ZONE"))
            await conn.execute(sa.text("ALTER TABLE ai_request_queue ADD COLUMN IF NOT EXISTS last_error TEXT"))
        except Exception as e:
            logger.info(f"ai_request_queue retry columns check/migration skipped or handled: {e}")

        # Add gamification columns to existing PostgreSQL users table if they don't exist
        try:
            await conn.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_streak INTEGER NOT NULL DEFAULT 0"))
            await conn.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS streak_freezes_left INTEGER NOT NULL DEFAULT 1"))
            await conn.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_freeze_used_at TIMESTAMP WITHOUT TIME ZONE"))
        except Exception as e:
            logger.info(f"Users gamification columns check/migration skipped or handled: {e}")

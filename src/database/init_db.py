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

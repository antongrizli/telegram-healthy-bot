"""Idempotent schema upgrades that preserve users' scheduling choices."""
import logging
import sqlalchemy as sa
from src.database.connection import engine
from src.database.models import Base

logger = logging.getLogger(__name__)

STARTUP_COLUMNS = {
    "users": {
        "ux_preferences": "JSON NOT NULL DEFAULT '{}'",
        "timezone": "VARCHAR(50) NOT NULL DEFAULT 'UTC'",
        "current_streak": "INTEGER NOT NULL DEFAULT 0",
        "streak_freezes_left": "INTEGER NOT NULL DEFAULT 1",
        "last_freeze_used_at": "TIMESTAMP",
        "weekly_report_day": "INTEGER NOT NULL DEFAULT 6",
        "monthly_report_day": "INTEGER NOT NULL DEFAULT 1",
    },
    "food_logs": {"meal_type": "VARCHAR(20) NOT NULL DEFAULT 'food'"},
    "ai_request_queue": {
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "next_retry_at": "TIMESTAMP",
        "last_error": "TEXT",
    },
}

async def init_db():
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(sa.text("SELECT pg_advisory_xact_lock(72139402)"))
        await conn.run_sync(Base.metadata.create_all)
        for table, definitions in STARTUP_COLUMNS.items():
            existing = await conn.run_sync(
                lambda c, name=table: {col["name"] for col in sa.inspect(c).get_columns(name)}
            )
            for column, definition in definitions.items():
                if column not in existing:
                    # Identifiers and definitions come only from the static map above.
                    await conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                    logger.info("Added schema column %s.%s", table, column)
        # 0 means Monday. Never reinterpret existing values as migration markers.
        # Schema errors propagate instead of starting against a partial schema.

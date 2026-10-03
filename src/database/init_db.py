"""Versioned schema upgrades through Alembic.

The former startup DDL (create_all + idempotent ALTERs) now lives in the baseline
revision ``migrations/versions/0001_baseline.py``. Add new schema changes as new
revisions (``alembic revision -m "..."``), never here.
"""
import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from src.database.connection import engine

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


def alembic_config(connection=None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    # Absolute path: the process working directory is not guaranteed to be the repo root.
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _upgrade(connection) -> None:
    command.upgrade(alembic_config(connection), "head")


async def init_db():
    logger.info("Upgrading database schema with Alembic...")
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            # Serialise concurrent starts; the lock is released when the transaction ends.
            await conn.execute(sa.text("SELECT pg_advisory_xact_lock(72139402)"))
        # Schema errors propagate instead of starting against a partial schema.
        await conn.run_sync(_upgrade)

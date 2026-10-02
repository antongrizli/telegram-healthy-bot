"""Real PostgreSQL checks, enabled by TEST_POSTGRES_URL (CI uses a disposable DB)."""
import asyncio
import os
from datetime import datetime, UTC, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import settings
from src.database import crud, init_db
from src.database.models import AiRequestLog, AiRequestAttempt


@pytest_asyncio.fixture
async def postgres_engine():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL is not configured")
    schema = "reliability_" + uuid4().hex
    admin = create_async_engine(url)
    isolated = None
    try:
        async with admin.begin() as conn:
            await conn.execute(sa.schema.CreateSchema(schema))
        isolated = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        yield isolated
    finally:
        if isolated:
            await isolated.dispose()
        # Remove only the exact disposable schema created by this fixture.
        async with admin.begin() as conn:
            await conn.execute(sa.schema.DropSchema(schema, cascade=True, if_exists=True))
        await admin.dispose()


@pytest.mark.asyncio
async def test_postgres_atomic_quota_across_independent_sessions(postgres_engine, monkeypatch):
    monkeypatch.setattr(init_db, "engine", postgres_engine)
    await init_db.init_db()
    monkeypatch.setattr(settings, "AI_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "AI_USER_REQUESTS_PER_MINUTE", 100)
    sessions = async_sessionmaker(postgres_engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(AiRequestLog(user_id=991, request_type="legacy",
                           executed_at=datetime.now(UTC).replace(tzinfo=None)-timedelta(seconds=10)))
        await db.commit()
    async def reserve():
        async with sessions() as db:
            return await crud.reserve_ai_attempt(db, 991, "test")
    outcomes = await asyncio.gather(*(reserve() for _ in range(12)))
    assert sum(result is None for result in outcomes) == 1
    assert all(result is None or result[0] == "minute" for result in outcomes)
    async with sessions() as db:
        assert (await db.execute(sa.select(sa.func.count(AiRequestAttempt.id)))).scalar_one() == 1


@pytest.mark.asyncio
async def test_postgres_legacy_upgrade_is_idempotent(postgres_engine, monkeypatch):
    monkeypatch.setattr(init_db, "engine", postgres_engine)
    async with postgres_engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, name VARCHAR, weekly_report_day INTEGER)"))
        await conn.execute(sa.text("INSERT INTO users VALUES (991, 'Test', 0)"))
    await init_db.init_db()
    await init_db.init_db()
    async with postgres_engine.connect() as conn:
        assert (await conn.execute(sa.text("SELECT weekly_report_day, monthly_report_day, timezone FROM users"))).one() == (0, 1, "UTC")

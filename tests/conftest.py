import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.database.models import Base

@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Fixture to provide an async in-memory SQLite database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with AsyncSessionLocal() as session:
        yield session
        await session.close()
        
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
def mock_bot():
    """Mock for the aiogram Bot class."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.send_chat_action = AsyncMock()
    return bot

@pytest.fixture
def mock_gemini_client(monkeypatch):
    """Fixture to mock the Gemini Client and generate content calls."""
    mock_client = MagicMock()
    mock_models = MagicMock()
    mock_response = MagicMock()
    
    mock_response.text = "Mocked AI Response"
    mock_models.generate_content.return_value = mock_response
    mock_client.models = mock_models

    async def aio_generate_content(*args, **kwargs):
        res = mock_models.generate_content(*args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res

    mock_client.aio = MagicMock()
    mock_client.aio.models = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(side_effect=aio_generate_content)
    
    # Patch the client in gemini service
    monkeypatch.setattr("src.services.gemini.client", mock_client)
    return mock_client

@pytest.fixture(autouse=True)
def patch_async_session_local(monkeypatch, db_session):
    """Autouse fixture to intercept and mock AsyncSessionLocal across all modules."""
    class AsyncSessionContextManager:
        async def __aenter__(self):
            return db_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    modules = [
        "src.database.connection",
        "src.handlers.weight",
        "src.handlers.profile",
        "src.handlers.food",
        "src.handlers.common",
        "src.handlers.admin",
        "src.services.scheduler",
        "src.services.rate_limiter",
        "src.services.ai_quota",
        "src.services.gamification",
        "src.services.briefing",
        "src.handlers.ux", "src.webapp.ux", "src.webapp.server", "src.middlewares.logging"
    ]
    
    for mod in modules:
        try:
            monkeypatch.setattr(f"{mod}.AsyncSessionLocal", AsyncSessionContextManager)
        except AttributeError:
            pass

@pytest.fixture(autouse=True)
def reset_rate_limiter_state():
    from src.services import rate_limiter
    rate_limiter._worker_shutting_down = False
    rate_limiter._worker_last_error = None
    yield
    rate_limiter._worker_shutting_down = False
    rate_limiter._worker_last_error = None


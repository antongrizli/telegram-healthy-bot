import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.database.models import Base

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    yield loop
    loop.close()

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
        "src.services.gamification",
        "src.services.briefing"
    ]
    
    for mod in modules:
        try:
            monkeypatch.setattr(f"{mod}.AsyncSessionLocal", AsyncSessionContextManager)
        except AttributeError:
            pass

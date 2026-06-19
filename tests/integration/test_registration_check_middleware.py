import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, User, Chat
from aiogram.fsm.context import FSMContext
from src.middlewares.registration_check import RegistrationCheckMiddleware
from src.handlers.profile import ProfileStatesGroup
from src.database.models import User as DbUser

pytestmark = pytest.mark.asyncio

def make_mock_message(text: str, user_id: int = 12345):
    from_user = User(id=user_id, is_bot=False, first_name="Test", last_name="User", username="testuser")
    chat = Chat(id=user_id, type="private")
    
    message = MagicMock(spec=Message)
    message.from_user = from_user
    message.chat = chat
    message.text = text
    message.bot = MagicMock()
    message.bot.send_message = AsyncMock()
    message.answer = AsyncMock()
    return message

def make_mock_callback_query(message_text: str, user_id: int = 12345):
    from_user = User(id=user_id, is_bot=False, first_name="Test", last_name="User", username="testuser")
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = from_user
    cb.message = make_mock_message(message_text, user_id)
    cb.answer = AsyncMock()
    return cb

@pytest.fixture
def mock_state():
    state = MagicMock(spec=FSMContext)
    state.get_state = AsyncMock(return_value=None)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    return state

async def test_middleware_allows_registered_user(mock_state):
    middleware = RegistrationCheckMiddleware()
    handler = AsyncMock(return_value="allowed")
    message = make_mock_message("📝 Log Food")
    
    db_user = DbUser(telegram_id=12345, is_admin=False, is_blocked=False, language="en")
    data = {
        "db_user": db_user,
        "user_language": "en",
        "state": mock_state
    }
    
    result = await middleware(handler, message, data)
    assert result == "allowed"
    handler.assert_called_once_with(message, data)
    mock_state.set_state.assert_not_called()

async def test_middleware_allows_start_command(mock_state):
    middleware = RegistrationCheckMiddleware()
    handler = AsyncMock(return_value="allowed")
    message = make_mock_message("/start")
    
    data = {
        "db_user": None,
        "user_language": "en",
        "state": mock_state
    }
    
    result = await middleware(handler, message, data)
    assert result == "allowed"
    handler.assert_called_once_with(message, data)
    mock_state.set_state.assert_not_called()

async def test_middleware_allows_profile_setup_states(mock_state):
    middleware = RegistrationCheckMiddleware()
    handler = AsyncMock(return_value="allowed")
    message = make_mock_message("John")
    
    # User is in profile setup name state
    mock_state.get_state.return_value = "ProfileStatesGroup:name"
    
    data = {
        "db_user": None,
        "user_language": "en",
        "state": mock_state
    }
    
    result = await middleware(handler, message, data)
    assert result == "allowed"
    handler.assert_called_once_with(message, data)
    mock_state.set_state.assert_not_called()

async def test_middleware_blocks_and_redirects_unregistered_user(mock_state):
    middleware = RegistrationCheckMiddleware()
    handler = AsyncMock(return_value="allowed")
    message = make_mock_message("📝 Log Food")
    
    data = {
        "db_user": None,
        "user_language": "en",
        "state": mock_state
    }
    
    result = await middleware(handler, message, data)
    
    # Handler should NOT be called
    handler.assert_not_called()
    assert result is None
    
    # Should have set FSM state to language to force registration
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.language)
    message.answer.assert_called_once()
    assert "welcome" in message.answer.call_args[0][0].lower()

async def test_middleware_blocks_and_redirects_unregistered_callback(mock_state):
    middleware = RegistrationCheckMiddleware()
    handler = AsyncMock(return_value="allowed")
    cb = make_mock_callback_query("clicked some button")
    
    data = {
        "db_user": None,
        "user_language": "en",
        "state": mock_state
    }
    
    result = await middleware(handler, cb, data)
    
    handler.assert_not_called()
    assert result is None
    
    # Should set FSM state and answer callback
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.language)
    cb.answer.assert_called_once()
    cb.message.answer.assert_called_once()

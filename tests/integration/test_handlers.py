import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from src.handlers.weight import WeightState, start_weight_logging, process_weight_input
from src.handlers.profile import ProfileStatesGroup, start_profile_setup, process_name
from src.database import crud

pytestmark = pytest.mark.asyncio

def make_mock_message(text: str, user_id: int = 12345, username: str = "testuser"):
    from_user = User(id=user_id, is_bot=False, first_name="Test", last_name="User", username=username)
    chat = Chat(id=user_id, type="private")
    
    message = MagicMock(spec=Message)
    message.from_user = from_user
    message.chat = chat
    message.text = text
    message.bot = MagicMock()
    message.bot.send_message = AsyncMock()
    message.answer = AsyncMock()
    return message

@pytest.fixture
def mock_state():
    state = MagicMock(spec=FSMContext)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    state.set_data = AsyncMock()
    return state

async def test_start_weight_logging(mock_state):
    message = make_mock_message("Log Weight")
    await start_weight_logging(message, mock_state, "en")
    
    mock_state.set_state.assert_called_once_with(WeightState.waiting_for_weight)
    message.answer.assert_called_once()
    assert "weight" in message.answer.call_args[0][0].lower()

async def test_process_weight_input_success(db_session, mock_state):
    await crud.create_or_update_user(
        db_session,
        telegram_id=12345,
        name="John",
        sex="male",
        age=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="light",
        goal="lose_weight",
        target_calories=2080,
        target_protein=156,
        target_fat=69,
        target_carb=208
    )

    message = make_mock_message("78.5", user_id=12345)
    await process_weight_input(message, mock_state, "en")
    
    mock_state.clear.assert_called_once()
    message.answer.assert_called_once()
    assert "78.5 kg" in message.answer.call_args[0][0]
    
    db_user = await crud.get_user(db_session, 12345)
    assert db_user.weight_kg == 78.5
    assert db_user.target_calories == 2062

async def test_process_weight_input_invalid(mock_state):
    message = make_mock_message("invalid_weight")
    await process_weight_input(message, mock_state, "en")
    
    mock_state.clear.assert_not_called()
    message.answer.assert_called_once()
    assert "invalid" in message.answer.call_args[0][0].lower()

async def test_start_profile_setup(mock_state):
    message = make_mock_message("⚙️ Set Up Profile")
    await start_profile_setup(message, mock_state, "en")
    
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.name)
    message.answer.assert_called_once()
    assert "name" in message.answer.call_args[0][0].lower()

async def test_process_name(mock_state):
    message = make_mock_message("John Doe")
    await process_name(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once_with(name="John Doe")
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.sex)
    message.answer.assert_called_once()
    assert "sex" in message.answer.call_args[0][0].lower()

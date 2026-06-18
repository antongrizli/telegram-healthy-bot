import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext

from src.handlers.weight import WeightState, start_weight_logging, process_weight_input
from src.handlers.profile import (
    ProfileStatesGroup, start_profile_setup, process_name, process_sex, process_notifications,
    start_profile_deletion, process_confirm_delete,
    cancel_profile_deletion, process_invalid_delete_confirm
)
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

async def test_start_profile_deletion(mock_state):
    message = make_mock_message("🗑️ Delete Profile")
    await start_profile_deletion(message, mock_state, "en")
    
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.confirm_delete)
    message.answer.assert_called_once()
    assert "delete" in message.answer.call_args[0][0].lower()

async def test_process_confirm_delete(db_session, mock_state, mocker):
    mock_remove_jobs = mocker.patch("src.services.scheduler.remove_user_jobs")
    
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
    
    message = make_mock_message("⚠️ Yes, Delete Everything", user_id=12345)
    await process_confirm_delete(message, mock_state, "en")
    
    mock_remove_jobs.assert_called_once_with(12345)
    mock_state.clear.assert_called_once()
    message.answer.assert_called_once()
    assert "deleted" in message.answer.call_args[0][0].lower()
    
    db_user = await crud.get_user(db_session, 12345)
    assert db_user is None

async def test_cancel_profile_deletion(db_session, mock_state):
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
    db_user = await crud.get_user(db_session, 12345)
    
    message = make_mock_message("❌ Cancel", user_id=12345)
    await cancel_profile_deletion(message, mock_state, "en", db_user)
    
    mock_state.clear.assert_called_once()
    message.answer.assert_called_once()
    assert "cancelled" in message.answer.call_args[0][0].lower()
    
    # User should still exist
    assert (await crud.get_user(db_session, 12345)) is not None

async def test_process_invalid_delete_confirm():
    message = make_mock_message("random text")
    await process_invalid_delete_confirm(message, "en")
    
    message.answer.assert_called_once()
    assert "delete" in message.answer.call_args[0][0].lower()

async def test_start_profile_setup_with_existing_db_user(db_session, mock_state):
    db_user = await crud.create_or_update_user(
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
    
    message = make_mock_message("⚙️ Set Up Profile")
    await start_profile_setup(message, mock_state, "en", db_user)
    
    mock_state.update_data.assert_called_once()
    args, kwargs = mock_state.update_data.call_args
    assert "current_profile" in kwargs
    current_profile = kwargs["current_profile"]
    assert current_profile["name"] == "John"
    assert current_profile["age"] == 30
    
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.name)
    message.answer.assert_called_once()
    markup = message.answer.call_args[1]["reply_markup"]
    assert markup.keyboard[0][0].text == "Keep: John"

async def test_process_name_keep_current(mock_state):
    current_profile = {
        "name": "John",
        "sex": "male",
        "age": 30,
        "height": 180.0,
        "weight": 80.0,
        "activity": "light",
        "goal": "lose_weight",
        "language": "en",
        "notifications_enabled": True,
        "daily_report_time": "21:00",
        "timezone": "Europe/London"
    }
    mock_state.get_data.return_value = {"current_profile": current_profile}
    
    message = make_mock_message("Keep: John")
    await process_name(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once_with(name="John")
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.sex)
    markup = message.answer.call_args[1]["reply_markup"]
    assert markup.keyboard[0][0].text == "Keep: Male"

async def test_process_sex_keep_current(mock_state):
    current_profile = {
        "name": "John",
        "sex": "male",
        "age": 30,
        "height": 180.0,
        "weight": 80.0,
        "activity": "light",
        "goal": "lose_weight",
        "language": "en",
        "notifications_enabled": True,
        "daily_report_time": "21:00",
        "timezone": "Europe/London"
    }
    mock_state.get_data.return_value = {"current_profile": current_profile}
    
    message = make_mock_message("Keep: Male")
    await process_sex(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once_with(sex="male")
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.age)
    markup = message.answer.call_args[1]["reply_markup"]
    assert markup.keyboard[0][0].text == "Keep: 30"

async def test_process_notifications_enabled_asks_report_time(mock_state):
    mock_state.get_data.return_value = {"language": "en"}
    message = make_mock_message("Yes")
    await process_notifications(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once_with(notifications_enabled=True)
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.report_time)
    message.answer.assert_called_once()
    assert "time" in message.answer.call_args[0][0].lower()

async def test_process_notifications_disabled_skips_report_time(mock_state):
    mock_state.get_data.return_value = {"language": "en"}
    message = make_mock_message("No")
    await process_notifications(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once_with(notifications_enabled=False)
    mock_state.set_state.assert_called_once_with(ProfileStatesGroup.timezone)
    message.answer.assert_called_once()
    assert "timezone" in message.answer.call_args[0][0].lower()

async def test_admin_navigation_flow(mock_state):
    from src.handlers.admin import cmd_admin, cmd_admin_stats, cmd_admin_stats_back
    from src.handlers.common import cmd_back_to_main_menu
    from src.database.models import User as DbUser
    
    # Simulate /admin
    msg_admin = make_mock_message("/admin")
    await cmd_admin(msg_admin, mock_state, "en")
    msg_admin.answer.assert_called_once()
    admin_markup = msg_admin.answer.call_args[1]["reply_markup"]
    assert admin_markup.keyboard[-1][0].text == "⬅️ Back to Main Menu"
    
    # Simulate Stats
    msg_stats = make_mock_message("📊 Stats")
    await cmd_admin_stats(msg_stats, "en")
    msg_stats.answer.assert_called_once()
    stats_markup = msg_stats.answer.call_args[1]["reply_markup"]
    assert stats_markup.keyboard[-1][0].text == "⬅️ Back to Admin Menu"
    
    # Simulate Back to Admin Menu
    msg_back_admin = make_mock_message("⬅️ Back to Admin Menu")
    await cmd_admin_stats_back(msg_back_admin, "en")
    msg_back_admin.answer.assert_called_once()
    admin_markup2 = msg_back_admin.answer.call_args[1]["reply_markup"]
    assert admin_markup2.keyboard[-1][0].text == "⬅️ Back to Main Menu"
    
    # Simulate Back to Main Menu
    db_user = DbUser(telegram_id=12345, is_admin=True, is_blocked=False)
    msg_back_main = make_mock_message("⬅️ Back to Main Menu")
    await cmd_back_to_main_menu(msg_back_main, mock_state, "en", db_user)
    msg_back_main.answer.assert_called_once()
    main_markup = msg_back_main.answer.call_args[1]["reply_markup"]
    assert "food" in main_markup.keyboard[0][0].text.lower()

async def test_admin_navigation_flow_ru(mock_state):
    from src.handlers.admin import cmd_admin, cmd_admin_stats, cmd_admin_stats_back
    from src.handlers.common import cmd_back_to_main_menu
    from src.database.models import User as DbUser
    
    # Simulate /admin
    msg_admin = make_mock_message("👑 Админ-панель")
    await cmd_admin(msg_admin, mock_state, "ru")
    msg_admin.answer.assert_called_once()
    admin_markup = msg_admin.answer.call_args[1]["reply_markup"]
    assert admin_markup.keyboard[-1][0].text == "⬅️ Главное меню"
    
    # Simulate Stats
    msg_stats = make_mock_message("📊 Статистика")
    await cmd_admin_stats(msg_stats, "ru")
    msg_stats.answer.assert_called_once()
    stats_markup = msg_stats.answer.call_args[1]["reply_markup"]
    assert stats_markup.keyboard[-1][0].text == "⬅️ Назад в меню"
    
    # Simulate Back to Admin Menu
    msg_back_admin = make_mock_message("⬅️ Назад в меню")
    await cmd_admin_stats_back(msg_back_admin, "ru")
    msg_back_admin.answer.assert_called_once()
    admin_markup2 = msg_back_admin.answer.call_args[1]["reply_markup"]
    assert admin_markup2.keyboard[-1][0].text == "⬅️ Главное меню"
    
    # Simulate Back to Main Menu
    db_user = DbUser(telegram_id=12345, is_admin=True, is_blocked=False)
    msg_back_main = make_mock_message("⬅️ Главное меню")
    await cmd_back_to_main_menu(msg_back_main, mock_state, "ru", db_user)
    msg_back_main.answer.assert_called_once()
    main_markup = msg_back_main.answer.call_args[1]["reply_markup"]
    assert "еду" in main_markup.keyboard[0][0].text.lower() or "food" in main_markup.keyboard[0][0].text.lower()




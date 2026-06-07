import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, UTC
from aiogram.fsm.context import FSMContext

from src.handlers.food import (
    MealEditingState,
    start_meals_list,
    process_meals_list_callback,
    process_meal_delete,
    start_meal_edit,
    process_meal_edit_text,
    accept_meal_edit,
    cancel_meal_edit
)
from src.database import crud
from tests.integration.test_handlers import make_mock_message, make_mock_callback, mock_state

pytestmark = pytest.mark.asyncio

@pytest.fixture
def mock_db_user():
    user = MagicMock()
    user.telegram_id = 12345
    user.language = "en"
    user.timezone = "UTC"
    return user

async def test_start_meals_list_empty(mock_db_user):
    message = make_mock_message("🍽️ My Meals")
    # Call handler
    await start_meals_list(message, "en", mock_db_user)
    message.answer.assert_called_once()
    assert "didn't log any meals" in message.answer.call_args[0][0]

async def test_start_meals_list_with_meals(db_session, mock_db_user):
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
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200
    )
    await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
        calories=70,
        proteins=6,
        fats=5,
        carbs=0.5
    )
    
    db_user = await crud.get_user(db_session, 12345)
    
    message = make_mock_message("🍽️ My Meals")
    await start_meals_list(message, "en", db_user)
    message.answer.assert_called_once()
    assert "Egg" in message.answer.call_args[0][0]
    assert "Totals: 70 kcal" in message.answer.call_args[0][0]

async def test_process_meal_delete(db_session, mock_db_user):
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
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200
    )
    meal = await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
        calories=70,
        proteins=6,
        fats=5,
        carbs=0.5
    )
    
    db_user = await crud.get_user(db_session, 12345)
    
    # Trigger delete
    message = make_mock_message("")
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    callback = make_mock_callback(f"meal:delete:{meal.id}:2026-06-07", message)
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    
    await process_meal_delete(callback, "en", db_user)
    
    # Check deleted from DB
    deleted_meal = await crud.get_food_log_by_id(db_session, meal.id, 12345)
    assert deleted_meal is None
    callback.answer.assert_called_once()
    message.edit_text.assert_called_once()

async def test_start_meal_edit(db_session, mock_state, mock_db_user):
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
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200
    )
    meal = await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
        calories=70,
        proteins=6,
        fats=5,
        carbs=0.5
    )
    
    db_user = await crud.get_user(db_session, 12345)
    
    message = make_mock_message("")
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    callback = make_mock_callback(f"meal:edit:{meal.id}:2026-06-07", message)
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    
    await start_meal_edit(callback, mock_state, "en", db_user)
    
    mock_state.set_state.assert_called_once_with(MealEditingState.waiting_for_edit_text)
    mock_state.update_data.assert_called_once()
    message.answer.assert_called_once()
    assert "editing the meal" in message.answer.call_args[0][0]

async def test_process_meal_edit_text(mock_state, mock_gemini_client):
    # Setup state context data
    mock_state.get_data.return_value = {
        "edit_meal_id": 99,
        "edit_date_str": "2026-06-07",
        "original_data": {
            "food_items": [{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
            "total_calories": 70, "total_protein": 6, "total_fat": 5, "total_carb": 0.5
        }
    }
    
    # Mock Gemini adjustment response
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Egg", "portion": "2 eggs", "calories": 140, "protein": 12, "fat": 10, "carb": 1.0}],'
        ' "total_calories": 140, "total_protein": 12, "total_fat": 10, "total_carb": 1.0}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response
    
    message = make_mock_message("Actually 2 eggs")
    
    await process_meal_edit_text(message, mock_state, "en")
    
    mock_state.update_data.assert_called_once()
    mock_state.set_state.assert_called_once_with(MealEditingState.waiting_for_edit_confirm)
    message.answer.assert_called()
    assert "Estimated Totals" in message.answer.call_args[0][0]

async def test_accept_meal_edit(db_session, mock_state, mock_db_user):
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
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200
    )
    meal = await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
        calories=70,
        proteins=6,
        fats=5,
        carbs=0.5
    )
    
    db_user = await crud.get_user(db_session, 12345)
    
    mock_state.get_data.return_value = {
        "edit_meal_id": meal.id,
        "edit_date_str": "2026-06-07",
        "adjusted_analysis": {
            "food_items": [{"name": "Egg", "portion": "2 eggs", "calories": 140, "protein": 12, "fat": 10, "carb": 1.0}],
            "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0
        }
    }
    
    message = make_mock_message("")
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    callback = make_mock_callback("mealedit:accept", message)
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    
    await accept_meal_edit(callback, mock_state, "en", db_user)
    
    # Verify DB record was updated
    updated_meal = await crud.get_food_log_by_id(db_session, meal.id, 12345)
    assert updated_meal.calories == 140
    assert updated_meal.items_json[0]["portion"] == "2 eggs"
    
    # Check states cleared
    mock_state.clear.assert_called_once()
    callback.answer.assert_called_once()
    message.answer.assert_called_once()
    assert "updated successfully" in message.answer.call_args[0][0]

async def test_cancel_meal_edit(mock_state, mock_db_user):
    mock_state.get_data.return_value = {
        "edit_meal_id": 12,
        "edit_date_str": "2026-06-07"
    }
    message = make_mock_message("")
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    callback = make_mock_callback("mealedit:cancel", message)
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    
    await cancel_meal_edit(callback, mock_state, "en", mock_db_user)
    
    mock_state.clear.assert_called_once()
    callback.answer.assert_called_once()
    message.answer.assert_called_once()
    assert "cancelled" in message.answer.call_args[0][0]

# --- Meal Type Classification Tests ---

from src.handlers.food import (
    FoodLoggingState,
    start_food_logging,
    process_meal_type_selection,
    accept_food_log
)

async def test_start_food_logging_prompt_meal_type(mock_state):
    message = make_mock_message("📝 Log Food")
    await start_food_logging(message, mock_state, "en")
    
    mock_state.set_state.assert_called_once_with(FoodLoggingState.waiting_for_meal_type)
    message.answer.assert_called_once()
    assert "Select the meal type" in message.answer.call_args[0][0]

async def test_process_meal_type_selection(mock_state):
    message = make_mock_message("")
    message.edit_text = AsyncMock()
    callback = make_mock_callback("meal_type:breakfast", message)
    
    await process_meal_type_selection(callback, mock_state, "en")
    
    callback.answer.assert_called_once()
    mock_state.update_data.assert_called_once_with(meal_type="breakfast")
    mock_state.set_state.assert_called_once_with(FoodLoggingState.waiting_for_input)
    message.edit_text.assert_called_once()
    assert "photo of your meal" in message.edit_text.call_args[0][0]

async def test_accept_food_log_saves_meal_type(db_session, mock_state):
    mock_state.get_data.return_value = {
        "analysis": {
            "food_items": [{"name": "Banana", "portion": "1 item", "calories": 90, "protein": 1.1, "fat": 0.3, "carb": 23.0}],
            "total_calories": 90, "total_protein": 1.1, "total_fat": 0.3, "total_carb": 23.0
        },
        "image_file_id": None,
        "raw_text": "Banana",
        "meal_type": "snack"
    }
    
    message = make_mock_message("")
    message.edit_reply_markup = AsyncMock()
    callback = make_mock_callback("food:accept", message)
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    
    await accept_food_log(callback, mock_state, "en")
    
    # Verify saved meal has meal_type = 'snack' in DB
    meals = await crud.get_food_logs(db_session, 12345, datetime(2000, 1, 1), datetime(2100, 1, 1))
    assert len(meals) == 1
    assert meals[0].meal_type == "snack"
    assert meals[0].calories == 90

async def test_start_meals_list_with_meal_types(db_session, mock_db_user):
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
        target_calories=2000,
        target_protein=150,
        target_fat=60,
        target_carb=200
    )
    
    # Log different meal types
    await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
        calories=70,
        proteins=6,
        fats=5,
        carbs=0.5,
        meal_type="breakfast"
    )
    await crud.add_food_log(
        db_session,
        user_id=12345,
        items_json=[{"name": "Salad", "portion": "200g", "calories": 150, "protein": 2, "fat": 10, "carb": 8}],
        calories=150,
        proteins=2,
        fats=10,
        carbs=8,
        meal_type="lunch"
    )
    
    db_user = await crud.get_user(db_session, 12345)
    
    message = make_mock_message("🍽️ My Meals")
    await start_meals_list(message, "en", db_user)
    
    message.answer.assert_called_once()
    output_text = message.answer.call_args[0][0]
    assert "🍳 *Breakfast*:" in output_text
    assert "🍲 *Lunch*:" in output_text


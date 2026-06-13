import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, UTC
from aiogram.fsm.context import FSMContext

from src.handlers.food import (
    MealViewingState,
    MealEditingState,
    FoodLoggingState,
    start_meals_list,
    process_meals_viewing,
    process_meal_edit_text,
    process_meal_edit_confirm,
    start_food_logging,
    process_meal_type_selection,
    process_food_confirm,
    process_food_input
)
from src.database import crud
from tests.integration.test_handlers import make_mock_message, mock_state

pytestmark = pytest.mark.asyncio

@pytest.fixture
def mock_db_user():
    user = MagicMock()
    user.telegram_id = 12345
    user.language = "en"
    user.timezone = "UTC"
    user.is_admin = False
    return user

async def test_start_meals_list_empty(mock_state, mock_db_user):
    message = make_mock_message("🍽️ My Meals")
    await start_meals_list(message, mock_state, "en", mock_db_user)
    
    mock_state.set_state.assert_called_once_with(MealViewingState.viewing)
    mock_state.update_data.assert_called()
    message.answer.assert_called_once()
    assert "didn't log any meals" in message.answer.call_args[0][0]

async def test_start_meals_list_with_meals(db_session, mock_state, mock_db_user):
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
    await start_meals_list(message, mock_state, "en", db_user)
    
    mock_state.set_state.assert_called_once_with(MealViewingState.viewing)
    message.answer.assert_called_once()
    assert "Egg" in message.answer.call_args[0][0]
    assert "Totals: 70 kcal" in message.answer.call_args[0][0]

async def test_process_meal_delete(db_session, mock_state, mock_db_user):
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
    
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    mock_state.get_data.return_value = {"view_date": today_str}
    
    message = make_mock_message("❌ Delete #1")
    
    await process_meals_viewing(message, mock_state, "en", db_user)
    
    deleted_meal = await crud.get_food_log_by_id(db_session, meal.id, 12345)
    assert deleted_meal is None
    message.answer.assert_called()
    assert "deleted successfully" in message.answer.call_args_list[0][0][0]

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
    
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    mock_state.get_data.return_value = {"view_date": today_str}
    
    message = make_mock_message("✏️ Edit #1")
    
    await process_meals_viewing(message, mock_state, "en", db_user)
    
    mock_state.set_state.assert_called_once_with(MealEditingState.waiting_for_edit_text)
    mock_state.update_data.assert_called()
    message.answer.assert_called_once()
    assert "editing the meal" in message.answer.call_args[0][0]

async def test_process_meal_edit_text(mock_state, mock_gemini_client):
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    mock_state.get_data.return_value = {
        "edit_meal_id": 99,
        "edit_date_str": today_str,
        "original_data": {
            "food_items": [{"name": "Egg", "portion": "1 egg", "calories": 70, "protein": 6, "fat": 5, "carb": 0.5}],
            "total_calories": 70, "total_protein": 6, "total_fat": 5, "total_carb": 0.5
        }
    }
    
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
    
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    mock_state.get_data.return_value = {
        "edit_meal_id": meal.id,
        "edit_date_str": today_str,
        "adjusted_analysis": {
            "food_items": [{"name": "Egg", "portion": "2 eggs", "calories": 140, "protein": 12, "fat": 10, "carb": 1.0}],
            "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0
        }
    }
    
    message = make_mock_message("✅ Accept")
    
    await process_meal_edit_confirm(message, mock_state, "en", db_user)
    
    updated_meal = await crud.get_food_log_by_id(db_session, meal.id, 12345)
    assert updated_meal.calories == 140
    assert updated_meal.items_json[0]["portion"] == "2 eggs"
    
    mock_state.set_state.assert_called_once_with(MealViewingState.viewing)
    message.answer.assert_called()
    assert "updated successfully" in message.answer.call_args_list[0][0][0]

async def test_cancel_meal_edit(mock_state, mock_db_user):
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    mock_state.get_data.return_value = {
        "edit_meal_id": 12,
        "edit_date_str": today_str
    }
    message = make_mock_message("❌ Cancel")
    
    await process_meal_edit_confirm(message, mock_state, "en", mock_db_user)
    
    mock_state.set_state.assert_called_once_with(MealViewingState.viewing)
    message.answer.assert_called()
    assert "cancelled" in message.answer.call_args_list[0][0][0]

# --- Meal Type Classification Tests ---

async def test_start_food_logging_prompt_meal_type(mock_state):
    message = make_mock_message("📝 Log Food")
    await start_food_logging(message, mock_state, "en")
    
    mock_state.set_state.assert_called_once_with(FoodLoggingState.waiting_for_meal_type)
    message.answer.assert_called_once()
    assert "Select the meal type" in message.answer.call_args[0][0]

async def test_process_meal_type_selection(mock_state, mock_db_user):
    message = make_mock_message("🍳 Breakfast")
    
    await process_meal_type_selection(message, mock_state, "en", mock_db_user)
    
    mock_state.update_data.assert_called_once_with(meal_type="breakfast")
    mock_state.set_state.assert_called_once_with(FoodLoggingState.waiting_for_input)
    message.answer.assert_called_once()
    assert "photo of your meal" in message.answer.call_args[0][0]

async def test_accept_food_log_saves_meal_type(db_session, mock_state, mock_db_user):
    mock_state.get_data.return_value = {
        "analysis": {
            "food_items": [{"name": "Banana", "portion": "1 item", "calories": 90, "protein": 1.1, "fat": 0.3, "carb": 23.0}],
            "total_calories": 90, "total_protein": 1.1, "total_fat": 0.3, "total_carb": 23.0
        },
        "image_file_id": None,
        "raw_text": "Banana",
        "meal_type": "snack"
    }
    
    message = make_mock_message("✅ Accept")
    
    await process_food_confirm(message, mock_state, "en", mock_db_user)
    
    meals = await crud.get_food_logs(db_session, 12345, datetime(2000, 1, 1), datetime(2100, 1, 1))
    assert len(meals) == 1
    assert meals[0].meal_type == "snack"
    assert meals[0].calories == 90

async def test_start_meals_list_with_meal_types(db_session, mock_state, mock_db_user):
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
    await start_meals_list(message, mock_state, "en", db_user)
    
    message.answer.assert_called_once()
    output_text = message.answer.call_args[0][0]
    assert "🍳 *Breakfast*:" in output_text
    assert "🍲 *Lunch*:" in output_text

async def test_process_food_input_photo_and_caption(mock_state, monkeypatch):
    from aiogram.types import PhotoSize
    
    message = make_mock_message("")
    message.photo = [
        PhotoSize(file_id="photo1", file_unique_id="unique1", width=100, height=100),
        PhotoSize(file_id="photo2", file_unique_id="unique2", width=200, height=200),
    ]
    message.caption = "This is a delicious breakfast of oatmeal and berries"
    
    mock_file = MagicMock()
    mock_file.file_path = "photos/photo2.jpg"
    message.bot.get_file = AsyncMock(return_value=mock_file)
    
    async def mock_download_file(file_path, destination):
        destination.write(b"dummy_photo_bytes")
    message.bot.download_file = AsyncMock(side_effect=mock_download_file)
    
    mock_item = MagicMock()
    mock_item.name = "Oatmeal"
    mock_item.portion = "1 bowl"
    mock_item.calories = 150
    mock_item.protein = 5.0
    mock_item.fat = 2.0
    mock_item.carb = 28.0
    
    mock_analysis = MagicMock()
    mock_analysis.food_items = [mock_item]
    mock_analysis.total_calories = 150
    mock_analysis.total_protein = 5.0
    mock_analysis.total_fat = 2.0
    mock_analysis.total_carb = 28.0
    mock_analysis.model_dump.return_value = {
        "food_items": [{"name": "Oatmeal", "portion": "1 bowl", "calories": 150, "protein": 5.0, "fat": 2.0, "carb": 28.0}],
        "total_calories": 150,
        "total_protein": 5.0,
        "total_fat": 2.0,
        "total_carb": 28.0
    }
    
    mock_analyze = AsyncMock(return_value=mock_analysis)
    monkeypatch.setattr("src.services.gemini.analyze_food_input", mock_analyze)
    
    mock_state.get_data.return_value = {"meal_type": "breakfast"}
    
    await process_food_input(message, mock_state, "en")
    
    # Verify gemini analysis is called with photo bytes and caption text description
    mock_analyze.assert_called_once_with(
        text_description="This is a delicious breakfast of oatmeal and berries",
        image_bytes=b"dummy_photo_bytes",
        language="en"
    )
    
    # State should be updated with the analysis results and the image/raw text info
    mock_state.update_data.assert_called_once_with(
        analysis=mock_analysis.model_dump(),
        image_file_id="photo2",
        raw_text="This is a delicious breakfast of oatmeal and berries"
    )
    
    # State transitioned to waiting_for_confirm
    mock_state.set_state.assert_called_once_with(FoodLoggingState.waiting_for_confirm)
    
    # User received message showing food details (call_count should be 2: one for wait message, one for final result)
    assert message.answer.call_count == 2
    answer_text = message.answer.call_args_list[1][0][0]
    assert "Oatmeal" in answer_text
    assert "150 kcal" in answer_text

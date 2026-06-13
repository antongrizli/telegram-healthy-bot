import pytest
from unittest.mock import MagicMock
from src.services import gemini
from src.services.gemini import FoodAnalysisResponse, FoodItem

pytestmark = pytest.mark.asyncio

async def test_analyze_food_input_success(mock_gemini_client):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Eggs", "portion": "2 eggs", "calories": 140, "protein": 12.0, "fat": 10.0, "carb": 1.0}],'
        ' "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    res = await gemini.analyze_food_input(text_description="2 boiled eggs")
    
    assert res is not None
    assert isinstance(res, FoodAnalysisResponse)
    assert res.total_calories == 140
    assert len(res.food_items) == 1
    assert res.food_items[0].name == "Eggs"

async def test_analyze_food_input_failure(mock_gemini_client):
    # Setup mock exception (non-transient generic error)
    mock_gemini_client.models.generate_content.side_effect = Exception("API Error")

    with pytest.raises(Exception):
        await gemini.analyze_food_input(text_description="2 boiled eggs")

async def test_analyze_food_input_transient_retry(mock_gemini_client):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Eggs", "portion": "2 eggs", "calories": 140, "protein": 12.0, "fat": 10.0, "carb": 1.0}],'
        ' "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0}'
    )
    
    call_count = 0
    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise Exception("503 Service Unavailable: This model is currently experiencing high demand.")
        return mock_response
        
    mock_gemini_client.models.generate_content.side_effect = side_effect

    res = await gemini.analyze_food_input(text_description="2 boiled eggs")
    
    assert res is not None
    assert res.total_calories == 140
    assert call_count == 3  # Two failed attempts (retried), third call succeeds

async def test_adjust_food_analysis_success(mock_gemini_client):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Eggs", "portion": "3 eggs", "calories": 210, "protein": 18.0, "fat": 15.0, "carb": 1.5}],'
        ' "total_calories": 210, "total_protein": 18.0, "total_fat": 15.0, "total_carb": 1.5}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    original_data = {
        "food_items": [{"name": "Eggs", "portion": "2 eggs", "calories": 140, "protein": 12.0, "fat": 10.0, "carb": 1.0}],
        "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0
    }
    
    res = await gemini.adjust_food_analysis(original_data, correction_text="Actually 3 eggs")
    
    assert res is not None
    assert res.total_calories == 210
    assert res.food_items[0].portion == "3 eggs"

async def test_adjust_food_analysis_failure(mock_gemini_client):
    mock_gemini_client.models.generate_content.side_effect = Exception("API Error")
    with pytest.raises(Exception):
        await gemini.adjust_food_analysis({}, "correction")

async def test_generate_report_success(mock_gemini_client):
    # Setup mock response containing raw markdown to clean
    mock_response = MagicMock()
    mock_response.text = "**Daily Report**\n*   **Goal:** lose_weight"
    mock_gemini_client.models.generate_content.return_value = mock_response

    profile = {"name": "Anton", "sex": "male", "age": 36, "height_cm": 188.0, "weight_kg": 88.0, "activity_level": "light", "goal": "lose_weight"}
    report = await gemini.generate_report(profile, [], [], "daily", "en")
    
    # Verify report is cleaned for Telegram Markdown V1
    assert report == "*Daily Report*\n• *Goal:* lose\\_weight"

async def test_generate_report_failure(mock_gemini_client):
    mock_gemini_client.models.generate_content.side_effect = Exception("API Error")
    profile = {"name": "Anton", "sex": "male", "age": 36, "height_cm": 188.0, "weight_kg": 88.0, "activity_level": "light", "goal": "lose_weight"}
    
    with pytest.raises(Exception):
        await gemini.generate_report(profile, [], [], "daily", "en")

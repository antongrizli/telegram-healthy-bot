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
    assert report == "Daily Report\n• Goal: lose_weight"

async def test_generate_report_failure(mock_gemini_client):
    mock_gemini_client.models.generate_content.side_effect = Exception("API Error")
    profile = {"name": "Anton", "sex": "male", "age": 36, "height_cm": 188.0, "weight_kg": 88.0, "activity_level": "light", "goal": "lose_weight"}
    
    with pytest.raises(Exception):
        await gemini.generate_report(profile, [], [], "daily", "en")


async def test_analyze_food_input_multiple_images(mock_gemini_client):
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Eggs", "portion": "2 eggs", "calories": 140, "protein": 12.0, "fat": 10.0, "carb": 1.0}],'
        ' "total_calories": 140, "total_protein": 12.0, "total_fat": 10.0, "total_carb": 1.0}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    res = await gemini.analyze_food_input(
        text_description="multiple images",
        images_bytes=[b"img1", b"img2"]
    )
    
    assert res is not None
    assert res.total_calories == 140
    
    # Verify generate_content was called with images
    call_args = mock_gemini_client.models.generate_content.call_args
    assert call_args is not None
    contents = call_args[1]["contents"]
    # We expect 2 image parts + 1 text description + 1 prompt = 4 items in contents
    assert len(contents) == 4


async def test_gemma_model_omits_response_schema(mock_gemini_client, monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_MODEL", "gemma-4-31b-it")
    mock_response = MagicMock()
    mock_response.text = (
        '{"food_items": [{"name": "Apple", "portion": "1 apple", "calories": 95, "protein": 0.5, "fat": 0.3, "carb": 25.0}],'
        ' "total_calories": 95, "total_protein": 0.5, "total_fat": 0.3, "total_carb": 25.0}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    res = await gemini.analyze_food_input(text_description="1 apple")
    assert res is not None
    assert res.food_items[0].name == "Apple"

    call_args = mock_gemini_client.models.generate_content.call_args
    config = call_args[1].get("config")
    # For Gemma models, response_schema must NOT be sent to avoid 400 INVALID_ARGUMENT
    assert getattr(config, "response_schema", None) is None
    assert getattr(config, "response_mime_type", None) == "application/json"


async def test_gemma_thought_tags_and_markdown_stripping(mock_gemini_client):
    mock_response = MagicMock()
    mock_response.text = (
        "<thought>\n"
        "Let's calculate {nutrition}: 1 banana has about 105 kcal.\n"
        "</thought>\n"
        "```json\n"
        "{\n"
        '  "food_items": [{"name": "Banana", "portion": "1 medium", "calories": 105, "protein": 1.3, "fat": 0.4, "carb": 27.0}],\n'
        '  "total_calories": 105, "total_protein": 1.3, "total_fat": 0.4, "total_carb": 27.0\n'
        "}\n"
        "```"
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    res = await gemini.analyze_food_input(text_description="1 banana")
    assert res is not None
    assert res.food_items[0].name == "Banana"
    assert res.total_calories == 105


async def test_gemma_normalization_and_recomputing_totals(mock_gemini_client):
    mock_response = MagicMock()
    # Missing total_calories and using 'items', 'proteins', 'fats', 'carbs'
    mock_response.text = (
        '{\n'
        '  "items": [\n'
        '    {"name": "Oatmeal", "portion": "1 bowl", "calories": 150, "proteins": 5.0, "fats": 2.5, "carbs": 27.0},\n'
        '    {"name": "Berries", "portion": "50g", "calories": 40, "proteins": 0.5, "fats": 0.2, "carbs": 9.0}\n'
        '  ]\n'
        '}'
    )
    mock_gemini_client.models.generate_content.return_value = mock_response

    res = await gemini.analyze_food_input(text_description="Oatmeal with berries")
    assert res is not None
    assert len(res.food_items) == 2
    assert res.food_items[0].name == "Oatmeal"
    assert res.food_items[0].protein == 5.0
    # Totals recomputed automatically
    assert res.total_calories == 190
    assert res.total_protein == 5.5


async def test_call_gemini_400_invalid_argument_fallback(mock_gemini_client):
    mock_response = MagicMock()
    mock_response.text = '{"food_items": [{"name": "Egg", "portion": "1", "calories": 70, "protein": 6.0, "fat": 5.0, "carb": 0.5}]}'

    attempt = 0
    def side_effect(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        config = kwargs.get("config")
        if attempt == 1 and getattr(config, "response_schema", None) is not None:
            raise Exception("400 INVALID_ARGUMENT. Request contains an invalid argument.")
        return mock_response

    mock_gemini_client.models.generate_content.side_effect = side_effect

    from google.genai import types
    config = types.GenerateContentConfig(response_schema=gemini.FoodAnalysisResponse, response_mime_type="application/json")
    res = await gemini.call_gemini_with_retry(
        contents=["test"],
        config=config,
        model="some-model"
    )
    assert res == mock_response
    assert config.response_schema is None


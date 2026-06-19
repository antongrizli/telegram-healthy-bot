import io
from datetime import datetime, UTC, timedelta
from src.services.charts import generate_nutrition_chart, generate_weight_chart

class MockFoodLog:
    def __init__(self, calories, proteins, fats, carbs, logged_at):
        self.calories = calories
        self.proteins = proteins
        self.fats = fats
        self.carbs = carbs
        self.logged_at = logged_at

class MockWeightLog:
    def __init__(self, weight, logged_at):
        self.weight = weight
        self.logged_at = logged_at

def test_generate_nutrition_chart_empty():
    buf = generate_nutrition_chart(
        food_logs=[],
        target_calories=2000,
        target_protein=150,
        target_fat=70,
        target_carb=200,
        language="en",
        timezone_str="UTC"
    )
    assert isinstance(buf, io.BytesIO)
    assert buf.getvalue().startswith(b"\x89PNG")  # Valid PNG header

def test_generate_nutrition_chart_populated():
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    logs = [
        MockFoodLog(500, 40.0, 15.0, 50.0, now_utc - timedelta(hours=2)),
        MockFoodLog(700, 50.0, 20.0, 80.0, now_utc - timedelta(days=1, hours=3)),
        MockFoodLog(600, 45.0, 18.0, 60.0, now_utc - timedelta(days=2, hours=4)),
    ]
    buf = generate_nutrition_chart(
        food_logs=logs,
        target_calories=2000,
        target_protein=150,
        target_fat=70,
        target_carb=200,
        language="ru",
        timezone_str="Europe/Moscow"
    )
    assert isinstance(buf, io.BytesIO)
    assert buf.getvalue().startswith(b"\x89PNG")

def test_generate_weight_chart_empty():
    buf = generate_weight_chart(
        weight_logs=[],
        language="en",
        timezone_str="UTC"
    )
    assert isinstance(buf, io.BytesIO)
    assert buf.getvalue().startswith(b"\x89PNG")

def test_generate_weight_chart_populated():
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    logs = [
        MockWeightLog(80.5, now_utc - timedelta(days=5)),
        MockWeightLog(79.8, now_utc - timedelta(days=3)),
        MockWeightLog(79.2, now_utc - timedelta(days=1)),
    ]
    buf = generate_weight_chart(
        weight_logs=logs,
        language="uk",
        timezone_str="Europe/Kyiv"
    )
    assert isinstance(buf, io.BytesIO)
    assert buf.getvalue().startswith(b"\x89PNG")

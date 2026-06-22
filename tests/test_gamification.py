import pytest
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock
from src.database import crud
from src.database.models import User, Streak, Achievement, HealthCard, FoodLog, WeightLog
from src.services import gamification

@pytest.mark.asyncio
async def test_food_log_streak_increment(db_session):
    # Create test user
    user = User(
        telegram_id=123456,
        name="John",
        sex="male",
        age=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="moderate",
        goal="lose_weight",
        language="en",
        timezone="UTC",
        target_calories=2000,
        target_protein=150,
        target_fat=70,
        target_carb=200
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # First food log (new streak = 1)
    streak_val, freeze_used, freezes_left = await gamification.process_food_log_streak(db_session, user)
    assert streak_val == 1
    assert not freeze_used
    assert freezes_left == 1
    assert user.current_streak == 1

    # Same day food log (streak remains 1)
    streak_val, freeze_used, freezes_left = await gamification.process_food_log_streak(db_session, user)
    assert streak_val == 1
    assert not freeze_used
    assert freezes_left == 1

@pytest.mark.asyncio
async def test_food_log_streak_freeze_usage(db_session):
    user = User(
        telegram_id=22222,
        name="Jane",
        sex="female",
        age=25,
        height_cm=165.0,
        weight_kg=60.0,
        activity_level="light",
        goal="maintain",
        language="en",
        timezone="UTC",
        target_calories=1800,
        target_protein=120,
        target_fat=60,
        target_carb=180,
        streak_freezes_left=1
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    # 1. Establish streak on yesterday
    streak = await crud.get_or_create_streak(db_session, user.telegram_id, "food_logging")
    yesterday = datetime.now(UTC) - timedelta(days=2)
    streak.current_count = 5
    streak.last_logged_date = datetime(yesterday.year, yesterday.month, yesterday.day)
    await db_session.commit()

    # 2. Log food today (gap of 2 days: yesterday missed, today logging)
    # This should trigger streak freeze usage!
    streak_val, freeze_used, freezes_left = await gamification.process_food_log_streak(db_session, user)
    
    assert streak_val == 6
    assert freeze_used
    assert freezes_left == 0
    assert user.streak_freezes_left == 0

@pytest.mark.asyncio
async def test_achievements_unlock(db_session):
    user = User(
        telegram_id=33333,
        name="Alex",
        sex="male",
        age=35,
        height_cm=175.0,
        weight_kg=75.0,
        activity_level="active",
        goal="gain_weight",
        language="en",
        timezone="UTC",
        target_calories=2500,
        target_protein=180,
        target_fat=80,
        target_carb=280
    )
    db_session.add(user)
    await db_session.commit()

    # 1. Add food log to trigger first_meal
    await crud.add_food_log(
        db_session,
        user_id=user.telegram_id,
        items_json=[{"name": "Apple", "portion": "1", "calories": 80, "protein": 0.5, "fats": 0.3, "carbs": 20}],
        calories=80,
        proteins=0.5,
        fats=0.3,
        carbs=20
    )

    newly_unlocked = await gamification.check_new_achievements(db_session, user.telegram_id)
    assert "first_meal" in newly_unlocked

    # Verify achievement is unlocked in database
    unlocked = await crud.get_user_achievements(db_session, user.telegram_id)
    unlocked_keys = {a.achievement_key for a in unlocked}
    assert "first_meal" in unlocked_keys

@pytest.mark.asyncio
async def test_weekly_health_card_score_calculation(db_session, monkeypatch):
    user = User(
        telegram_id=44444,
        name="Bob",
        sex="male",
        age=40,
        height_cm=185.0,
        weight_kg=90.0,
        activity_level="moderate",
        goal="lose_weight",
        language="en",
        timezone="UTC",
        target_calories=2200,
        target_protein=160,
        target_fat=70,
        target_carb=230
    )
    db_session.add(user)
    await db_session.commit()

    # Calculate previous week's start to place our logs inside it
    local_now = datetime.now(UTC)
    days_since_monday = local_now.weekday()
    week_start_midnight = datetime(local_now.year, local_now.month, local_now.day) - timedelta(days=days_since_monday)

    # Log food for 7 days of target calorie matching (exactly 2200) in the previous week
    for i in range(7):
        log_date = week_start_midnight - timedelta(days=i+1)
        food_log = FoodLog(
            user_id=user.telegram_id,
            items_json=[],
            calories=2200,
            proteins=160,
            fats=70,
            carbs=230,
            logged_at=log_date
        )
        db_session.add(food_log)
    await db_session.commit()

    # Mock gemini call
    mock_gemini_generate = AsyncMock(return_value="Coach note")
    monkeypatch.setattr("src.services.gamification.gemini_generate_card_note", mock_gemini_generate)

    # Generate Health Card
    card = await gamification.generate_weekly_health_card(db_session, user.telegram_id)
    
    assert card.card_data["overall_score"] > 50
    assert card.card_data["categories"]["consistency"]["score"] == 100  # 7 out of 7 days logged = 100%
    assert card.card_data["categories"]["nutrition"]["score"] == 100  # deviation is 0% since avg equals target

import pytest
from datetime import datetime, UTC, timedelta
from src.database import crud

pytestmark = pytest.mark.asyncio

async def test_create_or_update_and_get_user(db_session):
    # 1. Create a user
    user = await crud.create_or_update_user(
        db_session,
        telegram_id=11111,
        name="Alice",
        sex="female",
        age=25,
        height_cm=165.0,
        weight_kg=60.0,
        activity_level="sedentary",
        goal="maintain",
        target_calories=1600,
        target_protein=80,
        target_fat=50,
        target_carb=200
    )
    assert user.telegram_id == 11111
    assert user.name == "Alice"
    assert user.is_blocked is False

    # 2. Retrieve user
    db_user = await crud.get_user(db_session, 11111)
    assert db_user is not None
    assert db_user.name == "Alice"

    # 3. Update user
    updated_user = await crud.create_or_update_user(
        db_session,
        telegram_id=11111,
        name="Alice Cooper",
        sex="female",
        age=26,
        height_cm=165.0,
        weight_kg=59.0,
        activity_level="sedentary",
        goal="maintain",
        target_calories=1600,
        target_protein=80,
        target_fat=50,
        target_carb=200
    )
    assert updated_user.name == "Alice Cooper"
    assert updated_user.age == 26
    assert updated_user.weight_kg == 59.0

async def test_get_all_users_and_blocking(db_session):
    # Create User 1
    await crud.create_or_update_user(
        db_session, telegram_id=22222, name="Bob", sex="male", age=30,
        height_cm=180.0, weight_kg=80.0, activity_level="light", goal="lose_weight",
        target_calories=2000, target_protein=150, target_fat=70, target_carb=200
    )
    # Create User 2
    await crud.create_or_update_user(
        db_session, telegram_id=33333, name="Charlie", sex="male", age=35,
        height_cm=175.0, weight_kg=75.0, activity_level="moderate", goal="maintain",
        target_calories=2200, target_protein=110, target_fat=73, target_carb=275
    )

    # Verify we get both users
    users = await crud.get_all_users(db_session)
    assert len(users) == 2

    # Block Bob
    blocked = await crud.block_user(db_session, 22222, block=True)
    assert blocked is True

    # Get unblocked users only
    active_users = await crud.get_all_users(db_session, include_blocked=False)
    assert len(active_users) == 1
    assert active_users[0].name == "Charlie"

    # Block non-existent user
    blocked = await crud.block_user(db_session, 99999, block=True)
    assert blocked is False

async def test_food_logs(db_session):
    # Setup user
    await crud.create_or_update_user(
        db_session, telegram_id=44444, name="David", sex="male", age=40,
        height_cm=170.0, weight_kg=70.0, activity_level="light", goal="lose_weight",
        target_calories=1800, target_protein=135, target_fat=60, target_carb=180
    )

    # Log food
    items = [{"name": "Eggs", "portion": "2 eggs", "calories": 140, "protein": 12, "fat": 10, "carb": 1}]
    log = await crud.add_food_log(
        db_session,
        user_id=44444,
        items_json=items,
        calories=140,
        proteins=12.0,
        fats=10.0,
        carbs=1.0,
        raw_text="2 eggs"
    )
    assert log.id is not None
    assert log.calories == 140

    # Get food logs
    now = datetime.now(UTC).replace(tzinfo=None)
    start_date = now - timedelta(days=1)
    end_date = now + timedelta(days=1)
    logs = await crud.get_food_logs(db_session, 44444, start_date, end_date)
    assert len(logs) == 1
    assert logs[0].raw_text == "2 eggs"

async def test_weight_logs(db_session):
    # Setup user
    await crud.create_or_update_user(
        db_session, telegram_id=55555, name="Eva", sex="female", age=22,
        height_cm=160.0, weight_kg=55.0, activity_level="sedentary", goal="maintain",
        target_calories=1500, target_protein=75, target_fat=50, target_carb=187
    )

    # Log weights
    log1 = await crud.add_weight_log(db_session, 55555, 55.4)
    # Artificially shift timestamp for order testing
    log1.logged_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    await db_session.commit()

    log2 = await crud.add_weight_log(db_session, 55555, 55.0)

    # Verify latest and previous weight logs
    latest = await crud.get_latest_weight_log(db_session, 55555)
    assert latest.weight == 55.0

    previous = await crud.get_previous_weight_log(db_session, 55555)
    assert previous.weight == 55.4

    # Verify get_weight_logs list
    now = datetime.now(UTC).replace(tzinfo=None)
    start = now - timedelta(days=1)
    end = now + timedelta(days=1)
    logs = await crud.get_weight_logs(db_session, 55555, start, end)
    assert len(logs) == 2
    assert logs[0].weight == 55.4
    assert logs[1].weight == 55.0

async def test_message_stats_and_admin_panel(db_session):
    # Setup user
    await crud.create_or_update_user(
        db_session, telegram_id=66666, name="Frank", sex="male", age=33,
        height_cm=175.0, weight_kg=78.0, activity_level="light", goal="lose_weight",
        target_calories=1900, target_protein=142, target_fat=63, target_carb=190
    )

    # Log stats
    await crud.log_message_stat(db_session, 66666, "text")
    await crud.log_message_stat(db_session, 66666, "photo")

    # Log food log today
    items = [{"name": "Banana", "portion": "1", "calories": 90, "protein": 1, "fat": 0.3, "carb": 22}]
    await crud.add_food_log(
        db_session, user_id=66666, items_json=items, calories=90, proteins=1.0, fats=0.3, carbs=22.0
    )

    # Fetch stats
    stats = await crud.get_admin_stats(db_session)
    assert stats["total_users"] == 1
    assert stats["active_users_24h"] == 1
    assert stats["food_logs_24h"] == 1
    assert stats["messages_24h"] == 2

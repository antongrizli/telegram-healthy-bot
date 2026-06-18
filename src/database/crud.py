from datetime import datetime, UTC, timedelta
from sqlalchemy import select, update, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User, FoodLog, WeightLog, MessageStat, AiRequestLog, AiRequestQueue
from src.config import settings

async def get_user(db: AsyncSession, telegram_id: int) -> User:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalars().first()

async def get_all_users(db: AsyncSession, include_blocked: bool = True) -> list[User]:
    stmt = select(User)
    if not include_blocked:
        stmt = stmt.where(User.is_blocked == False)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def create_or_update_user(db: AsyncSession, telegram_id: int, **kwargs) -> User:
    user = await get_user(db, telegram_id)
    # Check if this user is in the settings.ADMIN_USER_IDS
    is_admin = telegram_id in settings.ADMIN_USER_IDS

    if user:
        for key, value in kwargs.items():
            setattr(user, key, value)
        user.is_admin = is_admin
    else:
        user = User(telegram_id=telegram_id, is_admin=is_admin, **kwargs)
        db.add(user)
    
    await db.commit()
    await db.refresh(user)
    return user

async def block_user(db: AsyncSession, telegram_id: int, block: bool = True) -> bool:
    user = await get_user(db, telegram_id)
    if user:
        user.is_blocked = block
        await db.commit()
        return True
    return False

async def add_food_log(
    db: AsyncSession,
    user_id: int,
    items_json: list,
    calories: int,
    proteins: float,
    fats: float,
    carbs: float,
    image_file_id: str = None,
    raw_text: str = None,
    meal_type: str = "food"
) -> FoodLog:
    food_log = FoodLog(
        user_id=user_id,
        items_json=items_json,
        calories=calories,
        proteins=proteins,
        fats=fats,
        carbs=carbs,
        image_file_id=image_file_id,
        raw_text=raw_text,
        meal_type=meal_type,
        logged_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(food_log)
    await db.commit()
    await db.refresh(food_log)
    return food_log

async def get_food_logs(db: AsyncSession, user_id: int, start_date: datetime, end_date: datetime) -> list[FoodLog]:
    result = await db.execute(
        select(FoodLog)
        .where(
            and_(
                FoodLog.user_id == user_id,
                FoodLog.logged_at >= start_date,
                FoodLog.logged_at <= end_date
            )
        )
        .order_by(FoodLog.logged_at.asc())
    )
    return list(result.scalars().all())

async def add_weight_log(db: AsyncSession, user_id: int, weight: float) -> WeightLog:
    weight_log = WeightLog(
        user_id=user_id,
        weight=weight,
        logged_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(weight_log)
    await db.commit()
    await db.refresh(weight_log)
    return weight_log

async def get_weight_logs(db: AsyncSession, user_id: int, start_date: datetime, end_date: datetime) -> list[WeightLog]:
    result = await db.execute(
        select(WeightLog)
        .where(
            and_(
                WeightLog.user_id == user_id,
                WeightLog.logged_at >= start_date,
                WeightLog.logged_at <= end_date
            )
        )
        .order_by(WeightLog.logged_at.asc())
    )
    return list(result.scalars().all())

async def get_latest_weight_log(db: AsyncSession, user_id: int) -> WeightLog:
    result = await db.execute(
        select(WeightLog)
        .where(WeightLog.user_id == user_id)
        .order_by(desc(WeightLog.logged_at))
        .limit(1)
    )
    return result.scalars().first()

async def get_previous_weight_log(db: AsyncSession, user_id: int) -> WeightLog:
    result = await db.execute(
        select(WeightLog)
        .where(WeightLog.user_id == user_id)
        .order_by(desc(WeightLog.logged_at))
        .offset(1)
        .limit(1)
    )
    return result.scalars().first()

async def log_message_stat(db: AsyncSession, user_id: int, message_type: str) -> MessageStat:
    stat = MessageStat(
        user_id=user_id,
        message_type=message_type,
        sent_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(stat)
    await db.commit()
    return stat

async def get_admin_stats(db: AsyncSession) -> dict:
    now = datetime.now(UTC).replace(tzinfo=None)
    one_day_ago = now - timedelta(days=1)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    
    # 1. Total users
    total_users_res = await db.execute(select(func.count(User.telegram_id)))
    total_users = total_users_res.scalar() or 0
    
    # 2. Active users (sent a message in last 24h / 7d)
    active_24h_res = await db.execute(
        select(func.count(func.distinct(MessageStat.user_id)))
        .where(MessageStat.sent_at >= one_day_ago)
    )
    active_24h = active_24h_res.scalar() or 0
    
    active_7d_res = await db.execute(
        select(func.count(func.distinct(MessageStat.user_id)))
        .where(MessageStat.sent_at >= seven_days_ago)
    )
    active_7d = active_7d_res.scalar() or 0

    # 3. Total food logs per day (last 24h) and per user average
    food_logs_24h_res = await db.execute(
        select(func.count(FoodLog.id))
        .where(FoodLog.logged_at >= one_day_ago)
    )
    food_logs_24h = food_logs_24h_res.scalar() or 0

    # 4. Total messages in last 24h
    msg_24h_res = await db.execute(
        select(func.count(MessageStat.id))
        .where(MessageStat.sent_at >= one_day_ago)
    )
    messages_24h = msg_24h_res.scalar() or 0

    # 5. AI API calls in last 1m / 24h
    one_minute_ago = now - timedelta(minutes=1)
    api_calls_1m_res = await db.execute(
        select(func.count(AiRequestLog.id)).where(AiRequestLog.executed_at >= one_minute_ago)
    )
    api_calls_1m = api_calls_1m_res.scalar() or 0

    api_calls_24h_res = await db.execute(
        select(func.count(AiRequestLog.id)).where(AiRequestLog.executed_at >= one_day_ago)
    )
    api_calls_24h = api_calls_24h_res.scalar() or 0

    # 6. Queued requests count
    queued_res = await db.execute(
        select(func.count(AiRequestQueue.id)).where(AiRequestQueue.status == "pending")
    )
    queued_requests = queued_res.scalar() or 0

    # --- Demographics ---
    # Languages
    lang_res = await db.execute(select(User.language, func.count(User.telegram_id)).group_by(User.language))
    languages = dict(lang_res.all())

    # Goals
    goal_res = await db.execute(select(User.goal, func.count(User.telegram_id)).group_by(User.goal))
    goals = dict(goal_res.all())

    # Genders
    sex_res = await db.execute(select(User.sex, func.count(User.telegram_id)).group_by(User.sex))
    genders = dict(sex_res.all())

    # Notifications disabled
    notif_res = await db.execute(select(func.count(User.telegram_id)).where(User.notifications_enabled == False))
    notif_disabled = notif_res.scalar() or 0

    # --- Engagement ---
    # Active 30d
    active_30d_res = await db.execute(
        select(func.count(func.distinct(MessageStat.user_id)))
        .where(MessageStat.sent_at >= thirty_days_ago)
    )
    active_30d = active_30d_res.scalar() or 0

    # New users
    new_24h_res = await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= one_day_ago))
    new_users_24h = new_24h_res.scalar() or 0

    new_7d_res = await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= seven_days_ago))
    new_users_7d = new_7d_res.scalar() or 0

    new_30d_res = await db.execute(select(func.count(User.telegram_id)).where(User.created_at >= thirty_days_ago))
    new_users_30d = new_30d_res.scalar() or 0

    # Weight logs 7d
    weight_7d_res = await db.execute(select(func.count(WeightLog.id)).where(WeightLog.logged_at >= seven_days_ago))
    weight_logs_7d = weight_7d_res.scalar() or 0

    # --- AI stats ---
    # API calls type 24h
    ai_type_res = await db.execute(
        select(AiRequestLog.request_type, func.count(AiRequestLog.id))
        .where(AiRequestLog.executed_at >= one_day_ago)
        .group_by(AiRequestLog.request_type)
    )
    ai_request_types = dict(ai_type_res.all())

    # Modality 24h
    photo_res = await db.execute(
        select(func.count(FoodLog.id))
        .where(
            and_(
                FoodLog.logged_at >= one_day_ago,
                FoodLog.image_file_id != None,
                FoodLog.image_file_id != ""
            )
        )
    )
    modality_photo_24h = photo_res.scalar() or 0

    text_res = await db.execute(
        select(func.count(FoodLog.id))
        .where(
            and_(
                FoodLog.logged_at >= one_day_ago,
                (FoodLog.image_file_id == None) | (FoodLog.image_file_id == ""),
                FoodLog.raw_text != None,
                FoodLog.raw_text != ""
            )
        )
    )
    modality_text_24h = text_res.scalar() or 0

    # Correction rate 24h
    input_count = ai_request_types.get("analyze_food_input", 0)
    adjust_count = ai_request_types.get("adjust_food_analysis", 0) + ai_request_types.get("adjust_meal_edit", 0)
    correction_rate = (adjust_count / input_count * 100.0) if input_count > 0 else 0.0

    # --- Queue Health ---
    q_status_res = await db.execute(
        select(AiRequestQueue.status, func.count(AiRequestQueue.id))
        .group_by(AiRequestQueue.status)
    )
    queue_status_counts = dict(q_status_res.all())

    latency_res = await db.execute(
        select(AiRequestQueue.created_at, AiRequestQueue.processed_at)
        .where(
            and_(
                AiRequestQueue.status == "completed",
                AiRequestQueue.processed_at >= one_day_ago
            )
        )
    )
    completed_items = latency_res.all()
    if completed_items:
        total_lat = sum((item.processed_at - item.created_at).total_seconds() for item in completed_items)
        queue_avg_latency = total_lat / len(completed_items)
    else:
        queue_avg_latency = 0.0

    error_res = await db.execute(
        select(AiRequestQueue.last_error, func.count(AiRequestQueue.id))
        .where(AiRequestQueue.last_error != None)
        .group_by(AiRequestQueue.last_error)
        .order_by(desc(func.count(AiRequestQueue.id)))
        .limit(5)
    )
    queue_errors = dict(error_res.all())

    return {
        "total_users": total_users,
        "active_users_24h": active_24h,
        "active_users_7d": active_7d,
        "food_logs_24h": food_logs_24h,
        "messages_24h": messages_24h,
        "api_calls_1m": api_calls_1m,
        "api_calls_24h": api_calls_24h,
        "queued_requests": queued_requests,
        "languages": languages,
        "goals": goals,
        "genders": genders,
        "notifications_disabled_count": notif_disabled,
        "active_users_30d": active_30d,
        "new_users_24h": new_users_24h,
        "new_users_7d": new_users_7d,
        "new_users_30d": new_users_30d,
        "weight_logs_7d": weight_logs_7d,
        "ai_request_types_24h": ai_request_types,
        "modality_photo_24h": modality_photo_24h,
        "modality_text_24h": modality_text_24h,
        "correction_rate_24h": correction_rate,
        "queue_status_counts": queue_status_counts,
        "queue_avg_latency_seconds": queue_avg_latency,
        "queue_errors": queue_errors
    }

async def delete_user(db: AsyncSession, telegram_id: int) -> bool:
    user = await get_user(db, telegram_id)
    if user:
        await db.delete(user)
        await db.commit()
        return True
    return False

async def delete_food_log(db: AsyncSession, log_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(FoodLog).where(
            and_(
                FoodLog.id == log_id,
                FoodLog.user_id == user_id
            )
        )
    )
    food_log = result.scalars().first()
    if food_log:
        await db.delete(food_log)
        await db.commit()
        return True
    return False

async def get_food_log_by_id(db: AsyncSession, log_id: int, user_id: int) -> FoodLog | None:
    result = await db.execute(
        select(FoodLog).where(
            and_(
                FoodLog.id == log_id,
                FoodLog.user_id == user_id
            )
        )
    )
    return result.scalars().first()

async def update_food_log(
    db: AsyncSession,
    log_id: int,
    user_id: int,
    items_json: list,
    calories: int,
    proteins: float,
    fats: float,
    carbs: float
) -> FoodLog | None:
    result = await db.execute(
        select(FoodLog).where(
            and_(
                FoodLog.id == log_id,
                FoodLog.user_id == user_id
            )
        )
    )
    food_log = result.scalars().first()
    if food_log:
        food_log.items_json = items_json
        food_log.calories = calories
        food_log.proteins = proteins
        food_log.fats = fats
        food_log.carbs = carbs
        await db.commit()
        await db.refresh(food_log)
        return food_log
    return None


from datetime import datetime, UTC, timedelta
from sqlalchemy import select, update, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User, FoodLog, WeightLog, MessageStat
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
    raw_text: str = None
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

    return {
        "total_users": total_users,
        "active_users_24h": active_24h,
        "active_users_7d": active_7d,
        "food_logs_24h": food_logs_24h,
        "messages_24h": messages_24h
    }

from datetime import datetime, UTC, timedelta
from sqlalchemy import select, update, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import User, FoodLog, WeightLog, MessageStat, AiRequestLog, AiRequestQueue, Streak, Achievement, HealthCard
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
    meal_type: str = "food",
    logged_at: datetime | None = None
) -> FoodLog:
    logged_at = logged_at or datetime.now(UTC)
    if logged_at.tzinfo is None:
        logged_at = logged_at.replace(tzinfo=UTC)
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
        logged_at=logged_at.astimezone(UTC).replace(tzinfo=None)
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

async def get_or_create_streak(db: AsyncSession, user_id: int, streak_type: str) -> Streak:
    result = await db.execute(
        select(Streak).where(and_(Streak.user_id == user_id, Streak.streak_type == streak_type))
    )
    streak = result.scalars().first()
    if not streak:
        streak = Streak(user_id=user_id, streak_type=streak_type, current_count=0, longest_count=0)
        db.add(streak)
        await db.commit()
        await db.refresh(streak)
    return streak

async def get_user_streaks(db: AsyncSession, user_id: int) -> list[Streak]:
    result = await db.execute(select(Streak).where(Streak.user_id == user_id))
    return list(result.scalars().all())

async def unlock_achievement(db: AsyncSession, user_id: int, achievement_key: str) -> Achievement | None:
    result = await db.execute(
        select(Achievement).where(
            and_(Achievement.user_id == user_id, Achievement.achievement_key == achievement_key)
        )
    )
    existing = result.scalars().first()
    if existing:
        return None
    ach = Achievement(user_id=user_id, achievement_key=achievement_key)
    db.add(ach)
    await db.commit()
    await db.refresh(ach)
    return ach

async def get_user_achievements(db: AsyncSession, user_id: int) -> list[Achievement]:
    result = await db.execute(
        select(Achievement)
        .where(Achievement.user_id == user_id)
        .order_by(Achievement.unlocked_at.desc())
    )
    return list(result.scalars().all())

async def save_health_card(db: AsyncSession, user_id: int, week_start: datetime, card_data: dict) -> HealthCard:
    result = await db.execute(
        select(HealthCard).where(
            and_(HealthCard.user_id == user_id, HealthCard.week_start == week_start)
        )
    )
    card = result.scalars().first()
    if card:
        card.card_data = card_data
        card.generated_at = datetime.now(UTC).replace(tzinfo=None)
    else:
        card = HealthCard(user_id=user_id, week_start=week_start, card_data=card_data)
        db.add(card)
    await db.commit()
    await db.refresh(card)
    return card

async def get_latest_health_card(db: AsyncSession, user_id: int) -> HealthCard | None:
    result = await db.execute(
        select(HealthCard)
        .where(HealthCard.user_id == user_id)
        .order_by(desc(HealthCard.week_start))
        .limit(1)
    )
    return result.scalars().first()



async def save_meal_draft(db, user_id, payload, draft_id=None, commit=True):
    """Persist a confirmation independently of the bot's in-memory FSM."""
    if draft_id is not None:
        result = await db.execute(
            update(AiRequestQueue).where(
                AiRequestQueue.id == draft_id, AiRequestQueue.user_id == user_id,
                AiRequestQueue.status == "awaiting_confirm"
            ).values(payload=payload).returning(AiRequestQueue.id)
        )
        saved_id = result.scalar_one_or_none()
        await db.commit()
        return saved_id
    draft = AiRequestQueue(user_id=user_id, chat_id=user_id, request_type="meal_draft",
                           payload=payload, status="awaiting_confirm")
    db.add(draft)
    await db.flush()
    if commit:
        await db.commit()
    return draft.id


async def get_meal_draft(db, draft_id, user_id):
    result = await db.execute(select(AiRequestQueue).where(
        AiRequestQueue.id == draft_id, AiRequestQueue.user_id == user_id,
        AiRequestQueue.status == "awaiting_confirm"))
    return result.scalar_one_or_none()


async def finish_meal_draft(db, draft_id, user_id, accept=True):
    """Atomically consume a user's draft and insert its meal once, even on retries."""
    result = await db.execute(update(AiRequestQueue).where(
        AiRequestQueue.id == draft_id, AiRequestQueue.user_id == user_id,
        AiRequestQueue.status == "awaiting_confirm"
    ).values(status="saved" if accept else "cancelled").returning(AiRequestQueue.payload))
    payload = result.scalar_one_or_none()
    if payload is None:
        return None
    if not accept:
        await db.commit()
        return True
    analysis = payload["analysis"]
    logged_at = datetime.fromisoformat(payload["logged_at"])
    meal = FoodLog(user_id=user_id, items_json=analysis["food_items"],
        calories=analysis["total_calories"], proteins=analysis["total_protein"],
        fats=analysis["total_fat"], carbs=analysis["total_carb"],
        raw_text=payload.get("raw_text"), image_file_id=payload.get("image_file_id"),
        meal_type=payload.get("meal_type", "food"),
        logged_at=logged_at.replace(tzinfo=UTC) if logged_at.tzinfo is None else logged_at)
    meal.logged_at = meal.logged_at.astimezone(UTC).replace(tzinfo=None)
    db.add(meal)
    await db.commit()
    return meal


async def get_pending_meals(db, user_id):
    result = await db.execute(select(AiRequestQueue).where(
        AiRequestQueue.user_id == user_id, AiRequestQueue.status == "awaiting_confirm"
    ).order_by(AiRequestQueue.id.asc()).limit(20))
    return list(result.scalars().all())


async def get_recent_user_activity(db: AsyncSession) -> list[dict]:
    """Latest 10 active or newly registered users in 30 days."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    last_meal = (select(func.max(FoodLog.logged_at))
                 .where(FoodLog.user_id == User.telegram_id)
                 .correlate(User).scalar_subquery())
    last_bot_use = (select(func.max(MessageStat.sent_at))
                    .where(MessageStat.user_id == User.telegram_id)
                    .correlate(User).scalar_subquery())
    recent_activity = func.coalesce(last_bot_use, User.created_at)
    result = await db.execute(select(
        User.telegram_id, User.name, User.created_at.label("joined_at"),
        last_meal.label("last_meal_at"), last_bot_use.label("last_bot_use_at")
    ).where(recent_activity >= cutoff)
     .order_by(recent_activity.desc(), User.telegram_id.desc()).limit(10))
    return [dict(row) for row in result.mappings()]


# Medication access always includes the authenticated owner's ID.
from src.database.models import Medication, MedicationReminder, MedicationIntake
from sqlalchemy.orm import selectinload


async def list_medications(db, user_id):
    return list((await db.scalars(select(Medication).where(Medication.user_id == user_id)
                                 .order_by(Medication.id.desc()))).all())


async def save_medication(db, user_id, values, medication_id=None):
    item = await db.scalar(select(Medication).where(Medication.id == medication_id,
                                                  Medication.user_id == user_id)) if medication_id else None
    if medication_id and item is None:
        return None
    if item is None:
        item = Medication(user_id=user_id)
        db.add(item)
    for key in ('name', 'category', 'details'):
        setattr(item, key, values[key])
    await db.commit()
    return item


async def list_medication_reminders(db, user_id):
    return list((await db.scalars(select(MedicationReminder).where(MedicationReminder.user_id == user_id)
        .options(selectinload(MedicationReminder.medication)).order_by(MedicationReminder.id))).all())


async def save_medication_reminder(db, user_id, values, reminder_id=None):
    owned = await db.scalar(select(Medication.id).where(Medication.id == values['medication_id'],
                                                       Medication.user_id == user_id))
    if owned is None:
        return None
    item = await db.scalar(select(MedicationReminder).where(MedicationReminder.id == reminder_id,
        MedicationReminder.user_id == user_id)) if reminder_id else None
    if reminder_id and item is None:
        return None
    if item is None:
        item = MedicationReminder(user_id=user_id)
        db.add(item)
    for key in ('medication_id', 'weekdays', 'reminder_time', 'start_date', 'end_date', 'dose'):
        setattr(item, key, values[key])
    await db.commit()
    return item


async def delete_medication_item(db, user_id, item_id, reminder=False):
    model = MedicationReminder if reminder else Medication
    item = await db.scalar(select(model).where(model.id == item_id, model.user_id == user_id))
    if item is None:
        return False
    await db.delete(item)
    await db.commit()
    return True


async def get_medication_intakes(db, user_id, start, end):
    return list((await db.scalars(select(MedicationIntake).where(MedicationIntake.user_id == user_id,
        MedicationIntake.scheduled_at >= start, MedicationIntake.scheduled_at <= end)
        .options(selectinload(MedicationIntake.reminder).selectinload(MedicationReminder.medication))
        .order_by(MedicationIntake.scheduled_at.desc()))).all())


async def mark_medication_intake(db, user_id, intake_id, status):
    if status not in ('taken', 'skipped'):
        raise ValueError('Invalid intake status')
    result = await db.execute(update(MedicationIntake).where(MedicationIntake.id == intake_id,
        MedicationIntake.user_id == user_id, MedicationIntake.status == 'unmarked',
        MedicationIntake.scheduled_at <= datetime.now(UTC).replace(tzinfo=None))
        .values(status=status, marked_at=datetime.now(UTC).replace(tzinfo=None)))
    await db.commit()
    return result.rowcount > 0


async def create_medication_occurrence(db, reminder, day, scheduled_at):
    from sqlalchemy.exc import IntegrityError
    existing = await db.scalar(select(MedicationIntake).where(
        MedicationIntake.reminder_id == reminder.id, MedicationIntake.local_date == day))
    if existing:
        return existing
    item = MedicationIntake(user_id=reminder.user_id, reminder_id=reminder.id,
                            local_date=day, scheduled_at=scheduled_at)
    try:
        async with db.begin_nested():
            db.add(item)
            await db.flush()
        await db.commit()
    except IntegrityError:
        return await db.scalar(select(MedicationIntake).where(
            MedicationIntake.reminder_id == reminder.id, MedicationIntake.local_date == day))
    return item


async def claim_medication_delivery(db, intake_id):
    # Commit before Telegram send: a retry/restart cannot send the same dose twice.
    result = await db.execute(update(MedicationIntake).where(MedicationIntake.id == intake_id,
        MedicationIntake.delivery_status == 'pending').values(delivery_status='sending'))
    await db.commit()
    return result.rowcount > 0


async def finish_medication_delivery(db, intake_id, status):
    await db.execute(update(MedicationIntake).where(MedicationIntake.id == intake_id).values(
        delivery_status=status, notified_at=datetime.now(UTC).replace(tzinfo=None) if status == 'sent' else None))
    await db.commit()


async def get_medication_photo_request(db, user_id, request_id):
    return await db.scalar(select(AiRequestQueue).where(AiRequestQueue.user_id == user_id,
        AiRequestQueue.id == request_id, AiRequestQueue.request_type == 'medication_photo'))


async def confirm_medication_photo(db, user_id, request_id):
    # Lock the owned draft so duplicate confirmations cannot create two products.
    request = await db.scalar(select(AiRequestQueue).where(AiRequestQueue.id == request_id,
        AiRequestQueue.user_id == user_id, AiRequestQueue.request_type == 'medication_photo').with_for_update())
    if not request or not request.payload.get('bot_category') or not request.payload.get('result'):
        return None
    if request.payload.get('medication_id'):
        return await db.scalar(select(Medication).where(Medication.id == request.payload['medication_id'],
                                                       Medication.user_id == user_id))
    from src.services.medications import medication_values
    try:
        values = medication_values({**request.payload['result'], 'category': request.payload['bot_category']})
    except ValueError:
        return None
    item = Medication(user_id=user_id, **values)
    db.add(item)
    await db.flush()
    request.payload = {**request.payload, 'medication_id': item.id}
    await db.commit()
    return item

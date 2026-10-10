from datetime import datetime, UTC, timedelta
from sqlalchemy import select, update, delete, func, desc, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models import (
    User, FoodLog, WeightLog, MessageStat, AiRequestLog, AiRequestAttempt,
    AiRequestQueue, Streak, Achievement, HealthCard
)
from src.config import settings

EXECUTABLE_QUEUE_TYPES = ('analyze_food_input', 'adjust_food_analysis',
                          'adjust_meal_edit', 'generate_report', 'medication_photo')


async def claim_queue_task(db, task_id):
    """Compare-and-set prevents executing a task cancelled after selection."""
    now = datetime.now(UTC).replace(tzinfo=None)
    result = await db.execute(update(AiRequestQueue).where(
        AiRequestQueue.id == task_id, AiRequestQueue.status == 'pending',
        AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES),
        (AiRequestQueue.next_retry_at.is_(None) | (AiRequestQueue.next_retry_at <= now)),
    ).values(status='processing').execution_options(synchronize_session=False))
    await db.commit()
    return result.rowcount == 1


async def daily_report_intake_state(db, user_id, date_str):
    from datetime import date
    from src.database.models import MedicationIntake
    rows = (await db.execute(select(MedicationIntake).where(
        MedicationIntake.user_id == user_id,
        MedicationIntake.local_date == date.fromisoformat(date_str),
    ).order_by(MedicationIntake.id))).scalars()
    return [(row.id, row.reminder_id, row.status) for row in rows]


async def ai_quota_usage(db, since, user_id=None):
    """Include pre-upgrade successful calls without double-counting new attempts."""
    from src.database.models import AiRequestAttempt
    first_attempt = select(func.min(AiRequestAttempt.executed_at)).scalar_subquery()
    counts, oldest = 0, None
    for model in (AiRequestAttempt, AiRequestLog):
        query = select(func.count(model.id), func.min(model.executed_at)).where(model.executed_at >= since)
        if model is AiRequestLog:
            query = query.where(model.executed_at < func.coalesce(first_attempt, datetime.now(UTC).replace(tzinfo=None)))
        if user_id is not None:
            query = query.where(model.user_id == user_id)
        count, start = (await db.execute(query)).one()
        counts += count
        if start is not None:
            oldest = min(oldest, start) if oldest else start
    return counts, oldest


async def ai_quota_exhausted(db, user_id=None, now=None):
    import math
    now = now or datetime.now(UTC).replace(tzinfo=None)
    windows = [("minute", 60, settings.AI_REQUESTS_PER_MINUTE, None),
               ("day", 86400, settings.AI_REQUESTS_PER_DAY, None)]
    if user_id is not None:
        windows.append(("user", 60, settings.AI_USER_REQUESTS_PER_MINUTE, user_id))
    for scope, seconds, limit, owner in windows:
        count, oldest = await ai_quota_usage(db, now - timedelta(seconds=seconds), owner)
        if count >= limit:
            return scope, max(1, math.ceil((oldest + timedelta(seconds=seconds) - now).total_seconds()) + 1)
    return None


async def reserve_ai_attempt(db, user_id, request_type):
    from src.database.models import AiRequestAttempt
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(72139403)"))
    now = datetime.now(UTC).replace(tzinfo=None)
    exhausted = await ai_quota_exhausted(db, user_id, now)
    if not exhausted:
        cutoff = now - timedelta(days=1)
        await db.execute(delete(AiRequestAttempt).where(AiRequestAttempt.executed_at < cutoff))
        await db.execute(delete(AiRequestLog).where(AiRequestLog.executed_at < cutoff))
        db.add(AiRequestAttempt(user_id=user_id, request_type=request_type, executed_at=now))
    await db.commit()  # Release the advisory lock before network I/O, including denial.
    return exhausted

async def record_event(db, user_id, name):
    from src.database.models import ProductEvent
    db.add(ProductEvent(user_id=user_id, name=name))
    # Retain only 90 days; no messages, photos or nutritional values in telemetry.
    await db.execute(delete(ProductEvent).where(ProductEvent.occurred_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)))
    await db.commit()

async def get_product_metrics(db, now=None):
    from src.database.models import ProductEvent
    from statistics import median
    now = now or datetime.now(UTC).replace(tzinfo=None)
    rows = (await db.execute(select(ProductEvent).where(ProductEvent.occurred_at >= now - timedelta(days=30)))).scalars().all()
    by_user = {}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)
    starts = {uid: min(r.occurred_at for r in events if r.name == 'onboarding_started')
              for uid, events in by_user.items() if any(r.name == 'onboarding_started' for r in events)}
    completed = {uid for uid, events in by_user.items() if uid in starts and any(r.name == 'onboarding_completed' and r.occurred_at >= starts[uid] for r in events)}
    first_meal = []
    retention = {}
    for uid in completed:
        meals = [r.occurred_at for r in by_user[uid] if r.name in ('meal_confirmed', 'meal_manual_saved') and r.occurred_at >= starts[uid]]
        if meals:
            first_meal.append((min(meals) - starts[uid]).total_seconds())
    for day in (1, 7):
        eligible = [uid for uid, start in starts.items() if (now.date() - start.date()).days > day]
        returned = sum(any((r.occurred_at.date() - starts[uid].date()).days == day and r.name == 'active' for r in by_user[uid]) for uid in eligible)
        retention[f'D{day}'] = dict(returned=returned, eligible=len(eligible))
    active_days = {(r.user_id, r.occurred_at.date()) for r in rows if r.name == 'active'}
    meal_days = {(r.user_id, r.occurred_at.date()) for r in rows if r.name in ('meal_confirmed', 'meal_manual_saved')}
    drafts = (await db.execute(select(AiRequestQueue).where(AiRequestQueue.request_type == 'meal_draft', AiRequestQueue.created_at >= now - timedelta(days=30)))).scalars().all()
    return dict(onboarding_started=len(starts), onboarding_completed=len(completed),
                first_meal_median_seconds=round(median(first_meal)) if first_meal else None,
                analyzed=len(drafts),
                confirmed=sum(d.status == 'saved' for d in drafts), retention=retention,
                active_days=len(active_days), active_days_with_meal=len(active_days & meal_days),
                events={name: sum(r.name == name for r in rows) for name in ('meal_opened', 'meal_submitted', 'report_opened')})

async def add_water_log(db, user_id, milliliters):
    from src.database.models import WaterLog
    row = WaterLog(user_id=user_id, milliliters=milliliters)
    db.add(row)
    await db.commit()
    return row

async def water_total(db, user_id, start, end):
    from src.database.models import WaterLog
    return (await db.execute(select(func.coalesce(func.sum(WaterLog.milliliters), 0)).where(
        WaterLog.user_id == user_id, WaterLog.logged_at >= start, WaterLog.logged_at <= end))).scalar_one()

async def food_queue_status(db, user_id):
    rows = (await db.execute(select(AiRequestQueue.status).where(AiRequestQueue.user_id == user_id,
        AiRequestQueue.request_type == 'analyze_food_input',
        AiRequestQueue.created_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)))).scalars().all()
    return dict(pending=sum(status in ('pending', 'processing') for status in rows), failed=sum(status == 'failed' for status in rows))

async def save_weight_entry(db, user_id, weight):
    from src.utils.formulas import calculate_targets
    user = await get_user(db, user_id)
    if not user or user.is_blocked:
        return None
    baseline = (await db.execute(select(WeightLog).where(WeightLog.user_id == user_id).order_by(WeightLog.logged_at).limit(1))).scalar_one_or_none()
    targets = calculate_targets(weight, user.height_cm, user.age, user.sex, user.activity_level, user.goal)
    user.weight_kg = weight
    for attr, key in [('target_calories', 'calories'), ('target_protein', 'protein'), ('target_fat', 'fat'), ('target_carb', 'carb')]:
        setattr(user, attr, targets[key])
    await add_weight_log(db, user_id, weight)
    return baseline

async def save_ux_settings(db, user_id, values):
    user = await get_user(db, user_id)
    if not user or user.is_blocked:
        return None
    user.timezone = values['timezone']
    user.notifications_enabled = values['notifications_enabled']
    user.ux_preferences = {key: values[key] for key in ('frequency', 'quiet_start', 'quiet_end')}
    await db.commit()
    return user

async def get_saved_report(db, user_id, report_id):
    return (await db.execute(select(AiRequestQueue).where(AiRequestQueue.id == report_id,
        AiRequestQueue.user_id == user_id, AiRequestQueue.request_type == 'report_snapshot'))).scalar_one_or_none()

async def save_report_snapshot(db, user_id, text, *, commit=True, **metadata):
    payload = {'text': text, **metadata}
    row = AiRequestQueue(user_id=user_id, chat_id=user_id, request_type='report_snapshot',
                         status='completed', payload=payload)
    db.add(row)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return row.id

async def get_latest_report_snapshot(db, user_id, report_type='daily'):
    result = await db.execute(
        select(AiRequestQueue)
        .where(
            AiRequestQueue.user_id == user_id,
            AiRequestQueue.request_type == 'report_snapshot',
            AiRequestQueue.status == 'completed'
        )
        .order_by(AiRequestQueue.id.desc())
    )
    for row in result.scalars().all():
        if row.payload and row.payload.get('report_type', 'daily') == report_type:
            return row
    return None

async def get_user(db: AsyncSession, telegram_id: int) -> User:
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalars().first()

async def get_all_users(db: AsyncSession, include_blocked: bool = True) -> list[User]:
    stmt = select(User)
    if not include_blocked:
        stmt = stmt.where(User.is_blocked == False, User.blocked_at.is_(None))
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def create_or_update_user(db: AsyncSession, telegram_id: int, **kwargs) -> User:
    user = await get_user(db, telegram_id)
    # Check if this user is in the settings.ADMIN_USER_IDS or specified in kwargs
    is_admin = kwargs.pop("is_admin", telegram_id in settings.ADMIN_USER_IDS)

    if user:
        for key, value in kwargs.items():
            setattr(user, key, value)
        user.is_admin = is_admin
        if "blocked_at" not in kwargs:
            user.blocked_at = None
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

async def mark_user_blocked(db: AsyncSession, telegram_id: int) -> bool:
    user = await get_user(db, telegram_id)
    if user:
        user.notifications_enabled = False
        if user.blocked_at is None:
            user.blocked_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
        return True
    return False

async def mark_user_unblocked(db: AsyncSession, telegram_id: int) -> bool:
    user = await get_user(db, telegram_id)
    if user:
        user.blocked_at = None
        user.notifications_enabled = True
        await db.commit()
        return True
    return False

def is_user_bot_blocked(user: User | None) -> bool:
    if not user:
        return False
    blocked_at = getattr(user, "blocked_at", None)
    return isinstance(blocked_at, datetime)

def is_user_deleted(user: User | None, grace_period_days: int = 3) -> bool:
    if not user:
        return False
    blocked_at = getattr(user, "blocked_at", None)
    if not isinstance(blocked_at, datetime):
        return False
    now = datetime.now(UTC).replace(tzinfo=None)
    if blocked_at.tzinfo is not None:
        blocked_at = blocked_at.astimezone(UTC).replace(tzinfo=None)
    return (now - blocked_at) >= timedelta(days=grace_period_days)

async def purge_blocked_users(db: AsyncSession, grace_period_days: int = 3) -> int:
    threshold = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=grace_period_days)
    stmt = select(User.telegram_id).where(
        User.blocked_at.isnot(None),
        User.blocked_at <= threshold
    )
    result = await db.execute(stmt)
    user_ids = list(result.scalars().all())
    for uid in user_ids:
        await delete_user(db, uid)
    return len(user_ids)

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


async def get_queue_health(db: AsyncSession, window_hours: int = 24) -> dict:
    """
    Returns AI queue health metrics:
    - status_counts: dict of counts by status
    - pending, processing, completed, failed, cancelled: counts
    - oldest_pending_age_seconds: age in seconds of oldest pending item (or 0.0)
    - oldest_processing_age_seconds: age in seconds of oldest processing item (or 0.0)
    - completed_24h, failed_24h: counts in window
    - avg_latency_seconds: average processing latency for completed items in window
    - error_rate: ratio of failed / (completed + failed) in window
    - queue_errors: top recent error messages with counts
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    window_start = now - timedelta(hours=window_hours)

    # 1. Status counts
    q_status_res = await db.execute(
        select(AiRequestQueue.status, func.count(AiRequestQueue.id))
        .where(AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES))
        .group_by(AiRequestQueue.status)
    )
    status_counts = dict(q_status_res.all())
    pending_count = status_counts.get("pending", 0)
    processing_count = status_counts.get("processing", 0)
    completed_total = status_counts.get("completed", 0)
    failed_total = status_counts.get("failed", 0)
    cancelled_total = status_counts.get("cancelled", 0)

    # 2. Oldest pending item age
    pending_oldest_res = await db.execute(
        select(AiRequestQueue.created_at)
        .where(AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES))
        .where(AiRequestQueue.status == "pending")
        .order_by(AiRequestQueue.created_at.asc())
        .limit(1)
    )
    oldest_pending_created = pending_oldest_res.scalar()
    if oldest_pending_created:
        t_naive = oldest_pending_created.replace(tzinfo=None) if oldest_pending_created.tzinfo else oldest_pending_created
        oldest_pending_age_seconds = max(0.0, round((now - t_naive).total_seconds(), 2))
    else:
        oldest_pending_age_seconds = 0.0

    # 3. Oldest processing item age
    proc_oldest_res = await db.execute(
        select(AiRequestQueue.created_at)
        .where(AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES))
        .where(AiRequestQueue.status == "processing")
        .order_by(AiRequestQueue.created_at.asc())
        .limit(1)
    )
    oldest_proc_created = proc_oldest_res.scalar()
    if oldest_proc_created:
        t_naive = oldest_proc_created.replace(tzinfo=None) if oldest_proc_created.tzinfo else oldest_proc_created
        oldest_processing_age_seconds = max(0.0, round((now - t_naive).total_seconds(), 2))
    else:
        oldest_processing_age_seconds = 0.0

    # 4. Completed items in window
    latency_res = await db.execute(
        select(AiRequestQueue.created_at, AiRequestQueue.processed_at)
        .where(AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES))
        .where(
            and_(
                AiRequestQueue.status == "completed",
                AiRequestQueue.processed_at >= window_start
            )
        )
    )
    completed_items = latency_res.all()
    completed_window = len(completed_items)
    if completed_items:
        valid_lats = [
            (item.processed_at - item.created_at).total_seconds()
            for item in completed_items
            if item.processed_at and item.created_at
        ]
        avg_latency = round(sum(valid_lats) / len(valid_lats), 2) if valid_lats else 0.0
    else:
        avg_latency = 0.0

    # 5. Failed items in window
    failed_win_res = await db.execute(
        select(func.count(AiRequestQueue.id))
        .where(
            and_(
                AiRequestQueue.status == "failed",
                func.coalesce(AiRequestQueue.processed_at, AiRequestQueue.created_at) >= window_start,
                AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES)
            )
        )
    )
    failed_window = failed_win_res.scalar() or 0

    total_window = completed_window + failed_window
    error_rate = round(failed_window / total_window, 4) if total_window > 0 else 0.0

    # 6. Top errors
    error_res = await db.execute(
        select(AiRequestQueue.last_error, func.count(AiRequestQueue.id))
        .where(AiRequestQueue.last_error != None,
               AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES),
               func.coalesce(AiRequestQueue.processed_at, AiRequestQueue.created_at) >= window_start)
        .group_by(AiRequestQueue.last_error)
        .order_by(desc(func.count(AiRequestQueue.id)))
        .limit(5)
    )
    queue_errors = dict(error_res.all())

    return {
        "pending": pending_count,
        "processing": processing_count,
        "completed": completed_total,
        "failed": failed_total,
        "cancelled": cancelled_total,
        "status_counts": status_counts,
        "completed_24h": completed_window,
        "failed_24h": failed_window,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "oldest_processing_age_seconds": oldest_processing_age_seconds,
        "avg_latency_seconds": avg_latency,
        "error_rate": error_rate,
        "queue_errors": queue_errors,
    }


async def get_failed_queue_tasks(db: AsyncSession, limit: int = 5, offset: int = 0):
    """Returns paginated failed queue tasks and total count."""
    total_res = await db.execute(
        select(func.count(AiRequestQueue.id)).where(AiRequestQueue.status == "failed")
    )
    total = total_res.scalar() or 0

    tasks_res = await db.execute(
        select(AiRequestQueue)
        .where(AiRequestQueue.status == "failed")
        .order_by(AiRequestQueue.id.desc())
        .limit(limit)
        .offset(offset)
    )
    tasks = list(tasks_res.scalars().all())
    return tasks, total


async def get_queue_task(db: AsyncSession, task_id: int):
    """Fetches a queue task by its ID."""
    res = await db.execute(select(AiRequestQueue).where(AiRequestQueue.id == task_id))
    return res.scalar_one_or_none()


async def retry_queue_task(db: AsyncSession, task_id: int):
    """
    Safely resets a failed or cancelled queue task back to 'pending'.
    Returns (success: bool, message: str, task: Optional[AiRequestQueue]).
    Validates task existence, non-completed state, and that user exists and is not blocked.
    """
    task = await get_queue_task(db, task_id)
    if not task:
        return False, "Task not found", None
    if task.status not in ("failed", "cancelled"):
        return False, f"Task status is '{task.status}', only failed/cancelled tasks can be retried", task

    if task.request_type not in EXECUTABLE_QUEUE_TYPES:
        return False, "This record is not an executable task", task
    user = await get_user(db, task.user_id)
    if not user:
        return False, "Task owner user not found in database", task
    if user.is_blocked:
        return False, "Task owner user is blocked", task

    owner_allowed = select(User.telegram_id).where(
        User.telegram_id == AiRequestQueue.user_id, User.is_blocked.is_(False)
    ).exists()
    result = await db.execute(update(AiRequestQueue).where(
        AiRequestQueue.id == task_id, AiRequestQueue.status.in_(['failed', 'cancelled']),
        AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES), owner_allowed,
    ).values(status='pending', error_message=None, last_error=None, retry_count=0,
             processed_at=None, next_retry_at=datetime.now(UTC).replace(tzinfo=None))
      .execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(task)
    if result.rowcount != 1:
        return False, "Task or owner changed; refresh before retrying", task
    return True, "Task scheduled for retry", task


async def cancel_queue_task(db: AsyncSession, task_id: int):
    """
    Cancels a failed or pending queue task.
    Returns (success: bool, message: str, task: Optional[AiRequestQueue]).
    """
    task = await get_queue_task(db, task_id)
    if not task:
        return False, "Task not found", None
    if task.request_type not in EXECUTABLE_QUEUE_TYPES:
        return False, "This record is not an executable task", task
    if task.status == 'cancelled':
        return False, "Task is already cancelled", task
    if task.status not in ('pending', 'failed'):
        return False, "Only pending/failed tasks can be cancelled; running work cannot be interrupted", task
    result = await db.execute(update(AiRequestQueue).where(
        AiRequestQueue.id == task_id, AiRequestQueue.status.in_(['pending', 'failed']),
        AiRequestQueue.request_type.in_(EXECUTABLE_QUEUE_TYPES),
    ).values(status='cancelled', error_message='Cancelled by admin', next_retry_at=None)
      .execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(task)
    if result.rowcount != 1:
        return False, "Task changed; refresh before cancelling", task
    return True, "Task cancelled", task


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
    q_health = await get_queue_health(db, window_hours=24)
    queue_status_counts = q_health["status_counts"]
    queue_avg_latency = q_health["avg_latency_seconds"]
    queue_errors = q_health["queue_errors"]
    queue_oldest_pending_age = q_health["oldest_pending_age_seconds"]
    queue_error_rate = q_health["error_rate"]

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
        "queue_errors": queue_errors,
        "queue_oldest_pending_age_seconds": queue_oldest_pending_age,
        "queue_error_rate_24h": queue_error_rate
    }

async def delete_user(db: AsyncSession, telegram_id: int) -> bool:
    from src.database.models import ProductEvent, AiRequestAttempt
    await db.execute(delete(ProductEvent).where(ProductEvent.user_id == telegram_id))
    # Retain anonymous 24-hour quota usage without retaining a deleted identity.
    for model in (AiRequestAttempt, AiRequestLog):
        await db.execute(update(model).where(model.user_id == telegram_id).values(user_id=None))
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
    from src.database.models import ProductEvent
    db.add(ProductEvent(user_id=user_id, name="meal_analyzed"))
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
    from src.database.models import ProductEvent
    db.add(ProductEvent(user_id=user_id, name="meal_confirmed"))
    await db.commit()
    return meal


async def get_pending_meals(db, user_id):
    result = await db.execute(select(AiRequestQueue).where(
        AiRequestQueue.user_id == user_id, AiRequestQueue.status == "awaiting_confirm"
    ).order_by(AiRequestQueue.id.asc()).limit(20))
    return list(result.scalars().all())


async def get_pending_meals_page(db, user_id, page=1):
    filters = (AiRequestQueue.user_id == user_id,
               AiRequestQueue.request_type == 'meal_draft',
               AiRequestQueue.status == 'awaiting_confirm')
    total = await db.scalar(select(func.count()).select_from(AiRequestQueue).where(*filters))
    pages = max(1, (total + 4) // 5)
    page = min(max(1, page), pages)
    rows = (await db.execute(select(AiRequestQueue).where(*filters)
        .order_by(AiRequestQueue.id).offset((page - 1) * 5).limit(5))).scalars().all()
    return list(rows), total, page, pages


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


async def get_admin_users_page(db, blocked=False, page=0, page_size=5):
    """All registered users, with the latest recorded chat or WebApp activity."""
    from sqlalchemy import case
    from src.database.models import ProductEvent
    total = await db.scalar(select(func.count(User.telegram_id)).where(User.is_blocked == blocked))
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(0, page), pages - 1)
    messages = select(func.max(MessageStat.sent_at)).where(MessageStat.user_id == User.telegram_id).correlate(User).scalar_subquery()
    events = select(func.max(ProductEvent.occurred_at)).where(
        ProductEvent.user_id == User.telegram_id, ProductEvent.name == 'active').correlate(User).scalar_subquery()
    last_active = case((messages.is_(None), events), (events.is_(None), messages),
                       (messages >= events, messages), else_=events)
    rows = await db.execute(select(User.telegram_id, User.name, User.username,
        User.created_at.label('joined_at'), last_active.label('last_active_at'), User.is_admin)
        .where(User.is_blocked == blocked).order_by(User.created_at.desc(), User.telegram_id.desc())
        .offset(page * page_size).limit(page_size))
    return dict(users=[dict(row) for row in rows.mappings()], total=total, page=page, pages=pages)


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


async def cleanup_operational_logs(db: AsyncSession, retention_days: int = 30) -> dict[str, int]:
    """Purges operational records older than retention_days (attempts, request logs, stats, completed queue)."""
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    attempts_res = await db.execute(
        delete(AiRequestAttempt).where(AiRequestAttempt.executed_at < cutoff)
    )
    logs_res = await db.execute(
        delete(AiRequestLog).where(AiRequestLog.executed_at < cutoff)
    )
    stats_res = await db.execute(
        delete(MessageStat).where(MessageStat.sent_at < cutoff)
    )
    queue_res = await db.execute(
        delete(AiRequestQueue).where(
            AiRequestQueue.status.in_(["completed", "cancelled"]),
            AiRequestQueue.created_at < cutoff,
        )
    )
    await db.commit()
    return {
        "attempts": attempts_res.rowcount or 0,
        "logs": logs_res.rowcount or 0,
        "stats": stats_res.rowcount or 0,
        "queue": queue_res.rowcount or 0,
    }


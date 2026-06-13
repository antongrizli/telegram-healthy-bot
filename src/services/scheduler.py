from datetime import datetime, UTC, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.services import gemini, rate_limiter
from src.utils import i18n_locales
from src.utils.escape import split_message

scheduler = AsyncIOScheduler()

async def send_multipart_message(bot: Bot, chat_id: int, text: str, parse_mode: str = "Markdown"):
    """
    Sends a potentially large message to a user, splitting it if it exceeds the max limit.
    Falls back to unparsed mode if the formatting tags break across splits.
    """
    parts = split_message(text, max_length=4000)
    for part in parts:
        if not part.strip():
            continue
        try:
            await bot.send_message(chat_id, part, parse_mode=parse_mode)
        except Exception:
            try:
                await bot.send_message(chat_id, part)
            except Exception as e:
                print(f"Failed to send plain message part to {chat_id}: {e}")

async def send_daily_reminder(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or not user.notifications_enabled:
            return
        
        # Check if they logged any food today. If yes, skip reminder.
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        now_local = datetime.now(user_tz)
        start_of_day_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=user_tz)
        start_date = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        if len(logs) > 0:
            return

        try:
            msg = i18n_locales.get_text("daily_reminder", user.language)
            await bot.send_message(user_id, msg, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending daily reminder to {user_id}: {e}")

async def generate_and_send_report_direct(bot: Bot, db: AsyncSession, user, report_type: str):
    """Compiles logs, generates an AI report, logs the request, and sends it to the user."""
    user_id = user.telegram_id
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    now_local = datetime.now(user_tz)
    
    if report_type == "daily":
        start_of_day_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=user_tz)
        start_date = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        start_weight_date = (start_of_day_local - timedelta(days=7)).astimezone(UTC).replace(tzinfo=None)
        weight_logs = await crud.get_weight_logs(db, user_id, start_weight_date, end_date)
        
    elif report_type == "weekly":
        start_of_week_local = now_local - timedelta(days=7)
        start_date = start_of_week_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)
        
    else:  # monthly
        start_of_month_local = now_local - timedelta(days=30)
        start_date = start_of_month_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)

    profile_dict = {
        "name": user.name,
        "sex": user.sex,
        "age": user.age,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "activity_level": user.activity_level,
        "goal": user.goal,
        "target_calories": user.target_calories,
        "target_protein": user.target_protein,
        "target_fat": user.target_fat,
        "target_carb": user.target_carb
    }
    
    report = await gemini.generate_report(profile_dict, food_logs, weight_logs, report_type, user.language)
    await rate_limiter.log_ai_request(db, user_id=user_id, request_type="generate_report")

    if report_type == "daily":
        total_cal = sum(log.calories for log in food_logs)
        total_p = sum(log.proteins for log in food_logs)
        total_f = sum(log.fats for log in food_logs)
        total_c = sum(log.carbs for log in food_logs)
        
        header = (
            f"📅 *Daily Report Summary*:\n"
            f"• *Calories*: {total_cal} / {user.target_calories} kcal\n"
            f"• *Protein*: {total_p:.1f} / {user.target_protein}g\n"
            f"• *Fat*: {total_f:.1f} / {user.target_fat}g\n"
            f"• *Carbs*: {total_c:.1f} / {user.target_carb}g\n\n"
        )
    elif report_type == "weekly":
        header = f"{i18n_locales.get_text('weekly_report_header', user.language)}\n\n"
    else:
        header = f"{i18n_locales.get_text('monthly_report_header', user.language)}\n\n"
        
    await send_multipart_message(bot, user_id, header + report, parse_mode="Markdown")

async def send_daily_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "daily"}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await bot.send_message(
                user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "daily")
        except Exception as e:
            print(f"Error sending daily report to {user_id}: {e}")
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "daily"}
            )
            await bot.send_message(
                user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

async def send_weekly_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "weekly"}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await bot.send_message(
                user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "weekly")
        except Exception as e:
            print(f"Error sending weekly report to {user_id}: {e}")
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "weekly"}
            )
            await bot.send_message(
                user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

async def send_monthly_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "monthly"}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await bot.send_message(
                user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "monthly")
        except Exception as e:
            print(f"Error sending monthly report to {user_id}: {e}")
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "monthly"}
            )
            await bot.send_message(
                user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

def reschedule_user_jobs(bot: Bot, user):
    user_id = user.telegram_id
    
    # Remove existing jobs for this user
    for suffix in ["_reminder", "_daily", "_weekly", "_monthly"]:
        job_id = f"user_{user_id}{suffix}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            
    if user.is_blocked:
        return
        
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    if user.notifications_enabled:
        # 1. Daily Food Log Reminder
        r_time = user.food_reminder_time or time(11, 0)
        scheduler.add_job(
            send_daily_reminder,
            CronTrigger(hour=r_time.hour, minute=r_time.minute, timezone=user_tz),
            id=f"user_{user_id}_reminder",
            args=[bot, user_id],
            replace_existing=True
        )
        
        # 2. Daily Report
        d_time = user.daily_report_time or time(21, 0)
        scheduler.add_job(
            send_daily_report,
            CronTrigger(hour=d_time.hour, minute=d_time.minute, timezone=user_tz),
            id=f"user_{user_id}_daily",
            args=[bot, user_id],
            replace_existing=True
        )
        
        # 3. Weekly Report
        w_day = user.weekly_report_day or 0  # 0 = Sunday
        scheduler.add_job(
            send_weekly_report,
            CronTrigger(day_of_week=w_day, hour=21, minute=0, timezone=user_tz),
            id=f"user_{user_id}_weekly",
            args=[bot, user_id],
            replace_existing=True
        )
        
        # 4. Monthly Report
        scheduler.add_job(
            send_monthly_report,
            CronTrigger(day=1, hour=21, minute=0, timezone=user_tz),
            id=f"user_{user_id}_monthly",
            args=[bot, user_id],
            replace_existing=True
        )

def remove_user_jobs(user_id: int):
    for suffix in ["_reminder", "_daily", "_weekly", "_monthly"]:
        job_id = f"user_{user_id}{suffix}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

async def init_scheduler(bot: Bot):

    async with AsyncSessionLocal() as db:
        users = await crud.get_all_users(db, include_blocked=False)
        for user in users:
            reschedule_user_jobs(bot, user)
            
    scheduler.start()

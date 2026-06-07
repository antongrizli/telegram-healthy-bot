from datetime import datetime, UTC, time, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.services import gemini
from src.utils import i18n_locales

scheduler = AsyncIOScheduler()

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

async def send_daily_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        now_local = datetime.now(user_tz)
        start_of_day_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=user_tz)
        
        start_date = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        
        # Past 7 days weight logs to show trend
        start_weight_date = (start_of_day_local - timedelta(days=7)).astimezone(UTC).replace(tzinfo=None)
        weight_logs = await crud.get_weight_logs(db, user_id, start_weight_date, end_date)
        
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            
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
            
            report = await gemini.generate_report(profile_dict, food_logs, weight_logs, "daily", user.language)
            
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
            
            await bot.send_message(user_id, header + report, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending daily report to {user_id}: {e}")

async def send_weekly_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        now_local = datetime.now(user_tz)
        start_of_week_local = now_local - timedelta(days=7)
        
        start_date = start_of_week_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)
        
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            
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
            
            report = await gemini.generate_report(profile_dict, food_logs, weight_logs, "weekly", user.language)
            header = f"{i18n_locales.get_text('weekly_report_header', user.language)}\n\n"
            await bot.send_message(user_id, header + report, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending weekly report to {user_id}: {e}")

async def send_monthly_report(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked:
            return
        
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        now_local = datetime.now(user_tz)
        start_of_month_local = now_local - timedelta(days=30)
        
        start_date = start_of_month_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)
        
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            
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
            
            report = await gemini.generate_report(profile_dict, food_logs, weight_logs, "monthly", user.language)
            header = f"{i18n_locales.get_text('monthly_report_header', user.language)}\n\n"
            await bot.send_message(user_id, header + report, parse_mode="Markdown")
        except Exception as e:
            print(f"Error sending monthly report to {user_id}: {e}")

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

async def init_scheduler(bot: Bot):
    async with AsyncSessionLocal() as db:
        users = await crud.get_all_users(db, include_blocked=False)
        for user in users:
            reschedule_user_jobs(bot, user)
            
    scheduler.start()

import logging
import hashlib
import json
from time import monotonic
from datetime import datetime, UTC, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from src.database.connection import AsyncSessionLocal
from src.database.models import AiRequestQueue
from src.database import crud
from src.services import gemini, rate_limiter
from src.utils import i18n_locales
from src.utils.escape import split_message
from src.config import settings

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


class SchedulerLogSummary:
    """Aggregate execution outcomes without exposing per-user job identifiers."""

    job_id = "scheduler_log_summary"

    def __init__(self):
        self.successful = 0
        self.failed = 0
        self.missed = 0

    def record(self, event):
        if event.job_id == self.job_id:
            return
        if event.code == EVENT_JOB_EXECUTED:
            self.successful += 1
        elif event.code == EVENT_JOB_ERROR:
            self.failed += 1
        elif event.code == EVENT_JOB_MISSED:
            self.missed += 1

    async def emit(self):
        logger.info(
            "Scheduler hourly summary: successful=%d, failed=%d, missed=%d",
            self.successful, self.failed, self.missed,
        )
        self.successful = self.failed = self.missed = 0


scheduler_log_summary = SchedulerLogSummary()


def get_scheduler_health() -> dict:
    """Returns scheduler operational status and execution metrics."""
    is_running = getattr(scheduler, "running", False)
    jobs_count = len(scheduler.get_jobs()) if is_running else 0
    return {
        "status": "running" if is_running else "stopped",
        "healthy": is_running,
        "jobs_count": jobs_count,
        "summary": {
            "successful": scheduler_log_summary.successful,
            "failed": scheduler_log_summary.failed,
            "missed": scheduler_log_summary.missed,
        }
    }


_last_watchdog_alert_time: float | None = None
_last_watchdog_status: str | None = None


async def check_worker_watchdog(bot: Bot):
    """Periodic health check of AI queue worker. Auto-heals if crashed/stopped, and throttles noisy alerts."""
    global _last_watchdog_alert_time, _last_watchdog_status
    from src.services.rate_limiter import get_worker_health, ensure_queue_worker_running
    worker_health = get_worker_health(stall_threshold_seconds=60.0)
    if not worker_health.get("healthy"):
        status = worker_health.get("status")
        # Attempt auto-recovery if crashed or stopped
        if status in ("crashed", "stopped"):
            restarted_task = ensure_queue_worker_running(bot=bot)
            if restarted_task:
                logger.info("Watchdog detected %s queue worker; auto-recovery task triggered", status)
                _last_watchdog_status = "recovering"
                return

        now = monotonic()
        # Throttle noisy warnings: only log if status changed or at least 15 minutes elapsed
        if (
            _last_watchdog_alert_time is None
            or status != _last_watchdog_status
            or (now - _last_watchdog_alert_time) > 900
        ):
            logger.warning("Queue worker watchdog alert: %s", worker_health)
            _last_watchdog_alert_time = now
            _last_watchdog_status = status
    else:
        if _last_watchdog_status is not None and _last_watchdog_status != "running":
            logger.info("Queue worker recovered and is healthy again")
        _last_watchdog_status = "running"
        _last_watchdog_alert_time = None


def configure_scheduler_logging():
    # Keep failures and missed-run warnings immediate; suppress routine INFO noise.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
    scheduler.remove_listener(scheduler_log_summary.record)
    scheduler.add_listener(
        scheduler_log_summary.record,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    scheduler.add_job(
        scheduler_log_summary.emit, "interval", hours=1,
        id=scheduler_log_summary.job_id, replace_existing=True,
        max_instances=1, coalesce=True,
    )

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
        except TelegramBadRequest as exc:
            if "parse entities" not in str(exc).lower():
                raise
            await bot.send_message(chat_id, part, parse_mode=None)


async def handle_user_blocked_bot(user_id: int, db: AsyncSession | None = None):
    """When Telegram reports a user blocked the bot, disable notifications, mark blocked_at, and unschedule all jobs."""
    logger.info("User %s blocked the bot; disabling notifications, recording blocked_at, and unscheduling jobs", user_id)
    if db is not None:
        await crud.mark_user_blocked(db, user_id)
    else:
        async with AsyncSessionLocal() as session:
            await crud.mark_user_blocked(session, user_id)
    remove_user_jobs(user_id)


async def send_report_notice(bot, user_id, text, **kwargs):
    """Notification failure must not mask an already queued report."""
    try:
        await bot.send_message(user_id, text, **kwargs)
    except TelegramForbiddenError:
        await handle_user_blocked_bot(user_id)
    except TelegramAPIError as exc:
        logger.info("Report notice could not be delivered to %s: %s", user_id, exc)


async def send_daily_reminder(bot: Bot, user_id: int):
    from src.services import ux
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or not user.notifications_enabled:
            return
        if not ux.coaching_allowed(user):
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
        if any(log.meal_type == 'dinner' for log in logs) or (not user.ux_preferences and len(logs) > 0):
            return

        try:
            msg = i18n_locales.get_text('ux_evening' if logs else 'ux_empty', user.language)
            from src.handlers.ux import today_keyboard
            await bot.send_message(user_id, msg, reply_markup=today_keyboard(user.language))
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
        except Exception as e:
            logger.error("Error sending daily reminder to %s: %s", user_id, e)

async def deliver_saved_report(bot, db, user, delivery):
    """Delivery retries reference an owned snapshot, never another provider call."""
    snapshot = await crud.get_saved_report(db, user.telegram_id, delivery['report_id'])
    if not snapshot:
        raise RuntimeError('Saved report is unavailable')
    from src.keyboards import reply
    await bot.send_message(user.telegram_id, delivery['summary'],
        reply_markup=reply.get_report_keyboard(user.language, delivery['report_id']))


async def generate_and_send_report_direct(bot: Bot, db: AsyncSession, user, report_type: str, report_at: datetime | None = None, queue_item=None, delivery_cache=None):
    """Compiles logs, generates an AI report, logs the request, and sends it to the user."""
    user_id = user.telegram_id
    if queue_item is not None and queue_item.payload.get('report_delivery'):
        await deliver_saved_report(bot, db, user, queue_item.payload['report_delivery'])
        return
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    now_local = (report_at or datetime.now(UTC)).astimezone(user_tz)
    
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
        
        # Fetch 30 days of weight logs specifically for the weight chart
        start_30d_local = now_local - timedelta(days=30)
        start_30d_date = start_30d_local.astimezone(UTC).replace(tzinfo=None)
        chart_weight_logs = await crud.get_weight_logs(db, user_id, start_30d_date, end_date)
        
    else:  # monthly
        start_of_month_local = now_local - timedelta(days=30)
        start_date = start_of_month_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)

    from src.services import ux
    from src.handlers.ux import today_keyboard
    if not food_logs:
        await bot.send_message(user_id, i18n_locales.get_text('ux_empty', user.language), reply_markup=today_keyboard(user.language))
        return
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
    
    from src.services.medications import report_context
    profile_dict["medications"] = await report_context(db, user_id, start_date, end_date)
    # Capture inputs before provider I/O: concurrent changes must not make an old
    # analysis appear current. Never recompute this fingerprint after generation.
    water_ml = await crud.water_total(db, user_id, start_date, end_date) if report_type == 'daily' else None
    fingerprint = await daily_report_fingerprint(
        db, user, now_local.date().isoformat(), food_logs, weight_logs,
        profile_dict['medications'], water_ml,
    ) if report_type == 'daily' else None
    from aiogram.utils.chat_action import ChatActionSender
    async with ChatActionSender.typing(bot=bot, chat_id=user_id):
        report = await gemini.generate_report(profile_dict, food_logs, weight_logs, report_type, user.language, user_id=user_id)
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
        
    report_id = await crud.save_report_snapshot(
        db, user_id, report,
        commit=queue_item is None,
        report_type=report_type,
        food_count=len(food_logs),
        last_food_id=getattr(food_logs[-1], "id", None) if food_logs else None,
        water_ml=water_ml,
        input_fingerprint=fingerprint,
        date_str=now_local.date().isoformat()
    )
    from src.keyboards import reply
    if report_type == 'daily':
        summary = (await ux.today_data(db, user, report_at or datetime.now(UTC), include_tip=True))['summary']
    else:
        logged_days = len({log.logged_at.replace(tzinfo=UTC).astimezone(user_tz).date() for log in food_logs})
        summary = header.strip('*\n') + '\n' + i18n_locales.get_text('ux_period', user.language,
            days=7 if report_type == 'weekly' else 30, count=logged_days,
            cal=round(sum(log.calories for log in food_logs) / max(1, logged_days)))
        ordered = sorted(weight_logs, key=lambda log: log.logged_at)
        summary += '\n' + (i18n_locales.get_text('ux_trend', user.language, change=f'{ordered[-1].weight - ordered[0].weight:+.1f}') if len(ordered) > 1 else i18n_locales.get_text('ux_no_trend', user.language))
        summary += '\n\n' + ux.next_action(user, dict(count=logged_days,
            cal=sum(log.calories for log in food_logs) / max(1, logged_days),
            protein=sum(log.proteins for log in food_logs) / max(1, logged_days)),
            days=7 if report_type == 'weekly' else 30)
    delivery = {'report_id': report_id, 'summary': summary}
    if delivery_cache is not None:
        delivery_cache['report_delivery'] = delivery
    if queue_item is not None:
        queue_item.payload = {**queue_item.payload, 'report_delivery': delivery}
        await db.commit()
    await deliver_saved_report(bot, db, user, delivery)
    if report_type == 'daily':
        for uid, (_, sent_at) in list(_daily_cache_deliveries.items()):
            if monotonic() - sent_at >= 30:
                _daily_cache_deliveries.pop(uid, None)
        _daily_cache_deliveries[user_id] = (fingerprint, monotonic())

    if report_type == "weekly":
        from src.keyboards import inline
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
        
        promo_text = (
            i18n_locales.get_text("ux_progress", user.language)
        )
        
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=i18n_locales.get_text("ux_progress", user.language),
                    web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=charts")
                )
            ]
        ])
        
        try:
            await bot.send_message(chat_id=user_id, text=promo_text, reply_markup=markup, parse_mode="Markdown")
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
        except Exception as e:
            logger.error("Failed to send weekly charts webapp promo to %s: %s", user_id, e)


_in_flight_daily_reports: set[int] = set()
_daily_cache_deliveries: dict[int, tuple[str, float]] = {}


async def daily_report_fingerprint(db, user, date_str, food_logs, weight_logs, medications, water_ml):
    """Version all report inputs; ORM objects themselves must not enter the hash."""
    data = {
        'version': 1, 'date': date_str,
        'profile': {key: getattr(user, key, None) for key in (
            'name', 'sex', 'age', 'height_cm', 'weight_kg', 'activity_level',
            'goal', 'target_calories', 'target_protein', 'target_fat',
            'target_carb', 'language', 'timezone', 'ux_preferences')},
        'food': [{key: getattr(row, key, None) for key in (
            'id', 'logged_at', 'items_json', 'raw_text', 'meal_type',
            'calories', 'proteins', 'fats', 'carbs')} for row in sorted(food_logs, key=lambda row: row.id)],
        'weight': [(row.id, row.logged_at, row.weight) for row in sorted(weight_logs, key=lambda row: row.id)],
        'water_ml': water_ml, 'medications': medications,
        'intakes': await crud.daily_report_intake_state(db, user.telegram_id, date_str),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


async def send_daily_report(bot: Bot, user_id: int, automated: bool = False):
    # Claim synchronously before the first await, including cache and queue paths.
    if user_id in _in_flight_daily_reports:
        return
    _in_flight_daily_reports.add(user_id)
    try:
        await _send_daily_report(bot, user_id, automated)
    finally:
        _in_flight_daily_reports.discard(user_id)


async def _send_daily_report(bot: Bot, user_id: int, automated: bool = False):

    report_at = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or (automated and not user.notifications_enabled):
            return

        from src.services.ux import zone
        user_tz = zone(user)
        now_local = report_at.astimezone(user_tz)
        start_of_day_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
        end_date = now_local.astimezone(UTC).replace(tzinfo=None)

        food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
        weight_logs = await crud.get_weight_logs(db, user_id,
            (start_of_day_local - timedelta(days=7)).astimezone(UTC).replace(tzinfo=None), end_date)
        from src.services.medications import report_context
        fingerprint = await daily_report_fingerprint(db, user, now_local.date().isoformat(),
            food_logs, weight_logs, await report_context(db, user_id, start_date, end_date),
            await crud.water_total(db, user_id, start_date, end_date))

        # Check if an up-to-date daily snapshot already exists and no new entries have been logged
        latest_snapshot = await crud.get_latest_report_snapshot(db, user_id, report_type="daily")
        if latest_snapshot and latest_snapshot.payload.get("date_str") == now_local.date().isoformat():
            if latest_snapshot.payload.get('input_fingerprint') == fingerprint:
                # Data hasn't changed: reuse existing snapshot without calling Gemini or rate limiter!
                from src.keyboards import reply
                from src.services import ux
                summary = (await ux.today_data(db, user, report_at, include_tip=True))['summary']
                now = monotonic()
                # Bound memory to recent deliveries, and allow changed inputs immediately.
                for uid, (_, sent_at) in list(_daily_cache_deliveries.items()):
                    if now - sent_at >= 30:
                        _daily_cache_deliveries.pop(uid, None)
                previous = _daily_cache_deliveries.get(user_id)
                if previous and previous[0] == fingerprint:
                    return
                try:
                    await bot.send_message(
                        user_id,
                        summary,
                        reply_markup=reply.get_report_keyboard(user.language, latest_snapshot.id)
                    )
                    _daily_cache_deliveries[user_id] = (fingerprint, now)
                except TelegramForbiddenError:
                    return
                except Exception as e:
                    logger.warning("Failed to send cached daily report to %s: %s", user_id, e)
                return

        # Check if an active report task is already pending/processing in queue
        active_queued = await db.execute(
            select(AiRequestQueue).where(
                AiRequestQueue.user_id == user_id,
                AiRequestQueue.request_type == "generate_report",
                AiRequestQueue.status.in_(["pending", "processing"])
            )
        )
        for queued in active_queued.scalars():
            queued_payload = queued.payload or {}
            # Legacy tasks have no fingerprint/day. Their original cutoff (or
            # creation time) still identifies the local day, not today's date.
            queued_day = queued_payload.get('date_str')
            if queued_day is None:
                try:
                    cutoff = (datetime.fromisoformat(queued_payload['report_at'])
                              if queued_payload.get('report_at') else queued.created_at)
                    if cutoff.tzinfo is None:
                        cutoff = cutoff.replace(tzinfo=UTC)
                    queued_day = cutoff.astimezone(user_tz).date().isoformat()
                except (ValueError, TypeError):
                    continue
            if (queued_payload.get('report_type', 'daily') == 'daily'
                    and queued_day == now_local.date().isoformat()
                    and queued_payload.get('input_fingerprint', fingerprint) == fingerprint):
                return

        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "daily", "report_at": report_at.isoformat(), "automated": automated,
                         "date_str": now_local.date().isoformat(), "input_fingerprint": fingerprint}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        delivery_cache = {}
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "daily", report_at=report_at, delivery_cache=delivery_cache)
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
            return
        except Exception as e:
            logger.error("Error sending daily report to %s: %s", user_id, e)
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "daily", "report_at": report_at.isoformat(), "automated": automated,
                         "date_str": now_local.date().isoformat(), "input_fingerprint": fingerprint, **delivery_cache}
            )
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

async def send_weekly_report(bot: Bot, user_id: int, automated: bool = False):
    report_at = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or (automated and not user.notifications_enabled):
            return
        
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "weekly", "report_at": report_at.isoformat(), "automated": automated}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        delivery_cache = {}
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "weekly", report_at=report_at, delivery_cache=delivery_cache)
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
            return
        except Exception as e:
            logger.error("Error sending weekly report to %s: %s", user_id, e)
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "weekly", "report_at": report_at.isoformat(), "automated": automated, **delivery_cache}
            )
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

async def send_monthly_report(bot: Bot, user_id: int, automated: bool = False):
    report_at = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or (automated and not user.notifications_enabled):
            return
        
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "monthly", "report_at": report_at.isoformat(), "automated": automated}
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("rate_limit_queued", user.language, position=position),
                parse_mode="Markdown"
            )
            return

        delivery_cache = {}
        try:
            await bot.send_message(user_id, i18n_locales.get_text("report_calculating", user.language), parse_mode="Markdown")
            await generate_and_send_report_direct(bot, db, user, "monthly", report_at=report_at, delivery_cache=delivery_cache)
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
            return
        except Exception as e:
            logger.error("Error sending monthly report to %s: %s", user_id, e)
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=user_id,
                chat_id=user_id,
                request_type="generate_report",
                payload={"report_type": "monthly", "report_at": report_at.isoformat(), "automated": automated, **delivery_cache}
            )
            await send_report_notice(
                bot, user_id,
                i18n_locales.get_text("ai_service_unavailable", user.language),
                parse_mode="Markdown"
            )

async def check_daily_streaks_and_targets(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or not user.notifications_enabled:
            return
            
        # Update daily target streaks
        from src.services import gamification
        cal_hit, prot_hit = await gamification.update_daily_targets_streak(db, user)
        
        # Check new achievements
        new_ach_keys = await gamification.check_new_achievements(db, user_id)
        
        # Notify if any achievements unlocked
        from src.services.ux import coaching_allowed
        if new_ach_keys and coaching_allowed(user):
            ach_notifs = []
            for ach_key in new_ach_keys:
                ach_def = gamification.ACHIEVEMENTS.get(ach_key)
                if ach_def:
                    icon = ach_def["icon"]
                    name = i18n_locales.get_text(ach_def["name_key"], user.language)
                    desc = i18n_locales.get_text(ach_def["desc_key"], user.language)
                    ach_notifs.append(f"{icon} *{name}* — {desc}")
            
            msg = f"🏆 *{i18n_locales.get_text('achievements_unlocked_title', user.language)}*\n" + "\n".join(ach_notifs)
            try:
                await bot.send_message(user_id, msg, parse_mode="Markdown")
            except TelegramForbiddenError:
                await handle_user_blocked_bot(user_id, db=db)
            except Exception as e:
                logger.error("Failed to send daily streak achievement unlock message to %s: %s", user_id, e)

async def send_morning_briefing_job(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or not user.notifications_enabled:
            return
        from src.services.ux import coaching_allowed
        if user.ux_preferences or not coaching_allowed(user):
            return
            
        from src.services import briefing
        msg = await briefing.generate_morning_briefing(db, user_id)
        
        # Render dynamic inline keyboard for morning briefing
        from src.keyboards import inline
        markup = inline.get_morning_actions_inline(user.language)
        
        try:
            await bot.send_message(user_id, msg, reply_markup=markup, parse_mode="Markdown")
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
        except Exception as e:
            logger.error("Failed to send morning briefing to %s: %s", user_id, e)

async def send_weekly_health_card_job(bot: Bot, user_id: int):
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or user.is_blocked or crud.is_user_bot_blocked(user) or not user.notifications_enabled:
            return
        from src.services.ux import coaching_allowed
        if not coaching_allowed(user, 'weekly'):
            return
            
        from src.services import gamification
        card = await gamification.generate_weekly_health_card(db, user_id)
        if user.ux_preferences:
            return  # The card stays in WebApp; configured coaching sends one weekly report.
        
        # Send a summary message and an inline keyboard to view full details
        score = card.card_data['overall_score']
        score_text = f"{score}/100" if score is not None else i18n_locales.get_text('ux_insufficient', user.language)
        msg = (
            f"🃏 *{i18n_locales.get_text('health_card_title', user.language)}*\n\n"
            f"📊 *{i18n_locales.get_text('overall_score', user.language)}*: {score_text}\n\n"
            f"💬 *{i18n_locales.get_text('ux_details', user.language)}*:\n{card.card_data['coach_message']}"
        )
        
        # Keyboard linking to the Mini App
        from src.keyboards import inline
        markup = inline.get_health_card_inline(user.language)
        
        try:
            await bot.send_message(user_id, msg, reply_markup=markup, parse_mode="Markdown")
        except TelegramForbiddenError:
            await handle_user_blocked_bot(user_id, db=db)
        except Exception as e:
            logger.error("Failed to send weekly health card to %s: %s", user_id, e)

async def reset_weekly_freezes_global():
    async with AsyncSessionLocal() as db:
        from src.services import gamification
        await gamification.reset_weekly_freezes(db)

async def scheduled_report(bot, user_id, report_type):
    from src.services.ux import coaching_allowed, zone
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user or not coaching_allowed(user, 'weekly' if report_type == 'weekly' else 'daily'):
            return
        if user.ux_preferences and (report_type == 'monthly' or (report_type == 'daily' and datetime.now(zone(user)).weekday() == user.weekly_report_day)):
            return
    await {'daily': send_daily_report, 'weekly': send_weekly_report, 'monthly': send_monthly_report}[report_type](bot, user_id, automated=True)


def reschedule_user_jobs(bot: Bot, user):
    if not user:
        return
    user_id = user.telegram_id
    
    # Remove existing jobs for this user
    for suffix in ["_reminder", "_daily", "_weekly", "_monthly", "_morning", "_daily_check", "_health_card"]:
        job_id = f"user_{user_id}{suffix}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            
    if user.is_blocked or crud.is_user_bot_blocked(user) or not user.notifications_enabled:
        return
        
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    if user.notifications_enabled:
        # 1. Daily Food Log Reminder
        r_time = time(19, 0) if user.ux_preferences else user.food_reminder_time or time(11, 0)
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
            scheduled_report,
            CronTrigger(hour=d_time.hour, minute=d_time.minute, timezone=user_tz),
            id=f"user_{user_id}_daily",
            args=[bot, user_id, 'daily'],
            replace_existing=True
        )
        
        # 3. Weekly Report
        w_day = user.weekly_report_day if user.weekly_report_day is not None else 6  # 6 = Sunday
        scheduler.add_job(
            scheduled_report,
            CronTrigger(day_of_week=w_day, hour=21, minute=0, timezone=user_tz),
            id=f"user_{user_id}_weekly",
            args=[bot, user_id, 'weekly'],
            replace_existing=True
        )
        
        # 4. Monthly Report
        m_day = user.monthly_report_day if user.monthly_report_day is not None else 1
        scheduler.add_job(
            scheduled_report,
            CronTrigger(day=m_day, hour=21, minute=0, timezone=user_tz),
            id=f"user_{user_id}_monthly",
            args=[bot, user_id, 'monthly'],
            replace_existing=True
        )
        
        # 5. Morning Briefing (Daily at 8:00 AM)
        scheduler.add_job(
            send_morning_briefing_job,
            CronTrigger(hour=8, minute=0, timezone=user_tz),
            id=f"user_{user_id}_morning",
            args=[bot, user_id],
            replace_existing=True
        )
        
        # 6. Daily Targets & Streak Check (Daily at 23:55)
        scheduler.add_job(
            check_daily_streaks_and_targets,
            CronTrigger(hour=23, minute=55, timezone=user_tz),
            id=f"user_{user_id}_daily_check",
            args=[bot, user_id],
            replace_existing=True
        )
        
        # 7. Weekly Health Card (Sunday at 10:00 AM)
        scheduler.add_job(
            send_weekly_health_card_job,
            CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=user_tz),
            id=f"user_{user_id}_health_card",
            args=[bot, user_id],
            replace_existing=True
        )

def remove_user_jobs(user_id: int):
    for suffix in ["_reminder", "_daily", "_weekly", "_monthly", "_morning", "_daily_check", "_health_card"]:
        job_id = f"user_{user_id}{suffix}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

async def purge_expired_blocked_users_job():
    """Periodic job: deletes users who blocked the bot > 3 days ago, clearing all their records."""
    try:
        async with AsyncSessionLocal() as db:
            purged_count = await crud.purge_blocked_users(db, grace_period_days=3)
            if purged_count > 0:
                logger.info("Purged %d user(s) who blocked the bot > 3 days ago", purged_count)
    except Exception as e:
        logger.error("Error in purge_expired_blocked_users_job: %s", e)


async def init_scheduler(bot: Bot):
    configure_scheduler_logging()
    from src.services.medications import send_medication_reminders
    scheduler.add_job(send_medication_reminders, "interval", seconds=30,
                      args=[bot], id="medication_reminders", replace_existing=True,
                      max_instances=1, coalesce=True)
    # Register global freeze reset job
    scheduler.add_job(
        reset_weekly_freezes_global,
        CronTrigger(day_of_week="sun", hour=23, minute=59, timezone="UTC"),
        id="global_reset_freezes",
        replace_existing=True
    )
    # Register worker liveness watchdog
    scheduler.add_job(
        check_worker_watchdog,
        "interval",
        seconds=60,
        args=[bot],
        id="worker_watchdog",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # Register hourly purge job for users who blocked the bot > 3 days ago
    scheduler.add_job(
        purge_expired_blocked_users_job,
        "interval",
        hours=1,
        id="purge_expired_blocked_users",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    async with AsyncSessionLocal() as db:
        users = await crud.get_all_users(db, include_blocked=False)
        for user in users:
            reschedule_user_jobs(bot, user)
            
    scheduler.start()

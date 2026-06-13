import io
import asyncio
import logging
from datetime import datetime, UTC, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext

from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.database.models import AiRequestLog, AiRequestQueue
from src.services import gemini
from src.utils import i18n_locales

logger = logging.getLogger(__name__)

_worker_running = False
_worker_task = None

def clean_md(text: str) -> str:
    if not text:
        return ""
    for char in ["*", "_", "[", "]", "`"]:
        text = text.replace(char, "")
    return text

async def check_rate_limit(db: AsyncSession) -> Tuple[bool, str]:
    """
    Checks if global rate limits are exceeded.
    Returns (is_limited, limit_type) where limit_type is 'minute' or 'day'.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    one_minute_ago = now - timedelta(minutes=1)
    one_day_ago = now - timedelta(days=1)

    # Clean up old logs to keep database small
    try:
        await db.execute(
            delete(AiRequestLog).where(AiRequestLog.executed_at < one_day_ago)
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to clean up old AI request logs: {e}")

    # Count in last 60 seconds
    min_count_res = await db.execute(
        select(func.count(AiRequestLog.id)).where(AiRequestLog.executed_at >= one_minute_ago)
    )
    min_count = min_count_res.scalar() or 0
    if min_count >= 15:
        return True, "minute"

    # Count in last 24 hours
    day_count_res = await db.execute(
        select(func.count(AiRequestLog.id)).where(AiRequestLog.executed_at >= one_day_ago)
    )
    day_count = day_count_res.scalar() or 0
    if day_count >= 1500:
        return True, "day"

    return False, ""

async def log_ai_request(db: AsyncSession, user_id: Optional[int], request_type: str) -> AiRequestLog:
    """Logs a successful AI request."""
    log_entry = AiRequestLog(
        user_id=user_id,
        request_type=request_type,
        executed_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(log_entry)
    await db.commit()
    await db.refresh(log_entry)
    return log_entry

async def add_to_queue(db: AsyncSession, user_id: int, chat_id: int, request_type: str, payload: dict) -> int:
    """Adds a pending request to the queue and returns its ID."""
    queue_item = AiRequestQueue(
        user_id=user_id,
        chat_id=chat_id,
        request_type=request_type,
        payload=payload,
        status="pending",
        created_at=datetime.now(UTC).replace(tzinfo=None)
    )
    db.add(queue_item)
    await db.commit()
    await db.refresh(queue_item)
    return queue_item.id

async def get_queue_position(db: AsyncSession, queue_item_id: int) -> int:
    """Returns the 1-based position of a pending item in the queue."""
    # Position is determined by how many pending items exist that have id <= queue_item_id
    stmt = select(func.count(AiRequestQueue.id)).where(
        AiRequestQueue.status == "pending",
        AiRequestQueue.id <= queue_item_id
    )
    res = await db.execute(stmt)
    return (res.scalar() or 0)

async def get_next_pending_queue_item(db: AsyncSession) -> Optional[AiRequestQueue]:
    """Fetches the oldest pending item in the queue that is ready for processing/retry."""
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = select(AiRequestQueue).where(
        AiRequestQueue.status == "pending",
        (AiRequestQueue.next_retry_at == None) | (AiRequestQueue.next_retry_at <= now)
    ).order_by(AiRequestQueue.id.asc()).limit(1)
    res = await db.execute(stmt)
    return res.scalars().first()

async def execute_queued_item(bot: Bot, storage, db: AsyncSession, item: AiRequestQueue) -> bool:
    """Executes a queued item and handles state transitions / notifications."""
    user_id = item.user_id
    chat_id = item.chat_id
    req_type = item.request_type
    payload = item.payload

    user = await crud.get_user(db, user_id)
    if not user:
        logger.warning(f"Queue item {item.id} has no user in database.")
        return False

    user_language = user.language or "en"
    key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id)
    fsm_context = FSMContext(storage=storage, key=key)

    if req_type == "analyze_food_input":
        from src.handlers.food import FoodLoggingState
        current_state = await fsm_context.get_state()

        text_desc = payload.get("text_description")
        image_file_id = payload.get("image_file_id")
        meal_type = payload.get("meal_type", "food")

        image_bytes = None
        if image_file_id:
            try:
                file_info = await bot.get_file(image_file_id)
                image_io = io.BytesIO()
                await bot.download_file(file_info.file_path, image_io)
                image_bytes = image_io.getvalue()
            except Exception as e:
                logger.error(f"Failed to download image for queued analysis: {e}")
                return False

        status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
        try:
            analysis = await gemini.analyze_food_input(
                text_description=text_desc,
                image_bytes=image_bytes,
                language=user_language
            )
        finally:
            try:
                await bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass

        if not analysis:
            return False

        await log_ai_request(db, user_id=user_id, request_type=req_type)

        items_str = ""
        for food_item in analysis.food_items:
            name = clean_md(food_item.name)
            portion = clean_md(food_item.portion)
            items_str += f"- **{name}** ({portion}): {food_item.calories} kcal | P: {food_item.protein}g, F: {food_item.fat}g, C: {food_item.carb}g\n"

        result_text = i18n_locales.get_text(
            "food_analysis_result",
            user_language,
            items=items_str,
            calories=analysis.total_calories,
            protein=analysis.total_protein,
            fat=analysis.total_fat,
            carb=analysis.total_carb
        )

        if current_state == FoodLoggingState.waiting_for_input:
            analysis_dict = analysis.model_dump()
            await fsm_context.update_data(
                analysis=analysis_dict,
                image_file_id=image_file_id,
                raw_text=text_desc,
                meal_type=meal_type
            )
            await fsm_context.set_state(FoodLoggingState.waiting_for_confirm)
            from src.keyboards import reply
            await bot.send_message(
                chat_id,
                result_text,
                reply_markup=reply.get_food_confirm_keyboard(user_language),
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id,
                f"ℹ️ *Queued Food Analysis Ready* (you are no longer in the food logging flow):\n\n{result_text}",
                parse_mode="Markdown"
            )
        return True

    elif req_type == "adjust_food_analysis":
        from src.handlers.food import FoodLoggingState
        current_state = await fsm_context.get_state()

        original_data = payload.get("original_data")
        correction_text = payload.get("correction_text")

        status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
        try:
            adjusted_analysis = await gemini.adjust_food_analysis(
                original_data=original_data,
                correction_text=correction_text,
                language=user_language
            )
        finally:
            try:
                await bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass

        if not adjusted_analysis:
            return False

        await log_ai_request(db, user_id=user_id, request_type=req_type)

        items_str = ""
        for food_item in adjusted_analysis.food_items:
            name = clean_md(food_item.name)
            portion = clean_md(food_item.portion)
            items_str += f"- **{name}** ({portion}): {food_item.calories} kcal | P: {food_item.protein}g, F: {food_item.fat}g, C: {food_item.carb}g\n"

        result_text = i18n_locales.get_text(
            "food_analysis_result",
            user_language,
            items=items_str,
            calories=adjusted_analysis.total_calories,
            protein=adjusted_analysis.total_protein,
            fat=adjusted_analysis.total_fat,
            carb=adjusted_analysis.total_carb
        )

        if current_state == FoodLoggingState.waiting_for_correction:
            analysis_dict = adjusted_analysis.model_dump()
            await fsm_context.update_data(analysis=analysis_dict)
            await fsm_context.set_state(FoodLoggingState.waiting_for_confirm)
            from src.keyboards import reply
            await bot.send_message(
                chat_id,
                result_text,
                reply_markup=reply.get_food_confirm_keyboard(user_language),
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id,
                f"ℹ️ *Queued Food Analysis Adjustment Ready* (you are no longer in the correction flow):\n\n{result_text}",
                parse_mode="Markdown"
            )
        return True

    elif req_type == "adjust_meal_edit":
        from src.handlers.food import MealEditingState
        current_state = await fsm_context.get_state()

        original_data = payload.get("original_data")
        correction_text = payload.get("correction_text")
        edit_meal_id = payload.get("edit_meal_id")
        edit_date_str = payload.get("edit_date_str")

        status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
        try:
            adjusted_analysis = await gemini.adjust_food_analysis(
                original_data=original_data,
                correction_text=correction_text,
                language=user_language
            )
        finally:
            try:
                await bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass

        if not adjusted_analysis:
            return False

        await log_ai_request(db, user_id=user_id, request_type=req_type)

        items_str = ""
        for food_item in adjusted_analysis.food_items:
            name = clean_md(food_item.name)
            portion = clean_md(food_item.portion)
            items_str += f"- **{name}** ({portion}): {food_item.calories} kcal | P: {food_item.protein}g, F: {food_item.fat}g, C: {food_item.carb}g\n"

        result_text = i18n_locales.get_text(
            "food_analysis_result",
            user_language,
            items=items_str,
            calories=adjusted_analysis.total_calories,
            protein=adjusted_analysis.total_protein,
            fat=adjusted_analysis.total_fat,
            carb=adjusted_analysis.total_carb
        )

        if current_state == MealEditingState.waiting_for_edit_text:
            adjusted_dict = adjusted_analysis.model_dump()
            await fsm_context.update_data(
                adjusted_analysis=adjusted_dict,
                edit_meal_id=edit_meal_id,
                edit_date_str=edit_date_str
            )
            await fsm_context.set_state(MealEditingState.waiting_for_edit_confirm)
            from src.keyboards import reply
            await bot.send_message(
                chat_id,
                result_text,
                reply_markup=reply.get_meal_edit_confirm_keyboard(user_language),
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(
                chat_id,
                f"ℹ️ *Queued Meal Edit Ready* (you are no longer in the editing flow):\n\n{result_text}",
                parse_mode="Markdown"
            )
        return True

    elif req_type == "generate_report":
        report_type = payload.get("report_type", "daily")
        from src.services.scheduler import generate_and_send_report_direct
        # Generate the report direct helper will query user, generate via Gemini and log request
        await generate_and_send_report_direct(bot, db, user, report_type)
        return True

    return False

async def process_next_queue_item(bot: Bot, storage):
    """Checks rate limits and processes the next pending item in the queue."""
    async with AsyncSessionLocal() as db:
        is_limited, _ = await check_rate_limit(db)
        if is_limited:
            return

        item = await get_next_pending_queue_item(db)
        if not item:
            return

        item.status = "processing"
        await db.commit()

        try:
            success = await execute_queued_item(bot, storage, db, item)
            if success:
                item.status = "completed"
                item.processed_at = datetime.now(UTC).replace(tzinfo=None)
                item.error_message = None
                item.last_error = None
            else:
                item.status = "failed"
                item.error_message = "Execution returned failure"
        except Exception as e:
            logger.error(f"Error executing queued item {item.id}: {e}", exc_info=True)
            item.retry_count += 1
            delay = min(240, 5 * (2 ** (item.retry_count - 1)))
            item.next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=delay)
            item.status = "pending"
            item.last_error = str(e)
            item.error_message = f"Failed at attempt {item.retry_count}, retrying in {delay}s"

        await db.commit()

async def start_queue_worker(bot: Bot, storage):
    """Background loop that processes the AI request queue."""
    global _worker_running, _worker_task
    _worker_running = True
    logger.info("AI Request Queue worker starting...")
    while _worker_running:
        try:
            await process_next_queue_item(bot, storage)
        except Exception as e:
            logger.error(f"Error in queue worker iteration: {e}", exc_info=True)
        await asyncio.sleep(2.0)

async def stop_queue_worker():
    """Stops the background queue worker."""
    global _worker_running
    _worker_running = False
    logger.info("AI Request Queue worker stopping...")

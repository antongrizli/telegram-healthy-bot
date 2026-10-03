import io
import asyncio
import logging
from datetime import datetime, UTC, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.context import FSMContext

from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.database.models import AiRequestLog, AiRequestQueue
from src.services import gemini
from src.utils import i18n_locales
from src.config import settings
from src.services.ai_quota import AIQuotaExceeded
from src.utils.telegram_edit import try_edit

logger = logging.getLogger(__name__)


async def remove_status_message(bot, chat_id, message_id):
    from aiogram.exceptions import TelegramAPIError
    if isinstance(message_id, int):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramAPIError:
            logger.info('Could not remove queued edit status message')

_worker_running = False
_worker_task = None
_worker_stop_event = None
_worker_started_at = None
_worker_stopped_at = None
_worker_last_heartbeat = None
_worker_last_error = None
_bot = None
_storage = None


def record_worker_heartbeat():
    """Update worker heartbeat timestamp."""
    global _worker_last_heartbeat
    _worker_last_heartbeat = datetime.now(UTC)


def get_worker_health(stall_threshold_seconds: float = 60.0) -> dict:
    """
    Returns worker health status dictionary:
    - status: 'running', 'stalled', 'crashed', or 'stopped'
    - healthy: bool
    - running: bool
    - error: Optional[str]
    - started_at: Optional[str]
    - stopped_at: Optional[str]
    - last_heartbeat_at: Optional[str]
    - heartbeat_age_seconds: Optional[float]
    """
    global _worker_running, _worker_task, _worker_started_at, _worker_stopped_at, _worker_last_heartbeat, _worker_last_error
    now = datetime.now(UTC)

    if _worker_last_error:
        heartbeat_age = round((now - _worker_last_heartbeat).total_seconds(), 2) if _worker_last_heartbeat else None
        return {
            "status": "crashed",
            "healthy": False,
            "running": False,
            "error": _worker_last_error,
            "started_at": _worker_started_at.isoformat() if _worker_started_at else None,
            "stopped_at": _worker_stopped_at.isoformat() if _worker_stopped_at else None,
            "last_heartbeat_at": _worker_last_heartbeat.isoformat() if _worker_last_heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age,
        }

    if _worker_task is not None and _worker_task.done():
        exc = None
        try:
            exc = _worker_task.exception()
        except asyncio.CancelledError:
            pass
        heartbeat_age = round((now - _worker_last_heartbeat).total_seconds(), 2) if _worker_last_heartbeat else None
        return {
            "status": "crashed" if exc else "stopped",
            "healthy": False,
            "running": False,
            "error": str(exc) if exc else None,
            "started_at": _worker_started_at.isoformat() if _worker_started_at else None,
            "stopped_at": _worker_stopped_at.isoformat() if _worker_stopped_at else None,
            "last_heartbeat_at": _worker_last_heartbeat.isoformat() if _worker_last_heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age,
        }

    if not _worker_running:
        heartbeat_age = round((now - _worker_last_heartbeat).total_seconds(), 2) if _worker_last_heartbeat else None
        return {
            "status": "stopped",
            "healthy": False,
            "running": False,
            "error": None,
            "started_at": _worker_started_at.isoformat() if _worker_started_at else None,
            "stopped_at": _worker_stopped_at.isoformat() if _worker_stopped_at else None,
            "last_heartbeat_at": _worker_last_heartbeat.isoformat() if _worker_last_heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age,
        }

    heartbeat_age = round((now - _worker_last_heartbeat).total_seconds(), 2) if _worker_last_heartbeat else None
    is_stalled = heartbeat_age is not None and heartbeat_age > stall_threshold_seconds

    return {
        "status": "stalled" if is_stalled else "running",
        "healthy": not is_stalled,
        "running": True,
        "error": None,
        "started_at": _worker_started_at.isoformat() if _worker_started_at else None,
        "stopped_at": _worker_stopped_at.isoformat() if _worker_stopped_at else None,
        "last_heartbeat_at": _worker_last_heartbeat.isoformat() if _worker_last_heartbeat else None,
        "heartbeat_age_seconds": heartbeat_age,
    }


def is_worker_alive(stall_threshold_seconds: float = 60.0) -> bool:
    return get_worker_health(stall_threshold_seconds=stall_threshold_seconds).get("healthy", False)


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
    exhausted = await crud.ai_quota_exhausted(db)
    return (True, exhausted[0]) if exhausted else (False, "")

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
        AiRequestQueue.request_type.in_(crud.EXECUTABLE_QUEUE_TYPES),
        AiRequestQueue.id <= queue_item_id
    )
    res = await db.execute(stmt)
    return (res.scalar() or 0)

async def update_queue_payload(db: AsyncSession, queue_item_id: int, payload: dict):
    """Updates the payload of a queue item."""
    await db.execute(
        update(AiRequestQueue).where(AiRequestQueue.id == queue_item_id).values(payload=payload)
    )
    await db.commit()

async def get_next_pending_queue_item(db: AsyncSession) -> Optional[AiRequestQueue]:
    """Fetches the oldest pending item in the queue that is ready for processing/retry."""
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = select(AiRequestQueue).where(
        AiRequestQueue.status == "pending",
        (AiRequestQueue.next_retry_at == None) | (AiRequestQueue.next_retry_at <= now)
    ).order_by(AiRequestQueue.id.asc()).limit(1)
    res = await db.execute(stmt)
    return res.scalars().first()

async def get_cached_adjustment(db, item, language):
    """Persist provider output before any Telegram delivery or FSM transition."""
    if item.payload.get('cached_adjustment'):
        return gemini.FoodAnalysisResponse(**item.payload['cached_adjustment'])
    result = await gemini.adjust_food_analysis(
        original_data=item.payload.get('original_data'),
        correction_text=item.payload.get('correction_text'),
        language=language, user_id=item.user_id,
    )
    if result:
        item.payload = {**item.payload, 'cached_adjustment': result.model_dump()}
        await db.commit()
        await log_ai_request(db, user_id=item.user_id, request_type=item.request_type)
    return result


async def execute_queued_item(bot: Bot, storage, db: AsyncSession, item: AiRequestQueue) -> bool:
    """Executes a queued item and handles state transitions / notifications."""
    user_id = item.user_id
    chat_id = item.chat_id
    req_type = item.request_type
    payload = item.payload

    user = await crud.get_user(db, user_id)
    if not user or user.is_blocked:
        logger.warning(f"Queue item {item.id} has no user in database.")
        return False

    user_language = user.language or "en"
    key = StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id)
    fsm_context = FSMContext(storage=storage, key=key)

    # A retry after delivery failure reuses the persisted result and confirmation.
    if payload.get("result_draft_id"):
        draft = await crud.get_meal_draft(db, payload["result_draft_id"], user_id)
        if draft:
            from src.services.meal_cards import deliver_card
            await deliver_card(bot, db, draft, chat_id, user_language, payload.get('status_message_id'))
        return True

    if req_type == "medication_photo":
        import base64
        if "result" not in payload:
            result = await gemini.recognize_medication(base64.b64decode(payload["image"]), payload["mime_type"], user_id=user_id)
            await log_ai_request(db, user_id=user_id, request_type=req_type)
            item.payload = {"result": result, **({"bot_category": payload["bot_category"]} if payload.get("bot_category") else {})}
            await db.commit()
        if item.payload.get("bot_category"):
            from src.handlers.medications import send_photo_result
            await send_photo_result(bot, item, user_language)
        return True

    if req_type == "analyze_food_input":
        from src.handlers.food import FoodLoggingState

        text_desc = payload.get("text_description")
        image_file_id = payload.get("image_file_id")
        image_file_ids = payload.get("image_file_ids")
        meal_type = payload.get("meal_type", "food")

        image_bytes = None
        images_bytes = None
        if image_file_ids:
            images_bytes = []
            for file_id in image_file_ids:
                try:
                    file_info = await bot.get_file(file_id)
                    image_io = io.BytesIO()
                    await bot.download_file(file_info.file_path, image_io)
                    images_bytes.append(image_io.getvalue())
                except Exception as e:
                    logger.error(f"Failed to download image {file_id} for queued analysis: {e}")
                    return False
        elif image_file_id:
            try:
                file_info = await bot.get_file(image_file_id)
                image_io = io.BytesIO()
                await bot.download_file(file_info.file_path, image_io)
                image_bytes = image_io.getvalue()
            except Exception as e:
                logger.error(f"Failed to download image for queued analysis: {e}")
                return False

        status_msg_id = payload.get("status_message_id")
        if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=i18n_locales.get_text("food_analyzing", user_language)
                )
            except Exception:
                pass
        elif status_msg_id is None:
            try:
                status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
                mid = getattr(status_msg, "message_id", None)
                if isinstance(mid, int):
                    status_msg_id = mid
                    payload = {**payload, "status_message_id": status_msg_id}
                    item.payload = payload
                    await db.commit()
            except Exception:
                status_msg_id = None

        analysis = await gemini.analyze_food_input(
            text_description=text_desc,
            image_bytes=image_bytes,
            images_bytes=images_bytes,
            language=user_language,
            user_id=user_id,
        )

        if not analysis:
            if status_msg_id and hasattr(bot, "edit_message_text"):
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=i18n_locales.get_text("err_analysis_failed", user_language)
                    )
                except Exception:
                    pass
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

        result_text += '\n' + i18n_locales.get_text('meal_type_' + meal_type, user_language)
        result_text += '\n' + i18n_locales.get_text('ux_estimate', user_language)
        draft_payload = {
            "analysis": analysis.model_dump(), "raw_text": text_desc,
            "image_file_id": image_file_id, "meal_type": meal_type,
            "logged_at": payload.get("logged_at") or item.created_at.replace(tzinfo=UTC).isoformat()
        }
        draft_id = await crud.save_meal_draft(db, user_id, draft_payload, commit=False)
        item.payload = {**payload, "result_draft_id": draft_id, "result_text": result_text}
        await db.commit()
        # Do not replace another meal's state if the user moved on during analysis.
        current_data = await fsm_context.get_data()
        if (await fsm_context.get_state() == FoodLoggingState.waiting_for_input
                and current_data.get("logged_at") == payload.get("logged_at")):
            await fsm_context.update_data(**draft_payload, draft_id=draft_id)
            await fsm_context.set_state(FoodLoggingState.waiting_for_confirm)
        from src.services.meal_cards import deliver_card
        draft = await crud.get_meal_draft(db, draft_id, user_id)
        if draft:
            await deliver_card(bot, db, draft, chat_id, user_language, status_msg_id)
        return True

    elif req_type == "adjust_food_analysis":
        from src.handlers.food import FoodLoggingState
        current_state = await fsm_context.get_state()

        original_data = payload.get("original_data")
        correction_text = payload.get("correction_text")

        status_msg_id = payload.get("status_message_id")
        if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=i18n_locales.get_text("food_analyzing", user_language)
                )
            except Exception:
                pass
        elif status_msg_id is None:
            try:
                status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
                mid = getattr(status_msg, "message_id", None)
                if isinstance(mid, int):
                    status_msg_id = mid
                    payload = {**payload, "status_message_id": status_msg_id}
                    item.payload = payload
                    await db.commit()
            except Exception:
                status_msg_id = None

        adjusted_analysis = await get_cached_adjustment(db, item, user_language)

        if not adjusted_analysis:
            if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=i18n_locales.get_text("err_correction_failed", user_language)
                    )
                except Exception:
                    pass
            return False

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

        draft_id = payload.get("draft_id")
        if draft_id:
            draft = await crud.get_meal_draft(db, draft_id, user_id)
            if not draft:
                return True
            await crud.save_meal_draft(db, user_id,
                {**draft.payload, "analysis": adjusted_analysis.model_dump()}, draft_id=draft_id)
            item.payload = {**payload, "result_draft_id": draft_id, "result_text": result_text}
            await db.commit()
            from src.services.meal_cards import deliver_card
            fresh = await crud.get_meal_draft(db, draft_id, user_id)
            if fresh:
                await deliver_card(bot, db, fresh, chat_id, user_language, status_msg_id)
            return True

        current_state = await fsm_context.get_state()
        flow_data = await fsm_context.get_data()
        if ((current_state == FoodLoggingState.waiting_for_correction
                and not flow_data.get('draft_id')
                and flow_data.get('analysis', original_data) == original_data)
                or (current_state == FoodLoggingState.waiting_for_confirm
                    and flow_data.get('queue_correction_id') == item.id)):
            analysis_dict = adjusted_analysis.model_dump()
            await fsm_context.update_data(analysis=analysis_dict, queue_correction_id=item.id)
            await fsm_context.set_state(FoodLoggingState.waiting_for_confirm)
            from src.keyboards import reply
            reply_markup = reply.get_food_confirm_keyboard(user_language)
            await bot.send_message(
                    chat_id,
                    result_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            await remove_status_message(bot, chat_id, status_msg_id)
        else:
            text_out = f"ℹ️ *Queued Food Analysis Adjustment Ready* (you are no longer in the correction flow):\n\n{result_text}"
            delivered = False
            if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
                delivered = await try_edit(bot.edit_message_text,
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=text_out,
                        parse_mode="Markdown"
                    )
            if not delivered:
                await bot.send_message(
                    chat_id,
                    text_out,
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

        status_msg_id = payload.get("status_message_id")
        if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=i18n_locales.get_text("food_analyzing", user_language)
                )
            except Exception:
                pass
        elif status_msg_id is None:
            try:
                status_msg = await bot.send_message(chat_id, i18n_locales.get_text("food_analyzing", user_language))
                mid = getattr(status_msg, "message_id", None)
                if isinstance(mid, int):
                    status_msg_id = mid
                    payload = {**payload, "status_message_id": status_msg_id}
                    item.payload = payload
                    await db.commit()
            except Exception:
                status_msg_id = None

        adjusted_analysis = await get_cached_adjustment(db, item, user_language)

        if not adjusted_analysis:
            if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=i18n_locales.get_text("err_correction_failed", user_language)
                    )
                except Exception:
                    pass
            return False

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

        current_state = await fsm_context.get_state()
        flow_data = await fsm_context.get_data()
        if ((current_state == MealEditingState.waiting_for_edit_text
                and flow_data.get('edit_meal_id', edit_meal_id) == edit_meal_id)
                or (current_state == MealEditingState.waiting_for_edit_confirm
                    and flow_data.get('queue_correction_id') == item.id)):
            adjusted_dict = adjusted_analysis.model_dump()
            await fsm_context.update_data(
                adjusted_analysis=adjusted_dict,
                edit_meal_id=edit_meal_id,
                edit_date_str=edit_date_str,
                queue_correction_id=item.id,
            )
            await fsm_context.set_state(MealEditingState.waiting_for_edit_confirm)
            from src.keyboards import reply
            reply_markup = reply.get_meal_edit_confirm_keyboard(user_language)
            await bot.send_message(
                    chat_id,
                    result_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            await remove_status_message(bot, chat_id, status_msg_id)
        else:
            text_out = f"ℹ️ *Queued Meal Edit Ready* (you are no longer in the editing flow):\n\n{result_text}"
            delivered = False
            if isinstance(status_msg_id, int) and hasattr(bot, "edit_message_text"):
                delivered = await try_edit(bot.edit_message_text,
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text=text_out,
                        parse_mode="Markdown"
                    )
            if not delivered:
                await bot.send_message(
                    chat_id,
                    text_out,
                    parse_mode="Markdown"
                )
        return True

    elif req_type == "generate_report":
        report_type = payload.get("report_type", "daily")
        if payload.get('automated'):
            from src.services.ux import coaching_allowed
            if not coaching_allowed(user, 'weekly' if report_type == 'weekly' else 'daily'):
                return True
        from src.services.scheduler import generate_and_send_report_direct
        # Generate the report direct helper will query user, generate via Gemini and log request
        report_at = datetime.fromisoformat(payload["report_at"]) if payload.get("report_at") else item.created_at.replace(tzinfo=UTC)
        await generate_and_send_report_direct(bot, db, user, report_type, report_at=report_at, queue_item=item)
        return True

    return False

async def _notify_item_failed(bot: Bot, db: AsyncSession, item: AiRequestQueue):
    """Notifies user of item failure by updating the status message in-place."""
    status_msg_id = item.payload.get("status_message_id") if item.payload else None
    if not isinstance(status_msg_id, int) or not hasattr(bot, "edit_message_text"):
        return
    user = await crud.get_user(db, item.user_id)
    lang = user.language if user and user.language else "en"
    err_text = i18n_locales.get_text("err_analysis_failed", lang)
    try:
        await bot.edit_message_text(
            chat_id=item.chat_id,
            message_id=status_msg_id,
            text=err_text
        )
    except Exception:
        pass

async def process_next_queue_item(bot: Bot, storage):
    """Checks rate limits and processes the next pending item in the queue."""
    async with AsyncSessionLocal() as db:
        item = await get_next_pending_queue_item(db)
        if not item:
            return
        cached = (item.payload.get('result_draft_id') or item.payload.get('cached_adjustment')
                  or item.payload.get('report_delivery')
                  or (item.request_type == 'medication_photo' and 'result' in item.payload))
        if not cached:
            is_limited, _ = await check_rate_limit(db)
            if is_limited:
                return

        if not await crud.claim_queue_task(db, item.id):
            return
        await db.refresh(item)

        logger.info("Processing queue task #%s (%s) for user %s (attempt %s)", item.id, item.request_type, item.user_id, item.retry_count + 1)
        try:
            success = await execute_queued_item(bot, storage, db, item)
            if success:
                item.status = "completed"
                item.processed_at = datetime.now(UTC).replace(tzinfo=None)
                item.error_message = None
                item.last_error = None
                logger.info("Queue task #%s (%s) for user %s completed successfully", item.id, item.request_type, item.user_id)
            else:
                item.status = "failed"
                item.processed_at = datetime.now(UTC).replace(tzinfo=None)
                item.error_message = "Execution returned failure"
                logger.warning("Queue task #%s (%s) for user %s returned failure", item.id, item.request_type, item.user_id)
                await _notify_item_failed(bot, db, item)
        except AIQuotaExceeded as exc:
            item.status = "pending"
            item.next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=exc.retry_after)
            item.error_message = "Waiting for AI quota"
        except TelegramForbiddenError:
            item.status = "failed"
            item.processed_at = datetime.now(UTC).replace(tzinfo=None)
            item.error_message = "Telegram delivery forbidden"
            item.last_error = "Telegram delivery forbidden"
            item.next_retry_at = None
            logger.info("Queue item %s cannot be delivered; not retrying", item.id)
        except Exception as e:
            logger.error(f"Error executing queued item {item.id}: {e}", exc_info=True)
            item.retry_count += 1
            item.last_error = str(e)
            if item.retry_count >= settings.AI_QUEUE_MAX_RETRIES:
                item.status = "failed"
                item.next_retry_at = None
                item.processed_at = datetime.now(UTC).replace(tzinfo=None)
                item.error_message = f"Retry limit reached after {item.retry_count} failures"
                await _notify_item_failed(bot, db, item)
            else:
                delay = min(240, 5 * (2 ** (item.retry_count - 1)))
                item.next_retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=delay)
                item.status = "pending"
                item.error_message = f"Failed at attempt {item.retry_count}, retrying in {delay}s"

        await db.commit()

async def start_queue_worker(bot: Bot, storage):
    """Background loop that processes the AI request queue."""
    global _worker_running, _worker_task, _worker_stop_event
    global _worker_started_at, _worker_stopped_at, _worker_last_heartbeat, _worker_last_error
    global _bot, _storage
    _bot = bot
    _storage = storage
    _worker_task = asyncio.current_task()
    _worker_stop_event = asyncio.Event()
    _worker_running = True
    _worker_started_at = datetime.now(UTC)
    _worker_last_heartbeat = datetime.now(UTC)
    _worker_last_error = None
    _worker_stopped_at = None
    logger.info("AI Request Queue worker starting...")
    async def pulse():
        while _worker_running:
            record_worker_heartbeat()
            await asyncio.sleep(10)

    heartbeat_task = asyncio.create_task(pulse())
    try:
        # Single-worker deployment: resume interrupted claims on startup.
        async with AsyncSessionLocal() as db:
            await db.execute(update(AiRequestQueue).where(
                AiRequestQueue.status == "processing",
                AiRequestQueue.request_type.in_(crud.EXECUTABLE_QUEUE_TYPES),
            ).values(status="pending"))
            await db.commit()
        while _worker_running:
            record_worker_heartbeat()
            try:
                # Pulse is independent of provider retries. A truly stuck operation
                # still has a bounded deadline and leaves a recoverable claim.
                await asyncio.wait_for(process_next_queue_item(bot, storage), timeout=900)
            except asyncio.TimeoutError:
                raise RuntimeError('Queue operation exceeded its execution deadline') from None
            except Exception as e:
                logger.error(f"Error in queue worker iteration: {e}", exc_info=True)
            record_worker_heartbeat()
            try:
                await asyncio.wait_for(_worker_stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
    except Exception as exc:
        _worker_last_error = str(exc)
        logger.critical(f"Fatal error in AI Request Queue worker: {exc}", exc_info=True)
        raise
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        _worker_running = False
        _worker_stopped_at = datetime.now(UTC)

async def stop_queue_worker(task=None, timeout=35):
    """Finish in-flight work while Telegram is open, or leave a recoverable claim."""
    global _worker_running, _worker_stopped_at
    _worker_running = False
    _worker_stopped_at = datetime.now(UTC)
    task = task or _worker_task
    if _worker_stop_event is not None:
        _worker_stop_event.set()
    logger.info("AI Request Queue worker stopping...")
    if task is not None and task is not asyncio.current_task():
        if task is not _worker_task and not task.done():
            task.cancel()  # Stop a task that has not entered its startup yet.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("Queue shutdown timed out; interrupted work will resume on restart")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise

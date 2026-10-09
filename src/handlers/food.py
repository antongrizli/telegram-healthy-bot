import io
import re
import logging
from typing import List, Optional
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)
from aiogram.filters import StateFilter, Command
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.keyboards import reply
from src.services import gemini, rate_limiter
from src.config import settings
from src.services import gamification
from src.services.ux import is_food_entry
from src.utils.telegram_edit import try_edit
from src.services.meal_cards import finalize_card

router = Router()

from src.utils.escape import clean_md
from src.states import (
    FoodLoggingState,
    MealEditingState,
    MealViewingState,
    LocalCorrectionState,
)
from src.presenters.food import format_food_analysis
from src.utils.telegram_action import send_typing_action
from src.presenters.food import format_draft_card_text, format_drafts_list
from src.keyboards.inline import get_draft_keyboard, correction_keyboard, get_drafts_list_keyboard


async def send_draft_card(message: Message, draft, user_language: str, page: int = 1):
    if not draft:
        return
    text = format_draft_card_text(draft.payload, user_language)
    sent_msg = await message.answer(
        text,
        reply_markup=get_draft_keyboard(draft.id, user_language, page=page),
        parse_mode=None
    )
    card_msg_id = getattr(sent_msg, "message_id", None)
    if isinstance(card_msg_id, int):
        async with AsyncSessionLocal() as db:
            uid = draft.user_id
            if uid:
                await crud.save_meal_draft(db, uid, {**draft.payload, 'card_message_id': card_msg_id, 'draft_page': page}, draft.id)


async def show_pending_meals(message: Message, user_language: str, drafts=None, page: int = 1, user_id: int | None = None):
    uid = user_id or getattr(getattr(message, "from_user", None), "id", None) or getattr(getattr(message, "chat", None), "id", None)
    if drafts is None:
        async with AsyncSessionLocal() as db:
            drafts, total, page, total_pages = await crud.get_pending_meals_page(db, uid, page)
        paged = True
    else:
        total = len(drafts)
        total_pages = max(1, (total + 4) // 5)
        paged = False
    if not drafts:
        await message.answer(i18n_locales.get_text("no_pending_meals", user_language))
        return

    page = max(1, min(page, total_pages))
    text = format_drafts_list(drafts, page, total_pages, user_language, total if paged else None)
    keyboard = get_drafts_list_keyboard(drafts, page, total_pages, user_language, paged=paged)
    await message.answer(text, reply_markup=keyboard, parse_mode=None)


LEGACY_PENDING_MEALS_ALIASES = {
    '📥 Pending meals',
    '📥 Неподтверждённая еда',
    '📥 Непідтверджена їжа',
    '📥 Posiłki do potwierdzenia',
    '📥 Unbestätigte Mahlzeiten',
    '📥 Onay bekleyen öğünler',
    '📥 Comidas pendientes',
}


@router.message(F.text.in_(set(i18n_locales.get_all_translations("btn_pending_meals")) | LEGACY_PENDING_MEALS_ALIASES))
async def show_pending_meals_from_legacy_button(message: Message, user_language: str):
    """Keep the old button working in messages sent before the menu update."""
    await show_pending_meals(message, user_language)



@router.message(F.text.in_(i18n_locales.get_all_translations("btn_log_food")))
async def start_food_logging(message: Message, state: FSMContext, user_language: str):
    async with AsyncSessionLocal() as db:
        await crud.record_event(db, message.from_user.id, 'meal_opened')
    await state.clear()
    await state.set_state(FoodLoggingState.waiting_for_input)
    await message.answer(
        i18n_locales.get_text('ux_quick_food', user_language),
        reply_markup=reply.get_cancel_keyboard(user_language))


@router.message(F.text.in_(i18n_locales.get_all_translations("btn_new_food")))
async def start_new_food_entry(message: Message, state: FSMContext, user_language: str):
    await start_food_logging(message, state, user_language)

@router.message(StateFilter(FoodLoggingState), Command("cancel"))
@router.message(FoodLoggingState.waiting_for_input, F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
@router.message(FoodLoggingState.waiting_for_correction, F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
@router.message(FoodLoggingState.waiting_for_meal_type, F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
async def cancel_food_logging(message: Message, state: FSMContext, user_language: str, db_user):
    data = await state.get_data()
    if data.get("analysis_message_id") and hasattr(message, "bot") and hasattr(message.bot, "edit_message_reply_markup"):
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=data["analysis_message_id"],
                reply_markup=None
            )
        except Exception:
            pass
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        i18n_locales.get_text("food_cancelled", user_language),
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
        parse_mode="Markdown"
    )

@router.message(StateFilter(MealEditingState), Command("cancel"))
@router.message(StateFilter(MealEditingState), F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
@router.message(StateFilter(LocalCorrectionState), Command("cancel"))
@router.message(StateFilter(LocalCorrectionState), F.text.in_(i18n_locales.get_all_translations("btn_cancel") + ["❌ Cancel", "❌ Отмена"]))
async def cancel_meal_editing(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        i18n_locales.get_text("food_cancelled", user_language),
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
        parse_mode="Markdown"
    )

@router.message(FoodLoggingState.waiting_for_meal_type)
async def process_meal_type_selection(message: Message, state: FSMContext, user_language: str, db_user):
    text = (message.text or "").strip()
    
    # Map text
    meal_type = None
    if text in i18n_locales.get_all_translations("meal_type_breakfast"):
        meal_type = "breakfast"
    elif text in i18n_locales.get_all_translations("meal_type_lunch"):
        meal_type = "lunch"
    elif text in i18n_locales.get_all_translations("meal_type_dinner"):
        meal_type = "dinner"
    elif text in i18n_locales.get_all_translations("meal_type_snack"):
        meal_type = "snack"
    elif text in i18n_locales.get_all_translations("meal_type_food"):
        meal_type = "food"
        
    if not meal_type:
        await message.answer(
            i18n_locales.get_text("meal_type_prompt", user_language),
            reply_markup=reply.get_meal_type_keyboard(user_language)
        )
        return

    await state.update_data(meal_type=meal_type)
    await state.set_state(FoodLoggingState.waiting_for_input)
    await message.answer(
        i18n_locales.get_text("food_prompt", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(FoodLoggingState.waiting_for_input)
async def process_food_input(
    message: Message,
    state: FSMContext,
    user_language: str,
    album: Optional[List[Message]] = None
):
    logged_at = message.date.astimezone(UTC).isoformat()
    from src.services.ux import infer_meal_type
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, message.from_user.id)
        current = await state.get_data()
        if user and (not current.get('meal_type') or current.get('logged_at')):
            await state.update_data(meal_type=infer_meal_type(user, message.date))
        await crud.record_event(db, message.from_user.id, 'meal_submitted')
    await state.update_data(logged_at=logged_at)
    image_bytes = None
    images_bytes = None
    image_file_id = None
    image_file_ids = None
    text_desc = None
    
    if album:
        images_bytes = []
        image_file_ids = []
        captions = []
        for m in album:
            if m.photo:
                fid = m.photo[-1].file_id
                image_file_ids.append(fid)
                try:
                    file_info = await message.bot.get_file(fid)
                    image_io = io.BytesIO()
                    await message.bot.download_file(file_info.file_path, image_io)
                    images_bytes.append(image_io.getvalue())
                except Exception as e:
                    pass
            if m.caption:
                captions.append(m.caption.strip())
        
        if captions:
            text_desc = "\n".join(captions)
        if image_file_ids:
            image_file_id = image_file_ids[0]
    else:
        if message.photo:
            image_file_id = message.photo[-1].file_id
            file_info = await message.bot.get_file(image_file_id)
            image_io = io.BytesIO()
            await message.bot.download_file(file_info.file_path, image_io)
            image_bytes = image_io.getvalue()
            if message.caption:
                text_desc = message.caption.strip()
        elif message.text:
            text_desc = message.text.strip()
        else:
            await message.answer(i18n_locales.get_text("food_prompt", user_language))
            return

    async with AsyncSessionLocal() as db:
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            state_data = await state.get_data()
            meal_type = state_data.get("meal_type", "food")
            payload = {
                "text_description": text_desc,
                "logged_at": logged_at,
                "image_file_id": image_file_id,
                "image_file_ids": image_file_ids,
                "language": user_language,
                "meal_type": meal_type
            }
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="analyze_food_input",
                payload=payload
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            status_msg = await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            status_msg_id = getattr(status_msg, "message_id", None)
            if isinstance(status_msg_id, int):
                payload["status_message_id"] = status_msg_id
                await rate_limiter.update_queue_payload(db, queue_id, payload)
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        async with send_typing_action(bot=message.bot, chat_id=message.chat.id):
            analysis = await gemini.analyze_food_input(
                text_description=text_desc,
                image_bytes=image_bytes,
                images_bytes=images_bytes,
                language=user_language,
                user_id=message.from_user.id,
            )
    except Exception as e:
        logger.warning(f"Direct food analysis failed, queuing request: {e}")
        try:
            await wait_msg.edit_text(i18n_locales.get_text("ai_service_unavailable", user_language))
        except Exception:
            pass
        state_data = await state.get_data()
        meal_type = state_data.get("meal_type", "food")
        status_msg_id = getattr(wait_msg, "message_id", None)
        payload = {
            "text_description": text_desc,
            "logged_at": logged_at,
            "image_file_id": image_file_id,
            "image_file_ids": image_file_ids,
            "language": user_language,
            "meal_type": meal_type,
            "status_message_id": status_msg_id if isinstance(status_msg_id, int) else None
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="analyze_food_input",
                payload=payload
            )
        return

    if not analysis:
        try:
            await wait_msg.edit_text(i18n_locales.get_text("err_analysis_failed", user_language))
        except Exception:
            await message.answer(i18n_locales.get_text("err_analysis_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="analyze_food_input")
        
    analysis_dict = analysis.model_dump()
    state_data = await state.get_data()
    async with AsyncSessionLocal() as db:
        draft_id = await crud.save_meal_draft(db, message.from_user.id, {
            "analysis": analysis_dict, "logged_at": logged_at,
            "raw_text": text_desc, "image_file_id": image_file_id,
            "meal_type": state_data.get("meal_type", "food")
        })
    await state.update_data(draft_id=draft_id)
    await state.update_data(
        analysis=analysis_dict,
        image_file_id=image_file_id,
        raw_text=text_desc
    )
    
    result_text = format_food_analysis(analysis, user_language)
    result_text += '\n' + i18n_locales.get_text('meal_type_' + state_data.get('meal_type', 'food'), user_language)
    result_text += '\n' + i18n_locales.get_text('ux_estimate', user_language)
    await state.set_state(FoodLoggingState.waiting_for_confirm)

    card_kb = get_draft_keyboard(draft_id, user_language)
    card_msg = None
    if await try_edit(wait_msg.edit_text,
            result_text,
            reply_markup=card_kb,
            parse_mode="Markdown"
        ):
        card_msg = wait_msg
    else:
        card_msg = await message.answer(
            result_text,
            reply_markup=card_kb,
            parse_mode="Markdown"
        )

    card_msg_id = getattr(card_msg, "message_id", None)
    if isinstance(card_msg_id, int):
        await state.update_data(analysis_message_id=card_msg_id, card_message_id=card_msg_id)
        async with AsyncSessionLocal() as db:
            d = await crud.get_meal_draft(db, draft_id, message.from_user.id)
            if d:
                await crud.save_meal_draft(db, message.from_user.id, {**d.payload, 'card_message_id': card_msg_id}, draft_id)
    else:
        await state.update_data(analysis_message_id=card_msg_id)

@router.message(FoodLoggingState.waiting_for_confirm, is_food_entry)
async def another_meal(message: Message, state: FSMContext, user_language: str, album=None):
    await state.clear()
    await state.set_state(FoodLoggingState.waiting_for_input)
    await process_food_input(message, state, user_language, album=album)


@router.message(FoodLoggingState.waiting_for_confirm)
async def process_food_confirm(message: Message, state: FSMContext, user_language: str, db_user):
    text = (message.text or "").strip()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    
    if text in i18n_locales.get_all_translations("btn_accept"):
        state_data = await state.get_data()
        if not state_data.get("draft_id") and not state_data.get("analysis"):
            from src.handlers.common import recover_menu
            await recover_menu(message, state, user_language, db_user)
            return
        analysis = state_data.get("analysis", {})
        image_file_id = state_data.get("image_file_id")
        raw_text = state_data.get("raw_text")
        meal_type = state_data.get("meal_type", "food")
        
        async with AsyncSessionLocal() as db:
            if state_data.get("draft_id"):
                original_draft = await crud.get_meal_draft(db, state_data['draft_id'], message.from_user.id)
                card_payload = dict(original_draft.payload) if original_draft else None
                saved = await crud.finish_meal_draft(db, state_data["draft_id"], message.from_user.id)
                if saved is None:
                    await state.clear()
                    await message.answer(i18n_locales.get_text("draft_unavailable", user_language))
                    return
                calories = saved.calories
                meal_type = saved.meal_type
            else:
                card_payload = None
                calories = analysis.get("total_calories", 0)
                await crud.add_food_log(
                    db,
                    user_id=message.from_user.id,
                    items_json=analysis.get("food_items", []),
                    calories=calories,
                    proteins=analysis.get("total_protein", 0),
                    fats=analysis.get("total_fat", 0),
                    carbs=analysis.get("total_carb", 0),
                    image_file_id=image_file_id,
                    raw_text=raw_text,
                    meal_type=meal_type,
                    logged_at=datetime.fromisoformat(state_data["logged_at"]) if state_data.get("logged_at") else None
                )

            # Update streaks & check achievements
            db_user_obj = await crud.get_user(db, message.from_user.id)
            if db_user_obj:
                await gamification.process_food_log_streak(db, db_user_obj)
                await gamification.check_new_achievements(db, message.from_user.id)

        if card_payload:
            await finalize_card(message, card_payload, user_language, True)
        elif state_data.get("analysis_message_id") and hasattr(message, "bot") and hasattr(message.bot, "edit_message_reply_markup"):
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id,
                    message_id=state_data["analysis_message_id"],
                    reply_markup=None
                )
            except Exception:
                pass

        confirmation_text = i18n_locales.format_food_logged(meal_type, calories, user_language)
        await message.answer(
            confirmation_text,
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin)
        )
        await state.clear()
        
    elif text in i18n_locales.get_all_translations("btn_cancel"):
        data = await state.get_data()
        if data.get("draft_id"):
            async with AsyncSessionLocal() as db:
                original_draft = await crud.get_meal_draft(db, data['draft_id'], message.from_user.id)
                card_payload = dict(original_draft.payload) if original_draft else None
                cancelled = await crud.finish_meal_draft(db, data["draft_id"], message.from_user.id, accept=False)
            if cancelled and card_payload:
                await finalize_card(message, card_payload, user_language, False)
        if data.get("analysis_message_id") and hasattr(message, "bot") and hasattr(message.bot, "edit_message_reply_markup"):
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=message.chat.id,
                    message_id=data["analysis_message_id"],
                    reply_markup=None
                )
            except Exception:
                pass
        await message.answer(
            i18n_locales.get_text("food_cancelled", user_language),
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin)
        )
        await state.clear()
        
    elif text in i18n_locales.get_all_translations("btn_correct"):
        await state.set_state(FoodLoggingState.waiting_for_correction)
        await message.answer(
            i18n_locales.get_text("food_correction_prompt", user_language),
            reply_markup=reply.get_cancel_keyboard(user_language),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            i18n_locales.get_text("confirm_keyboard_buttons", user_language),
            reply_markup=reply.get_food_confirm_keyboard(user_language)
        )

@router.message(FoodLoggingState.waiting_for_correction)
async def process_food_correction(message: Message, state: FSMContext, user_language: str):
    if not message.text:
        await message.answer(
            i18n_locales.get_text("food_correction_prompt", user_language),
            reply_markup=reply.get_cancel_keyboard(user_language),
            parse_mode="Markdown"
        )
        return
        
    state_data = await state.get_data()
    original_analysis = state_data["analysis"]
    correction_text = message.text.strip()
    if state_data.get('correction_action') == 'add':
        correction_text = 'Add the following food, keeping existing items: ' + correction_text
    
    async with AsyncSessionLocal() as db:
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            payload = {
                "original_data": original_analysis,
                "draft_id": state_data.get("draft_id"),
                "correction_text": correction_text,
                "language": user_language
            }
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_food_analysis",
                payload=payload
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            status_msg = await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            status_msg_id = getattr(status_msg, "message_id", None)
            if isinstance(status_msg_id, int):
                payload["status_message_id"] = status_msg_id
                await rate_limiter.update_queue_payload(db, queue_id, payload)
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        async with send_typing_action(bot=message.bot, chat_id=message.chat.id):
            adjusted_analysis = await gemini.adjust_food_analysis(
                original_data=original_analysis,
                correction_text=correction_text,
                language=user_language,
                user_id=message.from_user.id,
            )
    except Exception as e:
        logger.warning(f"Direct food correction failed, queuing request: {e}")
        try:
            await wait_msg.edit_text(i18n_locales.get_text("ai_service_unavailable", user_language))
        except Exception:
            pass
        status_msg_id = getattr(wait_msg, "message_id", None)
        payload = {
            "original_data": original_analysis,
            "draft_id": state_data.get("draft_id"),
            "correction_text": correction_text,
            "language": user_language,
            "status_message_id": status_msg_id if isinstance(status_msg_id, int) else None
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_food_analysis",
                payload=payload
            )
        return

    if not adjusted_analysis:
        try:
            await wait_msg.edit_text(i18n_locales.get_text("err_correction_failed", user_language))
        except Exception:
            await message.answer(i18n_locales.get_text("err_correction_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="adjust_food_analysis")
        
    analysis_dict = adjusted_analysis.model_dump()
    draft_id = state_data.get("draft_id")
    if draft_id:
        async with AsyncSessionLocal() as db:
            draft = await crud.get_meal_draft(db, draft_id, message.from_user.id)
            if not draft:
                await message.answer(i18n_locales.get_text("draft_unavailable", user_language))
                return
            await crud.save_meal_draft(db, message.from_user.id,
                {**draft.payload, "analysis": analysis_dict}, draft_id=draft_id)
    await state.update_data(analysis=analysis_dict, correction_action=None)
    
    result_text = format_food_analysis(adjusted_analysis, user_language)
    
    await state.set_state(FoodLoggingState.waiting_for_confirm)
    if draft_id:
        from src.services.meal_cards import deliver_card
        async with AsyncSessionLocal() as db:
            fresh = await crud.get_meal_draft(db, draft_id, message.from_user.id)
            if not fresh:
                return
            card_msg_id = await deliver_card(message.bot, db, fresh, message.chat.id,
                user_language, getattr(wait_msg, 'message_id', None))
        await state.update_data(analysis_message_id=card_msg_id, card_message_id=card_msg_id)
        return
    markup = get_draft_keyboard(draft_id, user_language) if draft_id else reply.get_food_confirm_keyboard(user_language)
    card_msg = await message.answer(
            result_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    from aiogram.exceptions import TelegramAPIError
    try:
        await wait_msg.delete()
    except TelegramAPIError:
        logger.info('Could not remove legacy food correction status')

    card_msg_id = getattr(card_msg, "message_id", None)
    if draft_id and isinstance(card_msg_id, int):
        await state.update_data(analysis_message_id=card_msg_id, card_message_id=card_msg_id)
        async with AsyncSessionLocal() as db:
            d = await crud.get_meal_draft(db, draft_id, message.from_user.id)
            if d:
                await crud.save_meal_draft(db, message.from_user.id, {**d.payload, "card_message_id": card_msg_id}, draft_id)

# --- Daily/Historical Meals Management ---

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_my_meals")))
@router.message(F.text == "/meals")
async def start_meals_list(message: Message, state: FSMContext, user_language: str, db_user):
    if not db_user:
        await message.answer(i18n_locales.get_text("profile_prompt_name", user_language))
        return
        
    try:
        user_tz = ZoneInfo(db_user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    local_now = datetime.now(user_tz)
    date_str = local_now.strftime("%Y-%m-%d")
    
    await state.set_state(MealViewingState.viewing)
    await state.update_data(view_date=date_str)
    
    await send_or_edit_meals_message(message, date_str, user_language, db_user)

async def send_or_edit_meals_message(event: Message | CallbackQuery, date_str: str, user_language: str, db_user):
    try:
        user_tz = ZoneInfo(db_user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    try:
        local_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        local_date = datetime.now(user_tz).date()
        date_str = local_date.strftime("%Y-%m-%d")
        
    start_of_day_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=user_tz)
    end_of_day_local = start_of_day_local + timedelta(days=1) - timedelta(microseconds=1)
    
    start_date_utc = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
    end_date_utc = end_of_day_local.astimezone(UTC).replace(tzinfo=None)
    
    async with AsyncSessionLocal() as db:
        meals = await crud.get_food_logs(db, db_user.telegram_id, start_date_utc, end_date_utc)
        
    local_today = datetime.now(user_tz).date()
    local_yesterday = local_today - timedelta(days=1)
    
    if local_date == local_today:
        header_template = "meals_today_header"
        show_next = False
    elif local_date == local_yesterday:
        header_template = "meals_yesterday_header"
        show_next = True
    else:
        header_template = "meals_day_header"
        show_next = local_date < local_today
        
    if header_template == "meals_day_header":
        header = i18n_locales.get_text(header_template, user_language, date=local_date.strftime("%d.%m.%Y"))
    else:
        header = i18n_locales.get_text(header_template, user_language)
        
    if not meals:
        body = i18n_locales.get_text("no_meals_day", user_language)
        reply_markup = reply.get_meals_keyboard([], show_next, user_language)
    else:
        body_items = []
        for idx, meal in enumerate(meals):
            num = idx + 1
            meal_local_time = meal.logged_at.replace(tzinfo=UTC).astimezone(user_tz).strftime("%H:%M")
            items_desc = ", ".join([f"{item.get('name')} ({item.get('portion')})" for item in meal.items_json])
            
            meal_type_emoji = (
                "🍳" if meal.meal_type == "breakfast" else
                "🍲" if meal.meal_type == "lunch" else
                "🍝" if meal.meal_type == "dinner" else
                "🍎" if meal.meal_type == "snack" else "🍽️"
            )
            meal_type_key = f"meal_type_{meal.meal_type}_clean" if meal.meal_type in ["breakfast", "lunch", "dinner", "snack", "food"] else "meal_type_food_clean"
            meal_type_label = i18n_locales.get_text(meal_type_key, user_language)
            
            body_items.append(
                f"{num}. *{meal_local_time}* - {meal_type_emoji} *{meal_type_label}*: {items_desc}\n"
                f"   _Totals: {meal.calories} kcal | P: {meal.proteins:.1f}g, F: {meal.fats:.1f}g, C: {meal.carbs:.1f}g_"
            )
        body = "\n".join(body_items) + "\n\n" + i18n_locales.get_text("meals_today_select", user_language)
        reply_markup = reply.get_meals_keyboard(meals, show_next, user_language)
        
    text = header + "\n" + body
    
    if isinstance(event, CallbackQuery):
        await event.message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await event.answer(text, reply_markup=reply_markup, parse_mode="Markdown")

@router.message(MealViewingState.viewing)
async def process_meals_viewing(message: Message, state: FSMContext, user_language: str, db_user):
    text = (message.text or "").strip()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    
    if text in ["⬅️ Back to Main Menu", "⬅️ Главное меню", "❌ Cancel", "❌ Отмена"]:
        await state.clear()
        await message.answer(
            i18n_locales.get_text("return_to_main_menu", user_language),
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin)
        )
        return
        
    state_data = await state.get_data()
    date_str = state_data.get("view_date")
    if not date_str:
        try:
            user_tz = ZoneInfo(db_user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
        date_str = datetime.now(user_tz).strftime("%Y-%m-%d")
        await state.update_data(view_date=date_str)
        
    try:
        user_tz = ZoneInfo(db_user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    current_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    
    if text in i18n_locales.get_all_translations("btn_prev_day"):
        new_date = current_date - timedelta(days=1)
        new_date_str = new_date.strftime("%Y-%m-%d")
        await state.update_data(view_date=new_date_str)
        await send_or_edit_meals_message(message, new_date_str, user_language, db_user)
        return
        
    if text in i18n_locales.get_all_translations("btn_next_day"):
        local_today = datetime.now(user_tz).date()
        if current_date < local_today:
            new_date = current_date + timedelta(days=1)
            new_date_str = new_date.strftime("%Y-%m-%d")
            await state.update_data(view_date=new_date_str)
            await send_or_edit_meals_message(message, new_date_str, user_language, db_user)
        return

    edit_match = re.match(r"(?:✏️\s*(?:Edit|Изменить)\s*#(\d+))", text, re.IGNORECASE)
    delete_match = re.match(r"(?:❌\s*(?:Delete|Удалить)\s*#(\d+))", text, re.IGNORECASE)
    
    if edit_match or delete_match:
        num = int(edit_match.group(1) if edit_match else delete_match.group(1))
        
        start_of_day_local = datetime(current_date.year, current_date.month, current_date.day, tzinfo=user_tz)
        end_of_day_local = start_of_day_local + timedelta(days=1) - timedelta(microseconds=1)
        start_date_utc = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
        end_date_utc = end_of_day_local.astimezone(UTC).replace(tzinfo=None)
        
        async with AsyncSessionLocal() as db:
            meals = await crud.get_food_logs(db, db_user.telegram_id, start_date_utc, end_date_utc)
            
        if not meals or num <= 0 or num > len(meals):
            await message.answer(i18n_locales.get_text("err_invalid_meal_selection", user_language))
            return
            
        selected_meal = meals[num - 1]
        
        if delete_match:
            async with AsyncSessionLocal() as db:
                success = await crud.delete_food_log(db, selected_meal.id, message.from_user.id)
            if success:
                await message.answer(i18n_locales.get_text("meal_deleted", user_language))
            else:
                await message.answer(i18n_locales.get_text("err_meal_not_found", user_language))
            await send_or_edit_meals_message(message, date_str, user_language, db_user)
            
        elif edit_match:
            meal_time_str = selected_meal.logged_at.replace(tzinfo=UTC).astimezone(user_tz).strftime("%H:%M")
            original_data = {
                "food_items": selected_meal.items_json,
                "total_calories": selected_meal.calories,
                "total_protein": selected_meal.proteins,
                "total_fat": selected_meal.fats,
                "total_carb": selected_meal.carbs
            }
            await state.set_state(MealEditingState.waiting_for_edit_text)
            await state.update_data(
                edit_meal_id=selected_meal.id,
                edit_date_str=date_str,
                original_data=original_data
            )
            items_list_str = ""
            for item in selected_meal.items_json:
                items_list_str += f"- {item.get('name')} ({item.get('portion')}): {item.get('calories')} kcal\n"
                
            prompt_msg = i18n_locales.get_text(
                "edit_meal_prompt",
                user_language,
                time=meal_time_str,
                items=items_list_str
            )
            await message.answer(
                prompt_msg,
                reply_markup=reply.get_cancel_keyboard(user_language),
                parse_mode="Markdown"
            )
        return
        
    await message.answer(
        i18n_locales.get_text("select_menu_option", user_language)
    )
    await send_or_edit_meals_message(message, date_str, user_language, db_user)

@router.message(MealEditingState.waiting_for_edit_text)
async def process_meal_edit_text(message: Message, state: FSMContext, user_language: str):
    if not message.text:
        await message.answer(
            i18n_locales.get_text("prompt_edit_text_only", user_language),
            reply_markup=reply.get_cancel_keyboard(user_language)
        )
        return
        
    state_data = await state.get_data()
    original_data = state_data["original_data"]
    correction_text = message.text.strip()
    
    async with AsyncSessionLocal() as db:
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            payload = {
                "original_data": original_data,
                "correction_text": correction_text,
                "language": user_language,
                "edit_meal_id": state_data.get("edit_meal_id"),
                "edit_date_str": state_data.get("edit_date_str")
            }
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_meal_edit",
                payload=payload
            )
            position = await rate_limiter.get_queue_position(db, queue_id)
            status_msg = await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            status_msg_id = getattr(status_msg, "message_id", None)
            if isinstance(status_msg_id, int):
                payload["status_message_id"] = status_msg_id
                await rate_limiter.update_queue_payload(db, queue_id, payload)
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        async with send_typing_action(bot=message.bot, chat_id=message.chat.id):
            adjusted_analysis = await gemini.adjust_food_analysis(
                original_data=original_data,
                correction_text=correction_text,
                language=user_language,
                user_id=message.from_user.id,
            )
    except Exception as e:
        logger.warning(f"Direct meal edit adjustment failed, queuing request: {e}")
        try:
            await wait_msg.edit_text(i18n_locales.get_text("ai_service_unavailable", user_language))
        except Exception:
            pass
        status_msg_id = getattr(wait_msg, "message_id", None)
        payload = {
            "original_data": original_data,
            "correction_text": correction_text,
            "language": user_language,
            "edit_meal_id": state_data.get("edit_meal_id"),
            "edit_date_str": state_data.get("edit_date_str"),
            "status_message_id": status_msg_id if isinstance(status_msg_id, int) else None
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_meal_edit",
                payload=payload
            )
        return

    if not adjusted_analysis:
        try:
            await wait_msg.edit_text(i18n_locales.get_text("err_correction_failed", user_language))
        except Exception:
            await message.answer(i18n_locales.get_text("err_correction_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="adjust_meal_edit")
        
    adjusted_dict = adjusted_analysis.model_dump()
    await state.update_data(adjusted_analysis=adjusted_dict)
    
    result_text = format_food_analysis(adjusted_analysis, user_language)
    
    await state.set_state(MealEditingState.waiting_for_edit_confirm)
    markup = reply.get_meal_edit_confirm_keyboard(user_language)
    await message.answer(
            result_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    from aiogram.exceptions import TelegramAPIError
    try:
        await wait_msg.delete()
    except TelegramAPIError:
        logger.info('Could not remove meal-edit status message')

@router.message(MealEditingState.waiting_for_edit_confirm)
async def process_meal_edit_confirm(message: Message, state: FSMContext, user_language: str, db_user):
    text = (message.text or "").strip()
    state_data = await state.get_data()
    meal_id = state_data["edit_meal_id"]
    date_str = state_data["edit_date_str"]
    
    if text in i18n_locales.get_all_translations("btn_accept"):
        adjusted = state_data["adjusted_analysis"]
        async with AsyncSessionLocal() as db:
            await crud.update_food_log(
                db,
                log_id=meal_id,
                user_id=message.from_user.id,
                items_json=adjusted["food_items"],
                calories=adjusted["total_calories"],
                proteins=adjusted["total_protein"],
                fats=adjusted["total_fat"],
                carbs=adjusted["total_carb"]
            )
            
        await message.answer(i18n_locales.get_text("edit_meal_success", user_language))
        await state.set_state(MealViewingState.viewing)
        await state.update_data(view_date=date_str)
        await state.update_data(adjusted_analysis=None, edit_meal_id=None, original_data=None)
        
        await send_or_edit_meals_message(message, date_str, user_language, db_user)
        
    elif text in i18n_locales.get_all_translations("btn_cancel"):
        await message.answer(i18n_locales.get_text("food_cancelled", user_language))
        await state.set_state(MealViewingState.viewing)
        await state.update_data(view_date=date_str)
        await state.update_data(adjusted_analysis=None, edit_meal_id=None, original_data=None)
        
        await send_or_edit_meals_message(message, date_str, user_language, db_user)
        
    elif text in i18n_locales.get_all_translations("btn_correct"):
        await state.set_state(MealEditingState.waiting_for_edit_text)
        await message.answer(
            i18n_locales.get_text("food_correction_prompt", user_language),
            reply_markup=reply.get_cancel_keyboard(user_language),
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            i18n_locales.get_text("confirm_keyboard_buttons", user_language),
            reply_markup=reply.get_meal_edit_confirm_keyboard(user_language)
        )


@router.callback_query(F.data == "uxdraft:noop")
async def uxdraft_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "uxdraft:close")
async def uxdraft_close(callback: CallbackQuery):
    await callback.answer()
    if hasattr(callback.message, "delete"):
        try:
            await callback.message.delete()
            return
        except Exception:
            pass
    if hasattr(callback.message, "edit_reply_markup"):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data.startswith("uxdraft:list:"))
async def uxdraft_show_list(callback: CallbackQuery, user_language: str):
    await callback.answer()
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 1
    async with AsyncSessionLocal() as db:
        drafts, total, page, total_pages = await crud.get_pending_meals_page(db, callback.from_user.id, page)
    if not drafts:
        if hasattr(callback.message, "edit_text"):
            if await try_edit(callback.message.edit_text,
                    i18n_locales.get_text("no_pending_meals", user_language),
                    reply_markup=None
                ):
                return
        await callback.message.answer(i18n_locales.get_text("no_pending_meals", user_language))
        return

    text = format_drafts_list(drafts, page, total_pages, user_language, total)
    keyboard = get_drafts_list_keyboard(drafts, page, total_pages, user_language, paged=True)
    if hasattr(callback.message, "edit_text"):
        if await try_edit(callback.message.edit_text, text, reply_markup=keyboard, parse_mode=None):
            return
    await callback.message.answer(text, reply_markup=keyboard, parse_mode=None)


@router.callback_query(F.data.startswith("uxdraft:view:"))
async def uxdraft_view_card(callback: CallbackQuery, user_language: str):
    try:
        parts = callback.data.split(":")
        draft_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 1
    except (IndexError, ValueError):
        await callback.answer()
        return

    async with AsyncSessionLocal() as db:
        draft = await crud.get_meal_draft(db, draft_id, callback.from_user.id)
        if not draft:
            await callback.answer(i18n_locales.get_text("draft_unavailable", user_language), show_alert=True)
            return
        msg_id = getattr(callback.message, "message_id", None)
        new_payload = {**draft.payload, "draft_page": page}
        if isinstance(msg_id, int):
            new_payload["card_message_id"] = msg_id
        await crud.save_meal_draft(db, callback.from_user.id, new_payload, draft.id)

    await callback.answer()
    card_text = format_draft_card_text(new_payload, user_language)
    card_keyboard = get_draft_keyboard(draft.id, user_language, page=page)
    if hasattr(callback.message, "edit_text"):
        if await try_edit(callback.message.edit_text, card_text, reply_markup=card_keyboard, parse_mode=None):
            return
    async with AsyncSessionLocal() as db:
        fresh = await crud.get_meal_draft(db, draft_id, callback.from_user.id)
    await send_draft_card(callback.message, fresh, user_language, page=page)


@router.callback_query(F.data.startswith('uxdraft:'))
async def quick_correction(callback: CallbackQuery, state: FSMContext, user_language: str):
    from src.services.ux import adjust_locally
    try:
        parts = callback.data.split(':')
        if len(parts) not in (3, 4):
            raise ValueError()
        _, action, raw_id, *extra = parts
        draft_id = int(raw_id)
        if action not in ('type', 'portion', 'add', 'remove', 'manual', 'back'):
            raise ValueError()

        async with AsyncSessionLocal() as db:
            draft = await crud.get_meal_draft(db, draft_id, callback.from_user.id)
            if not draft:
                await callback.answer(i18n_locales.get_text('draft_unavailable', user_language), show_alert=True)
                return

            page = draft.payload.get('draft_page', 1)

            if action == 'back':
                await callback.answer()
                if hasattr(callback.message, 'edit_reply_markup'):
                    try:
                        await callback.message.edit_reply_markup(reply_markup=get_draft_keyboard(draft_id, user_language, page=page))
                    except Exception:
                        pass
                return

            if action == 'type':
                if not extra:
                    await callback.answer()
                    choices = [(k, i18n_locales.get_text('meal_type_' + k, user_language)) for k in ('breakfast', 'lunch', 'dinner', 'snack', 'food')]
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=label, callback_data=f'uxdraft:type:{draft_id}:{key}')] for key, label in choices
                    ] + [[InlineKeyboardButton(text=i18n_locales.get_text('ux_previous', user_language), callback_data=f'uxdraft:back:{draft_id}')]])
                    if hasattr(callback.message, 'edit_reply_markup'):
                        if not await try_edit(callback.message.edit_reply_markup, reply_markup=keyboard):
                            await callback.message.answer(i18n_locales.get_text('ux_type', user_language), reply_markup=keyboard)
                    else:
                        await callback.message.answer(i18n_locales.get_text('ux_type', user_language), reply_markup=keyboard)
                    return
                else:
                    if extra[0] not in ('breakfast', 'lunch', 'dinner', 'snack', 'food'):
                        raise ValueError()
                    msg_id = getattr(callback.message, "message_id", None)
                    new_payload = {**draft.payload, 'meal_type': extra[0]}
                    if isinstance(msg_id, int):
                        new_payload['card_message_id'] = msg_id
                    await crud.save_meal_draft(db, callback.from_user.id, new_payload, draft_id)
                    await callback.answer(i18n_locales.get_text('ux_saved', user_language))

                    new_text = format_draft_card_text(new_payload, user_language)
                    edited = False
                    if hasattr(callback.message, 'edit_text'):
                        edited = await try_edit(callback.message.edit_text, new_text,
                            reply_markup=get_draft_keyboard(draft_id, user_language, page=page))
                    if not edited:
                        await send_draft_card(callback.message, await crud.get_meal_draft(db, draft_id, callback.from_user.id), user_language, page=page)
                    return

            if action == 'remove':
                items = draft.payload['analysis']['food_items']
                if len(items) <= 1:
                    await callback.answer(i18n_locales.get_text('ux_cannot_remove_last', user_language), show_alert=True)
                    return
                if not extra:
                    await callback.answer()
                    choices = [(str(i), item['name'][:50]) for i, item in enumerate(items)]
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f"❌ {label}", callback_data=f'uxdraft:remove:{draft_id}:{key}')] for key, label in choices
                    ] + [[InlineKeyboardButton(text=i18n_locales.get_text('ux_previous', user_language), callback_data=f'uxdraft:back:{draft_id}')]])
                    if hasattr(callback.message, 'edit_reply_markup'):
                        if not await try_edit(callback.message.edit_reply_markup, reply_markup=keyboard):
                            await callback.message.answer(i18n_locales.get_text('ux_remove_item', user_language), reply_markup=keyboard)
                    else:
                        await callback.message.answer(i18n_locales.get_text('ux_remove_item', user_language), reply_markup=keyboard)
                    return
                else:
                    analysis = adjust_locally(draft.payload['analysis'], 'remove', extra[0])
                    msg_id = getattr(callback.message, "message_id", None)
                    new_payload = {**draft.payload, 'analysis': analysis}
                    if isinstance(msg_id, int):
                        new_payload['card_message_id'] = msg_id
                    await crud.save_meal_draft(db, callback.from_user.id, new_payload, draft_id)
                    await callback.answer(i18n_locales.get_text('ux_saved', user_language))

                    new_text = format_draft_card_text(new_payload, user_language)
                    edited = False
                    if hasattr(callback.message, 'edit_text'):
                        edited = await try_edit(callback.message.edit_text, new_text,
                            reply_markup=get_draft_keyboard(draft_id, user_language, page=page))
                    if not edited:
                        await send_draft_card(callback.message, await crud.get_meal_draft(db, draft_id, callback.from_user.id), user_language, page=page)
                    return

            msg_id = getattr(callback.message, "message_id", None)
            if isinstance(msg_id, int) and draft.payload.get('card_message_id') != msg_id:
                await crud.save_meal_draft(db, callback.from_user.id, {**draft.payload, 'card_message_id': msg_id}, draft_id)

        await callback.answer()
        card_msg_id = getattr(callback.message, "message_id", None) or draft.payload.get('card_message_id')
        await state.update_data({
            **draft.payload,
            'draft_id': draft_id,
            'correction_action': action,
            'card_message_id': card_msg_id,
        })
        await state.set_state(FoodLoggingState.waiting_for_correction if action == 'add' else LocalCorrectionState.value)
        key = 'ux_add_item' if action == 'add' else f'ux_{action}_prompt'
        await callback.message.answer(i18n_locales.get_text(key, user_language), reply_markup=reply.get_cancel_keyboard(user_language))
    except (ValueError, TypeError, IndexError):
        await callback.answer(i18n_locales.get_text('ux_invalid', user_language), show_alert=True)


@router.message(LocalCorrectionState.value)
async def local_correction(message: Message, state: FSMContext, user_language: str):
    from src.services.ux import adjust_locally
    data = await state.get_data()
    async with AsyncSessionLocal() as db:
        draft = await crud.get_meal_draft(db, data.get('draft_id'), message.from_user.id)
        if not draft:
            await state.clear()
            await message.answer(i18n_locales.get_text('draft_unavailable', user_language))
            return
        try:
            analysis = adjust_locally(draft.payload['analysis'], data.get('correction_action'), message.text)
        except (ValueError, TypeError):
            await message.answer(i18n_locales.get_text('ux_invalid', user_language))
            return
        new_payload = {**draft.payload, 'analysis': analysis}
        await crud.save_meal_draft(db, message.from_user.id, new_payload, draft.id)
        fresh = await crud.get_meal_draft(db, draft.id, message.from_user.id)
        await state.update_data(analysis=analysis, correction_action=None)
        await state.set_state(FoodLoggingState.waiting_for_confirm)

        card_message_id = data.get('card_message_id') or draft.payload.get('card_message_id')
        page = new_payload.get('draft_page', 1)
        edited = False
        if isinstance(card_message_id, int) and hasattr(message, 'bot') and hasattr(message.bot, 'edit_message_text'):
            new_text = format_draft_card_text(new_payload, user_language)
            edited = await try_edit(message.bot.edit_message_text,
                    chat_id=message.chat.id,
                    message_id=card_message_id,
                    text=new_text,
                    reply_markup=get_draft_keyboard(draft.id, user_language, page=page)
                )

        if edited:
            await message.answer(
                i18n_locales.get_text('ux_saved', user_language),
                reply_markup=reply.get_food_confirm_keyboard(user_language)
            )
        else:
            await send_draft_card(message, fresh, user_language, page=page)


@router.callback_query(F.data.startswith("meal_draft:"))
async def handle_meal_draft(callback: CallbackQuery, state: FSMContext, user_language: str, db_user):
    try:
        _, action, raw_id = callback.data.split(":")
        draft_id = int(raw_id)
    except (ValueError, TypeError):
        await callback.answer()
        return
    if action not in {"accept", "correct", "cancel"}:
        await callback.answer()
        return
    async with AsyncSessionLocal() as db:
        original_draft = await crud.get_meal_draft(db, draft_id, callback.from_user.id)
        card_payload = dict(original_draft.payload) if original_draft else None
        if action == "correct":
            draft = await crud.get_meal_draft(db, draft_id, callback.from_user.id)
            if draft:
                await state.update_data(**draft.payload, draft_id=draft_id)
                await state.set_state(FoodLoggingState.waiting_for_correction)
        else:
            draft = await crud.finish_meal_draft(db, draft_id, callback.from_user.id, accept=action == "accept")
            if draft and action == "accept":
                current_user = await crud.get_user(db, callback.from_user.id)
                await gamification.process_food_log_streak(db, current_user)
                await gamification.check_new_achievements(db, callback.from_user.id)
    if not draft:
        await callback.answer(i18n_locales.get_text("draft_unavailable", user_language), show_alert=True)
        return
    await callback.answer()
    if action == "correct":
        keyboard = correction_keyboard(draft_id, user_language)
        if hasattr(callback.message, 'edit_reply_markup'):
            if not await try_edit(callback.message.edit_reply_markup, reply_markup=keyboard):
                await callback.message.answer(i18n_locales.get_text("food_correction_prompt", user_language),
                                              reply_markup=keyboard)
        else:
            await callback.message.answer(i18n_locales.get_text("food_correction_prompt", user_language),
                                          reply_markup=keyboard)
        return
    data = await state.get_data()
    if data.get("draft_id") == draft_id:
        await state.clear()
    # Remove active buttons and confirmation question from the card message
    if card_payload:
        await finalize_card(callback.message, card_payload, user_language, action == 'accept')

    if action == "accept":
        meal_type = getattr(draft, "meal_type", None) or "food"
        calories = getattr(draft, "calories", 0)
        confirmation_text = i18n_locales.format_food_logged(meal_type, calories, user_language)
    else:
        confirmation_text = i18n_locales.get_text("food_cancelled", user_language)

    await callback.message.answer(
        confirmation_text,
        reply_markup=reply.get_main_menu(user_language, is_admin=db_user.is_admin or db_user.telegram_id in settings.ADMIN_USER_IDS)
    )

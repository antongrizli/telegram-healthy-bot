import io
import re
from typing import List, Optional
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.keyboards import reply
from src.services import gemini, rate_limiter
from src.config import settings
from src.services import gamification

router = Router()

def clean_md(text: str) -> str:
    if not text:
        return ""
    for char in ["*", "_", "[", "]", "`"]:
        text = text.replace(char, "")
    return text

class FoodLoggingState(StatesGroup):
    waiting_for_meal_type = State()
    waiting_for_input = State()
    waiting_for_confirm = State()
    waiting_for_correction = State()

class MealEditingState(StatesGroup):
    waiting_for_edit_text = State()
    waiting_for_edit_confirm = State()

class MealViewingState(StatesGroup):
    viewing = State()


@router.message(F.text.in_(i18n_locales.get_all_translations("btn_log_food")))
async def start_food_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(FoodLoggingState.waiting_for_meal_type)
    await message.answer(
        i18n_locales.get_text("meal_type_prompt", user_language),
        reply_markup=reply.get_meal_type_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(StateFilter(FoodLoggingState), F.text.in_(["❌ Cancel", "❌ Отмена"]))
async def cancel_food_logging(message: Message, state: FSMContext, user_language: str, db_user):
    await state.clear()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    await message.answer(
        i18n_locales.get_text("food_cancelled", user_language),
        reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
        parse_mode="Markdown"
    )

@router.message(StateFilter(MealEditingState), F.text.in_(["❌ Cancel", "❌ Отмена"]))
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
    text = message.text.strip()
    
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
            await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        analysis = await gemini.analyze_food_input(
            text_description=text_desc,
            image_bytes=image_bytes,
            images_bytes=images_bytes,
            language=user_language
        )
    except Exception as e:
        await wait_msg.delete()
        state_data = await state.get_data()
        meal_type = state_data.get("meal_type", "food")
        payload = {
            "text_description": text_desc,
            "image_file_id": image_file_id,
            "image_file_ids": image_file_ids,
            "language": user_language,
            "meal_type": meal_type
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="analyze_food_input",
                payload=payload
            )
        await message.answer(
            i18n_locales.get_text("ai_service_unavailable", user_language)
        )
        return
    
    await wait_msg.delete()
    
    if not analysis:
        await message.answer(i18n_locales.get_text("err_analysis_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="analyze_food_input")
        
    analysis_dict = analysis.model_dump()
    await state.update_data(
        analysis=analysis_dict,
        image_file_id=image_file_id,
        raw_text=text_desc
    )
    
    items_str = ""
    for item in analysis.food_items:
        name = clean_md(item.name)
        portion = clean_md(item.portion)
        items_str += f"- **{name}** ({portion}): {item.calories} kcal | P: {item.protein}g, F: {item.fat}g, C: {item.carb}g\n"
        
    result_text = i18n_locales.get_text(
        "food_analysis_result",
        user_language,
        items=items_str,
        calories=analysis.total_calories,
        protein=analysis.total_protein,
        fat=analysis.total_fat,
        carb=analysis.total_carb
    )
    
    await state.set_state(FoodLoggingState.waiting_for_confirm)
    await message.answer(
        result_text,
        reply_markup=reply.get_food_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(FoodLoggingState.waiting_for_confirm)
async def process_food_confirm(message: Message, state: FSMContext, user_language: str, db_user):
    text = message.text.strip()
    is_admin = db_user.telegram_id in settings.ADMIN_USER_IDS or db_user.is_admin if db_user else False
    
    if text in i18n_locales.get_all_translations("btn_accept"):
        state_data = await state.get_data()
        analysis = state_data["analysis"]
        image_file_id = state_data["image_file_id"]
        raw_text = state_data["raw_text"]
        meal_type = state_data.get("meal_type", "food")
        
        async with AsyncSessionLocal() as db:
            await crud.add_food_log(
                db,
                user_id=message.from_user.id,
                items_json=analysis["food_items"],
                calories=analysis["total_calories"],
                proteins=analysis["total_protein"],
                fats=analysis["total_fat"],
                carbs=analysis["total_carb"],
                image_file_id=image_file_id,
                raw_text=raw_text,
                meal_type=meal_type
            )
            
            # Update streaks & check achievements
            db_user_obj = await crud.get_user(db, message.from_user.id)
            streak_val, freeze_used, freezes_left = 0, False, 1
            new_ach_keys = []
            if db_user_obj:
                streak_val, freeze_used, freezes_left = await gamification.process_food_log_streak(db, db_user_obj)
                new_ach_keys = await gamification.check_new_achievements(db, message.from_user.id)
            
            ach_notifs = []
            for ach_key in new_ach_keys:
                ach_def = gamification.ACHIEVEMENTS.get(ach_key)
                if ach_def:
                    icon = ach_def["icon"]
                    name = i18n_locales.get_text(ach_def["name_key"], user_language)
                    desc = i18n_locales.get_text(ach_def["desc_key"], user_language)
                    ach_notifs.append(f"{icon} *{name}* — {desc}")
            
        msg_parts = [i18n_locales.get_text("food_logged", user_language)]
        msg_parts.append(f"\n🔥 *{i18n_locales.get_text('streak_count', user_language, count=streak_val)}*")
        
        if freeze_used:
            msg_parts.append(i18n_locales.get_text("streak_freeze_used", user_language, count=freezes_left))
            
        if ach_notifs:
            msg_parts.append(f"\n🏆 *{i18n_locales.get_text('achievements_unlocked_title', user_language)}*")
            msg_parts.extend(ach_notifs)
            
        await message.answer(
            "\n".join(msg_parts),
            reply_markup=reply.get_main_menu(user_language, is_admin=is_admin),
            parse_mode="Markdown"
        )
        await state.clear()
        
    elif text in i18n_locales.get_all_translations("btn_cancel"):
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
    
    async with AsyncSessionLocal() as db:
        is_limited, _ = await rate_limiter.check_rate_limit(db)
        if is_limited:
            payload = {
                "original_data": original_analysis,
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
            await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        adjusted_analysis = await gemini.adjust_food_analysis(
            original_data=original_analysis,
            correction_text=correction_text,
            language=user_language
        )
    except Exception as e:
        await wait_msg.delete()
        payload = {
            "original_data": original_analysis,
            "correction_text": correction_text,
            "language": user_language
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_food_analysis",
                payload=payload
            )
        await message.answer(
            i18n_locales.get_text("ai_service_unavailable", user_language)
        )
        return
    
    await wait_msg.delete()
    
    if not adjusted_analysis:
        await message.answer(i18n_locales.get_text("err_correction_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="adjust_food_analysis")
        
    analysis_dict = adjusted_analysis.model_dump()
    await state.update_data(analysis=analysis_dict)
    
    items_str = ""
    for item in adjusted_analysis.food_items:
        name = clean_md(item.name)
        portion = clean_md(item.portion)
        items_str += f"- **{name}** ({portion}): {item.calories} kcal | P: {item.protein}g, F: {item.fat}g, C: {item.carb}g\n"
        
    result_text = i18n_locales.get_text(
        "food_analysis_result",
        user_language,
        items=items_str,
        calories=adjusted_analysis.total_calories,
        protein=adjusted_analysis.total_protein,
        fat=adjusted_analysis.total_fat,
        carb=adjusted_analysis.total_carb
    )
    
    await state.set_state(FoodLoggingState.waiting_for_confirm)
    await message.answer(
        result_text,
        reply_markup=reply.get_food_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

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
        try:
            await event.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            pass
    else:
        await event.answer(text, reply_markup=reply_markup, parse_mode="Markdown")

@router.message(MealViewingState.viewing)
async def process_meals_viewing(message: Message, state: FSMContext, user_language: str, db_user):
    text = message.text.strip()
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
            await message.answer(
                i18n_locales.get_text("rate_limit_queued", user_language, position=position)
            )
            return

    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    try:
        adjusted_analysis = await gemini.adjust_food_analysis(
            original_data=original_data,
            correction_text=correction_text,
            language=user_language
        )
    except Exception as e:
        await wait_msg.delete()
        payload = {
            "original_data": original_data,
            "correction_text": correction_text,
            "language": user_language,
            "edit_meal_id": state_data.get("edit_meal_id"),
            "edit_date_str": state_data.get("edit_date_str")
        }
        async with AsyncSessionLocal() as db:
            queue_id = await rate_limiter.add_to_queue(
                db,
                user_id=message.from_user.id,
                chat_id=message.chat.id,
                request_type="adjust_meal_edit",
                payload=payload
            )
        await message.answer(
            i18n_locales.get_text("ai_service_unavailable", user_language)
        )
        return
    
    await wait_msg.delete()
    
    if not adjusted_analysis:
        await message.answer(i18n_locales.get_text("err_correction_failed", user_language))
        return

    async with AsyncSessionLocal() as db:
        await rate_limiter.log_ai_request(db, user_id=message.from_user.id, request_type="adjust_meal_edit")
        
    adjusted_dict = adjusted_analysis.model_dump()
    await state.update_data(adjusted_analysis=adjusted_dict)
    
    items_str = ""
    for item in adjusted_analysis.food_items:
        name = clean_md(item.name)
        portion = clean_md(item.portion)
        items_str += f"- **{name}** ({portion}): {item.calories} kcal | P: {item.protein}g, F: {item.fat}g, C: {item.carb}g\n"
        
    result_text = i18n_locales.get_text(
        "food_analysis_result",
        user_language,
        items=items_str,
        calories=adjusted_analysis.total_calories,
        protein=adjusted_analysis.total_protein,
        fat=adjusted_analysis.total_fat,
        carb=adjusted_analysis.total_carb
    )
    
    await state.set_state(MealEditingState.waiting_for_edit_confirm)
    await message.answer(
        result_text,
        reply_markup=reply.get_meal_edit_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(MealEditingState.waiting_for_edit_confirm)
async def process_meal_edit_confirm(message: Message, state: FSMContext, user_language: str, db_user):
    text = message.text.strip()
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

import io
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.keyboards import inline
from src.services import gemini

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


@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_log_food"],
    i18n_locales.LOCALES["ru"]["btn_log_food"]
]))
async def start_food_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(FoodLoggingState.waiting_for_meal_type)
    await message.answer(
        i18n_locales.get_text("meal_type_prompt", user_language),
        reply_markup=inline.get_meal_type_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(FoodLoggingState.waiting_for_meal_type, F.data.startswith("meal_type:"))
async def process_meal_type_selection(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    meal_type = callback.data.split(":")[1]
    await state.update_data(meal_type=meal_type)
    
    await state.set_state(FoodLoggingState.waiting_for_input)
    await callback.message.edit_text(
        i18n_locales.get_text("food_prompt", user_language),
        parse_mode="Markdown"
    )

@router.message(FoodLoggingState.waiting_for_meal_type)
async def process_meal_type_message_fallback(message: Message, state: FSMContext, user_language: str):
    menu_texts = [
        i18n_locales.LOCALES["en"]["btn_my_profile"], i18n_locales.LOCALES["ru"]["btn_my_profile"],
        i18n_locales.LOCALES["en"]["btn_log_food"], i18n_locales.LOCALES["ru"]["btn_log_food"],
        i18n_locales.LOCALES["en"]["btn_log_weight"], i18n_locales.LOCALES["ru"]["btn_log_weight"],
        i18n_locales.LOCALES["en"]["btn_daily_report"], i18n_locales.LOCALES["ru"]["btn_daily_report"],
        i18n_locales.LOCALES["en"]["btn_weekly_report"], i18n_locales.LOCALES["ru"]["btn_weekly_report"],
        i18n_locales.LOCALES["en"]["btn_my_meals"], i18n_locales.LOCALES["ru"]["btn_my_meals"],
        i18n_locales.LOCALES["en"]["btn_help"], i18n_locales.LOCALES["ru"]["btn_help"]
    ]
    if message.text in menu_texts:
        await state.clear()
        if message.text in [i18n_locales.LOCALES["en"]["btn_log_food"], i18n_locales.LOCALES["ru"]["btn_log_food"]]:
            await start_food_logging(message, state, user_language)
        else:
            await message.answer(i18n_locales.get_text("food_cancelled", user_language))
        return
        
    await message.answer(
        i18n_locales.get_text("meal_type_prompt", user_language),
        reply_markup=inline.get_meal_type_keyboard(user_language)
    )

@router.message(FoodLoggingState.waiting_for_input)
async def process_food_input(message: Message, state: FSMContext, user_language: str):
    image_bytes = None
    image_file_id = None
    text_desc = None
    
    # 1. Check if user uploaded a photo
    if message.photo:
        image_file_id = message.photo[-1].file_id
        file_info = await message.bot.get_file(image_file_id)
        image_io = io.BytesIO()
        await message.bot.download_file(file_info.file_path, image_io)
        image_bytes = image_io.getvalue()
    elif message.text:
        # If they clicked main menu button instead of entering food, exit state and handle normally
        menu_texts = [
            i18n_locales.LOCALES["en"]["btn_my_profile"], i18n_locales.LOCALES["ru"]["btn_my_profile"],
            i18n_locales.LOCALES["en"]["btn_log_weight"], i18n_locales.LOCALES["ru"]["btn_log_weight"],
            i18n_locales.LOCALES["en"]["btn_daily_report"], i18n_locales.LOCALES["ru"]["btn_daily_report"],
            i18n_locales.LOCALES["en"]["btn_weekly_report"], i18n_locales.LOCALES["ru"]["btn_weekly_report"],
            i18n_locales.LOCALES["en"]["btn_help"], i18n_locales.LOCALES["ru"]["btn_help"]
        ]
        if message.text in menu_texts:
            await state.clear()
            # We let next handlers run or answer directly.
            # But in aiogram router handling order, clearing state here won't re-trigger handlers for this message.
            # We answer it directly.
            await message.answer(i18n_locales.get_text("food_cancelled", user_language))
            return
            
        text_desc = message.text.strip()
    else:
        # Audio, sticker or document - not supported directly
        await message.answer(i18n_locales.get_text("food_prompt", user_language))
        return

    # Call Gemini for analysis
    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    analysis = await gemini.analyze_food_input(
        text_description=text_desc,
        image_bytes=image_bytes,
        language=user_language
    )
    
    await wait_msg.delete()
    
    if not analysis:
        # Fallback error
        await message.answer("⚠️ Analysis failed. Try describing the food again or checking your internet connection.")
        return
        
    # Store parameters in state context
    # Pydantic model dict representation
    analysis_dict = analysis.model_dump()
    await state.update_data(
        analysis=analysis_dict,
        image_file_id=image_file_id,
        raw_text=text_desc
    )
    
    # Format description items
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
        reply_markup=inline.get_food_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(FoodLoggingState.waiting_for_confirm, F.data == "food:accept")
async def accept_food_log(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    state_data = await state.get_data()
    analysis = state_data["analysis"]
    image_file_id = state_data["image_file_id"]
    raw_text = state_data["raw_text"]
    meal_type = state_data.get("meal_type", "food")
    
    async with AsyncSessionLocal() as db:
        await crud.add_food_log(
            db,
            user_id=callback.from_user.id,
            items_json=analysis["food_items"],
            calories=analysis["total_calories"],
            proteins=analysis["total_protein"],
            fats=analysis["total_fat"],
            carbs=analysis["total_carb"],
            image_file_id=image_file_id,
            raw_text=raw_text,
            meal_type=meal_type
        )
        
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n_locales.get_text("food_logged", user_language))
    await state.clear()

@router.callback_query(FoodLoggingState.waiting_for_confirm, F.data == "food:cancel")
async def cancel_food_log(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n_locales.get_text("food_cancelled", user_language))
    await state.clear()

@router.callback_query(FoodLoggingState.waiting_for_confirm, F.data == "food:correct")
async def start_correction_mode(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    await state.set_state(FoodLoggingState.waiting_for_correction)
    await callback.message.answer(
        i18n_locales.get_text("food_correction_prompt", user_language),
        parse_mode="Markdown"
    )

@router.message(FoodLoggingState.waiting_for_correction)
async def process_food_correction(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    original_analysis = state_data["analysis"]
    correction_text = message.text.strip()
    
    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    
    # Request adjustment from Gemini
    adjusted_analysis = await gemini.adjust_food_analysis(
        original_data=original_analysis,
        correction_text=correction_text,
        language=user_language
    )
    
    await wait_msg.delete()
    
    if not adjusted_analysis:
        await message.answer("⚠️ Correction failed. Try describing the correction again.")
        return
        
    # Update state context with the adjusted data
    analysis_dict = adjusted_analysis.model_dump()
    await state.update_data(analysis=analysis_dict)
    
    # Format and present again
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
        reply_markup=inline.get_food_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

# --- Daily/Historical Meals Management ---

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_my_meals"],
    i18n_locales.LOCALES["ru"]["btn_my_meals"]
]))
@router.message(F.text == "/meals")
async def start_meals_list(message: Message, user_language: str, db_user):
    if not db_user:
        await message.answer(i18n_locales.get_text("profile_prompt_name", user_language))
        return
        
    try:
        user_tz = ZoneInfo(db_user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    local_now = datetime.now(user_tz)
    date_str = local_now.strftime("%Y-%m-%d")
    
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
        reply_markup = inline.get_meals_keyboard([], date_str, show_next, user_language)
    else:
        body_items = []
        for idx, meal in enumerate(meals):
            num = idx + 1
            meal_local_time = meal.logged_at.replace(tzinfo=UTC).astimezone(user_tz).strftime("%H:%M")
            items_desc = ", ".join([f"{item.get('name')} ({item.get('portion')})" for item in meal.items_json])
            
            # Map meal_type to localized string and emoji
            meal_type_emoji = (
                "🍳" if meal.meal_type == "breakfast" else
                "🍲" if meal.meal_type == "lunch" else
                "🍝" if meal.meal_type == "dinner" else
                "🍎" if meal.meal_type == "snack" else "🍽️"
            )
            type_names = {
                "breakfast": "Breakfast" if user_language == "en" else "Завтрак",
                "lunch": "Lunch" if user_language == "en" else "Обед",
                "dinner": "Dinner" if user_language == "en" else "Ужин",
                "snack": "Snack" if user_language == "en" else "Перекус",
                "food": "Food" if user_language == "en" else "Еда"
            }
            meal_type_label = type_names.get(meal.meal_type, "Food")
            
            body_items.append(
                f"{num}. *{meal_local_time}* - {meal_type_emoji} *{meal_type_label}*: {items_desc}\n"
                f"   _Totals: {meal.calories} kcal | P: {meal.proteins:.1f}g, F: {meal.fats:.1f}g, C: {meal.carbs:.1f}g_"
            )
        body = "\n".join(body_items) + "\n\n" + i18n_locales.get_text("meals_today_select", user_language)
        reply_markup = inline.get_meals_keyboard(meals, date_str, show_next, user_language)
        
    text = header + "\n" + body
    
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception:
            pass
    else:
        await event.answer(text, reply_markup=reply_markup, parse_mode="Markdown")

@router.callback_query(F.data.startswith("meal:list:"))
async def process_meals_list_callback(callback: CallbackQuery, user_language: str, db_user):
    await callback.answer()
    if not db_user:
        return
    date_str = callback.data.split(":")[2]
    await send_or_edit_meals_message(callback, date_str, user_language, db_user)

@router.callback_query(F.data.startswith("meal:delete:"))
async def process_meal_delete(callback: CallbackQuery, user_language: str, db_user):
    parts = callback.data.split(":")
    meal_id = int(parts[2])
    date_str = parts[3]
    
    async with AsyncSessionLocal() as db:
        success = await crud.delete_food_log(db, meal_id, callback.from_user.id)
        
    if success:
        await callback.answer(i18n_locales.get_text("meal_deleted", user_language))
    else:
        await callback.answer("⚠️ Error: Meal not found.")
        
    await send_or_edit_meals_message(callback, date_str, user_language, db_user)

@router.callback_query(F.data.startswith("meal:edit:"))
async def start_meal_edit(callback: CallbackQuery, state: FSMContext, user_language: str, db_user):
    await callback.answer()
    parts = callback.data.split(":")
    meal_id = int(parts[2])
    date_str = parts[3]
    
    async with AsyncSessionLocal() as db:
        meal = await crud.get_food_log_by_id(db, meal_id, callback.from_user.id)
        
    if not meal:
        await callback.message.answer("⚠️ Error: Meal not found.")
        return
        
    try:
        user_tz = ZoneInfo(db_user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    meal_time_str = meal.logged_at.replace(tzinfo=UTC).astimezone(user_tz).strftime("%H:%M")
    
    original_data = {
        "food_items": meal.items_json,
        "total_calories": meal.calories,
        "total_protein": meal.proteins,
        "total_fat": meal.fats,
        "total_carb": meal.carbs
    }
    
    await state.set_state(MealEditingState.waiting_for_edit_text)
    await state.update_data(
        edit_meal_id=meal_id,
        edit_date_str=date_str,
        original_data=original_data
    )
    
    items_list_str = ""
    for idx, item in enumerate(meal.items_json):
        items_list_str += f"- {item.get('name')} ({item.get('portion')}): {item.get('calories')} kcal\n"
        
    prompt_msg = i18n_locales.get_text(
        "edit_meal_prompt",
        user_language,
        time=meal_time_str,
        items=items_list_str
    )
    
    await callback.message.answer(prompt_msg, parse_mode="Markdown")

@router.message(MealEditingState.waiting_for_edit_text)
async def process_meal_edit_text(message: Message, state: FSMContext, user_language: str):
    state_data = await state.get_data()
    original_data = state_data["original_data"]
    correction_text = message.text.strip()
    
    wait_msg = await message.answer(i18n_locales.get_text("food_analyzing", user_language))
    
    adjusted_analysis = await gemini.adjust_food_analysis(
        original_data=original_data,
        correction_text=correction_text,
        language=user_language
    )
    
    await wait_msg.delete()
    
    if not adjusted_analysis:
        await message.answer("⚠️ Correction failed. Try describing the correction again.")
        return
        
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
        reply_markup=inline.get_meal_edit_confirm_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.callback_query(MealEditingState.waiting_for_edit_confirm, F.data == "mealedit:accept")
async def accept_meal_edit(callback: CallbackQuery, state: FSMContext, user_language: str, db_user):
    await callback.answer()
    state_data = await state.get_data()
    meal_id = state_data["edit_meal_id"]
    date_str = state_data["edit_date_str"]
    adjusted = state_data["adjusted_analysis"]
    
    async with AsyncSessionLocal() as db:
        await crud.update_food_log(
            db,
            log_id=meal_id,
            user_id=callback.from_user.id,
            items_json=adjusted["food_items"],
            calories=adjusted["total_calories"],
            proteins=adjusted["total_protein"],
            fats=adjusted["total_fat"],
            carbs=adjusted["total_carb"]
        )
        
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n_locales.get_text("edit_meal_success", user_language))
    await state.clear()
    
    await send_or_edit_meals_message(callback, date_str, user_language, db_user)

@router.callback_query(MealEditingState.waiting_for_edit_confirm, F.data == "mealedit:cancel")
async def cancel_meal_edit(callback: CallbackQuery, state: FSMContext, user_language: str, db_user):
    await callback.answer()
    state_data = await state.get_data()
    date_str = state_data["edit_date_str"]
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(i18n_locales.get_text("food_cancelled", user_language))
    await state.clear()
    
    await send_or_edit_meals_message(callback, date_str, user_language, db_user)

@router.callback_query(MealEditingState.waiting_for_edit_confirm, F.data == "mealedit:correct")
async def correct_meal_edit(callback: CallbackQuery, state: FSMContext, user_language: str):
    await callback.answer()
    await state.set_state(MealEditingState.waiting_for_edit_text)
    await callback.message.answer(
        i18n_locales.get_text("food_correction_prompt", user_language),
        parse_mode="Markdown"
    )


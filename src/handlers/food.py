import io
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
    waiting_for_input = State()
    waiting_for_confirm = State()
    waiting_for_correction = State()

@router.message(F.text.in_([
    i18n_locales.LOCALES["en"]["btn_log_food"],
    i18n_locales.LOCALES["ru"]["btn_log_food"]
]))
async def start_food_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(FoodLoggingState.waiting_for_input)
    await message.answer(
        i18n_locales.get_text("food_prompt", user_language),
        parse_mode="Markdown"
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
            raw_text=raw_text
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

from datetime import datetime
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.database.models import WeightLog
from src.utils import i18n_locales

router = Router()

class WeightState(StatesGroup):
    waiting_for_weight = State()

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_log_weight")))
async def start_weight_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(WeightState.waiting_for_weight)
    await message.answer(
        i18n_locales.get_text("weight_prompt", user_language),
        parse_mode="Markdown"
    )

@router.message(WeightState.waiting_for_weight)
async def process_weight_input(message: Message, state: FSMContext, user_language: str):
    try:
        weight = float(message.text.strip().replace(",", "."))
        if weight <= 20 or weight > 500:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_weight", user_language))
        return
        
    user_id = message.from_user.id
    user_goal = None
    
    async with AsyncSessionLocal() as db:
        # Update user's current weight and recalculate daily target macros
        user = await crud.get_user(db, user_id)
        if user:
            user_goal = user.goal
            user.weight_kg = weight
            from src.utils import formulas
            targets = formulas.calculate_targets(
                weight_kg=weight,
                height_cm=user.height_cm,
                age=user.age,
                sex=user.sex,
                activity_level=user.activity_level,
                goal=user.goal
            )
            user.target_calories = targets["calories"]
            user.target_protein = targets["protein"]
            user.target_fat = targets["fat"]
            user.target_carb = targets["carb"]

        # Find the earliest logged weight as the baseline
        result = await db.execute(
            select(WeightLog)
            .where(WeightLog.user_id == user_id)
            .order_by(WeightLog.logged_at.asc())
            .limit(1)
        )
        baseline_log = result.scalars().first()
        
        # Save the new weight log
        await crud.add_weight_log(db, user_id=user_id, weight=weight)
        
    # Calculate difference
    feedback_key = "weight_feedback_positive"
    if baseline_log:
        baseline_weight = baseline_log.weight
        diff = weight - baseline_weight
        if diff > 0.05:
            diff_str = i18n_locales.get_text(
                "weight_diff_gain",
                user_language,
                diff=diff,
                baseline=baseline_weight
            )
        elif diff < -0.05:
            diff_str = i18n_locales.get_text(
                "weight_diff_loss",
                user_language,
                diff=abs(diff),
                baseline=baseline_weight
            )
        else:
            diff_str = i18n_locales.get_text(
                "weight_diff_same",
                user_language,
                baseline=baseline_weight
            )
            
        # Determine feedback key based on goal and dynamic
        if user_goal == "lose_weight":
            if diff < -0.05:
                feedback_key = "weight_feedback_positive"
            else:
                feedback_key = "weight_feedback_warn"
        elif user_goal in ("gain_weight", "gain_muscle"):
            if diff > 0.05:
                feedback_key = "weight_feedback_positive"
            else:
                feedback_key = "weight_feedback_warn"
    else:
        diff_str = ""
        
    feedback_msg = i18n_locales.get_text(feedback_key, user_language)
    
    response_msg = i18n_locales.get_text(
        "weight_logged",
        user_language,
        weight=weight,
        weight_diff_str=diff_str,
        feedback_msg=feedback_msg
    )
    
    await state.clear()
    await message.answer(response_msg, parse_mode="Markdown")

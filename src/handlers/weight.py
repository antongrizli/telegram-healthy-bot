from datetime import datetime
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.utils import i18n_locales
from src.services import gamification
from src.keyboards import reply

router = Router()

class WeightState(StatesGroup):
    waiting_for_weight = State()

@router.message(F.text.in_(i18n_locales.get_all_translations("btn_log_weight")))
async def start_weight_logging(message: Message, state: FSMContext, user_language: str):
    await state.set_state(WeightState.waiting_for_weight)
    await message.answer(
        i18n_locales.get_text("weight_prompt", user_language),
        reply_markup=reply.get_cancel_keyboard(user_language),
        parse_mode="Markdown"
    )

@router.message(WeightState.waiting_for_weight)
async def process_weight_input(message: Message, state: FSMContext, user_language: str):
    try:
        from src.services.ux import numeric
        weight = numeric(message.text, 20.01, 500)
        if weight <= 20 or weight > 500:
            raise ValueError()
    except ValueError:
        await message.answer(i18n_locales.get_text("invalid_weight", user_language))
        return
        
    user_id = message.from_user.id
    user_goal = None
    
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        user_goal = user.goal if user else None
        baseline_log = await crud.save_weight_entry(db, user_id, weight)

        # Process gamification
        ach_notifs = []
        if user:
            await gamification.process_weight_log_streak(db, user)
            new_ach_keys = await gamification.check_new_achievements(db, user_id)
            
            for ach_key in new_ach_keys:
                ach_def = gamification.ACHIEVEMENTS.get(ach_key)
                if ach_def:
                    icon = ach_def["icon"]
                    name = i18n_locales.get_text(ach_def["name_key"], user_language)
                    desc = i18n_locales.get_text(ach_def["desc_key"], user_language)
                    ach_notifs.append(f"{icon} *{name}* — {desc}")
        
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
    
    if ach_notifs:
        response_msg += f"\n\n🏆 *{i18n_locales.get_text('achievements_unlocked_title', user_language)}*\n" + "\n".join(ach_notifs)
    
    await state.clear()
    from src.keyboards.reply import get_main_menu
    from src.config import settings
    await message.answer(response_msg, parse_mode="Markdown", reply_markup=get_main_menu(user_language, bool(user and (user.is_admin or user.telegram_id in settings.ADMIN_USER_IDS))))

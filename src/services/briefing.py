import logging
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import crud
from src.services import gemini
from src.utils import i18n_locales

logger = logging.getLogger(__name__)

async def generate_morning_briefing(db: AsyncSession, user_id: int) -> str:
    """
    Generates a personalized, empathetic morning briefing for the user based on yesterday's statistics.
    """
    user = await crud.get_user(db, user_id)
    if not user:
        raise ValueError("User not found")
        
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    local_now = datetime.now(user_tz)
    local_yesterday = local_now - timedelta(days=1)
    
    # Calculate start and end of yesterday in UTC
    start_yesterday_local = datetime(local_yesterday.year, local_yesterday.month, local_yesterday.day, tzinfo=user_tz)
    end_yesterday_local = start_yesterday_local + timedelta(days=1) - timedelta(seconds=1)
    
    start_utc = start_yesterday_local.astimezone(UTC).replace(tzinfo=None)
    end_utc = end_yesterday_local.astimezone(UTC).replace(tzinfo=None)
    
    # Fetch food & weight logs from yesterday
    food_logs = await crud.get_food_logs(db, user_id, start_utc, end_utc)
    weight_logs = await crud.get_weight_logs(db, user_id, start_utc, end_utc)
    latest_weight = await crud.get_latest_weight_log(db, user_id)
    
    # Get user streaks
    streaks = await crud.get_user_streaks(db, user_id)
    streak_info = ""
    for s in streaks:
        if s.streak_type == "food_logging" and s.current_count > 0:
            streak_info += f"- Daily logging streak: {s.current_count} days 🔥\n"
        elif s.streak_type == "calorie_target_hit" and s.current_count > 0:
            streak_info += f"- Calorie target streak: {s.current_count} days 🎯\n"
            
    # Format food logs
    food_text = ""
    total_cal = 0
    total_prot = 0
    total_fat = 0
    total_carb = 0
    
    for log in food_logs:
        items_str = ", ".join([f"{i.get('name')} ({i.get('portion')})" for i in log.items_json])
        food_text += f"- {items_str} | Cal: {log.calories} kcal, P: {log.proteins}g, F: {log.fats}g, C: {log.carbs}g\n"
        total_cal += log.calories
        total_prot += log.proteins
        total_fat += log.fats
        total_carb += log.carbs
        
    if not food_text:
        food_text = "No meals logged yesterday.\n"
        
    # Format weight
    weight_text = f"Baseline weight: {user.weight_kg} kg. "
    if latest_weight:
        weight_text += f"Latest weight: {latest_weight.weight} kg logged at {latest_weight.logged_at.strftime('%Y-%m-%d')}."
        
    # Format targets
    targets_text = (
        f"Calorie Target: {user.target_calories} kcal, "
        f"Protein: {user.target_protein}g, Fat: {user.target_fat}g, Carb: {user.target_carb}g"
    )
    
    lang_names = {
        "en": "English", "ru": "Russian", "uk": "Ukrainian", "pl": "Polish", 
        "de": "German", "tr": "Turkish", "es": "Spanish"
    }
    lang_name = lang_names.get(user.language, "English")
    
    prompt = (
        f"You are a warm, supportive digital mental health and fitness coach speaking directly to {user.name}.\n"
        f"Generate a personalized morning briefing for yesterday, {local_yesterday.strftime('%A, %B %d')}.\n\n"
        f"--- USER CONTEXT ---\n"
        f"Goal: {user.goal}\n"
        f"Yesterday's Intake: Calories: {total_cal} kcal, Protein: {total_prot}g, Fat: {total_fat}g, Carb: {total_carb}g\n"
        f"User Targets: {targets_text}\n"
        f"Yesterday's Food Logs:\n{food_text}"
        f"Weight: {weight_text}\n"
        f"Streaks:\n{streak_info or 'No active streaks.'}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Start with a warm, encouraging, customized morning greeting (under 1 sentence).\n"
        f"2. Summarize yesterday: celebrate successes (e.g. hitting protein target, logging all meals, maintaining a streak) or gently validate if targets were missed without any judgment, in an empathetic, supportive tone.\n"
        f"3. Provide exactly ONE specific, helpful, actionable tip for today (e.g., about protein intake, hydration, food logging, meal balance, or activity) customized to their goal.\n"
        f"4. Keep the entire response under 800 characters, formatted for Telegram with Markdown (use bolding and bullet points carefully).\n"
        f"5. Start directly with the greeting. Do not add intro/outro tags.\n"
        f"Language: {lang_name}"
    )
    
    try:
        response = await gemini.call_gemini_with_retry(
            contents=[prompt],
            config=gemini.types.GenerateContentConfig(temperature=0.3)
        )
        briefing = response.text
        if briefing:
            from src.utils.escape import clean_telegram_markdown
            briefing = clean_telegram_markdown(briefing)
        return briefing or f"Good morning, {user.name}! Let's focus on hitting your goals today."
    except Exception as e:
        logger.error(f"Error generating morning briefing: {e}")
        return f"Good morning, {user.name}! Yesterday you logged {total_cal} kcal. Keep up the good work and let's make today count!"

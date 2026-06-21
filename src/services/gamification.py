import logging
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from src.database import crud
from src.database.models import FoodLog, WeightLog, Streak, Achievement, HealthCard, User
from src.services import gemini
from src.utils import i18n_locales

logger = logging.getLogger(__name__)

# List of all defined achievements
ACHIEVEMENTS = {
    "first_meal": {"icon": "🍽️", "name_key": "ach_first_meal_name", "desc_key": "ach_first_meal_desc"},
    "streak_3": {"icon": "🔥", "name_key": "ach_streak_3_name", "desc_key": "ach_streak_3_desc"},
    "streak_7": {"icon": "💪", "name_key": "ach_streak_7_name", "desc_key": "ach_streak_7_desc"},
    "streak_30": {"icon": "🏆", "name_key": "ach_streak_30_name", "desc_key": "ach_streak_30_desc"},
    "first_weight": {"icon": "⚖️", "name_key": "ach_first_weight_name", "desc_key": "ach_first_weight_desc"},
    "weight_5": {"icon": "📊", "name_key": "ach_weight_5_name", "desc_key": "ach_weight_5_desc"},
    "target_hit_3": {"icon": "🎯", "name_key": "ach_target_hit_3_name", "desc_key": "ach_target_hit_3_desc"},
    "target_hit_7": {"icon": "✨", "name_key": "ach_target_hit_7_name", "desc_key": "ach_target_hit_7_desc"},
    "protein_king": {"icon": "🥩", "name_key": "ach_protein_king_name", "desc_key": "ach_protein_king_desc"},
    "first_card": {"icon": "🃏", "name_key": "ach_first_card_name", "desc_key": "ach_first_card_desc"},
    "night_owl": {"icon": "🦉", "name_key": "ach_night_owl_name", "desc_key": "ach_night_owl_desc"},
    "cheat_day": {"icon": "🍕", "name_key": "ach_cheat_day_name", "desc_key": "ach_cheat_day_desc"},
    "rabbit": {"icon": "🥗", "name_key": "ach_rabbit_name", "desc_key": "ach_rabbit_desc"},
    "hydration": {"icon": "💧", "name_key": "ach_hydration_name", "desc_key": "ach_hydration_desc"},
    "early_bird": {"icon": "🌅", "name_key": "ach_early_bird_name", "desc_key": "ach_early_bird_desc"},
}

async def process_food_log_streak(db: AsyncSession, user: User) -> tuple[int, bool, int]:
    """
    Updates the food logging streak for the user.
    Returns a tuple of (current_streak, freeze_used, freezes_left).
    """
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")

    local_now = datetime.now(user_tz)
    local_today = datetime(local_now.year, local_now.month, local_now.day)
    
    streak = await crud.get_or_create_streak(db, user.telegram_id, "food_logging")
    freeze_used = False
    
    if not streak.last_logged_date:
        streak.current_count = 1
        streak.longest_count = 1
        streak.started_at = local_today
    else:
        last_logged = streak.last_logged_date
        diff = (local_today.date() - last_logged.date()).days
        
        if diff == 0:
            # Already logged today, streak is unaffected
            pass
        elif diff == 1:
            # Consecutive day
            streak.current_count += 1
        else:
            # Missed days. Let's see if we can use a streak freeze!
            # A freeze can rescue the streak if the gap is exactly 2 days (i.e. missed yesterday, logging today).
            # If the gap is longer, the streak is broken regardless.
            if diff == 2 and user.streak_freezes_left > 0:
                user.streak_freezes_left -= 1
                user.last_freeze_used_at = datetime.now(UTC).replace(tzinfo=None)
                streak.current_count += 1  # Streak preserved and incremented for today's log
                freeze_used = True
            else:
                streak.current_count = 1
                streak.started_at = local_today

    streak.last_logged_date = local_today
    streak.updated_at = datetime.now(UTC).replace(tzinfo=None)
    
    if streak.current_count > streak.longest_count:
        streak.longest_count = streak.current_count
        
    user.current_streak = streak.current_count
    await db.commit()
    return streak.current_count, freeze_used, user.streak_freezes_left

async def process_weight_log_streak(db: AsyncSession, user: User) -> int:
    """
    Updates the weight logging streak.
    We track weight logging separately to check achievements, but it does not affect main user.current_streak.
    """
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")

    local_now = datetime.now(user_tz)
    local_today = datetime(local_now.year, local_now.month, local_now.day)
    
    streak = await crud.get_or_create_streak(db, user.telegram_id, "weight_logging")
    
    if not streak.last_logged_date:
        streak.current_count = 1
        streak.longest_count = 1
        streak.started_at = local_today
    else:
        last_logged = streak.last_logged_date
        diff = (local_today.date() - last_logged.date()).days
        
        if diff == 0:
            pass
        elif diff <= 7:  # Weight is weekly, so a streak is maintained if logged within 7 days
            streak.current_count += 1
        else:
            streak.current_count = 1
            streak.started_at = local_today

    streak.last_logged_date = local_today
    streak.updated_at = datetime.now(UTC).replace(tzinfo=None)
    
    if streak.current_count > streak.longest_count:
        streak.longest_count = streak.current_count
        
    await db.commit()
    return streak.current_count

async def update_daily_targets_streak(db: AsyncSession, user: User) -> tuple[bool, bool]:
    """
    Checks if today's calorie and protein goals were met, updating their respective streaks.
    Returns (calorie_hit, protein_hit).
    """
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")

    local_now = datetime.now(user_tz)
    start_of_day_local = datetime(local_now.year, local_now.month, local_now.day, tzinfo=user_tz)
    start_date = start_of_day_local.astimezone(UTC).replace(tzinfo=None)
    end_date = local_now.astimezone(UTC).replace(tzinfo=None)
    
    # Calculate today's intake
    logs = await crud.get_food_logs(db, user.telegram_id, start_date, end_date)
    total_cal = sum(log.calories for log in logs)
    total_prot = sum(log.proteins for log in logs)
    
    # 1. Calorie Target Check (within 15% range)
    cal_hit = 0.85 * user.target_calories <= total_cal <= 1.15 * user.target_calories
    # 2. Protein Target Check (at least 90% of target)
    prot_hit = total_prot >= 0.9 * user.target_protein
    
    local_today = datetime(local_now.year, local_now.month, local_now.day)
    
    # Update Calorie Streak
    cal_streak = await crud.get_or_create_streak(db, user.telegram_id, "calorie_target_hit")
    if cal_hit:
        if cal_streak.last_logged_date and (local_today.date() - cal_streak.last_logged_date.date()).days == 1:
            cal_streak.current_count += 1
        elif not cal_streak.last_logged_date or (local_today.date() - cal_streak.last_logged_date.date()).days > 1:
            cal_streak.current_count = 1
            cal_streak.started_at = local_today
        cal_streak.last_logged_date = local_today
        if cal_streak.current_count > cal_streak.longest_count:
            cal_streak.longest_count = cal_streak.current_count
    else:
        # If missed and last logged date was yesterday, break it (or let freeze check, but freezes are for logging streak only)
        if cal_streak.last_logged_date and (local_today.date() - cal_streak.last_logged_date.date()).days == 1:
            cal_streak.current_count = 0
            
    # Update Protein Streak
    prot_streak = await crud.get_or_create_streak(db, user.telegram_id, "protein_target_hit")
    if prot_hit:
        if prot_streak.last_logged_date and (local_today.date() - prot_streak.last_logged_date.date()).days == 1:
            prot_streak.current_count += 1
        elif not prot_streak.last_logged_date or (local_today.date() - prot_streak.last_logged_date.date()).days > 1:
            prot_streak.current_count = 1
            prot_streak.started_at = local_today
        prot_streak.last_logged_date = local_today
        if prot_streak.current_count > prot_streak.longest_count:
            prot_streak.longest_count = prot_streak.current_count
    else:
        if prot_streak.last_logged_date and (local_today.date() - prot_streak.last_logged_date.date()).days == 1:
            prot_streak.current_count = 0

    cal_streak.updated_at = datetime.now(UTC).replace(tzinfo=None)
    prot_streak.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    return cal_hit, prot_hit

async def check_new_achievements(db: AsyncSession, user_id: int) -> list[str]:
    """
    Checks if user meets requirements for any locked achievements.
    Unlocks them and returns a list of newly unlocked achievement keys.
    """
    unlocked = await crud.get_user_achievements(db, user_id)
    unlocked_keys = {a.achievement_key for a in unlocked}
    
    newly_unlocked = []
    
    # Get user details & logs
    user = await crud.get_user(db, user_id)
    if not user:
        return []
        
    # Helper counts
    # Total food logs count
    food_count_res = await db.execute(
        select(func.count(FoodLog.id)).where(FoodLog.user_id == user_id)
    )
    food_count = food_count_res.scalar() or 0
    
    # Total weight logs count
    weight_count_res = await db.execute(
        select(func.count(WeightLog.id)).where(WeightLog.user_id == user_id)
    )
    weight_count = weight_count_res.scalar() or 0
    
    # Fetch all food logs of the user for advanced logic checks
    food_logs_res = await db.execute(
        select(FoodLog).where(FoodLog.user_id == user_id)
    )
    user_food_logs = food_logs_res.scalars().all()
    
    # Streaks
    food_streak = await crud.get_or_create_streak(db, user_id, "food_logging")
    cal_streak = await crud.get_or_create_streak(db, user_id, "calorie_target_hit")
    prot_streak = await crud.get_or_create_streak(db, user_id, "protein_target_hit")
    
    # Check conditions
    # 1. First Meal
    if "first_meal" not in unlocked_keys and food_count >= 1:
        if await crud.unlock_achievement(db, user_id, "first_meal"):
            newly_unlocked.append("first_meal")
            
    # 2. Streaks (food)
    if "streak_3" not in unlocked_keys and food_streak.current_count >= 3:
        if await crud.unlock_achievement(db, user_id, "streak_3"):
            newly_unlocked.append("streak_3")
            
    if "streak_7" not in unlocked_keys and food_streak.current_count >= 7:
        if await crud.unlock_achievement(db, user_id, "streak_7"):
            newly_unlocked.append("streak_7")
            
    if "streak_30" not in unlocked_keys and food_streak.current_count >= 30:
        if await crud.unlock_achievement(db, user_id, "streak_30"):
            newly_unlocked.append("streak_30")
            
    # 3. Weight
    if "first_weight" not in unlocked_keys and weight_count >= 1:
        if await crud.unlock_achievement(db, user_id, "first_weight"):
            newly_unlocked.append("first_weight")
            
    if "weight_5" not in unlocked_keys and weight_count >= 5:
        if await crud.unlock_achievement(db, user_id, "weight_5"):
            newly_unlocked.append("weight_5")
            
    # 4. Target hits
    if "target_hit_3" not in unlocked_keys and cal_streak.current_count >= 3:
        if await crud.unlock_achievement(db, user_id, "target_hit_3"):
            newly_unlocked.append("target_hit_3")
            
    if "target_hit_7" not in unlocked_keys and cal_streak.current_count >= 7:
        if await crud.unlock_achievement(db, user_id, "target_hit_7"):
            newly_unlocked.append("target_hit_7")
            
    # 5. Protein
    if "protein_king" not in unlocked_keys and prot_streak.current_count >= 5:
        if await crud.unlock_achievement(db, user_id, "protein_king"):
            newly_unlocked.append("protein_king")
            
    # 6. Night Owl
    if "night_owl" not in unlocked_keys:
        has_night_log = False
        for log in user_food_logs:
            try:
                user_tz = ZoneInfo(user.timezone or "UTC")
            except Exception:
                user_tz = ZoneInfo("UTC")
            local_time = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz)
            if local_time.hour >= 23 or local_time.hour < 4:
                has_night_log = True
                break
        if has_night_log:
            if await crud.unlock_achievement(db, user_id, "night_owl"):
                newly_unlocked.append("night_owl")

    # 7. Cheat Day
    if "cheat_day" not in unlocked_keys and user.target_calories > 0:
        daily_calories = {}
        for log in user_food_logs:
            try:
                user_tz = ZoneInfo(user.timezone or "UTC")
            except Exception:
                user_tz = ZoneInfo("UTC")
            local_date = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz).date()
            daily_calories[local_date] = daily_calories.get(local_date, 0) + log.calories
        
        has_cheat_day = False
        for cal in daily_calories.values():
            if cal >= 1.5 * user.target_calories:
                has_cheat_day = True
                break
        if has_cheat_day:
            if await crud.unlock_achievement(db, user_id, "cheat_day"):
                newly_unlocked.append("cheat_day")

    # 8. Green Machine (Rabbit)
    if "rabbit" not in unlocked_keys:
        has_rabbit_log = False
        green_keywords = ["salad", "vegetable", "lettuce", "broccoli", "spinach", "cucumber", "greens", "cabbage",
                          "салат", "овощ", "зелень", "брокколи", "шпинат", "огурец", "капуста", "петрушка", "укроп"]
        for log in user_food_logs:
            desc = (log.raw_text or "").lower()
            if any(kw in desc for kw in green_keywords):
                has_rabbit_log = True
                break
        if has_rabbit_log:
            if await crud.unlock_achievement(db, user_id, "rabbit"):
                newly_unlocked.append("rabbit")

    # 9. Hydration Hero
    if "hydration" not in unlocked_keys:
        has_hydration_log = False
        water_keywords = ["water", "вода", "минералка", "минеральная вода"]
        for log in user_food_logs:
            desc = (log.raw_text or "").lower()
            if any(kw in desc for kw in water_keywords):
                has_hydration_log = True
                break
        if has_hydration_log:
            if await crud.unlock_achievement(db, user_id, "hydration"):
                newly_unlocked.append("hydration")

    # 10. Early Bird
    if "early_bird" not in unlocked_keys:
        has_early_log = False
        for log in user_food_logs:
            try:
                user_tz = ZoneInfo(user.timezone or "UTC")
            except Exception:
                user_tz = ZoneInfo("UTC")
            local_time = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz)
            if local_time.hour < 7:
                has_early_log = True
                break
        if has_early_log:
            if await crud.unlock_achievement(db, user_id, "early_bird"):
                newly_unlocked.append("early_bird")

    # 11. Health Card (checked on health card generation)
    # We will trigger the unlock check inside generate_weekly_health_card
    
    return newly_unlocked

async def generate_weekly_health_card(db: AsyncSession, user_id: int) -> HealthCard:
    """
    Generates the weekly Personalized Health Card.
    Calculates consistency, nutrition, and weight progress scores, unlocks card achievement,
    invokes Gemini to generate an empathetic coach note, and saves to DB.
    """
    user = await crud.get_user(db, user_id)
    if not user:
        raise ValueError("User not found")
        
    try:
        user_tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        user_tz = ZoneInfo("UTC")
        
    local_now = datetime.now(user_tz)
    # Start of current week (Monday)
    days_since_monday = local_now.weekday()
    week_start_local = local_now - timedelta(days=days_since_monday)
    week_start_midnight = datetime(week_start_local.year, week_start_local.month, week_start_local.day, tzinfo=user_tz)
    week_start_utc = week_start_midnight.astimezone(UTC).replace(tzinfo=None)
    
    # Calculate dates for logs
    start_date = (week_start_midnight - timedelta(days=7)).astimezone(UTC).replace(tzinfo=None)
    end_date = week_start_midnight.astimezone(UTC).replace(tzinfo=None)
    
    # 1. Nutrition Score
    food_logs = await crud.get_food_logs(db, user_id, start_date, end_date)
    # Calculate average daily calories, protein, carbs, fat
    days_logged = len(set(log.logged_at.date() for log in food_logs))
    
    total_cal = sum(log.calories for log in food_logs)
    total_prot = sum(log.proteins for log in food_logs)
    avg_cal = total_cal / 7.0
    avg_prot = total_prot / 7.0
    
    # Score out of 100 based on macro goal alignment
    cal_deviation = abs(avg_cal - user.target_calories) / user.target_calories if user.target_calories else 1.0
    nutrition_score = max(0, int(100 * (1.0 - cal_deviation)))
    
    # 2. Consistency Score
    # Number of days they logged something out of 7
    consistency_score = int((days_logged / 7.0) * 100)
    
    # 3. Weight Progress Score
    weight_logs = await crud.get_weight_logs(db, user_id, start_date, end_date)
    # Check weight trend vs goal
    weight_score = 100 # default if no logs
    weight_trend = "stable"
    
    if len(weight_logs) >= 2:
        sorted_weights = sorted(weight_logs, key=lambda w: w.logged_at)
        w_diff = sorted_weights[-1].weight - sorted_weights[0].weight
        
        if abs(w_diff) < 0.1:
            weight_trend = "stable"
            weight_score = 100 if user.goal == "maintain" else 70
        elif w_diff > 0:
            weight_trend = "up"
            weight_score = 100 if user.goal in ("gain_weight", "gain_muscle") else 50
        else:
            weight_trend = "down"
            weight_score = 100 if user.goal == "lose_weight" else 50
    elif len(weight_logs) == 1:
        weight_score = 80
        
    overall_score = int(0.4 * nutrition_score + 0.4 * consistency_score + 0.2 * weight_score)
    
    # Calculate trends relative to target or historic averages
    card_data = {
        "overall_score": overall_score,
        "categories": {
            "nutrition": {
                "score": nutrition_score,
                "trend": "up" if avg_prot >= user.target_protein * 0.9 else "down",
                "detail": f"Average daily calories: {int(avg_cal)} / {user.target_calories} kcal."
            },
            "consistency": {
                "score": consistency_score,
                "trend": "stable" if days_logged >= 4 else "down",
                "detail": f"Logged meals on {days_logged} out of 7 days."
            },
            "weight_progress": {
                "score": weight_score,
                "trend": weight_trend,
                "detail": f"Logged {len(weight_logs)} weight readings this week."
            }
        },
        "achievements_this_week": []
    }
    
    # Unlock first_card achievement if not unlocked
    unlocked_ach = await check_new_achievements(db, user_id)
    if "first_card" not in [a.achievement_key for a in await crud.get_user_achievements(db, user_id)]:
        if await crud.unlock_achievement(db, user_id, "first_card"):
            card_data["achievements_this_week"].append("first_card")
            
    # Generate Coach Message via Gemini
    profile_dict = {
        "name": user.name,
        "sex": user.sex,
        "age": user.age,
        "height_cm": user.height_cm,
        "weight_kg": user.weight_kg,
        "goal": user.goal,
    }
    
    coach_message = await gemini_generate_card_note(profile_dict, card_data, user.language)
    card_data["coach_message"] = coach_message
    
    card = await crud.save_health_card(db, user_id, week_start_utc, card_data)
    return card

async def gemini_generate_card_note(profile: dict, card_data: dict, language: str) -> str:
    """
    Calls Gemini to generate a supportive weekly health review note.
    """
    prompt = (
        f"You are a professional empathetic health coach. Generate a weekly progress card review for a user.\n"
        f"User Profile: {profile}\n"
        f"Weekly Performance: {card_data}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Acknowledge their scores (Overall: {card_data['overall_score']}/100, Nutrition: {card_data['categories']['nutrition']['score']}, Consistency: {card_data['categories']['consistency']['score']}, Weight Progress: {card_data['categories']['weight_progress']['score']}).\n"
        f"2. Write in a warm, encouraging, supportive style (mental health coach persona).\n"
        f"3. Provide exactly two actionable recommendations for the upcoming week based on where they scored lowest.\n"
        f"4. Keep the message concise (max 700 characters) and ready for Telegram. Do not include titles or headings, start directly with the coach message.\n"
        f"Language: {i18n_locales.get_text('lang_' + language, language)}"
    )
    
    try:
        response = await gemini.call_gemini_with_retry(
            contents=[prompt],
            config=gemini.types.GenerateContentConfig(temperature=0.3)
        )
        note = response.text
        if note:
            from src.utils.escape import clean_telegram_markdown
            note = clean_telegram_markdown(note)
        return note or "Excellent work this week! Keep staying consistent, tracking your meals daily, and moving closer to your goals."
    except Exception as e:
        logger.error(f"Error generating weekly card note: {e}")
        return "Excellent work this week! Keep staying consistent, tracking your meals daily, and moving closer to your goals."

async def reset_weekly_freezes(db: AsyncSession):
    """
    Resets all users' streak_freezes_left to 1 at the beginning of the week.
    Called by a scheduled cron job.
    """
    users = await crud.get_all_users(db, include_blocked=False)
    for u in users:
        u.streak_freezes_left = 1
    await db.commit()
    logger.info(f"Streak freezes reset for {len(users)} users.")

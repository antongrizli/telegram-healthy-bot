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
    "first_step": {"icon": "👟", "name_key": "ach_first_step_name", "desc_key": "ach_first_step_desc"},
    "streak_3": {"icon": "🔥", "name_key": "ach_streak_3_name", "desc_key": "ach_streak_3_desc"},
    "streak_7": {"icon": "💪", "name_key": "ach_streak_7_name", "desc_key": "ach_streak_7_desc"},
    "streak_14": {"icon": "🗓️", "name_key": "ach_streak_14_name", "desc_key": "ach_streak_14_desc"},
    "streak_21": {"icon": "🔄", "name_key": "ach_streak_21_name", "desc_key": "ach_streak_21_desc"},
    "streak_30": {"icon": "🏆", "name_key": "ach_streak_30_name", "desc_key": "ach_streak_30_desc"},
    "streak_50": {"icon": "🚂", "name_key": "ach_streak_50_name", "desc_key": "ach_streak_50_desc"},
    "streak_100": {"icon": "💯", "name_key": "ach_streak_100_name", "desc_key": "ach_streak_100_desc"},
    "streak_180": {"icon": "🌓", "name_key": "ach_streak_180_name", "desc_key": "ach_streak_180_desc"},
    "streak_365": {"icon": "👑", "name_key": "ach_streak_365_name", "desc_key": "ach_streak_365_desc"},
    "first_meal": {"icon": "🍽️", "name_key": "ach_first_meal_name", "desc_key": "ach_first_meal_desc"},
    "log_10_meals": {"icon": "🥉", "name_key": "ach_log_10_name", "desc_key": "ach_log_10_desc"},
    "log_50_meals": {"icon": "🥈", "name_key": "ach_log_50_name", "desc_key": "ach_log_50_desc"},
    "log_100_meals": {"icon": "🥇", "name_key": "ach_log_100_name", "desc_key": "ach_log_100_desc"},
    "log_250_meals": {"icon": "🏅", "name_key": "ach_log_250_name", "desc_key": "ach_log_250_desc"},
    "log_500_meals": {"icon": "🌟", "name_key": "ach_log_500_name", "desc_key": "ach_log_500_desc"},
    "log_1000_meals": {"icon": "🌠", "name_key": "ach_log_1000_name", "desc_key": "ach_log_1000_desc"},
    "breakfast_lover": {"icon": "🥞", "name_key": "ach_breakfast_lover_name", "desc_key": "ach_breakfast_lover_desc"},
    "lunch_boss": {"icon": "🍱", "name_key": "ach_lunch_boss_name", "desc_key": "ach_lunch_boss_desc"},
    "dinner_king": {"icon": "🍝", "name_key": "ach_dinner_king_name", "desc_key": "ach_dinner_king_desc"},
    "first_weight": {"icon": "⚖️", "name_key": "ach_first_weight_name", "desc_key": "ach_first_weight_desc"},
    "weight_5": {"icon": "📊", "name_key": "ach_weight_5_name", "desc_key": "ach_weight_5_desc"},
    "weight_10": {"icon": "📉", "name_key": "ach_weight_10_name", "desc_key": "ach_weight_10_desc"},
    "weight_25": {"icon": "📈", "name_key": "ach_weight_25_name", "desc_key": "ach_weight_25_desc"},
    "weight_50": {"icon": "🔬", "name_key": "ach_weight_50_name", "desc_key": "ach_weight_50_desc"},
    "weight_100": {"icon": "🧭", "name_key": "ach_weight_100_name", "desc_key": "ach_weight_100_desc"},
    "weight_trend_down": {"icon": "🔽", "name_key": "ach_weight_down_name", "desc_key": "ach_weight_down_desc"},
    "weight_trend_up": {"icon": "🔼", "name_key": "ach_weight_up_name", "desc_key": "ach_weight_up_desc"},
    "target_hit_1": {"icon": "🎯", "name_key": "ach_target_hit_1_name", "desc_key": "ach_target_hit_1_desc"},
    "target_hit_3": {"icon": "🎳", "name_key": "ach_target_hit_3_name", "desc_key": "ach_target_hit_3_desc"},
    "target_hit_7": {"icon": "✨", "name_key": "ach_target_hit_7_name", "desc_key": "ach_target_hit_7_desc"},
    "target_hit_14": {"icon": "🌈", "name_key": "ach_target_hit_14_name", "desc_key": "ach_target_hit_14_desc"},
    "target_hit_30": {"icon": "💎", "name_key": "ach_target_hit_30_name", "desc_key": "ach_target_hit_30_desc"},
    "calorie_deficit_master": {"icon": "✂️", "name_key": "ach_deficit_master_name", "desc_key": "ach_deficit_master_desc"},
    "bulking_boss": {"icon": "🧱", "name_key": "ach_bulking_boss_name", "desc_key": "ach_bulking_boss_desc"},
    "protein_king": {"icon": "🥩", "name_key": "ach_protein_king_name", "desc_key": "ach_protein_king_desc"},
    "protein_streak_7": {"icon": "🍗", "name_key": "ach_protein_7_name", "desc_key": "ach_protein_7_desc"},
    "carb_balancer": {"icon": "🥖", "name_key": "ach_carb_balancer_name", "desc_key": "ach_carb_balancer_desc"},
    "fat_balancer": {"icon": "🥑", "name_key": "ach_fat_balancer_name", "desc_key": "ach_fat_balancer_desc"},
    "macro_wizard": {"icon": "🧙‍♂️", "name_key": "ach_macro_wizard_name", "desc_key": "ach_macro_wizard_desc"},
    "sweet_tooth": {"icon": "🍩", "name_key": "ach_sweet_tooth_name", "desc_key": "ach_sweet_tooth_desc"},
    "keto_hero": {"icon": "🥓", "name_key": "ach_keto_hero_name", "desc_key": "ach_keto_hero_desc"},
    "night_owl": {"icon": "🦉", "name_key": "ach_night_owl_name", "desc_key": "ach_night_owl_desc"},
    "early_bird": {"icon": "🌅", "name_key": "ach_early_bird_name", "desc_key": "ach_early_bird_desc"},
    "weekend_warrior": {"icon": "🏕️", "name_key": "ach_weekend_warrior_name", "desc_key": "ach_weekend_warrior_desc"},
    "vampire": {"icon": "🦇", "name_key": "ach_vampire_name", "desc_key": "ach_vampire_desc"},
    "fasting_monk": {"icon": "🧘", "name_key": "ach_fasting_monk_name", "desc_key": "ach_fasting_monk_desc"},
    "cheat_day": {"icon": "🍕", "name_key": "ach_cheat_day_name", "desc_key": "ach_cheat_day_desc"},
    "rabbit": {"icon": "🥗", "name_key": "ach_rabbit_name", "desc_key": "ach_rabbit_desc"},
    "first_card": {"icon": "🃏", "name_key": "ach_first_card_name", "desc_key": "ach_first_card_desc"},
    "hydration": {"icon": "💧", "name_key": "ach_hydration_name", "desc_key": "ach_hydration_desc"},
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
    
    user = await crud.get_user(db, user_id)
    if not user:
        return []
        
    # Helpers
    food_count_res = await db.execute(select(func.count(FoodLog.id)).where(FoodLog.user_id == user_id))
    food_count = food_count_res.scalar() or 0
    
    weight_count_res = await db.execute(select(func.count(WeightLog.id)).where(WeightLog.user_id == user_id))
    weight_count = weight_count_res.scalar() or 0
    
    food_logs_res = await db.execute(select(FoodLog).where(FoodLog.user_id == user_id).order_by(FoodLog.logged_at.asc()))
    user_food_logs = food_logs_res.scalars().all()
    
    weight_logs_res = await db.execute(select(WeightLog).where(WeightLog.user_id == user_id).order_by(WeightLog.logged_at.asc()))
    user_weight_logs = weight_logs_res.scalars().all()
    
    food_streak = await crud.get_or_create_streak(db, user_id, "food_logging")
    cal_streak = await crud.get_or_create_streak(db, user_id, "calorie_target_hit")
    prot_streak = await crud.get_or_create_streak(db, user_id, "protein_target_hit")
    
    # helper for fast unlocking
    async def unlock(key: str):
        if key not in unlocked_keys:
            if await crud.unlock_achievement(db, user_id, key):
                newly_unlocked.append(key)
                unlocked_keys.add(key)

    # 1. Streaks
    if food_streak.current_count >= 1: await unlock("first_step")
    if food_streak.current_count >= 3: await unlock("streak_3")
    if food_streak.current_count >= 7: await unlock("streak_7")
    if food_streak.current_count >= 14: await unlock("streak_14")
    if food_streak.current_count >= 21: await unlock("streak_21")
    if food_streak.current_count >= 30: await unlock("streak_30")
    if food_streak.current_count >= 50: await unlock("streak_50")
    if food_streak.current_count >= 100: await unlock("streak_100")
    if food_streak.current_count >= 180: await unlock("streak_180")
    if food_streak.current_count >= 365: await unlock("streak_365")
    
    # 2. Food Logging Volume
    if food_count >= 1: await unlock("first_meal")
    if food_count >= 10: await unlock("log_10_meals")
    if food_count >= 50: await unlock("log_50_meals")
    if food_count >= 100: await unlock("log_100_meals")
    if food_count >= 250: await unlock("log_250_meals")
    if food_count >= 500: await unlock("log_500_meals")
    if food_count >= 1000: await unlock("log_1000_meals")
    
    # 3. Weight Tracking Milestones
    if weight_count >= 1: await unlock("first_weight")
    if weight_count >= 5: await unlock("weight_5")
    if weight_count >= 10: await unlock("weight_10")
    if weight_count >= 25: await unlock("weight_25")
    if weight_count >= 50: await unlock("weight_50")
    if weight_count >= 100: await unlock("weight_100")
    
    if weight_count >= 4 and "weight_trend_down" not in unlocked_keys:
        w = [l.weight for l in user_weight_logs[-4:]]
        if len(w) == 4 and w[0] > w[1] > w[2] > w[3]:
            await unlock("weight_trend_down")
            
    if weight_count >= 4 and "weight_trend_up" not in unlocked_keys:
        w = [l.weight for l in user_weight_logs[-4:]]
        if len(w) == 4 and w[0] < w[1] < w[2] < w[3]:
            await unlock("weight_trend_up")

    # 4. Target hits
    if cal_streak.current_count >= 1: await unlock("target_hit_1")
    if cal_streak.current_count >= 3: await unlock("target_hit_3")
    if cal_streak.current_count >= 7: await unlock("target_hit_7")
    if cal_streak.current_count >= 14: await unlock("target_hit_14")
    if cal_streak.current_count >= 30: await unlock("target_hit_30")
    
    # 5. Protein
    if prot_streak.current_count >= 1: await unlock("protein_king")
    if prot_streak.current_count >= 7: await unlock("protein_streak_7")

    # Pre-compute daily totals for advanced checks
    daily_calories = {}
    daily_carbs = {}
    daily_fats = {}
    breakfasts = 0
    lunches = 0
    dinners = 0
    
    for log in user_food_logs:
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        local_time = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz)
        local_date = local_time.date()
        
        daily_calories[local_date] = daily_calories.get(local_date, 0) + log.calories
        daily_carbs[local_date] = daily_carbs.get(local_date, 0.0) + log.carbs
        daily_fats[local_date] = daily_fats.get(local_date, 0.0) + log.fats
        
        h = local_time.hour
        if 5 <= h < 11: breakfasts += 1
        elif 11 <= h < 16: lunches += 1
        elif 16 <= h < 22: dinners += 1
        
        if "early_bird" not in unlocked_keys and h < 8:
            await unlock("early_bird")
        if "night_owl" not in unlocked_keys and (h >= 22 or h < 4):
            await unlock("night_owl")
            
        # Rabbit / Sweet tooth
        desc = (log.raw_text or "").lower()
        if "rabbit" not in unlocked_keys:
            green_keywords = ["salad", "vegetable", "lettuce", "broccoli", "spinach", "cucumber", "greens", "cabbage",
                              "салат", "овощ", "зелень", "брокколи", "шпинат", "огурец", "капуста"]
            if any(kw in desc for kw in green_keywords):
                await unlock("rabbit")
        
        if "hydration" not in unlocked_keys:
            water_keywords = ["water", "вода", "минералка", "минеральная вода"]
            if any(kw in desc for kw in water_keywords):
                await unlock("hydration")

    # Meal counts
    if breakfasts >= 10: await unlock("breakfast_lover")
    if lunches >= 10: await unlock("lunch_boss")
    if dinners >= 10: await unlock("dinner_king")

    # Day-level logic
    cheat_day_found = False
    deficit_found = False
    bulking_found = False
    carb_master_found = False
    fat_master_found = False
    keto_hero_found = False
    macro_wizard_found = False
    sweet_tooth_found = False
    
    for d, cal in daily_calories.items():
        # Cheat day
        if user.target_calories > 0 and cal >= 1.5 * user.target_calories:
            cheat_day_found = True
            
        # Keto hero
        if daily_carbs.get(d, 0) > 0 and daily_carbs.get(d, 0) < 30:
            keto_hero_found = True
            
        # Sweet tooth
        if daily_carbs.get(d, 0) > 200 and cal > 0:
            sweet_tooth_found = True
            
        if user.target_calories > 0 and user.target_carb > 0 and user.target_fat > 0:
            carb_diff = abs(daily_carbs.get(d, 0) - user.target_carb) / user.target_carb
            fat_diff = abs(daily_fats.get(d, 0) - user.target_fat) / user.target_fat
            
            if carb_diff <= 0.05: carb_master_found = True
            if fat_diff <= 0.05: fat_master_found = True
            
            if carb_diff <= 0.05 and fat_diff <= 0.05:
                # Need to check protein too but prot_streak handles protein. We approximate macro wizard here
                macro_wizard_found = True
                
    if cheat_day_found: await unlock("cheat_day")
    if keto_hero_found: await unlock("keto_hero")
    if sweet_tooth_found: await unlock("sweet_tooth")
    if carb_master_found: await unlock("carb_balancer")
    if fat_master_found: await unlock("fat_balancer")
    if macro_wizard_found: await unlock("macro_wizard")

    # We will leave fasting_monk and weekend_warrior for future advanced cron evaluation or assume they unlock later.
    
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

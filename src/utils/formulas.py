def calculate_targets(weight_kg: float, height_cm: float, age: int, sex: str, activity_level: str, goal: str) -> dict:
    """
    Calculates the target daily calories and macronutrients (protein, fat, carb in grams)
    using the Mifflin-St Jeor formula adjusted for activity level and goals.
    """
    # 1. BMR Calculation
    if sex.lower() == "male":
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + 5.0
        min_calories = 1500
    else:
        bmr = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age - 161.0
        min_calories = 1200

    # 2. Activity Level Multiplier (PAL)
    multipliers = {
        "sedentary": 1.2,
        "light": 1.375,
        "moderate": 1.55,
        "active": 1.725
    }
    pal = multipliers.get(activity_level.lower(), 1.2)
    tdee = bmr * pal

    # 3. Goal Calorie and Macro Adjustments
    if goal == "lose_weight":
        target_calories = max(min_calories, int(tdee * 0.85))
        # 30% Protein, 30% Fat, 40% Carbohydrate
        p_ratio, f_ratio, c_ratio = 0.30, 0.30, 0.40
    elif goal == "maintain":
        target_calories = int(tdee)
        # 20% Protein, 30% Fat, 50% Carbohydrate
        p_ratio, f_ratio, c_ratio = 0.20, 0.30, 0.50
    elif goal == "gain_weight":
        target_calories = int(tdee * 1.10)
        # 25% Protein, 25% Fat, 50% Carbohydrate
        p_ratio, f_ratio, c_ratio = 0.25, 0.25, 0.50
    elif goal == "gain_muscle":
        target_calories = int(tdee * 1.10)
        # 35% Protein, 25% Fat, 40% Carbohydrate
        p_ratio, f_ratio, c_ratio = 0.35, 0.25, 0.40
    else:
        target_calories = int(tdee)
        p_ratio, f_ratio, c_ratio = 0.20, 0.30, 0.50

    # 4. Convert ratios into grams
    target_protein = int((target_calories * p_ratio) / 4.0)
    target_fat = int((target_calories * f_ratio) / 9.0)
    target_carb = int((target_calories * c_ratio) / 4.0)

    return {
        "calories": target_calories,
        "protein": target_protein,
        "fat": target_fat,
        "carb": target_carb
    }

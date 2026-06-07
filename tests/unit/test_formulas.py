from src.utils.formulas import calculate_targets

def test_calculate_targets_male_lose():
    # Weight: 80kg, Height: 180cm, Age: 30, Sex: male, Activity: light, Goal: lose_weight
    # bmr = 10 * 80 + 6.25 * 180 - 5 * 30 + 5 = 800 + 1125 - 150 + 5 = 1780
    # pal (light) = 1.375
    # tdee = 1780 * 1.375 = 2447.5
    # target_calories (lose_weight) = max(1500, int(tdee * 0.85)) = max(1500, int(2080.375)) = 2080
    # target_protein = int((2080 * 0.3) / 4) = 156
    # target_fat = int((2080 * 0.3) / 9) = 69
    # target_carb = int((2080 * 0.4) / 4) = 208
    targets = calculate_targets(80.0, 180.0, 30, "male", "light", "lose_weight")
    assert targets["calories"] == 2080
    assert targets["protein"] == 156
    assert targets["fat"] == 69
    assert targets["carb"] == 208

def test_calculate_targets_female_maintain():
    # Weight: 60kg, Height: 165cm, Age: 25, Sex: female, Activity: sedentary, Goal: maintain
    # bmr = 10 * 60 + 6.25 * 165 - 5 * 25 - 161 = 600 + 1031.25 - 125 - 161 = 1345.25
    # pal (sedentary) = 1.2
    # tdee = 1345.25 * 1.2 = 1614.3
    # target_calories = 1614
    # target_protein = int((1614 * 0.2) / 4) = 80
    # target_fat = int((1614 * 0.3) / 9) = 53
    # target_carb = int((1614 * 0.5) / 4) = 201
    targets = calculate_targets(60.0, 165.0, 25, "female", "sedentary", "maintain")
    assert targets["calories"] == 1614
    assert targets["protein"] == 80
    assert targets["fat"] == 53
    assert targets["carb"] == 201

def test_calculate_targets_male_gain_weight():
    # Weight: 70kg, Height: 175cm, Age: 20, Sex: male, Activity: active, Goal: gain_weight
    # bmr = 10 * 70 + 6.25 * 175 - 5 * 20 + 5 = 700 + 1093.75 - 100 + 5 = 1698.75
    # pal (active) = 1.725
    # tdee = 1698.75 * 1.725 = 2930.34375
    # target_calories = int(tdee * 1.10) = int(3223.378) = 3223
    # target_protein = int((3223 * 0.25) / 4) = 201
    # target_fat = int((3223 * 0.25) / 9) = 89
    # target_carb = int((3223 * 0.5) / 4) = 402
    targets = calculate_targets(70.0, 175.0, 20, "male", "active", "gain_weight")
    assert targets["calories"] == 3223
    assert targets["protein"] == 201
    assert targets["fat"] == 89
    assert targets["carb"] == 402

def test_calculate_targets_female_gain_muscle():
    # Weight: 55kg, Height: 160cm, Age: 28, Sex: female, Activity: moderate, Goal: gain_muscle
    # bmr = 10 * 55 + 6.25 * 160 - 5 * 28 - 161 = 550 + 1000 - 140 - 161 = 1249
    # pal (moderate) = 1.55
    # tdee = 1249 * 1.55 = 1935.95
    # target_calories = int(tdee * 1.10) = int(2129.545) = 2129
    # target_protein = int((2129 * 0.35) / 4) = 186
    # target_fat = int((2129 * 0.25) / 9) = 59
    # target_carb = int((2129 * 0.40) / 4) = 212
    targets = calculate_targets(55.0, 160.0, 28, "female", "moderate", "gain_muscle")
    assert targets["calories"] == 2129
    assert targets["protein"] == 186
    assert targets["fat"] == 59
    assert targets["carb"] == 212

def test_calculate_targets_min_calories_female():
    # Test min calorie cap for female (1200 kcal)
    # Weight: 40kg, Height: 150cm, Age: 50, Sex: female, Activity: sedentary, Goal: lose_weight
    # bmr = 10 * 40 + 6.25 * 150 - 5 * 50 - 161 = 400 + 937.5 - 250 - 161 = 926.5
    # pal = 1.2 => tdee = 1111.8
    # target_calories (lose_weight) = int(tdee * 0.85) = 945 => capped at 1200
    targets = calculate_targets(40.0, 150.0, 50, "female", "sedentary", "lose_weight")
    assert targets["calories"] == 1200

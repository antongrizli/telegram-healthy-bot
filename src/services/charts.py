import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

CHART_LOCALES = {
    "en": {
        "nutrition_title": "Weekly Nutrition Summary",
        "calories_label": "Calories (kcal)",
        "calorie_target": "Target",
        "macros_title": "Average Macronutrients (g)",
        "macro_target": "Target",
        "macro_actual": "Actual",
        "protein": "Protein",
        "fat": "Fat",
        "carbs": "Carbs",
        "weight_title": "Weight Progress (30 Days)",
        "weight_label": "Weight (kg)",
        "no_data": "No weight logs recorded"
    },
    "ru": {
        "nutrition_title": "Недельная калорийность",
        "calories_label": "Калории (ккал)",
        "calorie_target": "Цель",
        "macros_title": "Средние макронутриенты (г)",
        "macro_target": "Цель",
        "macro_actual": "Факт",
        "protein": "Белки",
        "fat": "Жиры",
        "carbs": "Углеводы",
        "weight_title": "Динамика веса (30 дней)",
        "weight_label": "Вес (кг)",
        "no_data": "Нет записей о весе"
    },
    "uk": {
        "nutrition_title": "Тижнева калорійність",
        "calories_label": "Калорії (ккал)",
        "calorie_target": "Ціль",
        "macros_title": "Середні макронутрієнти (г)",
        "macro_target": "Ціль",
        "macro_actual": "Факт",
        "protein": "Білки",
        "fat": "Жири",
        "carbs": "Вуглеводи",
        "weight_title": "Динаміка ваги (30 днів)",
        "weight_label": "Вага (кг)",
        "no_data": "Немає записів про вагу"
    },
    "pl": {
        "nutrition_title": "Tygodniowe podsumowanie kalorii",
        "calories_label": "Kalorie (kcal)",
        "calorie_target": "Cel",
        "macros_title": "Średnie makroskładniki (g)",
        "macro_target": "Cel",
        "macro_actual": "Rzeczywiste",
        "protein": "Białko",
        "fat": "Tłuszcz",
        "carbs": "Węglowodany",
        "weight_title": "Postęp wagi (30 dni)",
        "weight_label": "Waga (kg)",
        "no_data": "Brak zapisów wagi"
    },
    "de": {
        "nutrition_title": "Wöchentliche Kalorienübersicht",
        "calories_label": "Kalorien (kcal)",
        "calorie_target": "Ziel",
        "macros_title": "Durchschnittliche Makronährstoffe (g)",
        "macro_target": "Ziel",
        "macro_actual": "Ist-Wert",
        "protein": "Eiweiß",
        "fat": "Fett",
        "carbs": "Kohlenhydrate",
        "weight_title": "Gewichtsverlauf (30 Tage)",
        "weight_label": "Gewicht (kg)",
        "no_data": "Keine Gewichtsdaten aufgezeichnet"
    },
    "tr": {
        "nutrition_title": "Haftalık Kalori Özeti",
        "calories_label": "Kalori (kcal)",
        "calorie_target": "Hedef",
        "macros_title": "Ortalama Makro Besinler (g)",
        "macro_target": "Hedef",
        "macro_actual": "Gerçekleşen",
        "protein": "Protein",
        "fat": "Yağ",
        "carbs": "Karbonhidrat",
        "weight_title": "Kilo Gelişimi (30 Gün)",
        "weight_label": "Kilo (kg)",
        "no_data": "Kilo kaydı bulunamadı"
    },
    "es": {
        "nutrition_title": "Resumen semanal de calorías",
        "calories_label": "Calorías (kcal)",
        "calorie_target": "Objetivo",
        "macros_title": "Promedio de macronutrientes (g)",
        "macro_target": "Objetivo",
        "macro_actual": "Real",
        "protein": "Proteínas",
        "fat": "Grasas",
        "carbs": "Carbohidratos",
        "weight_title": "Progreso de peso (30 días)",
        "weight_label": "Peso (kg)",
        "no_data": "No se registraron datos de peso"
    }
}

def get_locale_str(lang: str, key: str) -> str:
    lang_dict = CHART_LOCALES.get(lang, CHART_LOCALES["en"])
    return lang_dict.get(key, CHART_LOCALES["en"][key])

def generate_nutrition_chart(
    food_logs: list,
    target_calories: int,
    target_protein: int,
    target_fat: int,
    target_carb: int,
    language: str,
    timezone_str: str
) -> io.BytesIO:
    try:
        user_tz = ZoneInfo(timezone_str)
    except Exception:
        user_tz = ZoneInfo("UTC")

    now_local = datetime.now(user_tz)
    days = []
    daily_calories = {}

    for i in range(6, -1, -1):
        day = now_local - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        days.append(day)
        daily_calories[day_str] = 0.0

    total_protein = 0.0
    total_fat = 0.0
    total_carb = 0.0

    for log in food_logs:
        dt_utc = log.logged_at.replace(tzinfo=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(user_tz)
        day_str = dt_local.strftime("%Y-%m-%d")

        if day_str in daily_calories:
            daily_calories[day_str] += log.calories
            total_protein += log.proteins
            total_fat += log.fats
            total_carb += log.carbs

    num_days = 7
    avg_protein = total_protein / num_days
    avg_fat = total_fat / num_days
    avg_carb = total_carb / num_days

    bg_color = "#1E1E24"
    card_color = "#2D2D37"
    text_color = "#E0E0E6"
    grid_color = "#3D3D4A"

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), facecolor=bg_color)

    # Left plot: Daily Calories
    ax1.set_facecolor(card_color)
    x_labels = [d.strftime("%a\n%d/%m") for d in days]
    y_values = [daily_calories[d.strftime("%Y-%m-%d")] for d in days]

    ax1.bar(x_labels, y_values, color='#55D6BE', width=0.6, label=get_locale_str(language, "calories_label"))
    ax1.axhline(y=target_calories, color='#FF6B6B', linestyle='--', linewidth=2,
                label=f"{get_locale_str(language, 'calorie_target')}: {target_calories}")

    ax1.set_title(get_locale_str(language, "nutrition_title"), color=text_color, fontsize=12, fontweight='bold', pad=15)
    ax1.tick_params(colors=text_color)
    ax1.grid(True, color=grid_color, linestyle=':', alpha=0.6)
    ax1.spines['bottom'].set_color(grid_color)
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.spines['left'].set_color(grid_color)
    ax1.legend(facecolor=card_color, edgecolor=grid_color, labelcolor=text_color)

    # Right plot: Average Macros (Target vs Actual)
    ax2.set_facecolor(card_color)
    macro_labels = [
        get_locale_str(language, "protein"),
        get_locale_str(language, "fat"),
        get_locale_str(language, "carbs")
    ]
    targets = [target_protein, target_fat, target_carb]
    actuals = [avg_protein, avg_fat, avg_carb]

    x = range(len(macro_labels))
    width = 0.35

    ax2.bar([pos - width/2 for pos in x], targets, width, label=get_locale_str(language, "macro_target"), color='#FFD166')
    ax2.bar([pos + width/2 for pos in x], actuals, width, label=get_locale_str(language, "macro_actual"), color='#4EA8DE')

    ax2.set_xticks(x)
    ax2.set_xticklabels(macro_labels, color=text_color)
    ax2.set_title(get_locale_str(language, "macros_title"), color=text_color, fontsize=12, fontweight='bold', pad=15)
    ax2.tick_params(colors=text_color)
    ax2.grid(True, color=grid_color, linestyle=':', alpha=0.6)
    ax2.spines['bottom'].set_color(grid_color)
    ax2.spines['top'].set_visible(False)
    ax2.spines['right'].set_visible(False)
    ax2.spines['left'].set_color(grid_color)
    ax2.legend(facecolor=card_color, edgecolor=grid_color, labelcolor=text_color)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, facecolor=bg_color)
    buf.seek(0)
    plt.close(fig)
    return buf

def generate_weight_chart(
    weight_logs: list,
    language: str,
    timezone_str: str
) -> io.BytesIO:
    try:
        user_tz = ZoneInfo(timezone_str)
    except Exception:
        user_tz = ZoneInfo("UTC")

    bg_color = "#1E1E24"
    card_color = "#2D2D37"
    text_color = "#E0E0E6"
    grid_color = "#3D3D4A"

    fig, ax = plt.subplots(figsize=(8, 4), facecolor=bg_color)
    ax.set_facecolor(card_color)

    if not weight_logs:
        ax.text(0.5, 0.5, get_locale_str(language, "no_data"),
                color=text_color, ha='center', va='center', fontsize=14)
        ax.set_title(get_locale_str(language, "weight_title"), color=text_color, fontsize=12, fontweight='bold', pad=15)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_visible(False)
    else:
        dates = []
        weights = []
        for log in weight_logs:
            dt_utc = log.logged_at.replace(tzinfo=ZoneInfo("UTC"))
            dt_local = dt_utc.astimezone(user_tz)
            dates.append(dt_local)
            weights.append(log.weight)

        sorted_pairs = sorted(zip(dates, weights), key=lambda x: x[0])
        dates, weights = zip(*sorted_pairs)

        ax.plot(dates, weights, marker='o', color='#FF6B6B', linewidth=2, markersize=6,
                label=get_locale_str(language, "weight_label"))

        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        fig.autofmt_xdate()

        ax.set_title(get_locale_str(language, "weight_title"), color=text_color, fontsize=12, fontweight='bold', pad=15)
        ax.tick_params(colors=text_color)
        ax.grid(True, color=grid_color, linestyle=':', alpha=0.6)
        ax.spines['bottom'].set_color(grid_color)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color(grid_color)
        ax.legend(facecolor=card_color, edgecolor=grid_color, labelcolor=text_color)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, facecolor=bg_color)
    buf.seek(0)
    plt.close(fig)
    return buf

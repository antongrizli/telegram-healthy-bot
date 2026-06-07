from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from src.utils.i18n_locales import get_text

def get_sex_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text=get_text("sex_male", lang), callback_data="sex:male"),
            InlineKeyboardButton(text=get_text("sex_female", lang), callback_data="sex:female")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_activity_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text=get_text("act_sedentary", lang), callback_data="activity:sedentary")],
        [InlineKeyboardButton(text=get_text("act_light", lang), callback_data="activity:light")],
        [InlineKeyboardButton(text=get_text("act_moderate", lang), callback_data="activity:moderate")],
        [InlineKeyboardButton(text=get_text("act_active", lang), callback_data="activity:active")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_goal_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text=get_text("goal_lose", lang), callback_data="goal:lose_weight")],
        [InlineKeyboardButton(text=get_text("goal_maintain", lang), callback_data="goal:maintain")],
        [InlineKeyboardButton(text=get_text("goal_gain_w", lang), callback_data="goal:gain_weight")],
        [InlineKeyboardButton(text=get_text("goal_gain_m", lang), callback_data="goal:gain_muscle")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_lang_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="English 🇺🇸", callback_data="lang:en"),
            InlineKeyboardButton(text="Русский 🇷🇺", callback_data="lang:ru")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_notifications_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text=get_text("yes", lang), callback_data="notify:yes"),
            InlineKeyboardButton(text=get_text("no", lang), callback_data="notify:no")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_weekly_day_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    days_ru = ["Воскресенье", "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
    kb = []
    for idx, (en_day, ru_day) in enumerate(zip(days, days_ru)):
        label = ru_day if lang == "ru" else en_day
        kb.append([InlineKeyboardButton(text=label, callback_data=f"weeklyday:{idx}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_food_confirm_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text=get_text("btn_accept", lang), callback_data="food:accept"),
            InlineKeyboardButton(text=get_text("btn_correct", lang), callback_data="food:correct")
        ],
        [
            InlineKeyboardButton(text=get_text("btn_cancel", lang), callback_data="food:cancel")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="📊 Stats" if lang == "en" else "📊 Статистика", callback_data="admin:stats"),
            InlineKeyboardButton(text="📢 Broadcast" if lang == "en" else "📢 Рассылка", callback_data="admin:broadcast")
        ],
        [
            InlineKeyboardButton(text="🚫 Block User" if lang == "en" else "🚫 Блокировать", callback_data="admin:block"),
            InlineKeyboardButton(text="✅ Unblock User" if lang == "en" else "✅ Разблокировать", callback_data="admin:unblock")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_cancel_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена", callback_data="admin:menu")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_back_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="⬅️ Back to Menu" if lang == "en" else "⬅️ Назад в меню", callback_data="admin:menu")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_timezone_list_button_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text=get_text("btn_timezone_list", lang), callback_data="tz:regions")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_timezone_regions_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    regions = ["Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "UTC"]
    kb = []
    for i in range(0, len(regions), 3):
        row = []
        for r in regions[i:i+3]:
            if r == "UTC":
                row.append(InlineKeyboardButton(text="UTC", callback_data="settz:UTC"))
            else:
                row.append(InlineKeyboardButton(text=r, callback_data=f"tzreg:{r}:0"))
        kb.append(row)
    
    back_label = "🔙 Back" if lang == "en" else "🔙 Назад"
    kb.append([InlineKeyboardButton(text=back_label, callback_data="tz:start")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_regional_timezone_keyboard(region: str, page: int, lang: str = "en") -> InlineKeyboardMarkup:
    from zoneinfo import available_timezones
    # Gather all timezones starting with this region
    all_tzs = sorted([tz for tz in available_timezones() if tz.startswith(f"{region}/")])
    
    PAGE_SIZE = 10
    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_tzs = all_tzs[start_idx:end_idx]
    
    total_pages = (len(all_tzs) + PAGE_SIZE - 1) // PAGE_SIZE
    if total_pages == 0:
        total_pages = 1
    
    kb = []
    for i in range(0, len(page_tzs), 2):
        row = []
        tz1 = page_tzs[i]
        label1 = tz1.replace(f"{region}/", "").replace("_", " ")
        row.append(InlineKeyboardButton(text=label1, callback_data=f"settz:{tz1}"))
        
        if i + 1 < len(page_tzs):
            tz2 = page_tzs[i+1]
            label2 = tz2.replace(f"{region}/", "").replace("_", " ")
            row.append(InlineKeyboardButton(text=label2, callback_data=f"settz:{tz2}"))
        kb.append(row)
        
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"tzreg:{region}:{page-1}"))
    
    nav_row.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="tz:noop"))
    
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"tzreg:{region}:{page+1}"))
        
    kb.append(nav_row)
    
    back_regions_label = "🔙 Back to Regions" if lang == "en" else "🔙 К регионам"
    kb.append([InlineKeyboardButton(text=back_regions_label, callback_data="tz:regions")])
    
    return InlineKeyboardMarkup(inline_keyboard=kb)

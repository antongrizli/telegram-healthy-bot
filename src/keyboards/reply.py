from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from src.utils.i18n_locales import get_text

def get_main_menu(lang: str = "en", is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Returns the main menu keyboard based on user's selected language.
    """
    kb = [
        [
            KeyboardButton(text=get_text("btn_log_food", lang)), 
            KeyboardButton(text=get_text("btn_log_weight", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_daily_report", lang)), 
            KeyboardButton(text=get_text("btn_weekly_report", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_my_profile", lang)), 
            KeyboardButton(text=get_text("btn_my_meals", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_help", lang))
        ]
    ]
    if is_admin:
        kb.append([KeyboardButton(text="👑 Admin Panel" if lang == "en" else "👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_menu(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="📊 Stats" if lang == "en" else "📊 Статистика"),
            KeyboardButton(text="📢 Broadcast" if lang == "en" else "📢 Рассылка")
        ],
        [
            KeyboardButton(text="👥 Active Users" if lang == "en" else "👥 Активные пользователи"),
            KeyboardButton(text="🚫 Blocked Users" if lang == "en" else "🚫 Заблокированные")
        ],
        [
            KeyboardButton(text="⬅️ Back to Main Menu" if lang == "en" else "⬅️ Главное меню")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_setup_profile_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text=get_text("btn_setup_profile", lang))],
        [KeyboardButton(text=get_text("btn_delete_profile", lang))],
        [KeyboardButton(text="⬅️ Back to Main Menu" if lang == "en" else "⬅️ Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_delete_confirm_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text=get_text("btn_confirm_delete", lang))],
        [KeyboardButton(text=get_text("btn_cancel", lang))]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_sex_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([
        KeyboardButton(text=get_text("sex_male", lang)),
        KeyboardButton(text=get_text("sex_female", lang))
    ])
    kb.append([KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_activity_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.extend([
        [KeyboardButton(text=get_text("act_sedentary", lang))],
        [KeyboardButton(text=get_text("act_light", lang))],
        [KeyboardButton(text=get_text("act_moderate", lang))],
        [KeyboardButton(text=get_text("act_active", lang))],
        [KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")]
    ])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_goal_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.extend([
        [KeyboardButton(text=get_text("goal_lose", lang))],
        [KeyboardButton(text=get_text("goal_maintain", lang))],
        [KeyboardButton(text=get_text("goal_gain_w", lang))],
        [KeyboardButton(text=get_text("goal_gain_m", lang))],
        [KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")]
    ])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_lang_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([
        KeyboardButton(text="English 🇺🇸"),
        KeyboardButton(text="Русский 🇷🇺")
    ])
    kb.append([KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_notifications_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([
        KeyboardButton(text=get_text("yes", lang)),
        KeyboardButton(text=get_text("no", lang))
    ])
    kb.append([KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_timezone_list_button_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([KeyboardButton(text=get_text("btn_timezone_list", lang))])
    kb.append([KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_timezone_regions_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    regions = ["Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "UTC"]
    kb = []
    for i in range(0, len(regions), 3):
        row = [KeyboardButton(text=r) for r in regions[i:i+3]]
        kb.append(row)
    kb.append([
        KeyboardButton(text="🔙 Back" if lang == "en" else "🔙 Назад"),
        KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")
    ])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_regional_timezone_keyboard(region: str, page: int, lang: str = "en") -> ReplyKeyboardMarkup:
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
        row.append(KeyboardButton(text=tz1))
        if i + 1 < len(page_tzs):
            tz2 = page_tzs[i+1]
            row.append(KeyboardButton(text=tz2))
        kb.append(row)
        
    # Navigation row
    nav_row = []
    if page > 0:
        nav_row.append(KeyboardButton(text="⬅️"))
    nav_row.append(KeyboardButton(text=f"{page+1}/{total_pages}"))
    if page < total_pages - 1:
        nav_row.append(KeyboardButton(text="➡️"))
    kb.append(nav_row)
    
    # Back to regions and cancel
    kb.append([
        KeyboardButton(text="🔙 Back to Regions" if lang == "en" else "🔙 К регионам"),
        KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")
    ])
    
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_meal_type_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text=get_text("meal_type_breakfast", lang)),
            KeyboardButton(text=get_text("meal_type_lunch", lang))
        ],
        [
            KeyboardButton(text=get_text("meal_type_dinner", lang)),
            KeyboardButton(text=get_text("meal_type_snack", lang))
        ],
        [
            KeyboardButton(text=get_text("meal_type_food", lang))
        ],
        [KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_food_confirm_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text=get_text("btn_accept", lang)),
            KeyboardButton(text=get_text("btn_correct", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_cancel", lang))
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_meals_keyboard(meals: list, show_next: bool, lang: str = "en") -> ReplyKeyboardMarkup:
    kb = []
    for idx, meal in enumerate(meals):
        num = idx + 1
        btn_edit_text = get_text("btn_edit_meal", lang, num=num)
        btn_delete_text = get_text("btn_delete_meal", lang, num=num)
        kb.append([
            KeyboardButton(text=btn_edit_text),
            KeyboardButton(text=btn_delete_text)
        ])
    nav_row = [
        KeyboardButton(text=get_text("btn_prev_day", lang))
    ]
    if show_next:
        nav_row.append(KeyboardButton(text=get_text("btn_next_day", lang)))
    kb.append(nav_row)
    kb.append([KeyboardButton(text="⬅️ Back to Main Menu" if lang == "en" else "⬅️ Главное меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_meal_edit_confirm_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text=get_text("btn_accept", lang)),
            KeyboardButton(text=get_text("btn_correct", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_cancel", lang))
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_active_users_keyboard(users: list, lang: str = "en") -> ReplyKeyboardMarkup:
    kb = []
    for u in users:
        username_str = f" (@{u.username})" if u.username else ""
        label = f"🚫 Block {u.name} (ID: {u.telegram_id}){username_str}" if lang == "en" else f"🚫 Блокировать {u.name} (ID: {u.telegram_id}){username_str}"
        kb.append([KeyboardButton(text=label)])
    kb.append([KeyboardButton(text="⬅️ Back to Menu" if lang == "en" else "⬅️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_blocked_users_keyboard(users: list, lang: str = "en") -> ReplyKeyboardMarkup:
    kb = []
    for u in users:
        username_str = f" (@{u.username})" if u.username else ""
        label = f"✅ Unblock {u.name} (ID: {u.telegram_id}){username_str}" if lang == "en" else f"✅ Разблокировать {u.name} (ID: {u.telegram_id}){username_str}"
        kb.append([KeyboardButton(text=label)])
    kb.append([KeyboardButton(text="⬅️ Back to Menu" if lang == "en" else "⬅️ Назад в меню")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_cancel_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [[KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_back_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [[KeyboardButton(text="⬅️ Back to Menu" if lang == "en" else "⬅️ Назад в меню")]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

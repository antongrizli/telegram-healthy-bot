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

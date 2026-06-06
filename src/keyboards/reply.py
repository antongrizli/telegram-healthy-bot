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

def get_cancel_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="❌ Cancel" if lang == "en" else "❌ Отмена")
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

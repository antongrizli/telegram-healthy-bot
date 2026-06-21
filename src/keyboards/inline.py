from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from src.config import settings
from src.utils.i18n_locales import get_text

def get_morning_actions_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """
    Inline keyboard for morning briefings: Link to dashboard and Quick log breakfast.
    """
    kb = [
        [
            InlineKeyboardButton(
                text=get_text("btn_view_dashboard", lang),
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}")
            )
        ],
        [
            InlineKeyboardButton(
                text=get_text("btn_log_breakfast", lang),
                callback_data="log_breakfast"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_health_card_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """
    Inline keyboard for Health Cards: Links directly to Card tab in WebApp.
    """
    kb = [
        [
            InlineKeyboardButton(
                text=get_text("btn_view_card", lang),
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=health-card")
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_achievement_inline(lang: str = "en", ach_key: str = "") -> InlineKeyboardMarkup:
    """
    Inline keyboard for achievement unlocked alerts: share and see all badges.
    """
    kb = [
        [
            InlineKeyboardButton(
                text=get_text("btn_all_achievements", lang),
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=achievements")
            )
        ]
    ]
    if ach_key:
        kb.append([
            InlineKeyboardButton(
                text="🎉 Share / Поделиться" if lang == "ru" else "🎉 Share",
                callback_data=f"share_achievement:{ach_key}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_streak_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """
    Inline keyboard for streak updates and daily check-ins.
    """
    kb = [
        [
            InlineKeyboardButton(
                text=get_text("btn_view_dashboard", lang),
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}")
            )
        ],
        [
            InlineKeyboardButton(
                text=get_text("btn_streak_status", lang),
                callback_data="view_streaks"
            ),
            InlineKeyboardButton(
                text=get_text("btn_log_food", lang),
                callback_data="log_breakfast"
            )
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_report_range_inline(lang: str = "en") -> InlineKeyboardMarkup:
    """
    Inline keyboard for switching report ranges.
    """
    kb = [
        [
            InlineKeyboardButton(text="📅 Daily / День" if lang == "ru" else "📅 Daily", callback_data="report_range:daily"),
            InlineKeyboardButton(text="🗓️ Weekly / Неделя" if lang == "ru" else "🗓️ Weekly", callback_data="report_range:weekly"),
            InlineKeyboardButton(text="🗓️ Monthly / Месяц" if lang == "ru" else "🗓️ Monthly", callback_data="report_range:monthly")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

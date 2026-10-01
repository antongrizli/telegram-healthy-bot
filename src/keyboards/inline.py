from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from src.config import settings
from src.utils.i18n_locales import get_text


def get_admin_users_inline(data, lang, blocked=False, actor_id=None):
    mode = 'blocked' if blocked else 'active'
    action = 'unblock' if blocked else 'block'
    rows = []
    for user in data['users']:
        if user['is_admin'] or user['telegram_id'] in settings.ADMIN_USER_IDS or user['telegram_id'] == actor_id:
            continue
        label = get_text('admin_users_' + action, lang, user_id=user['telegram_id'])
        rows.append([InlineKeyboardButton(text=label,
            callback_data=f"adminusers:{action}:{user['telegram_id']}:{data['page']}")])
    nav = []
    if data['page']:
        nav.append(InlineKeyboardButton(text='◀', callback_data=f"adminusers:page:{mode}:{data['page']-1}"))
    nav.append(InlineKeyboardButton(text=f"{data['page']+1}/{data['pages']}",
                                   callback_data=f"adminusers:page:{mode}:{data['page']}"))
    if data['page'] + 1 < data['pages']:
        nav.append(InlineKeyboardButton(text='▶', callback_data=f"adminusers:page:{mode}:{data['page']+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text=get_text('admin_users_back', lang), callback_data='adminusers:back')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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
                text=get_text('ux_share', lang),
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
                callback_data="ux:food"
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
            InlineKeyboardButton(text=get_text('ux_daily', lang), callback_data="report_range:daily"),
            InlineKeyboardButton(text=get_text('ux_weekly', lang), callback_data="report_range:weekly"),
            InlineKeyboardButton(text=get_text('ux_monthly', lang), callback_data="report_range:monthly")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

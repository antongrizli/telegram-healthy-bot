import re
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from src.utils.i18n_locales import get_text, get_all_translations

def get_main_menu(lang: str = "en", is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Returns the main menu keyboard based on user's selected language.
    """
    kb = [[KeyboardButton(text=get_text(key, lang)) for key in row]
          for row in [('ux_add', 'ux_today'), ('ux_progress', 'ux_more'), ('btn_workout',)]]
    if is_admin:
        kb.append([KeyboardButton(text="👑 Admin Panel" if lang == "en" else "👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_section_menu(section, lang):
    rows = {
        'add': [('btn_log_food', 'btn_log_weight'), ('ux_water',), ('btn_pending_meals',)],
        'more': [('btn_my_profile', 'btn_settings'), ('btn_medications', 'btn_help')],
    }[section]
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=get_text(key, lang)) for key in row]
        for row in [*rows, ('ux_back',)]], resize_keyboard=True)


def get_today_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=get_text('btn_log_food', lang))],
        [KeyboardButton(text=get_text('btn_daily_report', lang)),
         KeyboardButton(text=get_text('btn_my_meals', lang))],
        [KeyboardButton(text=get_text('btn_pending_meals', lang))],
        [KeyboardButton(text=get_text('ux_back', lang))],
    ], resize_keyboard=True)


def get_workout_menu(lang: str, mode: str = 'home', frequency: int | None = None) -> ReplyKeyboardMarkup:
    """Reply-keyboard menus for the workout hub and an editable workout draft."""
    from src.utils.workout_locales import tr
    if mode == 'draft':
        rows = [
            [KeyboardButton(text=get_text('btn_workout_done', lang))],
            [KeyboardButton(text=get_text('btn_workout_template', lang))],
            [KeyboardButton(text=get_text('btn_workout_cancel', lang))],
        ]
    elif mode == 'frequency':
        rows = [
            [KeyboardButton(text=str(i)) for i in (1, 2, 3, 4)],
            [KeyboardButton(text=str(i)) for i in (5, 6, 7)],
            [KeyboardButton(text=get_text('ux_back', lang))],
        ]
    else:
        freq_text = (
            f"🔁 {tr('btn_workout_frequency_dynamic', lang, count=frequency)}"
            if frequency
            else get_text('btn_workout_frequency_default', lang)
        )
        rows = [
            [KeyboardButton(text=get_text('btn_workout_today', lang))],
            [KeyboardButton(text=get_text('btn_workout_history', lang))],
            [
                KeyboardButton(text=get_text('btn_workout_templates', lang)),
                KeyboardButton(text=freq_text),
            ],
            [KeyboardButton(text=get_text('ux_back', lang))],
        ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_training_hub_reply_keyboard(lang: str = "ru", is_active: bool = False) -> ReplyKeyboardMarkup:
    """Bottom docked reply keyboard for Workout Hub."""
    start_label = "▶️ Продолжить" if is_active else "▶️ Начать"
    if lang == "en":
        start_label = "▶️ Resume" if is_active else "▶️ Start"
    elif lang == "uk":
        start_label = "▶️ Продовжити" if is_active else "▶️ Почати"
    elif lang == "de":
        start_label = "▶️ Fortsetzen" if is_active else "▶️ Starten"
    elif lang == "pl":
        start_label = "▶️ Wznów" if is_active else "▶️ Rozpocznij"
    elif lang == "tr":
        start_label = "▶️ Devam et" if is_active else "▶️ Başla"
    elif lang == "es":
        start_label = "▶️ Reanudar" if is_active else "▶️ Empezar"

    goal_label = "🎯 Другая цель"
    repeat_label = "🔁 Повторить прошлую"
    write_label = "✍️ Записать"
    progress_label = "📈 Прогресс"
    settings_label = "⚙️ Тренажёры"

    if lang == "en":
        goal_label = "🎯 Other goal"
        repeat_label = "🔁 Like last time"
        write_label = "✍️ Log workout"
        progress_label = "📈 Progress"
        settings_label = "⚙️ Equipment"
    elif lang == "uk":
        goal_label = "🎯 Інша ціль"
        repeat_label = "🔁 Повторити минулу"
        write_label = "✍️ Записати"
        progress_label = "📈 Прогрес"
        settings_label = "⚙️ Тренажери"
    elif lang == "de":
        goal_label = "🎯 Anderes Ziel"
        repeat_label = "🔁 Wie letztes Mal"
        write_label = "✍️ Eintragen"
        progress_label = "📈 Fortschritt"
        settings_label = "⚙️ Geräte"
    elif lang == "pl":
        goal_label = "🎯 Inny cel"
        repeat_label = "🔁 Powtórz poprzedni"
        write_label = "✍️ Zapisz"
        progress_label = "📈 Postępy"
        settings_label = "⚙️ Maszyny"
    elif lang == "tr":
        goal_label = "🎯 Başka hedef"
        repeat_label = "🔁 Geçen seferki gibi"
        write_label = "✍️ Kaydet"
        progress_label = "📈 Gelişim"
        settings_label = "⚙️ Makineler"
    elif lang == "es":
        goal_label = "🎯 Otro objetivo"
        repeat_label = "🔁 Como la última vez"
        write_label = "✍️ Registrar"
        progress_label = "📈 Progreso"
        settings_label = "⚙️ Máquinas"

    rows = [
        [KeyboardButton(text=start_label)],
        [KeyboardButton(text=goal_label), KeyboardButton(text=repeat_label)],
        [KeyboardButton(text=write_label), KeyboardButton(text=progress_label)],
        [KeyboardButton(text=settings_label)],
        [KeyboardButton(text=get_text("ux_back", lang))],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_equipment_reply_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Bottom docked reply keyboard for Equipment & Profile section."""
    add_label = "➕ Добавить тренажёр"
    del_label = "🗑 Удалить тренажёр"
    prof_label = "✏️ Настроить профиль"
    back_hub_label = "⬅️ К тренировкам"

    if lang == "en":
        add_label = "➕ Add equipment"
        del_label = "🗑 Remove equipment"
        prof_label = "✏️ Edit profile"
        back_hub_label = "⬅️ Back to workouts"
    elif lang == "uk":
        add_label = "➕ Додати тренажер"
        del_label = "🗑 Видалити тренажер"
        prof_label = "✏️ Налаштувати профіль"
        back_hub_label = "⬅️ До тренувань"
    elif lang == "de":
        add_label = "➕ Gerät hinzufügen"
        del_label = "🗑 Gerät entfernen"
        prof_label = "✏️ Profil bearbeiten"
        back_hub_label = "⬅️ Zurück zum Training"
    elif lang == "pl":
        add_label = "➕ Dodaj maszynę"
        del_label = "🗑 Usuń maszynę"
        prof_label = "✏️ Edytuj profil"
        back_hub_label = "⬅️ Do treningów"
    elif lang == "tr":
        add_label = "➕ Makine ekle"
        del_label = "🗑 Makine sil"
        prof_label = "✏️ Profili düzenle"
        back_hub_label = "⬅️ Antrenmanlara dön"
    elif lang == "es":
        add_label = "➕ Añadir máquina"
        del_label = "🗑 Eliminar máquina"
        prof_label = "✏️ Editar perfil"
        back_hub_label = "⬅️ Volver a entrenamientos"

    rows = [
        [KeyboardButton(text=add_label), KeyboardButton(text=del_label)],
        [KeyboardButton(text=prof_label)],
        [KeyboardButton(text=back_hub_label), KeyboardButton(text=get_text("ux_back", lang))],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_equipment_sub_reply_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Bottom docked reply keyboard during equipment add/delete flow."""
    back_eq_label = "⬅️ К тренажёрам"
    back_hub_label = "⬅️ К тренировкам"
    if lang == "en":
        back_eq_label = "⬅️ Back to equipment"
        back_hub_label = "⬅️ Back to workouts"
    elif lang == "uk":
        back_eq_label = "⬅️ До тренажерів"
        back_hub_label = "⬅️ До тренувань"
    elif lang == "de":
        back_eq_label = "⬅️ Zurück zu Geräten"
        back_hub_label = "⬅️ Zurück zum Training"
    elif lang == "pl":
        back_eq_label = "⬅️ Do maszyn"
        back_hub_label = "⬅️ Do treningów"
    elif lang == "tr":
        back_eq_label = "⬅️ Makinelere dön"
        back_hub_label = "⬅️ Antrenmanlara dön"
    elif lang == "es":
        back_eq_label = "⬅️ Volver a máquinas"
        back_hub_label = "⬅️ Volver a entrenamientos"

    rows = [
        [KeyboardButton(text=back_eq_label), KeyboardButton(text=back_hub_label)],
        [KeyboardButton(text=get_text("ux_back", lang))],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def get_progress_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=get_text('btn_charts', lang)),
         KeyboardButton(text=get_text('btn_all_achievements', lang))],
        [KeyboardButton(text=get_text('btn_view_card', lang)),
         KeyboardButton(text=get_text('btn_weekly_report', lang))],
        [KeyboardButton(text=get_text('ux_back', lang))],
    ], resize_keyboard=True)


def get_report_keyboard(lang: str, report_id: int) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=f"{get_text('ux_details', lang)} · {report_id}")],
        [KeyboardButton(text=get_text('ux_back', lang))],
    ], resize_keyboard=True)



def get_admin_menu(lang: str = "en", pending_count: int = 0) -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text="📊 Stats" if lang == "en" else "📊 Статистика"),
            KeyboardButton(text="📢 Broadcast" if lang == "en" else "📢 Рассылка")
        ],
        [
            KeyboardButton(text="👥 Active Users" if lang == "en" else "👥 Активные пользователи"),
            KeyboardButton(text="🚫 Blocked Users" if lang == "en" else "🚫 Заблокированные")
        ],
        [KeyboardButton(text=access_count_label("btn_access_menu", lang, pending_count))],
        [
            KeyboardButton(text="⚠️ Failed Queue" if lang == "en" else "⚠️ Сбои очереди")
        ],
        [
            KeyboardButton(text=get_text("admin_download_logs", lang))
        ],
        [
            KeyboardButton(text=get_text("ux_back", lang))
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_setup_profile_keyboard(lang: str = "en", registered: bool = False) -> ReplyKeyboardMarkup:
    kb = [
        [KeyboardButton(text=get_text("btn_setup_profile", lang))],
        [KeyboardButton(text=get_text("btn_delete_profile", lang))],
        [KeyboardButton(text=get_text("ux_back", lang))]
    ]
    if registered:
        kb.insert(1, [KeyboardButton(text=get_text("btn_workout_goals", lang))])
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
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
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
        [KeyboardButton(text=get_text("btn_cancel", lang))]
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
        [KeyboardButton(text=get_text("btn_cancel", lang))]
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
    kb.append([
        KeyboardButton(text="Українська 🇺🇦"),
        KeyboardButton(text="Polski 🇵🇱")
    ])
    kb.append([
        KeyboardButton(text="Deutsch 🇩🇪"),
        KeyboardButton(text="Türkçe 🇹🇷")
    ])
    kb.append([
        KeyboardButton(text="Español 🇪🇸")
    ])
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_notifications_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([
        KeyboardButton(text=get_text("yes", lang)),
        KeyboardButton(text=get_text("no", lang))
    ])
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_timezone_list_button_keyboard(lang: str = "en", current_val: str = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_val))])
    kb.append([KeyboardButton(text=get_text("btn_timezone_list", lang))])
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_timezone_regions_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    regions = ["Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "UTC"]
    kb = []
    for i in range(0, len(regions), 3):
        row = [KeyboardButton(text=r) for r in regions[i:i+3]]
        kb.append(row)
    kb.append([
        KeyboardButton(text=get_text("ux_previous", lang)),
        KeyboardButton(text=get_text("btn_cancel", lang))
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
        KeyboardButton(text=get_text("ux_regions", lang)),
        KeyboardButton(text=get_text("btn_cancel", lang))
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
        [KeyboardButton(text=get_text("btn_cancel", lang))]
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
    kb.append([KeyboardButton(text=get_text("ux_back", lang))])
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


def get_admin_stats_keyboard(lang: str = "en") -> ReplyKeyboardMarkup:
    kb = [
        [
            KeyboardButton(text=get_text("btn_stats_demographics", lang)),
            KeyboardButton(text=get_text("btn_stats_engagement", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_stats_ai", lang)),
            KeyboardButton(text=get_text("btn_stats_queue", lang))
        ],
        [
            KeyboardButton(text=get_text("btn_stats_back_admin", lang))
        ]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_weekly_report_day_keyboard(lang: str = "en", current_val: int = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        current_name = get_text(f"weekday_{current_val}", lang)
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=current_name))])
    
    row = []
    for i in range(6):  # 0 (Mon) to 5 (Sat)
        row.append(KeyboardButton(text=get_text(f"weekday_{i}", lang)))
        if len(row) == 2:
            kb.append(row)
            row = []
            
    kb.append([KeyboardButton(text=get_text("weekday_6", lang))]) # Sunday (6)
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_monthly_report_day_keyboard(lang: str = "en", current_val: int = None) -> ReplyKeyboardMarkup:
    kb = []
    if current_val is not None:
        kb.append([KeyboardButton(text=get_text("btn_keep_current", lang, value=str(current_val)))])
        
    kb.append([
        KeyboardButton(text="1"),
        KeyboardButton(text="7"),
        KeyboardButton(text="15"),
        KeyboardButton(text="28")
    ])
    kb.append([KeyboardButton(text=get_text("btn_cancel", lang))])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


def get_access_menu(lang: str = 'en', pending_count: int = 0, auto_approve: bool = False) -> ReplyKeyboardMarkup:
    rows = []
    if not auto_approve:
        rows.append([KeyboardButton(text=access_count_label('btn_access_pending', lang, pending_count))])
    rows.append([KeyboardButton(text=get_text(
        'btn_auto_approval_on' if auto_approve else 'btn_auto_approval_off', lang))])
    rows.append([KeyboardButton(text=get_text('btn_access_back', lang))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def access_count_label(key, language, count):
    return f"{get_text(key, language)} ({count})"


def is_access_button(text, key):
    if not isinstance(text, str):
        return False
    labels = get_all_translations(key)
    return any(text == label or re.fullmatch(re.escape(label) + r" \(\d+\)", text) is not None for label in labels)

from src.handlers.admin import format_queue_errors

def test_format_queue_errors_empty():
    assert format_queue_errors({}, "en") == "  • No errors"
    assert format_queue_errors({}, "ru") == "  • Ошибок нет"

def test_format_queue_errors_with_data_en():
    errors = {
        "503 Service Unavailable": 2,
        "name 'settings' is not defined": 1
    }
    result = format_queue_errors(errors, "en")
    expected = (
        "  • **Count**: 2\n"
        "```\n"
        "503 Service Unavailable\n"
        "```\n"
        "  • **Count**: 1\n"
        "```\n"
        "name 'settings' is not defined\n"
        "```"
    )
    assert result == expected

def test_format_queue_errors_with_data_ru():
    errors = {
        "503 Service Unavailable": 2,
    }
    result = format_queue_errors(errors, "ru")
    expected = (
        "  • **Количество**: 2\n"
        "```\n"
        "503 Service Unavailable\n"
        "```"
    )
    assert result == expected

def test_format_queue_errors_escapes_triple_backticks():
    errors = {
        "error with ```some code``` inside": 1
    }
    result = format_queue_errors(errors, "en")
    assert "'''" in result
    assert "```" in result  # outer block still present


def test_recent_user_activity_formats_timestamps_and_names_safely():
    from datetime import datetime, timezone, timedelta
    from src.handlers.admin import format_recent_user_activity
    result = format_recent_user_activity([{
        "telegram_id": 42, "name": "Test_*\n User",
        "joined_at": datetime(2026, 9, 6, 12, 0, tzinfo=timezone(timedelta(hours=2))),
        "last_meal_at": datetime(2026, 9, 6, 11, 30)
    }], "en")
    assert "Test_* User (ID: 42)" in result
    assert "Joined: 2026-09-06 10:00:00 UTC" in result
    assert "Last meal tracked: 2026-09-06 11:30:00 UTC" in result


def test_recent_user_activity_empty_is_localized():
    from src.handlers.admin import format_recent_user_activity
    assert "No registrations" in format_recent_user_activity([], "en")
    assert "регистраций нет" in format_recent_user_activity([], "ru")


def test_pending_meal_button_is_localized_and_available_in_main_menu():
    from src.keyboards.reply import get_main_menu
    from src.utils.i18n_locales import LOCALES, get_text
    for lang in LOCALES:
        label = get_text("btn_pending_meals", lang)
        assert label != "btn_pending_meals"
        assert label in [button.text for row in get_main_menu(lang).keyboard for button in row]
        assert label in get_text("session_recovery", lang)
        assert "/pending" not in get_text("session_recovery", lang)

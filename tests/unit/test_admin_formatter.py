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

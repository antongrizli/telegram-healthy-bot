from src.utils.escape import escape_markdown, clean_telegram_markdown

def test_escape_markdown():
    assert escape_markdown("hello") == "hello"
    assert escape_markdown("hello_world") == "hello\\_world"
    assert escape_markdown("hello*world") == "hello\\*world"
    assert escape_markdown("hello[world") == "hello\\[world"
    assert escape_markdown("hello`world") == "hello\\`world"
    assert escape_markdown("") == ""
    assert escape_markdown(None) == ""

def test_clean_telegram_markdown_bold():
    # Convert standard markdown bold to Telegram V1 bold
    assert clean_telegram_markdown("This is **bold** text.") == "This is *bold* text."
    
def test_clean_telegram_markdown_italic():
    # Convert standard markdown italic (using * or _) to Telegram V1 italic (using _)
    assert clean_telegram_markdown("This is *italic* text.") == "This is _italic_ text."
    assert clean_telegram_markdown("This is _italic_ text.") == "This is _italic_ text."

def test_clean_telegram_markdown_bullets():
    # Replace * and - lists with unicode bullet •
    assert clean_telegram_markdown("* item 1\n* item 2") == "• item 1\n• item 2"
    assert clean_telegram_markdown("- item 1\n- item 2") == "• item 1\n• item 2"
    assert clean_telegram_markdown("  * indented item") == "  • indented item"

def test_clean_telegram_markdown_code_blocks():
    # Verify code blocks are preserved and special characters inside them are not escaped
    code = "```python\ndef test():\n    return 'hello_world'\n```"
    # Note: we don't change text inside code blocks
    assert clean_telegram_markdown(code) == code

def test_clean_telegram_markdown_inline_code():
    # Verify inline code is preserved and special characters inside it are not escaped
    inline = "Use `hello_world` inside the text."
    assert clean_telegram_markdown(inline) == inline

def test_clean_telegram_markdown_unclosed_block():
    # Verify unclosed code blocks are closed safely
    unclosed = "Some text\n```python\nprint(1)"
    assert clean_telegram_markdown(unclosed) == "Some text\n```python\nprint(1)```"

def test_clean_telegram_markdown_mixed_escaping():
    # Test a complex scenario of bold, italic, lists, and raw characters to escape
    raw_input = (
        "**Header**\n"
        "*   **Item 1:** This has a raw_underscore and a raw * star.\n"
        "*(Note: some italic note)*\n"
        "```text\n"
        "No_escape_inside_this_code_block*\n"
        "```"
    )
    expected_output = (
        "*Header*\n"
        "• *Item 1:* This has a raw\\_underscore and a raw \\* star.\n"
        "_(Note: some italic note)_\n"
        "```text\n"
        "No_escape_inside_this_code_block*\n"
        "```"
    )
    assert clean_telegram_markdown(raw_input) == expected_output

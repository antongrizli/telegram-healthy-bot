from src.utils.report_format import report_plain_text, report_parts


def test_legacy_daily_report_is_mobile_readable():
    raw = '''📊 *Ежедневный отчет для Антона*

*Текущий статус питания:*
Текст анализа.
```
| Показатель | Факт | Цель | Остаток |
|------------|------|------|---------|
| Калории    | 350  | 2232 | -1882   |
| Белки      | 9.9г | 167г | -157.1г |
```
---
*💊 Витамины и препараты*
• *Важное примечание:* Обратитесь к врачу.
'''
    result = report_plain_text(raw)
    assert '*' not in result and '`' not in result and '|' not in result
    assert '• Калории: Факт: 350 · Цель: 2232 · Остаток: -1882' in result
    assert '• Белки: Факт: 9.9г · Цель: 167г · Остаток: -157.1г' in result
    assert 'Важное примечание: Обратитесь к врачу.' in result
    assert report_plain_text(result) == result


def test_escaped_markdown_and_user_text_are_plain():
    assert report_plain_text('**Report**\n_user\\_name_ <script>') == 'Report\nuser_name <script>'


def test_long_report_parts_fit_telegram_even_with_emoji():
    parts = report_parts('😀' * 5000)
    assert ''.join(parts) == '😀' * 5000
    assert all(len(part.encode('utf-16-le')) // 2 <= 4096 for part in parts)
    assert all(len(part) <= 2000 for part in report_parts('Heading\n' + 'x' * 6000))

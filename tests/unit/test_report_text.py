import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.training import llm
from src.services.training.parser import parse_text
from src.services.training.report_text import clean_report

REPORT = """Отчёт о тренировке 2 ноября 2026:
Не выполнено: Ellipsentrainer — 10–12 мин · лёгкое сопротивление
Не выполнено: Brustpresse — 25–30 кг · 2 × 12
Не выполнено: Latzug — 25–30 кг · 2 × 12
Не выполнено: Rudermaschine — 25–30 кг · 2 × 12
Не выполнено: Beinbeuger sitzend — 25–30 кг · 2 × 12
Не выполнено: Beinstrecker — 25–30 кг · 2 × 12
Не выполнено: Abduktion — 25–30 кг · 2 × 12
Самочувствие: Нормально
Примечания: Ellipsentrainer 20 минут кросс тренинг

Brustpresse 35кг x 4 подхода по 15 раз

Latzug 40кг 3 подхода по 15 раз

Rudermaschine 45 кг 3 подхода по 12 раз

Beinstrecker 40 кг 3 подхода по 12 раз

Abduktion и  Adduktion 40 kg x 3 x 15
Bizepsmaschine 15x3x15kg. Проанализируй результат и скорректируй следующую тренировку."""


def test_clean_report_user_sample():
    rep = clean_report(REPORT)
    assert rep.feeling == "normal"
    assert "Проанализируй" in (rep.note or "")
    items = parse_text(rep.body, lang="ru")
    assert [i.name for i in items] == [
        "Ellipsentrainer", "Brustpresse", "Latzug", "Rudermaschine", "Beinstrecker",
        "Abduktion", "Adduktion", "Bizepsmaschine",
    ]
    assert all(":" not in i.name for i in items)
    brust = items[1]
    assert len(brust.sets) == 4 and all(s.weight_kg == 35.0 and s.reps == 15 for s in brust.sets)


def test_plain_text_untouched():
    rep = clean_report("Latzug 40кг 3х12")
    assert rep.body == "Latzug 40кг 3х12" and rep.feeling is None and rep.note is None


def test_parse_unparsed_lines_uses_llm_json():
    from src.services.training.dto import DraftItem

    item = DraftItem(line_no=1, raw="жим 40 на 10 три раза", name="жим 40 на 10 три раза", status="unparsed")
    payload = '{"items":[{"line_no":1,"name":"Brustpresse","kind":"strength","sets":[{"weight_kg":40.0,"reps":10}]}]}'
    resp = SimpleNamespace(text=payload)
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=resp)):
        out = asyncio.run(llm.parse_unparsed_lines([item], known_exercises=[], lang="ru"))
    assert out[0].source == "llm" and out[0].name == "Brustpresse"

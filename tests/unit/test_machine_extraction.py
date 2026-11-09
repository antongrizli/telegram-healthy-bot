"""Tests for AI machine extraction from pasted text and name-length safety."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.training import llm
from src.services.training.library import MAX_NAME_LEN, slugify

REPORT = """Отчёт о тренировке 2 ноября 2026:
Не выполнено: Brustpresse — 25–30 кг · 2 × 12
Самочувствие: Нормально
Abduktion и Adduktion 40 kg x 3 x 15
Проанализируй результат и скорректируй следующую тренировку."""


def _resp(text):
    return SimpleNamespace(text=text)


def test_extract_machines_parses_and_dedupes():
    payload = '```json\n{"machines": ["brustpresse", "Brustpresse", "Abduktion", "Adduktion", "", 5]}\n```'
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=_resp(payload))):
        result = asyncio.run(llm.extract_machines_from_text(REPORT))
    assert result == ["Brustpresse", "Abduktion", "Adduktion"]


def test_extract_machines_drops_overlong_names():
    payload = '{"machines": ["' + "x" * 300 + '", "Latzug"]}'
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=_resp(payload))):
        assert asyncio.run(llm.extract_machines_from_text(REPORT)) == ["Latzug"]


def test_extract_machines_returns_empty_on_error_and_bad_json():
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(side_effect=RuntimeError("429"))):
        assert asyncio.run(llm.extract_machines_from_text(REPORT)) == []
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=_resp("nonsense"))):
        assert asyncio.run(llm.extract_machines_from_text(REPORT)) == []


def test_slug_and_name_limits_fit_columns():
    assert len(slugify(REPORT * 3)) <= MAX_NAME_LEN
    assert MAX_NAME_LEN < 150

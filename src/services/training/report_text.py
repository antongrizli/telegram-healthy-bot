"""Clean a pasted workout report so only performed exercises reach the parser."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional

Feeling = Literal["easy", "normal", "hard"]

_TITLES = (
    "Workout report", "Отчёт о тренировке", "Отчет о тренировке", "Звіт про тренування",
    "Raport z treningu", "Trainingsbericht", "Antrenman raporu", "Informe del entrenamiento",
)
_NOT_DONE = (
    "Не выполнено", "Not done", "Не виконано", "Nie wykonano", "Nicht ausgeführt", "Nicht erledigt",
    "Yapılmadı", "No realizado",
)
_FEELING_LABELS = ("Самочувствие", "Feeling", "Самопочуття", "Samopoczucie", "Befinden", "Durum", "Sensación")
_NOTE_LABELS = ("Примечания", "Примечание", "Notes", "Примітки", "Notatki", "Notizen", "Notlar", "Notas")
_ANALYZE = ("Проанализируй", "Analyze", "Analyse", "Analizuj", "Analysiere", "Analiz et", "Analiza", "Проаналізуй")

_FEELING_VALUES = {
    "easy": ("легко", "easy", "łatwo", "leicht", "kolay", "fácil", "facil"),
    "normal": ("нормально", "normal", "normalnie", "normalny", "nормально"),
    "hard": ("тяжело", "hard", "ciężko", "schwer", "zor", "difícil", "dificil", "важко"),
}

_title_re = re.compile(r"^\s*(?:%s)\b" % "|".join(map(re.escape, _TITLES)), re.IGNORECASE)
_not_done_re = re.compile(r"^\s*(?:%s)\s*:" % "|".join(map(re.escape, _NOT_DONE)), re.IGNORECASE)
_feeling_re = re.compile(r"^\s*(?:%s)\s*:\s*(.*)$" % "|".join(map(re.escape, _FEELING_LABELS)), re.IGNORECASE)
_note_re = re.compile(r"^\s*(?:%s)\s*:\s*" % "|".join(map(re.escape, _NOTE_LABELS)), re.IGNORECASE)
_analyze_re = re.compile(r"(?:^|(?<=[.!?])\s+)(?:%s)\b.*$" % "|".join(map(re.escape, _ANALYZE)), re.IGNORECASE)


@dataclass
class CleanReport:
    body: str
    feeling: Optional[Feeling] = None
    note: Optional[str] = None


def _feeling_from(value: str) -> Optional[Feeling]:
    v = value.strip().lower().rstrip(".!")
    for key, words in _FEELING_VALUES.items():
        if v in words:
            return key  # type: ignore[return-value]
    return None


def clean_report(text: str) -> CleanReport:
    """Drop report title, planned-but-not-done lines and assistant instructions; extract feeling."""
    body: List[str] = []
    notes: List[str] = []
    feeling: Optional[Feeling] = None

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _title_re.match(line) or _not_done_re.match(line):
            continue
        m = _feeling_re.match(line)
        if m:
            feeling = feeling or _feeling_from(m.group(1))
            continue
        line = _note_re.sub("", line)
        m = _analyze_re.search(line)
        if m:
            notes.append(m.group(0).strip())
            line = line[: m.start()].rstrip(" .")
        if line:
            body.append(line)

    return CleanReport(body="\n".join(body), feeling=feeling, note="\n".join(notes) or None)

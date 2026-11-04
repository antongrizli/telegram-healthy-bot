"""Normalization of text, numbers, units and verbal numbers for training inputs."""
from __future__ import annotations

import re
from typing import Tuple

VERBAL_NUMBERS = {
    # Russian
    "один": 1, "одна": 1, "одно": 1,
    "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,

    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,

    # German
    "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "funf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwolf": 12,
    "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "funfzehn": 15, "sechzehn": 16, "siebzehn": 17,
    "achtzehn": 18, "neunzehn": 19, "zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50,

    # Ukrainian
    "один": 1, "одна": 1, "одне": 1, "два": 2, "дві": 2, "три": 3, "чотири": 4, "п'ять": 5, "пять": 5,
    "шість": 6, "сім": 7, "вісім": 8, "дев'ять": 9, "девять": 9, "десять": 10,
    "одинадцять": 11, "дванадцять": 12, "тринадцять": 13, "чотирнадцять": 14, "п'ятнадцять": 15, "пятнадцять": 15,
    "двадцять": 20, "тридцять": 30, "сорок": 40, "п'ятдесят": 50,

    # Polish
    "jeden": 1, "jedna": 1, "jedno": 1, "dwa": 2, "dwie": 2, "trzy": 3, "cztery": 4, "pięć": 5, "piec": 5,
    "sześć": 6, "szesc": 6, "siedem": 7, "osiem": 8, "dziewięć": 9, "dziewiec": 9, "dziesięć": 10, "dziesiec": 10,
    "jedenaście": 11, "jedenascie": 11, "dwanaście": 12, "dwanascie": 12, "piętnaście": 15, "pietnascie": 15,
    "dwadzieścia": 20, "dwadziescia": 20, "trzydzieści": 30, "trzydziesci": 30, "czterdzieści": 40, "czterdziesci": 40,
    "pięćdziesiąt": 50, "piecdziesiat": 50,

    # Turkish
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5,
    "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
    "on bir": 11, "on iki": 12, "on beş": 15, "on bes": 15, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40, "elli": 50,

    # Spanish
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
}

# Regex tokens
RE_DECIMAL_COMMA = re.compile(r"(\d+),(\d+)")
RE_CROSS = re.compile(r"[х×*X]")
RE_LBS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:lb|lbs|фунт\w*)\b", re.IGNORECASE)


def replace_verbal_numbers(text: str) -> str:
    """Replace spoken/written verbal numbers with digit representations."""
    res = text
    # Sort keys by length descending to match multi-word phrases first (e.g. 'on bir')
    for word in sorted(VERBAL_NUMBERS.keys(), key=len, reverse=True):
        val = VERBAL_NUMBERS[word]
        res = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", str(val), res, flags=re.IGNORECASE)
    return res


def normalize_units_and_cross(text: str) -> str:
    """Normalize decimal comma, multiplication cross symbols, and convert lbs to kg."""
    # Convert decimal comma between digits: 42,5 -> 42.5
    s = RE_DECIMAL_COMMA.sub(r"\1.\2", text)

    # Convert multiplication symbols, avoiding replacing cyrillic 'х' inside Russian words (like 'подход', 'хват')
    s = re.sub(r"[×*]", "x", s)
    s = re.sub(r"(?<=\d|\s)[хX](?=\s|\d|$)", "x", s)
    s = re.sub(r"(?<=\d)[хX]", "x", s)
    s = re.sub(r"[хX](?=\d)", "x", s)

    # Convert lbs to kg (1 lb = 0.453592 kg, rounded to 0.5)
    def _lb_to_kg(m: re.Match) -> str:
        lbs = float(m.group(1))
        kg = round(lbs * 0.45359237 * 2) / 2
        return f"{kg}kg"

    s = RE_LBS.sub(_lb_to_kg, s)
    return s


def clean_name(name: str) -> str:
    """Clean exercise name by removing leading/trailing punctuation and title-casing the first word."""
    cleaned = name.strip(" \t\n\r.:,-–—;\"'()")
    if not cleaned:
        return ""
    # Capitalize first character, leave rest as user entered
    return cleaned[0].upper() + cleaned[1:]

from src.handlers.training.machines_confirm import dedupe_against_library

LIB = [{"display_name": "Brustpresse", "catalog_key": "chest_press_machine"}]


def test_dedupe_by_catalog_key():
    assert dedupe_against_library(["Жим в тренажёре", "Bizepsmaschine"], LIB) == ["Bizepsmaschine"]


def test_dedupe_by_name_and_repeats():
    assert dedupe_against_library(["brustpresse", "Latzug", "latzug"], LIB) == ["Latzug"]

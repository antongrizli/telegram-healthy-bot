"""Unit tests for training parser validating golden fixture cases and user workout input."""
from pathlib import Path
import yaml
import pytest

from src.services.training.dto import ParsedSet
from src.services.training.parser import parse_line, parse_live_shorthand, parse_text

FIXTURES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "training_parse_cases.yaml"


def load_test_cases():
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.parametrize("case", load_test_cases())
def test_golden_parse_cases(case):
    raw_input = case["in"]
    library = case.get("library")
    history_raw = case.get("history", {})

    # Transform history_raw into ExerciseHistory format if needed
    history_index = {}
    for ex_key, data in history_raw.items():
        w_sets = [ParsedSet(**s) for s in data.get("working_sets", [])]
        history_index[ex_key.lower()] = {
            "working_sets": w_sets,
            "top_weight": max((s.weight_kg or 0 for s in w_sets), default=0.0),
        }

    items = parse_line(
        raw_input,
        line_no=1,
        library_names=library,
        history_index=history_index,
    )

    if case.get("status") == "ambiguous":
        assert len(items) == 1
        assert items[0].status == "ambiguous"
        assert len(items[0].options) >= 2
        return

    expected_out = case["out"]
    assert len(items) == len(expected_out), f"Count mismatch for {raw_input}: got {[i.name for i in items]}"

    for item, exp in zip(items, expected_out):
        assert exp["name"].lower() in item.name.lower() or item.name.lower() in exp["name"].lower()
        if "kind" in exp:
            assert item.kind == exp["kind"]
        if "sets_count" in exp:
            assert len(item.sets) == exp["sets_count"]
        if "reps" in exp:
            assert item.sets[0].reps == exp["reps"]
        if "weight" in exp:
            assert float(item.sets[0].weight_kg) == pytest.approx(exp["weight"])
        if "added_weight" in exp:
            assert float(item.sets[0].added_weight_kg) == pytest.approx(exp["added_weight"])
        if "duration_sec" in exp:
            assert item.sets[0].duration_sec == exp["duration_sec"]
        if "distance_m" in exp:
            assert item.sets[0].distance_m == exp["distance_m"]
        if "note" in exp:
            assert exp["note"].lower() in (item.note or "").lower()
        if "sets_detail" in exp:
            for s, (exp_w, exp_r) in zip(item.sets, exp["sets_detail"]):
                assert float(s.weight_kg) == pytest.approx(exp_w)
                assert s.reps == exp_r


def test_user_example_seven_lines_gives_eight_exercises():
    """Test the exact multi-line input from Section 3.6 of the specification."""
    input_text = """
Ellipsentrainer 20 минут кросс тренинг
Brustpresse 35кг x 4 подхода по 15 раз
Latzug 40кг 3 подхода по 15 раз
Rudermaschine 45 кг 3 подхода по 12 раз
Beinstrecker 40 кг 3 подхода по 12 раз
Abduktion и  Adduktion 40 kg x 3 x 15
Bizepsmaschine 15x3x15kg.
"""
    items = parse_text(input_text, library_names=["Abduktion", "Adduktion"])
    assert len(items) == 8, f"Expected 8 items, got {len(items)}: {[i.name for i in items]}"

    names = [i.name for i in items]
    assert names == [
        "Ellipsentrainer",
        "Brustpresse",
        "Latzug",
        "Rudermaschine",
        "Beinstrecker",
        "Abduktion",
        "Adduktion",
        "Bizepsmaschine",
    ]

    # Verify each exercise sets and values
    # Ellipsentrainer: 20 min cardio, note "кросс тренинг"
    assert items[0].kind == "cardio"
    assert items[0].sets[0].duration_sec == 1200
    assert "кросс тренинг" in items[0].note.lower()

    # Brustpresse: 4x15 @ 35 kg
    assert len(items[1].sets) == 4
    assert items[1].sets[0].reps == 15
    assert items[1].sets[0].weight_kg == 35.0

    # Latzug: 3x15 @ 40 kg
    assert len(items[2].sets) == 3
    assert items[2].sets[0].reps == 15
    assert items[2].sets[0].weight_kg == 40.0

    # Rudermaschine: 3x12 @ 45 kg
    assert len(items[3].sets) == 3
    assert items[3].sets[0].reps == 12
    assert items[3].sets[0].weight_kg == 45.0

    # Beinstrecker: 3x12 @ 40 kg
    assert len(items[4].sets) == 3
    assert items[4].sets[0].reps == 12
    assert items[4].sets[0].weight_kg == 40.0

    # Abduktion: 3x15 @ 40 kg
    assert len(items[5].sets) == 3
    assert items[5].sets[0].reps == 15
    assert items[5].sets[0].weight_kg == 40.0

    # Adduktion: 3x15 @ 40 kg
    assert len(items[6].sets) == 3
    assert items[6].sets[0].reps == 15
    assert items[6].sets[0].weight_kg == 40.0

    # Bizepsmaschine: 3x15 @ 15 kg
    assert len(items[7].sets) == 3
    assert items[7].sets[0].reps == 15
    assert items[7].sets[0].weight_kg == 15.0


def test_live_shorthand():
    from src.services.training.dto import Target

    target = Target(weight=42.5, reps=12)
    # Shorthand 1: '45 10'
    res1 = parse_live_shorthand("45 10", target)
    assert len(res1) == 1
    assert res1[0].weight_kg == 45.0
    assert res1[0].reps == 10

    # Shorthand 2: '12' (reps with current weight)
    res2 = parse_live_shorthand("12", target)
    assert len(res2) == 1
    assert res2[0].weight_kg == 42.5
    assert res2[0].reps == 12

    # Shorthand 3: '40x10 40x8'
    res3 = parse_live_shorthand("40x10 40x8", target)
    assert len(res3) == 2
    assert res3[0].weight_kg == 40.0
    assert res3[0].reps == 10
    assert res3[1].weight_kg == 40.0
    assert res3[1].reps == 8


def test_conversational_russian_formats():
    """Common colloquial formats: weight at end, missing reps word, bodyweight sets, comma reps, single set."""
    # Weight at end of sets x reps phrase
    items1 = parse_line("Жим лежа 4 подхода по 15 раз 35кг")
    assert len(items1) == 1 and items1[0].status == "ok"
    assert items1[0].name == "Жим лежа"
    assert len(items1[0].sets) == 4
    assert items1[0].sets[0].weight_kg == 35.0 and items1[0].sets[0].reps == 15

    # Omitted 'раз' after 'по 12'
    items2 = parse_line("Жим 50кг 3 подхода по 12")
    assert len(items2) == 1 and items2[0].status == "ok"
    assert len(items2[0].sets) == 3
    assert items2[0].sets[0].weight_kg == 50.0 and items2[0].sets[0].reps == 12

    # Bodyweight sets without weight
    items3 = parse_line("Отжимания 3 подхода по 15 раз")
    assert len(items3) == 1 and items3[0].status == "ok"
    assert items3[0].kind == "bodyweight"
    assert len(items3[0].sets) == 3 and items3[0].sets[0].reps == 15

    # Weight + comma-separated reps
    items4 = parse_line("Жим 20 кг: 10, 10, 8 повторений")
    assert len(items4) == 1 and items4[0].status == "ok"
    assert [(s.weight_kg, s.reps) for s in items4[0].sets] == [(20.0, 10), (20.0, 10), (20.0, 8)]

    # Single set with weight and reps word
    items5 = parse_line("Жим лежа 50 кг 10 раз")
    assert len(items5) == 1 and items5[0].status == "ok"
    assert len(items5[0].sets) == 1
    assert items5[0].sets[0].weight_kg == 50.0 and items5[0].sets[0].reps == 10

    # Bodyweight single set with reps word
    items6 = parse_line("Подтягивания 12 раз")
    assert len(items6) == 1 and items6[0].status == "ok"
    assert items6[0].kind == "bodyweight"
    assert len(items6[0].sets) == 1 and items6[0].sets[0].reps == 12

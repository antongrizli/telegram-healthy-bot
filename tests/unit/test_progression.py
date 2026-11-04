"""Unit tests for progression logic matching Section 11.3 specification table."""
import pytest

from src.services.training.dto import ExerciseHistory, ParsedSet
from src.services.training.progression import calculate_e1rm, next_target


def test_progression_table_from_spec():
    # 1. 3x12 @ 40, hypertrophy 8-12, feeling normal -> 42.5 x 8
    h1 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=40.0,
        working_sets=[ParsedSet(weight_kg=40.0, reps=12) for _ in range(3)],
        feeling="normal",
        days_ago=3,
    )
    t1 = next_target(h1, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t1.weight == 42.5
    assert t1.reps == 8

    # 2. 3x10 @ 40, 8-12, feeling normal -> 40 x 11
    h2 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=40.0,
        working_sets=[ParsedSet(weight_kg=40.0, reps=10) for _ in range(3)],
        feeling="normal",
        days_ago=3,
    )
    t2 = next_target(h2, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t2.weight == 40.0
    assert t2.reps == 11

    # 3. 3x10 @ 40, 8-12, feeling easy -> 40 x 12
    h3 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=40.0,
        working_sets=[ParsedSet(weight_kg=40.0, reps=10) for _ in range(3)],
        feeling="easy",
        days_ago=3,
    )
    t3 = next_target(h3, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t3.weight == 40.0
    assert t3.reps == 12

    # 4. 3x12 @ 45, 8-12, feeling hard -> 45 x 12 (держим)
    h4 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=45.0,
        working_sets=[ParsedSet(weight_kg=45.0, reps=12) for _ in range(3)],
        feeling="hard",
        days_ago=3,
    )
    t4 = next_target(h4, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t4.weight == 45.0
    assert t4.reps == 12

    # 5. 4x15 @ 35, 8-12, feeling normal -> 37.5 x 8 (all sets > hi)
    h5 = ExerciseHistory(
        exercise_id=1,
        name="Brustpresse",
        top_weight=35.0,
        working_sets=[ParsedSet(weight_kg=35.0, reps=15) for _ in range(4)],
        feeling="normal",
        days_ago=3,
    )
    t5 = next_target(h5, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t5.weight == 37.5
    assert t5.reps == 8

    # 6. 3x12 @ 40, 16 days ago -> 35 x 8 (-10%, step 2.5: 36 -> 35)
    h6 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=40.0,
        working_sets=[ParsedSet(weight_kg=40.0, reps=12) for _ in range(3)],
        feeling="normal",
        days_ago=16,
    )
    t6 = next_target(h6, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t6.weight == 35.0
    assert t6.reps == 8

    # 7. No history -> calibrate, 12 reps
    t7 = next_target(None, goal="hypertrophy", energy="normal", weight_step=2.5)
    assert t7.calibrate is True
    assert t7.reps == 12
    assert t7.weight is None

    # 8. Deload -> -10% weight, low rep range, flag="deload"
    h8 = ExerciseHistory(
        exercise_id=1,
        name="Bench",
        top_weight=40.0,
        working_sets=[ParsedSet(weight_kg=40.0, reps=10) for _ in range(3)],
        feeling="normal",
        days_ago=3,
    )
    t8 = next_target(h8, goal="hypertrophy", is_deload=True, weight_step=2.5)
    assert t8.weight == 35.0
    assert t8.reps == 8
    assert t8.flag == "deload"


def test_e1rm_calculation():
    # Epley: w * (1 + reps / 30)
    # 100kg x 10 reps = 100 * (1 + 10/30) = 133.33
    assert calculate_e1rm(100.0, 10) == pytest.approx(133.33, 0.01)
    # 60kg x 1 rep = 60 * (1 + 1/30) = 62.0
    assert calculate_e1rm(60.0, 1) == pytest.approx(62.0, 0.01)

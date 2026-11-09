from src.services.training.dto import Focus
from src.services.training.focus import resolve_focus
from src.services.training.planner import build_plan, find_coverage_gaps

CHEST = {"id": 1, "display_name": "Brustpresse", "kind": "strength", "muscle_primary": "chest",
         "muscles_secondary": [], "pattern": "push_h", "weight_step": 2.5, "catalog_key": "chest_press_machine"}
BACK = {"id": 2, "display_name": "Latzug", "kind": "strength", "muscle_primary": "back_lats",
        "muscles_secondary": [], "pattern": "pull_v", "weight_step": 2.5, "catalog_key": "lat_pulldown"}
ELLIPTICAL = {"id": 3, "display_name": "Ellipsentrainer", "kind": "cardio", "muscle_primary": "cardio",
              "muscles_secondary": [], "pattern": "cardio", "weight_step": 1, "catalog_key": "elliptical_trainer"}


def test_plan_uses_only_library():
    focus = resolve_focus(selected_groups=["fullbody"], minutes=45, energy="normal")
    plan = build_plan(None, [CHEST, BACK], {}, focus, minutes=45)
    assert plan
    assert {i.name for i in plan} <= {"Brustpresse", "Latzug"}


def test_empty_library_gives_empty_plan():
    focus = resolve_focus(selected_groups=["arms"], minutes=45, energy="normal")
    assert build_plan(None, [], {}, focus, minutes=45) == []


def test_gaps_arms_missing():
    assert find_coverage_gaps([CHEST, BACK], ["arms"]) == ["arms"]


def test_gaps_fullbody_partial():
    assert find_coverage_gaps([CHEST, BACK], ["fullbody"]) == ["legs"]


def test_cardio_gap_by_kind():
    assert find_coverage_gaps([ELLIPTICAL], ["cardio"]) == []
    assert find_coverage_gaps([CHEST], ["cardio"]) == ["cardio"]

from src.services.training.dto import ParsedSet, PlanItem
from src.services.training.planner import resize_plan


def _item(name, n, kind="strength"):
    return PlanItem(name=name, kind=kind, working=[ParsedSet(weight_kg=30.0, reps=12) for _ in range(n)])


def test_resize_plus_adds_sets():
    out = resize_plan([_item("A", 3), _item("B", 3), _item("C", 3)], 15)
    assert [len(i.working) for i in out] == [4, 4, 3]


def test_resize_minus_never_below_two_sets():
    out = resize_plan([_item("A", 2), _item("B", 2), _item("C", 2)], -15)
    assert len(out) == 2 and all(len(i.working) == 2 for i in out)


def test_resize_skips_cardio():
    out = resize_plan([_item("Elliptical", 1, kind="cardio"), _item("A", 3)], 15)
    assert len(out[0].working) == 1 and len(out[1].working) == 4

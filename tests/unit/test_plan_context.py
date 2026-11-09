from types import SimpleNamespace

from src.services.training.plan_context import exercise_to_dict


def test_exercise_to_dict_keys():
    ex = SimpleNamespace(
        id=1, display_name="Latzug", kind="strength", muscle_primary="back_lats", muscles_secondary=[],
        pattern="pull_v", equipment=None, weight_step=2.5, min_weight=None, catalog_key="lat_pulldown",
        is_favorite=False,
    )
    assert set(exercise_to_dict(ex)) == {
        "id", "display_name", "kind", "muscle_primary", "muscles_secondary", "pattern",
        "equipment", "weight_step", "min_weight", "catalog_key", "is_favorite",
    }

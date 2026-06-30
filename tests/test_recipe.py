"""Recipe loading and validation tests."""

import pytest

from xbloom_ble.recipe import Recipe, RecipeError

VALID = {
    "name": "Example",
    "dose_g": 16,
    "grind": 62,
    "pours": [
        {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
        {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
    ],
}


def _with(**overrides):
    import copy

    d = copy.deepcopy(VALID)
    d.update(overrides)
    return d


def test_valid_recipe_passes():
    r = Recipe.from_dict(VALID)
    assert r.name == "Example"
    assert r.dose_g == 16
    assert r.total_water_ml == 150
    assert len(r.pours) == 2


def test_valid_with_correct_ratio_passes():
    # 16 g * 9.375 ratio = 150 ml total
    r = Recipe.from_dict(_with(ratio=9.375))
    assert r.ratio == 9.375


def test_ratio_mismatch_raises():
    with pytest.raises(RecipeError, match="Σpours"):
        Recipe.from_dict(_with(ratio=16))  # 16*16=256 != 150


def test_bad_pattern_raises():
    bad = _with(pours=[
        {"ml": 35, "temp_c": 90, "pattern": "zigzag", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
        {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
    ])
    with pytest.raises(RecipeError, match="pattern"):
        Recipe.from_dict(bad)


def test_agitation_only_with_spiral_raises():
    bad = _with(pours=[
        {"ml": 35, "temp_c": 90, "pattern": "center", "agitation": True,
         "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
        {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
    ])
    with pytest.raises(RecipeError):
        Recipe.from_dict(bad)


def test_ml_out_of_range_raises():
    with pytest.raises(RecipeError, match="ml"):
        Recipe.from_dict(_with(pours=[
            {"ml": 5000, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_temp_out_of_range_raises():
    with pytest.raises(RecipeError, match="temp"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 120, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_grind_out_of_range_raises():
    with pytest.raises(RecipeError, match="grind"):
        Recipe.from_dict(_with(grind=150))


def test_dose_out_of_range_raises():
    with pytest.raises(RecipeError, match="dose"):
        Recipe.from_dict(_with(dose_g=0))


def test_dose_over_app_max_raises():
    # 18 g is the observed app maximum; 20 g must be rejected.
    with pytest.raises(RecipeError, match="dose"):
        Recipe.from_dict(_with(dose_g=20))


def test_dose_at_app_max_passes():
    # 18 g is exactly the app maximum and must be accepted.
    r = Recipe.from_dict(_with(dose_g=18))
    assert r.dose_g == 18


def test_rpm_out_of_range_raises():
    with pytest.raises(RecipeError, match="rpm"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 200, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_flow_out_of_range_raises():
    with pytest.raises(RecipeError, match="flow"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 15.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_single_pour_raises():
    with pytest.raises(RecipeError, match="bloom"):
        Recipe.from_dict(_with(pours=[
            {"ml": 150, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_missing_pours_raises():
    with pytest.raises(RecipeError, match="pours"):
        Recipe.from_dict({"name": "x", "dose_g": 16, "grind": 62})


def test_missing_dose_raises():
    with pytest.raises(RecipeError, match="dose_g"):
        Recipe.from_dict({"name": "x", "grind": 62, "pours": VALID["pours"]})


def test_to_protocol_dict_shape():
    r = Recipe.from_dict(VALID)
    d = r.to_protocol_dict()
    assert d["dose"] == 16
    assert d["grind"] == 62
    assert d["pours"][0]["ml"] == 35
    assert d["pours"][0]["temp"] == 90
    assert d["pours"][0]["flow"] == 3.0


def test_from_yaml(tmp_path):
    import yaml

    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(VALID), encoding="utf-8")
    r = Recipe.from_yaml(p)
    assert r.name == "Example"


def test_empty_yaml_raises(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(RecipeError):
        Recipe.from_yaml(p)

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


def test_temp_above_range_raises():
    # pour temp cap is 95 °C; 96 must be rejected.
    with pytest.raises(RecipeError, match="temp"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 96, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_temp_below_range_raises():
    # pour temp floor is 40 °C; 39 must be rejected.
    with pytest.raises(RecipeError, match="temp"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 39, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_grind_above_range_raises():
    # grind range is 1–80; 81 must be rejected.
    with pytest.raises(RecipeError, match="grind"):
        Recipe.from_dict(_with(grind=81))


def test_grind_zero_is_no_grind():
    # grind 0 is the special "no-grind" value (brew pre-ground): valid, and flagged.
    r = Recipe.from_dict(_with(grind=0))
    assert r.grind == 0
    assert r.no_grind is True


def test_normal_grind_is_not_no_grind():
    assert Recipe.from_dict(VALID).no_grind is False


def test_grind_negative_still_raises():
    # only 0 is special; other out-of-range values (e.g. -1, 81) still fail.
    with pytest.raises(RecipeError, match="grind"):
        Recipe.from_dict(_with(grind=-1))


def test_dose_out_of_range_raises():
    with pytest.raises(RecipeError, match="dose"):
        Recipe.from_dict(_with(dose_g=0))


def test_dose_over_app_max_raises():
    # 18 g is the firm app maximum; 19 g must be rejected.
    with pytest.raises(RecipeError, match="dose"):
        Recipe.from_dict(_with(dose_g=19))


def test_dose_at_app_max_passes():
    # 18 g is exactly the app maximum and must be accepted.
    r = Recipe.from_dict(_with(dose_g=18))
    assert r.dose_g == 18


def test_rpm_out_of_range_raises():
    # 50 RPM is below the 60–120 band and not 0, so it must be rejected.
    with pytest.raises(RecipeError, match="rpm"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 50, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_rpm_in_band_passes():
    # 90 RPM is within the 60–120 band and must be accepted.
    r = Recipe.from_dict(_with(pours=[
        {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 90, "flow_ml_s": 3.0},
        {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 90, "flow_ml_s": 3.0},
    ]))
    assert r.pours[0].rpm == 90


def test_rpm_zero_with_center_pour_passes():
    # rpm 0 (no agitation) is valid specifically for a center pour.
    r = Recipe.from_dict(_with(pours=[
        {"ml": 35, "temp_c": 90, "pattern": "center", "pause_s": 40, "rpm": 0, "flow_ml_s": 3.0},
        {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
    ]))
    assert r.pours[0].rpm == 0


def test_rpm_zero_with_non_center_pour_raises():
    # rpm 0 is only allowed for center pours; a spiral pour must be 60–120.
    with pytest.raises(RecipeError, match="rpm"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 0, "flow_ml_s": 3.0},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_flow_above_range_raises():
    # flow range is 3.0–3.5; 3.6 must be rejected.
    with pytest.raises(RecipeError, match="flow"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 3.6},
            {"ml": 115, "temp_c": 90, "pattern": "spiral", "pause_s": 5, "rpm": 100, "flow_ml_s": 3.0},
        ]))


def test_flow_below_range_raises():
    # flow range is 3.0–3.5; 2.9 must be rejected.
    with pytest.raises(RecipeError, match="flow"):
        Recipe.from_dict(_with(pours=[
            {"ml": 35, "temp_c": 90, "pattern": "spiral", "pause_s": 40, "rpm": 100, "flow_ml_s": 2.9},
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


# ── enriched brew-level metadata (optional, never sent to the machine) ──────
ENRICHED = {
    "name": "Iced Fireworks",
    "dose_g": 15,
    "grind": 58,
    "ratio": 10.0,
    "kind": "custom",
    "dripper": "Omni",
    "water_ml": 150,
    "hot_water_ml": 150,
    "ice_g": 85,
    "time": "~2:00",
    "note": "strawberry-forward; ground finer as it aged",
    "pours": [
        {"label": "Bloom", "ml": 40, "temp_c": 92, "pattern": "spiral",
         "pause_s": 45, "rpm": 120, "flow_ml_s": 3.0, "agitation": True},
        {"label": "Pour 1", "ml": 110, "temp_c": 91, "pattern": "spiral",
         "pause_s": 5, "rpm": 120, "flow_ml_s": 3.0},
    ],
}


def test_enriched_fields_parse():
    r = Recipe.from_dict(ENRICHED)
    assert r.kind == "custom" and r.dripper == "Omni"
    assert r.water_ml == 150 and r.hot_water_ml == 150 and r.ice_g == 85
    assert r.time == "~2:00" and r.note.startswith("strawberry")
    assert r.pours[0].label == "Bloom" and r.pours[1].label == "Pour 1"


def test_enriched_metadata_ignored_by_protocol():
    """Metadata must never leak into the machine payload."""
    proto = Recipe.from_dict(ENRICHED).to_protocol_dict()
    assert set(proto) == {"dose", "grind", "stage_temps", "pours"}
    for p in proto["pours"]:
        assert "label" not in p          # pour labels are informational only


def test_enriched_roundtrips_through_to_dict():
    r = Recipe.from_dict(ENRICHED)
    again = Recipe.from_dict(r.to_dict())
    assert again.to_dict() == r.to_dict()
    d = r.to_dict()
    assert d["kind"] == "custom" and d["note"].startswith("strawberry")
    assert d["pours"][0]["label"] == "Bloom"


def test_plain_recipe_omits_absent_metadata():
    """A recipe with no metadata serialises without the optional keys (back-compat)."""
    d = Recipe.from_dict(VALID).to_dict()
    for k in ("kind", "dripper", "water_ml", "hot_water_ml", "ice_g", "time", "note"):
        assert k not in d
    assert "label" not in d["pours"][0]


def test_negative_water_ml_raises():
    with pytest.raises(RecipeError):
        Recipe.from_dict(_with(water_ml=-5))

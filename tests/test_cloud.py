"""Cloud sync / AUTO-prefix safety tests.

These exercise the *managed recipe* logic — the guarantee that the tool only ever
updates or deletes recipes it created (named ``AUTO …``) and never touches the
user's own recipes. The network layer is stubbed; no real API calls are made.
"""

import pytest

from xbloom_ble.cloud import MANAGED_PREFIX, XBloomCloud, XBloomCloudError, _is_managed


@pytest.fixture
def client():
    """A logged-in-looking client with a fake account and stubbed writes."""
    c = XBloomCloud(email="x@y", password="z")
    c.token = "tok"
    c.member_id = 1
    # Fake account: one hand-made recipe + one tool-owned (AUTO) recipe.
    account = [
        {"tableId": 111, "theName": "Savora"},
        {"tableId": 222, "theName": f"{MANAGED_PREFIX}Geisha"},
    ]
    c._account = account  # test bookkeeping
    c.recipe_items = lambda: list(account)  # type: ignore[assignment]
    c.calls = []  # type: ignore[attr-defined]

    def _add(cloud):
        c.calls.append(("add", cloud["theName"]))
        return {"result": "success", "tableId": 999}

    c.add_recipe = _add  # type: ignore[assignment]
    c._as_cloud_dict = staticmethod(lambda r, k: dict(r))  # type: ignore[assignment]

    # Real update/delete but with the network POST stubbed out.
    c._post = lambda *a, **k: {"result": "success"}  # type: ignore[assignment]
    return c


def test_is_managed():
    assert _is_managed("AUTO Geisha")
    assert not _is_managed("Geisha")
    assert not _is_managed(None)


def test_pattern_codes_center_spiral_ring():
    # Verified by reading back app-made recipes AND by pushing each code and
    # checking the app's pattern selector: center=1, spiral=2, ring/circular=3.
    # (Collapsing ring onto 2 made every ring pour show up as spiral in the app.)
    import json as _json

    from xbloom_ble.cloud import recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    rec = Recipe.from_dict({
        "name": "T", "dose_g": 16, "grind": 60,
        "pours": [{"ml": 40, "temp_c": 92, "pattern": "spiral", "rpm": 120},
                  {"ml": 100, "temp_c": 92, "pattern": "ring", "rpm": 120},
                  {"ml": 60, "temp_c": 92, "pattern": "center", "rpm": 0}],
    })
    cloud = recipe_to_cloud(rec, cup_type="xdripper")
    patterns = [p["pattern"] for p in _json.loads(cloud["pourDataJSONStr"])]
    assert patterns == [2, 3, 1]


def test_recipe_from_cloud_decodes_all_three_patterns():
    # Cloud -> recipe must round-trip ring (3) instead of flattening it to spiral.
    from xbloom_ble.cloud import recipe_from_cloud

    payload = {
        "theName": "T", "dose": 16.0, "grinderSize": 60.0, "rpm": 120, "isSetGrinderSize": 1,
        "pourList": [
            {"volume": 40.0, "temperature": 92.0, "pattern": 2, "pausing": 20, "flowRate": 3.5,
             "isEnableVibrationBefore": 2, "isEnableVibrationAfter": 1},
            {"volume": 100.0, "temperature": 92.0, "pattern": 3, "pausing": 10, "flowRate": 3.5,
             "isEnableVibrationBefore": 2, "isEnableVibrationAfter": 2},
            {"volume": 60.0, "temperature": 92.0, "pattern": 1, "pausing": 1, "flowRate": 3.0,
             "isEnableVibrationBefore": 2, "isEnableVibrationAfter": 2},
        ],
    }
    rec = recipe_from_cloud(payload)
    assert [p.pattern for p in rec.pours] == ["spiral", "ring", "center"]
    assert [p.agitation for p in rec.pours] == [True, False, False]


def test_no_grind_sets_grinder_off_in_cloud():
    # A no-grind recipe (grind 0, pre-ground) must map to isSetGrinderSize=2 (off);
    # a normal recipe stays isSetGrinderSize=1 (on). (1=on, 2=off.)
    from xbloom_ble.cloud import recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    pours = [{"ml": 40, "temp_c": 92, "pattern": "spiral", "rpm": 120},
             {"ml": 200, "temp_c": 92, "pattern": "spiral", "rpm": 120}]
    off = recipe_to_cloud(Recipe.from_dict({"name": "T", "dose_g": 16, "grind": 0, "pours": pours}),
                          cup_type="xdripper")
    on = recipe_to_cloud(Recipe.from_dict({"name": "T", "dose_g": 16, "grind": 60, "pours": pours}),
                         cup_type="xdripper")
    assert off["isSetGrinderSize"] == 2       # grinder off
    assert "grinderSize" not in off           # OMITTED (not 0) — else the app shows a literal "0"
    assert on["isSetGrinderSize"] == 1        # grinder on
    assert on["grinderSize"] == 60.0          # normal recipe still carries the grind size


def test_agitation_maps_to_vibration_after():
    # A pour's agitation ("agitate after this pour", e.g. after-bloom) must encode
    # as isEnableVibrationAfter=1, NOT ...Before (which was a bug).
    import json as _json

    from xbloom_ble.cloud import recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    rec = Recipe.from_dict({
        "name": "T", "dose_g": 16, "grind": 60,
        "pours": [{"ml": 40, "temp_c": 92, "pattern": "spiral", "agitation": True, "rpm": 120},
                  {"ml": 100, "temp_c": 92, "pattern": "spiral", "rpm": 120}],
    })
    bloom = _json.loads(recipe_to_cloud(rec, cup_type="xdripper")["pourDataJSONStr"])[0]
    assert bloom["isEnableVibrationAfter"] == 1   # on
    assert bloom["isEnableVibrationBefore"] == 2  # off


def test_agitation_before_maps_to_vibration_before():
    # The app's "vibration before" toggle (level the bed before the bloom) is a
    # separate cloud field. Both directions must carry it.
    import json as _json

    from xbloom_ble.cloud import recipe_from_cloud, recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    rec = Recipe.from_dict({
        "name": "T", "dose_g": 16, "grind": 60,
        "pours": [{"ml": 40, "temp_c": 92, "pattern": "spiral", "agitation_before": True, "rpm": 120},
                  {"ml": 100, "temp_c": 92, "pattern": "spiral", "agitation": True, "rpm": 120}],
    })
    pours = _json.loads(recipe_to_cloud(rec, cup_type="xdripper")["pourDataJSONStr"])
    assert (pours[0]["isEnableVibrationBefore"], pours[0]["isEnableVibrationAfter"]) == (1, 2)
    assert (pours[1]["isEnableVibrationBefore"], pours[1]["isEnableVibrationAfter"]) == (2, 1)

    back = recipe_from_cloud({
        "theName": "T", "dose": 16.0, "grinderSize": 60.0, "rpm": 120, "isSetGrinderSize": 1,
        "pourList": [
            {"volume": 40.0, "temperature": 92.0, "pattern": 2, "pausing": 20, "flowRate": 3.5,
             "isEnableVibrationBefore": 1, "isEnableVibrationAfter": 2},
            {"volume": 100.0, "temperature": 92.0, "pattern": 2, "pausing": 5, "flowRate": 3.5,
             "isEnableVibrationBefore": 2, "isEnableVibrationAfter": 1},
        ],
    })
    assert [(p.agitation_before, p.agitation) for p in back.pours] == [(True, False), (False, True)]


def test_sync_new_recipe_adds_with_prefix(client):
    _, action = client.sync_recipe({"theName": "Kolumbia Decaf"})
    assert action == "added"
    assert client.calls[-1] == ("add", f"{MANAGED_PREFIX}Kolumbia Decaf")


def test_sync_existing_managed_updates_in_place(client):
    resp, action = client.sync_recipe({"theName": "Geisha"})  # matches AUTO Geisha
    assert action == "updated"
    # updated the AUTO recipe (222), never the user's Savora (111)


def test_sync_never_prefixes_twice(client):
    _, action = client.sync_recipe({"theName": "AUTO Geisha"})
    assert action == "updated"  # already prefixed → matched, not double-prefixed


def test_update_refuses_unmanaged(client):
    with pytest.raises(XBloomCloudError, match="only recipes named"):
        client.update_recipe(111, {"theName": "hijack"})  # 111 = user's Savora


def test_delete_refuses_unmanaged(client):
    with pytest.raises(XBloomCloudError, match="only recipes named"):
        client.delete_recipe(111)


def test_update_allows_managed(client):
    # 222 is AUTO Geisha → allowed
    assert client.update_recipe(222, {"theName": f"{MANAGED_PREFIX}Geisha"})["result"] == "success"


def test_delete_unknown_id_raises(client):
    with pytest.raises(XBloomCloudError, match="not found"):
        client.delete_recipe(55555)


def test_prune_keeps_listed_and_protects_user_recipes(client):
    # Keep 'Geisha' (the AUTO one); nothing else managed exists → nothing deleted,
    # and the user's Savora is never a candidate.
    deleted = client.prune_managed(["Geisha"])
    assert deleted == []


def test_prune_removes_stale_managed(client):
    # Keep an empty set → the one managed recipe (AUTO Geisha) is pruned; the
    # user's Savora is untouched.
    deleted = client.prune_managed([])
    assert deleted == [f"{MANAGED_PREFIX}Geisha"]
    assert "Savora" not in deleted


def test_app_ring_pour_with_vibration_after_still_validates():
    # Verbatim from the PR #20 review: an app recipe with a ring bloom + "vibration
    # after" must import AND validate. Extended: ring is kept (not flattened to
    # spiral), both toggles survive, and a YAML reload + cloud re-export carry
    # pattern=3 and both vibration flags = 1.
    import json as _json

    import yaml

    from xbloom_ble.cloud import recipe_from_cloud, recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    rec = recipe_from_cloud({
        "theName": "T", "dose": 16.0, "grinderSize": 60.0, "rpm": 120, "isSetGrinderSize": 1,
        "pourList": [
            {"volume": 40.0, "temperature": 92.0, "pattern": 3, "pausing": 20, "flowRate": 3.5,
             "isEnableVibrationBefore": 1, "isEnableVibrationAfter": 1},
            {"volume": 100.0, "temperature": 92.0, "pattern": 2, "pausing": 5, "flowRate": 3.5,
             "isEnableVibrationBefore": 2, "isEnableVibrationAfter": 2},
        ],
    })
    rec.validate()
    assert rec.pours[0].pattern == "ring"
    assert rec.pours[0].agitation is True and rec.pours[0].agitation_before is True

    reloaded = Recipe.from_yaml_text(yaml.safe_dump(rec.to_dict(), allow_unicode=True))
    reloaded.validate()
    assert reloaded.pours[0].pattern == "ring"
    assert reloaded.pours[0].agitation is True and reloaded.pours[0].agitation_before is True

    pours = _json.loads(recipe_to_cloud(reloaded, cup_type="xdripper")["pourDataJSONStr"])
    assert pours[0]["pattern"] == 3
    assert (pours[0]["isEnableVibrationBefore"], pours[0]["isEnableVibrationAfter"]) == (1, 1)
    assert pours[1]["pattern"] == 2
    assert (pours[1]["isEnableVibrationBefore"], pours[1]["isEnableVibrationAfter"]) == (2, 2)

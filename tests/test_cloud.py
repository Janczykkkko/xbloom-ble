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


def test_spiral_maps_to_pattern_2():
    # Verified against real app-made recipes: spiral/ring pours encode as cloud
    # pattern code 2 (an earlier port wrongly used 3).
    import json as _json

    from xbloom_ble.cloud import recipe_to_cloud
    from xbloom_ble.recipe import Recipe

    rec = Recipe.from_dict({
        "name": "T", "dose_g": 16, "grind": 60,
        "pours": [{"ml": 40, "temp_c": 92, "pattern": "spiral", "rpm": 120},
                  {"ml": 100, "temp_c": 92, "pattern": "ring", "rpm": 120}],
    })
    cloud = recipe_to_cloud(rec, cup_type="xdripper")
    patterns = [p["pattern"] for p in _json.loads(cloud["pourDataJSONStr"])]
    assert patterns == [2, 2]


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

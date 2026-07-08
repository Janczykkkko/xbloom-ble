"""CLI tests — drive main([...]) with BLE/cloud mocked out (no hardware, no network)."""

from __future__ import annotations

import pytest

from xbloom_ble import cli
from xbloom_ble.telemetry import StatusEvent

VALID = """
name: Test Recipe
dose_g: 16
grind: 60
ratio: 15
pours:
  - {ml: 40, temp_c: 92, pattern: spiral, pause_s: 30, rpm: 100, flow_ml_s: 3.0}
  - {ml: 200, temp_c: 92, pattern: spiral, pause_s: 5, rpm: 100, flow_ml_s: 3.0}
"""


@pytest.fixture
def recipe_file(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(VALID)
    return str(p)


class _Dev:
    def __init__(self, address="AA:BB:CC:DD:EE:FF", name="XBLOOM-1234"):
        self.address = address
        self.name = name


class FakeClient:
    """Stand-in for XBloomClient — records calls, streams a tiny telemetry burst."""

    instances: list[FakeClient] = []

    def __init__(self, address, **kw):
        self.address = address
        self.started = False
        self.saved = None
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def load_recipe(self, recipe, **kw):
        return StatusEvent(state=0x1F, state_name="armed", raw=b"", water_g=0.0, coffee_g=0.0)

    async def start(self, **kw):
        self.started = True
        return StatusEvent(state=0x3B, state_name="brewing", raw=b"", water_g=0.0, coffee_g=16.0)

    async def save_slots(self, recipes, **kw):
        self.saved = list(recipes)

    async def stream_telemetry(self, on_event, duration=300.0, **kw):
        on_event(StatusEvent(state=0x3B, state_name="brewing", raw=b"", water_g=120.0, coffee_g=90.0))
        on_event(StatusEvent(state=0x41, state_name="complete", raw=b"", water_g=240.0, coffee_g=210.0))


@pytest.fixture
def ble(monkeypatch, tmp_path):
    """Patch the BLE seam (scan + XBloomClient) and run in tmp (telemetry log lands there)."""
    FakeClient.instances = []

    async def fake_scan(timeout=8.0):
        return [_Dev()]

    monkeypatch.setattr("xbloom_ble.client.scan", fake_scan)
    monkeypatch.setattr("xbloom_ble.client.XBloomClient", FakeClient)
    monkeypatch.chdir(tmp_path)
    return FakeClient


# ── validate ────────────────────────────────────────────────────────
def test_validate_ok(recipe_file, capsys):
    assert cli.main(["validate", recipe_file]) == 0
    assert "OK:" in capsys.readouterr().out


def test_validate_invalid(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: X\ndose_g: 99\ngrind: 60\npours: []\n")
    assert cli.main(["validate", str(bad)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_validate_missing_file(capsys):
    assert cli.main(["validate", "/no/such/recipe.yaml"]) == 2
    assert "not found" in capsys.readouterr().out


# ── scan ────────────────────────────────────────────────────────────
def test_scan_found(ble, capsys):
    assert cli.main(["scan"]) == 0
    assert "Found 1 machine" in capsys.readouterr().out


def test_scan_none(monkeypatch, capsys):
    async def empty(timeout=8.0):
        return []
    monkeypatch.setattr("xbloom_ble.client.scan", empty)
    assert cli.main(["scan"]) == 1
    assert "No xBloom machines" in capsys.readouterr().out


# ── brew ────────────────────────────────────────────────────────────
def test_brew_load_only(ble, recipe_file, capsys):
    assert cli.main(["brew", recipe_file, "--address", "AA:BB:CC:DD:EE:FF"]) == 0
    out = capsys.readouterr().out
    assert "Loading recipe" in out and "Telemetry log saved" in out
    assert ble.instances[-1].started is False       # load-only never starts


def test_brew_start_flag(ble, recipe_file, capsys):
    assert cli.main(["brew", recipe_file, "--address", "AA:BB:CC:DD:EE:FF", "--start"]) == 0
    assert ble.instances[-1].started is True         # --start sends commit+start
    assert "Starting the brew remotely" in capsys.readouterr().out


def test_brew_scans_when_no_address(ble, recipe_file, capsys):
    assert cli.main(["brew", recipe_file]) == 0      # no --address → scans, finds the fake
    assert "scanning" in capsys.readouterr().out.lower()


def test_brew_no_machine(monkeypatch, recipe_file, capsys):
    async def empty(timeout=8.0):
        return []
    monkeypatch.setattr("xbloom_ble.client.scan", empty)
    assert cli.main(["brew", recipe_file]) == 2
    assert "no xBloom machine found" in capsys.readouterr().out


def test_brew_ble_error(monkeypatch, ble, recipe_file, capsys):
    class Boom(FakeClient):
        async def load_recipe(self, recipe, **kw):
            raise RuntimeError("GATT boom")
    monkeypatch.setattr("xbloom_ble.client.XBloomClient", Boom)
    assert cli.main(["brew", recipe_file, "--address", "AA:BB:CC:DD:EE:FF"]) == 3
    assert "ERROR: GATT boom" in capsys.readouterr().out


def test_brew_invalid_recipe(ble, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: X\ndose_g: 99\ngrind: 60\npours: []\n")
    assert cli.main(["brew", str(bad), "--address", "AA:BB:CC:DD:EE:FF"]) == 1


# ── save-slots ──────────────────────────────────────────────────────
def test_save_slots_ok(ble, recipe_file, capsys):
    rc = cli.main(["save-slots", recipe_file, recipe_file, recipe_file,
                   "--address", "AA:BB:CC:DD:EE:FF"])
    assert rc == 0
    assert ble.instances[-1].saved is not None and len(ble.instances[-1].saved) == 3
    assert "Presets stored" in capsys.readouterr().out


def test_save_slots_scale_off(ble, recipe_file):
    assert cli.main(["save-slots", recipe_file, recipe_file, recipe_file,
                     "--address", "AA:BB:CC:DD:EE:FF", "--scale-off", "C"]) == 0


def test_save_slots_bad_scale_off(ble, recipe_file, capsys):
    rc = cli.main(["save-slots", recipe_file, recipe_file, recipe_file,
                   "--address", "AA:BB:CC:DD:EE:FF", "--scale-off", "Z"])
    assert rc == 2 and "slot letters" in capsys.readouterr().out


def test_save_slots_ble_error(monkeypatch, ble, recipe_file, capsys):
    class Boom(FakeClient):
        async def save_slots(self, recipes, **kw):
            raise RuntimeError("RETRY")
    monkeypatch.setattr("xbloom_ble.client.XBloomClient", Boom)
    rc = cli.main(["save-slots", recipe_file, recipe_file, recipe_file,
                   "--address", "AA:BB:CC:DD:EE:FF"])
    assert rc == 3 and "RETRY" in capsys.readouterr().out


# ── cloud ───────────────────────────────────────────────────────────
class FakeCloud:
    def __init__(self, auth_path=None):
        pass

    def login(self, email, password):
        return {"member": {"tableId": 42}}

    def list_recipes(self, adapted_model=0):
        return {"list": [{"tableId": 1, "theName": "AUTO Foo"}, {"tableId": 2, "theName": "Mine"}]}

    def sync_recipe(self, recipe, cup_type="xdripper"):
        return {"tableId": 7}, "updated"

    def add_recipe(self, recipe, cup_type="xdripper"):
        return {"tableId": 8}

    def delete_recipe(self, rid):
        return {"result": "ok"}

    def fetch_public(self, share):
        return {"recipeVo": {"theName": "Shared", "dose": 15, "grandWater": 16, "grinderSize": 55}}


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr("xbloom_ble.cloud.XBloomCloud", FakeCloud)
    return FakeCloud


def test_cloud_login(cloud, capsys):
    assert cli.main(["cloud", "login", "--email", "a@b.c", "--password", "x"]) == 0
    assert "Logged in" in capsys.readouterr().out


def test_cloud_list(cloud, capsys):
    assert cli.main(["cloud", "list"]) == 0
    out = capsys.readouterr().out
    assert "AUTO Foo" in out and "*" in out


def test_cloud_sync(cloud, recipe_file, capsys):
    assert cli.main(["cloud", "sync", recipe_file]) == 0
    assert "Updated" in capsys.readouterr().out


def test_cloud_fetch(cloud, capsys):
    assert cli.main(["cloud", "fetch", "abc123"]) == 0
    assert "Shared" in capsys.readouterr().out


def test_cloud_delete(cloud, capsys):
    assert cli.main(["cloud", "delete", "7"]) == 0
    assert "Deleted" in capsys.readouterr().out


def test_cloud_error(monkeypatch, capsys):
    from xbloom_ble.cloud import XBloomCloudError

    class Boom(FakeCloud):
        def list_recipes(self, adapted_model=0):
            raise XBloomCloudError("not logged in")
    monkeypatch.setattr("xbloom_ble.cloud.XBloomCloud", Boom)
    assert cli.main(["cloud", "list"]) == 3
    assert "ERROR" in capsys.readouterr().out


# ── tui dispatch + parser ───────────────────────────────────────────
def test_tui_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("xbloom_ble.tui.run_tui", lambda **kw: called.update(kw) or 0)
    assert cli.main(["tui", "--demo", "--recipes", "recipes"]) == 0
    assert called["demo"] is True and called["recipes_dir"] == "recipes"


def test_no_command_launches_tui(monkeypatch):
    monkeypatch.setattr("xbloom_ble.tui.run_tui", lambda **kw: 0)
    assert cli.main([]) == 0


def test_version_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_build_parser_brew_flags():
    parser = cli.build_parser()
    args = parser.parse_args(["brew", "r.yaml", "--start", "--address", "AA:BB:CC:DD:EE:FF"])
    assert args.command == "brew" and args.start is True

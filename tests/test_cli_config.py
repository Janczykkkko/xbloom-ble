"""CLI: init / config / doctor / cloud sync-all — sandboxed via XBLOOM_*_DIR."""

from __future__ import annotations

import types

import pytest

from xbloom_ble import cli, paths
from xbloom_ble import config as cfgmod

_RECIPE = (
    "name: {name}\ndose_g: 16\ngrind: 55\nratio: 15\n"
    "pours:\n  - {{ml: 40, temp_c: 92, pause_s: 30, rpm: 100}}\n"
    "  - {{ml: 200, temp_c: 92, pause_s: 5, rpm: 100}}\n"
)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("XBLOOM_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("XBLOOM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XBLOOM_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_init_noninteractive_writes_config(sandbox):
    # --no-scan/--no-cloud + explicit address → no prompts, just writes the config.
    assert cli.main(["init", "--address", "AA:BB:CC:DD:EE:FF", "--no-scan", "--no-cloud"]) == 0
    cfg = cfgmod.load()
    assert cfg.address == "AA:BB:CC:DD:EE:FF"
    assert cfgmod.exists()


def test_config_path_and_show(sandbox, capsys):
    cli.main(["init", "--address", "AA:BB", "--no-scan", "--no-cloud"])
    capsys.readouterr()
    assert cli.main(["config", "path"]) == 0
    assert "config.yaml" in capsys.readouterr().out
    assert cli.main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "AA:BB" in out and "recipes dir" in out and "cloud token" in out


def test_doctor_runs(sandbox, capsys):
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "xbloom doctor" in out and "recipe store writable" in out
    assert rc in (0, 1)


def test_cloud_sync_all_overwrites_and_warns(sandbox, capsys):
    rdir = paths.recipes_dir()
    rdir.mkdir(parents=True)
    (rdir / "a.yaml").write_text(_RECIPE.format(name="Alpha"))
    (rdir / "b.yaml").write_text(_RECIPE.format(name="Beta"))

    calls = []

    class FakeClient:
        def sync_recipe(self, recipe, *, prefix="", **kw):
            calls.append((recipe.name, prefix))
            return {"tableId": 1}, ("updated" if recipe.name == "Alpha" else "added")

    args = types.SimpleNamespace(dir=None, cup="omni", managed=False)
    assert cli._cloud_sync_all(FakeClient(), args) == 0
    out = capsys.readouterr().out
    assert "overwrote existing 'Alpha'" in out
    assert "added 'Beta'" in out
    assert "1 added, 1 overwritten" in out
    assert all(prefix == "" for _, prefix in calls)   # own-name (overwrite) mode, no prefix


def test_cloud_sync_all_managed_uses_prefix(sandbox, capsys):
    rdir = paths.recipes_dir()
    rdir.mkdir(parents=True)
    (rdir / "a.yaml").write_text(_RECIPE.format(name="Alpha"))

    seen = []

    class FakeClient:
        def sync_recipe(self, recipe, *, prefix="", **kw):
            seen.append(prefix)
            return {"tableId": 2}, "added"

    args = types.SimpleNamespace(dir=None, cup="omni", managed=True)
    assert cli._cloud_sync_all(FakeClient(), args) == 0
    assert seen == ["AUTO "]   # --managed → safe prefix


def test_cloud_sync_all_empty_dir(sandbox, capsys):
    args = types.SimpleNamespace(dir=str(sandbox / "empty"), cup="xdripper", managed=False)
    (sandbox / "empty").mkdir()
    assert cli._cloud_sync_all(object(), args) == 0
    assert "No valid recipes" in capsys.readouterr().out


def test_cloud_cup_defaults_are_valid():
    # 'omni' is NOT a valid xBloom cup type — the API only accepts other/tea/xdripper/xpod.
    # Guard the defaults so sync-all/sync/add-recipe don't ship a bad one again.
    parser = cli.build_parser()
    valid = {"other", "tea", "xdripper", "xpod"}
    for argv in (["cloud", "sync-all"], ["cloud", "sync", "r.yaml"], ["cloud", "add-recipe", "r.yaml"]):
        assert parser.parse_args(argv).cup in valid, argv

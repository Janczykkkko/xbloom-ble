"""Paths resolution + config round-trip."""

from __future__ import annotations

import os
import stat

import pytest

from xbloom_ble import config as cfgmod
from xbloom_ble import paths


def test_env_overrides_win_on_every_os(monkeypatch, tmp_path):
    monkeypatch.setenv("XBLOOM_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("XBLOOM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XBLOOM_STATE_DIR", str(tmp_path / "state"))
    assert paths.config_dir() == tmp_path / "cfg"
    assert paths.recipes_dir() == tmp_path / "data" / "recipes"
    assert paths.history_file() == tmp_path / "state" / "history.json"
    assert paths.slots_file() == tmp_path / "state" / "slots.json"
    assert paths.token_file() == tmp_path / "state" / "cloud-auth.json"


def test_platformdirs_default_when_unset(monkeypatch):
    for v in ("XBLOOM_CONFIG_DIR", "XBLOOM_DATA_DIR", "XBLOOM_STATE_DIR", "XBLOOM_HOME"):
        monkeypatch.delenv(v, raising=False)
    # Just assert it resolves to *something* namespaced to the app, without asserting the OS path.
    assert paths.APP in str(paths.config_dir()).lower() or paths.config_dir().name
    assert paths.recipes_dir().name == "recipes"


def test_xbloom_home_single_base(monkeypatch, tmp_path):
    for v in ("XBLOOM_CONFIG_DIR", "XBLOOM_DATA_DIR", "XBLOOM_STATE_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("XBLOOM_HOME", str(tmp_path / "home"))
    base = tmp_path / "home"
    assert paths.config_file() == base / "config.yaml"
    assert paths.recipes_dir() == base / "recipes"
    assert paths.history_file() == base / "history.json"
    assert paths.slots_file() == base / "slots.json"
    assert paths.token_file() == base / "cloud-auth.json"


def test_per_type_override_beats_xbloom_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XBLOOM_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XBLOOM_DATA_DIR", str(tmp_path / "data"))
    assert paths.recipes_dir() == tmp_path / "data" / "recipes"      # per-type wins
    assert paths.config_file() == tmp_path / "home" / "config.yaml"  # …others still under HOME


def test_macos_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("XBLOOM_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XBLOOM_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert paths.config_dir() == tmp_path / "xdg" / "xbloom"


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms")
def test_write_private_is_0600(tmp_path):
    p = paths.write_private(tmp_path / "sub" / "token.json", '{"token": "x"}')
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700
    assert p.read_text() == '{"token": "x"}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms")
def test_tighten_if_loose(tmp_path):
    p = tmp_path / "loose.json"
    p.write_text("x")
    os.chmod(p, 0o644)
    warn = paths.tighten_if_loose(p)
    assert warn and "0600" in warn
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert paths.tighten_if_loose(p) is None   # already tight → no warning


def test_config_roundtrip_and_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    assert cfgmod.load(path) == cfgmod.Config()          # missing file → defaults
    cfg = cfgmod.Config(address="AA:BB:CC:DD:EE:FF", cloud_email="a@b.co", scale_on=False)
    cfgmod.save(cfg, path)
    assert cfgmod.exists(path)
    back = cfgmod.load(path)
    assert back.address == "AA:BB:CC:DD:EE:FF"
    assert back.cloud_email == "a@b.co"
    assert back.scale_on is False


def test_config_preserves_unknown_keys(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("address: AA:BB\nfuture_key: 42\n")
    cfg = cfgmod.load(path)
    assert cfg.address == "AA:BB"
    assert cfg.extra == {"future_key": 42}
    cfgmod.save(cfg, path)                                # round-trips the unknown key
    assert cfgmod.load(path).extra == {"future_key": 42}


def test_config_recipes_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XBLOOM_DATA_DIR", str(tmp_path / "d"))
    assert cfgmod.Config().resolved_recipes_dir == tmp_path / "d" / "recipes"
    assert cfgmod.Config(recipes_dir="/tmp/mine").resolved_recipes_dir.as_posix() == "/tmp/mine"

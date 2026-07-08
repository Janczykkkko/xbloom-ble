"""Cross-platform persistent locations for xbloom-ble.

Resolves the **config / data / state** directories via ``platformdirs``, so files land in the
right OS-native place (XDG on Linux, ``~/Library`` on macOS, ``%APPDATA%``/``%LOCALAPPDATA%`` on
Windows). Two deviations make it behave like the CLI tools people already run:

* a per-type ``XBLOOM_CONFIG_DIR`` / ``XBLOOM_DATA_DIR`` / ``XBLOOM_STATE_DIR`` env override is
  honored first on every OS (the ``GH_CONFIG_DIR`` / ``CARGO_HOME`` convention); or a single
  ``XBLOOM_HOME`` roots **all** of config/data/state under one directory (so ``config.yaml``,
  ``recipes/``, ``history.json``, ``slots.json`` and the token all land there);
* on macOS, ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` / ``XDG_STATE_HOME`` are honored when set, so
  a terminal user who keeps everything under ``~/.config`` gets that instead of ``~/Library``.

What lives where:

* **config** — the user-editable ``config.yaml`` (saved machine address, defaults).
* **data**   — the recipe store (``recipes/``): app-created content the user wants to keep.
* **state**  — brew ``history.json``, the dial-preset ``slots.json``, and the cached cloud auth
  token (``cloud-auth.json``, 0600). "History/state that persists but isn't precious" per XDG.

``platformdirs`` does NOT create directories or set permissions — use :func:`ensure_dir` before
writing, and :func:`write_private` for the token.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import platformdirs

APP = "xbloom"


def _resolve(override_env: str, xdg_env: str, default_factory) -> Path:
    """Resolve one base dir. Precedence: the per-type ``XBLOOM_*_DIR`` override → the single-base
    ``XBLOOM_HOME`` (config/data/state all land under it) → ``XDG_*`` on macOS → the platform
    default. ``default_factory`` is a callable so ``platformdirs`` is only invoked when actually
    needed (calling it eagerly can raise on some Windows CI setups / a spoofed platform)."""
    override = os.environ.get(override_env)
    if override:
        return Path(override).expanduser()
    home = os.environ.get("XBLOOM_HOME")   # single base → config/data/state all land under it

    if home:
        return Path(home).expanduser()
    if sys.platform == "darwin":
        base = os.environ.get(xdg_env)
        if base:
            return Path(base).expanduser() / APP
    return default_factory()


def config_dir() -> Path:
    return _resolve("XBLOOM_CONFIG_DIR", "XDG_CONFIG_HOME",
                    lambda: Path(platformdirs.user_config_dir(APP, appauthor=False)))


def data_dir() -> Path:
    return _resolve("XBLOOM_DATA_DIR", "XDG_DATA_HOME",
                    lambda: Path(platformdirs.user_data_dir(APP, appauthor=False)))


def state_dir() -> Path:
    return _resolve("XBLOOM_STATE_DIR", "XDG_STATE_HOME",
                    lambda: Path(platformdirs.user_state_dir(APP, appauthor=False)))


def config_file() -> Path:
    return config_dir() / "config.yaml"


def recipes_dir() -> Path:
    return data_dir() / "recipes"


def history_file() -> Path:
    return state_dir() / "history.json"


def slots_file() -> Path:
    return state_dir() / "slots.json"


def token_file() -> Path:
    return state_dir() / "cloud-auth.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_private(path: Path, text: str) -> Path:
    """Write ``text`` with owner-only perms (0600 file, 0700 dir), never briefly world-readable.

    For the cached cloud token. On Windows ``chmod`` only toggles the read-only bit, but the file
    lands in the per-user profile dir which the OS already ACL-protects — an acceptable baseline
    for a low-stakes, revocable coffee-machine session token.
    """
    path = Path(path)
    ensure_dir(path.parent)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def tighten_if_loose(path: Path) -> str | None:
    """If ``path`` is group/other-accessible, chmod it back to 0600 and return a warning to log.

    Best-effort; a no-op where st_mode perms aren't meaningful (Windows) or the file is absent.
    """
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if stat.S_ISREG(mode) and (mode & 0o077):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return (f"{path} was accessible by other users (mode {stat.S_IMODE(mode):o}) — "
                f"tightened to 0600")
    return None

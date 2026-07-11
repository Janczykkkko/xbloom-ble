"""User config for xbloom-ble — a small YAML file the onboarding writes and the TUI/CLI read.

Holds the saved machine address, an optional recipes-dir override, and small defaults. YAML (not
TOML) on purpose: the package already depends on ``pyyaml`` (recipes are YAML), so there's one
config language and no extra dependency. The config is tool-written, so YAML's footguns don't bite.

The cached cloud auth **token** is stored SEPARATELY (state dir, 0600 — see
:func:`xbloom_ble.paths.token_file`); the password is NEVER stored, only the exchanged token.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import paths

_KNOWN = ("address", "recipes_dir", "cloud_email", "scale_on", "auto_connect")


@dataclass
class Config:
    address: str = ""            # saved BLE machine address — skip the scan on later launches
    recipes_dir: str = ""        # override for the recipe store (else paths.recipes_dir())
    cloud_email: str = ""        # remembered to pre-fill re-login prompts (NOT the password)
    scale_on: bool = True        # default: brew with the scale on
    auto_connect: bool = True    # connect on TUI launch and hold the link open (faster brews)
    # forward-compat: any unrecognised keys are preserved on round-trip, never dropped.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_recipes_dir(self) -> Path:
        return Path(self.recipes_dir).expanduser() if self.recipes_dir else paths.recipes_dir()


def load(path: Path | None = None) -> Config:
    """Read the config (or return defaults if missing/invalid — never raises)."""
    path = Path(path) if path is not None else paths.config_file()
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return Config()
    if not isinstance(data, dict):
        return Config()
    known = {k: data[k] for k in _KNOWN if k in data}
    extra = {k: v for k, v in data.items() if k not in _KNOWN}
    return Config(**known, extra=extra)


def save(cfg: Config, path: Path | None = None) -> Path:
    """Write the config as YAML (creating the config dir). Merges through unknown keys."""
    path = Path(path) if path is not None else paths.config_file()
    paths.ensure_dir(path.parent)
    body = {k: v for k, v in asdict(cfg).items() if k != "extra"}
    body.update(cfg.extra)
    path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True))
    return path


def exists(path: Path | None = None) -> bool:
    return (Path(path) if path is not None else paths.config_file()).exists()

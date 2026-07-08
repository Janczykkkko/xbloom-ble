# Roadmap

Rough direction for `xbloom-ble` — not commitments, and PRs on any of these are welcome.
See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

## Cross-platform

The library is developed and tested on **Linux (BlueZ)**. `bleak` also supports macOS
(CoreBluetooth) and Windows (WinRT), but those are unproven here.

- [x] Run the pure-Python test suite on **macOS + Windows** in CI (the `cross-platform`
      job — non-blocking for now).
- [ ] Make the cross-platform job **required** once it's reliably green.
- [ ] **Real hardware** brew tested on macOS and Windows (community help welcome — attach
      a scrubbed BLE capture; see [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md)).
- [ ] Document any platform-specific BLE quirks (permissions, adapter selection).

## First-run experience

Today a fresh user gets an empty recipes dir, no saved machine, and no config. Smooth this out:

- [ ] **Config file** (`~/.xbloom/config.toml`): saved machine address, recipes dir,
      default scale-on, units.
- [ ] **Pair once:** scan → pick machine → save the address (no re-scan every launch).
- [ ] **Seed recipes** on first run — bundle a few starters, and/or offer "import from your
      xBloom app account" (the cloud client already exists).
- [ ] **Friendly empty state** in the TUI ("No recipes yet — `n` to create, `i` to import").
- [ ] **Polish the recipe editor** — the grid layout is cramped/rough (misaligned labels,
      tiny per-pour cells); make it a cleaner, more legible form.

## Persistence

- [ ] Move history/slots/config to a **stable per-user data dir** (via `platformdirs`:
      `~/.local/share/xbloom/`, `~/Library/Application Support/xbloom/`, `%APPDATA%\xbloom\`)
      instead of deriving the location from the recipes-dir parent. Migrate existing files.

## Protocol / telemetry

- [x] Decode the live **water + coffee scale streams** (TYPE `0x4b` water / `0x15` coffee).
- [x] Complete a brew on the **`0x24` "coffee ready"** state (not cup-off).
- [ ] Confirm the finer pour phases from the command echoes (`0x3a`/`0x3b`/`0x3e`/`0x3f`/`0x40`)
      — e.g. surface "bloom / pour 1 / pour 2 / drain" from the `0x3e` pour counter.
- [ ] Captures/decodes for **other firmware versions** (this targets `V12.0D.500`).

## Nice-to-haves

- [ ] Home Assistant / ESPHome integration built on the pure `protocol`/`client` layers.
- [ ] Export brew history (CSV/JSON) and a per-brew extraction summary.

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

- [x] **Config file** (`config.yaml`): saved machine address, recipes-dir override, default
      scale-on. (`xbloom config show`/`path`.)
- [x] **Pair once:** `xbloom init` scans → pick machine → saves the address (later launches skip
      the scan).
- [x] **Cloud login in setup** — `xbloom init` (and `xbloom cloud login`) exchange email+password
      for a token cached `0600` in the state dir; the password is never stored.
- [x] **`xbloom doctor`** — checks config, dirs, deps, token perms (and `--scan` the machine).
- [ ] **Seed recipes** on first run — bundle a few starters, and/or offer "import from your
      xBloom app account" (the cloud client already exists).
- [ ] **Textual first-run wizard** — auto-offer `init` from the TUI when no config exists on a TTY
      (today `init` is CLI-only; the TUI runs zero-config). Friendly empty state ("`n` to create,
      `i` to import").
- [ ] **Polish the recipe editor** — the grid layout is cramped/rough (misaligned labels,
      tiny per-pour cells); make it a cleaner, more legible form.

## Persistence

- [x] **Hold the BLE connection open** for the TUI session (connect on launch + reuse across
      brews, transparent reconnect on drop, `o` connect/disconnect toggle, `auto_connect` config
      default + `--no-auto-connect`) — brews skip the per-brew connect + handshake.
- [x] Move recipes/history/slots/config/token to **stable per-user dirs** via `platformdirs`
      (config / data / state, `XBLOOM_*_DIR` overrides, XDG-on-macOS), instead of deriving from the
      recipes-dir parent. The cached cloud token migrates from the pre-2.2 `~/.config/xbloom-ble/`
      location. `--recipes DIR` still keeps slots/history adjacent (external-generator compat).

## Protocol / telemetry

- [x] Decode the live **water + coffee scale streams** (TYPE `0x4b` water / `0x15` coffee).
- [x] Complete a brew on the **`0x24` "coffee ready"** state (not cup-off).
- [ ] Confirm the finer pour phases from the command echoes (`0x3a`/`0x3b`/`0x3e`/`0x3f`/`0x40`)
      — e.g. surface "bloom / pour 1 / pour 2 / drain" from the `0x3e` pour counter.
- [ ] Captures/decodes for **other firmware versions** (this targets `V12.0D.500`).

## Nice-to-haves

- [ ] Home Assistant / ESPHome integration built on the pure `protocol`/`client` layers.
- [ ] Export brew history (CSV/JSON) and a per-brew extraction summary.

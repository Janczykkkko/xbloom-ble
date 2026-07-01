# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Cloud subsystem** (`xbloom_ble.cloud` + `xbloom cloud …`) — push recipes to
  your xBloom **mobile-app account** via the *unofficial* xBloom cloud REST API
  (separate from the BLE code; talks to `client-api.xbloom.com`, not the machine).
  `XBloomCloud` client with `login` / `add_recipe` / `list_recipes` /
  `update_recipe` / `delete_recipe` / `fetch_public`, plus a `recipe_to_cloud`
  mapper from this package's `Recipe`. Auth via `XBLOOM_EMAIL` / `XBLOOM_PASSWORD`
  (token cached to `~/.config/xbloom-ble/cloud-auth.json`). Request bodies are
  `Base64(RSA-1024 PKCS#1 v1.5(JSON))`, Hutool-chunked (117→128 B).
- `cryptography` as an optional `[cloud]` extra (imported lazily; BLE-only install
  stays lean).
- **Attribution:** the cloud REST mechanics (base URL, endpoints, the RSA-encrypted
  body scheme, static `skey`, and recipe/pour field schema) were ported from
  [`cryptofishbug/xbloom-recipe-cli`](https://github.com/cryptofishbug/xbloom-recipe-cli)
  (MIT) — its `xbloom_client.py` and `recipe_maker.py`. Cleaned up, typed, mapped
  onto this package's `Recipe`, with `update`/`delete` added on the same pattern.

## [0.2.0]

### Added
- **Machine info** (`xbloom info`) — decode serial / firmware / water level /
  units from the machine-info blob (`telemetry.parse_machine_info`).
- **Scale** — `xbloom scale read` (free), `tare`, and `units` (g/oz/ml).
- **Grinder** — `xbloom grind --size --dose` runs the grinder only (no brew).
- **FreeSolo pour** — `xbloom pour --ml --temp --flow --pattern` dispenses a
  single pour of hot water (handshake → bypass+dose → set-cup → tare → pour).
- **Easy-Mode presets** — `xbloom save-slot 1|2|3 <recipe>` writes a preset
  (command `11510`); no brew.
- **Opt-in brew start** — `xbloom start <recipe>` and `xbloom brew --start`: the
  explicit, loudly-warned full brew (`build_start_frames`). The default `brew`
  stays load-only.
- Frame builders + `XBloomClient` methods and byte-level tests for all of the
  above; new opcodes ported from and cross-validated against brAzzi64/xbloom-ble.

### Changed
- **Decode fix:** the `0x41` frame's tail byte is now understood and emitted as
  `round(ratio × 10)` — the brew **ratio** (1:16 → `0xa0`, 1:17 → `0xaa`) — not a
  hardcoded constant. Reconciled against brAzzi64's `grandWater` field and
  cross-checked to reproduce the captured tails from each run's `Σml / dose`.
- Safety framing: **`brew` is load-only by default**; lower-level controls and
  `start` are explicit, opt-in actions. The default load path still never emits a
  brew-start opcode.

## [0.1.0]

### Added
- Initial release: byte-exact xBloom Studio BLE protocol port, recipe model +
  YAML loader + validation, telemetry decoder, async `bleak` client, and the
  `xbloom` CLI (`scan` / `validate` / `brew`).
- Safety invariant: the tool only ever **loads** a recipe; the human approves
  the brew on the machine. No code path emits the `0x42` (commit) or `0x46`
  (start) opcodes.

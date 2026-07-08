# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/). Releases are generated automatically by
[python-semantic-release](https://python-semantic-release.readthedocs.io/) from the
Conventional-Commit history — new versions are inserted below.

<!-- version list -->

## v2.1.1 (2026-07-08)

### Bug Fixes

- **tui**: Complete brew on weight-plateau + fix history row selection
  ([#16](https://github.com/Janczykkkko/xbloom-ble/pull/16),
  [`1a3151c`](https://github.com/Janczykkkko/xbloom-ble/commit/1a3151c52bdd8b6067a8fbb4c6b309144adcbf7d))


## v2.1.0 (2026-07-08)

### Documentation

- Real terminal screenshots + README refresh
  ([#14](https://github.com/Janczykkkko/xbloom-ble/pull/14),
  [`7cec335`](https://github.com/Janczykkkko/xbloom-ble/commit/7cec3353a7c4050829f46159a5c956a49f8011b5))

### Features

- **tui**: Slim recipe editor + pattern selector + clone
  ([#15](https://github.com/Janczykkkko/xbloom-ble/pull/15),
  [`7adc26a`](https://github.com/Janczykkkko/xbloom-ble/commit/7adc26a2183e3a2c4333d5df24524e4be6abb92b))


## v2.0.0 (2026-07-08)

### Features

- Terminal UI, remote brew start/cancel, and richer recipe metadata
  ([#13](https://github.com/Janczykkkko/xbloom-ble/pull/13),
  [`4765bdd`](https://github.com/Janczykkkko/xbloom-ble/commit/4765bdd9a17a3277e7d44f88fb06a746c9a76fa0))


## v1.1.1 (2026-07-07)

### Bug Fixes

- Stage recipes correctly (ratio byte, handshake, no-grind opcode)
  ([#12](https://github.com/Janczykkkko/xbloom-ble/pull/12),
  [`1f7cfff`](https://github.com/Janczykkkko/xbloom-ble/commit/1f7cfff18d7f1df655348178603b5eb5edfd5757))


## v1.1.0 (2026-07-07)

### Features

- No-grind (brew pre-ground) recipe support
  ([#11](https://github.com/Janczykkkko/xbloom-ble/pull/11),
  [`c2ad31b`](https://github.com/Janczykkkko/xbloom-ble/commit/c2ad31b4084ecec06bce08eb9592fc4554bc3a11))


## v1.0.1 (2026-07-01)

### Bug Fixes

- Force Pro mode for slot writes (fixes RETRY in Auto mode)
  ([#8](https://github.com/Janczykkkko/xbloom-ble/pull/8),
  [`0f1e50b`](https://github.com/Janczykkkko/xbloom-ble/commit/0f1e50bce0edda8bc18097eb37961135696940fe))


## v1.0.0 (2026-07-01)

### Documentation

- Cache-bust the PyPI badges ([#5](https://github.com/Janczykkkko/xbloom-ble/pull/5),
  [`801639c`](https://github.com/Janczykkkko/xbloom-ble/commit/801639cdd17795dc2622e5c2ac1b30743a9d0961))

### Features

- Batch-program Auto-Mode dial presets (save-slots)
  ([#6](https://github.com/Janczykkkko/xbloom-ble/pull/6),
  [`b282051`](https://github.com/Janczykkkko/xbloom-ble/commit/b28205121c085178403dddd18db22fcecabf472e))

### Breaking Changes

- `save_slot()` / `xbloom save-slot` are replaced by `save_slots()` / `xbloom save-slots` (all three
  slots required).


## v0.2.0 (2026-07-01)

### Chores

- Tidy changelog and clean release-note rendering
  ([#3](https://github.com/Janczykkkko/xbloom-ble/pull/3),
  [`599c84c`](https://github.com/Janczykkkko/xbloom-ble/commit/599c84c599fbd3f7f3c353eb82c9b0bea40ef856))

### Features

- Load recipes from an http(s) URL ([#4](https://github.com/Janczykkkko/xbloom-ble/pull/4),
  [`620d814`](https://github.com/Janczykkkko/xbloom-ble/commit/620d814ee0daccf9080da9787e63da3a7189bb6c))


## v0.1.0 (2026-07-01)

First public release — unofficial Bluetooth LE control for the
[xBloom Studio](https://xbloom.com) pour-over coffee machine.

### Features

- Load recipes onto the machine over BLE — load-only; the human approves the brew on the machine
- Live brew telemetry streamed during a brew
- YAML recipe model + validation (with real xBloom Studio limits)
- Cloud sync (`xbloom cloud`, optional) — push recipes to your xBloom app account via the unofficial cloud API
- `xbloom` CLI (`scan` / `validate` / `brew` / `cloud`) and a fully documented, reverse-engineered protocol

### Bug Fixes

- Use write-without-response on `ffe1` plus a correct `ffe2` notification decoder — loading is verified on hardware
- Align recipe value ranges to xBloom Studio published specs

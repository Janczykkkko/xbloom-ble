# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/). Releases are generated automatically by
[python-semantic-release](https://python-semantic-release.readthedocs.io/) from the
Conventional-Commit history — new versions are inserted below.

<!-- version list -->

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

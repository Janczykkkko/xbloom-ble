# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/). Releases are generated automatically by
[python-semantic-release](https://python-semantic-release.readthedocs.io/) from the
Conventional-Commit history — new versions are inserted below.

<!-- version list -->

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

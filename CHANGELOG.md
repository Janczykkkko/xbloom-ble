# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Cloud recipe sync** (`xbloom cloud`, optional `xbloom-ble[cloud]` extra):
  push recipes to your xBloom **app account** via the unofficial cloud REST API
  (`login` / `sync` / `add-recipe` / `list` / `delete` / `fetch`). Ported from
  `cryptofishbug/xbloom-recipe-cli` (MIT).
- **Managed-recipe safety model.** Recipes created by the tool are named
  `AUTO <name>`; `sync` is idempotent (update-or-add by name), and `update`/
  `delete`/`prune` will **only ever** touch `AUTO …` recipes — recipes you made
  by hand are never modified or removed (enforced in code + tests).

### Fixed
- **`ffe1` writes now use a Write Command (write-without-response).** The machine
  rejects a Write Request (write-with-response) with GATT "Unlikely Error"; the
  vendor app only ever uses Write Commands on `ffe1`. This is what actually makes
  the load land on hardware (verified end-to-end: the machine arms and prompts).
- **Notification decoder rewritten for the real `ffe2` frame format**
  (`58 02 07 | TYPE | SUB | LEN(u32) | c1 | payload | crc`). The previous decoder
  assumed notifications shared the command-frame shape (`58 01 01`), which is
  wrong — commands and notifications use different frames. ACKs are now matched by
  the `TYPE` byte (the echoed command), and machine state is read from `0x57`
  status frames. Validated byte-for-byte against the captured notification stream.

### Added
- Initial release: byte-exact xBloom Studio BLE protocol port, recipe model +
  YAML loader + validation, telemetry decoder, async `bleak` client, and the
  `xbloom` CLI (`scan` / `validate` / `brew`).
- Safety invariant: the tool only ever **loads** a recipe; the human approves
  the brew on the machine. No code path emits the `0x42` (commit) or `0x46`
  (start) opcodes.

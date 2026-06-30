# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial release: byte-exact xBloom Studio BLE protocol port, recipe model +
  YAML loader + validation, telemetry decoder, async `bleak` client, and the
  `xbloom` CLI (`scan` / `validate` / `brew`).
- Safety invariant: the tool only ever **loads** a recipe; the human approves
  the brew on the machine. No code path emits the `0x42` (commit) or `0x46`
  (start) opcodes.

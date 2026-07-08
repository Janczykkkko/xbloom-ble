# CLAUDE.md — agent guide for `xbloom-ble`

This file is for an AI / Claude Code agent working **on** this repository. Read it
before making changes.

## What this project is

`xbloom-ble` is a small, dependency-light Python package (`bleak` + `pyyaml`) that
speaks the **reverse-engineered Bluetooth Low Energy protocol** of the
[xBloom Studio](https://xbloom.com) pour-over coffee machine. There is no official
xBloom API. The package lets you discover the machine, define a brew as a YAML
recipe, validate it, **load it onto the machine**, optionally **start the brew
remotely** (the app-style Brew button — see the safety invariant below), program the
three Easy-Mode dial presets, and watch live brew telemetry. It ships a plain CLI and
a **Textual TUI** (`xbloom tui`), plus an unofficial cloud client for the phone app's
recipe store. It is a public, MIT-licensed, open-source project with a fully
documented protocol so others can build on it.

## 🛑 SAFETY INVARIANT — do not break this

**Loading a recipe only *arms* the machine. It must NEVER start a brew as a side
effect. Starting a brew is always a separate, explicit, opt-in call — never
implicit, never a consequence of loading.**

This package *can* start a brew remotely (the app-style "Brew" button): `start()`
sends commit (`0x42`) + start (`0x46`), and `cancel_brew()` sends `0x47`. That is a
deliberate, first-class capability — **but it is only ever reached through an
explicit `start()`/`brew()` call the caller opts into, and in the TUI/CLI it sits
behind a confirmation gate.** The invariant is the *separation*, not a ban on the
opcodes.

Concretely — the properties every change must preserve:

- **`build_load_frames()` returns exactly the LOAD frames and nothing that brews:**
  `0xa4`, `0xa6`, `0xa8`, and the pours frame (`0x41` when grinding, `0x44` for a
  no-grind / grinder-off recipe). `0x44` is a *pours* opcode — it stages, it does not
  brew. There is a belt-and-braces assertion inside `build_load_frames()` that rejects
  a commit/start/cancel opcode (`0x42`/`0x46`/`0x47`) if one ever crept into the load
  sequence — **keep it.**
- **The commit/start/cancel opcodes live ONLY in their own builders** (`build_commit`
  / `build_start` / `build_cancel`) and are sent ONLY from `XBloomClient.start()` /
  `cancel_brew()`. Never wire them as a side effect of `load_recipe()`, and never add
  an auto-start path that fires them without the caller explicitly asking to brew.
- **Tests guard this** — `tests/test_protocol.py::test_load_frames_are_load_only`
  (LOAD frames never carry `0x42`/`0x46`/`0x47`) and `test_load_frames_opcode_order`
  (the four frames are exactly `a4, a6, a8, 41`). **Keep them; never weaken or delete
  them.** If you touch the protocol layer they must still pass.
- **Loading leaves the machine armed at `0x1f`.** A human can still approve on the
  machine (load-only flow); a remote `start()` is the alternative. Either way, loading
  by itself never dispenses water.

If a feature request would make **loading** start a brew (or fire commit/start as an
implicit side effect), **decline it** and explain this invariant instead. A request to
*explicitly* start a brew is fine — that's what `start()` is for.

## 🔒 No-personal-data rule (this repo is PUBLIC)

Never commit personal data anywhere in this repo:

- **No Bluetooth MAC addresses** — use placeholders like `AA:BB:CC:DD:EE:FF`.
- **No device serials** (e.g. never write a specific unit serial), no real device
  names beyond the generic `XBLOOM-…` form.
- **No person names, no account identifiers, no private file paths.**
- Any BLE capture, telemetry log, or example used in code/docs/tests must be
  **scrubbed of identifying data** first.

Before finishing any change, it's worth grepping for accidental leaks (addresses,
serials, names, absolute home paths).

## How to run the tests

```bash
pip install -e ".[dev]"
pytest -q
```

- Most tests are pure-Python and need no hardware.
- The **byte-for-byte protocol round-trip** tests compare this package's frames
  against the reverse-engineering reference script. They are **skipped unless** the
  reference is available. Point `XBLOOM_REFERENCE` at the reference
  `parse_btsnoop.py` to enable them:

  ```bash
  XBLOOM_REFERENCE=/path/to/parse_btsnoop.py pytest -q
  ```

  The reference is **not** part of this repo (it contains capture-specific data and
  lives outside it). If it's absent, those comparison tests skip and the rest still
  run — that's expected.

## Code style

- Clean, **typed** (the codebase uses `from __future__ import annotations` and type
  hints throughout), and **documented** (module + function docstrings explaining the
  protocol and the "why").
- Lint/format with `ruff` (config in `pyproject.toml`; line length 100). Run
  `ruff check .` before finishing.
- Keep `xbloom_ble.protocol` **pure** (no BLE imports) — it's the byte layer others
  build on. BLE I/O lives in `xbloom_ble.client`.
- Recipe **validation lives in `xbloom_ble/recipe.py`** (`Recipe.validate()`); when
  you learn a real machine limit, encode it there and document it in the README's
  "Recipe limits & valid ranges" table (mark firm vs observed honestly).

## Where the protocol spec lives

- The **README** has the full protocol reference: frame format, CRC, GATT table,
  the LOAD sequence, the `0x41` pours-frame byte map, and the status states.
- **`docs/REVERSE-ENGINEERING.md`** documents the *methodology* — how the protocol
  was recovered from an Android HCI capture (capture → parse → differential decode →
  validate), so the work is reproducible.

Keep both in sync with any protocol change.

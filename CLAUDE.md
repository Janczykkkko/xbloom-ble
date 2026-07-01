# CLAUDE.md — agent guide for `xbloom-ble`

This file is for an AI / Claude Code agent working **on** this repository. Read it
before making changes.

## What this project is

`xbloom-ble` is a small, dependency-light Python package (`bleak` + `pyyaml`) that
speaks the **reverse-engineered Bluetooth Low Energy protocol** of the
[xBloom Studio](https://xbloom.com) pour-over coffee machine. There is no official
xBloom API. The package lets you discover the machine, define a brew as a YAML
recipe, validate it, **load it onto the machine**, and watch live brew telemetry.
It is a public, MIT-licensed, open-source project with a clean CLI and a fully
documented protocol so others can build on it.

## 🛑 SAFETY MODEL — do not break the load-only default

**The default `brew` path only ever LOADS a recipe. The machine then prompts, and
a human physically approves the brew ON THE MACHINE to start it.** That default is
the headline differentiator and must never regress. A separate, explicit, opt-in
`start` path exists for power users who *choose* an unattended brew.

Concretely:

- The xBloom BLE protocol has opcodes that force-start a brew: **`0x42` (commit)
  and `0x46` (start)**, plus the execute opcode **`0x119A`** (4506). **The LOAD
  path must NEVER build, emit, or send these.**
- `build_load_frames()` returns **exactly four** LOAD frames (`0xa4`, `0xa6`,
  `0xa8`, `0x41`) and nothing else. There is a belt-and-braces assertion in it that
  rejects a forbidden opcode if one ever crept in — keep it.
- **Brew-start lives ONLY in `build_start_frames()`** — a distinct builder used
  solely by the explicit `xbloom start` / `xbloom brew --start` command. It is the
  only place a start/execute opcode may appear, and it always extends (never
  replaces) the load prefix. Do **not** let the default `brew` reach it, and do
  **not** merge the two builders.
- Tests guard this: `tests/test_protocol.py::test_no_forbidden_opcodes`,
  `test_load_frames_opcode_order`, `test_load_path_never_has_start_frames`, and
  `test_start_path_load_prefix_still_load_only`. **Keep these; never weaken or
  delete them.** If you touch the protocol layer, they must still pass.
- After a plain load, the machine reports state `0x1f` (armed) and waits for the
  human. That on-machine approval is the safety gate for the default path.

The lower-level controls (`grind`, `pour`, `save-slot`, `scale tare`) and `start`
are **explicit** actions that act on the machine — always keep their loud ⚠️
warnings. If a request would make the **default** `brew` auto-start, **decline it**
and point at the opt-in `start` command instead.

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

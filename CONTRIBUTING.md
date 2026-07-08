# Contributing to xbloom-ble

Thanks for your interest — contributions are very welcome! This is an unofficial,
community, MIT-licensed effort to make the [xBloom Studio](https://xbloom.com)
scriptable over Bluetooth LE. Whether you're fixing a bug, decoding more of the
protocol, wiring it into your smart home, or improving the TUI, you're in the right
place.

- **Found a bug / firmware quirk?** Open an issue with a scrubbed capture or log (see
  [No personal data](#no-personal-data-in-anything-you-share)).
- **Want to add a feature?** Skim [Architecture](#architecture) and the
  [Safety invariant](#-the-safety-invariant--it-must-be-preserved) first, then open a PR.
- **Have a different firmware / hardware revision?** A clean BLE capture is the single
  most valuable thing you can contribute — see [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md).

## Architecture

The package is deliberately layered so the byte protocol is reusable independently of
any BLE stack or UI. Depend downward only; keep `protocol` pure.

| Module | Responsibility | Notes |
| --- | --- | --- |
| `xbloom_ble/protocol.py` | The **pure byte layer**: frame builders, CRC16-KERMIT, opcodes, the LOAD sequence. | **No BLE imports.** This is the layer other tools build on — keep it dependency-light and side-effect-free. |
| `xbloom_ble/recipe.py` | The `Recipe` model, `validate()`, and `to_protocol_dict()` / `to_dict()`. | All machine limits live here; document them in the README's ranges table. |
| `xbloom_ble/telemetry.py` | Decodes `ffe2` status notifications into `StatusEvent`s (state names, best-effort weights). | The machine's state machine (idle/armed/starting/brewing/no-water/no-beans…) is decoded here. |
| `xbloom_ble/client.py` | The **only module that touches hardware** (`bleak`): scan, connect, `load_recipe`, `start`/`brew`, `cancel_brew`, `save_slots`, `stream_telemetry`. | BLE I/O only — no UI. |
| `xbloom_ble/cloud.py` | Unofficial client for the phone app's recipe cloud (login, sync, fetch). | Optional; not needed for local BLE control. |
| `xbloom_ble/cli.py` | The `xbloom` command (`tui`, `scan`, `validate`, `brew`, `save-slots`, `cloud …`). | Thin argparse layer over `client`/`cloud`. |
| `xbloom_ble/tui/` | The [Textual](https://textual.textualize.io/) terminal UI. | See below. |

The TUI is split so it's fully testable without a machine:

- `tui/app.py` — the app, screens, and the brew worker loop.
- `tui/controller.py` — the `MachineController` **seam**: `RealController` (wraps
  `XBloomClient`) vs `FakeController` (a pure-software brew simulator that powers
  `--demo` and the tests). **New machine behaviour should be modelled in
  `FakeController` too**, so tests can exercise it.
- `tui/confirm.py` — the 3-way brew confirmation gate (Cancel / Load-only / Start).
- `tui/editor.py`, `tui/store.py`, `tui/history.py`, `tui/slots.py`, `tui/help.py` —
  recipe editing, the recipe store, brew history, slot programming, help.

## Workflow (PRs + Conventional Commits)

- `main` is protected — land changes via a **pull request**; CI (`ruff` + `pytest` on
  Python 3.10–3.13, plus a coverage gate) must pass.
- **Your PR title must be a [Conventional Commit](https://www.conventionalcommits.org/)** —
  it's validated in CI and, on squash-merge, becomes the commit that drives the release.
  Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`,
  `revert`. Examples: `feat: add scale-tare command`, `fix: correct pour pattern mapping`.
  Breaking change → `feat!:` or a `BREAKING CHANGE:` footer.
- Releases are **fully automated** by semantic-release (`feat` → minor, `fix` → patch,
  breaking → major): merging a PR to `main` bumps the version, tags, writes release notes, and
  **publishes to PyPI via Trusted Publishing** (OIDC — no tokens). You never bump the version or
  tag by hand. See [docs/RELEASING.md](docs/RELEASING.md).

## Dev setup

You'll need Python 3.10+ and, for any on-hardware work, a machine with a working
Bluetooth LE stack.

```bash
git clone https://github.com/Janczykkkko/xbloom-ble
cd xbloom-ble
pip install -e ".[dev]"      # or: uv venv && uv pip install -e ".[dev]"
```

- **Linux:** you need **BlueZ running** (`bluetoothd` — the standard system
  Bluetooth daemon that `bleak` talks to). Bluetooth must be on, and you may need to
  run as a user with BLE permissions.
- **macOS / Windows** use their native BLE stacks via `bleak`.

No machine handy? `xbloom tui --demo` runs the whole UI against the `FakeController`
brew simulator — no hardware, no Bluetooth.

## Run the tests

```bash
pytest -q
```

Most tests are pure-Python and need no hardware. The **byte-for-byte protocol
round-trip** tests compare the package's frames against a reverse-engineering
reference script that isn't part of this repo; they **skip** unless you point
`XBLOOM_REFERENCE` at it:

```bash
XBLOOM_REFERENCE=/path/to/parse_btsnoop.py pytest -q
```

### Coverage

CI runs a coverage job and posts a summary on every PR; the gate lives in
`pyproject.toml` (`[tool.coverage.*]`) and fails the build below the floor. Run it
locally the same way:

```bash
pytest --cov=xbloom_ble --cov-report=term-missing
```

New code should come with tests. The safety-critical layers (`protocol`, `recipe`,
`telemetry`) are held near-fully covered — don't regress them. Hardware I/O in
`client.py` is exercised with mocked `bleak`.

### Lint

Please run the linter/formatter before opening a PR:

```bash
ruff check .
```

## Debugging on real hardware

Any command that talks to the machine accepts `--debug`, which tees the **full BLE
frame chatter** (every `→` sent and every raw `←` notification, including the
brew-progress frames we don't fully decode yet) to `xbloom-debug-<timestamp>.log` in
the current directory:

```bash
xbloom brew recipe.yaml --start --debug     # capture a full brew
xbloom tui --debug                          # capture from the TUI
```

These logs are the raw material for decoding more of the protocol. **They contain your
machine's MAC address and serial — they are git-ignored (`xbloom-debug-*.log`); scrub
them before attaching to an issue/PR** (see below).

## 🛑 The safety invariant — it must be preserved

This is the heart of the project and is **non-negotiable**:

> **Loading a recipe only *arms* the machine — it must never start a brew as a side
> effect. Starting a brew is always a separate, explicit call the caller opts into,
> never a consequence of loading.**

The package *can* start a brew remotely (the app-style Brew button): `client.start()`
sends commit (`0x42`) + start (`0x46`), and `cancel_brew()` sends `0x47`. That is a
deliberate, first-class capability — **the invariant is the *separation*, not a ban on
the opcodes.** Concretely, a PR must preserve all of:

- `build_load_frames()` returns only the four LOAD frames (`0xa4`, `0xa6`, `0xa8`, and
  the pours frame — `0x41` grinding / `0x44` no-grind) and **never** a commit/start/cancel
  opcode. There's a belt-and-braces assertion inside it, and a test
  (`tests/test_protocol.py::test_load_frames_are_load_only`) guards it.
- The commit/start/cancel opcodes are emitted **only** from the dedicated
  `build_commit()`/`build_start()`/`build_cancel()` builders, called **only** from
  `XBloomClient.start()`/`cancel_brew()` — never wired as a side effect of
  `load_recipe()`, and never behind a hidden auto-start path.
- In the TUI/CLI, a remote start stays **behind the explicit confirmation gate**
  (Cancel / Load-only / Start).

**Any PR that makes loading a recipe start a brew, fires commit/start as an implicit
side effect, or weakens those tests will not be accepted.** A PR that adds an
*explicit*, opt-in brew action is fine — that's what `start()` is for.

## No personal data in anything you share

This repo is **public**. Any capture, telemetry log, screenshot, or example you
attach to an issue or PR **must be scrubbed of personal data first**: no Bluetooth
MAC addresses, no device serials, no real names, no private file paths. Use
placeholders (e.g. `AA:BB:CC:DD:EE:FF`). It's worth grepping your diff for accidental
leaks before you push.

## Pull request checklist

- [ ] Code is clean, typed, and documented (match the existing style; `ruff check .` passes).
- [ ] `pytest -q` passes and new behaviour has tests (model machine behaviour in
      `FakeController` where relevant); coverage doesn't regress.
- [ ] No new implicit-brew / auto-start path; the safety tests still pass.
- [ ] Protocol change? README protocol reference **and**
      [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md) updated to match.
- [ ] New recipe limit? README "Recipe limits & valid ranges" table updated (mark firm
      vs observed honestly).
- [ ] PR title is a Conventional Commit; a short, clear description of *what* and *why*.
- [ ] No personal data anywhere in the diff or attachments.

## License

By contributing, you agree your contributions are licensed under the project's
**MIT License** (see [LICENSE](LICENSE)).

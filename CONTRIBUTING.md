# Contributing to xbloom-ble

Thanks for your interest — contributions are very welcome! This is an unofficial,
community, MIT-licensed effort to make the [xBloom Studio](https://xbloom.com)
scriptable over Bluetooth LE. Whether you're fixing a bug, decoding more of the
protocol, or wiring it into your smart home, you're in the right place.

## Workflow (PRs + Conventional Commits)

- `main` is protected — land changes via a **pull request**; CI (`ruff` + `pytest` on
  Python 3.10–3.13) must pass.
- **Your PR title must be a [Conventional Commit](https://www.conventionalcommits.org/)** —
  it's validated in CI and, on squash-merge, becomes the commit that drives the release.
  Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`,
  `revert`. Examples: `feat: add scale-tare command`, `fix: correct pour pattern mapping`.
  Breaking change → `feat!:` or a `BREAKING CHANGE:` footer.
- Releases are **fully automated** (`feat` → minor, `fix` → patch, breaking → major) and
  published to PyPI — you never bump the version or tag by hand. See
  [docs/RELEASING.md](docs/RELEASING.md).

## Dev setup

You'll need Python 3.10+ and, for any on-hardware work, a machine with a working
Bluetooth LE stack.

```bash
git clone https://github.com/Janczykkkko/xbloom-ble
cd xbloom-ble
pip install -e ".[dev]"
```

- **Linux:** you need **BlueZ running** (`bluetoothd` — the standard system
  Bluetooth daemon that `bleak` talks to). Bluetooth must be on, and you may need to
  run as a user with BLE permissions.
- **macOS / Windows** use their native BLE stacks via `bleak`.

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

Please also run the linter before opening a PR:

```bash
ruff check .
```

## 🛑 The safety invariant — it must be preserved

This is non-negotiable and the heart of the project:

> **The tool only ever LOADS a recipe. The machine then prompts, and a human
> physically approves the brew ON THE MACHINE to start it. The tool never
> auto-starts a brew.**

The protocol's force-start opcodes — **`0x42` (commit)** and **`0x46` (start)** —
are deliberately **never built or sent**. There is intentionally no auto-start code
path, and a test (`tests/test_protocol.py`) guards it. **Any PR that emits `0x42`/
`0x46`, adds an auto-confirm path, or weakens those tests will not be accepted.**

## What's especially wanted

- **Captures & decodes for other firmware versions / hardware revisions.** This was
  verified against firmware `V12.0D.500` only — other firmwares are uncharted. A
  clean BLE capture (see [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md))
  from a different version is gold.
- **More recipe-range data.** Real confirmation of the true machine limits (dose,
  temperature, rpm, flow, pause) so the validator's "observed" bounds in the README
  can become "firm".
- **Integrations:** Home Assistant, ESPHome, or other smart-home / automation
  front-ends built on the pure `xbloom_ble.protocol` / `xbloom_ble.client` layers.

## No personal data in anything you share

This repo is **public**. Any capture, telemetry log, screenshot, or example you
attach to an issue or PR **must be scrubbed of personal data first**: no Bluetooth
MAC addresses, no device serials, no real names, no private file paths. Use
placeholders (e.g. `AA:BB:CC:DD:EE:FF`).

## Pull request basics

- Keep the code clean, typed, and documented (match the existing style; `ruff` must
  pass).
- **All tests must pass**, and you must **not** introduce a `0x42`/`0x46` opcode or
  any auto-start path.
- If you change the protocol, update the **README** protocol reference and
  **`docs/REVERSE-ENGINEERING.md`** to match.
- If you encode a new recipe limit, update the README's "Recipe limits & valid
  ranges" table (honestly mark firm vs observed).
- A short, clear description of what and why is appreciated.

## License

By contributing, you agree your contributions are licensed under the project's
**MIT License** (see [LICENSE](LICENSE)).

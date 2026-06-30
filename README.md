# xbloom-ble

> 🤖 Built & maintained by [Claude Code](https://claude.com/claude-code).

![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-d97757)

**Unofficial Bluetooth LE control for the [xBloom Studio](https://xbloom.com) pour-over coffee machine.**

There is no official xBloom API. This package speaks the machine's
reverse-engineered Bluetooth Low Energy protocol so you can script and version
your recipes — discover the machine, validate a recipe, **load it onto the
machine**, and watch live brew telemetry.

It is a small, dependency-light Python package (`bleak` + `pyyaml`) with a clean
CLI and a fully documented protocol so others can build on it.

> 🤖 **Designed with agentic use in mind.** This was written *by* an AI coding agent
> ([Claude Code](https://claude.com/claude-code)) and tailored to be driven *by* one —
> scriptable commands, predictable/parseable output, a fully documented protocol, and a
> safety model where the tool only ever **loads** a recipe and a **human approves the brew
> on the machine**. (It's just as pleasant to use by hand.)

---

## ⚠️ Safety — this tool only *loads*, it never auto-starts

This is the headline design decision and a hard invariant:

> **`xbloom-ble` only ever LOADS a recipe onto the machine. It sends the load
> sequence, the machine then prompts you, and YOU physically approve the brew on
> the machine itself. The tool will never start a brew for you.**

The xBloom BLE protocol *does* have opcodes that force-start a brew (`0x42`
commit and `0x46` start). **This package never builds or sends them.** There is
intentionally no code path that emits `0x42`/`0x46` — `build_load_frames()`
returns only the four LOAD frames, and there is even a belt-and-braces assertion
that rejects a forbidden opcode if one ever crept in. So the worst this tool can
do is arm a recipe you then have to confirm by hand, with the cup and beans in
front of you.

---

## Tested with

This was developed and verified against an **xBloom Studio running firmware
`V12.0D.500`** — that is the **only unit and firmware it has been tested against**.
The reverse-engineered protocol may differ on other firmware or hardware revisions,
and could break with future updates.

**Reports for other firmware/hardware are very welcome** — if it works (or doesn't)
on your machine, please open an issue. A BLE capture from a different firmware
version is especially useful (see [CONTRIBUTING.md](CONTRIBUTING.md); strip any
personal data first).

---

## Install

```bash
pip install xbloom-ble
```

From source:

```bash
git clone https://github.com/Janczykkkko/xbloom-ble
cd xbloom-ble
pip install -e .
```

**Linux** needs BlueZ running (`bluetoothd`) — it's the standard system
Bluetooth stack and is what `bleak` talks to. macOS and Windows use their native
BLE stacks. Bluetooth must be on, and on Linux you may need to run as a user
with BLE permissions.

---

## Usage

The CLI is `xbloom`.

### Discover your machine

```bash
xbloom scan
```

```
Found 1 machine(s):
  AA:BB:CC:DD:EE:FF  XBLOOM-1234
```

The machine is discovered by its vendor **service UUID**
(`0000e0ff-3c17-d293-8e48-14fe2e4da212`) or a device name starting with
`XBLOOM` — no hardcoded address.

### Validate a recipe (no hardware needed)

```bash
xbloom validate recipes/example-washed.yaml
```

```
OK: 'Example Washed' — 16 g, grind 62, 3 pours, 240 ml total water
```

### Load a recipe and watch the brew

```bash
xbloom brew recipes/example-washed.yaml --address AA:BB:CC:DD:EE:FF
```

or set the address via the environment (so nothing is hardcoded):

```bash
export XBLOOM_ADDRESS=AA:BB:CC:DD:EE:FF
xbloom brew recipes/example-washed.yaml
```

It validates, connects, **loads** the recipe, then prints:

```
✋ Recipe loaded. Add beans + cup, then APPROVE ON THE MACHINE to start. (This tool will NOT start it.)
```

…and streams live status (state changes and, during the brew, water/coffee
weights) until the brew completes or the timeout (`--timeout`, default 300 s)
elapses. A telemetry log is written to `./telemetry-<timestamp>.json`.

Common flags: `--address`, `--timeout`, `-v/--verbose`, `--version`.

---

## Recipe format

> 📖 **Looking for recipes to start from?** Browse the community **[xBloom recipe ledger](https://xbloom.lodywgumce.tv)**
> — per-bean pour recipes (grind, temps, pour schedule) you can adapt to the format below.

Recipes are plain YAML:

```yaml
name: Example Washed
dose_g: 16          # coffee dose in grams
grind: 62           # grinder setting (1–80)
ratio: 15           # optional; if given, Σ pour ml must equal dose_g * ratio
stage_temps: [110.0, 90.0]   # optional; machine stage temps, default 110/90
pours:
  - {ml: 45,  temp_c: 93, pattern: spiral, agitation: true, pause_s: 40, rpm: 100, flow_ml_s: 3.0}
  - {ml: 100, temp_c: 91, pattern: spiral, pause_s: 10, rpm: 100, flow_ml_s: 3.2}
  - {ml: 95,  temp_c: 90, pattern: spiral, pause_s: 5,  rpm: 100, flow_ml_s: 3.2}
```

Per-pour fields (ranges are **firm — per xBloom Studio specs**):

| Field       | Meaning                                                        |
|-------------|----------------------------------------------------------------|
| `ml`        | Water volume for this pour (≥1 ml). A pour over 127 ml is auto-split by the protocol — not an error. |
| `temp_c`    | Water temperature (40–95 °C, 1 °C steps).                       |
| `pattern`   | `spiral`, `ring`, or `center`.                                 |
| `agitation` | `true` only with `spiral` (an agitated bloom). Default `false`. |
| `pause_s`   | Pause after this pour, seconds (0–255; the on-machine countdown caps near 99 s). |
| `rpm`       | Agitation rotation speed (60–120, 10-RPM steps; `0` for `center`). |
| `flow_ml_s` | Flow rate in ml/s (3.0–3.5, 0.1 steps).                         |

The app also exposes two **special, non-numeric temperature settings — `RT`
(room temp) and `BP` (boiling point)** — which are not expressible as a numeric
`temp_c`; the numeric range is 40–95 °C.

See **[Recipe limits & valid ranges](#recipe-limits--valid-ranges)** below for the
full table and the firm bounds enforced.

Validation rejects: fewer than two pours (you need at least a bloom and a first
pour), an unknown `pattern`/`agitation` combo, out-of-range values, and — if a
`ratio` is given — a pour total that doesn't equal `dose_g * ratio`.

---

## Recipe limits & valid ranges

These are the bounds `xbloom validate` enforces. Most are **firm (per the xBloom
Studio published specifications)** — a real machine/app limit; a couple of
ceilings (`ml`, `pause_s`) remain practical sanity guards. **If your machine
behaves differently, please open an issue with a capture — the ranges should
track real hardware.**

| Value         | Accepted range | Firmness |
|---------------|----------------|----------|
| `dose_g`      | 1–18 g         | **Firm (per xBloom Studio specs).** 18 g is the maximum the xBloom app lets you set. |
| `grind`       | 1–80           | **Firm (per xBloom Studio specs).** The grinder has 80 micro-steps (~18.75 µm each); a *lower* number is *finer*. |
| `temp_c` (pour) | 40–95 °C     | **Firm (per xBloom Studio specs).** Settable in 1 °C steps. The app also offers special non-numeric `RT` (room temp) and `BP` (boiling point) settings, outside this numeric range. |
| `stage_temps` | 40–130 °C each | Machine **preheat/stage set-points** (default 110/90 °C) — NOT the pour temperature, so they legitimately exceed the 95 °C pour cap. Wider allowance around the default. |
| `rpm`         | 0, or 60–120   | **Firm (per xBloom Studio specs).** 60–120 in 10-RPM steps; `0` (no agitation) is allowed only for `center` pours. |
| `flow_ml_s`   | 3.0–3.5 ml/s   | **Firm (per xBloom Studio specs).** Settable in 0.1 steps. |
| `pause_s`     | 0–255          | The wire byte is `256 − seconds` (so 0–255 fits), but the **on-machine countdown caps near 99 s** — treat 0–99 as the practical range. |
| `ml` (pour)   | 1–4000 ml      | Lower bound (≥1) is firm; a pour **over 127 ml is auto-split** by the protocol (not an error). The 4000 ceiling is just a sanity guard. |
| `pattern`     | `spiral`, `ring`, `center` | **Firm.** These are the decoded pattern codes; `agitation: true` is only valid with `spiral`. |

> **Source:** xBloom Studio published specifications.

The pour count must be **≥2** (at least a bloom and a first pour), and if you give
an optional `ratio`, Σ(pour ml) must equal `dose_g * ratio`.

---

## Reverse-engineered protocol

The wire protocol was reverse-engineered from an Android Bluetooth HCI capture
and verified by round-tripping against the original recorded frames. This is
documented in full so you can build on it.

> 📓 **How it was done:** the full capture → parse → differential-decode methodology
> (reproducible, no special hardware) is written up in
> **[docs/REVERSE-ENGINEERING.md](docs/REVERSE-ENGINEERING.md)**.

### Frame format

Every frame (commands to `ffe1`, status from `ffe2`) is:

```
58 01 01 | CMD(u8) | SEQ(u8) | LEN(u16 LE) | 00 00 | PAYLOAD | CRC16(u16 LE)
```

- `58 01 01` — constant header.
- `CMD` — command opcode.
- `SEQ` — sequence byte; the load sequence uses `0x1f` (31).
- `LEN` — total frame length (header through CRC), little-endian, at offset 5.
- `00 00` — two constant zero bytes.
- `PAYLOAD` — command-specific.
- `CRC16` — **CRC-16/KERMIT** over the whole frame minus the trailing two bytes,
  stored little-endian.

**CRC-16/KERMIT:** polynomial `0x1021`, init `0`, reflected input and output, no
final XOR (check value `0x2189` for `b"123456789"`).

### GATT

Vendor service `0000e0ff-3c17-d293-8e48-14fe2e4da212`:

| Characteristic | Short | Role                |
|----------------|-------|---------------------|
| command        | `ffe1`| write               |
| status         | `ffe2`| notify (telemetry)  |
| aux            | `ffe3`| auxiliary           |

### The LOAD sequence

Sent frame-by-frame to `ffe1`, waiting for each ACK on `ffe2` (the machine
echoes the command, e.g. `580207a6…`):

1. **`0xa4`** — session start. Constant payload `01 b9 00 00 00 01 00 00 00`.
2. **`0xa6`** — dose. Dose in grams as a `u8` at payload offset 9.
3. **`0xa8`** — stage temps. `01` + f32 LE temp1 + f32 LE temp2 (default
   `110.0`, `90.0`).
4. **`0x41`** — pours + grind (see byte map below).

After frame 4 the machine reports STATE `0x1f` (armed) and **waits for the human
to approve on the machine**. The protocol's `0x42` (commit) and `0x46` (start)
opcodes would force-start the brew — **this package never sends them.**

### The `0x41` pours frame payload

```
01 | LEN(u8 = #body bytes) | <pour segments…> | grind(u8) | tail(u8 = 0xa0)
```

Each pour becomes an **8-byte segment**:

| Offset | Byte       | Meaning                                              |
|--------|------------|------------------------------------------------------|
| 0      | `ml`       | Pour volume for this segment, ml.                    |
| 1      | `temp`     | Water temperature, °C.                               |
| 2      | `pat`      | Pattern code (see table).                            |
| 3      | `agit`     | Agitation code (see table).                          |
| 4      | `negpause` | `(256 − pause_s) & 0xff` — post-pour pause.          |
| 5      | `00`       | Constant zero.                                       |
| 6      | `rpm`      | Agitation rotation speed (0 for center pours).       |
| 7      | `flow10`   | Flow rate in ml/s × 10 (3.0 → `0x1e`).               |

**Pattern codes** — `(pattern, agitation) → (pat, agit)`:

| Pattern  | Agitation | `pat` | `agit` |
|----------|-----------|-------|--------|
| spiral   | true      | 0x02  | 0x02   |
| spiral   | false     | 0x02  | 0x00   |
| ring     | false     | 0x01  | 0x00   |
| center   | false     | 0x00  | 0x01   |

**Large pours:** a pour above 127 ml is split into 127-ml **4-byte lead
segments** (`[ml, temp, pat, agit]`) followed by an 8-byte remainder segment
carrying the flow/pause/rpm fields.

### Status notifications (`ffe2`)

A `0x57` status frame's **state byte** (the byte just after the `0xc1` marker)
tells you what the machine is doing:

| State | Name               | Meaning                              |
|-------|--------------------|--------------------------------------|
| 0x01  | idle               | Idle / ready.                        |
| 0x1f  | armed              | Recipe loaded, awaiting approval.    |
| 0x1e  | awaiting_confirm   | Waiting for the human to confirm.    |
| 0x3b  | brewing            | Brew in progress.                    |
| 0x43  | brew_record        | Live brew record (water/coffee g).   |
| 0x41  | complete           | Brew complete.                       |
| 0x15 / 0x4b | idle_heartbeat | Idle heartbeat (ignored).         |

`0x43` brew-record frames carry live water/coffee weights (16-bit LE tenths of a
gram), decoded best-effort.

---

## Library API

```python
import asyncio
from xbloom_ble import Recipe
from xbloom_ble.client import XBloomClient, scan

async def main():
    recipe = Recipe.from_yaml("recipes/example-washed.yaml")
    devices = await scan()
    async with XBloomClient(devices[0].address) as client:
        await client.load_recipe(recipe)      # loads only — never starts
        # → now physically approve the brew on the machine
        await client.stream_telemetry(lambda ev: print(ev), duration=300)

asyncio.run(main())
```

`xbloom_ble.protocol` is pure (no BLE) and is the place to start if you want to
build a different front-end:

```python
from xbloom_ble.protocol import build_load_frames
frames = build_load_frames(recipe.to_protocol_dict())  # [a4, a6, a8, 41]
```

---

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The protocol tests assert this package's frames are **byte-for-byte** identical
to the reverse-engineering reference, including the 127-ml split, the center and
ring patterns, and an agitated bloom. Point `XBLOOM_REFERENCE` at the reference
script if it lives elsewhere (those comparison tests skip if it's absent).

---

## Disclaimer

This is an **unofficial** project. It is **not affiliated with, endorsed by, or
supported by xBloom** in any way. "xBloom" and "xBloom Studio" are trademarks of
their respective owner.

The protocol here was reverse-engineered and may be incomplete or wrong; it may
break with firmware updates. **Use at your own risk — you assume full
responsibility** for anything you do with your machine. By design this tool only
*loads* recipes and **never auto-starts a brew**: the machine always prompts you
and you approve the brew physically on the device. Even so, supervise your
machine. No warranty (see [LICENSE](LICENSE)).

## License

MIT © 2026 Janczykkkko

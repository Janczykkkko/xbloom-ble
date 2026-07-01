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

## ⚠️ Safety — `brew` is load-only by default

This is the headline design decision:

> **The default `xbloom brew` command only ever LOADS a recipe onto the machine.
> It sends the load sequence, the machine then prompts you, and YOU physically
> approve the brew on the machine itself. `brew` will never start a brew for you.**

`build_load_frames()` returns only the four LOAD frames. It never emits the
protocol's brew-start opcodes (`0x42` commit / `0x46` start), and a
belt-and-braces assertion rejects a forbidden opcode if one ever crept into that
path. So the default is: the worst this tool can do is *arm* a recipe you then
confirm by hand, with the cup and beans in front of you.

### Lower-level controls and opt-in start are **explicit** actions

The package also exposes lower-level controls that **do act on the machine**.
These are **separate, explicit** commands — never the default `brew` behaviour —
each with a loud warning:

- **`xbloom grind`** — runs the grinder (no brew).
- **`xbloom pour`** — a FreeSolo single pour that **dispenses hot water**.
- **`xbloom save-slot`** — writes an Easy-Mode preset to a slot (no brew).
- **`xbloom scale tare` / `units`** — zero the scale / set the weight unit.
- **`xbloom start`** (and **`xbloom brew --start`**) — the **opt-in full brew**:
  loads the recipe *and* starts it. This is the ONLY path that emits a brew-start
  opcode, and it lives in a distinct builder (`build_start_frames`), kept strictly
  separate from `build_load_frames` so the default can never reach it.

Read-only commands (`scan`, `validate`, `info`, `scale read`) don't act on the
machine at all. If you just want the safe default, use `brew` — everything that
acts on the machine is opt-in and clearly marked ⚠️.

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

### Machine info (read-only)

```bash
xbloom info
```

Connects and prints decoded machine info — serial, firmware, water level, and
the temperature/weight units — then disconnects. Read-only.

### Scale

```bash
xbloom scale read      # read the current weight in grams (free / read-only)
xbloom scale tare      # ⚠️ zero the scale (acts on the machine)
xbloom scale units g   # set the weight unit: g | oz | ml
```

### ⚠️ Grind only (no brew)

```bash
xbloom grind --size 30 --dose 16
```

Runs the grinder at grind size `30` for a `16 g` dose. **This runs the grinder** —
make sure beans are loaded. It does not brew.

### ⚠️ FreeSolo pour (dispenses hot water)

```bash
xbloom pour --ml 200 --temp 92 --flow 3.0 --pattern spiral
```

Dispenses a single pour of hot water at the given volume/temperature/flow/pattern.
**This dispenses HOT WATER** — make sure a cup is in place. It sends the FreeSolo
preamble (handshake, bypass+dose, set-cup, scale tare) then the pour.

### Save an Easy-Mode preset (no brew)

```bash
xbloom save-slot 1 recipes/example-washed.yaml
```

Writes the recipe as an Easy-Mode preset to slot `1`, `2`, or `3` (A/B/C). This
is a stateful write to the machine but **does not brew**.

### ⚠️ Start a brew (opt-in)

```bash
xbloom start recipes/example-washed.yaml
# or, equivalently:
xbloom brew recipes/example-washed.yaml --start
```

The **opt-in full brew**: loads the recipe *and* starts it (no on-machine
approval step). Prints a loud warning first. Use this only on a staged machine
with a cup and beans in place — it **will** start a brew. The plain
`xbloom brew` (without `--start`) stays load-only.

---

## Cloud — push recipes to your xBloom app account

A **separate subsystem** from everything above. The BLE commands talk to the
machine over Bluetooth; the `cloud` command talks to the xBloom **cloud REST API**
(`client-api.xbloom.com`), so a recipe you push here shows up in the **xBloom
mobile app** under your account — ready to send to the machine from your phone.

> ⚠️ **Unofficial API — use at your own risk.** There is no official xBloom cloud
> API. The endpoints, the RSA-encrypted request format, and the recipe schema were
> all **reverse-engineered by the community** (ported from
> [`cryptofishbug/xbloom-recipe-cli`](https://github.com/cryptofishbug/xbloom-recipe-cli),
> MIT — see the CHANGELOG). It **may break at any time** if xBloom changes their
> backend, and it operates on **your own account**. This is unrelated to the
> load-only BLE safety model — no brew is triggered; it only edits recipes in your
> account.

It needs the `cryptography` package, shipped as an optional extra:

```bash
pip install "xbloom-ble[cloud]"
```

Provide your account credentials via environment variables (never hardcode them):

```bash
export XBLOOM_EMAIL="you@example.com"
export XBLOOM_PASSWORD="your-password"
```

Then:

```bash
xbloom cloud login                              # authenticate + cache the token
xbloom cloud add-recipe recipes/example-washed.yaml   # push a recipe to your account
xbloom cloud add-recipe recipes/example-washed.yaml --cup xpod   # pick the cup type
xbloom cloud list                               # list recipes in your account
xbloom cloud delete <tableId>                   # delete a recipe by id
xbloom cloud fetch <share-id-or-url>            # read a public shared recipe (no login)
```

`login` caches the token + member id to `~/.config/xbloom-ble/cloud-auth.json`
(override with `--auth-path` or `$XBLOOM_CLOUD_AUTH`) so later commands reuse it.
If you skip `login` but have `XBLOOM_EMAIL`/`XBLOOM_PASSWORD` set, the library can
log in on demand.

Under the hood, authenticated request bodies are
`Base64( RSA-1024 PKCS#1 v1.5 ( JSON ) )`, chunked Hutool-style (117-byte plaintext
blocks → 128-byte cipher blocks). The 1024-bit RSA **public** key is embedded in
the source (a public key — safe to ship). See
[`xbloom_ble/cloud.py`](xbloom_ble/cloud.py).

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
opcodes would force-start the brew — **the load path never sends them** (they
appear only in the explicit opt-in `start` path; see below).

### The `0x41` pours frame payload

```
01 | LEN(u8 = #body bytes) | <pour segments…> | grind(u8) | tail(u8 = round(ratio×10))
```

The final two bytes are **`[grinder_size][ratio×10]`**. The tail is the brew
**ratio** encoded as `round(ratio × 10)` — e.g. 1:16 → `0xa0` (160), 1:17 →
`0xaa` (170) — computed from the recipe's ratio (explicit, or derived from
`Σ pour ml / dose_g`). (This resolves the previously-undecoded "tail" byte,
reconciled against the [brAzzi64/xbloom-ble](https://github.com/brAzzi64/xbloom-ble)
`grandWater` field.)

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

### Lower-level control commands

Beyond the LOAD sequence, the machine accepts these commands (opcodes ported
from and cross-validated against [brAzzi64/xbloom-ble](https://github.com/brAzzi64/xbloom-ble)).
A command is named here by its 16-bit code; on the wire that code is
little-endian, so it splits into this package's `CMD(u8) | SEQ(u8)` framing as
**cmd = low byte, seq = high byte** (e.g. `0x1FA6` → `cmd 0xa6, seq 0x1f`). Our
LOAD opcodes `a4`/`a6`/`a8` are exactly the reference's `8100`/`8102`/`8104`.

| Command      | Code (16-bit) | cmd / seq | Payload                          | In `xbloom-ble` |
|--------------|---------------|-----------|----------------------------------|-----------------|
| Machine info | `0x9E49` (40521) | `49`/`9e` | none (query)                  | `info`          |
| Scale tare   | `0x2134` (8500)  | `34`/`21` | none                          | `scale tare`    |
| Scale units  | `0x1F45` (8005)  | `45`/`1f` | int (0=g, 1=oz, 2=ml)         | `scale units`   |
| Grind start  | `0x0DAC` (3500)  | `ac`/`0d` | `[1000, size, speed]`         | `grind`         |
| Grind stop   | `0x0DB1` (3505)  | `b1`/`0d` | none                          | (internal)      |
| Handshake    | `0x1FA4` (8100)  | `a4`/`1f` | `[185, 1]`                    | preamble        |
| Bypass+dose  | `0x1FA6` (8102)  | `a6`/`1f` | `[vol_f, temp_f, dose_i]`     | preamble        |
| Set cup      | `0x1FA8` (8104)  | `a8`/`1f` | `[max_f, min_f]`              | preamble        |
| Pour recipe  | `0x1F44` (8004)  | `44`/`1f` | recipe blob (0x41-style body) | `pour`          |
| Execute/start| `0x119A` (4506)  | `9a`/`11` | none                          | `pour`, `start` |
| Save slot    | `0x2CF6` (11510) | `f6`/`2c` | `[slot_idx][flags][blob]`     | `save-slot`     |

- **Type-1** command payloads are a `0x01` marker followed by N 4-byte
  little-endian values (floats for float fields, ints otherwise).
- **Type-2** (`save-slot`) is a `0x01` marker followed by a raw byte blob:
  `[slot_idx (0/1/2)][flags][recipe blob]`. **Flags** is a bitfield — grinder ON
  `0x02` / grinder OFF `0x04`, scale `0x10` (so scale+grinder = `0x12`).
- **FreeSolo pour** = handshake → bypass+dose → set-cup → (optional) scale tare →
  pour recipe blob → execute.
- **Brew start** (`xbloom start` / `brew --start`) = the four LOAD frames →
  handshake → bypass+dose → set-cup → scale tare → execute (`0x119A`). This is
  the **only** path that emits `0x119A`; the default `brew` never does.

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

`XBloomClient` also exposes the explicit lower-level controls:
`get_machine_info()`, `read_scale()`, `tare_scale()`, `set_scale_units()`,
`grind()`, `pour()`, `save_slot()`, and `start_brew()` (the opt-in full brew).
`load_recipe()` remains load-only; only `start_brew()` starts a brew.

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
responsibility** for anything you do with your machine. By design the default
`brew` command only *loads* a recipe and the machine prompts you to approve the
brew physically on the device. The lower-level controls (`grind`, `pour`,
`save-slot`) and the opt-in `start` / `brew --start` are **explicit** actions
that act on the machine — they dispense water, run the grinder, or start a brew;
use them only on a supervised, staged machine. **Always supervise your machine.**
No warranty (see [LICENSE](LICENSE)).

## License

MIT © 2026 Janczykkkko

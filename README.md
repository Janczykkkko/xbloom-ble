# xbloom-ble

> 🤖 Built & maintained by [Claude Code](https://claude.com/claude-code).

[![CI](https://github.com/Janczykkkko/xbloom-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/Janczykkkko/xbloom-ble/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/xbloom-ble?cacheSeconds=3600)](https://pypi.org/project/xbloom-ble/)
[![Python](https://img.shields.io/pypi/pyversions/xbloom-ble?cacheSeconds=3600)](https://pypi.org/project/xbloom-ble/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Built with Claude Code](https://img.shields.io/badge/built%20with-Claude%20Code-d97757)

**Unofficial Bluetooth LE control for the [xBloom Studio](https://xbloom.com) pour-over coffee machine.**

There is no official xBloom API. This package speaks the machine's
reverse-engineered Bluetooth Low Energy protocol so you can script and version
your recipes — discover the machine, validate a recipe, **load it onto the
machine**, and watch live brew telemetry.

It can also — optionally — **sync recipes to your xBloom phone-app account** over
the unofficial cloud REST API (`xbloom cloud`, see below), so a recipe you keep
in version control shows up in the app too.

It is a small, dependency-light Python package (`bleak` + `pyyaml`; the cloud
feature adds an optional `cryptography` dep) with a clean CLI and a fully
documented protocol so others can build on it.

> 🤖 **Designed with agentic use in mind.** This was written *by* an AI coding agent
> ([Claude Code](https://claude.com/claude-code)) and tailored to be driven *by* one —
> scriptable commands, predictable/parseable output, a fully documented protocol, and a
> safety model where **loading** a recipe only arms the machine and **starting** the brew
> is a separate, explicit step. (It's just as pleasant to use by hand.)

---

## ⚠️ Safety — loading and starting are separate, deliberate steps

`xbloom-ble` can both **load** a recipe and **start** the brew (just like the
official app). The design keeps those two apart so nothing brews by accident:

> **Loading a recipe only *arms* the machine — it can never start a brew.
> Starting is a separate, explicit call that you make on purpose, and it
> physically dispenses near-boiling water.**

Concretely:

- `load_recipe()` / `xbloom brew` (no `--start`) send only the four LOAD frames
  (`build_load_frames()` never contains a start opcode, with a belt-and-braces
  assertion to prove it). The machine arms and prompts; you can approve it on the
  machine by hand.
- `start()` / `xbloom brew --start` / the TUI's confirm-gated **Start** additionally
  send commit (`0x42`) + start (`0x46`) to launch the brew, and `cancel_brew()`
  (`0x47`) aborts one. Starting is **never** a side effect of loading — you always
  ask for it explicitly.

> 🔥 Only start a brew when the machine is physically ready (water tank filled,
> dripper/cup in place). A remote start pours hot water with no one required at the
> machine.

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

## Getting started

Do it in this order:

1. **Install** (above) and make sure Bluetooth is on.
2. **Find the machine** — `xbloom scan` — and note its address (or set
   `export XBLOOM_ADDRESS=…` so you never type it again).
3. **Write a recipe** — a small YAML file (see [Recipe format](#recipe-format)),
   or point at a hosted one by URL. Check it with `xbloom validate <recipe>`.
4. **Make sure the phone app is disconnected** from the machine before any write —
   the machine allows a **single** BLE link, and the app holds it. Close the app
   *and* turn the phone's Bluetooth off.
5. **Pick one of the two paths below.**

Then, whichever path you choose:

> **There are exactly two ways to get a recipe onto the machine — pick one:**
>
> **▸ Path 1 — Load one recipe and brew it now** (`xbloom brew <recipe>`).
> The tool loads the recipe and the machine prompts you to **approve the brew on
> the machine** — or add `--start` to launch it remotely (⚠️ dispenses hot water).
> Best for a one-off brew of whatever you're dialing in.
>
> **▸ Path 2 — Program the three dial presets, then brew with no phone**
> (`xbloom save-slots <A> <B> <C>`). Stores three recipes on the machine's
> Easy-Mode dial (slots A/B/C) so you can brew straight from the dial, no app, no
> recipe cards. Best for your everyday go-to coffees.

(Separately, `xbloom cloud` manages the recipe **library in your phone app
account** — that's not a way to drive the machine directly; see below.)

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

It validates, connects, **loads** the recipe (does **not** start it), then prints:

```
✋ Recipe loaded. Add beans + cup, then APPROVE ON THE MACHINE to start.
```

…and streams live status (machine state changes) until the brew completes or the
timeout (`--timeout`, default 300 s) elapses. A telemetry log is written to
`./telemetry-<timestamp>.json`.

### Start the brew remotely (`--start`)

To also **launch** the brew from your computer — like tapping *Brew* in the app —
add `--start`. This sends commit + start after loading:

```bash
xbloom brew recipes/example-washed.yaml --start
```

> 🔥 `--start` dispenses hot water. Only use it with the machine physically ready
> (water in, cup/dripper in place). Without `--start`, `brew` just loads and you
> approve on the machine.

The recipe argument to `brew` / `validate` / `cloud` can also be an **`http(s)://`
URL** — so a recipe can be served and brewed without downloading it first:

```bash
xbloom brew https://xbloom.lodywgumce.tv/r/teso-la-leona.yaml
```

Common flags: `--address`, `--timeout`, `-v/--verbose`, `--version`.

### Terminal UI

`xbloom` with no subcommand (or `xbloom tui`) launches a **keyboard-first terminal
UI** — a k9s-style cockpit over a directory of recipe files:

```bash
xbloom tui --recipes ./recipes            # or just: xbloom
```

- **Recipes** tab — a table of your recipe files with a detail sidebar (dose,
  ratio, grind, water, per-pour schedule, notes) that follows the cursor. Create
  and edit recipes in a validated form (`n` / `e`), assign the dial slots
  (`1`/`2`/`3`) and push them (`p`).
- **Brewing** tab — press **brew** (`b` / Enter) to load a recipe; a **confirm
  gate** appears (arrow to *Start*, Enter, or `y`) before anything launches, then a
  live water/coffee graph streams. `c` cancels.
- **History** tab — past brews with their saved telemetry curve.

Run it against the simulator (no machine) with `--demo` to explore it safely.

### Program the dial presets (save-slots)

The xBloom Studio's **Auto Mode** stores three recipes on the machine's dial
(slots **A / B / C**) so you can brew from the dial with no phone. `save-slots`
programs all three at once from three recipes — a **preset write, it never
brews**:

```bash
xbloom save-slots light.yaml medium.yaml iced.yaml
xbloom save-slots a.yaml b.yaml c.yaml --scale-off C   # disable the scale in slot C's preset
```

All three are required in one call — the machine only *stores* the presets once
it has received the whole A/B/C set (it saves the batch atomically). Writing a
single slot leaves the machine showing **RETRY**.

> ⚠️ **These presets live on the machine, and the phone app can overwrite them.**
> The xBloom app keeps its *own* A/B/C assignments and pushes them to the machine
> over Bluetooth whenever you (re)assign a slot in the app — which will clobber
> what you set here. There is **no way to read the machine's current slots back**
> (the app can't either; it only remembers what it last pushed). So: keep your
> three recipes somewhere (a folder, a repo) and re-run `save-slots` to restore
> them, and program the slots when you intend to drive the machine **from its
> dial, not the app**.
>
> 🔌 Before writing, **disconnect the phone** (close the app *and* turn its
> Bluetooth off) — the machine allows one BLE link at a time. The machine can be
> on **any screen**: `save-slots` switches it into Pro mode to write and back to
> Auto after (see the protocol section), so you don't need to set the mode yourself.

### Push recipes to your app account (cloud)

Separately from BLE machine control, `xbloom cloud` can push recipes to your
xBloom **app account** via the *unofficial* xBloom cloud REST API, so a recipe
you define here shows up in the phone app. Needs the optional dependency:
`pip install "xbloom-ble[cloud]"`.

```bash
export XBLOOM_EMAIL=you@example.com XBLOOM_PASSWORD=…   # or `xbloom cloud login`
xbloom cloud sync my-recipe.yaml     # create-or-update a tool-owned recipe (idempotent)
xbloom cloud list                    # list account recipes ('*' = tool-owned)
xbloom cloud delete <tableId>        # only AUTO … recipes can be deleted
xbloom cloud fetch <share-url>       # read a publicly shared recipe (no auth)
```

> 🔒 **Safety: the tool only ever manages recipes it created.** Every recipe
> pushed via `sync` is named **`AUTO <name>`**, and `sync`/`delete` will **only**
> update or remove `AUTO …` recipes. Recipes you made by hand in the app are
> never modified or deleted. This is enforced in code (`update_recipe` /
> `delete_recipe` refuse a non-`AUTO` target) and covered by tests.

This uses a community-reverse-engineered, unofficial API (it may break, and it
touches your real account) — see [`cloud.py`](xbloom_ble/cloud.py) for the
mechanics (RSA-encrypted bodies, endpoints, field schema).

---

## Recipe format

> 📖 **Looking for recipes to start from?** Browse the community **[xBloom recipe ledger](https://xbloom.lodywgumce.tv)**
> — per-bean pour recipes (grind, temps, pour schedule) you can adapt to the format below.

Recipes are plain YAML:

```yaml
name: Example Washed
dose_g: 16          # coffee dose in grams
grind: 62           # grinder setting (1–80); or 0 = no-grind (brew pre-ground, grinder off)
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

### Optional brew-level metadata

A recipe may also carry optional **metadata** fields. These are *informational
context* for a UI, recipe site, or your own notes — they round-trip through YAML
but are **never sent to the machine** and are **not** range-checked against
hardware limits:

```yaml
kind: custom          # recipe kind / preset base (custom, medium-auto, …)
dripper: Omni         # the dripper/brewer used
water_ml: 240         # total brew water (may exceed Σ pours for bypass/iced brews)
hot_water_ml: 150     # iced: hot water poured over ice
ice_g: 85             # iced: ice weight
time: "~2:00"         # expected brew time, display string
note: strawberry-forward; ground finer as it aged
pours:
  - {label: Bloom, ml: 45, temp_c: 93, pattern: spiral, agitation: true, pause_s: 40, rpm: 100}
```

Each pour may also carry a `label` (e.g. `Bloom`, `Pour 1`). All of these are
optional; omit them and the recipe behaves exactly as before.

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
| `grind`       | 1–80, or 0     | **Firm (per xBloom Studio specs).** The grinder has 80 micro-steps (~18.75 µm each); a *lower* number is *finer*. **`0` = no-grind** (brew pre-ground, grinder off) — *observed*, see note below. |
| `temp_c` (pour) | 40–95 °C     | **Firm (per xBloom Studio specs).** Settable in 1 °C steps. The app also offers special non-numeric `RT` (room temp) and `BP` (boiling point) settings, outside this numeric range. |
| `stage_temps` | 40–130 °C each | Machine **preheat/stage set-points** (default 110/90 °C) — NOT the pour temperature, so they legitimately exceed the 95 °C pour cap. Wider allowance around the default. |
| `rpm`         | 0, or 60–120   | **Firm (per xBloom Studio specs).** 60–120 in 10-RPM steps; `0` (no agitation) is allowed only for `center` pours. |
| `flow_ml_s`   | 3.0–3.5 ml/s   | **Firm (per xBloom Studio specs).** Settable in 0.1 steps. |
| `pause_s`     | 0–255          | The wire byte is `256 − seconds` (so 0–255 fits), but the **on-machine countdown caps near 99 s** — treat 0–99 as the practical range. |
| `ml` (pour)   | 1–4000 ml      | Lower bound (≥1) is firm; a pour **over 127 ml is auto-split** by the protocol (not an error). The 4000 ceiling is just a sanity guard. |
| `pattern`     | `spiral`, `ring`, `center` | **Firm.** These are the decoded pattern codes; `agitation: true` is only valid with `spiral`. |

> **Source:** xBloom Studio published specifications.

#### No-grind (brew pre-ground)

Set **`grind: 0`** to brew **pre-ground** coffee — the machine's grinder toggle is
turned **off** and the grind step is skipped (put your ground coffee straight in the
dripper). This is *not* "grind at setting 0": a real `0` grinds at the finest setting.

On the wire it maps to a sentinel, not `0`:

- **BLE** — the `0x41` grind byte is sent as **`0xFE`** (the machine reads this as
  "skip the grinder" and leaves its stored grind size untouched).
- **Cloud** — the `grinderSize` field is **omitted** and `isSetGrinderSize` is set to
  off (matching an app-made no-grind recipe; sending `grinderSize: 0` makes the app
  show a literal "0").

> **Reverse-engineered, and confirmed on hardware.** The `0xFE` grind sentinel and the
> `0x44` grinder-off pours opcode were recovered from HCI captures (see
> [`docs/REVERSE-ENGINEERING.md`](docs/REVERSE-ENGINEERING.md)) and verified by driving a
> machine from this library (slot preset skips the grinder; a `grind: 0` recipe stages via
> `0x44`). The cloud behaviour is verified against an app-made recipe.

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

**Commands** written to `ffe1` (host → machine) are:

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

**Notifications** on `ffe2` (machine → host) use a **different** shape — see
[Status notifications](#status-notifications-ffe2) below.

**CRC-16/KERMIT:** polynomial `0x1021`, init `0`, reflected input and output, no
final XOR (check value `0x2189` for `b"123456789"`).

### GATT

Vendor service `0000e0ff-3c17-d293-8e48-14fe2e4da212`:

| Characteristic | Short | Role                |
|----------------|-------|---------------------|
| command        | `ffe1`| write               |
| status         | `ffe2`| notify (telemetry)  |
| aux            | `ffe3`| auxiliary           |

> ⚠️ **`ffe1` accepts only a *Write Command* (write-without-response, ATT `0x52`).**
> A *Write Request* (write-with-response, `0x12`) is rejected by the firmware with
> GATT "Unlikely Error" — verified against the vendor app, which never uses a Write
> Request on `ffe1`. Command acknowledgements come back as notifications on `ffe2`.

### The LOAD sequence

Sent to `ffe1`; ACKs come back as notifications on `ffe2` (the machine echoes the
command, e.g. `580207a6…`):

1. **`0xa4`** — session start. Constant payload `01 b9 00 00 00 01 00 00 00`.
2. **`0x56`** — status handshake. The machine replies with a status/info frame. On a
   fresh connection the machine will **not arm** until it has settled out of its
   post-connect transitional state, so the app sends this and pauses briefly before
   staging; this package does the same (a short settle after `a4`/`0x56`). Skipping it
   and firing the dose/temps/pours frames immediately gets no acks and never arms.
3. **`0xa6`** — dose. Dose in grams as a `u8` at payload offset 9.
4. **`0xa8`** — stage temps. `01` + f32 LE temp1 + f32 LE temp2 (default
   `110.0`, `90.0`).
5. **`0x41`** (grind) or **`0x44`** (grinder off / no-grind) — pours frame (see byte
   map below).

After the pours frame the machine reports STATE `0x1f` (armed). At that point you can
approve on the machine by hand, **or** start the brew remotely with three further
single-byte frames (each payload `01`, byte-exact from the app's capture):

6. **`0x42`** (seq `0x1f`) — **commit**: arm → `0x1e` (awaiting-confirm), ~99 s countdown.
7. **`0x46`** (seq `0x9e`) — **start**: begins brewing (`0x3b`).
8. **`0x47`** (seq `0x9e`) — **cancel**: aborts a committed/running brew.

`build_load_frames()` never includes the commit/start opcodes — loading only arms the
machine. Starting/cancelling is done through the dedicated `build_commit()` /
`build_start()` / `build_cancel()` builders, emitted only by an explicit
`start()` / `cancel_brew()` call.

### The pours frame payload (`0x41` / `0x44`)

The pours frame's opcode is **`0x41`** when the machine grinds, or **`0x44`** when the
grinder is **off** (no-grind / pre-ground). Both carry the identical body:

```
01 | LEN(u8 = #body bytes) | <pour segments…> | grind(u8) | ratio(u8)
```

- `grind` — the grinder setting `1–80`, **or `0xFE`** for a **no-grind** recipe (brew
  pre-ground; recipe `grind: 0` → wire `0xFE`, and the opcode becomes `0x44`) — see *No-grind*.
- `ratio` — the brew ratio **× 10** (water : coffee): `1:10 → 0x64`, `1:15 → 0x96`,
  `1:16 → 0xa0`. **The machine validates this against Σ(pour ml) / dose and rejects a load
  whose ratio byte doesn't match**, so it is derived from the recipe (not a fixed value).

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

### Programming the dial presets (Auto-Mode slots)

Auto Mode's three dial presets (A/B/C) are written with a **different command,
`0x2CF6`**, and — unlike the LOAD sequence — as a **batch of all three, with no
commit frame**. Each slot frame:

```
58 01 02 | f6 2c (=0x2CF6) | LEN(u32 LE) | 01 | SLOT(0/1/2) | FLAGS | <0x41 blob> | CRC16
```

- **`SLOT`** — `0`=A, `1`=B, `2`=C.
- **`FLAGS`** — `0x12` = store with the on-brew **scale enabled**, `0x02` =
  disabled (bit `0x10` is the scale flag).
- **`<0x41 blob>`** — the same `pours | grind | tail` body as the LOAD `0x41`
  frame, minus its leading `0x01`.

The write sequence (reverse-engineered from two app captures + confirmed on
hardware):

1. `0xa4` session start; wait for the machine to reach idle (`0x57` state `0x01`).
2. Write the **three** slot frames (A, B, C) back-to-back. The machine acks each
   with a `58 02 07 f6 2c … c2 d204` notification.
3. The machine then stores the whole set **atomically** — signalled by a `0xf8`
   notification and the status progression `0x43` (saving) → `0x25` (saved) →
   `0x01` (idle). **There is no commit frame.**

Writing a single slot (or adding a trailing "commit") leaves the machine hung at
`0x43` and it shows **RETRY** — the store only completes with the full A/B/C
batch. Like every other write here, `0x2CF6` is a preset write and **never starts
a brew**.

**Pro vs Auto mode (`0x2CF7`).** The machine only accepts slot writes in **Pro
mode** (status `0x01`, idle). In **Auto mode** — the on-machine A/B/C recipe
selector — it parks at status `0x41` and rejects the writes (RETRY). The mode is
set with command **`0x2CF7`**: `58 01 02 | f7 2c | LEN | 01 | <4 bytes> | CRC`,
where `00 00 00 00` = Pro and `91 32 78 56` = Auto. `save_slots` therefore sends
**Pro** before the batch and **Auto** after (so the fresh presets are ready to
pick on the dial). This too is only a mode switch — it never brews.

> The machine exposes **no way to read the current slots back** — the vendor app
> doesn't read them either; it just re-pushes whatever it last stored. Keep your
> recipes and re-run the batch to restore them.

### Status notifications (`ffe2`)

Notifications use their **own** frame shape (distinct from the command frames
above):

```
58 02 07 | TYPE(u8) | SUB(u8) | LEN(u32 LE) | 0xc1 | PAYLOAD | CRC16(u16 LE)
```

- **`TYPE`** (offset 3) is the frame kind:
  - a **command echo / ACK** — `TYPE` equals the command byte just written
    (`a4/a6/a8/41/…`), so an ACK is simply "the notification whose offset-3 byte
    matches my command" (e.g. `5802 07 a6 …` acks `0xa6`).
  - **`0x57`** — a **status** frame; the byte right after `0xc1` is the machine
    *state* (table below).
  - **`0x15` / `0x4b`** — idle **heartbeats** (ignored).
  - `0x49` carries a machine-info dump (serial + firmware string); `0x39` etc.
    carry live brew progress (best-effort, not needed for load-only).

**State byte** (inside a `0x57` frame, right after `0xc1`):

| State | Name               | Meaning                              |
|-------|--------------------|--------------------------------------|
| 0x01  | idle               | Idle / ready (also at brew end).     |
| 0x1d  | loading            | Recipe being received.               |
| 0x1f  | armed              | Recipe loaded, awaiting approval.    |
| 0x1e  | awaiting_confirm   | Waiting for the human to confirm.    |
| 0x3b  | brewing            | Brew in progress.                    |
| 0x41  | complete           | Brew complete; also = Auto-mode selector. |
| 0x43  | saving_slots       | Auto-Mode slot batch being stored.   |
| 0x25  | slots_saved        | Auto-Mode slots stored OK (→ idle).  |

The load path waits for state **`0x1f` (armed)**, which the machine reports right
after it ACKs the `0x41` pours frame — that's when it prompts the human to approve.

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
        await client.load_recipe(recipe)      # loads + arms only — never brews
        # then EITHER approve on the machine by hand, OR start it remotely:
        await client.start()                  # commit + start — ⚠️ dispenses hot water
        # (client.brew(recipe) = load_recipe + start; client.cancel_brew() aborts)
        await client.stream_telemetry(lambda ev: print(ev), duration=300)

asyncio.run(main())
```

`xbloom_ble.protocol` is pure (no BLE) and is the place to start if you want to
build a different front-end:

```python
from xbloom_ble.protocol import build_load_frames
frames = build_load_frames(recipe.to_protocol_dict())  # [a4, a6, a8, 41]
```

The cloud client (`pip install "xbloom-ble[cloud]"`) pushes to the app account;
`sync_recipe` is idempotent and only ever manages `AUTO …` recipes:

```python
from xbloom_ble.cloud import XBloomCloud
client = XBloomCloud(email="…", password="…")   # or XBLOOM_EMAIL/XBLOOM_PASSWORD
client.login()
client.sync_recipe(recipe)                        # create-or-update "AUTO <name>"
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
responsibility** for anything you do with your machine. By design, *loading* a
recipe only arms the machine and never brews on its own; **starting** a brew is a
separate, explicit action (`--start` / `start()`) that dispenses hot water — only
do it with the machine physically ready, and supervise it. No warranty (see
[LICENSE](LICENSE)).

## License

MIT © 2026 Janczykkkko

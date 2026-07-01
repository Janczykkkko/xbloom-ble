# xBloom Studio control (xbloom-ble)

Drive an **xBloom Studio** pour-over machine over Bluetooth LE with the `xbloom` CLI
(this repo — reverse-engineered protocol, no official API). Use this when the task is
to load a recipe, program the machine's dial presets, or push recipes to the app account.

## 🛑 The one safety rule (non-negotiable)
**The tool only ever LOADS a recipe / writes a preset — it NEVER starts a brew.** After a
load the **machine prompts and a human physically approves ON THE MACHINE** to start. The
force-start opcodes (`0x42`/`0x46`) are never built or sent, by design. Never try to add an
auto-start path.

## Before any BLE write
- **Bluetooth on**; on Linux `bluetoothd` running.
- **Disconnect the phone app** — the machine accepts a **single** BLE link, and the app holds
  it. Close the app *and* turn the phone's Bluetooth off, or writes fail with **RETRY**.
- Find the machine: `xbloom scan` (discovers by vendor service UUID / `XBLOOM…` name). Set
  `export XBLOOM_ADDRESS=AA:BB:CC:DD:EE:FF` to skip scanning.

## The two paths to get a recipe onto the machine

**▸ Path 1 — Load one recipe and brew it now.**
```bash
xbloom validate recipe.yaml           # check it first (no hardware)
xbloom brew recipe.yaml               # loads, then approve ON THE MACHINE to start
```
`brew` accepts a local path or an `http(s)://` URL. It streams telemetry until the brew
completes; a `telemetry-*.json` is written.

**▸ Path 2 — Program the three dial presets (Auto Mode), then brew with no phone.**
```bash
xbloom save-slots A.yaml B.yaml C.yaml            # program slots A / B / C
xbloom save-slots a.yaml b.yaml c.yaml --scale-off C   # slot C stored with the scale off
```
- **All three are required in one call** — the machine only stores the presets once it has the
  full A/B/C set (it saves atomically). A single-slot write hangs → **RETRY**. There is **no
  commit frame**.
- ⚠️ **The app can overwrite these.** Presets live on the machine; the xBloom app re-pushes its
  own A/B/C whenever a slot is reassigned in the app. There is **no way to read the machine's
  slots back** (the app doesn't either — it remembers what it last pushed). Keep the three
  recipes and re-run `save-slots` to restore them; program slots to drive the machine **from its
  dial, not the app**.

## Separately: the app account (cloud)
`xbloom cloud …` manages the recipe **library in the phone-app account** via the unofficial
cloud REST API — this is *not* a way to set the machine's dial presets. Tool-created recipes are
named `AUTO …` and are the only ones `sync`/`delete` will touch. Needs `pip install
"xbloom-ble[cloud]"`.

## Notes
- Recipe format, valid ranges, and the full reverse-engineered protocol (frames, CRC, the
  slot-save batch, status states) are in the [README](../../../README.md) and
  [`docs/REVERSE-ENGINEERING.md`](../../../docs/REVERSE-ENGINEERING.md).
- Library API: `XBloomClient.load_recipe(recipe)` and `XBloomClient.save_slots([a, b, c])`.

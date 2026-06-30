---
name: brew
description: Brew a coffee on an xBloom Studio with this repo's `xbloom` CLI — choose/write a recipe, validate it, load it onto the machine, then approve ON THE MACHINE to start. Use when the user wants to brew, load a recipe, or run a pour-over via xbloom-ble.
---

# Brew a coffee on the xBloom Studio

This skill drives the `xbloom` CLI in this repository to load a brew recipe onto an
xBloom Studio coffee machine over Bluetooth LE. It is **generic** — it works with any
recipe and any user's machine; nothing here is tied to a specific person or device.

## 🛑 Safety rule — read this first

**This tool only LOADS a recipe. It NEVER starts the brew.** After loading, the
machine prompts, and **the human adds beans + cup and physically approves the brew
ON THE MACHINE** to start it. There is intentionally no auto-start path (the protocol
opcodes that force-start a brew are never sent). Tell the user clearly that they must
approve on the machine — do not imply the tool will start brewing.

## Requirements

- The package is installed (`pip install -e .` from the repo root).
- **Bluetooth is on** and, on Linux, **BlueZ is running** (`bluetoothd`).
- The **xBloom mobile app is closed** — it holds the BLE connection, so the machine
  can't talk to two controllers at once.
- The machine is **powered on and in Bluetooth range**.
- A **bean capsule / ground coffee and a cup** are ready (but you don't load beans
  until the machine prompts).

## Steps

### 1. Choose or write a recipe

Recipes are plain YAML. There are examples in `recipes/` (e.g.
`recipes/example-washed.yaml`). The full recipe format and the accepted value ranges
are documented in the repo `README.md` (see "Recipe format" and "Recipe limits &
valid ranges"). A minimal recipe:

```yaml
name: My Brew
dose_g: 16          # 1–18 g (18 g is the app maximum)
grind: 62           # 1–80 (lower = finer)
pours:
  - {ml: 45,  temp_c: 93, pattern: spiral, agitation: true, pause_s: 40, rpm: 100, flow_ml_s: 3.0}
  - {ml: 100, temp_c: 91, pattern: spiral, pause_s: 10, rpm: 100, flow_ml_s: 3.2}
  - {ml: 95,  temp_c: 90, pattern: spiral, pause_s: 5,  rpm: 100, flow_ml_s: 3.2}
```

You need at least two pours (a bloom and a first pour).

### 2. Validate it (no hardware needed)

```bash
xbloom validate path/to/recipe.yaml
```

Fix any reported errors before continuing. Validation catches out-of-range values,
unknown patterns, too few pours, and (if you set a `ratio`) a pour total that doesn't
match `dose_g * ratio`.

### 3. Load it onto the machine

```bash
xbloom brew path/to/recipe.yaml --address AA:BB:CC:DD:EE:FF
```

Or set the address via the environment so nothing is hardcoded:

```bash
export XBLOOM_ADDRESS=AA:BB:CC:DD:EE:FF
xbloom brew path/to/recipe.yaml
```

Don't know the address? Run `xbloom scan` to discover the machine, or just run
`xbloom brew …` with no address and it will scan and use the first machine found.

The command validates, connects, and **loads** the recipe. It then prints:

```
✋ Recipe loaded. Add beans + cup, then APPROVE ON THE MACHINE to start. (This tool will NOT start it.)
```

### 4. The human approves ON THE MACHINE

Now the user adds the beans + cup and **physically confirms the brew on the
machine**. The tool does not — and cannot — start it for them. This is the safety
gate.

### 5. Telemetry streams

Once the brew starts, the tool streams live status (state changes and, during the
brew, water/coffee weights) until the brew completes or the timeout elapses
(`--timeout`, default 300 s). A telemetry log is written to
`./telemetry-<timestamp>.json` in the working directory.

## If something goes wrong

- **No machine found:** check Bluetooth is on, the app is closed, the machine is on
  and in range; try `xbloom scan`. On Linux, confirm `bluetoothd` is running and you
  have BLE permissions.
- **Validation fails:** read the error — it names the offending field and range.
- **Nothing brews after loading:** that's expected until the human approves on the
  machine. The tool stops at "loaded".

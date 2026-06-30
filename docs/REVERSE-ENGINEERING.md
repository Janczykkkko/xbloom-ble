# How the xBloom Studio BLE protocol was reverse-engineered

The xBloom Studio has no official API — the only way to control it is the vendor mobile app,
which talks to the machine over **Bluetooth Low Energy (BLE)**. This document explains, step by
step, how the brew protocol was recovered from that traffic, so the work is **reproducible** and
others can extend it. No special hardware is required — just an Android phone and a Linux box.

> **TL;DR of the method:** capture the BLE traffic between the app and the machine while running
> brews; parse it down to the GATT writes; find the frame format and checksum; then run many brews
> that each change *one* setting and **diff** the resulting command bytes to learn what every byte
> means.

---

## 1. Capture the BLE traffic (Android HCI snoop)

Android can log every Bluetooth packet to a file (`btsnoop_hci.log`) — including the GATT writes
the app sends to the machine.

1. Enable **Developer options** (tap *Build number* 7×).
2. Developer options → **Enable Bluetooth HCI snoop log** → set to *Enabled / All*.
3. **Toggle Bluetooth OFF then ON.** This is the easy-to-miss step: the log only starts a fresh
   capture after a Bluetooth restart. Skip it and you get an empty log.
4. Open the app, connect to the machine, and **drive it** — set a recipe, start a brew (you can
   cancel right after; the command is sent the instant you start).
5. Pull the log. The reliable phone-only way is **Developer options → Bug report → Full report**,
   which bundles `FS/data/log/bt/btsnoop_hci.log` inside the zip. (With a PC: `adb bugreport`.)

Tip: disconnect other BLE devices (watch, earbuds) first so the log is mostly machine traffic.

---

## 2. Parse the log down to GATT operations

`btsnoop_hci.log` is a [btsnoop](https://fte.com/webhelp/sodera/Content/Technical_Information/BT_Snoop_File_Format.htm)
file (v1, datalink type 1002 = HCI H4). Decoding it is a small pipeline:

```
btsnoop record  →  H4 packet  →  HCI ACL  →  L2CAP (reassemble fragments)  →  ATT PDU
```

- Each record wraps one H4 packet; the first byte is the type (`0x02` = ACL data).
- ACL carries L2CAP; **channel ID `0x0004`** is the **ATT** (GATT) channel.
- The interesting ATT opcodes:
  - `0x12` Write Request / `0x52` Write Command — **commands the app sends to the machine**.
  - `0x1b` Handle Value Notification — **status the machine sends back**.
  - `0x05`/`0x09`/`0x11` responses — used to map handles → characteristic UUIDs.

This reveals the vendor GATT service `0000e0ff-3c17-d293-8e48-14fe2e4da212` with three
characteristics: **`ffe1`** (the command channel, where all the writes go), **`ffe2`** (status
notifications), and `ffe3` (auxiliary). Everything below is the payload of writes to `ffe1`.

---

## 3. Find the frame format and the checksum

Lining up the `ffe1` writes, every command shares a shape:

```
58 01 01 | CMD(1) | SEQ(1) | LEN(2, little-endian) | 00 00 | PAYLOAD… | CHK(2)
```

`LEN` matches the total frame length, which points at the trailing two bytes being a **checksum**.
Identifying *which* checksum is a small brute-force: run a CRC-16 search (all common
poly/init/reflect combinations) over `frame[:-2]` and see which reproduces the last two bytes.
For the xBloom it is **CRC-16/KERMIT** (poly `0x1021`, init `0`, input & output reflected). Once
that's confirmed on a handful of frames, you can **forge new, valid frames**.

---

## 4. Differential decode — learn what every byte means

This is the core technique and the part worth copying. You can't know what a byte does by staring
at one capture. Instead:

1. Pick a **baseline** recipe.
2. Capture a series of brews where **each one changes exactly one setting** from the baseline
   (bloom pause, one pour's temperature, the grind, the pour pattern, the flow rate, …).
3. **Screenshot each recipe** as ground truth — and note the order, because all the runs land in
   one log.
4. **Diff** each run's command payload against the baseline. The byte(s) that moved are the field
   you changed. Because you know *which* setting changed, you get a direct byte → meaning mapping.

A huge accelerator: brew a recipe whose values you already know and **search the payload for those
numbers** in different encodings (`u8`, little-endian `u16`, value×10, etc.). Known values act as a
Rosetta Stone — e.g. a `3.0 ml/s` flow shows up as `0x1e` (30), a `92 °C` as `0x5c`.

For the xBloom this pinned the **pours frame** (command `0x41`):

```
0x41 payload = 01 | LEN | <pour records…> | grind(u8) | tail(u8)
```

Each pour is an **8-byte record**:

| offset | field | encoding |
|---|---|---|
| 0 | volume | ml (u8) |
| 1 | temperature | °C (u8) |
| 2 | pattern | spiral `0x02`, ring `0x01`, center `0x00` |
| 3 | agitation / pattern-modifier | `0x02` = spiral+agitation; `0x01` pairs with center |
| 4 | pause | **`(256 − seconds) & 0xFF`** |
| 5 | — | constant `0x00` |
| 6 | rpm | agitation rotation speed (`0` for center) |
| 7 | flow | **ml/s × 10** |

Two encodings worth calling out because they're non-obvious and only fall out of the diff:
the **pause is stored as `256 − seconds`**, and a **pour over 127 ml is split** into 127-ml lead
segments plus a remainder segment.

---

## 5. Map the brew lifecycle (the state machine)

Watching the `ffe2` notifications across a full run (load → start → finish) reveals the command
sequence and a **state byte** the machine reports:

- **Load** (what this library sends): `a4` (open session) → `a6` (dose) → `a8` (stage temps) →
  `0x41` (pours + grind). After a clean load the machine reports **state `0x1f` (armed)** and
  **prompts the user on the machine** to add beans and confirm.
- **Start**: `0x42` (commit) and `0x46` (start) are what the *app* sends when the user taps confirm.
  Sending `0x42` **force-starts the brew and bypasses the on-machine prompt.**
- Status states seen on `ffe2`: `0x01` idle · `0x1f` armed · `0x1e` awaiting-confirm · `0x3b`
  brewing · `0x43` brew-record (live water/coffee weights) · `0x41` complete.

This is the key safety insight behind the library: **loading the recipe is enough to make the
machine prompt the human.** So the tool only ever sends the load sequence (`a4 → a6 → a8 → 0x41`)
and **never** `0x42`/`0x46` — the person physically approves on the machine to start. A controller
*cannot* brew on an empty machine.

---

## 6. Validate

Two checks turn "probably right" into "proven":

1. **Byte-for-byte round-trip.** Rebuild the command frames from a parsed recipe and assert they
   equal the captured bytes (including the CRC) for every captured run. (See `tests/test_protocol.py`.)
2. **On real hardware.** Connect from a Linux host (BlueZ + [bleak](https://github.com/hbldh/bleak)),
   send only the load sequence, and confirm the machine arms and prompts — then a human approves and
   it brews the intended recipe.

---

## Reproducing this yourself

- **Capture:** Android HCI snoop (above), or [nRF Connect](https://www.nordicsemi.com/Products/Development-tools/nRF-Connect-for-mobile)
  to browse the live GATT table.
- **Parse:** any btsnoop reader (Wireshark opens them directly: filter `btatt`), or a ~150-line
  Python script doing the H4→ACL→L2CAP→ATT walk above.
- **Decode:** the one-variable-at-a-time differential method in §4 — it's slow but it's certain.

The full protocol reference (frame format, GATT, byte maps, status states) lives in the
[README](../README.md#reverse-engineered-protocol).

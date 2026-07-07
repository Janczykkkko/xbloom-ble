"""Byte-exact xBloom Studio BLE wire protocol.

This module is a pure (no-BLE) port of the *verified*, round-trip-proven
builders that were reverse-engineered from an Android Bluetooth HCI capture.
It is what lets the rest of the package talk to the machine without guessing.

Frame format
------------
Every command frame written to the ``ffe1`` characteristic is::

    58 01 01 | CMD(u8) | SEQ(u8) | LEN(u16le) | 00 00 | PAYLOAD | CRC16(u16le)

* ``58 01 01`` — constant header.
* ``CMD``      — command opcode (see below).
* ``SEQ``      — sequence byte. The load sequence uses ``0x1f`` (31).
* ``LEN``      — total frame length in bytes, little-endian, *including* header
  and CRC. Stored at offset 5.
* ``00 00``    — two constant zero bytes.
* ``PAYLOAD``  — command-specific body.
* ``CRC16``    — CRC-16/KERMIT over the whole frame except the last two bytes,
  stored little-endian.

CRC-16/KERMIT: polynomial ``0x1021``, init ``0``, reflected input and output,
no final XOR.

GATT
----
Vendor service ``0000e0ff-3c17-d293-8e48-14fe2e4da212`` exposes:

* ``ffe1`` — command (write).
* ``ffe2`` — status (notify).
* ``ffe3`` — aux.

The LOAD sequence (this package's only job)
-------------------------------------------
Sent frame-by-frame, waiting for each ACK on ``ffe2``:

1. ``0xa4`` — session start (constant payload ``01b900000001000000``).
2. ``0xa6`` — dose (grams as ``u8`` at payload offset 9).
3. ``0xa8`` — stage temps (``01`` + f32le temp1 + f32le temp2, default 110/90).
4. ``0x41`` — pours + grind.

After these four frames the machine reports STATE ``0x1f`` (armed/loaded). At
that point you can either approve the brew **on the machine** or start it
remotely (below), exactly like the official app.

Starting a brew (commit / start / cancel)
-----------------------------------------
Loading only *arms* the machine. To start the brew remotely — the way the app
does when you tap "Brew" — three further single-byte frames are used:

* ``0x42`` (seq ``0x1f``) — **commit**: the machine moves to ``0x1e``
  (awaiting-confirm) and shows its ~99 s add-beans countdown.
* ``0x46`` (seq ``0x9e``) — **start**: the "go" — the machine begins brewing.
* ``0x47`` (seq ``0x9e``) — **cancel**: abort a committed/running brew.

All three carry the constant one-byte payload ``01`` and were captured
byte-for-byte from the vendor app (:func:`build_commit`, :func:`build_start`,
:func:`build_cancel`).

⚠️ SAFETY — loading and starting are separate, explicit steps
-------------------------------------------------------------
Starting a brew physically dispenses near-boiling water. The design keeps that
deliberate: :func:`build_load_frames` returns **only** the four LOAD frames and
never a commit/start opcode, so *loading a recipe can never brew by accident*.
The commit/start frames live in their own builders and are only emitted when a
caller explicitly asks to start (or cancel) a brew. Never wire commit/start as a
side effect of loading — only in response to a clear, intentional "start" action
with the machine physically ready.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping

__all__ = [
    "PATTERN_CODES",
    "LOAD_SEQ",
    "crc16_kermit",
    "xbloom_frame",
    "build_a4",
    "build_a6",
    "build_a8",
    "build_41",
    "build_load_frames",
    "build_session_start",
    "build_status_query",
    "build_save_slot",
    "build_set_mode",
    "build_commit",
    "build_start",
    "build_cancel",
    "POURS_CMD_GRIND",
    "POURS_CMD_NO_GRIND",
    "NO_GRIND",
    "NO_GRIND_WIRE",
    "CMD_SAVE_SLOT",
    "CMD_SET_MODE",
    "LOAD_SEQ",
    "BREW_SEQ",
    "COMMIT_OPCODE",
    "START_OPCODE",
    "CANCEL_OPCODE",
]

# Sequence byte used for the load sequence, and for the brew (commit/start) phase.
LOAD_SEQ = 0x1F
BREW_SEQ = 0x9E

# Brew-control opcodes. These START (or cancel) a brew — they are NOT part of the
# load sequence and are only emitted by an explicit start/cancel call.
COMMIT_OPCODE = 0x42  # commit: arm → awaiting-confirm (seq 0x1f)
START_OPCODE = 0x46   # start: the "go" — begin brewing (seq 0x9e)
CANCEL_OPCODE = 0x47  # cancel: abort a committed/running brew (seq 0x9e)

# (pattern, agitation) -> (pat_byte, agit_byte). Verified combos from the
# capture; others are best-effort extrapolation.
PATTERN_CODES: dict[tuple[str, bool], tuple[int, int]] = {
    ("spiral", True): (0x02, 0x02),   # bloom (spiral + agitation ON)
    ("spiral", False): (0x02, 0x00),  # default spiral
    ("ring", False): (0x01, 0x00),    # ring / middle
    ("center", False): (0x00, 0x01),  # center single dot
}


def crc16_kermit(data: bytes) -> int:
    """CRC-16/KERMIT of ``data``.

    Polynomial ``0x1021``, init ``0``, reflected input and output, no final XOR.
    On an xBloom frame this is computed over the whole frame minus the trailing
    two CRC bytes, and stored little-endian.
    """
    crc = 0
    for byte in data:
        byte = int(f"{byte:08b}"[::-1], 2)
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return int(f"{crc:016b}"[::-1], 2)


def xbloom_frame(cmd: int, seq: int, payload: bytes) -> bytes:
    """Build a complete ``ffe1`` command frame.

    ``58 01 01 | cmd | seq | len_u16le | 00 00 | payload | crc16le``.
    """
    body = bytes([0x58, 0x01, 0x01, cmd, seq]) + b"\x00\x00" + b"\x00\x00" + payload
    total = len(body) + 2
    frame = bytearray(body)
    frame[5:7] = struct.pack("<H", total)
    crc = crc16_kermit(bytes(frame))
    return bytes(frame) + struct.pack("<H", crc)


# ---------------------------------------------------------------------------
# Payload builders (no frame header / CRC)
# ---------------------------------------------------------------------------
def build_a4() -> bytes:
    """0xa4 session-start payload (observed constant)."""
    return bytes.fromhex("01b900000001000000")


def build_a6(dose_g: int) -> bytes:
    """0xa6 dose payload: dose grams as ``u8`` at offset 9."""
    pl = bytearray(13)
    pl[0] = 0x01
    pl[9] = int(dose_g) & 0xFF
    return bytes(pl)


def build_a8(temp1: float = 110.0, temp2: float = 90.0) -> bytes:
    """0xa8 stage-temps payload: ``01`` + f32le(temp1) + f32le(temp2).

    The captured standard case is ``01 0000dc42 0000b442`` = 110.0, 90.0.
    """
    f1 = struct.pack("<f", float(temp1))
    f2 = struct.pack("<f", float(temp2))
    return bytes([0x01]) + f1 + f2


def _pour_segments(p: Mapping) -> list[bytes]:
    """Turn one logical pour dict into a list of segment byte-strings.

    ``p`` keys: ``ml``, ``temp``, ``pattern`` ('spiral'|'center'|'ring'),
    ``agitation`` (bool), ``pause`` (seconds, post-pour), ``rpm`` (int),
    ``flow`` (ml/s float).

    8-byte pour segment: ``[ml, temp, pat, agit, negpause, 00, rpm, flow*10]``.
    A pour whose volume exceeds 127 ml is split into 127-ml 4-byte lead
    segments followed by an 8-byte remainder carrying flow/pause/rpm.
    """
    pat, agit = PATTERN_CODES[(p.get("pattern", "spiral"), bool(p.get("agitation", False)))]
    ml = int(p["ml"])
    temp = int(p["temp"]) & 0xFF
    pause = int(p.get("pause", 0))
    rpm = int(p.get("rpm", 0)) & 0xFF
    flow10 = int(round(float(p.get("flow", 3.0)) * 10)) & 0xFF
    negpause = (256 - pause) & 0xFF
    segs: list[bytes] = []
    remaining = ml
    while remaining > 127:
        segs.append(bytes([127, temp, pat, agit]))
        remaining -= 127
    segs.append(bytes([remaining & 0xFF, temp, pat, agit, negpause, 0x00, rpm, flow10]))
    return segs


# Grind byte sentinel — "no-grind" / brew pre-ground (grinder off).
# A recipe grind of ``0`` is a request to SKIP the grinder (brew already-ground
# coffee), not to grind at setting 0. On the wire the machine reads a valid grind
# as ``1–80``; the app encodes "grinder off" as the out-of-range byte ``0xFE`` and
# leaves the machine's stored grind SIZE untouched. (Observed in an HCI capture of
# the app's grinder-OFF save; sending an actual ``0`` grinds at the finest setting.)
NO_GRIND = 0            # recipe-level grind meaning "don't grind" (pre-ground)
NO_GRIND_WIRE = 0xFE    # the byte the machine reads as "skip the grinder"


def _grind_byte(grind: int) -> int:
    """Map a recipe grind to its wire byte: ``0`` (no-grind) → ``0xFE``, else the grind."""
    return NO_GRIND_WIRE if int(grind) == NO_GRIND else int(grind) & 0xFF


def build_41(pours: Iterable[Mapping], grind: int, tail: int = 0xA0) -> bytes:
    """0x41 pours+grind payload: ``01 | LEN(u8) | <segments> | grind | tail``.

    A ``grind`` of ``0`` is the **no-grind** sentinel (brew pre-ground): it is
    emitted as the wire byte ``0xFE``, which tells the machine to skip the grinder.
    """
    segs: list[bytes] = []
    for i, p in enumerate(pours):
        # RPM is carried ONLY on the first pour — the machine zeroes it on later
        # pours (verified byte-for-byte against the vendor app's captures).
        segs.extend(_pour_segments({**p, "rpm": 0} if i else p))
    body = b"".join(segs)
    return bytes([0x01, len(body) & 0xFF]) + body + bytes([_grind_byte(grind), tail & 0xFF])


# Pours-frame opcode: 0x41 when the machine grinds, 0x44 when the grinder is OFF
# (no-grind / pre-ground). Both carry the same pours+grind+ratio body; only the
# opcode differs. (Verified against the vendor app's HCI captures + on-machine.)
POURS_CMD_GRIND = 0x41
POURS_CMD_NO_GRIND = 0x44


def _ratio_byte(recipe: Mapping) -> int:
    """The pours-frame's trailing byte: the brew **ratio × 10** (water:coffee).

    e.g. 1:10 → 0x64, 1:15 → 0x96, 1:16 → 0xa0. The machine validates this against
    Σ(pour ml) / dose and REJECTS a load whose ratio byte doesn't match — so it must
    be derived from the recipe, not fixed. An explicit ``tail`` overrides (edge cases)."""
    if recipe.get("tail") is not None:
        return int(recipe["tail"]) & 0xFF
    total = sum(int(p["ml"]) for p in recipe["pours"])
    dose = int(recipe.get("dose", 0))
    return (round(total / dose * 10) & 0xFF) if dose else 0xA0


def build_load_frames(recipe: Mapping) -> list[bytes]:
    """Build the ordered list of LOAD frames for a recipe.

    Returns exactly ``[a4, a6, a8, pours]`` — the four frames that *load* the
    recipe onto the machine. The pours opcode is ``0x41`` normally, or ``0x44``
    for a **no-grind** recipe (``grind == 0``, brew pre-ground). It **never**
    includes ``0x42`` (commit) or ``0x46`` (start): loading only arms the machine,
    so a load can never brew by accident. To start a brew, call the dedicated
    :func:`build_commit`/:func:`build_start` builders explicitly.

    ``recipe`` may be a plain dict (with keys ``dose``, ``grind``, optional
    ``stage_temps``, optional ``tail``, optional ``seq``, and ``pours``) or any
    mapping providing the same keys. :class:`xbloom_ble.recipe.Recipe` exposes
    a ``to_protocol_dict()`` producing exactly this shape.
    """
    seq = recipe.get("seq", LOAD_SEQ)
    t1, t2 = recipe.get("stage_temps", (110.0, 90.0))
    tail = _ratio_byte(recipe)                                   # ratio × 10, derived
    pours_cmd = POURS_CMD_NO_GRIND if int(recipe["grind"]) == 0 else POURS_CMD_GRIND
    frames = [
        xbloom_frame(0xA4, seq, build_a4()),
        xbloom_frame(0xA6, seq, build_a6(recipe["dose"])),
        xbloom_frame(0xA8, seq, build_a8(t1, t2)),
        xbloom_frame(pours_cmd, seq, build_41(recipe["pours"], recipe["grind"], tail)),
    ]
    # Belt-and-braces: loading is load-only. A commit/start opcode must never ride
    # in on the LOAD sequence — starting a brew is always a separate, explicit call.
    for fr in frames:
        if fr[3] in (COMMIT_OPCODE, START_OPCODE, CANCEL_OPCODE):  # pragma: no cover
            raise AssertionError("load frames must never contain a brew-start/cancel opcode")
    return frames


def build_commit() -> bytes:
    """The ``0x42`` **commit** frame — arms → awaiting-confirm.

    After a recipe is loaded (machine at STATE ``0x1f`` armed), sending this moves
    the machine to STATE ``0x1e`` (awaiting-confirm) with its ~99 s add-beans
    countdown — the same frame the vendor app sends when you tap "Brew". Constant
    payload ``01``, seq ``0x1f``. Byte-exact vs the app's capture
    (``580101421f0c000000017fcf``).

    ⚠️ This is a brew-control frame: it is a step toward physically starting a
    brew. Emit it only in response to an explicit start action.
    """
    return xbloom_frame(COMMIT_OPCODE, LOAD_SEQ, b"\x01")


def build_start() -> bytes:
    """The ``0x46`` **start** frame — the "go" that begins brewing.

    Sent after :func:`build_commit` (machine at ``0x1e``); the machine begins
    brewing (STATE ``0x3b``). Constant payload ``01``, seq ``0x9e`` (the brew
    phase id). Byte-exact vs the app's capture (``580101469e0c0000000180a1``).

    ⚠️ This physically dispenses near-boiling water. Emit it only when the machine
    is ready and someone intends to brew.
    """
    return xbloom_frame(START_OPCODE, BREW_SEQ, b"\x01")


def build_cancel() -> bytes:
    """The ``0x47`` **cancel** frame — abort a committed/running brew.

    Returns the machine toward idle without completing the brew. Constant payload
    ``01``, seq ``0x9e``. Byte-exact vs the app's capture
    (``580101479e0c00000001553e``).
    """
    return xbloom_frame(CANCEL_OPCODE, BREW_SEQ, b"\x01")


def build_session_start() -> bytes:
    """The ``0xa4`` session-start frame the app sends once, right after connecting.

    :meth:`XBloomClient.save_slots` sends this before the slot writes so the
    machine is in a live session and reaches its idle/ready state; the same frame
    is the first of the LOAD sequence. Carries no brew-start opcode.
    """
    return xbloom_frame(0xA4, LOAD_SEQ, build_a4())


def build_status_query() -> bytes:
    """The ``0x56`` status/handshake frame the app sends right after ``a4`` on connect.

    The machine replies with a status/info notification. Empirically the machine will
    not arm a freshly-connected session until it has settled past its post-connect
    transitional state; the app sends this (then waits) before staging a recipe, and
    :meth:`XBloomClient.load_recipe` does the same so the load reliably reaches the
    armed state. Carries no brew-start opcode.
    """
    return xbloom_frame(0x56, LOAD_SEQ, b"\x01")


# Easy-Mode preset slots (A/B/C = 0/1/2). Programming the slots writes a preset
# onto the machine; it does NOT brew.
#
# ⚠️ Slot save is a BATCH-OF-THREE, no-commit operation (reverse-engineered from
# two vendor-app captures + confirmed on hardware). The app writes all three
# slots (A, B, C) as ``0x2CF6`` frames back-to-back — each acked by the machine
# with a ``58 02 07 f6 2c … c2 d204`` notification — and then the machine saves
# the whole set atomically, signalled by a ``0xf8`` notify and the status
# progression ``0x43`` (saving) → ``0x25`` (saved) → ``0x01`` (idle). There is NO
# separate "commit" frame: writing a single slot (or adding a trailing commit)
# leaves the machine hung at ``0x43`` and it shows RETRY. So the client always
# writes all three at once. See :meth:`XBloomClient.save_slots`.
CMD_SAVE_SLOT = 0x2CF6  # 11510
SLOT_FLAG_SCALE_ON = 0x12
SLOT_FLAG_SCALE_OFF = 0x02


def build_save_slot(recipe: Mapping, slot: int, scale: bool = True) -> bytes:
    """Build the frame that writes ``recipe`` to Easy-Mode preset ``slot`` (0=A, 1=B, 2=C).

    Frame::

        58 01 02 | f6 2c(=0x2CF6) | LEN(u32 LE) | 01 | slot | flags | <0x41 blob> | CRC16

    ``flags`` is ``0x12`` with the on-brew **scale enabled** (the default) or
    ``0x02`` with it disabled. The ``<0x41 blob>`` is the same pours+grind+ratio
    body as the LOAD ``0x41`` frame (minus its leading ``0x01``).

    This programs a preset only — it never starts a brew (the command is
    ``0x2CF6``, never ``0x42``/``0x46``). Verified byte-for-byte against the
    vendor app's captured slot writes. Note the machine only *stores* the slots
    once all three (A/B/C) have been written in one batch — see
    :meth:`XBloomClient.save_slots`.
    """
    if slot not in (0, 1, 2):
        raise ValueError(f"slot must be 0 (A), 1 (B) or 2 (C); got {slot!r}")
    tail = _ratio_byte(recipe)                               # ratio × 10, derived (matches the app)
    blob = build_41(recipe["pours"], recipe["grind"], tail)  # 01 | len | pours | grind | tail
    flags = SLOT_FLAG_SCALE_ON if scale else SLOT_FLAG_SCALE_OFF
    payload = bytes([0x01, slot, flags]) + blob[1:]          # drop the 0x41 leading 0x01
    body = bytearray(bytes([0x58, 0x01, 0x02]) + struct.pack("<H", CMD_SAVE_SLOT)
                     + b"\x00\x00\x00\x00" + payload)
    body[5:9] = struct.pack("<I", len(body) + 2)             # 4-byte LEN incl. CRC
    return bytes(body) + struct.pack("<H", crc16_kermit(bytes(body)))


# Machine operating mode (verified from an HCI capture of the app's mode toggle).
# Slot writes are ONLY accepted in PRO mode — in AUTO mode (the on-machine A/B/C recipe
# selector) the machine sits in status 0x41 and rejects them (RETRY). PRO mode drops it to
# status 0x01 (idle), where saves land. So :meth:`XBloomClient.save_slots` forces PRO first.
CMD_SET_MODE = 0x2CF7  # 11511
MODE_PRO_PAYLOAD = bytes.fromhex("00000000")   # → status 0x01 (idle); slot writes accepted
MODE_AUTO_PAYLOAD = bytes.fromhex("91327856")  # → status 0x41; the A/B/C preset selector


def build_set_mode(pro: bool = True) -> bytes:
    """Build the frame that switches the machine between PRO and AUTO mode.

    Frame: ``58 01 02 | f7 2c(=0x2CF7) | LEN(u32 LE) | 01 | <4-byte mode> | CRC16``. ``pro=True``
    selects PRO mode (``00000000`` → status ``0x01`` idle, where slot writes are accepted);
    ``pro=False`` selects AUTO mode (``91327856`` → the on-machine A/B/C recipe selector). This
    only changes the display mode — it never brews. Byte-exact vs the vendor app.
    """
    payload = bytes([0x01]) + (MODE_PRO_PAYLOAD if pro else MODE_AUTO_PAYLOAD)
    body = bytearray(bytes([0x58, 0x01, 0x02]) + struct.pack("<H", CMD_SET_MODE)
                     + b"\x00\x00\x00\x00" + payload)
    body[5:9] = struct.pack("<I", len(body) + 2)
    return bytes(body) + struct.pack("<H", crc16_kermit(bytes(body)))

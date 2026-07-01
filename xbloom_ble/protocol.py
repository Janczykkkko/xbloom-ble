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

The LOAD sequence (the default ``brew`` command — load-only)
------------------------------------------------------------
Sent frame-by-frame, waiting for each ACK on ``ffe2``:

1. ``0xa4`` — session start (constant payload ``01b900000001000000``).
2. ``0xa6`` — dose (grams as ``u8`` at payload offset 9).
3. ``0xa8`` — stage temps (``01`` + f32le temp1 + f32le temp2, default 110/90).
4. ``0x41`` — pours + grind. Its **tail byte = ``round(ratio × 10)``** (the brew
   ratio, e.g. 1:16 → ``0xa0``, 1:17 → ``0xaa``); the byte before it is the
   grinder size.

After these four frames the machine reports STATE ``0x1f`` (armed/loaded) and
**waits for the human to approve the brew on the machine itself**.

Command-code convention
-----------------------
The reference decode (brAzzi64/xbloom-ble) names commands by a 16-bit code
(e.g. ``8102`` = ``0x1FA6``). On the wire the two bytes at frame offsets 3–4 are
that code little-endian, which in *this* module's ``CMD(u8) | SEQ(u8)`` framing
reads as ``cmd = low byte`` and ``seq = high byte``. So brAzzi64 ``0x1FA6`` =
our ``cmd 0xA6, seq 0x1F`` — i.e. our load opcodes ``a4/a6/a8`` are brAzzi64's
``8100/8102/8104``. The lower-level builders below carry a note with the
brAzzi64 code they port.

⚠️ SAFETY — the LOAD path never emits a brew-start opcode
---------------------------------------------------------
The protocol also defines opcodes that force-start a brew:

* ``0x42`` — commit.
* ``0x46`` — start / force-start.

Sending those bypasses the human-approval step. :func:`build_load_frames` (the
default ``brew`` path) returns **only** the four LOAD frames and never contains
``0x42``/``0x46``. Brew-start lives in a *separate, explicit, opt-in* builder,
:func:`build_start_frames`, used solely by the gated ``xbloom start`` /
``xbloom brew --start`` command — the ONLY place those bytes may appear.
"""

from __future__ import annotations

import struct
from typing import Iterable, Mapping

__all__ = [
    "PATTERN_CODES",
    "LOAD_SEQ",
    "COMMAND_SEQ",
    "crc16_kermit",
    "xbloom_frame",
    "type1_frame",
    "type2_frame",
    "ratio_to_tail",
    "build_a4",
    "build_a6",
    "build_a8",
    "build_41",
    "build_load_frames",
    "build_machine_info_query",
    "build_scale_tare",
    "build_scale_units",
    "build_grind",
    "build_grind_stop",
    "build_handshake",
    "build_bypass_dose",
    "build_set_cup",
    "build_pour_frames",
    "build_save_slot",
    "build_start_frames",
    "SCALE_UNITS",
    "FORBIDDEN_COMMIT_OPCODE",
    "FORBIDDEN_START_OPCODE",
]

# Sequence byte used for the load sequence.
LOAD_SEQ = 0x1F
# The lower-level control commands (scale/grind/pour/slot/start) all carry the
# same 0x1f "session" seq byte on the wire (they are the high byte of the
# reference's 16-bit command code — see the module docstring).
COMMAND_SEQ = 0x1F

# Opcodes that force-start a brew. Documented here so it is unmistakable that
# they exist — and that the LOAD path never builds or sends them (they may
# appear ONLY in :func:`build_start_frames`, the explicit opt-in path).
FORBIDDEN_COMMIT_OPCODE = 0x42  # commit
FORBIDDEN_START_OPCODE = 0x46  # start / force-start

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
        byte = int("{:08b}".format(byte)[::-1], 2)
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return int("{:016b}".format(crc)[::-1], 2)


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


def _split_cmd(code: int) -> tuple[int, int]:
    """Split a reference 16-bit command code into (cmd_low, seq_high) bytes."""
    return code & 0xFF, (code >> 8) & 0xFF


def type1_frame(code: int, args: Iterable[float | int] = ()) -> bytes:
    """Build a *Type-1* command frame (a ``01`` marker + N little-endian args).

    The reference (brAzzi64) documents most control commands as a 16-bit command
    ``code`` whose payload is a leading ``0x01`` followed by N 4-byte
    little-endian values — floats where the field is a float (temps, volumes),
    unsigned ints otherwise. ``code`` is placed as ``cmd = low byte`` /
    ``seq = high byte`` (see the module docstring).
    """
    payload = bytearray([0x01])
    for a in args:
        if isinstance(a, float):
            payload += struct.pack("<f", a)
        else:
            payload += struct.pack("<i", int(a))
    cmd, seq = _split_cmd(code)
    return xbloom_frame(cmd, seq, bytes(payload))


def type2_frame(code: int, blob: bytes) -> bytes:
    """Build a *Type-2* command frame (a ``01`` marker + a raw hex blob).

    Used by the Easy-Mode slot write (``11510``), whose payload is a leading
    ``0x01`` then ``[slot_idx][flags][recipe_blob]`` raw bytes.
    """
    cmd, seq = _split_cmd(code)
    return xbloom_frame(cmd, seq, bytes([0x01]) + bytes(blob))


def ratio_to_tail(ratio: float) -> int:
    """The ``0x41`` tail byte for a brew ``ratio``: ``round(ratio × 10) & 0xff``.

    The reference's ``grandWater`` field is the brew RATIO (not total water):
    the frame's last byte is ``round(ratio*10)``. 1:16 → ``0xa0`` (160), 1:17 →
    ``0xaa`` (170).
    """
    return int(round(float(ratio) * 10)) & 0xFF


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


def build_41(pours: Iterable[Mapping], grind: int, tail: int = 0xA0) -> bytes:
    """0x41 pours+grind payload: ``01 | LEN(u8) | <segments> | grind | tail``.

    The final two bytes are ``[grinder_size][ratio×10]`` — the ``tail`` is the
    brew ratio encoded as ``round(ratio*10)`` (see :func:`ratio_to_tail`). It
    defaults to ``0xa0`` (a 1:16 ratio) when a recipe supplies no ratio;
    :func:`build_load_frames` computes it from ``recipe['ratio']`` when present.
    """
    body = b"".join(seg for p in pours for seg in _pour_segments(p))
    return bytes([0x01, len(body) & 0xFF]) + body + bytes([int(grind) & 0xFF, tail & 0xFF])


def build_load_frames(recipe: Mapping) -> list[bytes]:
    """Build the ordered list of LOAD frames for a recipe.

    Returns exactly ``[a4, a6, a8, 41]`` — the four frames that *load* the
    recipe onto the machine. It does **not** include ``0x42`` (commit) or
    ``0x46`` (start): the human approves the brew on the machine.

    ``recipe`` may be a plain dict (with keys ``dose``, ``grind``, optional
    ``ratio``, optional ``stage_temps``, optional ``tail``, optional ``seq``,
    and ``pours``) or any mapping providing the same keys.
    :class:`xbloom_ble.recipe.Recipe` exposes a ``to_protocol_dict()`` producing
    exactly this shape.

    The ``0x41`` tail byte is derived from ``recipe['ratio']`` when present
    (``round(ratio*10)`` — see :func:`ratio_to_tail`); otherwise it falls back
    to an explicit ``recipe['tail']`` or the ``0xa0`` (1:16) default.
    """
    seq = recipe.get("seq", LOAD_SEQ)
    t1, t2 = recipe.get("stage_temps", (110.0, 90.0))
    ratio = recipe.get("ratio")
    if ratio is not None:
        tail = ratio_to_tail(ratio)
    else:
        tail = recipe.get("tail", 0xA0)
    frames = [
        xbloom_frame(0xA4, seq, build_a4()),
        xbloom_frame(0xA6, seq, build_a6(recipe["dose"])),
        xbloom_frame(0xA8, seq, build_a8(t1, t2)),
        xbloom_frame(0x41, seq, build_41(recipe["pours"], recipe["grind"], tail)),
    ]
    # Belt-and-braces safety assertion: never let a forbidden opcode out.
    for fr in frames:
        if fr[3] in (FORBIDDEN_COMMIT_OPCODE, FORBIDDEN_START_OPCODE):  # pragma: no cover
            raise AssertionError("load frames must never contain a brew-start opcode")
    return frames


# ---------------------------------------------------------------------------
# Lower-level control frame builders (ported from brAzzi64/xbloom-ble PROTOCOL.md)
#
# These are EXPLICIT actions that act on the machine. They are exposed only via
# the gated `xbloom scale/grind/pour/save-slot/start` subcommands, never by the
# default load-only `brew` path. Each notes the reference (brAzzi64) 16-bit
# command code it ports (see the module docstring for the code↔cmd/seq mapping).
# ---------------------------------------------------------------------------

# Reference command codes (16-bit; low byte = our cmd, high byte = our seq).
CMD_HANDSHAKE = 0x1FA4      # 8100 — handshake / session open ([185, 1])
CMD_BYPASS_DOSE = 0x1FA6    # 8102 — bypass volume/temp + dose
CMD_SET_CUP = 0x1FA8        # 8104 — cup weight range [max, min]
CMD_MACHINE_INFO = 0x9E49   # 40521 — machine-info query
CMD_SCALE_TARE = 0x2134     # 8500 — tare / zero the scale
CMD_SCALE_UNITS = 0x1F45    # 8005 — weight-unit switch (0=g, 1=oz, 2=ml)
CMD_GRIND_START = 0x0DAC    # 3500 — grinder start [1000, size, speed]
CMD_GRIND_STOP = 0x0DB1     # 3505 — grinder stop
CMD_POUR_RECIPE = 0x1F44    # 8004 — single-pour / FreeSolo recipe blob
CMD_EXECUTE = 0x119A        # 4506 — execute / start brew
CMD_SAVE_SLOT = 0x2CF6      # 11510 — Easy-Mode slot preset write

#: Weight-unit codes for :func:`build_scale_units`.
SCALE_UNITS: dict[str, int] = {"g": 0, "oz": 1, "ml": 2}

#: Easy-Mode slot indices (A/B/C → 0/1/2), keyed by the 1-based slot number.
SLOT_INDICES: dict[int, int] = {1: 0, 2: 1, 3: 2}

#: Slot flag bits (brAzzi64): grinder ON = 0x02, grinder OFF = 0x04, scale = 0x10.
SLOT_FLAG_GRINDER_ON = 0x02
SLOT_FLAG_GRINDER_OFF = 0x04
SLOT_FLAG_SCALE = 0x10


def build_machine_info_query() -> bytes:
    """Query the machine-info blob (serial/firmware/water/units). Read-only.

    Reference command ``40521``; a no-arg Type-1 request. The reply arrives as a
    notification decoded by :func:`xbloom_ble.telemetry.parse_machine_info`.
    """
    return type1_frame(CMD_MACHINE_INFO, [])


def build_scale_tare() -> bytes:
    """Zero (tare) the scale. Reference command ``8500`` (no args)."""
    return type1_frame(CMD_SCALE_TARE, [])


def build_scale_units(unit: str) -> bytes:
    """Set the weight unit. ``unit`` ∈ {``g``, ``oz``, ``ml``}.

    Reference command ``8005`` with one int arg (0=g, 1=oz, 2=ml).
    """
    key = str(unit).lower()
    if key not in SCALE_UNITS:
        raise ValueError(f"unknown scale unit {unit!r} (want one of {sorted(SCALE_UNITS)})")
    return type1_frame(CMD_SCALE_UNITS, [SCALE_UNITS[key]])


def build_grind(size: int, speed: int = 90) -> bytes:
    """Run the grinder standalone. Reference command ``3500`` = ``[1000, size, speed]``.

    ``size`` is the grind setting (1–80, lower = finer); ``speed`` is the burr
    speed (60–120 in 10-unit steps). The machine grinds the currently-loaded
    dose — this does NOT brew.
    """
    return type1_frame(CMD_GRIND_START, [1000, int(size), int(speed)])


def build_grind_stop() -> bytes:
    """Stop the grinder. Reference command ``3505`` (no args)."""
    return type1_frame(CMD_GRIND_STOP, [])


def build_handshake() -> bytes:
    """Session handshake preamble. Reference command ``8100`` = ``[185, 1]``."""
    return type1_frame(CMD_HANDSHAKE, [185, 1])


def build_bypass_dose(dose_g: int, bypass_vol: float = 0.0, bypass_temp: float = 0.0) -> bytes:
    """Dose + bypass preamble. Reference command ``8102`` = ``[vol, temp, dose]``.

    ``bypass_vol``/``bypass_temp`` default to 0.0 (bypass OFF). ``dose_g`` is the
    coffee dose in grams (int).
    """
    return type1_frame(CMD_BYPASS_DOSE, [float(bypass_vol), float(bypass_temp), int(dose_g)])


def build_set_cup(cup_max: float = 200.0, cup_min: float = 80.0) -> bytes:
    """Cup weight-range preamble. Reference command ``8104`` = ``[max, min]`` (floats)."""
    return type1_frame(CMD_SET_CUP, [float(cup_max), float(cup_min)])


def build_pour_frames(
    ml: int,
    temp: int,
    *,
    flow: float = 3.0,
    pattern: str = "spiral",
    agitation: bool = False,
    rpm: int = 90,
    dose_g: int = 0,
    tare: bool = True,
) -> list[bytes]:
    """FreeSolo single-pour sequence (dispenses water) — an EXPLICIT action.

    Ports brAzzi64's single-pour flow: handshake → bypass+dose (``8102``) →
    set-cup (``8104``) → optional scale tare (``8500``) → recipe blob
    (``8004``, one pour) → execute (``4506``).

    Returns the ordered list of frames. **This dispenses hot water** and is only
    reachable via the gated ``xbloom pour`` command.
    """
    pour = {
        "ml": int(ml),
        "temp": int(temp),
        "pattern": pattern,
        "agitation": bool(agitation),
        "pause": 0,
        "rpm": int(rpm),
        "flow": float(flow),
    }
    # The pour recipe blob reuses the 0x41 body shape (single pour, no grind/ratio
    # tail — a FreeSolo pour just dispenses water at the given settings).
    blob = b"".join(_pour_segments(pour))
    recipe_payload = bytes([0x01, len(blob) & 0xFF]) + blob
    frames = [
        build_handshake(),
        build_bypass_dose(dose_g),
        build_set_cup(),
    ]
    if tare:
        frames.append(build_scale_tare())
    cmd, seq = _split_cmd(CMD_POUR_RECIPE)
    frames.append(xbloom_frame(cmd, seq, recipe_payload))
    frames.append(type1_frame(CMD_EXECUTE, []))
    return frames


def build_save_slot(
    slot: int,
    recipe: Mapping,
    *,
    scale: bool = True,
    grinder: bool = True,
) -> bytes:
    """Write an Easy-Mode preset to slot ``1``/``2``/``3`` (A/B/C). No brew.

    Reference command ``11510`` (Type-2): payload ``[slot_idx][flags][recipe_blob]``.
    ``flags`` is a bitfield — grinder ON=0x02 / OFF=0x04, scale=0x10 (brAzzi64).
    ``recipe`` is the same protocol dict :func:`build_load_frames` consumes; the
    embedded blob reuses the ``0x41`` pours+grind+ratio-tail encoding.
    """
    if slot not in SLOT_INDICES:
        raise ValueError(f"slot must be 1, 2 or 3 (got {slot!r})")
    flags = SLOT_FLAG_GRINDER_ON if grinder else SLOT_FLAG_GRINDER_OFF
    if scale:
        flags |= SLOT_FLAG_SCALE
    ratio = recipe.get("ratio")
    tail = ratio_to_tail(ratio) if ratio is not None else int(recipe.get("tail", 0xA0)) & 0xFF
    blob = build_41(recipe["pours"], recipe["grind"], tail)
    body = bytes([SLOT_INDICES[slot], flags & 0xFF]) + blob
    return type2_frame(CMD_SAVE_SLOT, body)


def build_start_frames(recipe: Mapping) -> list[bytes]:
    """⚠️ Build the full brew-start sequence — THE ONLY builder that starts a brew.

    This is the explicit, opt-in path behind ``xbloom start`` / ``xbloom brew
    --start``. It loads the recipe (the four LOAD frames) and then sends the
    brew-start: the ``8102``/``8104`` preamble, a scale tare, and the execute
    opcode. Emitting this WILL start a brew on the machine.

    Kept strictly separate from :func:`build_load_frames` so the default path can
    never reach a start opcode.
    """
    frames = list(build_load_frames(recipe))
    frames += [
        build_handshake(),
        build_bypass_dose(int(recipe["dose"])),
        build_set_cup(),
        build_scale_tare(),
        type1_frame(CMD_EXECUTE, []),  # 4506 — execute / start
    ]
    return frames

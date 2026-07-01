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

After these four frames the machine reports STATE ``0x1f`` (armed/loaded) and
**waits for the human to approve the brew on the machine itself**.

⚠️ SAFETY — opcodes deliberately NOT emitted
--------------------------------------------
The original protocol also defines:

* ``0x42`` — commit.
* ``0x46`` — start / force-start.

Sending those bypasses the human-approval step and force-starts a brew. This
package **never** builds or sends them. :func:`build_load_frames` returns only
the four LOAD frames. There is intentionally no code path in this package that
emits ``0x42`` or ``0x46``.
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
    "FORBIDDEN_COMMIT_OPCODE",
    "FORBIDDEN_START_OPCODE",
]

# Sequence byte used for the load sequence.
LOAD_SEQ = 0x1F

# Opcodes that force-start a brew. Documented here so it is unmistakable that
# they exist — and that this package never builds or sends them.
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


def build_41(pours: Iterable[Mapping], grind: int, tail: int = 0xA0) -> bytes:
    """0x41 pours+grind payload: ``01 | LEN(u8) | <segments> | grind | tail``."""
    body = b"".join(seg for p in pours for seg in _pour_segments(p))
    return bytes([0x01, len(body) & 0xFF]) + body + bytes([int(grind) & 0xFF, tail & 0xFF])


def build_load_frames(recipe: Mapping) -> list[bytes]:
    """Build the ordered list of LOAD frames for a recipe.

    Returns exactly ``[a4, a6, a8, 41]`` — the four frames that *load* the
    recipe onto the machine. It does **not** include ``0x42`` (commit) or
    ``0x46`` (start): the human approves the brew on the machine.

    ``recipe`` may be a plain dict (with keys ``dose``, ``grind``, optional
    ``stage_temps``, optional ``tail``, optional ``seq``, and ``pours``) or any
    mapping providing the same keys. :class:`xbloom_ble.recipe.Recipe` exposes
    a ``to_protocol_dict()`` producing exactly this shape.
    """
    seq = recipe.get("seq", LOAD_SEQ)
    t1, t2 = recipe.get("stage_temps", (110.0, 90.0))
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

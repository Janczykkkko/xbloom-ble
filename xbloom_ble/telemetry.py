"""Decode xBloom Studio status notifications (the ``ffe2`` characteristic).

The machine pushes status frames to the ``ffe2`` notify characteristic. Unlike
the *command* frames we send to ``ffe1`` (``58 01 01 | cmd | seq | len16 | 00 00
| payload | crc``), notifications use a **distinct** frame shape (verified from
the vendor app's HCI capture — 4658 notifications, all this format)::

    58 02 07 | TYPE(1) | SUB(1) | LEN(u32le) | 0xc1 | payload | CRC16(u16le)

* ``TYPE`` (offset 3) is the frame kind:
  - a **command echo / ACK** — ``TYPE`` equals the command byte the app just
    wrote (``a4/a6/a8/41/42/46``), so an ACK is simply "the notification whose
    offset-3 byte matches my command".
  - ``0x57`` — a **status** frame; the byte right after ``0xc1`` is the machine
    *state* (see table).
  - ``0x15`` / ``0x4b`` — idle **heartbeats** (ignored).
  - ``0x49`` — machine-info dump (serial + firmware string), ``0x39`` etc. carry
    live brew progress (best-effort, not needed for load-only).

State byte (inside a ``0x57`` frame, right after ``0xc1``)
---------------------------------------------------------
====  ============================  =========================================
Byte  Name                          Meaning
====  ============================  =========================================
0x01  idle                          Idle / ready (also seen at brew end).
0x0c  no_water                      Refused: no water (checked right after commit).
0x0f  no_beans                      Refused: wants beans (machine WAITS here).
0x10  brewing                       Live pour / brew in progress (see note below).
0x1d  loading                       Recipe being received.
0x1f  armed                         Recipe loaded, armed, awaiting approval.
0x1e  awaiting_confirm              Waiting for the human to confirm on device.
0x22  starting                      Post-commit: grinding / spinning up.
0x3b  brewing                       Brew in progress (seen on the app-capture firmware).
0x41  complete                      Brew complete.
0x43  saving_slots                  Easy-Mode slot batch being stored.
0x25  slots_saved                   Easy-Mode slots stored OK (then → idle).
====  ============================  =========================================

Note on the brew sequence (observed on firmware V12.0D.500): after commit the
machine goes ``awaiting_confirm (0x1e) → starting (0x22)``, then **grinds SILENTLY**
— it emits no ``0x57`` *status* frame for ~20 s (only the scale stream, reading ~0)
— before it reports the pour as ``0x10``. Consumers must not treat that gap as a
stalled brew.

The state ``0x1f`` (armed) is what :meth:`XBloomClient.load_recipe` waits for
after sending the four LOAD frames — the machine is armed and prompting the human.

Live weights: the machine streams the two brew-record weights the app graphs, ~10x/s,
as float32 (LE) frames — TYPE ``0x4b`` = water (in milligrams), TYPE ``0x15`` =
coffee/cup (in grams). :func:`parse_notification` decodes them into
:attr:`StatusEvent.water_g` / :attr:`StatusEvent.coffee_g`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "STATE_NAMES",
    "IGNORED_STATES",
    "TERMINAL_STATES",
    "StatusEvent",
    "parse_notification",
    "is_idle_or_complete",
]

STATE_NAMES: dict[int, str] = {
    0x01: "idle",
    0x0C: "no_water",          # machine has no water (checked before grinding)
    0x0F: "no_beans",          # machine wants beans (add beans, or cancel) — it WAITS here
    0x10: "brewing",           # live pour / brew in progress (observed on HW: the machine
                               #   grinds SILENTLY after 0x22, then reports 0x10 as it pours)
    0x1D: "loading",
    0x1F: "armed",
    0x1E: "awaiting_confirm",
    0x22: "starting",          # post-confirm: grinding / spinning up
    0x23: "brewing",           # mid-pour sub-state (keeps the status on "brewing…")
    0x24: "ready",             # brew DONE — the "coffee ready" beep. The cup is still on
                               #   the scale; the machine only returns to idle (0x01) once
                               #   it's lifted, so 'ready' is the real end-of-brew signal.
    0x3B: "brewing",
    0x41: "complete",
    0x43: "saving_slots",
    0x25: "slots_saved",
}

# Live-scale streams. The machine pushes the two brew-record weights the app graphs
# (~10x/s) as a float32 (little-endian) right after the 0xc1 marker. These were long
# mistaken for idle "heartbeats" (they DO stream at idle, reading ~0) — they are the
# weight stream. Verified against hardware + the app's on-screen "Brew Record" graph.
WATER_TYPE = 0x4B          # TYPE 0x4b: water weight — float32 LE in MILLIgrams (÷1000 = g)
COFFEE_TYPE = 0x15         # TYPE 0x15: coffee/cup weight — float32 LE already in grams
# Heartbeat state sentinels (0x15/0x4b as *states*) — kept for the is_heartbeat property
# and back-compat; the live streams above are keyed by TYPE, not state.
IGNORED_STATES = frozenset({0x15, 0x4B})

# States that mean the brew is over. 0x24 = "coffee ready" (the beep — the true end of
# a brew, cup still on the scale); 0x01 = idle (only reached once the cup is lifted).
# Making 0x24 terminal is what lets a plain stream_telemetry consumer (the CLI) stop at
# the beep instead of hanging until cup-off. (0x41 kept for firmwares that report it.)
TERMINAL_STATES = frozenset({0x24, 0x41, 0x01})

STATUS_CMD = 0x57      # TYPE byte of a status frame (state follows the 0xc1 marker)
STATE_MARKER = 0xC1


@dataclass
class StatusEvent:
    """A decoded status notification."""

    state: int | None
    state_name: str
    raw: bytes
    #: Live water weight in grams (brew-record frames only), best-effort.
    water_g: float | None = None
    #: Live coffee/extracted weight in grams (brew-record frames only).
    coffee_g: float | None = None

    @property
    def is_heartbeat(self) -> bool:
        return self.state in IGNORED_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def __str__(self) -> str:
        bits = [self.state_name]
        if self.water_g is not None:
            bits.append(f"water={self.water_g:g}g")
        if self.coffee_g is not None:
            bits.append(f"coffee={self.coffee_g:g}g")
        return " ".join(bits)


def _marker_idx(data: bytes) -> int:
    """Offset of the ``0xc1`` payload marker in a ``58 02 07`` notification.

    The header is fixed width (``58 02 07`` + TYPE + SUB + 4-byte LEN = 9 bytes),
    so the marker sits at offset 9; fall back to a search for robustness.
    """
    if len(data) > 9 and data[9] == STATE_MARKER:
        return 9
    return data.find(STATE_MARKER, 5)


def _decode_scale_grams(data: bytes, *, scale: float) -> float | None:
    """Decode a scale frame's float32 (LE) weight, in grams.

    The value sits immediately after the 0xc1 marker. ``scale`` converts the raw
    units to grams (water arrives in milligrams → ``0.001``; coffee is already grams
    → ``1.0``). Returns ``None`` for implausible readings — the scale drifts and
    reads noise (negative / huge / NaN) when idle or untared, and only means anything
    once a brew is pouring onto it.
    """
    marker = _marker_idx(data)
    if marker < 0 or marker + 5 > len(data):
        return None
    try:
        raw = struct.unpack_from("<f", data, marker + 1)[0]
    except struct.error:
        return None
    grams = raw * scale
    if grams != grams or grams < 0.0 or grams > 2000.0:  # NaN or out of range
        return None
    return round(grams, 2)


def parse_notification(data: bytes) -> StatusEvent | None:
    """Decode a raw ``ffe2`` notification into a :class:`StatusEvent`.

    ``data`` may be ``bytes``, ``bytearray``, or a hex string. Returns ``None``
    for frames that are not recognisable notifications (so callers can simply
    skip them). Frame shape: ``58 02 07 | TYPE | SUB | LEN(u32le) | c1 | … | crc``.
    """
    if isinstance(data, str):
        data = bytes.fromhex(data.replace(" ", ""))
    else:
        data = bytes(data)

    if len(data) < 10 or data[0] != 0x58:
        return None

    ftype = data[3]  # TYPE byte: command echo/ACK, 0x57 status, or a scale stream.

    # Live-scale streams (the two brew-record weights the app graphs). Each carries a
    # single float32 (LE) after the 0xc1 marker: 0x4b = water (milligrams), 0x15 =
    # coffee (grams). We surface each in its field; the other stays None (they arrive as
    # separate, interleaved frames). Idle/untared noise decodes to None and is dropped.
    if ftype == WATER_TYPE:
        g = _decode_scale_grams(data, scale=0.001)
        return StatusEvent(state=None, state_name="scale", raw=data, water_g=g)
    if ftype == COFFEE_TYPE:
        g = _decode_scale_grams(data, scale=1.0)
        return StatusEvent(state=None, state_name="scale", raw=data, coffee_g=g)

    marker = _marker_idx(data)
    payload = data[marker + 1 : -2] if marker >= 0 else b""

    # Status frame: the state code is the first byte after the 0xc1 marker.
    if ftype == STATUS_CMD and payload:
        state = payload[0]
        name = STATE_NAMES.get(state, f"unknown_0x{state:02x}")
        return StatusEvent(state=state, state_name=name, raw=data)

    # Otherwise it's a command echo / ACK (TYPE == the acked command byte) or a
    # brew-progress frame. No parsed state; the ACK is identified by data[3].
    return StatusEvent(state=None, state_name=f"ack_0x{ftype:02x}", raw=data)


def is_idle_or_complete(event: StatusEvent) -> bool:
    """True if the event indicates the brew is over (complete or back to idle)."""
    return event.state in TERMINAL_STATES

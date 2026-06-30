"""Decode xBloom Studio status notifications (the ``ffe2`` characteristic).

The machine pushes status frames to the ``ffe2`` notify characteristic. They
use the same outer frame format as commands (``58 .. .. cmd seq len .. payload
crc``). Status frames carry command ``0x57``; their *state byte* — the byte that
follows the ``0xc1`` marker inside the payload — tells you what the machine is
doing.

State byte meanings
-------------------
====  ============================  =========================================
Byte  Name                          Meaning
====  ============================  =========================================
0x01  idle                          Idle / ready.
0x1f  armed                         Recipe loaded, armed, awaiting approval.
0x1e  awaiting_confirm              Waiting for the human to confirm on device.
0x3b  brewing                       Brew in progress.
0x43  brew_record                   Live brew record (water/coffee weights).
0x41  complete                      Brew complete.
0x15  idle_heartbeat                Idle heartbeat (ignored).
0x4b  idle_heartbeat                Idle heartbeat (ignored).
====  ============================  =========================================

``0x43`` brew-record frames additionally carry live weights, decoded
best-effort (the byte layout is partially reverse-engineered).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

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
    0x1F: "armed",
    0x1E: "awaiting_confirm",
    0x3B: "brewing",
    0x43: "brew_record",
    0x41: "complete",
    0x15: "idle_heartbeat",
    0x4B: "idle_heartbeat",
}

# Heartbeats that should be ignored by consumers.
IGNORED_STATES = frozenset({0x15, 0x4B})

# States that mean the brew is over / the machine is idle.
TERMINAL_STATES = frozenset({0x41, 0x01})

STATUS_CMD = 0x57
STATE_MARKER = 0xC1


@dataclass
class StatusEvent:
    """A decoded status notification."""

    state: Optional[int]
    state_name: str
    raw: bytes
    #: Live water weight in grams (brew-record frames only), best-effort.
    water_g: Optional[float] = None
    #: Live coffee/extracted weight in grams (brew-record frames only).
    coffee_g: Optional[float] = None

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


def _decode_weights(payload: bytes, state_idx: int) -> tuple[Optional[float], Optional[float]]:
    """Best-effort decode of brew-record weights from a 0x43 frame.

    The brew record stores live weights as 16-bit little-endian values in
    tenths of a gram, immediately after the state byte. The exact field count
    varies, so we read defensively and only return values that look sane.
    """
    after = payload[state_idx + 1 :]
    vals: list[float] = []
    for i in range(0, len(after) - 1, 2):
        raw = struct.unpack_from("<H", after, i)[0]
        grams = raw / 10.0
        # Plausible brew weights only (filters CRC bytes / markers).
        if 0.0 <= grams <= 2000.0:
            vals.append(grams)
        if len(vals) >= 2:
            break
    water = vals[0] if vals else None
    coffee = vals[1] if len(vals) > 1 else None
    return water, coffee


def parse_notification(data: bytes) -> Optional[StatusEvent]:
    """Decode a raw ``ffe2`` notification into a :class:`StatusEvent`.

    ``data`` may be ``bytes``, ``bytearray``, or a hex string. Returns ``None``
    for frames that are not recognisable status frames (so callers can simply
    skip them).
    """
    if isinstance(data, str):
        data = bytes.fromhex(data.replace(" ", ""))
    else:
        data = bytes(data)

    if len(data) < 4 or data[0] != 0x58:
        return None

    # The state byte follows the 0xc1 marker inside the payload.
    marker_idx = data.find(STATE_MARKER, 5)
    if marker_idx < 0 or marker_idx + 1 >= len(data):
        # Recognised frame but no state marker (e.g. a bare ACK echo).
        return StatusEvent(state=None, state_name="ack", raw=data)

    state = data[marker_idx + 1]
    name = STATE_NAMES.get(state, f"unknown_0x{state:02x}")

    water = coffee = None
    if state == 0x43:
        water, coffee = _decode_weights(data, marker_idx + 1)

    return StatusEvent(state=state, state_name=name, raw=data, water_g=water, coffee_g=coffee)


def is_idle_or_complete(event: StatusEvent) -> bool:
    """True if the event indicates the brew is over (complete or back to idle)."""
    return event.state in TERMINAL_STATES

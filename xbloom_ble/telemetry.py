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
    "MachineInfo",
    "parse_notification",
    "parse_machine_info",
    "parse_scale_weight",
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


# ---------------------------------------------------------------------------
# Machine-info blob  (reply to the 40521 query — see protocol.build_machine_info_query)
# ---------------------------------------------------------------------------
@dataclass
class MachineInfo:
    """Decoded machine-info blob: serial, firmware, and unit/status flags.

    Fields are best-effort per brAzzi64's field map; any that don't decode
    cleanly are left ``None`` (the byte layout can vary across firmware).
    """

    serial: Optional[str] = None
    firmware: Optional[str] = None
    water_ok: Optional[bool] = None
    system_status: Optional[int] = None
    grinder: Optional[int] = None
    temp_unit: Optional[str] = None       # "C" or "F"
    weight_unit: Optional[str] = None     # "g" or "oz"
    raw: bytes = b""

    def __str__(self) -> str:
        bits = []
        if self.serial:
            bits.append(f"serial={self.serial}")
        if self.firmware:
            bits.append(f"fw={self.firmware}")
        if self.water_ok is not None:
            bits.append("water=ok" if self.water_ok else "water=low")
        if self.grinder is not None:
            bits.append(f"grinder={self.grinder}")
        if self.temp_unit:
            bits.append(f"temp={self.temp_unit}")
        if self.weight_unit:
            bits.append(f"weight={self.weight_unit}")
        return " ".join(bits) or "machine-info (undecoded)"


def _ascii_field(fields: bytes, start: int, end: int) -> Optional[str]:
    """Extract a printable-ASCII field from ``fields[start:end]``, or ``None``."""
    chunk = fields[start:end]
    if not chunk:
        return None
    text = "".join(chr(b) for b in chunk if 0x20 <= b < 0x7F)
    text = text.strip()
    return text or None


def parse_machine_info(data: bytes) -> Optional[MachineInfo]:
    """Decode a machine-info reply notification into a :class:`MachineInfo`.

    ``data`` may be ``bytes``, ``bytearray`` or a hex string. The information
    fields live in the payload (after the frame header, before the CRC). Offsets
    follow brAzzi64's decode; decoding is defensive so a short/odd frame simply
    yields whatever fields are recoverable rather than raising.
    """
    if isinstance(data, str):
        data = bytes.fromhex(data.replace(" ", ""))
    else:
        data = bytes(data)
    if len(data) < 12 or data[0] != 0x58:
        return None

    # Payload = frame minus the 9-byte header (58 01 01 cmd seq len len 00 00)
    # and the trailing 2-byte CRC. brAzzi64 reads the info fields from that body.
    fields = data[9:-2]
    info = MachineInfo(raw=data)
    info.serial = _ascii_field(fields, 0, 13)
    info.firmware = _ascii_field(fields, 19, 29)
    if len(fields) > 33:
        info.water_ok = bool(fields[33])
    if len(fields) > 34:
        info.system_status = fields[34]
    if len(fields) > 37:
        info.grinder = max(1, fields[37] - 30)
    if len(fields) > 39:
        info.temp_unit = "F" if fields[39] else "C"
    if len(fields) > 41:
        info.weight_unit = "oz" if fields[41] else "g"
    return info


def parse_scale_weight(data: bytes) -> Optional[float]:
    """Decode a live scale-weight notification (grams), or ``None``.

    The scale streams weight as a little-endian IEEE-754 float in the payload
    (brAzzi64: ``struct('<f', data[10:14])``). Returns the weight in grams if it
    looks sane, else ``None``.
    """
    if isinstance(data, str):
        data = bytes.fromhex(data.replace(" ", ""))
    else:
        data = bytes(data)
    if len(data) < 14 or data[0] != 0x58:
        return None
    grams = struct.unpack_from("<f", data, 10)[0]
    if grams != grams or abs(grams) > 5000:  # NaN or implausible
        return None
    return round(grams, 1)

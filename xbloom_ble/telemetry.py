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
0x1d  loading                       Recipe being received.
0x1f  armed                         Recipe loaded, armed, awaiting approval.
0x1e  awaiting_confirm              Waiting for the human to confirm on device.
0x3b  brewing                       Brew in progress.
0x41  complete                      Brew complete.
0x43  saving_slots                  Easy-Mode slot batch being stored.
0x25  slots_saved                   Easy-Mode slots stored OK (then → idle).
====  ============================  =========================================

The state ``0x1f`` (armed) is what :meth:`XBloomClient.load_recipe` waits for
after sending the four LOAD frames — the machine is armed and prompting the
human. Live brew-weight decoding is best-effort and left ``None`` here.
"""

from __future__ import annotations

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
    0x0F: "no_beans",          # machine wants beans (add beans, or cancel) — it WAITS here
    0x1D: "loading",
    0x1F: "armed",
    0x1E: "awaiting_confirm",
    0x22: "starting",          # post-confirm: grinding / spinning up
    0x3B: "brewing",
    0x41: "complete",
    0x43: "saving_slots",
    0x25: "slots_saved",
}

# States the machine reports (via 0x57 status frames) once the human has confirmed a
# brew — i.e. the brew is underway. Used to distinguish "the brew ended" (→ idle) from
# the machine just sitting idle before anything started.
BREW_ACTIVE_STATES = frozenset({0x1E, 0x22, 0x3B, 0x0F})

# Notification TYPE bytes (offset 3) that are idle heartbeats — ignored.
HEARTBEAT_TYPES = frozenset({0x15, 0x4B})
# Kept for backward compat (consumers referencing it): heartbeat state sentinels.
IGNORED_STATES = frozenset({0x15, 0x4B})

# States that mean the brew is over / the machine is idle.
TERMINAL_STATES = frozenset({0x41, 0x01})

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

    ftype = data[3]  # TYPE byte: command echo/ACK, 0x57 status, or heartbeat.

    # Idle heartbeats — surface as heartbeat events so consumers skip them.
    if ftype in HEARTBEAT_TYPES:
        return StatusEvent(state=ftype, state_name="idle_heartbeat", raw=data)

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

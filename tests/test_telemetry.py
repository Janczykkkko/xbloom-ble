"""Telemetry decoding tests.

Notifications on ``ffe2`` use the shape ``58 02 07 | TYPE | SUB | LEN(u32le) |
c1 | payload | crc`` (distinct from the command frames we *send* to ``ffe1``).
We build faithful notification bytes with :func:`_notif` and also assert against
a handful of **golden frames captured verbatim** from the vendor app's HCI log,
so the decoder is pinned to real hardware output.
"""

import struct

from xbloom_ble.telemetry import parse_notification


def _notif(ftype: int, state: int | None = None, sub: int = 0x1F) -> bytes:
    """Build a real-shape ``58 02 07`` notification.

    A ``0x57`` status frame carries ``c1 <state> 00 00 00``; other TYPEs (ACK
    echoes, heartbeats) carry just the ``c1`` marker. CRC is a dummy (the parser
    does not verify it).
    """
    head = bytes([0x58, 0x02, 0x07, ftype, sub])
    payload = bytes([0xC1]) + (bytes([state, 0, 0, 0]) if state is not None else b"")
    total = len(head) + 4 + len(payload) + 2
    return head + struct.pack("<I", total) + payload + b"\x00\x00"


def _status(state: int) -> bytes:
    return _notif(0x57, state=state)


# --- state decoding (0x57 status frames) ----------------------------------

def test_idle_state():
    ev = parse_notification(_status(0x01))
    assert ev is not None
    assert ev.state == 0x01
    assert ev.state_name == "idle"
    assert ev.is_terminal


def test_armed_state():
    ev = parse_notification(_status(0x1F))
    assert ev.state == 0x1F
    assert ev.state_name == "armed"
    assert not ev.is_terminal
    assert not ev.is_heartbeat


def test_awaiting_confirm_state():
    assert parse_notification(_status(0x1E)).state_name == "awaiting_confirm"


def test_loading_state():
    assert parse_notification(_status(0x1D)).state_name == "loading"


def test_complete_state_is_terminal():
    ev = parse_notification(_status(0x41))
    assert ev.state_name == "complete"
    assert ev.is_terminal


def test_unknown_state():
    assert parse_notification(_status(0x77)).state_name == "unknown_0x77"


# --- live scale streams (0x4b water, 0x15 coffee) & ACKs -------------------

def test_water_scale_decodes_grams():
    # 0x4b = water: float32 LE in MILLIgrams. 0x4708b800 = 35000.0 -> 35.0 g (bloom).
    ev = parse_notification("5802074b9e10000000c100b808470000")
    assert ev.water_g == 35.0
    assert ev.coffee_g is None
    assert not ev.is_heartbeat


def test_coffee_scale_decodes_grams():
    # 0x15 = coffee: float32 LE already in grams. 0x4141ef9e = 12.121 g (bloom plateau).
    ev = parse_notification("580207155010000000c19eef41410000")
    assert ev.coffee_g == 12.12
    assert ev.water_g is None


def test_scale_end_of_brew_values():
    # end-of-brew captures: water 256.0 g (= the 256 ml total), coffee 226.45 g.
    assert parse_notification("5802074b9e10000000c100007a480000").water_g == 256.0
    assert parse_notification("580207155010000000c12d7262430000").coffee_g == 226.45


def test_scale_zero_reading_kept_but_noise_dropped():
    # A genuine 0.0 g reading (empty scale at brew start) is kept…
    assert parse_notification("5802074b9e10000000c100000000fd32").water_g == 0.0
    # …but untared/idle drift decoding to an implausible value is dropped to None.
    noise = parse_notification("580207155010000000c1d7a319c20000")
    assert noise.water_g is None and noise.coffee_g is None


def test_command_echo_is_ack():
    # A notification whose TYPE byte equals the command sent = that command's ACK.
    for cmd in (0xA4, 0xA6, 0xA8, 0x41):
        ev = parse_notification(_notif(cmd))
        assert ev is not None
        assert ev.state is None
        assert ev.state_name == f"ack_0x{cmd:02x}"
        assert ev.raw[3] == cmd  # this is how the client matches an ACK


# --- misc ------------------------------------------------------------------

def test_non_notification_bytes_return_none():
    assert parse_notification(b"\x00\x01\x02") is None
    assert parse_notification(b"") is None
    assert parse_notification(b"\x58\x02\x07") is None  # too short


def test_accepts_hex_string():
    assert parse_notification(_status(0x1F).hex()).state_name == "armed"


# --- golden frames captured verbatim from the vendor app ------------------

def test_golden_captured_frames():
    # (hex, expected state_name, expected state) — real ffe2 notifications.
    cases = [
        ("580207571f10000000c11f000000ce5e", "armed", 0x1F),
        ("580207571f10000000c1010000002d33", "idle", 0x01),
        ("580207571f10000000c11e0000007542", "awaiting_confirm", 0x1E),
        ("580207a61f0c000000c12b8f", "ack_0xa6", None),   # a6 (dose) ACK
        ("580207411f0c000000c1ab6a", "ack_0x41", None),   # 41 (pours) ACK
        ("5802074b9e10000000c100000000fd32", "scale", None),   # 0x4b water: 0.0 g
        ("580207155010000000c10000000016b5", "scale", None),   # 0x15 coffee: 0.0 g
    ]
    for hx, name, state in cases:
        ev = parse_notification(hx)
        assert ev is not None, hx
        assert ev.state_name == name, (hx, ev.state_name)
        assert ev.state == state, (hx, ev.state)

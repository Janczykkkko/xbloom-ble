"""Telemetry decoding tests.

Status frames share the command frame format. We build realistic ``ffe2``
notification bytes with the package's own frame builder so the test data is a
faithful ``58 .. .. 57 .. len .. .. c1 <state> …`` shape, then assert the
decoder recovers the right state.
"""

import struct

from xbloom_ble.protocol import xbloom_frame
from xbloom_ble.telemetry import parse_notification


def _status_frame(state: int, extra: bytes = b"") -> bytes:
    """A 0x57 status frame whose payload carries the c1<state> marker."""
    payload = b"\x00" * 3 + bytes([0xC1, state]) + extra
    return xbloom_frame(0x57, 0x1F, payload)


def test_idle_state():
    ev = parse_notification(_status_frame(0x01))
    assert ev is not None
    assert ev.state == 0x01
    assert ev.state_name == "idle"
    assert ev.is_terminal


def test_armed_state():
    ev = parse_notification(_status_frame(0x1F))
    assert ev.state_name == "armed"
    assert not ev.is_terminal
    assert not ev.is_heartbeat


def test_awaiting_confirm_state():
    ev = parse_notification(_status_frame(0x1E))
    assert ev.state_name == "awaiting_confirm"


def test_brewing_state():
    ev = parse_notification(_status_frame(0x3B))
    assert ev.state_name == "brewing"


def test_complete_state_is_terminal():
    ev = parse_notification(_status_frame(0x41))
    assert ev.state_name == "complete"
    assert ev.is_terminal


def test_heartbeats_flagged():
    for hb in (0x15, 0x4B):
        ev = parse_notification(_status_frame(hb))
        assert ev.is_heartbeat
        assert ev.state_name == "idle_heartbeat"


def test_brew_record_weights():
    # water 123.4 g (1234 -> 0x04d2), coffee 56.7 g (567 -> 0x0237), little-endian
    extra = struct.pack("<H", 1234) + struct.pack("<H", 567)
    ev = parse_notification(_status_frame(0x43, extra))
    assert ev.state_name == "brew_record"
    assert ev.water_g == 123.4
    assert ev.coffee_g == 56.7


def test_unknown_state():
    ev = parse_notification(_status_frame(0x77))
    assert ev.state_name == "unknown_0x77"


def test_non_status_bytes_return_none():
    assert parse_notification(b"\x00\x01\x02") is None
    assert parse_notification(b"") is None


def test_accepts_hex_string():
    frame = _status_frame(0x3B)
    ev = parse_notification(frame.hex())
    assert ev.state_name == "brewing"


def test_frame_without_marker_is_ack():
    # A valid 0x58 frame with no 0xc1 state marker -> treated as a bare ACK.
    frame = xbloom_frame(0xA6, 0x1F, bytes(13))
    ev = parse_notification(frame)
    assert ev is not None
    assert ev.state is None
    assert ev.state_name == "ack"

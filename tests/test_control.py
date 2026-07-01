"""Byte-level tests for the lower-level control frame builders.

These cover the frames ported from brAzzi64's PROTOCOL.md — machine-info query,
scale (tare/units), grinder, FreeSolo pour, and the Easy-Mode slot write — plus
the machine-info / scale-weight decoders. No hardware; pure frame construction.

Every command frame follows this package's outer format::

    58 01 01 | CMD(u8) | SEQ(u8) | LEN(u16le) | 00 00 | PAYLOAD | CRC16(u16le)

where a reference 16-bit command code splits as ``cmd = low byte`` /
``seq = high byte`` (e.g. ``0x1FA6`` → cmd ``0xa6``, seq ``0x1f``).
"""

import struct

from xbloom_ble.protocol import (
    build_grind,
    build_grind_stop,
    build_machine_info_query,
    build_pour_frames,
    build_save_slot,
    build_scale_tare,
    build_scale_units,
    crc16_kermit,
)
from xbloom_ble.telemetry import parse_machine_info, parse_scale_weight


def _cmd_seq(frame):
    return frame[3], frame[4]


def _payload(frame):
    """Payload = frame minus the 9-byte header and the 2-byte CRC."""
    return frame[9:-2]


def _assert_valid_frame(frame):
    assert frame[0:3] == b"\x58\x01\x01"
    stored_len = struct.unpack("<H", frame[5:7])[0]
    assert stored_len == len(frame)
    stored_crc = struct.unpack("<H", frame[-2:])[0]
    assert stored_crc == crc16_kermit(frame[:-2])


# ---------------------------------------------------------------------------
# machine-info query
# ---------------------------------------------------------------------------
def test_machine_info_query_frame():
    f = build_machine_info_query()
    _assert_valid_frame(f)
    # reference 40521 = 0x9E49 → cmd 0x49, seq 0x9e
    assert _cmd_seq(f) == (0x49, 0x9E)
    # a no-arg Type-1 payload is just the 0x01 marker
    assert _payload(f) == b"\x01"


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------
def test_scale_tare_frame():
    f = build_scale_tare()
    _assert_valid_frame(f)
    # 8500 = 0x2134 → cmd 0x34, seq 0x21
    assert _cmd_seq(f) == (0x34, 0x21)
    assert _payload(f) == b"\x01"


def test_scale_units_frame():
    for unit, code in (("g", 0), ("oz", 1), ("ml", 2)):
        f = build_scale_units(unit)
        _assert_valid_frame(f)
        # 8005 = 0x1F45 → cmd 0x45, seq 0x1f
        assert _cmd_seq(f) == (0x45, 0x1F)
        # payload = 0x01 + one LE int32 arg
        assert _payload(f) == b"\x01" + struct.pack("<i", code)


def test_scale_units_bad_unit_raises():
    import pytest

    with pytest.raises(ValueError):
        build_scale_units("stone")


# ---------------------------------------------------------------------------
# grinder
# ---------------------------------------------------------------------------
def test_grind_frame():
    f = build_grind(30, 90)
    _assert_valid_frame(f)
    # 3500 = 0x0DAC → cmd 0xac, seq 0x0d
    assert _cmd_seq(f) == (0xAC, 0x0D)
    # payload = 0x01 + [1000, size, speed] as LE int32s
    want = b"\x01" + struct.pack("<iii", 1000, 30, 90)
    assert _payload(f) == want


def test_grind_stop_frame():
    f = build_grind_stop()
    _assert_valid_frame(f)
    # 3505 = 0x0DB1 → cmd 0xb1, seq 0x0d
    assert _cmd_seq(f) == (0xB1, 0x0D)
    assert _payload(f) == b"\x01"


# ---------------------------------------------------------------------------
# FreeSolo pour
# ---------------------------------------------------------------------------
def test_pour_frame_sequence():
    frames = build_pour_frames(200, 92, flow=3.0, dose_g=16)
    for f in frames:
        _assert_valid_frame(f)
    cmds = [_cmd_seq(f) for f in frames]
    # handshake(8100=0x1FA4), bypass+dose(8102=0x1FA6), set-cup(8104=0x1FA8),
    # tare(8500=0x2134), recipe(8004=0x1F44), execute(4506=0x119A)
    assert cmds == [
        (0xA4, 0x1F), (0xA6, 0x1F), (0xA8, 0x1F),
        (0x34, 0x21), (0x44, 0x1F), (0x9A, 0x11),
    ]


def test_pour_frame_no_tare():
    frames = build_pour_frames(200, 92, tare=False)
    cmds = [_cmd_seq(f) for f in frames]
    assert (0x34, 0x21) not in cmds  # tare omitted
    assert cmds[-1] == (0x9A, 0x11)  # still ends with execute


def test_pour_recipe_blob_encodes_pour():
    """The pour's recipe frame carries the pour ml/temp/flow in its 0x41-style body."""
    frames = build_pour_frames(120, 90, flow=3.2, dose_g=16, tare=False)
    recipe_frame = next(f for f in frames if _cmd_seq(f) == (0x44, 0x1F))
    body = _payload(recipe_frame)  # 01 | LEN | segment(8)
    assert body[0] == 0x01
    seg = body[2:2 + body[1]]
    assert seg[0] == 120        # ml
    assert seg[1] == 90         # temp
    assert seg[7] == 32         # flow 3.2 → 32


# ---------------------------------------------------------------------------
# Easy-Mode slot write
# ---------------------------------------------------------------------------
_SLOT_RECIPE = {
    "dose": 16, "grind": 62, "ratio": 16,
    "pours": [
        {"ml": 40, "temp": 92, "pattern": "spiral", "agitation": True,
         "pause": 30, "rpm": 90, "flow": 3.0},
        {"ml": 216, "temp": 90, "pattern": "spiral", "agitation": False,
         "pause": 5, "rpm": 90, "flow": 3.0},
    ],
}


def test_save_slot_frame_layout():
    f = build_save_slot(1, _SLOT_RECIPE)
    _assert_valid_frame(f)
    # 11510 = 0x2CF6 → cmd 0xf6, seq 0x2c
    assert _cmd_seq(f) == (0xF6, 0x2C)
    body = _payload(f)  # 01 | slot_idx | flags | recipe_blob...
    assert body[0] == 0x01
    assert body[1] == 0x00        # slot 1 → index 0
    # flags: scale ON (0x10) | grinder ON (0x02) = 0x12
    assert body[2] == 0x12
    # embedded recipe blob starts with the 0x41 body marker
    assert body[3] == 0x01


def test_save_slot_indices_and_flags():
    assert _payload(build_save_slot(2, _SLOT_RECIPE))[1] == 0x01  # slot 2 → idx 1
    assert _payload(build_save_slot(3, _SLOT_RECIPE))[1] == 0x02  # slot 3 → idx 2
    # grinder OFF → 0x04; scale off → no 0x10
    f = build_save_slot(1, _SLOT_RECIPE, scale=False, grinder=False)
    assert _payload(f)[2] == 0x04


def test_save_slot_blob_tail_is_ratio():
    """The embedded recipe blob's tail byte is the ratio (1:16 → 0xa0)."""
    body = _payload(build_save_slot(1, _SLOT_RECIPE))
    blob = body[3:]  # 0x41-style body: 01 | LEN | segs | grind | tail
    assert blob[-1] == 0xA0
    assert blob[-2] == 62  # grind


def test_save_slot_bad_slot_raises():
    import pytest

    with pytest.raises(ValueError):
        build_save_slot(4, _SLOT_RECIPE)


# ---------------------------------------------------------------------------
# machine-info / scale-weight decoders
# ---------------------------------------------------------------------------
def _info_frame(serial: str, firmware: str) -> bytes:
    """Craft a machine-info reply whose body places serial/fw at the decoded offsets."""
    from xbloom_ble.protocol import xbloom_frame

    body = bytearray(45)
    body[0:len(serial)] = serial.encode("ascii")
    body[19:19 + len(firmware)] = firmware.encode("ascii")
    body[33] = 1          # water ok
    body[37] = 93         # grinder 93 - 30 = 63
    body[39] = 0          # temp unit C
    body[41] = 0          # weight unit g
    return xbloom_frame(0x49, 0x9E, bytes(body))


def test_parse_machine_info():
    frame = _info_frame("J15A01F5AW016", "V12.0D.500")
    info = parse_machine_info(frame)
    assert info is not None
    assert info.serial == "J15A01F5AW016"
    assert info.firmware == "V12.0D.500"
    assert info.water_ok is True
    assert info.grinder == 63
    assert info.temp_unit == "C"
    assert info.weight_unit == "g"


def test_parse_machine_info_rejects_junk():
    assert parse_machine_info(b"\x00\x01\x02") is None
    assert parse_machine_info(b"") is None


def test_parse_scale_weight():
    from xbloom_ble.protocol import xbloom_frame

    # weight float at payload offset 10 → data offset 10 (header is 9 bytes,
    # but brAzzi64 reads data[10:14] on the raw frame).
    raw = bytearray(xbloom_frame(0x15, 0x50, bytes(20)))
    struct.pack_into("<f", raw, 10, 18.5)
    assert parse_scale_weight(bytes(raw)) == 18.5


def test_parse_scale_weight_rejects_junk():
    assert parse_scale_weight(b"\x00\x01") is None
    assert parse_scale_weight(b"") is None

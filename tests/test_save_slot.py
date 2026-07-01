"""Slot-write (save-slot) protocol tests — byte-exact against a captured app frame."""

import pytest

from xbloom_ble.protocol import build_save_slot

# The Savora recipe exactly as the vendor app stored it to a slot (rpm on the
# first pour only; all pours spiral) — the ground truth for the byte-exact check.
REC = {
    "grind": 60,
    "tail": 0xA0,
    "pours": [
        {"ml": 50, "temp": 92, "pattern": "spiral", "agitation": True, "pause": 45, "rpm": 120, "flow": 3.0},
        {"ml": 70, "temp": 91, "pattern": "spiral", "agitation": False, "pause": 5, "rpm": 120, "flow": 3.0},
        {"ml": 65, "temp": 90, "pattern": "spiral", "agitation": False, "pause": 5, "rpm": 120, "flow": 3.0},
        {"ml": 55, "temp": 90, "pattern": "spiral", "agitation": False, "pause": 5, "rpm": 120, "flow": 3.0},
    ],
}

# Captured writes to ffe1 (app → machine): slot A scale-on, slot C scale-off.
CAP_A = "580102f62c3100000001001220325c0202d300781e465b0200fb00001e415a0200fb00001e375a0200fb00001e3ca00838"
CAP_C = "580102f62c3100000001020220325c0202d300781e465b0200fb00001e415a0200fb00001e375a0200fb00001e3ca0fdd0"


def test_save_slot_byte_exact_scale_on():
    assert build_save_slot(REC, 0, scale=True).hex() == CAP_A


def test_save_slot_byte_exact_scale_off():
    assert build_save_slot(REC, 2, scale=False).hex() == CAP_C


def test_save_slot_index_byte():
    for slot in (0, 1, 2):
        assert build_save_slot(REC, slot)[10] == slot


def test_save_slot_scale_flag_default_on():
    assert build_save_slot(REC, 0)[11] == 0x12          # default: scale on
    assert build_save_slot(REC, 0, scale=False)[11] == 0x02


def test_save_slot_uses_slot_command_not_brew_start():
    fr = build_save_slot(REC, 1)
    assert fr[3:5] == bytes([0xF6, 0x2C])               # 0x2CF6 slot-write, never 0x42/0x46
    assert 0x42 not in fr[3:5] and 0x46 not in fr[3:5]


def test_save_slot_rejects_bad_index():
    for bad in (3, -1, "A"):
        with pytest.raises(ValueError):
            build_save_slot(REC, bad)

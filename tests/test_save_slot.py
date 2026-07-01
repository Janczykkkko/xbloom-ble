"""Slot-write (save-slots) protocol tests — byte-exact against a captured app frame,
plus the batch-normalisation logic in the client."""

import pytest

from xbloom_ble.client import XBloomClient, XBloomError
from xbloom_ble.protocol import build_save_slot, build_set_mode

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


# --- mode switch (byte-exact vs the app's mode-toggle capture) --------------
def test_set_mode_pro_byte_exact():
    assert build_set_mode(pro=True).hex() == "580102f72c1000000001000000002a90"


def test_set_mode_auto_byte_exact():
    assert build_set_mode(pro=False).hex() == "580102f72c100000000191327856ff58"


def test_set_mode_is_not_a_brew_opcode():
    for pro in (True, False):
        fr = build_set_mode(pro=pro)
        assert fr[3:5] == bytes([0xF7, 0x2C])          # 0x2CF7 mode-switch, never 0x42/0x46


# --- batch normalisation (save_slots) --------------------------------------
def _r(name):
    from xbloom_ble.recipe import Recipe

    return Recipe.from_dict({
        "name": name, "dose_g": 16, "grind": 60,
        "pours": [
            {"ml": 40, "temp_c": 92, "pattern": "spiral", "agitation": True, "rpm": 120, "pause_s": 40},
            {"ml": 200, "temp_c": 90, "pattern": "spiral", "rpm": 120, "pause_s": 5},
        ],
    })


def test_normalize_slots_sequence_of_three():
    rs = [_r("A"), _r("B"), _r("C")]
    assert XBloomClient._normalize_slots(rs) == rs


def test_normalize_slots_requires_exactly_three():
    with pytest.raises(XBloomError):
        XBloomClient._normalize_slots([_r("A"), _r("B")])


def test_normalize_slots_mapping_by_letter_and_index():
    a, b, c = _r("A"), _r("B"), _r("C")
    assert XBloomClient._normalize_slots({"A": a, "B": b, "C": c}) == [a, b, c]
    assert XBloomClient._normalize_slots({0: a, 1: b, 2: c}) == [a, b, c]


def test_normalize_slots_mapping_missing_slot():
    with pytest.raises(XBloomError):
        XBloomClient._normalize_slots({"A": _r("A"), "B": _r("B")})


def test_normalize_scale_bool_and_sequence():
    assert XBloomClient._normalize_scale(True) == [True, True, True]
    assert XBloomClient._normalize_scale([True, False, True]) == [True, False, True]
    with pytest.raises(XBloomError):
        XBloomClient._normalize_scale([True, False])

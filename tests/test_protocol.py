"""Byte-for-byte protocol tests against the reverse-engineering reference.

These import the *reference* implementation (``parse_btsnoop.py``) and assert
that this package's :func:`build_load_frames` reproduces the reference's first
four frames exactly, for a range of recipes — and that the LOAD frames never
carry a brew-start opcode (starting a brew is a separate, explicit call).

Set the ``XBLOOM_REFERENCE`` environment variable to the path of the
``parse_btsnoop.py`` reference script to enable these comparisons; by default it
is looked for next to the repo (``reference/parse_btsnoop.py``). If the
reference is not present, the comparison tests are skipped (the rest still run).
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from xbloom_ble.protocol import (
    CANCEL_OPCODE,
    COMMIT_OPCODE,
    NO_GRIND_WIRE,
    START_OPCODE,
    build_41,
    build_cancel,
    build_commit,
    build_load_frames,
    build_start,
    crc16_kermit,
    xbloom_frame,
)

# ---------------------------------------------------------------------------
# Locate and import the reverse-engineering reference (optional).
# ---------------------------------------------------------------------------
# Default to a sibling `reference/` dir; override with $XBLOOM_REFERENCE.
_DEFAULT_REF = str(Path(__file__).resolve().parent.parent / "reference" / "parse_btsnoop.py")


def _load_reference():
    ref_path = os.environ.get("XBLOOM_REFERENCE", _DEFAULT_REF)
    if not Path(ref_path).is_file():
        return None
    sys.path.insert(0, str(Path(ref_path).parent))
    spec = importlib.util.spec_from_file_location("xbloom_reference", ref_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REFERENCE = _load_reference()
needs_ref = pytest.mark.skipif(REFERENCE is None, reason="reverse-engineering reference not available")


# ---------------------------------------------------------------------------
# Recipes covering the interesting code paths.
# ---------------------------------------------------------------------------
RECIPES = {
    "simple": {
        "dose": 16,
        "grind": 62,
        "pours": [
            {"ml": 35, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 40, "rpm": 100, "flow": 3.0},
            {"ml": 115, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 100, "flow": 3.0},
        ],
    },
    "over_127ml": {  # exercises the 127-ml split path
        "dose": 18,
        "grind": 75,
        "pours": [
            {"ml": 45, "temp": 92, "pattern": "spiral", "agitation": True,
             "pause": 30, "rpm": 90, "flow": 3.0},
            {"ml": 235, "temp": 91, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 120, "flow": 3.2},
        ],
    },
    "center_and_ring": {  # exercises center + ring patterns
        "dose": 20,
        "grind": 50,
        "stage_temps": (108.0, 88.0),
        "pours": [
            {"ml": 40, "temp": 93, "pattern": "spiral", "agitation": True,
             "pause": 35, "rpm": 100, "flow": 2.8},
            {"ml": 80, "temp": 90, "pattern": "ring", "agitation": False,
             "pause": 10, "rpm": 90, "flow": 3.0},
            {"ml": 60, "temp": 88, "pattern": "center", "agitation": False,
             "pause": 0, "rpm": 0, "flow": 3.0},
        ],
    },
}


@needs_ref
@pytest.mark.parametrize("name", list(RECIPES))
def test_load_frames_match_reference(name):
    """build_load_frames == reference build_brew()[:4], byte for byte."""
    recipe = RECIPES[name]
    ours = build_load_frames(recipe)
    ref_frames = REFERENCE.build_brew(recipe)
    assert len(ours) == 4
    assert ours == ref_frames[:4], (
        f"recipe {name}: load frames differ from reference\n"
        f"ours: {[f.hex() for f in ours]}\n"
        f"ref : {[f.hex() for f in ref_frames[:4]]}"
    )


@pytest.mark.parametrize("name", list(RECIPES))
def test_load_frames_are_load_only(name):
    """LOAD frames never carry a brew-start/cancel opcode — loading can't brew.

    Starting a brew is a separate, explicit step (build_commit/build_start), so a
    load sequence must never contain 0x42/0x46/0x47.
    """
    for frame in build_load_frames(RECIPES[name]):
        cmd = frame[3]
        assert cmd not in (COMMIT_OPCODE, START_OPCODE, CANCEL_OPCODE)


def test_commit_start_cancel_frames_match_capture():
    """The commit/start/cancel frames are byte-exact vs the vendor app's capture."""
    assert build_commit().hex() == "580101421f0c000000017fcf"   # 0x42, seq 0x1f
    assert build_start().hex() == "580101469e0c0000000180a1"    # 0x46, seq 0x9e
    assert build_cancel().hex() == "580101479e0c00000001553e"   # 0x47, seq 0x9e


def test_load_frames_opcode_order():
    """The four frames are exactly a4, a6, a8, 41 in order."""
    frames = build_load_frames(RECIPES["simple"])
    assert [f[3] for f in frames] == [0xA4, 0xA6, 0xA8, 0x41]


def test_pours_opcode_and_ratio_byte():
    """The pours frame: opcode 0x41 (grind) / 0x44 (no-grind), and the trailing
    byte is the ratio×10 (derived from Σpours/dose), NOT a fixed 0xa0."""
    r = {"dose": 16, "grind": 55, "pours": [
        {"ml": 40, "temp": 92, "pattern": "spiral", "pause": 30, "rpm": 100, "flow": 3.0},
        {"ml": 200, "temp": 92, "pattern": "spiral", "pause": 5, "rpm": 100, "flow": 3.0}]}
    pours = build_load_frames(r)[-1]
    assert pours[3] == 0x41            # grinder ON → 0x41
    assert pours[-4] == 55             # grind byte
    assert pours[-3] == 0x96           # ratio 240/16 = 15 → 0x96 (was hard-coded 0xa0)

    r["grind"] = 0                     # no-grind
    ng = build_load_frames(r)[-1]
    assert ng[3] == 0x44               # grinder OFF → 0x44 opcode
    assert ng[-4] == NO_GRIND_WIRE     # grind byte = 0xFE
    assert ng[-3] == 0x96              # same ratio byte


def test_ratio_byte_matches_common_ratios():
    def ratio_byte(total, dose):
        r = {"dose": dose, "grind": 55, "pours": [
            {"ml": total, "temp": 92, "pattern": "spiral", "pause": 5, "rpm": 100, "flow": 3.0},
            {"ml": 1, "temp": 92, "pattern": "spiral", "pause": 5, "rpm": 100, "flow": 3.0}]}
        return build_load_frames(r)[-1][-3]
    assert ratio_byte(159, 16) == 0x64   # 160/16 = 10.0 → 0x64  (1:10, seen in app)
    assert ratio_byte(255, 16) == 0xa0   # 256/16 = 16.0 → 0xa0  (1:16)


def test_grind_byte_passthrough_and_no_grind_sentinel():
    """The 0x41 grind byte (2nd-to-last, before the tail) is the grind — except
    grind 0 (no-grind / pre-ground) is emitted as the 0xFE sentinel, not 0x00."""
    pours = RECIPES["simple"]["pours"]
    assert build_41(pours, grind=62)[-2] == 62          # normal grind passes through
    assert build_41(pours, grind=0)[-2] == NO_GRIND_WIRE == 0xFE   # no-grind → 0xFE
    # the tail byte is unaffected
    assert build_41(pours, grind=0)[-1] == 0xA0


def test_crc16_kermit_known_vector():
    """Known CRC-16/KERMIT check value: 0x2189 for b'123456789'."""
    assert crc16_kermit(b"123456789") == 0x2189


def test_crc16_matches_reference_if_available():
    if REFERENCE is None:
        pytest.skip("reference not available")
    for data in (b"", b"\x00", b"123456789", bytes(range(20))):
        assert crc16_kermit(data) == REFERENCE.crc16_kermit(data)


def test_frame_crc_roundtrips():
    """A built frame's trailing CRC validates over the frame minus 2 bytes."""
    frame = xbloom_frame(0xA6, 0x1F, bytes(13))
    import struct

    stored = struct.unpack("<H", frame[-2:])[0]
    assert stored == crc16_kermit(frame[:-2])

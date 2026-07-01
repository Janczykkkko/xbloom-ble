"""Byte-for-byte protocol tests against the reverse-engineering reference.

These import the *reference* implementation (``parse_btsnoop.py``) and assert
that this package's :func:`build_load_frames` reproduces the reference's first
four frames exactly, for a range of recipes — and that no frame ever carries a
brew-start opcode.

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
    FORBIDDEN_COMMIT_OPCODE,
    FORBIDDEN_START_OPCODE,
    build_41,
    build_load_frames,
    build_start_frames,
    crc16_kermit,
    ratio_to_tail,
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
def test_no_forbidden_opcodes(name):
    """No LOAD frame may carry the commit (0x42) or start (0x46) opcode."""
    for frame in build_load_frames(RECIPES[name]):
        cmd = frame[3]
        assert cmd not in (FORBIDDEN_COMMIT_OPCODE, FORBIDDEN_START_OPCODE)


def test_load_frames_opcode_order():
    """The four frames are exactly a4, a6, a8, 41 in order."""
    frames = build_load_frames(RECIPES["simple"])
    assert [f[3] for f in frames] == [0xA4, 0xA6, 0xA8, 0x41]


# ---------------------------------------------------------------------------
# Tail byte = round(ratio × 10)  (the decode fix)
# ---------------------------------------------------------------------------
def test_ratio_to_tail_known_values():
    """1:16 → 0xa0 (160), 1:17 → 0xaa (170) — the captured tail bytes."""
    assert ratio_to_tail(16) == 0xA0
    assert ratio_to_tail(17) == 0xAA
    assert ratio_to_tail(15) == 150
    assert ratio_to_tail(16.5) == 165


def test_load_frame_tail_from_ratio():
    """build_load_frames encodes the 0x41 tail from recipe['ratio']."""
    # 0x41 payload ends with [grind, tail]; tail is the 2nd-to-last frame byte
    # (last two frame bytes are the CRC).
    for ratio, want in ((16, 0xA0), (17, 0xAA)):
        r = dict(RECIPES["simple"], ratio=ratio)
        frame41 = build_load_frames(r)[3]
        assert frame41[-3] == want, f"ratio {ratio} tail"


def test_captured_run_tails_reproduced_from_ratio():
    """The captured runs' ratios reproduce their captured tail bytes.

    Run 0 (dose 15, Σml 240 → 1:16) → 0xa0; run 11 (dose 18, Σml 306 → 1:17) → 0xaa.
    """
    run0 = {
        "dose": 15, "grind": 60, "ratio": 240 / 15,
        "pours": [
            {"ml": 50, "temp": 92, "pattern": "spiral", "agitation": True,
             "pause": 45, "rpm": 120, "flow": 3.0},
            {"ml": 70, "temp": 91, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 0, "flow": 3.0},
            {"ml": 65, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 0, "flow": 3.0},
            {"ml": 55, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 0, "flow": 3.0},
        ],
    }
    run11 = {
        "dose": 18, "grind": 75, "ratio": 306 / 18,
        "pours": [
            {"ml": 79, "temp": 92, "pattern": "spiral", "agitation": False,
             "pause": 15, "rpm": 90, "flow": 3.0},
            {"ml": 107, "temp": 80, "pattern": "center", "agitation": False,
             "pause": 20, "rpm": 0, "flow": 3.2},
            {"ml": 65, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 0, "flow": 3.0},
            {"ml": 55, "temp": 90, "pattern": "spiral", "agitation": False,
             "pause": 5, "rpm": 0, "flow": 3.0},
        ],
    }
    assert build_load_frames(run0)[3][-3] == 0xA0
    assert build_load_frames(run11)[3][-3] == 0xAA


def test_build_41_tail_default_is_a0():
    """A recipe with no ratio keeps the 0xa0 (1:16) default tail (reference parity)."""
    body = build_41(RECIPES["simple"]["pours"], RECIPES["simple"]["grind"])
    assert body[-1] == 0xA0


# ---------------------------------------------------------------------------
# Brew-start path  (the ONLY place a start opcode may appear)
# ---------------------------------------------------------------------------
def test_start_frames_contain_load_prefix_and_start():
    """build_start_frames = the 4 LOAD frames + a brew-start tail."""
    frames = build_start_frames(RECIPES["simple"])
    # first four are exactly the load frames
    assert [f[3] for f in frames[:4]] == [0xA4, 0xA6, 0xA8, 0x41]
    # more frames follow (the start preamble + execute)
    assert len(frames) > 4
    # the execute opcode 0x119A appears as cmd 0x9a / seq 0x11
    assert any(f[3] == 0x9A and f[4] == 0x11 for f in frames), "execute frame present"


def test_start_path_load_prefix_still_load_only():
    """The LOAD prefix inside a start sequence never carries a forbidden opcode."""
    frames = build_start_frames(RECIPES["simple"])
    for f in frames[:4]:
        assert f[3] not in (FORBIDDEN_COMMIT_OPCODE, FORBIDDEN_START_OPCODE)


def test_load_path_never_has_start_frames():
    """SAFETY: the load path is strictly shorter than / a prefix of the start path."""
    load = build_load_frames(RECIPES["simple"])
    start = build_start_frames(RECIPES["simple"])
    assert len(load) == 4
    assert start[:4] == load  # start extends load; load never gains start frames


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

"""xbloom-ble — unofficial Bluetooth LE control for the xBloom Studio.

This package speaks the reverse-engineered BLE protocol of the xBloom Studio
pour-over machine. There is no official API.

⚠️ Safety invariant: the default ``brew`` path only ever *loads* a recipe onto
the machine — the machine then prompts, and the human physically approves the
brew on the device. :func:`build_load_frames` never emits the ``0x42`` (commit)
or ``0x46`` (start) opcodes. Brew-start lives in a separate, explicit, opt-in
builder (:func:`build_start_frames`, behind ``xbloom start`` / ``brew --start``).
Lower-level controls (``grind``/``pour``/``save-slot``) are likewise explicit
actions. See ``protocol.py``.
"""

__version__ = "0.2.0"

from .protocol import (
    PATTERN_CODES,
    build_load_frames,
    build_start_frames,
    crc16_kermit,
    ratio_to_tail,
    xbloom_frame,
)
from . import cloud
from .recipe import Pour, Recipe, RecipeError
from .telemetry import (
    STATE_NAMES,
    MachineInfo,
    StatusEvent,
    parse_machine_info,
    parse_notification,
    parse_scale_weight,
)

__all__ = [
    "__version__",
    "cloud",
    "build_load_frames",
    "build_start_frames",
    "ratio_to_tail",
    "PATTERN_CODES",
    "crc16_kermit",
    "xbloom_frame",
    "Recipe",
    "Pour",
    "RecipeError",
    "StatusEvent",
    "MachineInfo",
    "parse_notification",
    "parse_machine_info",
    "parse_scale_weight",
    "STATE_NAMES",
]

"""xbloom-ble — unofficial Bluetooth LE control for the xBloom Studio.

This package speaks the reverse-engineered BLE protocol of the xBloom Studio
pour-over machine. There is no official API.

⚠️ Safety invariant: this package only ever *loads* a recipe onto the machine.
The machine then prompts the user, who physically approves the brew on the
device itself. The package never emits the ``0x42`` (commit) or ``0x46`` (start)
opcodes, so it can never auto-start a brew. See ``protocol.py``.
"""

__version__ = "1.0.1"

from .protocol import PATTERN_CODES, build_load_frames, crc16_kermit, xbloom_frame
from .recipe import Pour, Recipe, RecipeError
from .telemetry import STATE_NAMES, StatusEvent, parse_notification

__all__ = [
    "__version__",
    "build_load_frames",
    "PATTERN_CODES",
    "crc16_kermit",
    "xbloom_frame",
    "Recipe",
    "Pour",
    "RecipeError",
    "StatusEvent",
    "parse_notification",
    "STATE_NAMES",
]

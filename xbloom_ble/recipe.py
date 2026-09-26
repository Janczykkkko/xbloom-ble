"""Recipe model, YAML loading, and validation for xBloom Studio brews.

A recipe describes a pour-over: the dose, grind, optional stage temperatures,
and an ordered list of pours. It is validated independently of any hardware so
that mistakes are caught before anything is sent over BLE.

YAML schema
-----------
.. code-block:: yaml

    name: Example
    dose_g: 16
    grind: 62
    stage_temps: [110.0, 90.0]   # optional; defaults to [110.0, 90.0]
    ratio: 15                    # optional; if given, Σpours must equal dose_g*ratio
    # optional brew-level metadata (informational — NOT sent to the machine):
    dripper: Omni
    kind: custom
    water_ml: 240
    time: "~2:00"
    note: strawberry-forward, ground finer as it aged
    pours:
      - {label: Bloom,  ml: 35,  temp_c: 90, pattern: spiral, pause_s: 40, rpm: 100, flow_ml_s: 3.0}
      - {label: Pour 1, ml: 115, temp_c: 90, pattern: spiral, pause_s: 5,  rpm: 100, flow_ml_s: 3.0}

Patterns: ``spiral``, ``ring``, ``center``. ``agitation: true`` (vibrate *after*
the pour, e.g. an agitated bloom) is accepted on ``spiral`` and ``ring`` pours —
the combinations the xBloom app itself stores. ``agitation_before: true`` is the
app's "vibration before" toggle. A recipe that validates is a valid *model* of
an app recipe; it does not promise that every property is reproduced over BLE:
the current BLE encoding carries ``spiral``+after only, so ``ring``+after and
every ``agitation_before`` are **kept in the model and on the cloud but omitted
from the BLE frames** (see :meth:`Recipe.ble_warnings`). The metadata fields
(``dripper``, ``kind``, ``water_ml``, ``hot_water_ml``, ``ice_g``, ``time``,
``note``, and per-pour ``label``) are optional context that round-trips through
YAML but never reaches the machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .protocol import PATTERN_CODES

__all__ = ["Pour", "Recipe", "RecipeError", "MODEL_PATTERN_COMBOS"]

# (pattern, agitation) combinations the *model* accepts. This is the BLE table
# plus the app-verified ``ring`` + "vibration after" (an app-made ring bloom reads
# back from the cloud with that toggle on). Anything not in the BLE table is
# projected at the BLE boundary — see ``Pour.to_protocol_dict`` / ``Pour.ble_omissions``.
MODEL_PATTERN_COMBOS: frozenset[tuple[str, bool]] = frozenset(PATTERN_CODES) | {("ring", True)}


class RecipeError(ValueError):
    """Raised when a recipe is malformed or fails validation."""


@dataclass
class Pour:
    """A single pour stage."""

    ml: int
    temp_c: int
    pattern: str = "spiral"
    agitation: bool = False
    pause_s: int = 0
    rpm: int = 0
    flow_ml_s: float = 3.0
    #: Optional human label for this pour (e.g. "Bloom", "Pour 1"). Informational
    #: only — never sent to the machine.
    label: str | None = None
    #: Agitate (dock-arm vibration) *before* this pour — the app's "전 진동" /
    #: "vibration before" toggle, typically used to level the bed before the bloom.
    #: Kept in the model and synced to the app account (``isEnableVibrationBefore``);
    #: the current BLE encoding has no decoded byte for it, so it is **omitted from
    #: the BLE frames** (reported by :meth:`ble_omissions`). Default ``False``.
    agitation_before: bool = False

    def ble_omissions(self) -> list[str]:
        """What of this pour the *current* BLE encoding cannot carry.

        Empty for a pour that is sent exactly as modelled. Otherwise a list of
        short phrases (``"vibration before"``, ``"vibration after on a ring pour"``)
        naming the properties that :meth:`to_protocol_dict` will leave out. This
        describes what the implementation can express today, not what the machine
        can do.
        """
        out: list[str] = []
        if self.agitation_before:
            out.append("vibration before")
        if self.agitation and (self.pattern, True) not in PATTERN_CODES:
            out.append(f"vibration after on a {self.pattern} pour")
        return out

    def to_protocol_dict(self) -> dict[str, Any]:
        """Shape expected by :func:`xbloom_ble.protocol.build_41`.

        This is the model → BLE boundary: a combination the BLE table cannot
        encode is *projected* onto the nearest one it can (``ring`` + after →
        ``ring`` without agitation; ``agitation_before`` has no byte at all).
        The projection happens here only — the ``Pour`` itself, ``to_dict()`` and
        the cloud mapping keep the original toggles. No new byte combinations
        are invented. See :meth:`ble_omissions` for what was dropped.
        """
        agitation = bool(self.agitation) and (self.pattern, True) in PATTERN_CODES
        return {
            "ml": self.ml,
            "temp": self.temp_c,
            "pattern": self.pattern,
            "agitation": agitation,
            "pause": self.pause_s,
            "rpm": self.rpm,
            "flow": self.flow_ml_s,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the YAML pour shape (round-trips with :meth:`Recipe.from_dict`)."""
        d: dict[str, Any] = {}
        if self.label is not None:
            d["label"] = self.label
        d.update(
            ml=int(self.ml),
            temp_c=int(self.temp_c),
            pattern=self.pattern,
            pause_s=int(self.pause_s),
            rpm=int(self.rpm),
            flow_ml_s=float(self.flow_ml_s),
            agitation=bool(self.agitation),
        )
        if self.agitation_before:
            d["agitation_before"] = True
        return d


@dataclass
class Recipe:
    """A full xBloom Studio recipe.

    The core fields (``dose_g``/``grind``/``stage_temps``/``pours``) are what the
    machine brews. The remaining fields are **optional brew-level metadata** —
    informational context a UI or recipe site can render, but which is *never*
    sent to the machine and *not* range-checked against hardware limits:

    * ``dripper`` — the dripper/brewer used (e.g. "Omni").
    * ``kind`` — recipe kind / preset base (e.g. "custom", "medium-auto").
    * ``water_ml`` — total brew water (may exceed Σ pours for bypass/iced brews).
    * ``hot_water_ml`` / ``ice_g`` — iced-brew specifics (hot water over ice).
    * ``time`` — expected brew time as a display string (e.g. "~2:00").
    * ``note`` — free-text notes about the recipe.
    """

    name: str
    dose_g: int
    grind: int
    pours: list[Pour]
    stage_temps: tuple[float, float] = (110.0, 90.0)
    ratio: float | None = None
    tail: int = 0xA0
    # Optional brew-level metadata (informational — never sent to the machine).
    dripper: str | None = None
    kind: str | None = None
    water_ml: int | None = None
    hot_water_ml: int | None = None
    ice_g: int | None = None
    time: str | None = None
    note: str | None = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recipe:
        if not isinstance(data, dict):
            raise RecipeError("recipe must be a mapping")
        try:
            raw_pours = data["pours"]
        except KeyError as exc:
            raise RecipeError("recipe is missing required key 'pours'") from exc
        if not isinstance(raw_pours, list) or not raw_pours:
            raise RecipeError("recipe 'pours' must be a non-empty list")

        pours: list[Pour] = []
        for i, rp in enumerate(raw_pours):
            if not isinstance(rp, dict):
                raise RecipeError(f"pour #{i + 1} must be a mapping")
            try:
                pours.append(
                    Pour(
                        ml=rp["ml"],
                        temp_c=rp["temp_c"],
                        pattern=rp.get("pattern", "spiral"),
                        agitation=bool(rp.get("agitation", False)),
                        pause_s=rp.get("pause_s", 0),
                        rpm=rp.get("rpm", 0),
                        flow_ml_s=rp.get("flow_ml_s", 3.0),
                        label=rp.get("label"),
                        agitation_before=bool(rp.get("agitation_before", False)),
                    )
                )
            except KeyError as exc:
                raise RecipeError(f"pour #{i + 1} missing key {exc}") from exc

        stage_temps = data.get("stage_temps", [110.0, 90.0])
        if not isinstance(stage_temps, (list, tuple)) or len(stage_temps) != 2:
            raise RecipeError("'stage_temps' must be a 2-element list [temp1, temp2]")

        for key in ("dose_g", "grind"):
            if key not in data:
                raise RecipeError(f"recipe is missing required key '{key}'")

        recipe = cls(
            name=str(data.get("name", "Unnamed")),
            dose_g=data["dose_g"],
            grind=data["grind"],
            pours=pours,
            stage_temps=(float(stage_temps[0]), float(stage_temps[1])),
            ratio=data.get("ratio"),
            tail=data.get("tail", 0xA0),
            # Optional brew-level metadata (informational — not sent to the machine).
            dripper=data.get("dripper"),
            kind=data.get("kind"),
            water_ml=data.get("water_ml"),
            hot_water_ml=data.get("hot_water_ml"),
            ice_g=data.get("ice_g"),
            time=data.get("time"),
            note=data.get("note"),
        )
        recipe.validate()
        return recipe

    @classmethod
    def from_yaml(cls, path: str | Path) -> Recipe:
        """Load and validate a recipe from a YAML file."""
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if data is None:
            raise RecipeError(f"recipe file {path} is empty")
        return cls.from_dict(data)

    @classmethod
    def from_yaml_text(cls, text: str, *, origin: str = "<text>") -> Recipe:
        """Load and validate a recipe from a YAML string."""
        data = yaml.safe_load(text)
        if data is None:
            raise RecipeError(f"recipe {origin} is empty")
        return cls.from_dict(data)

    @classmethod
    def from_source(cls, src: str | Path, *, timeout: float = 15.0) -> Recipe:
        """Load a recipe from a local path **or** an ``http(s)://`` URL.

        URLs are fetched with a short timeout and a 1 MB size cap; the body is
        parsed as the same YAML recipe format. Lets recipes be shared/served
        (e.g. ``xbloom brew https://…/teso-la-leona.yaml``).
        """
        s = str(src)
        if s.startswith(("http://", "https://")):
            from urllib.request import Request, urlopen

            req = Request(s, headers={"User-Agent": "xbloom-ble"})
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-supplied URL
                text = resp.read(1_000_000).decode("utf-8")
            return cls.from_yaml_text(text, origin=s)
        return cls.from_yaml(s)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Validate the recipe, raising :class:`RecipeError` on any problem."""
        errors: list[str] = []

        # dose / grind
        # dose: the xBloom app caps the dose at 18 g (firm app maximum).
        if not (1 <= int(self.dose_g) <= 18):
            errors.append(f"dose_g {self.dose_g} out of range (1–18 g; 18 g is the app maximum)")
        # grind: 1–80 — the grinder has 80 micro-steps (~18.75 µm each); a lower
        # number is finer. (xBloom Studio published spec.) A grind of 0 is the
        # special "no-grind" value: brew pre-ground, grinder off (see NO_GRIND).
        if int(self.grind) != 0 and not (1 <= int(self.grind) <= 80):
            errors.append(
                f"grind {self.grind} out of range (1–80; the grinder has 80 micro-steps — "
                f"or 0 for no-grind / pre-ground)"
            )

        # stage temps (machine preheat/stage set-points; default 110/90). These
        # are NOT the pour temperature and legitimately exceed the 95 °C pour cap,
        # so they keep the wider 40–130 °C allowance.
        for label, t in zip(("stage temp1", "stage temp2"), self.stage_temps, strict=False):
            if not (40 <= float(t) <= 130):
                errors.append(f"{label} {t}°C out of range (40–130°C)")

        # need at least a bloom + a first pour
        if len(self.pours) < 2:
            errors.append("recipe needs at least a bloom pour and a first pour (≥2 pours)")

        total_ml = 0
        for i, p in enumerate(self.pours, start=1):
            if (p.pattern, bool(p.agitation)) not in MODEL_PATTERN_COMBOS:
                valid = sorted({pat for pat, _ in MODEL_PATTERN_COMBOS})
                errors.append(
                    f"pour #{i}: pattern/agitation ({p.pattern!r}, {p.agitation}) "
                    f"not in known set {valid} (agitation is valid with 'spiral' or 'ring')"
                )
            # A pour over 127 ml is auto-split by the protocol — that is fine,
            # not an error. ml just needs to be ≥1 and fit a sane upper bound.
            if not (1 <= int(p.ml) <= 4000):
                errors.append(f"pour #{i}: ml {p.ml} out of range (1–4000)")
            # temp: settable 40–95 °C in 1 °C steps (xBloom Studio published spec).
            # The app also offers two special non-numeric settings, RT (room temp)
            # and BP (boiling point); those are not expressible as a numeric value
            # here, so the numeric validator range is 40–95.
            if not (40 <= int(p.temp_c) <= 95):
                errors.append(f"pour #{i}: temp_c {p.temp_c} out of range (40–95°C)")
            # rpm: agitation speed, 60–120 in 10-RPM steps — EXCEPT a `center` pour
            # has no agitation, where rpm must be 0. (xBloom Studio published spec.)
            if p.pattern == "center":
                if int(p.rpm) != 0 and not (60 <= int(p.rpm) <= 120):
                    errors.append(
                        f"pour #{i}: rpm {p.rpm} out of range (0 for a center pour, else 60–120)"
                    )
            else:
                if not (60 <= int(p.rpm) <= 120):
                    errors.append(f"pour #{i}: rpm {p.rpm} out of range (60–120)")
            # flow: 3.0–3.5 ml/s in 0.1 steps (xBloom Studio published spec).
            if not (3.0 <= float(p.flow_ml_s) <= 3.5):
                errors.append(f"pour #{i}: flow_ml_s {p.flow_ml_s} out of range (3.0–3.5)")
            # pause: the wire byte is (256 − seconds), so it can hold 0–255, but
            # the on-machine countdown caps near 99 s — that is the practical
            # range. We accept the full byte range here.
            if not (0 <= int(p.pause_s) <= 255):
                errors.append(f"pour #{i}: pause_s {p.pause_s} out of range (0–255)")
            total_ml += int(p.ml)

        # ratio check (only if a ratio is supplied)
        if self.ratio is not None:
            expected = round(float(self.dose_g) * float(self.ratio))
            if total_ml != expected:
                errors.append(
                    f"Σpours = {total_ml} ml but dose_g*ratio = "
                    f"{self.dose_g}*{self.ratio} = {expected} ml"
                )

        # Optional metadata: only sanity-check the numeric ones (not hardware
        # limits — these never reach the machine). Deliberately lenient so
        # informational context can't break a valid, brewable recipe.
        for label, v in (("water_ml", self.water_ml), ("hot_water_ml", self.hot_water_ml),
                         ("ice_g", self.ice_g)):
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                errors.append(f"{label} must be a non-negative number, got {v!r}")

        if errors:
            raise RecipeError("; ".join(errors))

    def ble_warnings(self) -> list[str]:
        """Per-pour notice of what the BLE load will *omit* from this recipe.

        Pure: no I/O, no logging. Empty when every pour is sent exactly as
        modelled. Each entry names the pour (1-based) and the omitted
        properties, e.g. ``"pour #1: vibration before omitted from the BLE load
        (kept in the recipe/cloud); ring pattern is sent"``. Callers that are about
        to write to the machine (``load_recipe``, ``save_slots``, the CLI, the TUI)
        surface these once before the first BLE write.
        """
        out: list[str] = []
        for i, p in enumerate(self.pours, start=1):
            omitted = p.ble_omissions()
            if omitted:
                out.append(
                    f"pour #{i}: {' and '.join(omitted)} omitted from the BLE load "
                    f"(kept in the recipe/cloud); {p.pattern} pattern is sent"
                )
        return out

    # ------------------------------------------------------------------
    # Protocol bridge
    # ------------------------------------------------------------------
    @property
    def no_grind(self) -> bool:
        """True if this recipe brews **pre-ground** (grinder off) — i.e. ``grind == 0``.

        The machine is asked to skip the grinder (wire byte ``0xFE``); its stored
        grind size is left untouched.
        """
        return int(self.grind) == 0

    @property
    def total_water_ml(self) -> int:
        return sum(int(p.ml) for p in self.pours)

    @property
    def effective_ratio(self) -> float:
        """Brew ratio (water : coffee). Uses the explicit ``ratio`` if given,
        else derives it from ``Σ pour ml / dose_g`` (one decimal). Used by the
        cloud mapping (``grandWater``)."""
        if self.ratio is not None:
            return float(self.ratio)
        return round(self.total_water_ml / float(self.dose_g), 1) if self.dose_g else 0.0

    def to_protocol_dict(self) -> dict[str, Any]:
        """Shape consumed by :func:`xbloom_ble.protocol.build_load_frames`.

        Note: ``tail`` (the pours-frame ratio byte) is intentionally omitted — the
        protocol layer derives it from Σ(pour ml) / dose, so it always matches the
        recipe. The machine rejects a load whose ratio byte is inconsistent.
        """
        return {
            "dose": int(self.dose_g),
            "grind": int(self.grind),
            "stage_temps": tuple(self.stage_temps),
            "pours": [p.to_protocol_dict() for p in self.pours],
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the YAML recipe shape (round-trips with :meth:`from_dict`).

        Emits the core fields plus any optional brew-level metadata that is set
        (omitting ``None``), so a recipe read from YAML and written back is stable.
        """
        d: dict[str, Any] = {"name": self.name, "dose_g": int(self.dose_g),
                             "grind": int(self.grind)}
        if self.ratio is not None:
            d["ratio"] = self.ratio
        d["stage_temps"] = [float(self.stage_temps[0]), float(self.stage_temps[1])]
        # optional metadata, in a stable, readable order (only when present)
        for key in ("kind", "dripper", "water_ml", "hot_water_ml", "ice_g", "time", "note"):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        d["pours"] = [p.to_dict() for p in self.pours]
        return d

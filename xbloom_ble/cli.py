"""Command-line interface for xbloom-ble.

Subcommands:

* ``xbloom scan``              — list discovered machines.
* ``xbloom validate <recipe>`` — validate a recipe file.
* ``xbloom brew <recipe>``     — load a recipe and stream telemetry. **Loads
  only** — the machine prompts and the human approves the brew on the device.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .recipe import Recipe, RecipeError
from .telemetry import StatusEvent

log = logging.getLogger("xbloom_ble")

LOAD_BANNER = (
    "✋ Recipe loaded. Add beans + cup, then APPROVE ON THE MACHINE to start. "
    "(This tool will NOT start it.)"
)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
async def _cmd_scan(args) -> int:
    from .client import scan

    devices = await scan(timeout=args.timeout)
    if not devices:
        print("No xBloom machines found.")
        return 1
    print(f"Found {len(devices)} machine(s):")
    for d in devices:
        name = getattr(d, "name", None) or "?"
        print(f"  {d.address}  {name}")
    return 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def _cmd_validate(args) -> int:
    try:
        recipe = Recipe.from_yaml(args.recipe)
    except RecipeError as exc:
        print(f"INVALID: {exc}")
        return 1
    except FileNotFoundError:
        print(f"ERROR: recipe file not found: {args.recipe}")
        return 2
    print(f"OK: '{recipe.name}' — {recipe.dose_g} g, grind {recipe.grind}, "
          f"{len(recipe.pours)} pours, {recipe.total_water_ml} ml total water")
    return 0


# ---------------------------------------------------------------------------
# brew (load only)
# ---------------------------------------------------------------------------
async def _cmd_brew(args) -> int:
    import os

    from .client import XBloomClient, scan

    # 1. Validate.
    try:
        recipe = Recipe.from_yaml(args.recipe)
    except RecipeError as exc:
        print(f"INVALID recipe: {exc}")
        return 1
    except FileNotFoundError:
        print(f"ERROR: recipe file not found: {args.recipe}")
        return 2
    print(f"Recipe '{recipe.name}' is valid ({recipe.total_water_ml} ml total).")

    # 2. Resolve address.
    address = args.address or os.environ.get("XBLOOM_ADDRESS")
    if not address:
        print("No --address / XBLOOM_ADDRESS given; scanning…")
        devices = await scan(timeout=args.scan_timeout)
        if not devices:
            print("ERROR: no xBloom machine found. Pass --address or set XBLOOM_ADDRESS.")
            return 2
        address = devices[0].address
        print(f"Using {address}.")

    # 3. Connect + load.
    events: list[dict] = []

    def _record(ev: StatusEvent) -> None:
        entry = {"t": round(time.time(), 3), "state": ev.state_name}
        if ev.water_g is not None:
            entry["water_g"] = ev.water_g
        if ev.coffee_g is not None:
            entry["coffee_g"] = ev.coffee_g
        events.append(entry)
        line = f"  [{ev.state_name}]"
        extras = []
        if ev.water_g is not None:
            extras.append(f"water {ev.water_g:g} g")
        if ev.coffee_g is not None:
            extras.append(f"coffee {ev.coffee_g:g} g")
        if extras:
            line += " " + ", ".join(extras)
        print(line)

    try:
        async with XBloomClient(address) as client:
            print("Loading recipe onto the machine…")
            armed = await client.load_recipe(recipe)
            _record(armed)
            print()
            print(LOAD_BANNER)
            print()
            print("Streaming telemetry (Ctrl-C to stop)…")
            await client.stream_telemetry(_record, duration=args.timeout)
    except Exception as exc:  # noqa: BLE001 - surface any BLE error cleanly
        print(f"ERROR: {exc}")
        return 3

    # 4. Save telemetry log.
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path.cwd() / f"telemetry-{stamp}.json"
    out.write_text(
        json.dumps(
            {"recipe": recipe.name, "address": address, "events": events},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nTelemetry log saved to {out}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xbloom",
        description="Unofficial Bluetooth LE control for the xBloom Studio "
        "(loads recipes only — the human approves the brew on the machine).",
    )
    p.add_argument("--version", action="version", version=f"xbloom-ble {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    sub = p.add_subparsers(dest="command", required=True)

    s_scan = sub.add_parser("scan", help="discover xBloom machines")
    s_scan.add_argument("--timeout", type=float, default=8.0, help="scan seconds (default 8)")

    s_val = sub.add_parser("validate", help="validate a recipe YAML file")
    s_val.add_argument("recipe", help="path to recipe YAML")

    s_brew = sub.add_parser(
        "brew",
        help="load a recipe and stream telemetry (does NOT start the brew)",
    )
    s_brew.add_argument("recipe", help="path to recipe YAML")
    s_brew.add_argument("--address", help="machine BLE address (or set XBLOOM_ADDRESS)")
    s_brew.add_argument("--timeout", type=float, default=300.0,
                        help="telemetry stream seconds (default 300)")
    s_brew.add_argument("--scan-timeout", type=float, default=8.0,
                        help="scan seconds when no address given (default 8)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "scan":
        return asyncio.run(_cmd_scan(args))
    if args.command == "brew":
        try:
            return asyncio.run(_cmd_brew(args))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return 130
    parser.error("unknown command")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

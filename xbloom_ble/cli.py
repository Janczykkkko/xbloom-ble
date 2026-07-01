"""Command-line interface for xbloom-ble.

Subcommands:

* ``xbloom scan``              — list discovered machines.
* ``xbloom validate <recipe>`` — validate a recipe file.
* ``xbloom info``              — print machine info (serial/firmware/units).
* ``xbloom brew <recipe>``     — load a recipe and stream telemetry. **Loads
  only** by default — the machine prompts and the human approves the brew.
  ``--start`` opts into a full unattended brew (⚠️ acts on the machine).
* ``xbloom start <recipe>``    — ⚠️ load AND start a brew (explicit opt-in).
* ``xbloom scale read|tare|units`` — read weight (free) / tare / set units.
* ``xbloom grind``             — ⚠️ run the grinder only (no brew).
* ``xbloom pour``              — ⚠️ FreeSolo single pour (dispenses hot water).
* ``xbloom save-slot``         — write an Easy-Mode preset to slot 1/2/3 (no brew).
* ``xbloom cloud …``           — push recipes to your xBloom **app account** via
  the *unofficial* xBloom cloud REST API (community-reverse-engineered). This is a
  separate subsystem from the BLE commands above and touches your account, not the
  machine over Bluetooth. Subcommands: ``login``, ``add-recipe``, ``list``,
  ``delete``, ``fetch``.

⚠️ The lower-level controls (``grind``/``pour``/``save-slot``/``scale tare``) and
``start`` / ``brew --start`` are EXPLICIT actions that act on the machine. The
default ``brew`` command remains load-only.
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

START_WARNING = (
    "⚠️  This WILL start a brew on the machine — staged only. Make sure a cup is "
    "in place and beans are loaded. Ctrl-C now to abort."
)

POUR_WARNING = (
    "⚠️  This DISPENSES HOT WATER from the machine — staged only. Make sure a cup "
    "is in place. Ctrl-C now to abort."
)

GRIND_WARNING = (
    "⚠️  This RUNS THE GRINDER — make sure beans are loaded. Ctrl-C now to abort."
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
# address resolution (shared)
# ---------------------------------------------------------------------------
async def _resolve_address(args) -> str | None:
    import os

    from .client import scan

    address = getattr(args, "address", None) or os.environ.get("XBLOOM_ADDRESS")
    if address:
        return address
    print("No --address / XBLOOM_ADDRESS given; scanning…")
    devices = await scan(timeout=getattr(args, "scan_timeout", 8.0))
    if not devices:
        print("ERROR: no xBloom machine found. Pass --address or set XBLOOM_ADDRESS.")
        return None
    print(f"Using {devices[0].address}.")
    return devices[0].address


def _load_recipe_or_exit(path):
    """Load+validate a recipe; return (recipe, None) or (None, exit_code)."""
    try:
        return Recipe.from_yaml(path), None
    except RecipeError as exc:
        print(f"INVALID recipe: {exc}")
        return None, 1
    except FileNotFoundError:
        print(f"ERROR: recipe file not found: {path}")
        return None, 2


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------
async def _cmd_info(args) -> int:
    from .client import XBloomClient

    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            info = await client.get_machine_info()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    if info is None:
        print("No machine-info reply received.")
        return 1
    print(str(info))
    return 0


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------
async def _cmd_scale(args) -> int:
    from .client import XBloomClient

    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            if args.action == "read":
                grams = await client.read_scale()
                if grams is None:
                    print("No scale reading received.")
                    return 1
                print(f"{grams:g} g")
            elif args.action == "tare":
                await client.tare_scale()
                print("Scale tared.")
            elif args.action == "units":
                await client.set_scale_units(args.unit)
                print(f"Scale unit set to {args.unit}.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# grind
# ---------------------------------------------------------------------------
async def _cmd_grind(args) -> int:
    from .client import XBloomClient

    print(GRIND_WARNING)
    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            print(f"Grinding: size {args.size}, dose {args.dose} g…")
            await client.grind(args.size, args.speed)
            print("Grinder command sent.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# pour (FreeSolo — dispenses hot water)
# ---------------------------------------------------------------------------
async def _cmd_pour(args) -> int:
    from .client import XBloomClient

    print(POUR_WARNING)
    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            print(f"Pouring {args.ml} ml @ {args.temp} °C ({args.pattern}, {args.flow} ml/s)…")
            await client.pour(
                args.ml, args.temp, flow=args.flow, pattern=args.pattern, rpm=args.rpm,
            )
            print("Pour command sent.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# save-slot
# ---------------------------------------------------------------------------
async def _cmd_save_slot(args) -> int:
    from .client import XBloomClient

    recipe, err = _load_recipe_or_exit(args.recipe)
    if err is not None:
        return err
    print(f"Writing '{recipe.name}' to Easy-Mode slot {args.slot}…")
    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            await client.save_slot(args.slot, recipe)
            print(f"Slot {args.slot} saved. (No brew was started.)")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# start (⚠️ opt-in full brew)
# ---------------------------------------------------------------------------
async def _cmd_start(args) -> int:
    from .client import XBloomClient

    recipe, err = _load_recipe_or_exit(args.recipe)
    if err is not None:
        return err
    print(f"Recipe '{recipe.name}' is valid ({recipe.total_water_ml} ml total).")
    print(START_WARNING)
    address = await _resolve_address(args)
    if not address:
        return 2
    try:
        async with XBloomClient(address) as client:
            print("Loading recipe and STARTING the brew…")
            await client.start_brew(recipe)
            print("Brew started. Streaming telemetry (Ctrl-C to stop)…")
            await client.stream_telemetry(
                lambda ev: print(f"  [{ev.state_name}]"), duration=args.timeout,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 0


# ---------------------------------------------------------------------------
# brew (load only by default; --start opts into a full brew)
# ---------------------------------------------------------------------------
async def _cmd_brew(args) -> int:
    from .client import XBloomClient

    # --start delegates to the explicit opt-in brew path.
    if getattr(args, "start", False):
        return await _cmd_start(args)

    # 1. Validate.
    recipe, err = _load_recipe_or_exit(args.recipe)
    if err is not None:
        return err
    print(f"Recipe '{recipe.name}' is valid ({recipe.total_water_ml} ml total).")

    # 2. Resolve address.
    address = await _resolve_address(args)
    if not address:
        return 2

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
# cloud (unofficial xBloom cloud REST API — pushes to the app account)
# ---------------------------------------------------------------------------
CLOUD_NOTE = (
    "ℹ️  'cloud' uses the UNOFFICIAL xBloom cloud API (community-reverse-"
    "engineered, may break). It touches YOUR xBloom app account."
)


def _cmd_cloud(args) -> int:
    """Synchronous handler for the `cloud` subcommand group (REST, not BLE)."""
    from .cloud import XBloomCloud, XBloomCloudError

    print(CLOUD_NOTE)
    try:
        client = XBloomCloud(auth_path=getattr(args, "auth_path", None))
        action = args.cloud_action
        if action == "login":
            import getpass
            import os

            email = args.email or os.environ.get("XBLOOM_EMAIL") or input("Email: ")
            password = (
                args.password
                or os.environ.get("XBLOOM_PASSWORD")
                or getpass.getpass("Password: ")
            )
            resp = client.login(email, password)
            member = resp.get("member", {})
            print(f"Logged in (member id={member.get('tableId')}). Auth cached.")
            return 0

        if action == "add-recipe":
            recipe, err = _load_recipe_or_exit(args.recipe)
            if err is not None:
                return err
            print(f"Pushing '{recipe.name}' to your xBloom account…")
            resp = client.add_recipe(recipe, cup_type=args.cup)
            print(f"Created. tableId={resp.get('tableId')}")
            return 0

        if action == "list":
            resp = client.list_recipes()
            recipes = resp.get("list", [])
            print(f"{len(recipes)} recipe(s):")
            for r in recipes:
                print(f"  [{r.get('tableId')}] {r.get('theName', '?')}")
            return 0

        if action == "delete":
            resp = client.delete_recipe(args.id)
            print(f"Deleted recipe {args.id}. ({resp.get('result')})")
            return 0

        if action == "fetch":
            resp = client.fetch_public(args.share)
            rv = resp.get("recipeVo", resp)
            if args.json:
                print(json.dumps(resp, ensure_ascii=False, indent=2))
            else:
                print(f"  Name:  {rv.get('theName', '?')}")
                print(f"  Dose:  {rv.get('dose', '?')} g")
                print(f"  Ratio: 1:{rv.get('grandWater', '?')}")
                print(f"  Grind: {rv.get('grinderSize', '?')}")
            return 0
    except XBloomCloudError as exc:
        print(f"ERROR: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 2


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xbloom",
        description="Unofficial Bluetooth LE control for the xBloom Studio. The "
        "default 'brew' is load-only (the human approves on the machine); "
        "grind/pour/save-slot and start are explicit, opt-in actions.",
    )
    p.add_argument("--version", action="version", version=f"xbloom-ble {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    sub = p.add_subparsers(dest="command", required=True)

    s_scan = sub.add_parser("scan", help="discover xBloom machines")
    s_scan.add_argument("--timeout", type=float, default=8.0, help="scan seconds (default 8)")

    s_val = sub.add_parser("validate", help="validate a recipe YAML file")
    s_val.add_argument("recipe", help="path to recipe YAML")

    def _add_conn_args(sp):
        sp.add_argument("--address", help="machine BLE address (or set XBLOOM_ADDRESS)")
        sp.add_argument("--scan-timeout", type=float, default=8.0,
                        help="scan seconds when no address given (default 8)")

    s_brew = sub.add_parser(
        "brew",
        help="load a recipe (load-only by default); --start to start the brew",
    )
    s_brew.add_argument("recipe", help="path to recipe YAML")
    _add_conn_args(s_brew)
    s_brew.add_argument("--timeout", type=float, default=300.0,
                        help="telemetry stream seconds (default 300)")
    s_brew.add_argument(
        "--start", action="store_true",
        help="⚠️ OPT-IN: also START the brew (acts on the machine — staged only)",
    )

    s_info = sub.add_parser("info", help="print machine info (serial/firmware/units)")
    _add_conn_args(s_info)

    s_start = sub.add_parser(
        "start",
        help="⚠️ load AND start a brew (explicit opt-in — acts on the machine)",
    )
    s_start.add_argument("recipe", help="path to recipe YAML")
    _add_conn_args(s_start)
    s_start.add_argument("--timeout", type=float, default=300.0,
                        help="telemetry stream seconds (default 300)")

    s_scale = sub.add_parser("scale", help="scale: read (free) / tare / units")
    scale_sub = s_scale.add_subparsers(dest="action", required=True)
    sc_read = scale_sub.add_parser("read", help="read the current weight (free)")
    _add_conn_args(sc_read)
    sc_tare = scale_sub.add_parser("tare", help="zero the scale (acts on the machine)")
    _add_conn_args(sc_tare)
    sc_units = scale_sub.add_parser("units", help="set the weight unit")
    sc_units.add_argument("unit", choices=["g", "oz", "ml"], help="weight unit")
    _add_conn_args(sc_units)

    s_grind = sub.add_parser("grind", help="⚠️ run the grinder only (no brew)")
    s_grind.add_argument("--size", type=int, required=True, help="grind size 1–80")
    s_grind.add_argument("--dose", type=float, required=True, help="dose in grams")
    s_grind.add_argument("--speed", type=int, default=90, help="burr speed 60–120 (default 90)")
    _add_conn_args(s_grind)

    s_pour = sub.add_parser("pour", help="⚠️ FreeSolo single pour (dispenses hot water)")
    s_pour.add_argument("--ml", type=int, required=True, help="pour volume in ml")
    s_pour.add_argument("--temp", type=int, required=True, help="water temperature °C")
    s_pour.add_argument("--flow", type=float, default=3.0, help="flow ml/s (default 3.0)")
    s_pour.add_argument("--pattern", choices=["spiral", "ring", "center"],
                        default="spiral", help="pour pattern (default spiral)")
    s_pour.add_argument("--rpm", type=int, default=90, help="agitation rpm (default 90)")
    _add_conn_args(s_pour)

    s_slot = sub.add_parser("save-slot", help="write an Easy-Mode preset to a slot (no brew)")
    s_slot.add_argument("slot", type=int, choices=[1, 2, 3], help="slot number 1/2/3 (A/B/C)")
    s_slot.add_argument("recipe", help="path to recipe YAML")
    _add_conn_args(s_slot)

    # cloud — unofficial xBloom cloud REST API (pushes to the app account)
    s_cloud = sub.add_parser(
        "cloud",
        help="push recipes to your xBloom APP account (unofficial cloud API)",
        description="Push recipes to your xBloom mobile-app account via the "
        "UNOFFICIAL, community-reverse-engineered xBloom cloud REST API. This is a "
        "separate subsystem from the BLE commands and touches YOUR account (not the "
        "machine over Bluetooth). Credentials come from XBLOOM_EMAIL / XBLOOM_PASSWORD "
        "or the 'login' prompt; the token is cached locally.",
    )
    s_cloud.add_argument(
        "--auth-path", help="override the cached-auth JSON path "
        "(default ~/.config/xbloom-ble/cloud-auth.json or $XBLOOM_CLOUD_AUTH)",
    )
    cloud_sub = s_cloud.add_subparsers(dest="cloud_action", required=True)

    c_login = cloud_sub.add_parser("login", help="log in and cache the token (uses your account)")
    c_login.add_argument("--email", help="account email (or set XBLOOM_EMAIL)")
    c_login.add_argument("--password", help="account password (or set XBLOOM_PASSWORD; else prompt)")

    c_add = cloud_sub.add_parser(
        "add-recipe", help="⚠️ create a recipe in your account from a recipe YAML",
    )
    c_add.add_argument("recipe", help="path to recipe YAML")
    c_add.add_argument(
        "--cup", default="xdripper",
        choices=["xpod", "xdripper", "other", "tea"],
        help="cup type for the cloud recipe (default xdripper)",
    )

    cloud_sub.add_parser("list", help="list the recipes in your account")

    c_del = cloud_sub.add_parser("delete", help="⚠️ delete a recipe from your account")
    c_del.add_argument("id", help="the recipe tableId to delete")

    c_fetch = cloud_sub.add_parser("fetch", help="fetch a public shared recipe (no login)")
    c_fetch.add_argument("share", help="share id or share URL")
    c_fetch.add_argument("--json", action="store_true", help="print the raw JSON response")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))

    if args.command == "validate":
        return _cmd_validate(args)

    if args.command == "cloud":
        return _cmd_cloud(args)

    _async_cmds = {
        "scan": _cmd_scan,
        "info": _cmd_info,
        "brew": _cmd_brew,
        "start": _cmd_start,
        "scale": _cmd_scale,
        "grind": _cmd_grind,
        "pour": _cmd_pour,
        "save-slot": _cmd_save_slot,
    }
    handler = _async_cmds.get(args.command)
    if handler is not None:
        try:
            return asyncio.run(handler(args))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return 130
    parser.error("unknown command")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

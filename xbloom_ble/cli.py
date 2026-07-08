"""Command-line interface for xbloom-ble.

Subcommands:

* ``xbloom scan``              — list discovered machines.
* ``xbloom validate <recipe>`` — validate a recipe file.
* ``xbloom brew <recipe>``     — load a recipe and stream telemetry. **Loads
  only** by default — the machine prompts and the human approves on the device.
  Pass ``--start`` to also launch the brew remotely (commit + start), like the
  app's Brew button. ⚠️ ``--start`` dispenses hot water — only with the machine ready.
* ``xbloom save-slots A B C``   — program the machine's three Easy-Mode dial
  presets from three recipes (a preset write — never brews). Presets live on the
  machine; the xBloom app overwrites them if you reassign a slot there.
* ``xbloom cloud …``           — push recipes to your xBloom **app account** via
  the *unofficial* xBloom cloud REST API (separate from BLE machine control).
  Recipes created here are named ``AUTO <name>`` and are the only ones this tool
  will update or delete — your own recipes are never touched.
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
    "(Loaded only — this tool did NOT start it. Re-run with --start to launch remotely.)"
)
START_BANNER = (
    "🔥 Starting the brew remotely (commit + start) — the machine is dispensing "
    "hot water now."
)


def _silence_ble_stack() -> None:
    """Keep the BlueZ/D-Bus stack's own DEBUG chatter off the console — we only want
    OUR frames. (Without this, ``--debug``/``-v`` would flood stderr with dbus noise.)"""
    for name in ("bleak", "bleak.backends", "dbus_fast", "dbus_next"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _setup_logging(verbose: bool, debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    _silence_ble_stack()
    if debug:
        # DEBUG for OUR logger only (never the BLE stack), teed to a timestamped file
        # so a session's full frame chatter can be captured and shared for diagnosis.
        xlog = logging.getLogger("xbloom_ble")
        xlog.setLevel(logging.DEBUG)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = Path.cwd() / f"xbloom-debug-{stamp}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s",
                                               datefmt="%H:%M:%S"))
        xlog.addHandler(handler)
        print(f"🐛 BLE debug log → {path}")


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
    recipe, err = _load_recipe_or_exit(args.recipe)
    if err is not None:
        return err
    grind_str = "no-grind (pre-ground)" if recipe.no_grind else f"grind {recipe.grind}"
    print(f"OK: '{recipe.name}' — {recipe.dose_g} g, {grind_str}, "
          f"{len(recipe.pours)} pours, {recipe.total_water_ml} ml total water")
    return 0


# ---------------------------------------------------------------------------
# brew (load only)
# ---------------------------------------------------------------------------
async def _cmd_brew(args) -> int:
    import os

    from .client import XBloomClient, scan

    # 1. Load + validate (from a local path or an http(s) URL).
    recipe, err = _load_recipe_or_exit(args.recipe)
    if err is not None:
        return err
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
            if getattr(args, "start", False):
                print(START_BANNER)
                print()
                brewing = await client.start()
                _record(brewing)
            else:
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
# save-slots (program the three Easy-Mode presets — does NOT brew)
# ---------------------------------------------------------------------------
SLOTS_RECONNECT_WARNING = (
    "⚠️  These presets live ON THE MACHINE. If you later open the xBloom app and "
    "reassign a slot, the app pushes ITS choices over Bluetooth and overwrites "
    "these. Program the slots to drive the machine from its dial (no app)."
)


async def _cmd_save_slots(args) -> int:
    import os

    from .client import XBloomClient, scan

    # Load all three recipes (A, B, C) — the machine only stores a full set.
    recipes = []
    for src in (args.slot_a, args.slot_b, args.slot_c):
        recipe, err = _load_recipe_or_exit(src)
        if err is not None:
            return err
        recipes.append(recipe)

    # --scale-off is a comma list of slot letters whose stored preset has the
    # scale disabled (default: all three on).
    off = {s.strip().upper() for s in (args.scale_off or "").split(",") if s.strip()}
    if off - {"A", "B", "C"}:
        print(f"ERROR: --scale-off takes slot letters A/B/C; got {args.scale_off!r}")
        return 2
    scales = [letter not in off for letter in "ABC"]

    address = args.address or os.environ.get("XBLOOM_ADDRESS")
    if not address:
        print("No --address / XBLOOM_ADDRESS given; scanning…")
        devices = await scan(timeout=args.scan_timeout)
        if not devices:
            print("ERROR: no xBloom machine found. Pass --address or set XBLOOM_ADDRESS.")
            return 2
        address = devices[0].address
        print(f"Using {address}.")

    for letter, recipe, on in zip("ABC", recipes, scales, strict=True):
        print(f"  slot {letter} ← '{recipe.name}' (scale {'on' if on else 'off'})")
    print("Writing all three presets — NOT a brew…")
    try:
        async with XBloomClient(address) as client:
            await client.save_slots(recipes, scale=scales)
    except Exception as exc:  # noqa: BLE001 - surface any BLE error cleanly
        print(f"ERROR: {exc}")
        print("(If the machine shows RETRY: make sure the phone/app is disconnected "
              "so the machine has a single BLE link, then try again.)")
        return 3
    print("✓ Presets stored to slots A/B/C. The machine did not brew.")
    print(SLOTS_RECONNECT_WARNING)
    return 0


# ---------------------------------------------------------------------------
# cloud (unofficial xBloom cloud REST API — pushes to the app account)
# ---------------------------------------------------------------------------
CLOUD_NOTE = (
    "ℹ️  'cloud' uses the UNOFFICIAL xBloom cloud API (community-reverse-"
    "engineered, may break). It touches YOUR xBloom app account."
)


def _load_recipe_or_exit(src: str):
    """Load a recipe from a local path or an http(s) URL → (recipe, error_code)."""
    try:
        return Recipe.from_source(src), None
    except RecipeError as exc:
        print(f"INVALID recipe: {exc}")
        return None, 1
    except FileNotFoundError:
        print(f"ERROR: recipe file not found: {src}")
        return None, 2
    except OSError as exc:  # URL fetch failure (URLError/HTTPError/timeout subclass OSError)
        print(f"ERROR: could not fetch recipe from {src}: {exc}")
        return None, 2


def _cmd_cloud(args) -> int:
    """Handler for the ``cloud`` subcommand group (REST, not BLE)."""
    import os

    from .cloud import MANAGED_PREFIX, XBloomCloud, XBloomCloudError

    print(CLOUD_NOTE)
    try:
        client = XBloomCloud(auth_path=getattr(args, "auth_path", None))
        action = args.cloud_action

        if action == "login":
            import getpass

            email = args.email or os.environ.get("XBLOOM_EMAIL") or input("Email: ")
            password = (
                args.password
                or os.environ.get("XBLOOM_PASSWORD")
                or getpass.getpass("Password: ")
            )
            resp = client.login(email, password)
            print(f"Logged in (member id={resp.get('member', {}).get('tableId')}). Auth cached.")
            return 0

        if action == "list":
            recipes = client.list_recipes(adapted_model=0).get("list", [])
            print(f"{len(recipes)} recipe(s) ('*' = tool-owned {MANAGED_PREFIX!r}):")
            for r in recipes:
                name = r.get("theName", "?")
                owned = "*" if str(name).startswith(MANAGED_PREFIX) else " "
                print(f" {owned}[{r.get('tableId')}] {name}")
            return 0

        if action in ("sync", "add-recipe"):
            recipe, err = _load_recipe_or_exit(args.recipe)
            if err is not None:
                return err
            if action == "sync":
                # Idempotent + safe: forces the AUTO prefix, updates the matching
                # tool-owned recipe or adds a new one; never touches your recipes.
                resp, act = client.sync_recipe(recipe, cup_type=args.cup)
                tid = resp.get("tableId", "?")
                print(f"{act.capitalize()}: '{MANAGED_PREFIX}{recipe.name}' (tableId={tid}).")
            else:
                print(f"Pushing '{recipe.name}' to your xBloom account…")
                resp = client.add_recipe(recipe, cup_type=args.cup)
                print(f"Created. tableId={resp.get('tableId')}")
            return 0

        if action == "delete":
            # Guarded: refuses unless the target is an AUTO … (tool-owned) recipe.
            resp = client.delete_recipe(args.id)
            print(f"Deleted recipe {args.id}. ({resp.get('result')})")
            return 0

        if action == "fetch":
            resp = client.fetch_public(args.share)
            rv = resp.get("recipeVo", resp)
            if getattr(args, "json", False):
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
        description="Unofficial Bluetooth LE control for the xBloom Studio "
        "(load a recipe, then approve on the machine or start the brew remotely).",
    )
    p.add_argument("--version", action="version", version=f"xbloom-ble {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    # No subcommand → launch the TUI (an interactive terminal UI).
    sub = p.add_subparsers(dest="command", required=False)

    s_tui = sub.add_parser("tui", help="launch the interactive terminal UI (default)")
    s_tui.add_argument("--recipes", metavar="DIR",
                       help="recipe directory (default ~/.xbloom/recipes)")
    s_tui.add_argument("--address", help="machine BLE address (or set XBLOOM_ADDRESS)")
    s_tui.add_argument("--demo", action="store_true",
                       help="run against a simulated machine (no hardware needed)")
    s_tui.add_argument("--auto-brew", action="store_true",
                       help="start a brew immediately (demos/tests)")
    s_tui.add_argument("--debug", action="store_true",
                       help="also log the full BLE chatter to a file (xbloom-debug-*.log)")

    s_scan = sub.add_parser("scan", help="discover xBloom machines")
    s_scan.add_argument("--timeout", type=float, default=8.0, help="scan seconds (default 8)")

    s_val = sub.add_parser("validate", help="validate a recipe YAML file")
    s_val.add_argument("recipe", help="path to a recipe YAML, or an http(s):// URL")

    s_brew = sub.add_parser(
        "brew",
        help="load a recipe and stream telemetry (add --start to launch the brew)",
    )
    s_brew.add_argument("recipe", help="path to a recipe YAML, or an http(s):// URL")
    s_brew.add_argument("--start", action="store_true",
                        help="also START the brew remotely (commit+start) — ⚠️ dispenses hot water")
    s_brew.add_argument("--address", help="machine BLE address (or set XBLOOM_ADDRESS)")
    s_brew.add_argument("--timeout", type=float, default=300.0,
                        help="telemetry stream seconds (default 300)")
    s_brew.add_argument("--scan-timeout", type=float, default=8.0,
                        help="scan seconds when no address given (default 8)")
    s_brew.add_argument("--debug", action="store_true",
                        help="log the full BLE chatter to a file (xbloom-debug-*.log)")

    s_slot = sub.add_parser(
        "save-slots",
        help="program the 3 machine preset slots A/B/C from 3 recipes (presets — NOT a brew)",
        description="Program the machine's three Easy-Mode dial presets (A, B, C) from three "
        "recipes, in one batch. The machine only stores a full A/B/C set, so all three are "
        "required. Presets live on the machine; reassigning a slot in the xBloom app will "
        "overwrite them. Never starts a brew.",
    )
    _rc = "recipe YAML path or http(s):// URL"
    s_slot.add_argument("slot_a", metavar="RECIPE_A", help=f"slot A {_rc}")
    s_slot.add_argument("slot_b", metavar="RECIPE_B", help=f"slot B {_rc}")
    s_slot.add_argument("slot_c", metavar="RECIPE_C", help=f"slot C {_rc}")
    s_slot.add_argument("--scale-off", metavar="LETTERS",
                        help="comma list of slots whose stored preset disables the scale, "
                        "e.g. 'C' or 'A,C' (default: all on)")
    s_slot.add_argument("--address", help="machine BLE address (or set XBLOOM_ADDRESS)")
    s_slot.add_argument("--scan-timeout", type=float, default=8.0,
                        help="scan seconds when no address given (default 8)")
    s_slot.add_argument("--debug", action="store_true",
                        help="log the full BLE chatter to a file (xbloom-debug-*.log)")

    # cloud — unofficial xBloom cloud REST API (pushes to the app account)
    s_cloud = sub.add_parser(
        "cloud",
        help="push recipes to your xBloom APP account (unofficial cloud API)",
        description="UNOFFICIAL, community-reverse-engineered xBloom cloud REST API. "
        "Pushes recipes to your app account (separate from BLE machine control). "
        f"Recipes created here are named '{'AUTO'} <name>' and are the ONLY ones this "
        "tool will ever update or delete — your own recipes are never touched.",
    )
    s_cloud.add_argument(
        "--auth-path",
        help="token cache path (default under ~/.config/xbloom-ble/ or $XBLOOM_CLOUD_AUTH)",
    )
    cloud_sub = s_cloud.add_subparsers(dest="cloud_action", required=True)

    c_login = cloud_sub.add_parser("login", help="log in and cache the token")
    c_login.add_argument("--email", help="account email (or $XBLOOM_EMAIL)")
    c_login.add_argument("--password", help="account password (or $XBLOOM_PASSWORD)")

    c_sync = cloud_sub.add_parser(
        "sync", help="create-or-update a tool-owned 'AUTO …' recipe (idempotent, safe)"
    )
    c_sync.add_argument("recipe", help="path to a recipe YAML, or an http(s):// URL")
    c_sync.add_argument("--cup", default="omni", help="cup type (default omni)")

    c_add = cloud_sub.add_parser(
        "add-recipe", help="⚠️ create a recipe in your account from a recipe YAML"
    )
    c_add.add_argument("recipe", help="path to a recipe YAML, or an http(s):// URL")
    c_add.add_argument("--cup", default="omni", help="cup type (default omni)")

    cloud_sub.add_parser("list", help="list the recipes in your account (marks tool-owned)")

    c_del = cloud_sub.add_parser("delete", help="delete a recipe (only AUTO … recipes)")
    c_del.add_argument("id", help="recipe tableId to delete")

    c_fetch = cloud_sub.add_parser("fetch", help="fetch a publicly shared recipe (no auth)")
    c_fetch.add_argument("share", help="share id or share URL")
    c_fetch.add_argument("--json", action="store_true", help="print raw JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # The TUI owns the screen and manages its own logging (activity panel + optional
    # debug file), so it must NOT get a stderr log handler — that would paint over it.
    if args.command in (None, "tui"):
        _silence_ble_stack()
        return _cmd_tui(args)

    _setup_logging(getattr(args, "verbose", False), getattr(args, "debug", False))

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
    if args.command == "save-slots":
        try:
            return asyncio.run(_cmd_save_slots(args))
        except KeyboardInterrupt:
            print("\nInterrupted.")
            return 130
    if args.command == "cloud":
        return _cmd_cloud(args)
    parser.error("unknown command")  # tui/None handled above
    return 2  # pragma: no cover


def _cmd_tui(args) -> int:
    import os

    from .tui import run_tui
    return run_tui(
        recipes_dir=getattr(args, "recipes", None),
        address=getattr(args, "address", None) or os.environ.get("XBLOOM_ADDRESS"),
        demo=getattr(args, "demo", False),
        auto_brew=getattr(args, "auto_brew", False),
        debug=getattr(args, "debug", False),
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

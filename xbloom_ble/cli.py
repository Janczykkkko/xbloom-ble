"""Command-line interface for xbloom-ble.

Subcommands:

* ``xbloom scan``              — list discovered machines.
* ``xbloom validate <recipe>`` — validate a recipe file.
* ``xbloom brew <recipe>``     — load a recipe and stream telemetry. **Loads
  only** — the machine prompts and the human approves the brew on the device.
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
# cloud (unofficial xBloom cloud REST API — pushes to the app account)
# ---------------------------------------------------------------------------
CLOUD_NOTE = (
    "ℹ️  'cloud' uses the UNOFFICIAL xBloom cloud API (community-reverse-"
    "engineered, may break). It touches YOUR xBloom app account."
)


def _load_recipe_or_exit(path: str):
    try:
        return Recipe.from_yaml(path), None
    except RecipeError as exc:
        print(f"INVALID recipe: {exc}")
        return None, 1
    except FileNotFoundError:
        print(f"ERROR: recipe file not found: {path}")
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
        help="token cache path (default ~/.config/xbloom-ble/cloud-auth.json or $XBLOOM_CLOUD_AUTH)",
    )
    cloud_sub = s_cloud.add_subparsers(dest="cloud_action", required=True)

    c_login = cloud_sub.add_parser("login", help="log in and cache the token")
    c_login.add_argument("--email", help="account email (or $XBLOOM_EMAIL)")
    c_login.add_argument("--password", help="account password (or $XBLOOM_PASSWORD)")

    c_sync = cloud_sub.add_parser(
        "sync", help="create-or-update a tool-owned 'AUTO …' recipe (idempotent, safe)"
    )
    c_sync.add_argument("recipe", help="path to recipe YAML")
    c_sync.add_argument("--cup", default="omni", help="cup type (default omni)")

    c_add = cloud_sub.add_parser(
        "add-recipe", help="⚠️ create a recipe in your account from a recipe YAML"
    )
    c_add.add_argument("recipe", help="path to recipe YAML")
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
    if args.command == "cloud":
        return _cmd_cloud(args)
    parser.error("unknown command")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

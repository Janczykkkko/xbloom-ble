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
                # The machine checks water/beans right after commit. If it refused,
                # start() already consumed that one status frame (the telemetry stream
                # won't repeat it) — so cancel back to idle and stop here instead of
                # streaming forever waiting for a brew that will never begin.
                if brewing.state_name in ("no_water", "no_beans"):
                    what = "water in the tank" if brewing.state_name == "no_water" else "beans"
                    print(f"\n⚠️  Machine refused: no {what}. Aborting (0x47).")
                    await client.cancel_brew()
                    return 0
            else:
                print(LOAD_BANNER)
            print()
            print("Streaming telemetry (Ctrl-C to stop)…")
            # With --debug, also tap ffe3 to capture the live-scale weight stream (the
            # grams aren't on ffe2). Log-only; never affects the brew.
            await client.stream_telemetry(
                _record, duration=args.timeout, capture_aux=getattr(args, "debug", False)
            )
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

        if action == "sync-all":
            return _cloud_sync_all(client, args)
    except XBloomCloudError as exc:
        print(f"ERROR: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 3
    return 2


def _cloud_sync_all(client, args) -> int:
    """Sync every local recipe to the cloud account. By default under each recipe's OWN name —
    overwriting an identically-named account recipe (with a ⚠ warning per overwrite). ``--managed``
    uses the safe ``AUTO …`` prefix instead (never touches your own recipes)."""
    from . import config as cfgmod
    from .cloud import MANAGED_PREFIX
    from .tui.store import RecipeStore

    rdir = args.dir or str(cfgmod.load().resolved_recipes_dir)
    entries = [e for e in RecipeStore(rdir).list() if e.ok]
    if not entries:
        print(f"No valid recipes found in {rdir}.")
        return 0
    prefix = MANAGED_PREFIX if args.managed else ""
    mode = (f"prefixed '{MANAGED_PREFIX}…'" if prefix
            else "under their own names (overwrites same-named)")
    print(f"Syncing {len(entries)} recipe(s) from {rdir} — {mode}:")
    added = updated = 0
    for e in entries:
        resp, act = client.sync_recipe(e.recipe, prefix=prefix, cup_type=args.cup)
        name = (prefix + e.recipe.name) if prefix else e.recipe.name
        if act == "updated":
            updated += 1
            print(f"  ⚠ overwrote existing '{name}' (tableId={resp.get('tableId', '?')})")
        else:
            added += 1
            print(f"  ✓ added '{name}'")
    print(f"Done: {added} added, {updated} overwritten.")
    return 0


def _cmd_init(args) -> int:
    """First-run setup: pair the machine (save its address), optionally log in to the cloud, and
    write the config. Interactive on a TTY; on a non-TTY / CI it uses args + env only."""
    import getpass
    import os

    from . import config as cfgmod
    from . import paths

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("CI")
    cfg = cfgmod.load()
    print(f"xBloom setup — writes {paths.config_file()}  (re-run anytime; nothing is wiped)\n")

    # 1) machine address — arg / env / scan-and-pick / keep existing
    addr = args.address or os.environ.get("XBLOOM_ADDRESS") or cfg.address
    if not addr and not args.no_scan and interactive:
        from .client import scan
        print("1) Scanning for xBloom machines (8 s)…")
        try:
            devices = asyncio.run(scan(timeout=8.0))
        except Exception as exc:  # noqa: BLE001
            print(f"   scan failed ({exc}); set --address later.")
            devices = []
        for i, d in enumerate(devices, 1):
            print(f"   [{i}] {d.address}  {getattr(d, 'name', None) or '?'}")
        if devices:
            choice = input("   pick a machine [1] (Enter to skip): ").strip() or "1"
            if choice.isdigit() and 1 <= int(choice) <= len(devices):
                addr = devices[int(choice) - 1].address
        else:
            print("   none found (machine on? phone app closed?) — set later with --address.")
    if addr:
        cfg.address = addr
        print(f"   ✓ machine address: {addr}\n")

    # 2) recipe store
    rdir = cfg.resolved_recipes_dir
    paths.ensure_dir(rdir)
    print(f"2) Recipe store: {rdir}\n")

    # 3) optional cloud login (skippable; never blocks BLE use)
    if not args.no_cloud and interactive:
        prompt = "3) Link your xBloom app account for cloud recipe sync? [y/N]: "
        ans = input(prompt).strip().lower()
        if ans in ("y", "yes"):
            from .cloud import XBloomCloud
            email = args.email or os.environ.get("XBLOOM_EMAIL") or input("   Email: ").strip()
            password = os.environ.get("XBLOOM_PASSWORD") or getpass.getpass("   Password: ")
            try:
                XBloomCloud(email=email, password=password).login()
                cfg.cloud_email = email
                print("   ✓ logged in — token cached (your password is NOT stored).\n")
            except Exception as exc:  # noqa: BLE001
                print(f"   ✗ login failed: {exc} — skip for now, try `xbloom cloud login` later.\n")

    cfgmod.save(cfg)
    print(f"✓ Setup saved to {paths.config_file()}.")
    print("  Next:  xbloom tui   ·   xbloom doctor   ·   xbloom cloud sync-all")
    return 0


def _cmd_config(args) -> int:
    from . import config as cfgmod
    from . import paths

    if args.config_action == "path":
        print(paths.config_file())
        return 0
    cfg = cfgmod.load()
    exists = " (exists)" if cfgmod.exists() else " (not created — run `xbloom init`)"
    print(f"config file : {paths.config_file()}{exists}")
    print(f"recipes dir : {cfg.resolved_recipes_dir}")
    print(f"history     : {paths.history_file()}")
    print(f"slots       : {paths.slots_file()}")
    tok = paths.token_file()
    print(f"cloud token : {tok}  ({'cached' if tok.exists() else 'not logged in'})")
    print(f"address     : {cfg.address or '(none — scans on launch)'}")
    print(f"cloud email : {cfg.cloud_email or '(not set)'}")
    print(f"scale on    : {cfg.scale_on}")
    return 0


def _cmd_doctor(args) -> int:
    import os

    from . import config as cfgmod
    from . import paths

    cfg = cfgmod.load()
    ok = True

    def check(good, msg):
        nonlocal ok
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {msg}")

    print("xbloom doctor —")
    check(True, f"config: {paths.config_file()}"
          + ("" if cfgmod.exists() else "  (no config yet — run `xbloom init`)"))
    try:
        paths.ensure_dir(cfg.resolved_recipes_dir)
        writable = os.access(cfg.resolved_recipes_dir, os.W_OK)
    except OSError:
        writable = False
    check(writable, f"recipe store writable: {cfg.resolved_recipes_dir}")
    tok = paths.token_file()
    if tok.exists():
        warn = paths.tighten_if_loose(tok)
        check(True, f"cloud token cached ({tok})" + (f" — {warn}" if warn else " — perms OK"))
    else:
        check(True, "cloud token: not logged in (optional)")
    try:
        import textual  # noqa: F401
        check(True, "TUI deps installed")
    except ImportError:
        check(False, "TUI deps missing — pip install 'xbloom-ble[tui]'")
    try:
        import cryptography  # noqa: F401
        check(True, "cloud deps installed")
    except ImportError:
        check(False, "cloud deps missing — pip install 'xbloom-ble[cloud]'")
    if getattr(args, "scan", False):
        from .client import scan
        try:
            found = asyncio.run(scan(timeout=6.0))
            check(bool(found),
                  f"machine reachable ({len(found)} found)" if found
                  else "no machine found (on? phone app closed?)")
        except Exception as exc:  # noqa: BLE001
            check(False, f"scan failed: {exc}")
    print("  →", "all good" if ok else "some checks failed (see above)")
    return 0 if ok else 1


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

    s_init = sub.add_parser(
        "init", help="first-run setup — pair the machine, optional cloud login, write config")
    s_init.add_argument("--address", help="use this BLE address (skip scanning)")
    s_init.add_argument("--email", help="cloud account email (or $XBLOOM_EMAIL)")
    s_init.add_argument("--no-scan", action="store_true", help="don't scan for a machine")
    s_init.add_argument("--no-cloud", action="store_true", help="skip the cloud-login step")

    s_config = sub.add_parser("config", help="show the config file location / current settings")
    config_sub = s_config.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("path", help="print the config file path")
    config_sub.add_parser("show", help="print the resolved config + data/state locations")

    s_doctor = sub.add_parser("doctor", help="check config, dirs, deps and the cached token")
    s_doctor.add_argument("--scan", action="store_true", help="also scan for the machine")

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

    c_syncall = cloud_sub.add_parser(
        "sync-all",
        help="sync ALL local recipes to your account (overwrites same-named — warns)")
    c_syncall.add_argument("--dir", help="recipe directory (default: your config's recipe store)")
    c_syncall.add_argument("--cup", default="omni", help="cup type (default omni)")
    c_syncall.add_argument(
        "--managed", action="store_true",
        help="use the safe 'AUTO …' prefix instead (never overwrite your own recipes)")
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
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
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

"""Interactive terminal UI for xbloom-ble (optional extra: ``pip install xbloom-ble[tui]``).

``xbloom`` with no subcommand — or ``xbloom tui`` — launches it.
"""

from __future__ import annotations


def run_tui(
    *,
    recipes_dir: str | None = None,
    address: str | None = None,
    demo: bool = False,
    auto_brew: bool = False,
    debug: bool = False,
    auto_connect: bool | None = None,
) -> int:
    """Build the store + controller and run the app. Returns a process exit code.

    Persistence resolution:

    * **``--recipes DIR`` given** → recipes live there, and the dial-preset + history files stay
      *adjacent* (``DIR/../xbloom-{slots,history}.json``) — the long-standing layout, kept so
      external drivers that point at their own generated recipe dir don't break.
    * **not given** → recipes come from the config (or the per-user data dir), and slots/history/
      token go to the per-user **state** dir. The saved machine address (config) is used when none
      is passed, so later launches skip the scan.
    """
    try:
        from .app import XBloomApp
    except ModuleNotFoundError as exc:  # textual not installed
        print(
            "The TUI needs extra packages. Install them with:\n"
            "  pip install 'xbloom-ble[tui]'\n"
            f"(missing: {exc.name})"
        )
        return 1

    from .. import config as cfgmod
    from .. import paths
    from .controller import FakeController, RealController
    from .history import HistoryStore
    from .slots import SlotStore
    from .store import RecipeStore

    cfg = cfgmod.load()
    if recipes_dir:
        store = RecipeStore(recipes_dir)
        slots = SlotStore(store.dir.parent / "xbloom-slots.json")
        history = HistoryStore(store.dir.parent / "xbloom-history.json")
    else:
        store = RecipeStore(cfg.resolved_recipes_dir)
        slots = SlotStore(paths.slots_file())
        history = HistoryStore(paths.history_file())
        address = address or cfg.address or None
    store.ensure()

    # auto_connect: explicit arg (e.g. --no-auto-connect) wins, else the saved config default.
    ac = cfg.auto_connect if auto_connect is None else auto_connect
    controller = FakeController(speed=0.2, auto_start=1.5) if demo else RealController(address)
    XBloomApp(store, controller, auto_brew=auto_brew, debug=debug,
              history=history, slots=slots, auto_connect=ac).run()
    return 0

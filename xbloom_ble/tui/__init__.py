"""Interactive terminal UI for xbloom-ble (optional extra: ``pip install xbloom-ble[tui]``).

``xbloom`` with no subcommand — or ``xbloom tui`` — launches it.
"""

from __future__ import annotations

DEFAULT_RECIPES_DIR = "~/.xbloom/recipes"


def run_tui(
    *,
    recipes_dir: str | None = None,
    address: str | None = None,
    demo: bool = False,
    auto_brew: bool = False,
) -> int:
    """Build the store + controller and run the app. Returns a process exit code."""
    try:
        from .app import XBloomApp
    except ModuleNotFoundError as exc:  # textual not installed
        print(
            "The TUI needs extra packages. Install them with:\n"
            "  pip install 'xbloom-ble[tui]'\n"
            f"(missing: {exc.name})"
        )
        return 1

    from .controller import FakeController, RealController
    from .store import RecipeStore

    store = RecipeStore(recipes_dir or DEFAULT_RECIPES_DIR)
    store.ensure()
    controller = FakeController(speed=0.2, auto_start=1.5) if demo else RealController(address)
    XBloomApp(store, controller, auto_brew=auto_brew).run()
    return 0

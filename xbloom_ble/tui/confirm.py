"""Brew confirmation gate — an explicit "yes, start it" step before hot water.

Starting a brew dispenses near-boiling water on the real machine, so it must never
happen on a stray keypress. Pressing brew opens this modal; the brew only launches
on an explicit confirm. Cancel is the *default* focus so a lingering Enter is safe.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..recipe import Recipe


class ConfirmBrewScreen(ModalScreen[bool]):
    """Ask the user to confirm starting a brew. Dismisses ``True`` to start."""

    DEFAULT_CSS = """
    ConfirmBrewScreen { align: center middle; }
    ConfirmBrewScreen > #dialog {
        width: 62; height: auto; padding: 1 2;
        background: $panel; border: heavy $warning;
    }
    ConfirmBrewScreen #cb-title { text-style: bold; color: $warning; }
    ConfirmBrewScreen #cb-body { margin: 1 0; }
    ConfirmBrewScreen #cb-buttons { height: auto; align-horizontal: center; }
    ConfirmBrewScreen Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("left,up", "focus_cancel", show=False),
        Binding("right,down", "focus_start", show=False),
        Binding("y", "start", "start", show=True),
        Binding("n,escape", "cancel", "cancel", show=True),
        # (enter presses the focused button — Button binds it natively)
    ]

    def __init__(self, recipe: Recipe) -> None:
        super().__init__()
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        r = self._recipe
        grind = "no-grind" if r.no_grind else f"grind {r.grind}"
        with Vertical(id="dialog"):
            yield Static("🔥 Start brew?", id="cb-title")
            yield Static(
                f"[b]{r.name}[/]\n"
                f"{r.dose_g} g · 1:{r.effective_ratio:g} · {grind} · {r.total_water_ml} ml\n\n"
                "[$warning]This dispenses hot water on the machine.[/] "
                "Beans in, cup/dripper in place?",
                id="cb-body",
            )
            with Horizontal(id="cb-buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("🔥 Start", variant="warning", id="start")

    def on_mount(self) -> None:
        # Default focus on Cancel: a stray Enter cancels, never starts.
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Enter/click on a button — Cancel or Start.
        self.dismiss(event.button.id == "start")

    def action_focus_cancel(self) -> None:
        self.query_one("#cancel", Button).focus()

    def action_focus_start(self) -> None:
        self.query_one("#start", Button).focus()

    def action_start(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

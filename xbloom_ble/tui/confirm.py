"""Brew confirmation gate — an explicit choice before hot water.

Pressing brew opens this modal. It offers two ways to brew (both stream the live
graph + save to history), plus cancel:

* **Load only** — stage the recipe; the machine arms and you approve *on the
  machine* to start. Safest — you're physically there.
* **Start** — stage *and* start remotely (commit + start); the machine brews
  without you touching it.

Dismisses ``"load"``, ``"start"``, or ``None`` (cancel). Cancel is the default
focus, so a stray Enter never brews.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..recipe import Recipe


class ConfirmBrewScreen(ModalScreen[str]):
    """Ask how to brew. Dismisses ``"load"``, ``"start"``, or ``None`` (cancel)."""

    DEFAULT_CSS = """
    ConfirmBrewScreen { align: center middle; }
    ConfirmBrewScreen > #dialog {
        width: 68; height: auto; max-height: 90%; padding: 1 2;
        background: $panel; border: heavy $warning;
    }
    ConfirmBrewScreen #cb-title { text-style: bold; color: $warning; }
    ConfirmBrewScreen #cb-detail { margin: 1 0 0 0; }
    ConfirmBrewScreen #cb-ask { margin: 1 0; }
    ConfirmBrewScreen #cb-buttons { height: auto; align-horizontal: center; }
    ConfirmBrewScreen Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("left,up", "nav_prev", show=False),
        Binding("right,down", "nav_next", show=False),
        Binding("l,o", "load", "load only", show=True),
        Binding("s,y", "start", "start", show=True),
        Binding("n,escape", "cancel", "cancel", show=True),
        # (enter presses the focused button — Button binds it natively)
    ]

    def __init__(self, recipe: Recipe) -> None:
        super().__init__()
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("🔥 Start brew?", id="cb-title")
            yield Static(self._detail(), id="cb-detail")
            yield Static(
                Text.assemble(
                    ("Beans in, cup/dripper in place? ", "bold #ffb300"),
                    ("Load only", "bold white"),
                    (" arms the machine (approve on it); ", "dim"),
                    ("Start", "bold white"),
                    (" brews remotely — both dispense hot water.", "dim"),
                ),
                id="cb-ask",
            )
            with Horizontal(id="cb-buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("Load only", variant="primary", id="load")
                yield Button("🔥 Start", variant="warning", id="start")

    def _detail(self) -> Text:
        """The recipe, so you can confirm what you're about to brew before hot water."""
        r = self._recipe
        t = Text()
        t.append(f"{r.name}\n", style="bold #d78700")
        grind = "no-grind" if r.no_grind else str(r.grind)
        water = r.water_ml or r.total_water_ml
        line = f"{r.dose_g} g · 1:{r.effective_ratio:g} · grind {grind} · {water} ml"
        if r.dripper:
            line += f" · {r.dripper}"
        t.append(line + "\n", style="dim")
        t.append(f"stage temps {r.stage_temps[0]:g} / {r.stage_temps[1]:g} °C\n", style="dim")
        t.append("\nPours\n", style="bold")
        for i, p in enumerate(r.pours, 1):
            label = p.label or f"Pour {i}"
            t.append(f" {i} ", style="cyan")
            t.append(f"{label:<8}", style="white")
            t.append(f"{p.ml:>3} ml  {p.temp_c}°  {p.pattern}", style="dim")
            extras = []
            if p.pause_s:
                extras.append(f"{p.pause_s}s")
            if p.agitation:
                extras.append("agit")
            if extras:
                t.append(f"  {' '.join(extras)}", style="yellow")
            t.append("\n")
        return t

    def on_mount(self) -> None:
        # Default focus on Cancel: a stray Enter cancels, never starts.
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Enter/click on a button: cancel → None, load → "load", start → "start".
        self.dismiss(None if event.button.id == "cancel" else event.button.id)

    def action_nav_prev(self) -> None:
        self.focus_previous()

    def action_nav_next(self) -> None:
        self.focus_next()

    def action_load(self) -> None:
        self.dismiss("load")

    def action_start(self) -> None:
        self.dismiss("start")

    def action_cancel(self) -> None:
        self.dismiss(None)

"""In-app help — a scrollable overlay with verbose instructions (press ``h``)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Getting around", [
        ("j / k  ·  ↓ / ↑", "move the cursor in the current list"),
        ("Tab / Shift+Tab", "switch tabs (Recipes → Brewing → History)"),
        ("Esc", "back to the Recipes tab (or close a dialog)"),
        ("l", "show/hide the activity-log panel on the right"),
        ("r", "reload recipe files from disk"),
        ("h  or  ?", "open this help  ·  q  quit"),
    ]),
    ("Recipes tab", [
        ("(highlight a recipe)", "the sidebar shows its full detail — dose, ratio, grind, "
                                 "water, the pour schedule and notes — and follows the cursor"),
        ("Enter  or  b", "brew the highlighted recipe (opens the confirm gate — see below)"),
        ("n", "create a new recipe in a validated form"),
        ("e", "edit the highlighted recipe"),
        ("d", "delete the highlighted recipe file"),
        ("/", "filter the list by name  ·  i  import a recipe from a URL/id"),
        ("1 / 2 / 3", "assign the highlighted recipe to dial slot A / B / C"),
        ("p", "push the three assigned slots to the machine (Auto-Mode presets)"),
    ]),
    ("Brewing — how a brew runs", [
        ("Enter / b", "opens the CONFIRM GATE for the highlighted recipe"),
        ("(gate) Load only", "key l — stage the recipe; the machine arms and you approve ON "
                             "THE MACHINE to start. Safest: you're physically there."),
        ("(gate) Start", "key s — stage AND start remotely (commit+start); the machine brews "
                         "without you touching it."),
        ("(gate) nav", "← / → move between the buttons; Enter presses the focused one; "
                       "n / Esc cancels. Cancel is focused by default."),
        ("⚠ hot water", "both Load-only and Start end in hot water — only brew with water in "
                        "the tank and a cup/dripper in place"),
        ("(brewing)", "either way the Brewing tab streams a live water/coffee graph and saves "
                      "to History; you can leave the tab (Tab/Esc) and the brew keeps running"),
        ("c", "cancel a brew in progress"),
    ]),
    ("History tab", [
        ("(select a brew)", "shows that brew's saved telemetry curve on the right"),
    ]),
    ("Recipes come from a folder", [
        ("--recipes DIR", "the TUI reads one-recipe-per-file YAML from this directory "
                          "(default ~/.xbloom/recipes). New/edited recipes are saved there."),
        ("--address / --demo", "point at a machine BLE address (or XBLOOM_ADDRESS); --demo runs "
                               "against a simulator with no hardware"),
    ]),
]


class HelpScreen(ModalScreen):
    """A scrollable help overlay. Any of h/?/esc/q closes it."""

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > #help-box {
        width: 84; max-width: 96%; height: auto; max-height: 90%;
        padding: 1 2; background: $panel; border: heavy $primary;
    }
    HelpScreen #help-body { height: auto; }
    """

    BINDINGS = [Binding("h,question_mark,escape,q", "close", "close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(self._text(), id="help-body")

    def _text(self) -> Text:
        t = Text()
        t.append("☕ xBloom — terminal UI help\n", style="bold #d78700")
        t.append("A keyboard-first cockpit for browsing recipes and driving the machine.\n",
                 style="dim")
        for title, rows in _SECTIONS:
            t.append(f"\n{title}\n", style="bold cyan")
            for keys, desc in rows:
                t.append(f"  {keys:<20}", style="bold white")
                t.append(f"  {desc}\n", style="grey85")
        t.append("\nPress ", style="dim")
        t.append("h", style="bold white")
        t.append(" / ", style="dim")
        t.append("Esc", style="bold white")
        t.append(" to close.", style="dim")
        return t

    def action_close(self) -> None:
        self.dismiss(None)

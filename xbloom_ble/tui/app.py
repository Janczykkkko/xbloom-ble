"""The xBloom TUI — a tabbed, k9s-style cockpit for the machine.

Top tabs (Recipes · Brewing · History) switched with Tab/Shift+Tab; a context+hotkey
header; a live activity-log side panel; slots and brew driven right from the recipe
list; the recipe editor as a modal with a discard guard. Runs against any
:class:`MachineController` — real hardware or the simulator (``--demo``).
"""

from __future__ import annotations

import asyncio
import logging
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import DataTable, Input, RichLog, Static, TabbedContent, TabPane
from textual_plotext import PlotextPlot

from .confirm import ConfirmBrewScreen
from .controller import MachineController
from .editor import EditorScreen
from .help import HelpScreen
from .history import HistoryPane, HistoryStore
from .slots import SLOTS, SlotStore
from .store import RecipeStore

LOGO = "☕ xBloom"
TABS = ["recipes", "brewing", "history"]

# Friendly (message, style) for the machine states shown during a brew.
_STATE_MSG = {
    "armed": ("● armed — approve on the machine ▶", "yellow"),
    "awaiting_confirm": ("● add beans + confirm on the machine ▶", "yellow"),
    "no_beans": ("⚠ no beans on the machine", "red"),
    "no_water": ("⚠ no water in the tank", "red"),
    "starting": ("● grinding / starting…", "cyan"),
    "brewing": ("● brewing…", "cyan"),
    "ready": ("✓ coffee ready — enjoy!", "green"),
    "complete": ("✓ complete", "green"),
    "idle": ("● idle", "dim"),
}

# After a remote start, if we NEVER get a real sign the brew began (start() saw
# nothing, and telemetry stays empty), cancel after this long rather than streaming
# forever. It must exceed the machine's SILENT grind+bloom (heartbeats only, ~20 s on
# real HW) — but once start() has seen a real 'starting'/'brewing' frame the guard is
# disarmed (see `saw_progress` in _brew), so this only bounds the truly-stuck case.
_GRIND_GUARD_S = 30.0
# Once the pours are done (water ≈ target) and the cup weight has not risen for this many
# seconds, the drawdown has finished (= the "coffee ready" beep) and the brew is complete.
# This machine only emits a definite 0x41 'done' on CUP-LIFT, so without this a hands-off brew
# would stream forever waiting for a cup that never lifts; the scale is the ground truth.
_DRAWDOWN_PLATEAU_S = 6.0


class _PanelLogHandler(logging.Handler):
    """Routes ``xbloom_ble`` log records into the TUI's activity panel.

    The BLE client logs via ``logging``; without this those records would hit
    stderr and paint over the Textual screen. We schedule the write onto the app's
    event loop (``call_soon_threadsafe`` works from the app thread *and* bleak's
    callback thread), so hardware chatter shows up in the activity log instead.
    """

    _STYLES = {logging.ERROR: "red", logging.WARNING: "yellow", logging.DEBUG: "dim"}

    def __init__(self, app: XBloomApp, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._app = app
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            style = self._STYLES.get(record.levelno, "cyan")
            self._loop.call_soon_threadsafe(self._app._log, msg, style)
        except Exception:  # logging must never crash the app
            pass


class Header(Horizontal):
    """k9s-style top chrome: context block (left) + hotkey grid (right)."""

    DEFAULT_CSS = """
    Header { height: 8; background: #000000; border-bottom: heavy $primary; padding: 0 1; }
    Header > #ctx  { width: 44; padding: 0 2; background: #000000; }
    Header > #keys { width: 1fr; padding: 0 2; background: #000000; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="ctx")
        yield Static(id="keys")

    def set_context(self, ctx: dict[str, str]) -> None:
        t = Text()
        t.append(f"{LOGO}\n\n", style="bold #d78700")
        for k, v in ctx.items():
            t.append(f"{k:<9}", style="dim")
            t.append(f"{v}\n")
        self.query_one("#ctx", Static).update(t)

    def set_keys(self, keys: list[tuple[str, str]]) -> None:
        t = Text("\n")
        for i, (k, label) in enumerate(keys):
            t.append(f" <{k}> ", style="bold cyan")
            t.append(f"{label:<13}", style="white")
            if i % 2:
                t.append("\n")
        self.query_one("#keys", Static).update(t)


class RecipesView(DataTable):
    """The recipe library as a dense table, with an assigned-SLOT column."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("SLOT", "NAME", "DOSE", "RATIO", "GRIND", "POURS", "WATER", "TYPE")

    def load(self, entries: list, assignments: dict[str, str] | None = None,
             filter_text: str = "") -> None:
        path_slot = {v: k for k, v in (assignments or {}).items() if v}
        self.clear()
        ft = filter_text.lower()
        for e in entries:
            if ft and ft not in e.name.lower():
                continue
            slot = path_slot.get(str(e.path), "")
            slot_cell = Text(f" {slot} ", style="bold black on cyan") if slot else ""
            r = e.recipe
            if r is None:
                self.add_row(slot_cell, Text(e.name, style="red"), "—", "—", "—", "—", "—",
                             Text("invalid", style="red"), key=str(e.path))
                continue
            typ = ("no-grind" if r.no_grind else "iced" if "iced" in e.name.lower() else "filter")
            typ_style = "magenta" if r.no_grind else "cyan" if typ == "iced" else "green"
            self.add_row(
                slot_cell, Text(r.name, style="bold"), f"{r.dose_g} g",
                f"1:{r.effective_ratio:g}", "—" if r.no_grind else str(r.grind),
                str(len(r.pours)), f"{r.total_water_ml} ml", Text(typ, style=typ_style),
                key=str(e.path),
            )


class RecipeDetail(VerticalScroll):
    """Detail sidebar for the highlighted recipe — the enriched, human-facing view."""

    # A read-only detail panel must NOT be a Tab stop — otherwise Textual's built-in
    # focus-movement swallows Tab before the app's tab-switch binding fires.
    can_focus = False

    def show(self, entry) -> None:
        body = self.query_one("#recipe-detail-body", Static)
        if entry is None:
            body.update(Text("no recipes — press <n> to create one", style="dim"))
            return
        r = entry.recipe
        if r is None:                       # broken file — show the parse error
            body.update(Text.assemble(
                (f"{entry.name}\n\n", "bold red"),
                ("invalid recipe\n", "red"), (entry.error or "", "dim")))
            return
        t = Text()
        t.append(f"{r.name}\n", style="bold #d78700")
        typ = "no-grind" if r.no_grind else "iced" if "iced" in entry.name.lower() else "filter"
        t.append(f"{typ}", style="cyan")
        if r.kind:
            t.append(f" · {r.kind}", style="dim")
        if r.dripper:
            t.append(f" · {r.dripper}", style="dim")
        t.append("\n\n")

        def row(k, v):
            t.append(f"{k:<8}", style="dim")
            t.append(f"{v}\n")

        row("dose", f"{r.dose_g} g")
        row("ratio", f"1:{r.effective_ratio:g}")
        row("grind", "— (no-grind)" if r.no_grind else str(r.grind))
        row("water", f"{r.water_ml or r.total_water_ml} ml")
        if r.hot_water_ml or r.ice_g:
            row("iced", f"{r.hot_water_ml or '?'} ml hot · {r.ice_g or '?'} g ice")
        if r.time:
            row("time", r.time)
        row("temps", f"{r.stage_temps[0]:g} / {r.stage_temps[1]:g} °C")

        t.append("\nPours\n", style="bold")
        for i, p in enumerate(r.pours, 1):
            label = p.label or f"Pour {i}"
            t.append(f" {i} ", style="cyan")
            t.append(f"{label:<8}", style="white")
            t.append(f"{p.ml:>3}ml {p.temp_c}° {p.pattern}", style="dim")
            extras = []
            if p.pause_s:
                extras.append(f"{p.pause_s}s")
            if p.agitation:
                extras.append("agit")
            if extras:
                t.append(f"  {' '.join(extras)}", style="yellow")
            t.append("\n")

        if r.note:
            t.append("\nNote\n", style="bold")
            t.append(r.note, style="italic #b0b0b0")
        body.update(t)

    def compose(self) -> ComposeResult:
        yield Static("", id="recipe-detail-body")


class BrewView(Container):
    """Live brew: status line + water/coffee graph."""

    def compose(self) -> ComposeResult:
        yield Static("Pick a recipe and press Enter to brew.", id="brew-status")
        yield PlotextPlot(id="plot")


class XBloomApp(App):
    """The cockpit."""

    TITLE = "xBloom"
    CSS = """
    Screen { background: #000000; }
    #body-row { height: 1fr; }
    #tabs { width: 1fr; background: #000000; }
    Tabs { background: #050505; }
    #logpanel { width: 46; height: 1fr; background: #050505; border-left: heavy $primary;
                padding: 0 1; color: $text-muted; }
    #logpanel.hidden { display: none; }
    #cmd { display: none; dock: bottom; border: tall $accent; background: #000000; }
    #cmd.show { display: block; }
    #crumbs { dock: bottom; height: 1; background: $primary; color: $text; padding: 0 1; }
    DataTable { height: 1fr; background: #000000; }
    DataTable > .datatable--header { background: #101010; text-style: bold; }
    #recipes-row { height: 1fr; }
    #recipes-row > #recipes-table { width: 1fr; }
    #recipe-detail { width: 46; background: #050505; border-left: heavy $primary; padding: 0 1; }
    #recipe-detail-body { padding: 1 0; }
    #brew-status { height: 3; padding: 1 1; background: #000000; }
    #plot { height: 1fr; border: round $panel; margin: 0 1 1 1; background: #000000; }
    """

    BINDINGS = [
        Binding("tab", "next_tab", "tab →", show=False, priority=True),
        Binding("shift+tab", "prev_tab", "← tab", show=False, priority=True),
        Binding("slash", "filter", "filter", key_display="/", show=False),
        Binding("i", "import", "import", show=False),
        Binding("b", "brew", "brew"),
        Binding("c", "cancel", "cancel"),
        Binding("e", "edit", "edit"),
        Binding("n", "new", "new"),
        Binding("C", "clone", "clone", show=False),
        Binding("d", "delete", "delete"),
        Binding("1", "assign_a", "→A", show=False),
        Binding("2", "assign_b", "→B", show=False),
        Binding("3", "assign_c", "→C", show=False),
        Binding("p", "push_slots", "push", show=False),
        Binding("escape", "back", "back", show=False),
        Binding("j", "cursor_down", "down", show=False),
        Binding("k", "cursor_up", "up", show=False),
        Binding("r", "reload", "reload"),
        Binding("l", "toggle_log", "log"),
        Binding("h", "help", "help"),
        Binding("question_mark", "help", "help", show=False),
        Binding("q", "quit", "quit"),
    ]

    TAB_TITLES = {"recipes": "🫘 recipes", "brewing": "▶ brewing", "history": "🕘 history"}

    def __init__(
        self, store: RecipeStore, controller: MachineController, *,
        auto_brew: bool = False, history: HistoryStore | None = None,
        slots: SlotStore | None = None, debug: bool = False,
    ) -> None:
        super().__init__()
        self.store = store
        self.controller = controller
        self.auto_brew = auto_brew
        self._debug = debug
        self.history = history or HistoryStore(store.dir.parent / "xbloom-history.json")
        self.slots = slots or SlotStore(store.dir.parent / "xbloom-slots.json")
        self._entries: list = []
        self._current = None
        self._mode = ""            # "" | "filter" | "import"
        self._filter = ""
        self._brewing = False
        self._last_message = ""    # last toast/notice (handy for tests)
        self._last_log = ""        # last activity-log line (handy for tests)
        self._t: list[float] = []
        self._water: list[float] = []
        self._coffee: list[float] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body-row"):
            with TabbedContent(id="tabs"):
                with TabPane("🫘 Recipes", id="recipes"):
                    with Horizontal(id="recipes-row"):
                        yield RecipesView(id="recipes-table")
                        yield RecipeDetail(id="recipe-detail")
                with TabPane("▶ Brewing", id="brewing"):
                    yield BrewView(id="brew")
                with TabPane("🕘 History", id="history"):
                    yield HistoryPane(id="history-pane")
            # min_width small so lines shrink to the panel width and WRAP (the default
            # 78 renders wide and the narrow panel just clips it instead of wrapping).
            yield RichLog(id="logpanel", markup=True, wrap=True, min_width=12, max_lines=500)
        yield Static("", id="crumbs")
        yield Input(placeholder="", id="cmd")

    def on_mount(self) -> None:
        self._attach_log_capture()
        self.query_one("#logpanel", RichLog).write(Text("● activity log", style="bold cyan"))
        self._reload()
        self._log(f"loaded {len(self._entries)} recipes from {self.store.dir}", "green")
        self._sync_chrome()
        self.query_one(RecipesView).focus()
        if self.auto_brew:
            self.set_timer(0.4, self.action_brew)

    def _attach_log_capture(self) -> None:
        """Redirect the xbloom_ble logger into the activity panel (off the raw screen).

        With ``debug`` on, also lower the level to DEBUG and tee the full BLE chatter
        to a timestamped file so a session can be captured and shared.
        """
        # Keep the BlueZ/D-Bus stack's DEBUG chatter off the screen — we only capture
        # our own frames (otherwise --debug floods the UI with dbus signals).
        for noisy in ("bleak", "bleak.backends", "dbus_fast", "dbus_next"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        lg = logging.getLogger("xbloom_ble")
        self._saved_log = (lg.handlers[:], lg.level, lg.propagate)
        handlers: list[logging.Handler] = [_PanelLogHandler(self, asyncio.get_running_loop())]
        if self._debug:
            from pathlib import Path
            path = Path.cwd() / f"xbloom-debug-{time.strftime('%Y%m%d-%H%M%S')}.log"
            fh = logging.FileHandler(path, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s",
                                              datefmt="%H:%M:%S"))
            handlers.append(fh)
            self._log(f"🐛 BLE debug log → {path}", "magenta")
        lg.handlers = handlers
        lg.setLevel(logging.DEBUG if self._debug else logging.INFO)
        lg.propagate = False        # don't let records reach the root/stderr handler

    def on_unmount(self) -> None:
        saved = getattr(self, "_saved_log", None)
        if saved:
            lg = logging.getLogger("xbloom_ble")
            lg.handlers, lg.level, lg.propagate = saved

    # ── helpers ────────────────────────────────────────────────────
    @property
    def _view(self) -> str:
        try:
            return self.query_one(TabbedContent).active
        except Exception:
            return "recipes"

    def _set_view(self, view: str) -> None:
        self.query_one(TabbedContent).active = view

    def _log(self, msg: str, style: str = "white") -> None:
        self._last_log = msg
        try:
            stamp = time.strftime("%H:%M:%S")
            self.query_one("#logpanel", RichLog).write(
                Text.assemble((f"{stamp} ", "dim"), (msg, style))
            )
        except Exception:
            pass

    def _notice(self, message: str) -> None:
        self._last_message = message
        try:
            self.notify(message)
        except Exception:
            pass

    def action_toggle_log(self) -> None:
        self.query_one("#logpanel", RichLog).toggle_class("hidden")

    def action_help(self) -> None:
        if not self._modal_open():
            self.push_screen(HelpScreen())

    # ── tabs ───────────────────────────────────────────────────────
    def _modal_open(self) -> bool:
        """True when a modal (editor / confirm) is on top — don't steal its keys."""
        return len(self.screen_stack) > 1

    def action_next_tab(self) -> None:
        if self._modal_open():          # priority binding — let the modal keep Tab
            return
        i = TABS.index(self._view) if self._view in TABS else 0
        self._set_view(TABS[(i + 1) % len(TABS)])

    def action_prev_tab(self) -> None:
        if self._modal_open():
            return
        i = TABS.index(self._view) if self._view in TABS else 0
        self._set_view(TABS[(i - 1) % len(TABS)])

    def action_back(self) -> None:
        if self._view != "recipes":
            self._set_view("recipes")

    def on_tabbed_content_tab_activated(self, event) -> None:
        if self._view == "history":
            pane = self.query_one(HistoryPane)
            pane.load(self.history.list())
            from .history import HistoryList
            try:                                    # focus the list so j/k AND arrows drive it
                pane.query_one(HistoryList).focus()
            except Exception:                       # noqa: BLE001
                pass
        self._sync_chrome()
        if self._view == "recipes":
            self.query_one(RecipesView).focus()

    # ── chrome ─────────────────────────────────────────────────────
    def _sync_chrome(self) -> None:
        if not self.is_running:          # the app may be tearing down mid-brew
            return
        try:
            self._render_chrome()
        except Exception:                # a widget vanished during teardown — harmless
            pass

    def _render_chrome(self) -> None:
        conn = "● connected" if getattr(self.controller, "address", None) else "○ idle"
        brew = "brewing…" if self._brewing else "idle"
        self.query_one(Header).set_context({
            "Machine": conn,
            "Recipes": str(len(self._entries)),
            "Brew": brew,
            "Tab": self._view,
        })
        keys = {
            "recipes": [
                ("enter", "brew ▶"), ("e", "edit"), ("n", "new"), ("C", "clone"),
                ("1/2/3", "→ slot"),
                ("p", "push slots"), ("/", "filter"), ("i", "import"), ("d", "delete"),
                ("tab", "next tab"), ("h", "help"), ("q", "quit"),
            ],
            "brewing": [
                ("c", "cancel"), ("esc", "recipes"), ("tab", "next tab"),
                ("l", "log"), ("h", "help"), ("q", "quit"),
            ],
            "history": [
                ("j/k", "select"), ("esc", "recipes"), ("tab", "next tab"),
                ("l", "log"), ("h", "help"), ("q", "quit"),
            ],
        }.get(self._view, [])
        self.query_one(Header).set_keys(keys)
        crumb = Text()
        crumb.append(" xbloom ", style="bold black on cyan")
        crumb.append(f" {self.TAB_TITLES.get(self._view, self._view)} ", style="black on white")
        if self._current is not None and self._view == "brewing":
            crumb.append(f" {self._current.name} ", style="cyan")
        if self._filter:
            crumb.append(f"  /{self._filter}", style="yellow")
        self.query_one("#crumbs", Static).update(crumb)

    # ── recipes ────────────────────────────────────────────────────
    def _reload(self) -> None:
        self._entries = self.store.list()
        self.query_one(RecipesView).load(self._entries, self.slots.get(), self._filter)
        if self._entries and self._current is None:
            self._current = next((e.recipe for e in self._entries if e.ok), None)
        self._refresh_detail(self._entries[0] if self._entries else None)
        self._sync_chrome()

    def _entry_for_row(self, key: str):
        return next((e for e in self._entries if str(e.path) == key), None)

    def _current_entry(self):
        if not self._entries:
            return None
        try:
            row = self.query_one(RecipesView).cursor_row
        except Exception:
            return None
        # filtered rows: map the visible cursor back to the entry by row key
        try:
            key = self.query_one(RecipesView).coordinate_to_cell_key((row, 0)).row_key.value
            return self._entry_for_row(str(key))
        except Exception:
            return self._entries[row] if row is not None and 0 <= row < len(self._entries) else None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if getattr(event.data_table, "id", None) != "recipes-table":
            return
        entry = self._entry_for_row(str(event.row_key.value))
        if entry and entry.ok and not self._brewing:
            self._current = entry.recipe
        self._refresh_detail(entry)

    def _refresh_detail(self, entry=None) -> None:
        """Update the Recipes-tab detail sidebar for the given (or current) entry."""
        try:
            self.query_one(RecipeDetail).show(entry if entry is not None else self._current_entry())
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on a recipe row (recipes tab) → brew it.
        if self._view != "recipes":
            return
        entry = self._entry_for_row(str(event.row_key.value))
        if entry is None:
            return
        if not entry.ok:
            self._notice(f"“{entry.name}” has errors — can't brew it")
            return
        self._current = entry.recipe
        self.action_brew()

    # ── filter / import prompt ─────────────────────────────────────
    def action_filter(self) -> None:
        self._open_input("filter")

    def action_import(self) -> None:
        self._open_input("import")

    def _open_input(self, mode: str) -> None:
        self._mode = mode
        inp = self.query_one("#cmd", Input)
        inp.add_class("show")
        inp.placeholder = ("filter recipes…" if mode == "filter"
                           else "paste an xBloom share URL / id…")
        inp.value = self._filter if mode == "filter" else ""
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if self._mode == "filter":
            self._filter = val
            self.query_one(RecipesView).load(self._entries, self.slots.get(), self._filter)
            self._sync_chrome()
        elif self._mode == "import" and val:
            self.run_worker(self._import_recipe(val), name="import")
        self._close_input()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._mode == "filter":
            self.query_one(RecipesView).load(self._entries, self.slots.get(), event.value.strip())

    def _close_input(self) -> None:
        self._mode = ""
        inp = self.query_one("#cmd", Input)
        inp.remove_class("show")
        inp.value = ""
        if self._view == "recipes":
            self.query_one(RecipesView).focus()

    def on_key(self, event) -> None:
        if event.key == "escape" and self._mode:
            self._close_input()
            event.stop()

    async def _import_recipe(self, url: str) -> None:
        self._log(f"importing {url}…", "cyan")
        try:
            from ..cloud import XBloomCloud, recipe_from_cloud
            payload = await asyncio.to_thread(XBloomCloud().fetch_public, url)
            recipe = recipe_from_cloud(payload)
            self.store.save(recipe)
            self._current = None
            self._reload()
            self._log(f"✓ imported “{recipe.name}”", "green")
            self._notice(f"imported “{recipe.name}”")
        except Exception as exc:  # noqa: BLE001
            self._log(f"import failed: {exc}", "red")
            self._notice(f"import failed: {exc}")

    # ── editor (modal) ─────────────────────────────────────────────
    def action_edit(self) -> None:
        entry = self._current_entry()
        recipe = entry.recipe if entry and entry.ok else None
        path = entry.path if entry else None
        self._log(f"editing {recipe.name if recipe else '(new)'}")
        self.push_screen(EditorScreen(self.store, recipe, path), self._editor_done)

    def action_new(self) -> None:
        self._log("new recipe")
        self.push_screen(EditorScreen(self.store, None, None), self._editor_done)

    def action_clone(self) -> None:
        entry = self._current_entry()
        recipe = entry.recipe if entry and entry.ok else None
        if recipe is None:
            self._notice("nothing to clone here")
            return
        self._log(f"cloning {recipe.name}")
        self.push_screen(EditorScreen(self.store, recipe, None, clone=True), self._editor_done)

    def _editor_done(self, saved: bool | None) -> None:
        if saved:
            self._current = None
            self._reload()
            self._log("recipe saved", "green")
            self._notice("recipe saved")

    def action_delete(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        self.store.delete(entry.path)
        self._current = None
        self._reload()
        self._log(f"deleted {entry.name}", "yellow")
        self._notice(f"deleted “{entry.name}”")

    def action_reload(self) -> None:
        if not self._brewing:
            self._reload()

    # ── slots (from the recipe list) ───────────────────────────────
    def _assign_slot(self, slot: str) -> None:
        if self._view != "recipes":
            return
        entry = self._current_entry()
        if entry is None or not entry.ok:
            self._notice("highlight a valid recipe first")
            return
        self.slots.assign(slot, entry.path)
        self._log(f"slot {slot} ← {entry.name}", "green")
        self._notice(f"slot {slot} = {entry.name}")
        self._reload()

    def action_assign_a(self) -> None:
        self._assign_slot("A")

    def action_assign_b(self) -> None:
        self._assign_slot("B")

    def action_assign_c(self) -> None:
        self._assign_slot("C")

    def action_push_slots(self) -> None:
        if self._view != "recipes":
            return
        assignments = self.slots.get()
        recipes = []
        for s in SLOTS:
            entry = self._entry_for_row(assignments.get(s, ""))
            if entry is None or not entry.ok:
                self._notice(f"slot {s} is empty — assign all three (1/2/3) first")
                return
            recipes.append(entry.recipe)
        self.run_worker(self._push_slots(recipes), name="push")

    async def _push_slots(self, recipes) -> None:
        self._log("pushing slots A/B/C → machine…", "cyan")
        try:
            await self.controller.connect()
            self._sync_chrome()
            await self.controller.save_slots(recipes)
            self._log("✓ slots A/B/C programmed", "green")
            self._notice("slots A/B/C programmed")
        except Exception as exc:  # noqa: BLE001
            self._log(f"slot push failed: {exc}", "red")
            self._notice(f"slot push failed: {exc}")
        finally:
            try:
                await self.controller.disconnect()
            except Exception:
                pass

    # ── navigation ─────────────────────────────────────────────────
    def action_cursor_down(self) -> None:
        table = self.query_one(RecipesView) if self._view == "recipes" else None
        if self._view == "history":
            from .history import HistoryList
            hist = self.query_one(HistoryList)
            hist.action_cursor_down()
            self.query_one(HistoryPane).show_detail(hist.cursor_row)
            return
        if table:
            table.action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._view == "history":
            from .history import HistoryList
            hist = self.query_one(HistoryList)
            hist.action_cursor_up()
            self.query_one(HistoryPane).show_detail(hist.cursor_row)
            return
        if self._view == "recipes":
            self.query_one(RecipesView).action_cursor_up()

    # ── brewing ────────────────────────────────────────────────────
    def action_brew(self) -> None:
        if self._current is None:
            self._current = next((e.recipe for e in self._entries if e.ok), None)
        if self._current is None or self._brewing:
            return
        # Gate: starting a brew dispenses hot water — require an explicit confirm.
        self.push_screen(ConfirmBrewScreen(self._current), self._brew_confirmed)

    def _brew_confirmed(self, mode: str | None) -> None:
        # mode: "start" (remote commit+start), "load" (arm; approve on machine), None (cancel)
        if mode not in ("start", "load") or self._brewing or self._current is None:
            if mode is None:
                self._log("brew cancelled", "yellow")
            return
        self._set_view("brewing")
        self._t, self._water, self._coffee = [], [], []
        self.run_worker(self._brew(remote_start=mode == "start"), exclusive=True, name="brew")

    def action_cancel(self) -> None:
        if self._brewing:
            self._log("cancelling brew… (0x47)", "yellow")
            self._brew_status("[yellow]● cancelling… (waiting for the machine to stop)[/]")
            self.run_worker(self.controller.cancel(), name="cancel")

    async def _abort_supply(self, state_name: str, head: str) -> None:
        """Machine refused for no beans/water — cancel it back to idle and message."""
        what = "beans" if state_name == "no_beans" else "water"
        self._log(f"✗ no {what} — aborting; add {what} and brew again", "red")
        self._brew_status(f"{head}\n[red]✗ no {what} — brew aborted "
                          f"(add {what}, then brew again)[/]")
        try:
            await self.controller.cancel()
        except Exception:  # noqa: BLE001
            pass

    def _brew_status(self, text: str) -> None:
        if not self.is_running:
            return
        try:
            self.query_one("#brew-status", Static).update(text)
        except Exception:
            pass

    def _replot(self) -> None:
        if not self.is_running:
            return
        try:
            plot = self.query_one("#plot", PlotextPlot)
        except Exception:
            return
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        plt.title("water / coffee (g) vs time (s)")
        if self._t:
            plt.plot(self._t, self._water, label="water", color="cyan+", marker="braille")
            if any(self._coffee):
                plt.plot(self._t, self._coffee, label="coffee", color="orange", marker="braille")
        if self._current is not None:
            plt.horizontal_line(self._current.total_water_ml, color="gray")
        plot.refresh()

    async def _brew(self, *, remote_start: bool) -> None:
        r = self._current
        self._brewing = True
        self._sync_chrome()
        grind = "no-grind" if r.no_grind else f"grind {r.grind}"
        head = (f"[b]{r.name}[/]  {r.dose_g} g · 1:{r.effective_ratio:g} · "
                f"{grind} · {r.total_water_ml} ml")
        try:
            self._brew_status(f"{head}\n[dim]connecting…[/]")
            self._log(f"brew {r.name} — connecting…")
            await self.controller.connect()
            self._brew_status(f"{head}\n[dim]staging…[/]")
            self._log(f"staging {r.name} ({grind}, 1:{r.effective_ratio:g})")
            await self.controller.stage(r)
            started = None
            if remote_start:
                self._brew_status(f"{head}\n[yellow]● armed — starting…[/]")
                self._log("armed — starting brew ▶", "yellow")
                started = await self.controller.start()
                # start() returns the state the machine acted with. If it refused
                # immediately (no water/beans), the telemetry stream won't repeat that
                # event (start() consumed it) — so handle it here.
                if started is not None and started.state_name in ("no_beans", "no_water"):
                    await self._abort_supply(started.state_name, head)
                    return
                self._brew_status(f"{head}\n[cyan]● brewing…[/]")
                self._log("brew started ▶", "cyan")
            else:
                # Load-only: the machine is armed; the human approves ON THE MACHINE.
                # Telemetry keeps streaming, so the graph + history fill in either way.
                self._brew_status(f"{head}\n[yellow]● armed — approve on the machine ▶[/]")
                self._log("armed — approve on the machine to start ▶", "yellow")
            t0 = time.monotonic()
            last_state = None
            brew_began = remote_start   # a remote start means the brew is already underway
            # ⚠️ start() CONSUMES the machine's post-commit status frame, and on real
            # hardware the machine then goes SILENT (heartbeats only) all through the
            # grind + first bloom — often >20 s before the next status frame (the pour).
            # So seed "saw real progress" from what start() actually observed; otherwise
            # the 20 s guard below never sees a status in time and cancels a real brew
            # mid-grind. Only a genuine machine frame (raw != b"") counts — the synthetic
            # fallback start() returns when it saw nothing must NOT disarm the guard.
            saw_progress = bool(
                started is not None and started.raw
                and started.state_name in ("starting", "brewing")
            )
            completed = False           # the brew reached a natural end (record history)
            last_water = last_coffee = 0.0   # running scale values (frames are separate)
            last_replot = 0.0
            # measured-completion state: target water (dose×ratio) + drawdown-plateau tracking.
            target_water = (r.dose_g or 0) * (r.effective_ratio or 0)
            coffee_peak = 0.0
            last_rise = t0
            async for ev in self.controller.telemetry():
                if not self.is_running:
                    break
                # Live-scale frames (0x4b water / 0x15 coffee) arrive separately, ~10x/s.
                # Pair them into one running point for the graph; a real reading means the
                # brew is genuinely pouring (progress). Handle + skip the rest of the loop.
                if ev.water_g is not None or ev.coffee_g is not None:
                    if ev.water_g is not None:
                        last_water = ev.water_g
                    if ev.coffee_g is not None:
                        last_coffee = ev.coffee_g
                    saw_progress = True
                    now = time.monotonic()
                    self._t.append(round(now - t0, 1))
                    self._water.append(last_water)
                    self._coffee.append(last_coffee)
                    if now - last_replot > 0.4:   # throttle: the stream is ~10 Hz
                        self._replot()
                        last_replot = now
                    # Measured completion: this machine's only definite 'done' (0x41) fires on
                    # CUP-LIFT, so a hands-off brew would hang. Instead — once the pours are done
                    # (water ≈ target) AND the cup weight has stopped rising for a few seconds
                    # (drip finished = the beep), the brew is over. Ground truth from the scale.
                    if last_coffee > coffee_peak + 0.3:
                        coffee_peak = last_coffee
                        last_rise = now
                    if (target_water > 0 and last_water >= 0.85 * target_water
                            and last_coffee >= 0.5 * (r.dose_g or 0)
                            and now - last_rise >= _DRAWDOWN_PLATEAU_S):
                        completed = True
                        break
                # A real scale frame (state_name "scale") carries no state — done. The
                # sim emits state + weights in one event, so only skip pure scale frames.
                if ev.state_name == "scale":
                    continue
                if ev.state_name in ("awaiting_confirm", "starting", "brewing",
                                     "no_beans", "no_water"):
                    brew_began = True
                if ev.state_name in ("starting", "brewing"):
                    # 'starting' (0x22) = the machine is grinding/spinning up on our
                    # commit — real progress. Counting it here keeps the 20 s "didn't
                    # start" guard below from cancelling a legitimate brew that spends
                    # its first seconds grinding + blooming before the first 0x3b.
                    saw_progress = True
                # Track state transitions ALWAYS — so we log the brew, show a friendly
                # status, and exit on complete instead of spinning.
                if ev.state_name != last_state:
                    last_state = ev.state_name
                    msg, style = _STATE_MSG.get(ev.state_name, (f"● {ev.state_name}", "cyan"))
                    self._log(msg, style)
                    self._brew_status(f"{head}\n[{style}]{msg}[/]")
                if ev.state_name in ("no_beans", "no_water"):
                    await self._abort_supply(ev.state_name, head)
                    break
                if ev.state_name == "ready" and saw_progress:
                    # 0x24 = the "coffee ready" beep — the brew is done while the cup is
                    # still on the scale. Complete here so we DON'T wait for the machine to
                    # return to idle (which only happens once the cup is lifted).
                    completed = True
                    break
                if ev.state_name == "ack_0x41" and saw_progress:
                    # 0x41 = the machine's 'done' echo — on THIS machine it only arrives on
                    # CUP-LIFT (or after), so the drawdown-plateau above usually completes first.
                    # Kept as an immediate trigger for when the cup IS lifted right at the beep.
                    completed = True
                    break
                if ev.state_name in ("complete", "cancelled"):
                    completed = ev.state_name == "complete"
                    break
                if ev.state_name == "idle" and brew_began:
                    # This machine has NO distinct 'complete' status — it signals brew-END
                    # by returning to idle (0x01). If we actually saw the brew run, that's a
                    # normal finish (record history); an early idle without progress is not.
                    completed = saw_progress
                    break
                if remote_start and not saw_progress and (time.monotonic() - t0) > _GRIND_GUARD_S:
                    # We commanded a remote start but the machine never actually brewed
                    # (it reverts to armed ~1-2s later when there are no beans / it isn't
                    # ready). Stop instead of streaming forever; nudge to check the setup.
                    self._log("brew didn't start — beans + water + cup in? (stopping)", "yellow")
                    self._brew_status(f"{head}\n[yellow]⚠ brew didn't start — is there "
                                      "beans + water + a cup? then try again[/]")
                    try:
                        await self.controller.cancel()
                    except Exception:  # noqa: BLE001
                        pass
                    break
            self._replot()   # final render so the last (throttled) segment shows
            if completed:
                # Peak, not last: the machine zeroes its water scale at the very end.
                final_water = max(self._water) if self._water else 0.0
                final_coffee = max(self._coffee) if self._coffee else 0.0
                suffix = (f" — {final_water:g} g water · {final_coffee:g} g in cup"
                          if self._water else " (brew ran; no live weights captured)")
                self._log(f"✓ brew complete{suffix}", "green")
                self.history.record(
                    recipe=r.name, dose_g=r.dose_g, water_g=final_water,
                    ratio=r.effective_ratio,
                    duration_s=self._t[-1] if self._t else round(time.monotonic() - t0, 1),
                    telemetry={"t": list(self._t), "water": list(self._water),
                               "coffee": list(self._coffee)},
                )
        except Exception as exc:  # noqa: BLE001
            self._brew_status(f"{head}\n[red]● error: {exc}[/]")
            self._log(f"error: {exc}", "red")
        finally:
            self._brewing = False
            self._sync_chrome()
            try:
                await self.controller.disconnect()
            except Exception:
                pass

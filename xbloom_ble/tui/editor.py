"""Recipe editor — a validated form for creating/editing a recipe.

Top fields (name/dose/grind/ratio/temps) + a pour list you can add to and remove
from. Validates live against :class:`~xbloom_ble.recipe.Recipe` and shows the error
inline; Save is blocked until the recipe is valid.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label, Static

from ..recipe import Recipe, RecipeError
from .store import RecipeStore


class ClickCell(Static):
    """A slim, clickable 'button' rendered as a ``Static``.

    A real Textual ``Button`` won't show its label in a height-1 row (it needs ~3 rows
    and renders as a blank coloured bar), so all the editor's actions are these instead:
    they render their text on a single row. The ``id`` names the action (``add`` /
    ``save`` / ``cancel`` / a pour's ``remove``); :meth:`EditorView._activate` routes it.
    """

    def on_click(self) -> None:
        try:
            self.screen.query_one(EditorView)._activate(self)
        except Exception:
            pass


class AgitCell(Static):
    """A slim per-pour agitation toggle (``✓`` = on).

    Agitation is on/off in the protocol — the machine agitates *during* that pour (the
    spiral motion); there is no separate pre-/post-pour timing to send, so this is a
    plain toggle. A ``Static`` (not a ``Checkbox``) so it renders cleanly in a height-1
    row — a Checkbox's switch glyph gets clipped there.
    """

    def __init__(self, on: bool = False, **kwargs) -> None:
        self._on = bool(on)
        super().__init__(self._glyph(), **kwargs)   # initial content (never None → renders)

    def _glyph(self) -> str:
        return "[green]✓[/]" if self._on else "[dim]·[/]"

    @property
    def value(self) -> bool:
        return self._on

    def toggle(self) -> None:
        self._on = not self._on
        self.update(self._glyph())

    def on_click(self) -> None:
        self.toggle()
        try:
            ev = self.screen.query_one(EditorView)
            ev._mark_dirty()
            ev._revalidate()
        except Exception:
            pass


class PatternCell(Static):
    """Slim pattern selector — cycles the three valid pour patterns on click / Enter
    (spiral → ring → center). There are only three values and a real dropdown overlay
    fights the height-1 rows, so a compact cycler fits the editor's slim grid.
    """

    PATTERNS = ("spiral", "ring", "center")

    def __init__(self, value: str = "spiral", **kwargs) -> None:
        self._val = value if value in self.PATTERNS else "spiral"
        super().__init__(self._val, **kwargs)

    @property
    def value(self) -> str:
        return self._val

    def cycle(self) -> None:
        self._val = self.PATTERNS[(self.PATTERNS.index(self._val) + 1) % len(self.PATTERNS)]
        self.update(self._val)

    def on_click(self) -> None:
        self.cycle()
        try:
            ev = self.screen.query_one(EditorView)
            ev._mark_dirty()
            ev._revalidate()
        except Exception:
            pass


class PourRow(Horizontal):
    """One editable pour: ml · temp · pattern · pause · rpm · flow · agitate · ✕."""

    DEFAULT_CSS = """
    PourRow { height: 1; }
    PourRow > .plabel { width: 3; content-align: right middle; color: $text-muted; }
    PourRow Input {
        width: 8; height: 1; border: none; background: #101010; padding: 0 1; margin-right: 1;
    }
    PourRow Input:focus { background: #16213e; }
    PourRow #pattern {
        width: 11; height: 1; background: #101010; content-align: left middle;
        padding: 0 1; margin-right: 1;
    }
    PourRow #pattern:hover { background: #16213e; }
    PourRow #agitation { width: 9; height: 1; content-align: left middle; padding-left: 1; }
    PourRow #remove { width: 4; height: 1; color: $error; content-align: center middle; }
    PourRow #remove:hover { background: $error 30%; text-style: bold; }
    PourRow .navsel { background: $accent; text-style: bold; }
    """

    def __init__(self, idx: int, pour: dict | None = None) -> None:
        super().__init__(classes="pour-row")
        p = pour or {}
        self._idx = idx
        self._init = {
            "ml": str(p.get("ml", 40)),
            "temp_c": str(p.get("temp_c", 92)),
            "pattern": p.get("pattern", "spiral"),
            "pause_s": str(p.get("pause_s", 5)),
            "rpm": str(p.get("rpm", 100)),
            "flow_ml_s": str(p.get("flow_ml_s", 3.0)),
            "agitation": bool(p.get("agitation", False)),
        }

    def compose(self) -> ComposeResult:
        yield Label(f"{self._idx}", classes="plabel")
        yield Input(self._init["ml"], id="ml", placeholder="ml", type="integer")
        yield Input(self._init["temp_c"], id="temp_c", placeholder="°C", type="integer")
        yield PatternCell(self._init["pattern"], id="pattern")
        yield Input(self._init["pause_s"], id="pause_s", placeholder="pause", type="integer")
        yield Input(self._init["rpm"], id="rpm", placeholder="rpm", type="integer")
        yield Input(self._init["flow_ml_s"], id="flow_ml_s", placeholder="flow")
        yield AgitCell(self._init["agitation"], id="agitation")   # ✓ = agitate this pour
        yield ClickCell("✕", id="remove")

    def value(self) -> dict:
        def num(wid, cast, default):
            try:
                return cast(self.query_one(f"#{wid}", Input).value)
            except Exception:
                return default
        return {
            "ml": num("ml", int, 0),
            "temp_c": num("temp_c", int, 92),
            "pattern": self._patt(),
            "pause_s": num("pause_s", int, 5),
            "rpm": num("rpm", int, 100),
            "flow_ml_s": num("flow_ml_s", float, 3.0),
            "agitation": self._agit(),
        }

    def _patt(self) -> str:
        try:
            return self.query_one("#pattern", PatternCell).value
        except Exception:
            return "spiral"

    def _agit(self) -> bool:
        try:
            return self.query_one("#agitation", AgitCell).value
        except Exception:
            return False


class EditorView(VerticalScroll):
    """Create/edit a recipe. ``on_done(saved: bool)`` is called on save or cancel."""

    DEFAULT_CSS = """
    EditorView { padding: 1 2; background: #000000; }
    EditorView #etitle { text-style: bold; color: $primary; padding: 0 0 1 0; }
    EditorView .field { height: 1; margin-bottom: 1; }
    EditorView .flabel { width: 14; content-align: left middle; color: $text-muted; }
    EditorView Input {
        height: 1; border: none; background: #101010; padding: 0 1;
    }
    EditorView Input:focus { background: #16213e; }
    EditorView Input.-invalid { background: #2a1010; }
    EditorView #name { width: 40; }
    EditorView .num { width: 8; margin-right: 2; }
    EditorView Checkbox { height: 1; border: none; background: transparent; padding: 0; }
    /* Slim clickable 'buttons' (Static — a real height-1 Button renders as a blank bar). */
    EditorView .cbtn {
        width: auto; height: 1; padding: 0 2; margin: 0 2 0 0; text-style: bold;
        color: $text; background: #2a2a33; content-align: center middle;
    }
    EditorView .cbtn.success { background: $success; color: #06210f; }
    EditorView .cbtn.primary { background: $primary; color: $text; }
    EditorView .cbtn:hover { text-style: bold reverse; }
    EditorView .cbtn.disabled { background: #1a1a1a; color: $text-disabled; }
    EditorView .cbtn.navsel { background: $accent; color: $text; }
    EditorView .actions { height: auto; padding: 1 0 0 0; }
    /* Pours: a slim header row the editable cells align under (like the recipe table). */
    #ptitle { text-style: bold; padding: 1 0 0 0; }
    #phead { height: 1; color: $text-muted; text-style: bold; }
    #phead > .plabel { width: 3; }
    #phead > .pcol { width: 9; padding-left: 1; }
    #phead > .pcol.wide { width: 12; }
    #err { height: auto; color: $error; padding: 1 0; }
    #summary { color: $text-muted; padding: 0 0 1 0; }
    #pours { height: auto; }
    """

    def __init__(self, store: RecipeStore, on_done: Callable[[bool], None], **kwargs) -> None:
        super().__init__(**kwargs)
        self.store = store
        self.on_done = on_done
        self._path = None
        self._orig = None       # the source recipe (to preserve its metadata)
        self._new = False       # a create/clone (always saves; never "no changes")

    def compose(self) -> ComposeResult:
        yield Static("✎ Recipe editor", id="etitle")
        with Horizontal(classes="field"):
            yield Label("Name", classes="flabel")
            yield Input(id="name", placeholder="recipe name")
        with Horizontal(classes="field"):
            yield Label("Dose (g)", classes="flabel")
            yield Input(id="dose_g", classes="num", type="integer")
            yield Label("Grind", classes="flabel")
            yield Input(id="grind", classes="num", type="integer")
            yield Checkbox("no-grind", id="no_grind")
        with Horizontal(classes="field"):
            yield Label("Ratio (1:X)", classes="flabel")
            yield Input(id="ratio", classes="num")
            yield Label("Stage temps", classes="flabel")
            yield Input(id="t1", classes="num", type="integer")
            yield Input(id="t2", classes="num", type="integer")
        yield Static("Pours", id="ptitle")
        with Horizontal(id="phead"):
            yield Label("", classes="plabel")
            yield Label("ml", classes="pcol")
            yield Label("°C", classes="pcol")
            yield Label("pattern", classes="pcol wide")
            yield Label("pause", classes="pcol")
            yield Label("rpm", classes="pcol")
            yield Label("flow", classes="pcol")
            yield Label("agit", classes="pcol")
        yield Vertical(id="pours")
        yield ClickCell("+ add pour", id="add", classes="cbtn primary")
        yield Static("", id="summary")
        yield Static("", id="err")
        with Horizontal(classes="actions"):
            yield ClickCell("Save", id="save", classes="cbtn success")
            yield ClickCell("Cancel", id="cancel", classes="cbtn")

    def load(self, recipe: Recipe | None, path=None, clone: bool = False) -> None:
        """Populate from a recipe (or blank defaults for a new one).

        ``clone`` loads an existing recipe's values but as a brand-new recipe: no path
        (saves to a fresh file), name suffixed " (copy)", and it always counts as
        changed. The source's metadata is still carried across via ``_orig``.
        """
        self._path = None if clone else path
        self._orig = recipe
        self._new = recipe is None or clone
        title = "✎ New recipe" if recipe is None else (
            "✎ New recipe (clone)" if clone else "✎ Edit recipe")
        self.query_one("#etitle", Static).update(title)
        name = "" if recipe is None else (f"{recipe.name} (copy)" if clone else recipe.name)
        self.query_one("#name", Input).value = name
        self.query_one("#dose_g", Input).value = str(recipe.dose_g) if recipe else "16"
        if recipe is None:
            grind_val = "55"                                # sensible default for a NEW recipe
        elif recipe.no_grind:
            grind_val = ""
        else:
            grind_val = str(recipe.grind)
        self.query_one("#grind", Input).value = grind_val
        self.query_one("#no_grind", Checkbox).value = bool(recipe and recipe.no_grind)
        self.query_one("#ratio", Input).value = f"{recipe.effective_ratio:g}" if recipe else "15"
        st = recipe.stage_temps if recipe else (110.0, 90.0)
        self.query_one("#t1", Input).value = str(int(st[0]))
        self.query_one("#t2", Input).value = str(int(st[1]))
        pours = self.query_one("#pours", Vertical)
        pours.remove_children()
        if recipe:
            src = [{
                "ml": p.ml, "temp_c": p.temp_c, "pattern": p.pattern, "pause_s": p.pause_s,
                "rpm": p.rpm, "flow_ml_s": p.flow_ml_s, "agitation": p.agitation,
            } for p in recipe.pours]
        else:  # blank defaults for a new recipe
            src = [{"ml": 40, "pause_s": 30}, {"ml": 200, "pause_s": 5}]
        for i, p in enumerate(src, 1):
            pours.mount(PourRow(i, p))
        self.call_after_refresh(self._revalidate)

    # ── build + validate ───────────────────────────────────────────
    def _collect(self) -> dict:
        def numv(wid, cast, default):
            try:
                return cast(self.query_one(f"#{wid}", Input).value)
            except (ValueError, TypeError):
                return default
        no_grind = self.query_one("#no_grind", Checkbox).value
        data = {
            "name": self.query_one("#name", Input).value.strip() or "Unnamed",
            "dose_g": numv("dose_g", int, 0),
            "grind": 0 if no_grind else numv("grind", int, -1),
            "ratio": numv("ratio", float, None),
            "stage_temps": [numv("t1", float, 110.0), numv("t2", float, 90.0)],
            "pours": [row.value() for row in self.query(PourRow)],
        }
        # The form edits only the core brew params — preserve the original recipe's
        # optional metadata (dripper/kind/water_ml/…) and per-pour labels so an edit
        # never silently drops them.
        if self._orig is not None:
            for key in ("kind", "dripper", "water_ml", "hot_water_ml", "ice_g", "time", "note"):
                val = getattr(self._orig, key, None)
                if val is not None:
                    data[key] = val
            for i, pour in enumerate(data["pours"]):
                if i < len(self._orig.pours) and self._orig.pours[i].label:
                    pour["label"] = self._orig.pours[i].label
        return data

    def _build(self):
        return Recipe.from_dict(self._collect())

    def _revalidate(self) -> None:
        data = self._collect()
        total = sum(p["ml"] for p in data["pours"])
        ratio = data.get("ratio")
        exp = f" (dose×ratio = {data['dose_g'] * ratio:g})" if ratio else ""
        self.query_one("#summary", Static).update(
            f"Σ pours = {total} ml{exp} · {len(data['pours'])} pours"
        )
        save = self.query_one("#save", ClickCell)
        try:
            self._build()
            self.query_one("#err", Static).update("[green]✓ valid[/]")
            save.remove_class("disabled")
        except RecipeError as exc:
            self.query_one("#err", Static).update(f"[$error]⚠ {exc}[/]")
            save.add_class("disabled")

    def on_input_changed(self, _event) -> None:
        self._revalidate()

    def on_checkbox_changed(self, _event) -> None:
        # grey out grind when no-grind is on
        self.query_one("#grind", Input).disabled = self.query_one("#no_grind", Checkbox).value
        self._revalidate()

    def _activate(self, cell) -> None:
        """Route a ClickCell action: add pour / remove pour / save / cancel."""
        cid = cell.id
        if cid == "add":
            n = len(self.query(PourRow)) + 1
            self.query_one("#pours", Vertical).mount(PourRow(n, {"ml": 60, "pause_s": 5}))
            self._mark_dirty()
            self._revalidate()
        elif cid == "remove":
            self.remove_pour(cell.parent)
        elif cid == "save":
            fn = getattr(self.screen, "action_save", None)
            (fn or self.save)()
        elif cid == "cancel":
            self.on_done(False)

    def remove_pour(self, row) -> None:
        """Delete a pour row (called by its ✕ cell), then renumber + revalidate."""
        if not isinstance(row, PourRow):
            return
        row.remove()
        self._renumber()
        self._mark_dirty()
        self.call_after_refresh(self._revalidate)

    def _mark_dirty(self) -> None:
        fn = getattr(self.screen, "_mark_dirty", None)
        if callable(fn):
            fn()

    def is_unchanged(self) -> bool:
        """True if the form still matches the recipe it opened (nothing was edited)."""
        if self._new or self._orig is None:   # a create/clone always saves
            return False
        try:
            return self._build().to_dict() == self._orig.to_dict()
        except RecipeError:
            return False

    def save(self) -> bool:
        """Validate + persist. Returns True if saved (no-op if the recipe is invalid)."""
        try:
            recipe = self._build()
        except RecipeError:
            return False
        self.store.save(recipe, self._path)
        self.on_done(True)
        return True

    def _renumber(self) -> None:
        for i, row in enumerate(self.query(PourRow), 1):
            row._idx = i
            try:
                row.query_one(Label).update(str(i))
            except Exception:
                pass

    def nav_grid(self) -> list[list]:
        """The editable widgets laid out in a 2D grid (rows of fields) for arrow-key
        navigation: name / dose-grind / ratio-temps / each pour / add / save-cancel."""
        grid = [
            [self.query_one("#name")],
            [self.query_one("#dose_g"), self.query_one("#grind"), self.query_one("#no_grind")],
            [self.query_one("#ratio"), self.query_one("#t1"), self.query_one("#t2")],
        ]
        for pr in self.query(PourRow):
            grid.append([w for w in pr.children if not isinstance(w, Label)])
        grid.append([self.query_one("#add")])
        grid.append([self.query_one("#save"), self.query_one("#cancel")])
        return grid


class EditorScreen(ModalScreen):
    """The editor as a modal with a **navigate/edit** model (vim-like).

    NAVIGATE mode: arrow keys move a highlighted selection between fields (up/down
    across rows, left/right within a row); ``Enter`` edits the selected field (or
    presses a button / toggles a checkbox); ``Esc`` (with unsaved changes) arms a
    discard, ``Esc`` again cancels. EDIT mode (a field is focused for typing): type
    freely, ``Enter``/``Esc`` return to navigate. ``Ctrl+S`` saves from anywhere.
    """

    BINDINGS = []   # all keys handled in on_key (so nothing leaks to the app while editing)
    DEFAULT_CSS = """
    EditorScreen { align: center middle; }
    EditorScreen > EditorView {
        width: 88%; height: 88%; border: heavy $primary; background: #000000; padding: 0 1;
    }
    .navsel { background: $accent 45%; text-style: bold; }
    """

    def __init__(self, store: RecipeStore, recipe: Recipe | None, path,
                 clone: bool = False) -> None:
        super().__init__()
        self._store = store
        self._recipe = recipe
        self._path = path
        self._clone = clone
        self._dirty = False
        self._loaded = False
        self._escape_armed = False
        self._nav = (0, 0)         # (row, col) in the field grid
        self._editing = False

    def compose(self) -> ComposeResult:
        yield EditorView(self._store, self._done)

    def on_mount(self) -> None:
        self.query_one(EditorView).load(self._recipe, self._path, clone=self._clone)
        self.call_after_refresh(self._start_nav)

    def _start_nav(self) -> None:
        self._loaded = True
        self._nav = (0, 0)
        self.set_focus(None)
        self._highlight()

    # ── navigation grid ────────────────────────────────────────────
    def _grid(self) -> list[list]:
        return self.query_one(EditorView).nav_grid()

    def _cur_widget(self):
        grid = self._grid()
        r, c = self._nav
        r = max(0, min(r, len(grid) - 1))
        c = max(0, min(c, len(grid[r]) - 1))
        self._nav = (r, c)
        return grid[r][c]

    def _highlight(self) -> None:
        for w in self.query(".navsel"):
            w.remove_class("navsel")
        if self._editing:
            return
        try:
            self._cur_widget().add_class("navsel")
        except Exception:
            pass

    def _move(self, key: str) -> None:
        grid = self._grid()
        r, c = self._nav
        if key == "up":
            r -= 1
        elif key == "down":
            r += 1
        elif key == "left":
            c -= 1
        elif key == "right":
            c += 1
        r = max(0, min(r, len(grid) - 1))
        c = max(0, min(c, len(grid[r]) - 1))
        self._nav = (r, c)
        self._highlight()

    def _enter_edit(self) -> None:
        w = self._cur_widget()
        if isinstance(w, (ClickCell, AgitCell, PatternCell)):
            w.on_click()                       # button / toggle agit / cycle pattern
            self.call_after_refresh(self._highlight)   # grid may have changed
        elif isinstance(w, Checkbox):
            w.value = not w.value
            self._dirty = True
            self._escape_armed = False
        elif isinstance(w, Input):
            if w.disabled:
                return
            w.remove_class("navsel")
            self._editing = True
            w.focus()

    def _exit_edit(self) -> None:
        self._editing = False
        self.set_focus(None)
        self.call_after_refresh(self._highlight)

    def on_key(self, event) -> None:
        if not self._loaded:
            return
        if event.key == "ctrl+s":
            self.action_save()
            event.stop()
            return
        if self._editing:
            if event.key in ("escape", "enter"):
                self._exit_edit()
                event.stop()
            # else: let the focused Input handle typing + cursor keys
            return
        # NAVIGATE mode — handle known keys and SWALLOW the rest so letter keys
        # (d=delete, b=brew, …) never leak to the app while the editor is open.
        if event.key in ("up", "down", "left", "right"):
            self._move(event.key)
        elif event.key == "enter":
            self._enter_edit()
        elif event.key == "escape":
            self.action_cancel()
        event.stop()

    # ── save / cancel ──────────────────────────────────────────────
    def _done(self, saved: bool) -> None:
        if saved:
            self.dismiss(True)
        else:
            self.action_cancel()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._escape_armed = False

    def action_save(self) -> None:
        ev = self.query_one(EditorView)
        if ev.is_unchanged():
            self.notify("no changes — nothing to save", severity="information")
            self.dismiss(False)
            return
        if not ev.save():
            self.app.bell()
            self.notify("recipe is invalid — fix the errors before saving", severity="warning")

    def action_cancel(self) -> None:
        if self._dirty and not self._escape_armed:
            self._escape_armed = True
            self.notify("unsaved changes — press esc again to discard, or ^s to save",
                        severity="warning")
        else:
            self.dismiss(False)

    def on_input_changed(self, _event) -> None:
        if self._loaded:
            self._dirty = True
            self._escape_armed = False

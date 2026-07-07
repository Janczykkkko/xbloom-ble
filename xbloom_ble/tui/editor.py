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
from textual.widgets import Button, Checkbox, Input, Label, Static

from ..recipe import Recipe, RecipeError
from .store import RecipeStore


class PourRow(Horizontal):
    """One editable pour: ml · temp · pattern · pause · rpm · flow · agitate · ✕."""

    DEFAULT_CSS = """
    PourRow { height: 3; }
    PourRow Input { width: 9; }
    PourRow #pattern { width: 11; }
    PourRow .plabel { width: 3; content-align: right middle; color: $text-muted; }
    PourRow Checkbox { width: 8; }
    PourRow Button { width: 5; }
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
        yield Input(self._init["pattern"], id="pattern", placeholder="spiral/ring/center")
        yield Input(self._init["pause_s"], id="pause_s", placeholder="pause", type="integer")
        yield Input(self._init["rpm"], id="rpm", placeholder="rpm", type="integer")
        yield Input(self._init["flow_ml_s"], id="flow_ml_s", placeholder="flow")
        yield Checkbox("agit", value=self._init["agitation"], id="agitation")
        yield Button("✕", id="remove", variant="error")

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
            return self.query_one("#pattern", Input).value.strip() or "spiral"
        except Exception:
            return "spiral"

    def _agit(self) -> bool:
        try:
            return self.query_one("#agitation", Checkbox).value
        except Exception:
            return False


class EditorView(VerticalScroll):
    """Create/edit a recipe. ``on_done(saved: bool)`` is called on save or cancel."""

    DEFAULT_CSS = """
    EditorView { padding: 1 2; background: #000000; }
    EditorView .field { height: 3; }
    EditorView .flabel { width: 16; content-align: left middle; color: $text-muted; }
    EditorView #name { width: 40; }
    EditorView .num { width: 12; }
    #err { height: auto; color: $error; padding: 1 0; }
    #summary { color: $text-muted; padding: 0 0 1 0; }
    #pours { height: auto; }
    """

    def __init__(self, store: RecipeStore, on_done: Callable[[bool], None], **kwargs) -> None:
        super().__init__(**kwargs)
        self.store = store
        self.on_done = on_done
        self._path = None
        self._orig = None       # the recipe being edited (to preserve its metadata)

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
        yield Vertical(id="pours")
        yield Button("+ add pour", id="add", variant="primary")
        yield Static("", id="summary")
        yield Static("", id="err")
        with Horizontal(classes="field"):
            yield Button("Save", id="save", variant="success")
            yield Button("Cancel", id="cancel")

    def load(self, recipe: Recipe | None, path=None) -> None:
        """Populate from a recipe (or blank defaults for a new one)."""
        self._path = path
        self._orig = recipe
        self.query_one("#etitle", Static).update("✎ Edit recipe" if recipe else "✎ New recipe")
        self.query_one("#name", Input).value = recipe.name if recipe else ""
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
        try:
            self._build()
            self.query_one("#err", Static).update("[green]✓ valid[/]")
            self.query_one("#save", Button).disabled = False
        except RecipeError as exc:
            self.query_one("#err", Static).update(f"[$error]⚠ {exc}[/]")
            self.query_one("#save", Button).disabled = True

    def on_input_changed(self, _event) -> None:
        self._revalidate()

    def on_checkbox_changed(self, _event) -> None:
        # grey out grind when no-grind is on
        self.query_one("#grind", Input).disabled = self.query_one("#no_grind", Checkbox).value
        self._revalidate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "add":
            n = len(self.query(PourRow)) + 1
            self.query_one("#pours", Vertical).mount(PourRow(n, {"ml": 60, "pause_s": 5}))
            self._revalidate()
        elif bid == "remove":
            event.button.parent.remove()
            self._renumber()
            self.call_after_refresh(self._revalidate)
        elif bid == "cancel":
            self.on_done(False)
        elif bid == "save":
            self.save()

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

    def __init__(self, store: RecipeStore, recipe: Recipe | None, path) -> None:
        super().__init__()
        self._store = store
        self._recipe = recipe
        self._path = path
        self._dirty = False
        self._loaded = False
        self._escape_armed = False
        self._nav = (0, 0)         # (row, col) in the field grid
        self._editing = False

    def compose(self) -> ComposeResult:
        yield EditorView(self._store, self._done)

    def on_mount(self) -> None:
        self.query_one(EditorView).load(self._recipe, self._path)
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
        if isinstance(w, Button):
            w.press()                          # add pour / remove / save / cancel
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

    def action_save(self) -> None:
        if not self.query_one(EditorView).save():
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

"""Journey tests for the TUI — one per user flow, driven against the simulator.

Each flow presses the real keys and asserts the outcome, headless via Textual's Pilot.
Covers the tabbed shell (Recipes/Brewing/History), the modal editor, slots-from-list,
import, and history-with-telemetry.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("textual")
pytest.importorskip("textual_plotext")

from textual.widgets import Button, Input  # noqa: E402

from xbloom_ble.telemetry import StatusEvent  # noqa: E402
from xbloom_ble.tui import app as app_mod  # noqa: E402
from xbloom_ble.tui.app import RecipesView, XBloomApp  # noqa: E402
from xbloom_ble.tui.confirm import ConfirmBrewScreen  # noqa: E402
from xbloom_ble.tui.controller import FakeController, MachineController  # noqa: E402
from xbloom_ble.tui.editor import EditorScreen, PourRow  # noqa: E402
from xbloom_ble.tui.history import HistoryList, HistoryStore  # noqa: E402
from xbloom_ble.tui.slots import SlotStore  # noqa: E402
from xbloom_ble.tui.store import RecipeStore  # noqa: E402

FILTER_RECIPE = """
name: Test Filter
dose_g: 16
grind: 55
ratio: 15
pours:
  - {ml: 40, temp_c: 92, pattern: spiral, pause_s: 30, rpm: 100, flow_ml_s: 3.0}
  - {ml: 200, temp_c: 92, pattern: spiral, pause_s: 5, rpm: 100, flow_ml_s: 3.0}
"""
NOGRIND_RECIPE = """
name: Test No-Grind
dose_g: 16
grind: 0
ratio: 15
pours:
  - {ml: 40, temp_c: 92, pattern: spiral, pause_s: 30, rpm: 100, flow_ml_s: 3.0}
  - {ml: 200, temp_c: 92, pattern: spiral, pause_s: 5, rpm: 100, flow_ml_s: 3.0}
"""
BROKEN_RECIPE = "name: Broken One\ndose_g: 16\ngrind: 999\npours: []\n"


@pytest.fixture
def store(tmp_path):
    (tmp_path / "filter.yaml").write_text(FILTER_RECIPE)
    (tmp_path / "nogrind.yaml").write_text(NOGRIND_RECIPE)
    return RecipeStore(tmp_path)


@pytest.fixture
def store_with_broken(tmp_path):
    (tmp_path / "filter.yaml").write_text(FILTER_RECIPE)
    (tmp_path / "broken.yaml").write_text(BROKEN_RECIPE)
    return RecipeStore(tmp_path)


def drive(store, scenario, *, speed=0.002, auto_start=0.03, size=(120, 34),
          history=None, slots=None, controller=None):
    async def run():
        ctrl = controller or FakeController(speed=speed, auto_start=auto_start)
        app = XBloomApp(store, ctrl, history=history, slots=slots)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.05)
            return await scenario(app, pilot)
    return asyncio.run(run())


async def await_brew(app, pilot, timeout=20.0):   # generous: coverage instrumentation slows the sim
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        await pilot.pause(0.02)
        if not app._brewing and app._water:
            return
    raise AssertionError("brew did not complete")


async def start_brew(app, pilot, key="b"):
    """Press a brew key and clear the confirm gate (press 'y' to start)."""
    await pilot.press(key)
    for _ in range(40):
        await pilot.pause(0.02)
        if isinstance(app.screen, ConfirmBrewScreen):
            break
    else:
        raise AssertionError("brew confirm gate did not open")
    await pilot.press("y")
    await pilot.pause(0.05)


async def open_editor(app, pilot, key="n"):
    await pilot.press(key)
    for _ in range(30):
        await pilot.pause(0.03)
        if isinstance(app.screen, EditorScreen):
            return app.screen
    raise AssertionError("editor modal did not open")


# ── units ───────────────────────────────────────────────────────────
def test_store_lists_and_validates(store):
    entries = store.list()
    assert {e.name for e in entries} == {"Test Filter", "Test No-Grind"}
    assert all(e.ok for e in entries)


def test_store_flags_broken(store_with_broken):
    broken = [e for e in store_with_broken.list() if not e.ok]
    assert len(broken) == 1 and broken[0].error


def test_fake_controller_brew_reaches_target():
    import yaml

    from xbloom_ble.recipe import Recipe
    r = Recipe.from_dict(yaml.safe_load(FILTER_RECIPE))

    async def run():
        c = FakeController(speed=0.001, auto_start=0.01)
        await c.stage(r)
        return [ev.water_g async for ev in c.telemetry() if ev.water_g is not None]
    waters = asyncio.run(run())
    assert waters[-1] == r.total_water_ml and waters == sorted(waters)


def test_recipe_from_cloud_roundtrips():
    import yaml

    from xbloom_ble.cloud import recipe_from_cloud, recipe_to_cloud
    from xbloom_ble.recipe import Recipe
    original = Recipe.from_dict(yaml.safe_load(FILTER_RECIPE))
    back = recipe_from_cloud(recipe_to_cloud(original, cup_type="xdripper"))
    assert back.dose_g == original.dose_g and back.total_water_ml == original.total_water_ml


# ── tabs / navigation ───────────────────────────────────────────────
def test_journey_startup(store):
    async def s(app, pilot):
        return app.query_one(RecipesView).row_count, app._current is not None, app._view
    rows, has_current, view = drive(store, s)
    assert rows == 2 and has_current and view == "recipes"


def test_journey_detail_sidebar_updates_on_highlight(store):
    """The Recipes-tab sidebar renders the highlighted recipe and follows the cursor."""
    from xbloom_ble.tui.app import RecipeDetail

    async def s(app, pilot):
        body = app.query_one("#recipe-detail-body")
        first = str(body.render())
        await pilot.press("j")                     # move cursor to the next recipe
        await pilot.pause(0.05)
        second = str(body.render())
        return first, second, RecipeDetail.can_focus
    first, second, focusable = drive(store, s)
    # both test recipes have a name + a "Pours" section; the two differ as we scroll
    assert "Pours" in first and "dose" in first
    assert first != second
    assert focusable is False                      # sidebar must not be a Tab stop


def test_journey_tab_cycles_views(store, tmp_path):
    async def s(app, pilot):
        seen = [app._view]
        for _ in range(3):
            await pilot.press("tab")
            await pilot.pause(0.05)
            seen.append(app._view)
        return seen
    seen = drive(store, s, slots=SlotStore(tmp_path / "s.json"),
                 history=HistoryStore(tmp_path / "h.json"))
    assert seen == ["recipes", "brewing", "history", "recipes"]


def test_journey_shift_tab_goes_back(store, tmp_path):
    async def s(app, pilot):
        await pilot.press("shift+tab")
        await pilot.pause(0.05)
        return app._view
    assert drive(store, s, slots=SlotStore(tmp_path / "s.json"),
                 history=HistoryStore(tmp_path / "h.json")) == "history"


def test_journey_navigation_changes_current(store):
    async def s(app, pilot):
        first = app._current.name
        await pilot.press("j")
        await pilot.pause(0.05)
        return first, app._current.name
    first, second = drive(store, s)
    assert first != second


def test_journey_filter_narrows_table(store):
    async def s(app, pilot):
        await pilot.press("slash")
        await pilot.press(*"grind")
        await pilot.pause(0.05)
        return app.query_one(RecipesView).row_count
    assert drive(store, s) == 1


# ── brewing ─────────────────────────────────────────────────────────
def test_journey_enter_brews_switches_to_brewing_tab(store):
    async def s(app, pilot):
        await pilot.press("enter")            # enter on a recipe → confirm gate
        for _ in range(40):
            await pilot.pause(0.02)
            if isinstance(app.screen, ConfirmBrewScreen):
                break
        await pilot.press("y")                # confirm
        await await_brew(app, pilot)
        return app._view, list(app._water)
    view, waters = drive(store, s)
    assert view == "brewing" and waters and waters[-1] > 0


def test_journey_b_brews(store):
    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        await await_brew(app, pilot)
        return list(app._water)
    assert drive(store, s)[-1] > 0


def test_journey_help_opens_and_closes(store):
    from xbloom_ble.tui.help import HelpScreen

    async def s(app, pilot):
        await pilot.press("h")
        for _ in range(30):
            await pilot.pause(0.02)
            if isinstance(app.screen, HelpScreen):
                break
        opened = isinstance(app.screen, HelpScreen)
        body = str(app.screen.query_one("#help-body").render())
        await pilot.press("escape")
        await pilot.pause(0.1)
        return opened, body, isinstance(app.screen, HelpScreen)
    opened, body, still_open = drive(store, s)
    assert opened and not still_open
    assert "confirm gate" in body.lower() and "hot water" in body.lower()


def test_journey_confirm_gate_shows_recipe_details(store):
    async def s(app, pilot):
        await pilot.press("b")
        for _ in range(40):
            await pilot.pause(0.02)
            if isinstance(app.screen, ConfirmBrewScreen):
                break
        return str(app.screen.query_one("#cb-detail").render())
    detail = drive(store, s)
    assert "Pours" in detail and "ml" in detail        # the pour schedule is shown


def test_journey_brew_needs_confirmation(store):
    """The brew is gated: pressing brew opens a confirm modal and does NOT start
    on its own. Cancelling the gate leaves the machine untouched."""
    ctrl = FakeController(speed=0.03, auto_start=5.0)

    async def s(app, pilot):
        await pilot.press("b")
        gate_open = False
        for _ in range(40):
            await pilot.pause(0.02)
            if isinstance(app.screen, ConfirmBrewScreen):
                gate_open = True
                break
        await pilot.press("n")                # cancel the gate
        await pilot.pause(0.1)
        return gate_open, ctrl.started, app._brewing
    gate_open, started, brewing = drive(store, s, controller=ctrl)
    assert gate_open and started is False and not brewing


def test_journey_brew_gate_arrow_to_start_then_enter(store):
    """The gate is navigable: arrow past Load to Start, press Enter → brew launches."""
    ctrl = FakeController(speed=0.03, auto_start=5.0)

    async def s(app, pilot):
        await pilot.press("b")
        for _ in range(40):
            await pilot.pause(0.02)
            if isinstance(app.screen, ConfirmBrewScreen):
                break
        await pilot.press("right", "right")   # focus Cancel → Load → Start
        await pilot.pause(0.05)
        await pilot.press("enter")            # Enter on the focused Start button
        await await_brew(app, pilot)
        return ctrl.started, app._water[-1]
    started, final = drive(store, s, controller=ctrl)
    assert started is True and final > 0


def test_journey_brew_load_only_streams_without_remote_start(store):
    """'Load only' arms the machine (no commit+start) but still streams telemetry
    and completes — for approving on the machine while keeping the live graph."""
    ctrl = FakeController(speed=0.03, auto_start=0.05)  # short: sim 'approval' fires the brew

    async def s(app, pilot):
        await pilot.press("b")
        for _ in range(40):
            await pilot.pause(0.02)
            if isinstance(app.screen, ConfirmBrewScreen):
                break
        await pilot.press("l")                # Load only
        await await_brew(app, pilot)
        return ctrl.started, app._water[-1], app._view
    started, final, view = drive(store, s, controller=ctrl)
    assert started is False and final > 0 and view == "brewing"


def test_journey_brew_starts_remotely(store):
    """After confirming, brew should START remotely (commit+start), app-style —
    not just load and wait for on-machine approval. FakeController records start()."""
    ctrl = FakeController(speed=0.03, auto_start=5.0)  # long fallback: only start() should fire it

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        await await_brew(app, pilot)
        return ctrl.started, app._water[-1]
    started, final = drive(store, s, controller=ctrl)
    assert started is True and final > 0


class _SilentGrindController(MachineController):
    """Reproduces real hardware for the silent-grind regression.

    On the machine, ``start()`` reports ``starting`` as a REAL status frame and then
    the machine grinds + blooms SILENTLY (heartbeats only, which telemetry drops) for
    ~20 s before the pour. So the telemetry stream yields nothing for a long while, and
    the first frame it DOES yield (the pour) may be an undecoded state. The brew must
    not be cancelled mid-grind by the "didn't start" guard.
    """

    def __init__(self, grind_silence: float) -> None:
        self.address = "AA:BB:CC:DD:EE:FF"
        self.cancelled = False
        self.started = False
        self._silence = grind_silence

    async def scan(self):
        return [self.address]

    async def connect(self, address=None):
        pass

    async def stage(self, recipe):
        recipe.validate()
        return StatusEvent(state=0x1F, state_name="armed", raw=b"\x58armed")

    async def start(self):
        self.started = True
        # A REAL post-commit status frame (non-empty raw) — exactly what client.start()
        # returns once the machine reaches 0x22. This is the seed the guard relies on.
        return StatusEvent(state=0x22, state_name="starting", raw=b"\x58starting")

    async def telemetry(self, duration: float = 300.0):
        await asyncio.sleep(self._silence)          # the silent grind — no status frames
        # The pour finally shows up as an UNDECODED frame (as 0x10 was, pre-decode) — it
        # must reach the guard without having set saw_progress, to prove the seed works.
        yield StatusEvent(state=0x99, state_name="unknown_0x99", raw=b"\x58pour")
        await asyncio.sleep(0.02)
        yield StatusEvent(state=0x41, state_name="complete", raw=b"\x58done")

    async def cancel(self):
        self.cancelled = True

    async def save_slots(self, recipes):
        pass

    async def disconnect(self):
        pass


class _ScaleStreamController(MachineController):
    """Streams real-shape scale frames (0x4b water / 0x15 coffee arrive separately),
    then the 0x24 'ready' beep — exercising the live-weight graph pairing + completion.
    """

    def __init__(self) -> None:
        self.address = "AA:BB:CC:DD:EE:FF"
        self.cancelled = False
        self.started = False

    async def scan(self):
        return [self.address]

    async def connect(self, address=None):
        pass

    async def stage(self, recipe):
        recipe.validate()
        return StatusEvent(state=0x1F, state_name="armed", raw=b"\x58armed")

    async def start(self):
        self.started = True
        return StatusEvent(state=0x22, state_name="starting", raw=b"\x58start")

    async def telemetry(self, duration: float = 300.0):
        # water and coffee come as SEPARATE frames — the UI pairs them into one point
        for w, c in [(35.0, None), (None, 12.0), (240.0, None), (None, 204.0)]:
            yield StatusEvent(state=None, state_name="scale", raw=b"\x58s",
                              water_g=w, coffee_g=c)
            await asyncio.sleep(0.01)
        yield StatusEvent(state=0x24, state_name="ready", raw=b"\x58r")

    async def cancel(self):
        self.cancelled = True

    async def save_slots(self, recipes):
        pass

    async def disconnect(self):
        pass


def test_journey_brew_streams_live_weights_and_completes_on_ready(store, tmp_path):
    """The live water/coffee streams fill the graph, the brew completes on the 0x24
    'ready' beep (no cup-off needed), and peak weights are recorded to history."""
    from xbloom_ble.tui.history import HistoryStore
    hist = HistoryStore(tmp_path / "h.json")
    ctrl = _ScaleStreamController()

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        for _ in range(300):
            await pilot.pause(0.02)
            if not app._brewing:
                break
        return list(app._water), list(app._coffee), hist.list()

    water, coffee, hlist = drive(store, s, controller=ctrl, history=hist)
    assert max(water) == 240.0 and max(coffee) == 204.0   # paired running points
    assert not ctrl.cancelled                              # completed, not aborted
    assert hlist and hlist[0]["water_g"] == 240.0          # peak recorded to history


class _RefusalController(_ScaleStreamController):
    """start() reports a no-water/no-beans refusal — the brew must abort (cancel)."""

    def __init__(self, what: str = "no_water") -> None:
        super().__init__()
        self._what = what

    async def start(self):
        self.started = True
        st = 0x0C if self._what == "no_water" else 0x0F
        return StatusEvent(state=st, state_name=self._what, raw=b"\x58refuse")

    async def telemetry(self, duration: float = 300.0):
        if False:            # never reached — _brew aborts before streaming
            yield


def test_journey_brew_aborts_on_no_water(store):
    ctrl = _RefusalController("no_water")

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        for _ in range(100):
            await pilot.pause(0.02)
            if not app._brewing:
                break
        return ctrl.cancelled, app._brewing

    cancelled, brewing = drive(store, s, controller=ctrl)
    assert cancelled and not brewing     # _abort_supply cancelled it back to idle


def test_journey_remote_brew_survives_silent_grind(store, monkeypatch):
    """Regression (hw log 2026-07-08 09:15): a real brew was cancelled mid-grind.

    The machine grinds SILENTLY past the guard window, and start() had already consumed
    the one 'starting' frame — so saw_progress stayed False and the guard fired 0x47 as
    the pour began. Fix: seed saw_progress from start()'s real return. With the guard
    shrunk below the silent-grind time, the brew must still complete and NOT be cancelled.
    """
    monkeypatch.setattr(app_mod, "_GRIND_GUARD_S", 0.15)
    ctrl = _SilentGrindController(grind_silence=0.4)   # 0.4s silence >> 0.15s guard

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        for _ in range(400):
            await pilot.pause(0.02)
            if not app._brewing:
                break
        return app._brewing, ctrl.cancelled

    brewing, cancelled = drive(store, s, controller=ctrl)
    assert cancelled is False        # the guard must NOT have cancelled the silent-grind brew
    assert brewing is False          # …and it ran to completion


def test_journey_escape_leaves_brewing_but_keeps_it_running(store):
    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        target = app._current.total_water_ml
        caught_midbrew = False
        for _ in range(200):                      # catch it genuinely mid-brew
            await pilot.pause(0.02)
            if app._brewing and app._water and app._water[-1] < target:
                caught_midbrew = True
                break
        await pilot.press("escape")               # leave the brewing tab MID-brew
        await pilot.pause(0.05)
        left_tab = app._view
        await await_brew(app, pilot)              # brew keeps going after we left…
        return caught_midbrew, left_tab, app._water[-1], target
    # Deterministic: leaving the tab must not cancel the brew, so it completes to
    # the full target (a timing-independent proof, vs. sampling `_brewing`).
    caught, left_tab, final, target = drive(store, s, speed=0.05, auto_start=0.05)
    assert caught and left_tab == "recipes" and final == target


def test_journey_cancel_stops_brew(store):
    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        target = app._current.total_water_ml
        for _ in range(60):
            await pilot.pause(0.02)
            if app._water and 0 < app._water[-1] < target:
                break
        await pilot.press("c")
        for _ in range(60):
            await pilot.pause(0.02)
            if not app._brewing:
                break
        return app._brewing, app._water[-1], target
    brewing, last, target = drive(store, s, speed=0.03, auto_start=0.05)
    assert not brewing and last < target


# ── editor (modal) ──────────────────────────────────────────────────
def test_journey_new_recipe_via_ctrl_s(store):
    async def s(app, pilot):
        ed = await open_editor(app, pilot, "n")
        ed.query_one("#name", Input).value = "Made With CtrlS"
        await pilot.pause(0.1)
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        return isinstance(app.screen, EditorScreen), {e.name for e in app._entries}
    still_modal, names = drive(store, s)
    assert not still_modal and "Made With CtrlS" in names


def test_journey_edit_opens_populated(store):
    async def s(app, pilot):
        current = app._current.name
        ed = await open_editor(app, pilot, "e")
        return ed.query_one("#name", Input).value, len(ed.query(PourRow)), current
    name, npours, current = drive(store, s)
    assert name == current and npours >= 2


ENRICHED_RECIPE = """
name: Fireworks Filter
dose_g: 15
grind: 60
ratio: 16
kind: medium-auto
dripper: Omni
water_ml: 240
time: "~2:00"
note: strawberry-forward; ground finer as it aged
pours:
  - {label: Bloom, ml: 60, temp_c: 92, pattern: spiral, pause_s: 45, rpm: 120, flow_ml_s: 3.0, agitation: true}
  - {label: Pour 1, ml: 180, temp_c: 91, pattern: spiral, pause_s: 5, rpm: 120, flow_ml_s: 3.0}
"""


def test_journey_editor_preserves_metadata_on_edit(tmp_path):
    """Editing core fields must NOT drop the recipe's metadata / pour labels."""
    (tmp_path / "fireworks.yaml").write_text(ENRICHED_RECIPE)
    store = RecipeStore(tmp_path)

    async def s(app, pilot):
        ed = await open_editor(app, pilot, "e")
        ed.query_one("#grind", Input).value = "58"       # change one core field
        await pilot.pause(0.1)
        ed.query_one("#save", Button).press()
        await pilot.pause(0.15)
        return store.load(tmp_path / "fireworks.yaml")
    r = drive(store, s)
    assert r.grind == 58                                  # the edit landed
    assert r.dripper == "Omni" and r.kind == "medium-auto"
    assert r.water_ml == 240 and r.time == "~2:00"
    assert r.note.startswith("strawberry")
    assert r.pours[0].label == "Bloom" and r.pours[1].label == "Pour 1"


def test_journey_editor_validation_blocks_save(store):
    async def s(app, pilot):
        ed = await open_editor(app, pilot, "e")
        ed.query_one("#dose_g", Input).value = "99"      # dose > 18
        await pilot.pause(0.1)
        return ed.query_one(EditorScreen).query_one("#save", Button).disabled \
            if False else ed.query_one("#save", Button).disabled
    assert drive(store, s) is True


def test_journey_editor_add_remove_pour(store):
    async def s(app, pilot):
        ed = await open_editor(app, pilot, "e")
        before = len(ed.query(PourRow))
        ed.query_one("#add", Button).press()
        await pilot.pause(0.15)
        after_add = len(ed.query(PourRow))
        ed.query(PourRow).last().query_one("#remove", Button).press()
        await pilot.pause(0.15)
        return before, after_add, len(ed.query(PourRow))
    before, after_add, after_remove = drive(store, s)
    assert after_add == before + 1 and after_remove == before


def test_journey_editor_discard_guard(store):
    async def s(app, pilot):
        ed = await open_editor(app, pilot, "e")
        ed.query_one("#name", Input).value = "changed but not saved"
        await pilot.pause(0.1)
        await pilot.press("escape")               # dirty → armed, not dismissed
        await pilot.pause(0.05)
        armed = isinstance(app.screen, EditorScreen)
        await pilot.press("escape")               # again → dismissed
        await pilot.pause(0.1)
        return armed, isinstance(app.screen, EditorScreen)
    armed, still_open = drive(store, s)
    assert armed and not still_open


def test_journey_editor_navigate_then_edit(store):
    async def s(app, pilot):
        ed = await open_editor(app, pilot, "e")
        await pilot.pause(0.15)
        start = ed._nav
        await pilot.press("down")                 # NAVIGATE to the next row
        await pilot.pause(0.05)
        moved = ed._nav
        await pilot.press("enter")                # EDIT the selected field
        await pilot.pause(0.05)
        editing = ed._editing
        await pilot.press("escape")               # exit edit → back to navigate
        await pilot.pause(0.05)
        return start, moved, editing, ed._editing
    start, moved, editing, after = drive(store, s)
    assert moved != start and editing is True and after is False


def test_journey_editor_nav_swallows_letter_keys(store):
    # in navigate mode, 'd' must NOT delete a recipe (keys are swallowed by the modal)
    async def s(app, pilot):
        before = len(app._entries)
        await open_editor(app, pilot, "e")
        await pilot.pause(0.15)
        await pilot.press("d")                    # would delete if it leaked to the app
        await pilot.pause(0.1)
        return before, len(app._entries), isinstance(app.screen, EditorScreen)
    before, after, still_open = drive(store, s)
    assert after == before and still_open


def test_journey_delete_removes_recipe(store):
    async def s(app, pilot):
        before = len(app._entries)
        await pilot.press("d")
        await pilot.pause(0.15)
        return before, len(app._entries), app._last_message
    before, after, msg = drive(store, s)
    assert after == before - 1 and "delet" in msg.lower()


def test_journey_broken_recipe_cannot_brew(store_with_broken):
    async def s(app, pilot):
        rv = app.query_one(RecipesView)
        broken_idx = next(i for i, e in enumerate(app._entries) if not e.ok)
        rv.move_cursor(row=broken_idx)
        await pilot.pause(0.05)
        await pilot.press("enter")
        await pilot.pause(0.1)
        return app._brewing, app._last_message
    brewing, msg = drive(store_with_broken, s)
    assert not brewing and "error" in msg.lower()


# ── slots (from the recipe list) ────────────────────────────────────
def test_journey_slot_assign_shows_in_table(store, tmp_path):
    slots = SlotStore(tmp_path / "s.json")

    async def s(app, pilot):
        await pilot.press("1")
        await pilot.pause(0.1)
        return slots.get()["A"], app._last_message
    path, msg = drive(store, s, slots=slots)
    assert path and "slot a" in msg.lower()


def test_journey_slots_push_programs_all_three(store, tmp_path):
    slots = SlotStore(tmp_path / "s.json")
    ctrl = FakeController(speed=0.002, auto_start=0.03)

    async def s(app, pilot):
        for key in ("1", "2", "3"):
            await pilot.press(key)
            await pilot.pause(0.03)
        await pilot.press("p")
        for _ in range(40):
            await pilot.pause(0.03)
            if ctrl.saved_slots is not None:
                break
        return ctrl.saved_slots
    saved = drive(store, s, slots=slots, controller=ctrl)
    assert saved is not None and len(saved) == 3


def test_journey_slots_push_incomplete_warns(store, tmp_path):
    async def s(app, pilot):
        await pilot.press("p")
        await pilot.pause(0.05)
        return app._last_message
    assert "empty" in drive(store, s, slots=SlotStore(tmp_path / "s.json")).lower()


# ── import ──────────────────────────────────────────────────────────
def test_journey_import_saves_recipe(store, monkeypatch):
    import yaml

    from xbloom_ble import cloud as cloudmod
    from xbloom_ble.recipe import Recipe
    payload = {**cloudmod.recipe_to_cloud(Recipe.from_dict(yaml.safe_load(FILTER_RECIPE))),
               "theName": "Imported From Cloud"}
    monkeypatch.setattr(cloudmod.XBloomCloud, "fetch_public", lambda self, url: payload)

    async def s(app, pilot):
        await pilot.press("i")                    # opens the import prompt
        await pilot.pause(0.05)
        await pilot.press(*"shareid", "enter")
        for _ in range(60):
            await pilot.pause(0.05)
            if any(e.name == "Imported From Cloud" for e in app._entries):
                break
        return {e.name for e in app._entries}
    assert "Imported From Cloud" in drive(store, s)


# ── history (list + detail + telemetry) ─────────────────────────────
def test_journey_history_records_with_telemetry(store, tmp_path):
    hist = HistoryStore(tmp_path / "h.json")

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        await await_brew(app, pilot)
        await pilot.pause(0.1)
        return hist.list()
    entries = drive(store, s, history=hist)
    assert len(entries) == 1
    assert entries[0]["telemetry"]["water"]           # the full curve was saved


def test_journey_history_tab_shows_list_and_detail(store, tmp_path):
    hist = HistoryStore(tmp_path / "h.json")

    async def s(app, pilot):
        await start_brew(app, pilot, "b")
        await await_brew(app, pilot)
        await pilot.pause(0.1)
        app._set_view("history")
        await pilot.pause(0.15)
        head = str(app.query_one("#hdetail-head").renderable) if hasattr(
            app.query_one("#hdetail-head"), "renderable") else "?"
        return app._view, app.query_one(HistoryList).row_count, head
    view, rows, _head = drive(store, s, history=hist)
    assert view == "history" and rows == 1


# ── activity log ────────────────────────────────────────────────────
def test_journey_activity_log_streams_brew(store):
    async def s(app, pilot):
        logs = []
        orig = app._log
        app._log = lambda m, *a, **k: (logs.append(m), orig(m, *a, **k))[1]
        await start_brew(app, pilot, "b")
        await await_brew(app, pilot)
        return logs
    logs = drive(store, s)
    joined = " · ".join(logs).lower()
    assert any("staging" in x for x in logs) and "armed" in joined and "complete" in joined


def test_journey_log_toggle(store):
    from textual.widgets import RichLog

    async def s(app, pilot):
        panel = app.query_one("#logpanel", RichLog)
        before = panel.has_class("hidden")
        await pilot.press("l")
        await pilot.pause(0.05)
        return before, panel.has_class("hidden")
    before, after = drive(store, s)
    assert before is False and after is True

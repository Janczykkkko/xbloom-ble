"""Brew history — a local record of completed brews (incl. telemetry) + a list/detail view.

Each finished brew (recipe, totals, and the full water/coffee-vs-time curve) is appended
to a JSON file. :class:`HistoryPane` shows them newest-first with a detail panel that
re-draws the saved graph for the selected brew.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static
from textual_plotext import PlotextPlot


class HistoryStore:
    """A newest-first JSON log of completed brews (with telemetry)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def list(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def record(self, *, recipe: str, dose_g: int, water_g: float, ratio: float,
               duration_s: float, telemetry: dict | None = None, at: float | None = None) -> None:
        entries = self.list()
        entries.insert(0, {
            "ts": at if at is not None else time.time(),
            "recipe": recipe,
            "dose_g": dose_g,
            "water_g": round(water_g, 1),
            "ratio": ratio,
            "duration_s": round(duration_s, 1),
            # telemetry = {"t": [...], "water": [...], "coffee": [...]}
            "telemetry": telemetry or {"t": [], "water": [], "coffee": []},
        })
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries[:500]))


class HistoryList(DataTable):
    """Past brews, newest first."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("WHEN", "RECIPE", "DOSE", "RATIO", "WATER", "TIME")

    def load(self, entries: list[dict]) -> None:
        self.clear()
        if not entries:
            self.add_row("—", Text("no brews yet — brew something!", style="dim"), "", "", "", "")
            return
        for i, e in enumerate(entries):
            when = time.strftime("%m-%d %H:%M", time.localtime(e.get("ts", 0)))
            mins, secs = divmod(int(e.get("duration_s", 0)), 60)
            self.add_row(
                when,
                Text(str(e.get("recipe", "?")), style="bold"),
                f"{e.get('dose_g', '?')} g",
                f"1:{e.get('ratio', 0):g}",
                f"{e.get('water_g', 0):g} g",
                f"{mins}:{secs:02d}",
                key=str(i),
            )


class HistoryPane(Horizontal):
    """List (left) + detail panel with the saved graph (right)."""

    DEFAULT_CSS = """
    HistoryPane { height: 1fr; }
    HistoryPane > HistoryList { width: 1fr; }
    HistoryPane > #hdetail { width: 60%; border-left: heavy $panel; }
    #hdetail-head { height: 3; padding: 1 1; }
    #hdetail-plot { height: 1fr; border: round $panel; margin: 0 1 1 1; }
    """

    def compose(self) -> ComposeResult:
        yield HistoryList()
        with Vertical(id="hdetail"):
            yield Static("Select a brew to see its curve", id="hdetail-head")
            yield PlotextPlot(id="hdetail-plot")

    def load(self, entries: list[dict]) -> None:
        self._entries = entries
        self.query_one(HistoryList).load(entries)
        self.show_detail(0)

    def show_detail(self, idx: int) -> None:
        entries = getattr(self, "_entries", [])
        head = self.query_one("#hdetail-head", Static)
        plot = self.query_one("#hdetail-plot", PlotextPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()
        if not entries or idx is None or not (0 <= idx < len(entries)):
            head.update("[dim]no brews yet[/]")
            plot.refresh()
            return
        e = entries[idx]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0)))
        head.update(
            f"[b]{e.get('recipe', '?')}[/]  {e.get('dose_g', '?')} g · 1:{e.get('ratio', 0):g} · "
            f"{e.get('water_g', 0):g} g\n[dim]{when} · {e.get('duration_s', 0):g} s[/]"
        )
        tel = e.get("telemetry") or {}
        t, water, coffee = tel.get("t", []), tel.get("water", []), tel.get("coffee", [])
        plt.title("water / coffee (g) vs time (s)")
        if t:
            plt.plot(t, water, label="water", color="cyan+", marker="braille")
            if any(coffee):
                plt.plot(t, coffee, label="coffee", color="orange", marker="braille")
        plot.refresh()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Redraw the detail graph whenever the highlighted row changes — by j/k, arrow keys,
        # OR a mouse click. Without this the graph stays stuck on the first brew (the app's
        # j/k handler only covers its own two keys; arrows and clicks bypass it entirely).
        self.show_detail(event.cursor_row)

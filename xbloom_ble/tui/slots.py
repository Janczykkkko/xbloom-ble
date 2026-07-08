"""Dial presets (A/B/C) — assign recipes to the machine's Easy-Mode slots and push.

Assignments (slot → recipe file) persist to a small JSON. Pushing programs all three
slots on the machine via the controller's ``save_slots``.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.text import Text
from textual.widgets import DataTable

SLOTS = ("A", "B", "C")


class SlotStore:
    """Persisted slot → recipe-path map."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def get(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        return {s: data.get(s, "") for s in SLOTS}

    def assign(self, slot: str, recipe_path: str) -> None:
        data = self.get()
        data[slot] = str(recipe_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))


class SlotsView(DataTable):
    """The three dial presets and what's assigned to each."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.add_columns("SLOT", "RECIPE", "DOSE", "RATIO", "GRIND")

    def load(self, assignments: dict[str, str], resolve) -> None:
        """assignments: slot→path; resolve(path) → RecipeEntry|None."""
        self.clear()
        for s in SLOTS:
            entry = resolve(assignments.get(s, ""))
            if entry and entry.ok:
                r = entry.recipe
                self.add_row(
                    Text(s, style="bold cyan"), Text(r.name, style="bold"),
                    f"{r.dose_g} g", f"1:{r.effective_ratio:g}",
                    "—" if r.no_grind else str(r.grind), key=s,
                )
            else:
                self.add_row(Text(s, style="bold cyan"),
                             Text("(empty — assign with 1/2/3)", style="dim"), "", "", "", key=s)

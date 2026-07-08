"""RecipeStore — a directory of one-recipe-per-file YAMLs.

The TUI's recipe library. Lists / loads / saves / deletes recipe files, and reports
validation status so the UI can flag broken recipes without crashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..recipe import Recipe, RecipeError


@dataclass
class RecipeEntry:
    """A recipe file + its parse/validate status (so the list can show broken ones)."""

    path: Path
    name: str
    recipe: Recipe | None       # None if it failed to parse/validate
    error: str | None           # the validation/parse message, if any

    @property
    def ok(self) -> bool:
        return self.recipe is not None

    @property
    def stem(self) -> str:
        return self.path.stem


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "recipe"


class RecipeStore:
    """Manage a directory of recipe YAML files."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory).expanduser()

    def ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[RecipeEntry]:
        """All recipe files, sorted by name; broken ones included (with .error set)."""
        entries: list[RecipeEntry] = []
        for path in sorted(self.dir.glob("*.yaml")):
            entries.append(self._load(path))
        entries.sort(key=lambda e: e.name.lower())
        return entries

    def _load(self, path: Path) -> RecipeEntry:
        try:
            data = yaml.safe_load(path.read_text()) or {}
            recipe = Recipe.from_dict(data)      # from_dict validates
            name = recipe.name or path.stem
            return RecipeEntry(path=path, name=name, recipe=recipe, error=None)
        except (RecipeError, yaml.YAMLError, KeyError, ValueError, TypeError) as exc:
            # keep the name if we can, so the UI can still show + let the user fix it
            name = ""
            try:
                name = (yaml.safe_load(path.read_text()) or {}).get("name", "")
            except Exception:
                pass
            return RecipeEntry(path=path, name=name or path.stem, recipe=None, error=str(exc))

    def load(self, path: str | Path) -> Recipe:
        return Recipe.from_dict(yaml.safe_load(Path(path).read_text()) or {})

    def save(self, recipe: Recipe, path: str | Path | None = None) -> Path:
        """Write a recipe to disk (validating first). Derives a filename from the name
        if no path is given. Returns the path written."""
        recipe.validate()
        if path is None:
            path = self.dir / f"{_slug(recipe.name)}.yaml"
        path = Path(path)
        # Recipe.to_dict() is the single serialization authority (core fields +
        # any optional brew-level metadata, in a stable, readable order).
        self.ensure()
        path.write_text(
            yaml.safe_dump(recipe.to_dict(), sort_keys=False,
                           default_flow_style=False, allow_unicode=True)
        )
        return path

    def delete(self, path: str | Path) -> None:
        Path(path).unlink(missing_ok=True)

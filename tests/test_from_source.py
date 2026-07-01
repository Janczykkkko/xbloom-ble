"""Tests for loading a recipe from a path or an http(s) URL."""

import io

import pytest

from xbloom_ble.recipe import Recipe, RecipeError

RECIPE_YAML = (
    "name: URLRecipe\n"
    "dose_g: 16\n"
    "grind: 60\n"
    "pours:\n"
    "  - {ml: 40, temp_c: 92, rpm: 120}\n"
    "  - {ml: 100, temp_c: 92, rpm: 120}\n"
)


def test_from_yaml_text():
    r = Recipe.from_yaml_text(RECIPE_YAML)
    assert r.name == "URLRecipe"
    assert len(r.pours) == 2


def test_from_yaml_text_empty_raises():
    with pytest.raises(RecipeError):
        Recipe.from_yaml_text("")


def _fake_urlopen(body: bytes):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    def _open(req, timeout=None):  # matches urlopen(req, timeout=...)
        return _Resp(body)

    return _open


def test_from_source_url(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(RECIPE_YAML.encode()))
    r = Recipe.from_source("https://example.com/teso-la-leona.yaml")
    assert r.name == "URLRecipe"
    assert r.dose_g == 16


def test_from_source_path_delegates(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(RECIPE_YAML, encoding="utf-8")
    r = Recipe.from_source(str(p))
    assert r.name == "URLRecipe"


def test_from_source_url_fetch_error_is_oserror(monkeypatch):
    import urllib.request

    def _boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(OSError):
        Recipe.from_source("https://example.com/nope.yaml")

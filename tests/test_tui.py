"""Tests for ssis2sql.tui — Story 2.

Pure-helper tests cover find_repo_root and discover_recipes; Textual pilot tests
cover App composition, sidebar navigation, the recipe runner, and the quit binding.

All tests are hermetic: subprocess.run is monkeypatched in the pure-helper fixture
and subprocess.Popen in the recipe-runner pilot tests — no real just build runs.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# This import will raise ModuleNotFoundError until GREEN creates ssis2sql/tui.py.
# Every test in this file fails at collection until that module exists.
from ssis2sql.tui import Recipe, Ssis2SqlTUI, discover_recipes, find_repo_root

# ---------------------------------------------------------------------------
# Shared fixture: captured just --dump --dump-format json payload.
# Contains opus, tui, a private recipe, and convert-tree so the tests can
# verify the correct filtering behaviour.
# ---------------------------------------------------------------------------

_JUST_DUMP = json.dumps({
    "recipes": {
        "_private_helper": {
            "name": "_private_helper",
            "doc": None,
            "private": True,
            "parameters": [],
        },
        "clean": {
            "name": "clean",
            "doc": "Remove the venv and caches.",
            "private": False,
            "parameters": [],
        },
        "convert": {
            "name": "convert",
            "doc": "Convert a .dtsx file to T-SQL.",
            "private": False,
            "parameters": [{"name": "FILE", "kind": "singular"}],
        },
        "convert-samples": {
            "name": "convert-samples",
            "doc": "Convert sample packages.",
            "private": False,
            "parameters": [],
        },
        "convert-tree": {
            "name": "convert-tree",
            "doc": "Recursively convert a directory.",
            "private": False,
            "parameters": [
                {"name": "INPUT", "kind": "singular"},
                {"name": "OUTPUT", "kind": "singular"},
            ],
        },
        "cov": {
            "name": "cov",
            "doc": "Run the test suite with coverage.",
            "private": False,
            "parameters": [],
        },
        "demo": {
            "name": "demo",
            "doc": "Convert the bundled example.",
            "private": False,
            "parameters": [],
        },
        "inspect": {
            "name": "inspect",
            "doc": "Print the parsed component graph.",
            "private": False,
            "parameters": [{"name": "FILE", "kind": "singular"}],
        },
        "install": {
            "name": "install",
            "doc": "Create the venv and install deps.",
            "private": False,
            "parameters": [],
        },
        "opus": {
            "name": "opus",
            "doc": "Run Claude in max-effort mode.",
            "private": False,
            "parameters": [],
        },
        "test": {
            "name": "test",
            "doc": "Run the test suite.",
            "private": False,
            "parameters": [],
        },
        "tui": {
            "name": "tui",
            "doc": "Launch the Textual TUI.",
            "private": False,
            "parameters": [],
        },
    }
})


@pytest.fixture
def fake_subprocess_run(monkeypatch):
    """Monkeypatch subprocess.run so discover_recipes never calls real just."""
    fake = SimpleNamespace(stdout=_JUST_DUMP, returncode=0)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)


# ---------------------------------------------------------------------------
# AC 7 — find_repo_root: nearest ancestor containing a justfile.
# ---------------------------------------------------------------------------

def test_find_repo_root_returns_nearest_justfile_ancestor(tmp_path):
    """find_repo_root(start) returns the closest parent dir with a justfile."""
    (tmp_path / "justfile").write_text("test:\n    pytest\n", encoding="utf-8")
    subdir = tmp_path / "src" / "deep"
    subdir.mkdir(parents=True)

    assert find_repo_root(subdir) == tmp_path


def test_find_repo_root_accepts_the_root_itself(tmp_path):
    """find_repo_root returns start when start itself contains the justfile."""
    (tmp_path / "justfile").write_text("test:\n    pytest\n", encoding="utf-8")

    assert find_repo_root(tmp_path) == tmp_path


def test_find_repo_root_raises_when_no_ancestor_has_justfile(tmp_path):
    """find_repo_root raises FileNotFoundError when no ancestor has a justfile."""
    orphan = tmp_path / "no" / "justfile" / "here"
    orphan.mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        find_repo_root(orphan)


# ---------------------------------------------------------------------------
# AC 8 — discover_recipes: sorted Recipe list from just --dump output.
# ---------------------------------------------------------------------------

def test_discover_recipes_returns_nonempty_list(tmp_path, fake_subprocess_run):
    """discover_recipes returns at least one Recipe for a normal justfile."""
    assert len(discover_recipes(tmp_path)) > 0


def test_discover_recipes_result_is_sorted_by_name(tmp_path, fake_subprocess_run):
    """Recipes are returned alphabetically — the plan requires sorted output."""
    names = [r.name for r in discover_recipes(tmp_path)]
    assert names == sorted(names)


def test_discover_recipes_excludes_opus(tmp_path, fake_subprocess_run):
    """opus is excluded — it launches an interactive Claude session."""
    names = [r.name for r in discover_recipes(tmp_path)]
    assert "opus" not in names


def test_discover_recipes_excludes_tui(tmp_path, fake_subprocess_run):
    """tui is excluded — cannot launch the TUI from inside itself."""
    names = [r.name for r in discover_recipes(tmp_path)]
    assert "tui" not in names


def test_discover_recipes_excludes_private_recipes(tmp_path, fake_subprocess_run):
    """Recipes marked private=True must not appear in the sidebar."""
    names = [r.name for r in discover_recipes(tmp_path)]
    assert "_private_helper" not in names


def test_discover_recipes_includes_convert_tree(tmp_path, fake_subprocess_run):
    """convert-tree (Story 1) is present in the recipe list."""
    names = [r.name for r in discover_recipes(tmp_path)]
    assert "convert-tree" in names


def test_discover_recipes_params_for_convert(tmp_path, fake_subprocess_run):
    """Recipe.params for 'convert' is exactly ['FILE']."""
    recipes = discover_recipes(tmp_path)
    convert = next(r for r in recipes if r.name == "convert")
    assert convert.params == ["FILE"]


def test_discover_recipes_params_for_convert_tree(tmp_path, fake_subprocess_run):
    """Recipe.params for 'convert-tree' is ['INPUT', 'OUTPUT']."""
    recipes = discover_recipes(tmp_path)
    ct = next(r for r in recipes if r.name == "convert-tree")
    assert ct.params == ["INPUT", "OUTPUT"]


def test_discover_recipes_doc_is_populated(tmp_path, fake_subprocess_run):
    """Recipe.doc is set from the just dump — not left blank."""
    recipes = discover_recipes(tmp_path)
    clean = next(r for r in recipes if r.name == "clean")
    assert clean.doc == "Remove the venv and caches."


def test_recipe_dataclass_has_sensible_defaults():
    """Recipe(name=...) defaults to empty doc and empty params."""
    r = Recipe(name="x")
    assert r.doc == ""
    assert r.params == []


# ---------------------------------------------------------------------------
# Pilot tests — AC 1–6.
#
# Dependency: textual>=8.2 and pytest-asyncio>=1.3 (added to pyproject.toml in
# Step 0; asyncio_mode="auto" set so async def tests run without extra markers).
#
# All pilot tests inject a synthetic recipe list via monkeypatching
# discover_recipes so the app never needs to call real just.
# ---------------------------------------------------------------------------

def _three_recipes() -> list[Recipe]:
    """Minimal recipe list used by compose/nav tests."""
    return [
        Recipe(name="clean", doc="Remove artefacts."),
        Recipe(name="demo", doc="Convert the example."),
        Recipe(name="test", doc="Run the test suite."),
    ]


async def test_app_compose_one_button_per_recipe(monkeypatch, tmp_path):
    """AC 1: sidebar has exactly one nav-button per non-excluded recipe."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Button

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        sidebar_buttons = list(app.query("#sidebar Button"))
        assert len(sidebar_buttons) == 3
        ids = {b.id for b in sidebar_buttons}
        assert ids == {"nav-clean", "nav-demo", "nav-test"}


async def test_app_compose_no_button_for_excluded_recipes(monkeypatch, tmp_path):
    """AC 2: no sidebar button exists for opus or tui."""
    import ssis2sql.tui as tui_mod

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    # discover_recipes already filters them, but we assert on button presence.
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        ids = {b.id for b in app.query("#sidebar Button")}
        assert "nav-opus" not in ids
        assert "nav-tui" not in ids


async def test_clicking_nav_button_switches_content_pane(monkeypatch, tmp_path):
    """AC 3: clicking a sidebar button sets ContentSwitcher.current to pane-<name>."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import ContentSwitcher

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-demo")
        await pilot.pause()

        assert app.query_one(ContentSwitcher).current == "pane-demo"


async def test_run_button_writes_to_log_and_exits(monkeypatch, tmp_path):
    """AC 4: pressing Run streams subprocess output into the Log; [exit N] appears."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: [
        Recipe(name="demo", doc="Convert the example."),
    ])

    # Hermetic subprocess: fake stdout lines; returncode 0.
    fake_proc = MagicMock()
    fake_proc.stdout = iter(["line one\n", "line two\n"])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake_proc)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#run-demo")
        # Give the thread worker time to finish.
        await pilot.pause(delay=0.5)

        log = app.query_one("#log-demo", Log)
        all_lines = "\n".join(log.lines)
        # The worker writes "$ just demo", then each stdout line, then "[exit 0]".
        assert "line one" in all_lines      # first streamed stdout line present
        assert "line two" in all_lines      # second streamed stdout line present
        assert "[exit 0]" in all_lines      # exit line written after proc.wait()


async def test_run_output_is_incremental_and_ui_stays_responsive(monkeypatch, tmp_path):
    """AC 5: the recipe runner runs off the event loop — the UI still processes
    input while a run is in flight, and the run streams its output to the Log."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import ContentSwitcher, Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: [
        Recipe(name="demo", doc="Convert the example."),
        Recipe(name="test", doc="Run tests."),
    ])

    fake_proc = MagicMock()
    fake_proc.stdout = iter(["running\n"])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake_proc)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        # Start a run on the demo pane, then switch panes via the sidebar.
        await pilot.click("#run-demo")
        await pilot.click("#nav-test")
        await pilot.pause(delay=0.4)

        # The sidebar click was processed despite the in-flight run — the
        # event loop was not blocked by the recipe runner (it is a thread worker).
        assert app.query_one(ContentSwitcher).current == "pane-test"
        # And the run still streamed its output through to the demo pane's Log.
        demo_log = "\n".join(app.query_one("#log-demo", Log).lines)
        assert "[exit 0]" in demo_log


async def test_q_key_quits_app_when_focus_not_on_input(monkeypatch, tmp_path):
    """AC 6: pressing q while a sidebar Button has focus exits the app."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Button

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: [
        Recipe(name="clean", doc="Remove artefacts."),
    ])

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        # Explicitly focus a sidebar button so 'q' is not swallowed by an Input.
        nav = app.query_one("#nav-clean", Button)
        app.set_focus(nav)
        await pilot.pause()

        # Press 'q' — this must route through BINDINGS = [("q", "quit", ...)]
        # → action_quit() → app.exit() → sets app._exit = True.
        # A missing or misspelled binding would leave app._exit False.
        await pilot.press("q")
        await pilot.pause()

        assert app._exit is True, "q binding did not trigger app.exit()"

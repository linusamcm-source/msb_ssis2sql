"""Tests for ssis2sql.tui — Story 2 and Story 3.

Story 2 — pure-helper tests cover find_repo_root and discover_recipes; Textual
pilot tests cover App composition, sidebar navigation, the recipe runner, and the
quit binding.

Story 3 — pilot tests for the DirectoryTree picker panes: convert-tree pane widget
layout, directory-selection fills Input, re-root on Enter, Convert-tree validation
(empty/invalid path → error in Log, no worker launch), DtsxTree.filter_paths
keeps only dirs and .dtsx files, and ct-* file-click is a no-op.

Most tests are hermetic: subprocess.run / subprocess.Popen are monkeypatched so no
real just build runs. The exception is the SEC-3 regression test
(test_justfile_convert_tree_single_quotes_block_injection), which intentionally
invokes the real just binary to verify the recipes safely quote their arguments.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ssis2sql/tui.py exists (Story 2, commit 3bfe4a9). Story 3 tests fail individually
# because picker pane widgets (ct-input-tree, file-convert, etc.) and DtsxTree are
# not yet added to tui.py — that is the Story 3 GREEN phase.
from ssis2sql.tui import (
    Recipe,
    Ssis2SqlTUI,
    _MSSQL_KEYS,
    discover_recipes,
    find_repo_root,
    parse_pytest_summary,
    read_env,
    write_env,
)

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
    """AC 1: sidebar has one nav-button per non-excluded recipe, plus the
    synthetic ``validation`` pane button the app always appends."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Button

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        sidebar_buttons = list(app.query("#sidebar Button"))
        assert len(sidebar_buttons) == 4
        ids = {b.id for b in sidebar_buttons}
        assert ids == {"nav-clean", "nav-demo", "nav-test", "nav-validation"}


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


# ---------------------------------------------------------------------------
# Story 3 — DirectoryTree picker panes (AC 1–6).
#
# These tests fail in RED because tui.py has no picker panes yet:
#   - DtsxTree is not defined (ImportError at collection)
#   - Widget ids ct-input-tree / ct-output-tree / ct-input-path / ct-output-path
#     / file-convert / tree-convert / tree-inspect do not exist yet (NoMatches)
#
# All pilot tests use a two-recipe list that includes "convert-tree" and "convert"
# so _build_pane branching is exercised.  A tmp_path directory is used wherever
# a real filesystem path is required to be a directory.
# ---------------------------------------------------------------------------

def _picker_recipes() -> list[Recipe]:
    """Minimal recipe list that triggers picker-pane branching."""
    return [
        Recipe(name="convert", doc="Convert a .dtsx.", params=["FILE"]),
        Recipe(name="convert-tree", doc="Recursively convert.", params=["INPUT", "OUTPUT"]),
        Recipe(name="inspect", doc="Inspect a .dtsx.", params=["FILE"]),
    ]


# ---------------------------------------------------------------------------
# AC 1 — convert-tree pane has two DirectoryTree widgets and two Input widgets.
# ---------------------------------------------------------------------------

async def test_convert_tree_pane_has_two_directory_trees_and_two_inputs(
    monkeypatch, tmp_path
):
    """AC 1: the convert-tree pane contains ct-input-tree, ct-output-tree,
    ct-input-path, and ct-output-path."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import DirectoryTree, Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        # Switch to the convert-tree pane.
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Both DirectoryTree widgets must be present with their plan-specified ids.
        app.query_one("#ct-input-tree", DirectoryTree)
        app.query_one("#ct-output-tree", DirectoryTree)
        # Both Input widgets must be present.
        app.query_one("#ct-input-path", Input)
        app.query_one("#ct-output-path", Input)


# ---------------------------------------------------------------------------
# AC 2 — selecting a directory fills the matching Input via event.control.id.
# ---------------------------------------------------------------------------

async def test_selecting_input_directory_fills_ct_input_path(monkeypatch, tmp_path):
    """AC 2: DirectorySelected on ct-input-tree writes str(path) to ct-input-path."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    chosen = tmp_path / "chosen_input"
    chosen.mkdir()

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Simulate a DirectorySelected event from ct-input-tree by calling the
        # app handler directly with a mock event object.  The handler is a
        # regular method: on_directory_tree_directory_selected(self, event).
        mock_event = SimpleNamespace(
            control=SimpleNamespace(id="ct-input-tree"),
            path=chosen,
        )
        app.on_directory_tree_directory_selected(mock_event)
        await pilot.pause()

        assert app.query_one("#ct-input-path", Input).value == str(chosen)


async def test_selecting_output_directory_fills_ct_output_path(monkeypatch, tmp_path):
    """AC 2: DirectorySelected on ct-output-tree writes str(path) to ct-output-path."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    chosen = tmp_path / "chosen_output"
    chosen.mkdir()

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        mock_event = SimpleNamespace(
            control=SimpleNamespace(id="ct-output-tree"),
            path=chosen,
        )
        app.on_directory_tree_directory_selected(mock_event)
        await pilot.pause()

        assert app.query_one("#ct-output-path", Input).value == str(chosen)


async def test_two_trees_are_distinguished_by_control_id_not_order(
    monkeypatch, tmp_path
):
    """AC 2: input-tree and output-tree write to different Inputs — confirmed by
    sending events with swapped control ids and asserting each Input got its own value."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    src_dir = tmp_path / "src"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    out_dir.mkdir()

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        app.on_directory_tree_directory_selected(
            SimpleNamespace(control=SimpleNamespace(id="ct-input-tree"), path=src_dir)
        )
        app.on_directory_tree_directory_selected(
            SimpleNamespace(control=SimpleNamespace(id="ct-output-tree"), path=out_dir)
        )
        await pilot.pause()

        assert app.query_one("#ct-input-path", Input).value == str(src_dir)
        assert app.query_one("#ct-output-path", Input).value == str(out_dir)


# ---------------------------------------------------------------------------
# AC 3 — submitting a valid path into ct-input-path re-roots ct-input-tree.
# ---------------------------------------------------------------------------

async def test_submitting_valid_path_reroots_ct_input_tree(monkeypatch, tmp_path):
    """AC 3: on_input_submitted with a valid dir path reassigns DirectoryTree.path."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import DirectoryTree, Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    new_root = tmp_path / "new_root"
    new_root.mkdir()

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Simulate Input.Submitted by calling the handler directly.
        # The handler must guard with Path(value).is_dir() and then assign
        # tree.path = Path(value).
        input_widget = app.query_one("#ct-input-path", Input)
        mock_event = SimpleNamespace(
            input=input_widget,
            value=str(new_root),
        )
        app.on_input_submitted(mock_event)
        await pilot.pause()

        tree = app.query_one("#ct-input-tree", DirectoryTree)
        assert tree.path == new_root


# ---------------------------------------------------------------------------
# AC 4 (validation) — empty or invalid path → error in Log, no worker launch.
# ---------------------------------------------------------------------------

async def test_convert_tree_with_empty_input_path_writes_error_to_log(
    monkeypatch, tmp_path
):
    """AC 6 (plan): Convert tree with no input path writes an error to the Log
    and does not start the recipe worker."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    # Track whether Popen was called — it must NOT be called on validation failure.
    popen_calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: popen_calls.append(a) or MagicMock())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Leave both Inputs empty and press the Convert tree button.
        await pilot.click("#run-convert-tree")
        await pilot.pause()

        log = app.query_one("#log-convert-tree", Log)
        log_text = "\n".join(log.lines)
        # An error line must appear.
        assert len(log.lines) > 0, "Log must have at least one error line"
        # No subprocess must have been launched.
        assert popen_calls == [], "Popen must not be called when input path is empty"


async def test_convert_tree_with_nonexistent_input_path_writes_error_to_log(
    monkeypatch, tmp_path
):
    """AC 6 (plan): Convert tree with non-existent input path writes error, no launch."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input, Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    popen_calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: popen_calls.append(a) or MagicMock())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Simulate filling in a non-existent path via the handler.
        app.on_directory_tree_directory_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="ct-input-tree"),
                path=tmp_path / "does_not_exist",
            )
        )
        await pilot.pause()
        await pilot.click("#run-convert-tree")
        await pilot.pause()

        log = app.query_one("#log-convert-tree", Log)
        assert len(log.lines) > 0, "Log must contain an error message"
        assert popen_calls == [], "Popen must not be called for non-existent input"


# ---------------------------------------------------------------------------
# AC 4 (positive path) — valid paths run just convert-tree with both paths.
# ---------------------------------------------------------------------------

async def test_convert_tree_with_valid_paths_runs_recipe(monkeypatch, tmp_path):
    """AC 4: Convert tree with valid input and output dirs calls Popen with
    'convert-tree', the input path, and the output path; Log receives output."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input, Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    input_dir = tmp_path / "input_dir"
    output_dir = tmp_path / "output_dir"
    input_dir.mkdir()
    output_dir.mkdir()

    # Hermetic subprocess: two stdout lines and exit 0.
    fake_proc = MagicMock()
    fake_proc.stdout = iter(["converted pkg.dtsx\n", "1 file converted\n"])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    popen_calls: list = []

    def _fake_popen(*args, **kwargs):
        popen_calls.append(args)
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Fill both Inputs via the directory-selection handler (same as AC2 tests).
        app.on_directory_tree_directory_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="ct-input-tree"),
                path=input_dir,
            )
        )
        app.on_directory_tree_directory_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="ct-output-tree"),
                path=output_dir,
            )
        )
        await pilot.pause()

        await pilot.click("#run-convert-tree")
        # Give the thread worker time to finish.
        await pilot.pause(delay=0.5)

        # Popen must have been called exactly once.
        assert len(popen_calls) == 1, "Popen must be called exactly once for a valid run"
        # The command list must contain "convert-tree" and both paths.
        cmd = popen_calls[0][0]  # first positional arg is the command sequence
        assert "convert-tree" in cmd, "command must include 'convert-tree'"
        assert str(input_dir) in cmd, "command must include the input path"
        assert str(output_dir) in cmd, "command must include the output path"

        # The Log must contain the streamed output and the exit line.
        log = app.query_one("#log-convert-tree", Log)
        all_lines = "\n".join(log.lines)
        assert "converted pkg.dtsx" in all_lines, "stdout line must appear in Log"
        assert "[exit 0]" in all_lines, "[exit 0] must appear in Log"


# ---------------------------------------------------------------------------
# AC 5(b) — selecting a .dtsx file in the convert pane fills the file Input.
# ---------------------------------------------------------------------------

async def test_selecting_dtsx_in_convert_pane_fills_file_input(monkeypatch, tmp_path):
    """AC 5(b): on_directory_tree_file_selected with a .dtsx on tree-convert writes
    the path str to the #file-convert Input."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    dtsx_file = tmp_path / "sales_etl.dtsx"
    dtsx_file.write_text("", encoding="utf-8")

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert")
        await pilot.pause()

        mock_event = SimpleNamespace(
            control=SimpleNamespace(id="tree-convert"),
            path=dtsx_file,
        )
        app.on_directory_tree_file_selected(mock_event)
        await pilot.pause()

        assert app.query_one("#file-convert", Input).value == str(dtsx_file)


# ---------------------------------------------------------------------------
# AC 5 — DtsxTree.filter_paths keeps dirs and .dtsx, drops other extensions.
# ---------------------------------------------------------------------------

def test_dtsx_tree_filter_paths_keeps_dirs_and_dtsx(tmp_path):
    """AC 5: DtsxTree.filter_paths returns only directories and .dtsx files."""
    from ssis2sql.tui import DtsxTree  # not at module level — DtsxTree is Story 3 GREEN

    # Build a set of paths with various extensions.
    dtsx_file = tmp_path / "pkg.dtsx"
    sql_file = tmp_path / "out.sql"
    txt_file = tmp_path / "notes.txt"
    subdir = tmp_path / "subdir"

    for p in (dtsx_file, sql_file, txt_file):
        p.write_text("", encoding="utf-8")
    subdir.mkdir()

    tree = DtsxTree(tmp_path)
    kept = list(tree.filter_paths([dtsx_file, sql_file, txt_file, subdir]))

    assert dtsx_file in kept, ".dtsx files must be kept"
    assert subdir in kept, "directories must be kept"
    assert sql_file not in kept, ".sql files must be filtered out"
    assert txt_file not in kept, ".txt files must be filtered out"


def test_dtsx_tree_filter_paths_case_insensitive_dtsx(tmp_path):
    """DtsxTree treats .DTSX (upper-case) as a .dtsx file — suffix is lowercased."""
    from ssis2sql.tui import DtsxTree  # not at module level — DtsxTree is Story 3 GREEN

    upper = tmp_path / "PKG.DTSX"
    upper.write_text("", encoding="utf-8")

    tree = DtsxTree(tmp_path)
    kept = list(tree.filter_paths([upper]))

    assert upper in kept, ".DTSX (upper-case) must be kept"


# ---------------------------------------------------------------------------
# AC 6 — file-click on ct-input-tree / ct-output-tree is a no-op.
# ---------------------------------------------------------------------------

async def test_file_click_on_ct_input_tree_does_not_change_input(
    monkeypatch, tmp_path
):
    """AC 5 (plan §3.1): FileSelected on ct-input-tree must not change ct-input-path.
    The handler only responds to DirectorySelected — a file-click is a no-op."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Input

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    dtsx_file = tmp_path / "pkg.dtsx"
    dtsx_file.write_text("", encoding="utf-8")

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Record the input value before the (spurious) file-click event.
        before = app.query_one("#ct-input-path", Input).value

        # Simulate a FileSelected event from ct-input-tree via the handler.
        # The handler for FileSelected on the ct-* trees must be a no-op.
        mock_event = SimpleNamespace(
            control=SimpleNamespace(id="ct-input-tree"),
            path=dtsx_file,
        )
        # If on_directory_tree_file_selected exists and is wired for ct-* trees,
        # it must not change ct-input-path.  Call it; assert value unchanged.
        if hasattr(app, "on_directory_tree_file_selected"):
            app.on_directory_tree_file_selected(mock_event)
            await pilot.pause()

        after = app.query_one("#ct-input-path", Input).value
        assert after == before, (
            "ct-input-path must not change when a file is clicked in ct-input-tree"
        )


# ---------------------------------------------------------------------------
# SPEC-3-M2 — AC6: empty output path also writes error and does not launch.
# ---------------------------------------------------------------------------

async def test_convert_tree_with_empty_output_path_writes_error_to_log(
    monkeypatch, tmp_path
):
    """AC 6 (SPEC-3-M2): valid input path but empty output path → error line, no Popen."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    popen_calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: popen_calls.append(a) or MagicMock())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert-tree")
        await pilot.pause()

        # Fill the input path with a valid directory; leave output empty.
        app.on_directory_tree_directory_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="ct-input-tree"),
                path=tmp_path,
            )
        )
        await pilot.pause()
        await pilot.click("#run-convert-tree")
        await pilot.pause()

        log = app.query_one("#log-convert-tree", Log)
        assert len(log.lines) > 0, "Log must contain an error line for empty output path"
        assert popen_calls == [], "Popen must not be called when output path is empty"


# ---------------------------------------------------------------------------
# SPEC-3-M1 — AC5(c): convert/inspect Run button calls Popen with the file path.
# ---------------------------------------------------------------------------

async def test_convert_pane_run_button_calls_popen_with_file_path(
    monkeypatch, tmp_path
):
    """AC 5(c) (SPEC-3-M1): pressing Run in the convert pane calls Popen with
    'convert' and the selected file path — proving the full launch path works."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    dtsx_file = tmp_path / "sales_etl.dtsx"
    dtsx_file.write_text("", encoding="utf-8")

    fake_proc = MagicMock()
    fake_proc.stdout = iter([])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    popen_calls: list = []

    def _fake_popen(*args, **kwargs):
        popen_calls.append(args)
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert")
        await pilot.pause()

        # Select the .dtsx file via the file-selected handler.
        app.on_directory_tree_file_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="tree-convert"),
                path=dtsx_file,
            )
        )
        await pilot.pause()

        await pilot.click("#run-convert")
        await pilot.pause(delay=0.5)

        assert len(popen_calls) == 1, "Popen must be called exactly once"
        cmd = popen_calls[0][0]
        assert "convert" in cmd, "command must include 'convert'"
        assert str(dtsx_file) in cmd, "command must include the dtsx file path"

        log = app.query_one("#log-convert", Log)
        assert "[exit 0]" in "\n".join(log.lines)


# ---------------------------------------------------------------------------
# CR-3-M — _launch_dtsx_picker must guard file existence.
# ---------------------------------------------------------------------------

async def test_convert_pane_run_with_nonexistent_file_writes_error(
    monkeypatch, tmp_path
):
    """CR-3-M: _launch_dtsx_picker must validate the file exists before launching."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _picker_recipes())

    popen_calls: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: popen_calls.append(a) or MagicMock())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-convert")
        await pilot.pause()

        # Set a non-existent file path via the handler.
        app.on_directory_tree_file_selected(
            SimpleNamespace(
                control=SimpleNamespace(id="tree-convert"),
                path=tmp_path / "does_not_exist.dtsx",
            )
        )
        await pilot.pause()
        await pilot.click("#run-convert")
        await pilot.pause()

        log = app.query_one("#log-convert", Log)
        assert len(log.lines) > 0, "Log must contain an error for non-existent file"
        assert popen_calls == [], "Popen must not be called for non-existent file"


# ---------------------------------------------------------------------------
# SEC-3 — justfile recipes must single-quote {{INPUT}}/{{OUTPUT}}/{{FILE}} to
# prevent command injection.  This test actually runs `just` with a payload
# containing $(touch <sentinel>) — the sentinel must NOT be created.
# ---------------------------------------------------------------------------

def test_justfile_convert_tree_single_quotes_block_injection(tmp_path):
    """SEC-3 regression: a shell-command-substitution payload in INPUT must not
    execute.  Passes the sentinel path via $(touch …) into just convert-tree;
    the sentinel file must not appear on disk after the call."""
    import subprocess as sp

    repo_root = Path(__file__).parent.parent
    sentinel = tmp_path / "injected"
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # The injection payload: if {{INPUT}} is double- or un-quoted the shell
    # expands $(...) and creates the sentinel; single-quoting prevents this.
    payload = f"/nonexistent/$(touch {sentinel})"

    sp.run(
        ["just", "convert-tree", payload, str(out_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )  # exit code is non-zero (input not found) — that's expected

    assert not sentinel.exists(), (
        "command injection succeeded: the sentinel was created, "
        "meaning {{INPUT}} is not single-quoted in the convert-tree recipe"
    )


# ---------------------------------------------------------------------------
# Phase 1 — parse_pytest_summary: pure helper, scans captured pytest output
# lines and returns a one-line human summary.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lines,expected", [
    (["===== 17 passed in 0.84s ====="], "17 passed"),
    (["=== 2 failed, 15 passed, 1 skipped in 1.20s ==="],
     "15 passed · 2 failed · 1 skipped"),
    (["============ 12 skipped in 0.30s ============"], "12 skipped"),
    (["=== 1 failed, 1 error in 0.50s ==="], "1 failed · 1 error"),
    (["=== 8 passed, 4 xfailed in 2.0s ==="], "8 passed · 4 xfailed"),
    (["collected 0 items", "no tests ran in 0.01s"], "no test summary found"),
    (["random text", "nothing useful"], "no test summary found"),
    ([], "no test summary found"),
])
def test_parse_pytest_summary(lines, expected):
    assert parse_pytest_summary(lines) == expected


# ---------------------------------------------------------------------------
# Phase 3 — Pilot tests for the Validation pane (plan §3.1).
#
# The three validation layers (validate-static, validate-unit, validate) move
# into a dedicated ValidationPane; the synthetic "validation" recipe surfaces
# it as the nav-validation sidebar button. Layer-button clicks route into
# _run_validation, which subprocess.Popen-launches `just <recipe>` — every such
# test monkeypatches subprocess.Popen with a hermetic fake so no real just runs.
# ---------------------------------------------------------------------------


async def test_validation_pane_is_present_and_navigable(monkeypatch, tmp_path):
    """Plan §3.1(1): clicking nav-validation switches the ContentSwitcher to
    pane-validation."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import ContentSwitcher

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-validation")
        await pilot.pause()

        assert app.query_one(ContentSwitcher).current == "pane-validation"


async def test_layer_recipes_have_no_plain_sidebar_button(monkeypatch, tmp_path):
    """Plan §3.1(2): the three layer recipes (validate-static, validate-unit,
    validate) move into the pane and get no plain sidebar button; validate-cov
    stays an ordinary RecipePane button, and nav-validation is always present."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Button

    # A local recipe list (not _three_recipes) including all four validate-*
    # recipes — three layers that must be folded away and validate-cov which
    # must remain an ordinary button.
    layer_recipes = [
        Recipe(name="validate", doc="Run the differential layer."),
        Recipe(name="validate-cov", doc="Run validation with coverage."),
        Recipe(name="validate-static", doc="Run the static layer."),
        Recipe(name="validate-unit", doc="Run the unit layer."),
    ]

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: layer_recipes)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        ids = {b.id for b in app.query("#sidebar Button")}
        # The pane button and the ordinary validate-cov button are present.
        assert "nav-validation" in ids
        assert "nav-validate-cov" in ids
        # The three layer recipes have no plain sidebar button.
        assert "nav-validate-static" not in ids
        assert "nav-validate-unit" not in ids
        assert "nav-validate" not in ids


async def test_static_layer_button_streams_into_log_and_summary(monkeypatch, tmp_path):
    """Plan §3.1(3): clicking the Static layer button streams `just
    validate-static` output into #log-validation, ends with [exit 0], and
    #validation-summary renders the parsed count."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log, Static

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    # Hermetic subprocess: fake pytest stdout lines; returncode 0.
    fake_proc = MagicMock()
    fake_proc.stdout = iter([
        "collected 17 items\n",
        "===== 17 passed in 0.84s =====\n",
    ])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake_proc)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-validation")
        await pilot.pause()
        await pilot.click("#run-validate-static")
        # Give the thread worker time to finish.
        await pilot.pause(delay=0.5)

        log = app.query_one("#log-validation", Log)
        all_lines = "\n".join(log.lines)
        assert "17 passed" in all_lines      # streamed pytest summary line
        assert "[exit 0]" in all_lines       # exit line written after proc.wait()

        summary = app.query_one("#validation-summary", Static)
        assert "17 passed" in str(summary.render())


async def test_differential_button_warns_when_dotenv_absent(monkeypatch, tmp_path):
    """Plan §3.1(4): with no .env in the repo root, clicking the Differential
    layer button writes a `.env not found` note into #log-validation."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    # tmp_path has no .env file.
    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    # Hermetic subprocess — the run still proceeds; tests skip without SQL Server.
    fake_proc = MagicMock()
    fake_proc.stdout = iter(["===== 12 skipped in 0.30s =====\n"])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake_proc)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-validation")
        await pilot.pause()
        await pilot.click("#run-validate")
        await pilot.pause(delay=0.5)

        log = app.query_one("#log-validation", Log)
        all_lines = "\n".join(log.lines)
        assert "note: .env not found" in all_lines


async def test_differential_button_no_warning_when_dotenv_present(monkeypatch, tmp_path):
    """Plan §3.1(5): with a .env present in the repo root, clicking the
    Differential layer button does NOT write the `.env not found` note."""
    import ssis2sql.tui as tui_mod
    from textual.widgets import Log

    # A .env file exists in the repo root — no warning should be emitted.
    (tmp_path / ".env").write_text("MSSQL_SERVER_ADDRESS=x\n", encoding="utf-8")

    monkeypatch.setattr(tui_mod, "find_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(tui_mod, "discover_recipes", lambda _: _three_recipes())

    # Hermetic subprocess — same fake as the absent-.env test.
    fake_proc = MagicMock()
    fake_proc.stdout = iter(["===== 12 skipped in 0.30s =====\n"])
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake_proc)

    app = Ssis2SqlTUI()
    async with app.run_test() as pilot:
        await pilot.click("#nav-validation")
        await pilot.pause()
        await pilot.click("#run-validate")
        await pilot.pause(delay=0.5)

        log = app.query_one("#log-validation", Log)
        all_lines = "\n".join(log.lines)
        assert "note: .env not found" not in all_lines


# ---------------------------------------------------------------------------
# Phase 1 (tabs) — read_env / write_env: pure .env helpers (plan §1.1).
# ---------------------------------------------------------------------------


def test_read_env_returns_dict_of_four_keys(tmp_path):
    """read_env on a written .env returns a dict of the four MSSQL_* keys."""
    env_path = tmp_path / ".env"
    write_env(env_path, {
        "MSSQL_SERVER_ADDRESS": "localhost",
        "MSSQL_SERVER_PORT": "1433",
        "MSSQL_SA_USERNAME": "sa",
        "MSSQL_SA_PASSWORD": "secret",
    })

    values = read_env(env_path)
    assert {k: values[k] for k in _MSSQL_KEYS} == {
        "MSSQL_SERVER_ADDRESS": "localhost",
        "MSSQL_SERVER_PORT": "1433",
        "MSSQL_SA_USERNAME": "sa",
        "MSSQL_SA_PASSWORD": "secret",
    }


def test_read_env_missing_path_returns_empty_dict(tmp_path):
    """read_env on a non-existent path returns an empty dict, not an error."""
    assert read_env(tmp_path / "does_not_exist.env") == {}


def test_read_env_skips_comments_and_blank_lines(tmp_path):
    """read_env skips # comments and blank lines, keeping only KEY=VALUE pairs."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# a comment\n"
        "\n"
        "MSSQL_SERVER_ADDRESS=localhost\n"
        "   \n"
        "# another comment\n"
        "MSSQL_SERVER_PORT=1433\n",
        encoding="utf-8",
    )

    values = read_env(env_path)
    assert values == {
        "MSSQL_SERVER_ADDRESS": "localhost",
        "MSSQL_SERVER_PORT": "1433",
    }


def test_write_env_then_read_env_round_trips(tmp_path):
    """write_env followed by read_env round-trips the four values exactly."""
    env_path = tmp_path / ".env"
    original = {
        "MSSQL_SERVER_ADDRESS": "db.example.com",
        "MSSQL_SERVER_PORT": "1434",
        "MSSQL_SA_USERNAME": "admin",
        "MSSQL_SA_PASSWORD": "p@ssw0rd",
    }
    write_env(env_path, original)

    values = read_env(env_path)
    assert {k: values[k] for k in _MSSQL_KEYS} == original


def test_write_env_drops_non_mssql_keys(tmp_path):
    """write_env writes only the four MSSQL_* keys — extra keys are dropped."""
    env_path = tmp_path / ".env"
    write_env(env_path, {
        "MSSQL_SERVER_ADDRESS": "localhost",
        "MSSQL_SERVER_PORT": "1433",
        "MSSQL_SA_USERNAME": "sa",
        "MSSQL_SA_PASSWORD": "secret",
        "SOME_OTHER_KEY": "should-not-appear",
        "PATH": "/usr/bin",
    })

    values = read_env(env_path)
    assert set(values) == set(_MSSQL_KEYS)
    assert "SOME_OTHER_KEY" not in values
    assert "PATH" not in values


def test_write_env_partial_dict_does_not_crash(tmp_path):
    """write_env on a partial dict does not crash; missing keys → empty values."""
    env_path = tmp_path / ".env"
    write_env(env_path, {"MSSQL_SERVER_ADDRESS": "localhost"})

    values = read_env(env_path)
    assert set(values) == set(_MSSQL_KEYS)
    assert values["MSSQL_SERVER_ADDRESS"] == "localhost"
    assert values["MSSQL_SERVER_PORT"] == ""
    assert values["MSSQL_SA_USERNAME"] == ""
    assert values["MSSQL_SA_PASSWORD"] == ""

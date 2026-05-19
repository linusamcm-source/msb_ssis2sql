"""Textual control-panel TUI for ssis2sql — launches justfile recipes."""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# Recipes that must not appear as buttons:
#  - opus: launches an interactive Claude session (cannot be captured in a Log pane)
#  - tui:  launching the TUI from inside the TUI would be recursive
_EXCLUDED_RECIPES = frozenset({"opus", "tui"})

# Validation-framework recipes that move into the dedicated ValidationPane;
# they are dropped from the auto-discovered sidebar buttons to avoid duplication.
_VALIDATION_LAYER_RECIPES = frozenset({"validate", "validate-static", "validate-unit"})

# (recipe, button label) for the three layers, in display order.
_VALIDATION_LAYERS = (
    ("validate-static", "Static"),
    ("validate-unit", "Unit"),
    ("validate", "Differential"),
)


@dataclass
class Recipe:
    """A justfile recipe with its name, doc comment, and parameter list."""

    name: str
    doc: str = ""
    params: list[str] = field(default_factory=list)


def find_repo_root(start: Path) -> Path:
    """Return the nearest ancestor of ``start`` (inclusive) containing a justfile."""
    for d in (start, *start.parents):
        if (d / "justfile").is_file():
            return d
    raise FileNotFoundError(f"no justfile found above {start}")


def discover_recipes(repo_root: Path) -> list[Recipe]:
    """Parse ``just --dump --dump-format json`` into a sorted list of Recipe objects."""
    proc = subprocess.run(
        ["just", "--dump", "--dump-format", "json"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    data = json.loads(proc.stdout)
    recipes: list[Recipe] = []
    for name, meta in data["recipes"].items():
        if meta.get("private") or name in _EXCLUDED_RECIPES:
            continue
        recipes.append(
            Recipe(
                name=name,
                doc=meta.get("doc") or "",
                params=[p["name"] for p in meta.get("parameters", [])],
            )
        )
    return sorted(recipes, key=lambda r: r.name)


# ---------------------------------------------------------------------------
# Textual app — imported here so the core CLI stays Textual-free.
# ---------------------------------------------------------------------------

from textual import work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.widgets import (  # noqa: E402
    Button,
    ContentSwitcher,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Log,
    Static,
)
from textual.worker import get_current_worker  # noqa: E402


def _slug(name: str) -> str:
    """Recipe name -> a widget-id-safe slug (hyphens are already valid CSS ids)."""
    return name


_SUMMARY_RE = re.compile(
    r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|deselected)"
)
_SUMMARY_ORDER = ["passed", "failed", "error", "skipped", "xfailed", "xpassed"]


def parse_pytest_summary(lines: list[str]) -> str:
    """Extract pytest's pass/fail/skip counts from captured output lines.

    pytest prints its tally on the final ``===`` line; later occurrences win,
    so a stray earlier number is harmless. Returns a fallback string when no
    recognisable summary is present.
    """
    counts: dict[str, int] = {}
    for line in lines:
        for number, kind in _SUMMARY_RE.findall(line):
            counts[kind.rstrip("s") if kind == "errors" else kind] = int(number)
    parts = [f"{counts[k]} {k}" for k in _SUMMARY_ORDER if k in counts]
    return " · ".join(parts) if parts else "no test summary found"


# ---------------------------------------------------------------------------
# .env helpers — read/write the MSSQL_* connection settings.
# ---------------------------------------------------------------------------

_MSSQL_KEYS = (
    "MSSQL_SERVER_ADDRESS",
    "MSSQL_SERVER_PORT",
    "MSSQL_SA_USERNAME",
    "MSSQL_SA_PASSWORD",
)


def read_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file. Missing file → empty dict.

    Blank lines and ``#`` comments are skipped; only the first ``=`` splits.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write the four MSSQL_* keys as KEY=VALUE lines (others dropped)."""
    lines = ["# MSSQL connection parameters for the validation framework."]
    lines += [f"{k}={values.get(k, '')}" for k in _MSSQL_KEYS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Story 3 — filtered DirectoryTree for .dtsx file pickers.
# ---------------------------------------------------------------------------

class DtsxTree(DirectoryTree):
    """A DirectoryTree that shows only directories and .dtsx files."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if p.is_dir() or p.suffix.lower() == ".dtsx"]


# ---------------------------------------------------------------------------
# Pane widgets.
# ---------------------------------------------------------------------------

class RecipePane(VerticalScroll):
    """Generic pane for one recipe: doc text + Run button + Log."""

    def __init__(self, recipe: Recipe) -> None:
        super().__init__(id=f"pane-{_slug(recipe.name)}")
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        yield Static(self._recipe.doc or self._recipe.name, classes="pane-desc")
        yield Button("Run", id=f"run-{_slug(self._recipe.name)}", variant="primary")
        yield Log(id=f"log-{_slug(self._recipe.name)}")


class ConvertTreePane(VerticalScroll):
    """Picker pane for convert-tree: two DirectoryTree + two Input widgets.

    Layout (top to bottom):
      Input ct-input-path  — source of truth for the input root
      DirectoryTree ct-input-tree  — browse; selecting a dir fills ct-input-path
      Input ct-output-path — source of truth for the output root
      DirectoryTree ct-output-tree — browse; selecting a dir fills ct-output-path
      Button run-convert-tree
      Log    log-convert-tree
    """

    def __init__(self, recipe: Recipe) -> None:
        super().__init__(id="pane-convert-tree")
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        yield Static(self._recipe.doc or self._recipe.name, classes="pane-desc")
        yield Input(id="ct-input-path", placeholder="Input parent directory…")
        yield DirectoryTree(Path.home(), id="ct-input-tree")
        yield Input(id="ct-output-path", placeholder="Output directory…")
        yield DirectoryTree(Path.home(), id="ct-output-tree")
        yield Button("Convert tree", id="run-convert-tree", variant="primary")
        yield Log(id="log-convert-tree")


class DtsxPickerPane(VerticalScroll):
    """Picker pane for convert/inspect: DtsxTree filtered to .dtsx files + Input."""

    def __init__(self, recipe: Recipe) -> None:
        super().__init__(id=f"pane-{_slug(recipe.name)}")
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        yield Static(self._recipe.doc or self._recipe.name, classes="pane-desc")
        yield Input(id=f"file-{_slug(self._recipe.name)}", placeholder="Path to .dtsx file…")
        yield DtsxTree(Path.home(), id=f"tree-{_slug(self._recipe.name)}")
        yield Button("Run", id=f"run-{_slug(self._recipe.name)}", variant="primary")
        yield Log(id=f"log-{_slug(self._recipe.name)}")


class ValidationPane(VerticalScroll):
    """Dedicated pane: run the validation framework's three layers."""

    def __init__(self, recipe: Recipe) -> None:
        super().__init__(id="pane-validation")
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        yield Static(self._recipe.doc or "Run the validation framework.",
                     classes="pane-desc")
        with Horizontal(id="validation-buttons"):
            for recipe, label in _VALIDATION_LAYERS:
                yield Button(label, id=f"run-{recipe}", variant="primary")
        yield Static("idle", id="validation-summary")
        yield Log(id="log-validation")


class ConfigPane(VerticalScroll):
    """Configuration pane: edit the .env MSSQL connection settings."""

    def __init__(self, recipe: Recipe, repo_root: Path) -> None:
        super().__init__(id="pane-config")
        self._recipe = recipe
        self._env_path = repo_root / ".env"

    def compose(self) -> ComposeResult:
        values = read_env(self._env_path)
        yield Static("Edit the SQL Server connection used by the differential "
                     "validation layer. Saved to .env (gitignored).",
                     classes="pane-desc")
        for key in _MSSQL_KEYS:
            yield Static(key, classes="config-label")
            yield Input(
                value=values.get(key, ""),
                id=f"cfg-{key}",
                password=key.endswith("PASSWORD"),
            )
        yield Button("Save", id="run-config-save", variant="primary")
        yield Static("", id="config-status")


# ---------------------------------------------------------------------------
# App.
# ---------------------------------------------------------------------------

class Ssis2SqlTUI(App):
    """Textual control-panel: sidebar of recipe buttons + right-hand content switcher."""

    CSS = """
    #sidebar { dock: left; width: 28; background: $panel; }
    #sidebar Button { width: 100%; margin: 0 0 1 0; }
    #content { width: 1fr; padding: 1 2; }
    .pane-desc { color: $text-muted; margin-bottom: 1; }
    Log { height: 1fr; border: round $primary; }
    #validation-buttons { height: auto; }
    #validation-buttons Button { margin: 0 1 0 0; }
    #validation-summary { margin: 1 0; color: $text-muted; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._repo_root = find_repo_root(Path(__file__).resolve())
        recipes = discover_recipes(self._repo_root)
        recipes = [r for r in recipes if r.name not in _VALIDATION_LAYER_RECIPES]
        self._recipes = [
            *recipes,
            Recipe(name="validation", doc="Run the ssis2sql validation framework."),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                for r in self._recipes:
                    yield Button(r.name, id=f"nav-{_slug(r.name)}")
            initial = f"pane-{_slug(self._recipes[0].name)}" if self._recipes else None
            with ContentSwitcher(id="content", initial=initial):
                for r in self._recipes:
                    yield self._build_pane(r)
        yield Footer()

    def _build_pane(self, recipe: Recipe) -> VerticalScroll:
        """Return the appropriate pane widget for a recipe."""
        if recipe.name == "validation":
            return ValidationPane(recipe)
        if recipe.name == "convert-tree":
            return ConvertTreePane(recipe)
        if recipe.name in ("convert", "inspect"):
            return DtsxPickerPane(recipe)
        return RecipePane(recipe)

    # ------------------------------------------------------------------
    # Button routing.
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("nav-"):
            self.query_one(ContentSwitcher).current = f"pane-{bid[len('nav-'):]}"
        elif bid == "run-convert-tree":
            self._launch_convert_tree()
        elif bid.removeprefix("run-") in _VALIDATION_LAYER_RECIPES:
            self._launch_validation(bid[len("run-"):])
        elif bid.startswith("run-"):
            recipe = bid[len("run-"):]
            if recipe in ("convert", "inspect"):
                self._launch_dtsx_picker(recipe)
            else:
                self._launch(recipe)

    # ------------------------------------------------------------------
    # Generic recipe launcher (paramless or pre-validated args).
    # ------------------------------------------------------------------

    def _launch(self, recipe: str) -> None:
        log = self.query_one(f"#log-{_slug(recipe)}", Log)
        log.clear()
        self._run_recipe(recipe, [], log)

    # ------------------------------------------------------------------
    # Validation-pane launcher — clears the log and parses a summary.
    # ------------------------------------------------------------------

    def _launch_validation(self, recipe: str) -> None:
        log = self.query_one("#log-validation", Log)
        summary = self.query_one("#validation-summary", Static)
        log.clear()
        summary.update("running…")
        if recipe == "validate" and not (self._repo_root / ".env").is_file():
            log.write_line(
                "note: .env not found — the differential layer needs a SQL Server; "
                "tests will skip without it. See README 'Validation > Configuration'."
            )
        self._run_validation(recipe, log, summary)

    # ------------------------------------------------------------------
    # convert-tree launcher — validates both inputs first.
    # ------------------------------------------------------------------

    def _launch_convert_tree(self) -> None:
        log = self.query_one("#log-convert-tree", Log)
        log.clear()
        in_path = self.query_one("#ct-input-path", Input).value.strip()
        out_path = self.query_one("#ct-output-path", Input).value.strip()
        if not in_path:
            log.write_line("error: input path is empty")
            return
        if not Path(in_path).exists():
            log.write_line(f"error: input path does not exist: {in_path}")
            return
        if not out_path:
            log.write_line("error: output path is empty")
            return
        self._run_recipe("convert-tree", [in_path, out_path], log)

    # ------------------------------------------------------------------
    # DtsxTree pane launcher (convert / inspect).
    # ------------------------------------------------------------------

    def _launch_dtsx_picker(self, recipe: str) -> None:
        log = self.query_one(f"#log-{_slug(recipe)}", Log)
        log.clear()
        file_path = self.query_one(f"#file-{_slug(recipe)}", Input).value.strip()
        if not file_path:
            log.write_line(f"error: no file selected for {recipe}")
            return
        if not Path(file_path).is_file():
            log.write_line(f"error: file does not exist: {file_path}")
            return
        self._run_recipe(recipe, [file_path], log)

    # ------------------------------------------------------------------
    # Thread worker — all widget mutations via call_from_thread.
    # ------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="recipe-run")
    def _run_recipe(self, recipe: str, args: list[str], log: Log) -> int:
        worker = get_current_worker()
        cmd = ["just", recipe, *args]
        self.call_from_thread(log.write_line, f"$ {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, cwd=self._repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if worker.is_cancelled:
                proc.terminate()
                break
            self.call_from_thread(log.write_line, line.rstrip("\n"))
        proc.wait()
        self.call_from_thread(log.write_line, f"[exit {proc.returncode}]")
        return proc.returncode

    @work(thread=True, exclusive=True, group="recipe-run")
    def _run_validation(self, recipe: str, log: Log, summary: Static) -> int:
        worker = get_current_worker()
        cmd = ["just", recipe]
        self.call_from_thread(log.write_line, f"$ {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd, cwd=self._repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        lines: list[str] = []
        for line in proc.stdout:
            if worker.is_cancelled:
                proc.terminate()
                break
            text = line.rstrip("\n")
            lines.append(text)
            self.call_from_thread(log.write_line, text)
        proc.wait()
        self.call_from_thread(log.write_line, f"[exit {proc.returncode}]")
        self.call_from_thread(summary.update, parse_pytest_summary(lines))
        return proc.returncode

    # ------------------------------------------------------------------
    # Story 3 event handlers.
    # ------------------------------------------------------------------

    def on_directory_tree_directory_selected(self, event) -> None:
        """Fill the matching Input when a directory is selected in a picker tree."""
        target_id = {
            "ct-input-tree": "ct-input-path",
            "ct-output-tree": "ct-output-path",
        }.get(event.control.id)
        if target_id:
            self.query_one(f"#{target_id}", Input).value = str(event.path)

    def on_directory_tree_file_selected(self, event) -> None:
        """Fill the file Input when a .dtsx is selected in a DtsxTree picker."""
        # ct-* trees are directory pickers — file clicks on them are no-ops.
        tree_to_input = {
            "tree-convert": "file-convert",
            "tree-inspect": "file-inspect",
        }
        target_id = tree_to_input.get(event.control.id)
        if target_id:
            self.query_one(f"#{target_id}", Input).value = str(event.path)

    def on_input_submitted(self, event) -> None:
        """Re-root the matching DirectoryTree when the user types a path and presses Enter."""
        input_to_tree = {
            "ct-input-path": "ct-input-tree",
            "ct-output-path": "ct-output-tree",
        }
        widget_id = event.input.id
        tree_id = input_to_tree.get(widget_id)
        if tree_id and Path(event.value).is_dir():
            self.query_one(f"#{tree_id}", DirectoryTree).path = Path(event.value)


def main() -> None:
    """Entry point: launch the Ssis2SqlTUI app."""
    Ssis2SqlTUI().run()


if __name__ == "__main__":
    main()

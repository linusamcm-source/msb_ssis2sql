"""Textual control-panel TUI for ssis2sql — launches justfile recipes."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Recipes that must not appear as buttons:
#  - opus: launches an interactive Claude session (cannot be captured in a Log pane)
#  - tui:  launching the TUI from inside the TUI would be recursive
_EXCLUDED_RECIPES = frozenset({"opus", "tui"})


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
from textual.widgets import Button, ContentSwitcher, Footer, Header, Log, Static  # noqa: E402
from textual.worker import get_current_worker  # noqa: E402


def _slug(name: str) -> str:
    """Recipe name -> a widget-id-safe slug (hyphens are already valid CSS ids)."""
    return name


class RecipePane(VerticalScroll):
    """Generic pane for one recipe: doc text + Run button + Log."""

    def __init__(self, recipe: Recipe) -> None:
        super().__init__(id=f"pane-{_slug(recipe.name)}")
        self._recipe = recipe

    def compose(self) -> ComposeResult:
        yield Static(self._recipe.doc or self._recipe.name, classes="pane-desc")
        yield Button("Run", id=f"run-{_slug(self._recipe.name)}", variant="primary")
        yield Log(id=f"log-{_slug(self._recipe.name)}")


class Ssis2SqlTUI(App):
    """Textual control-panel: sidebar of recipe buttons + right-hand content switcher."""

    CSS = """
    #sidebar { dock: left; width: 28; background: $panel; }
    #sidebar Button { width: 100%; margin: 0 0 1 0; }
    #content { width: 1fr; padding: 1 2; }
    .pane-desc { color: $text-muted; margin-bottom: 1; }
    Log { height: 1fr; border: round $primary; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._repo_root = find_repo_root(Path(__file__).resolve())
        self._recipes = discover_recipes(self._repo_root)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                for r in self._recipes:
                    yield Button(r.name, id=f"nav-{_slug(r.name)}")
            initial = f"pane-{_slug(self._recipes[0].name)}" if self._recipes else None
            with ContentSwitcher(id="content", initial=initial):
                for r in self._recipes:
                    yield RecipePane(r)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("nav-"):
            self.query_one(ContentSwitcher).current = f"pane-{bid[len('nav-'):]}"
        elif bid.startswith("run-"):
            recipe = bid[len("run-"):]
            self._launch(recipe)

    def _launch(self, recipe: str) -> None:
        log = self.query_one(f"#log-{_slug(recipe)}", Log)
        log.clear()
        self._run_recipe(recipe, [], log)

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


def main() -> None:
    """Entry point: launch the Ssis2SqlTUI app."""
    Ssis2SqlTUI().run()


if __name__ == "__main__":
    main()

# Plan — Recursive DTSX→SQL Batch Converter + Textual Control-Panel TUI

**Status:** Ready to execute. **Created:** 2026-05-19. **Repo:** `/Users/linus/Development/ssis`

## Goal

1. Add a recursive batch converter: the user picks a **parent directory**; every `.dtsx`
   beneath it (any depth) is converted to `.sql`, written to a user-chosen **output
   directory**, with the output tree mirroring the input tree. All path handling uses
   `pathlib.Path` so it is platform-agnostic.
2. Expose that as a **`just` recipe** (`convert-tree INPUT OUTPUT`).
3. Build a **Textual TUI** that acts as a control panel for the whole `justfile`: a
   left-hand sidebar of buttons, one per recipe, each switching a content pane on the
   right. The `convert-tree` pane uses `DirectoryTree` widgets to pick the input and
   output paths. Every pane runs its recipe as a subprocess and streams the output into
   a log.

## Architecture decisions (already made — do not re-litigate)

| Decision | Choice | Reason |
|---|---|---|
| Recursive walk | Python `Path.rglob("*.dtsx")` in a new `ssis2sql/batch.py` module | User requirement: platform-agnostic `pathlib`. Bash `find` (as in `convert-samples`) is **not** allowed here. |
| How `convert-tree` is invoked | New `ssis2sql` CLI subcommand `convert-tree`, wrapped by a thin `just` recipe | Matches existing `convert`/`inspect` pattern; keeps logic testable in Python. |
| Conversion engine | Reuse `ssis2sql.generator.convert_file()` — **do not** write a new converter | Converter already exists and is tested. |
| TUI ↔ recipe coupling | TUI shells out to `just <recipe> [args]` as a subprocess | User requirement: "a justfile command that is called by the Textual UI". The TUI is a `just` launcher. |
| Left-hand tabs | `ContentSwitcher` + a `Vertical` of `Button`s docked left (`dock: left`) | **Textual has no left-side tab placement** — verified, see Phase 0. `TabbedContent` is top-docked only. |
| TUI location | New standalone module `ssis2sql/tui.py`, run via `python -m ssis2sql.tui` | Keeps the `textual` import out of the core `ssis2sql` CLI. |
| TUI CSS | Inline `CSS` class attribute (not `CSS_PATH`) | Avoids shipping a `.tcss` data file with the package. |
| Recipe discovery | Parse `just --dump --dump-format json` at startup | Buttons stay in sync with the `justfile` automatically. |
| `textual` dependency | Add to `[project] dependencies` in `pyproject.toml` | So both `just install` and `just tui` work with no extra flags. |
| Recipes excluded from TUI buttons | `opus` (interactive Claude session), `tui` (cannot launch the TUI from inside itself) | The rest become buttons. |
| Output log widget | `Log` (not `RichLog`) | `Log.write_line` is simple and reliable for streaming plain subprocess text; `RichLog` defers writes until its size is known. |

## Phases

- **Phase 0** — Documentation discovery (done; consolidated below).
- **Phase 1** — `ssis2sql/batch.py` + `convert-tree` CLI subcommand + `just convert-tree` recipe + tests. *No Textual. Independently shippable.*
- **Phase 2** — Textual TUI scaffold: app, left sidebar, `ContentSwitcher`, generic recipe-runner panes, subprocess worker, `just tui` recipe.
- **Phase 3** — `DirectoryTree` picker panes for `convert-tree`, `convert`, `inspect`.
- **Phase 4** — Verification.

Each phase is self-contained: it lists the files, the patterns to copy (with citations),
a verification checklist, and anti-pattern guards.

---

# Phase 0 — Documentation Discovery (consolidated)

Two reference sources were generated for this plan. **Read the cited ranges before
writing code in later phases.**

- `.repomix-output.xml` — pack of *this* repo (`ssis2sql`).
- `.repomix-textual.xml` — pack of the Textual framework (`github.com/Textualize/textual`,
  5.8 MB, 855 files). Generated with:
  ```
  npx --yes repomix --remote https://github.com/Textualize/textual \
    --include "docs/**,src/textual/widgets/_directory_tree.py,src/textual/widgets/_tabbed_content.py,src/textual/widgets/_tabs.py,src/textual/widgets/_content_switcher.py,src/textual/widgets/_button.py,src/textual/widgets/_input.py,src/textual/widgets/_log.py,src/textual/widgets/_rich_log.py,src/textual/app.py,src/textual/worker.py,src/textual/_work_decorator.py,src/textual/containers.py" \
    --output .repomix-textual.xml --style xml
  ```
  It is large — grep / line-offset Read it, never read it whole.

## Allowed APIs — `ssis2sql` (this repo)

| Symbol | Location | Signature / fields |
|---|---|---|
| `convert_file` | `ssis2sql/generator.py:49` | `convert_file(path: str \| pathlib.Path, options: ConvertOptions \| None = None) -> ConversionResult` |
| `ConvertOptions` | `ssis2sql/generator.py:25` | dataclass: `wrap_in_procedure: bool=False`, `procedure_name: str="usp_Migrated_Package"`, `include_header: bool=True` |
| `ConversionResult` | `ssis2sql/generator.py:34` | dataclass: `sql: str`, `warnings: list[str]`, `package: Package \| None`. `__str__` returns `.sql`. **These are the only three fields — do not invent others.** |
| `parse_file` | `ssis2sql/parser.py:99` | `parse_file(path: str \| pathlib.Path) -> Package` |
| `@logged`, `logger` | `ssis2sql/observability.py` | decorator + loguru logger; existing modules use it on public functions |
| `Ssis2SqlError` | `ssis2sql/errors.py` | base exception; CLI `main()` catches `Ssis2SqlError` **and** `OSError` and exits 2 |
| CLI argparse setup | `ssis2sql/cli.py:14-45` | `convert` subparser to copy from; shared `-v/-vv`, `-o/--output`, `--procedure`, `--no-header` flags live here |
| pytest fixtures | `conftest.py` | `example_path` → `examples/sales_etl.dtsx`; `example_package` → parsed `Package` |

CLI invocation: `python -m ssis2sql convert <dtsx> -o <out>`. Console-script `ssis2sql`
registered at `pyproject.toml` `[project.scripts]`.

`just --dump --dump-format json` output shape (verified by running it):
```json
{"recipes": {"<name>": {"name":"...", "doc":"... or null", "private":false,
  "parameters":[{"name":"FILE","default":null,"kind":"singular", ...}], ...}}}
```
Current recipes: `clean convert convert-samples cov demo inspect install opus test`.

## Allowed APIs — Textual (cite `.repomix-textual.xml`)

| Need | API | XML location |
|---|---|---|
| App base class | `App` — class vars `CSS` (53717), `CSS_PATH` (53828), `BINDINGS` (53872); `compose(self) -> ComposeResult` (54811); `run()` (55726) | `src/textual/app.py` |
| Thread→UI bridge | `App.call_from_thread(callback, *args, **kwargs)` — **mandatory** for any widget mutation from inside a thread worker | `src/textual/app.py:55206` |
| DirectoryTree | `DirectoryTree(path: str \| Path, *, name=, id=, classes=, disabled=)` — `path` is first positional | `src/textual/widgets/_directory_tree.py:49416` |
| DirectoryTree file event | `DirectoryTree.FileSelected` — attrs `.path: Path`, `.node`, `.control`. Handler `on_directory_tree_file_selected` | `_directory_tree.py:49358` |
| DirectoryTree dir event | `DirectoryTree.DirectorySelected` — same attrs. Handler `on_directory_tree_directory_selected` | `_directory_tree.py:49383` |
| Re-root a DirectoryTree | `path` is a reactive var — assigning `tree.path = new_path` re-roots & reloads (`watch_path`) | `_directory_tree.py:49408,49618` |
| Filter shown entries | subclass `DirectoryTree`, override `filter_paths(self, paths) -> Iterable[Path]` | example `docs/examples/widgets/directory_tree_filtered.py` (XML 24938) |
| ContentSwitcher | `ContentSwitcher(*children, name=, id=, classes=, disabled=, initial: str \| None=None)` — every child needs a unique `id`; `current` reactive switches the visible child | `src/textual/widgets/_content_switcher.py:49163,49153` |
| Sidebar-nav pattern | Button `id` == content-panel `id`; handler does `self.query_one(ContentSwitcher).current = event.button.id` | example `docs/examples/widgets/content_switcher.py` (XML 24544) |
| Button | `Button(label=None, variant="default", *, id=, classes=, disabled=, ...)`; variants `default/primary/success/warning/error` | `src/textual/widgets/_button.py:48893` |
| Button event | `Button.Pressed` — attr `.button`. Handler `on_button_pressed(self, event: Button.Pressed)` | `_button.py:48873` |
| Input | `Input(value=None, placeholder="", *, id=, ...)` | `src/textual/widgets/_input.py:50191` |
| Input event | `Input.Submitted` (on Enter) — attrs `.input`, `.value`. Handler `on_input_submitted` | `_input.py:50147` |
| Log | `Log(highlight=False, max_lines=None, auto_scroll=True, *, id=, ...)`; methods `write_line(str)`, `write(str)`, `clear()` | `src/textual/widgets/_log.py` (constructor ~50020) |
| Worker decorator | `@work(*, name="", group="default", exclusive=False, exit_on_error=True, thread=False, description=None)` — `from textual import work` | `src/textual/_work_decorator.py:53331` |
| Worker introspection | `get_current_worker()` → `Worker`; check `.is_cancelled` to bail early | `src/textual/worker.py:58840` |
| Worker state event | `Worker.StateChanged`; handler `on_worker_state_changed`; `WorkerState` enum PENDING/RUNNING/CANCELLED/ERROR/SUCCESS | `src/textual/worker.py:58898,58857` |
| Containers | `Horizontal`, `Vertical`, `VerticalScroll` — from `textual.containers` | `src/textual/containers.py:58620,58582,58608` |
| Left sidebar | CSS `dock: left; width: <n>;` on the sidebar container | example `docs/examples/guide/layout/dock_layout3_sidebar_header.{py,tcss}` (XML 15136/15162) |

**Copy-ready Textual examples** (inside `.repomix-textual.xml`):
- `docs/examples/widgets/content_switcher.py` (XML 24544) + `.tcss` (24612) — **the left-tab pattern.**
- `docs/examples/widgets/directory_tree.py` (XML 24961); filtered variant (XML 24938).
- `docs/examples/widgets/button.py` (XML 24306) + `.tcss` (24360).
- `docs/examples/widgets/input.py` (XML 25175); `log.py` (XML 25428).
- `docs/examples/guide/workers/weather05.py` (XML 18524) — thread worker + `call_from_thread`.
- `docs/examples/guide/workers/weather04.py` (XML 18474) — `on_worker_state_changed`.
- `src/textual/widgets/_directory_tree.py` `_load_directory` (XML 49769) — real `@work(thread=True)` doing blocking I/O.
- `docs/examples/guide/layout/dock_layout3_sidebar_header.{py,tcss}` (XML 15136/15162) — `dock: left` sidebar.

## Anti-patterns — do NOT do these

1. **No left-side tab CSS.** `tab-placement` does not exist in Textual (triple-verified:
   exact grep = 0 hits; case-insensitive = 4 unrelated prose hits; no `placement` CSS
   type). `TabbedContent` hard-codes `dock: top`. Use `ContentSwitcher` + `dock: left`.
2. **Never mutate a widget from inside a thread worker directly.** Wrap every UI call
   (`log.write_line(...)`, button enable/disable) in `self.call_from_thread(...)`.
3. **A non-async worker function must be declared `@work(thread=True)`.** A plain `def`
   under `@work` without `thread=True` raises `WorkerDeclarationError`.
4. **Do not assume `run_worker`'s signature** — it was not in the packed XML. Use the
   `@work` decorator (fully verified) instead.
5. **Do not invent `ConversionResult` fields.** It has exactly `.sql`, `.warnings`,
   `.package`.
6. **Do not use bash `find` for the recursive walk.** Must be `pathlib` `rglob` —
   platform-agnostic is an explicit requirement.
7. **Do not write conversion logic.** Reuse `convert_file()`.
8. **`RichLog.write` is deferred before the widget is sized** — this plan uses `Log`
   instead, so do not substitute `RichLog` without also moving writes to `on_ready`.

---

# Phase 1 — Recursive batch converter (no Textual)

**Goal:** `just convert-tree INPUT OUTPUT` recursively converts every `.dtsx` under
`INPUT` into `.sql` under `OUTPUT`, mirroring the directory structure, using `pathlib`.

## Files

- **New:** `ssis2sql/batch.py`
- **New:** `tests/test_batch.py`
- **Edit:** `ssis2sql/cli.py` — add a `convert-tree` subcommand
- **Edit:** `justfile` — add a `convert-tree` recipe
- **Edit:** `tests/test_cli.py` — add a `convert-tree` CLI test

## What to implement

### 1.1 `ssis2sql/batch.py`

Before writing: Read `ssis2sql/generator.py:1-60` (for `convert_file`, `ConvertOptions`,
`ConversionResult`) and `ssis2sql/observability.py` (for the `@logged` decorator usage,
as applied in `generator.py:48`).

Skeleton to adapt:

```python
"""Recursively convert a directory tree of .dtsx packages into mirrored .sql files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .generator import ConversionResult, ConvertOptions, convert_file
from .observability import logged, logger

# Directories that hold Visual Studio build copies, not source packages.
# (Mirrors the `-not -path '*/bin/*'` filter in the `convert-samples` recipe.)
_SKIP_DIRS = frozenset({"bin", "obj"})


@dataclass
class FileOutcome:
    source: Path
    destination: Path
    ok: bool
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchResult:
    outcomes: list[FileOutcome] = field(default_factory=list)

    @property
    def converted(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


@logged
def convert_tree(
    input_root: str | Path,
    output_root: str | Path,
    options: ConvertOptions | None = None,
) -> BatchResult:
    """Convert every .dtsx under ``input_root`` into ``output_root``, mirroring the tree.

    Each ``<input_root>/<rel>/<name>.dtsx`` becomes ``<output_root>/<rel>/<name>.sql``.
    A failure on one package is recorded and does not stop the run.
    """
    input_root = Path(input_root)
    output_root = Path(output_root)
    if not input_root.is_dir():
        raise NotADirectoryError(f"input is not a directory: {input_root}")

    result = BatchResult()
    for src in sorted(input_root.rglob("*.dtsx")):
        rel = src.relative_to(input_root)
        if _SKIP_DIRS.intersection(rel.parts):
            continue
        dst = output_root / rel.with_suffix(".sql")
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            conversion: ConversionResult = convert_file(src, options)
            dst.write_text(conversion.sql, encoding="utf-8")
            result.outcomes.append(
                FileOutcome(src, dst, ok=True, warnings=list(conversion.warnings))
            )
            logger.info("converted {} -> {}", rel, dst)
        except Exception as exc:  # noqa: BLE001 - one bad package must not abort the run
            result.outcomes.append(FileOutcome(src, dst, ok=False, error=str(exc)))
            logger.warning("failed to convert {}: {}", rel, exc)
    return result
```

Notes:
- `rglob`, `relative_to`, `/`, `with_suffix`, `mkdir` are all `pathlib` — platform-agnostic. ✔
- `NotADirectoryError` is an `OSError`, already caught by `cli.py`'s `main()`.
- Skip-dir check uses `rel.parts` so `examples/.../bin/...` packages are excluded — this
  matches the existing `convert-samples` behaviour.

### 1.2 `convert-tree` CLI subcommand — `ssis2sql/cli.py`

Read `ssis2sql/cli.py` fully first. **Copy the structure of the existing `convert`
subparser (`cli.py:27-39`) and its handler `_cmd_convert` (`cli.py:48-67`)** — do not
invent a new CLI style.

- Add `from .batch import convert_tree` to the imports (alongside `cli.py:9`).
- Add a third subparser:
  ```python
  tree = sub.add_parser(
      "convert-tree",
      help="Recursively convert a directory of .dtsx files into mirrored .sql files.",
  )
  tree.add_argument("input", help="Parent directory scanned recursively for .dtsx files.")
  tree.add_argument("output", help="Directory the mirrored .sql tree is written into.")
  tree.add_argument("--procedure", metavar="NAME", help="Wrap each script in a stored procedure.")
  tree.add_argument("--no-header", action="store_true", help="Omit the generated header.")
  tree.add_argument("-v", "--verbose", action="count", default=0)
  ```
- Add `_cmd_convert_tree(args)` modelled on `_cmd_convert`: build `ConvertOptions` the
  same way `_cmd_convert` does (`cli.py:48-67`), call
  `convert_tree(Path(args.input), Path(args.output), options)`, print one line per file
  plus a final summary (`converted N, failed M`), and **return exit code 1 if
  `result.failed > 0`, else 0**.
- Route `"convert-tree"` to `_cmd_convert_tree` in `main()`'s dispatch.

### 1.3 `justfile` recipe

Add after the `convert-samples` recipe:
```
# Recursively convert every .dtsx under INPUT into OUTPUT, mirroring the input tree.
# Usage: just convert-tree path/to/input path/to/output
convert-tree INPUT OUTPUT:
    .venv/bin/python -m ssis2sql convert-tree {{INPUT}} {{OUTPUT}}
```

### 1.4 Tests — `tests/test_batch.py`

Use `tmp_path` and the `examples/` packages as known-good inputs (the `conftest.py`
`example_path` fixture points at `examples/sales_etl.dtsx`). Cover:
- **Mirroring:** build a nested input dir (`a/b/pkg.dtsx`), run `convert_tree`, assert
  `output/a/b/pkg.sql` exists and is non-empty.
- **Skip dirs:** a `.dtsx` under a `bin/` subdir is not converted.
- **Bad input:** `convert_tree("/no/such/dir", out)` raises `NotADirectoryError`.
- **Empty tree:** an input dir with no `.dtsx` returns `BatchResult` with `converted == 0`.
- **Failure isolation:** an invalid `.dtsx` (write garbage XML) is recorded as a failed
  `FileOutcome` while a sibling valid package still converts.

Add one `convert-tree` test to `tests/test_cli.py` mirroring the existing CLI tests.

## Verification checklist — Phase 1

- [ ] `just install` then `just test` — all tests pass, including `test_batch.py`.
- [ ] `just convert-tree examples /tmp/ssis-out` runs; `/tmp/ssis-out` mirrors the
      `examples/` tree with `.sql` files; the summary line reports the count.
- [ ] Output directory structure exactly mirrors input subdirectories.
- [ ] `python -m py_compile ssis2sql/batch.py ssis2sql/cli.py` — no syntax errors.
- [ ] `just cov` — coverage of `ssis2sql/batch.py` is reported and not noticeably
      dragging the overall number down.

## Anti-pattern guards — Phase 1

- `grep -n "find " justfile` — the new `convert-tree` recipe must **not** use bash
  `find`; the recursion is in `batch.py`.
- `grep -n "\.sql\b" ssis2sql/batch.py` — output paths come from `with_suffix(".sql")`,
  not string concatenation.
- No new conversion logic — `grep -n "convert_file" ssis2sql/batch.py` must show
  `batch.py` *calling* `convert_file`, not reimplementing it.
- `convert_tree` must accept `str | Path` and coerce with `Path(...)`.

---

# Phase 2 — Textual TUI scaffold + generic recipe runner

**Goal:** `just tui` launches a Textual app: a left sidebar of buttons (one per
non-excluded `justfile` recipe) and a right-hand `ContentSwitcher`. Each pane shows the
recipe's doc, a **Run** button, and a `Log`. Pressing **Run** executes `just <recipe>`
as a subprocess and streams its output into the `Log`. This phase handles the
**parameter-less** recipes (`install`, `test`, `cov`, `demo`, `convert-samples`,
`clean`); argful recipes (`convert`, `inspect`, `convert-tree`) get a basic
text-`Input` fallback now and proper pickers in Phase 3.

## Files

- **Edit:** `pyproject.toml` — add `textual` to `[project] dependencies`
- **New:** `ssis2sql/tui.py`
- **Edit:** `justfile` — add a `tui` recipe
- **New (optional):** `tests/test_tui.py` — unit tests for the pure helper functions

## What to implement

### 2.1 Add the dependency — `pyproject.toml`

Change line 14 from `dependencies = ["loguru>=0.7"]` to include `textual`. Run
`pip install textual` inside `.venv`, then `pip show textual` to read the installed
version, and pin to that minor, e.g.:
```toml
dependencies = ["loguru>=0.7", "textual>=1.0"]
```
Then re-run `just install`. (All Textual APIs cited here are stable well before 1.0, so
any modern release works — pin to whatever `pip` resolves.)

### 2.2 `ssis2sql/tui.py`

Read these from `.repomix-textual.xml` before writing:
- `docs/examples/widgets/content_switcher.py` (XML 24544) + `.tcss` (24612) — the
  sidebar-nav pattern: button `id` matches panel `id`.
- `docs/examples/guide/layout/dock_layout3_sidebar_header.tcss` (XML 15162) — `dock: left`.
- `docs/examples/guide/workers/weather05.py` (XML 18524) — `@work(thread=True)` +
  `call_from_thread`.
- `src/textual/widgets/_directory_tree.py:49769` — a real `@work(thread=True)` worker.

**Pure helper functions** (keep these module-level and side-effect-free so they can be
unit-tested without launching the app):

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Recipes that must not appear as buttons:
#  - opus: launches an interactive Claude session (cannot be captured in a Log pane)
#  - tui:  launching the TUI from inside the TUI
_EXCLUDED_RECIPES = frozenset({"opus", "tui"})


@dataclass
class Recipe:
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
```

**The App** — structure (adapt; do not treat as final code):

```python
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Log, Static
from textual.widgets import ContentSwitcher
from textual.worker import get_current_worker


def _slug(name: str) -> str:
    """justfile recipe name -> a widget-id-safe slug (hyphens are already valid ids)."""
    return name


class Ssis2SqlTUI(App):
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
                    yield self._build_pane(r)
        yield Footer()

    def _build_pane(self, recipe: Recipe):
        # Phase 2: generic pane. Phase 3 replaces the panes for
        # convert / inspect / convert-tree with DirectoryTree pickers.
        pane = VerticalScroll(id=f"pane-{_slug(recipe.name)}")
        # Children are mounted in on_mount or composed via a custom widget;
        # simplest: yield a custom container. See note below.
        ...

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
        # Phase 3: collect args from the pane's Input/DirectoryTree widgets.
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
    Ssis2SqlTUI().run()


if __name__ == "__main__":
    main()
```

Implementation notes:
- **`compose` cannot easily `yield` into a child built by a helper that itself needs to
  `yield`.** Cleanest: make each pane a small custom `Widget`/`Container` subclass with
  its own `compose`, or build panes with explicit `with VerticalScroll(...):` blocks
  inside the app's `compose`. Pick one approach and apply it uniformly. Each pane must
  contain a `Static` (the recipe `doc`, class `pane-desc`), a
  `Button("Run", id=f"run-{name}", variant="primary")`, and a `Log(id=f"log-{name}")`.
- Recipe names with hyphens (`convert-samples`, `convert-tree`) are valid Textual `id`s.
- `exclusive=True, group="recipe-run"` means starting a new run cancels any in-flight
  run — acceptable; optionally also disable Run buttons while a worker is RUNNING using
  an `on_worker_state_changed` handler (pattern: `weather04.py`, XML 18474).
- For argful recipes in this phase, add a plain `Input(id=f"arg-{name}")` to the pane
  and pass `[input.value]` (or split on spaces) as `args`. Phase 3 supersedes this.

### 2.3 `justfile` recipe

```
# Launch the Textual control-panel UI for ssis2sql.
tui:
    .venv/bin/python -m ssis2sql.tui
```

### 2.4 Optional tests — `tests/test_tui.py`

Unit-test the pure helpers (no app, no async needed):
- `find_repo_root(Path(__file__))` returns the repo root (the dir with `justfile`).
- `discover_recipes(repo_root)` returns a non-empty list, **excludes `opus` and `tui`**,
  and includes `convert-tree` once Phase 1 is merged.
- Feed `discover_recipes` a captured `just --dump --dump-format json` string (monkeypatch
  `subprocess.run`) and assert the `Recipe.params` list for `convert` is `["FILE"]`.

Full Textual pilot tests (`App.run_test()`) are async and would need `pytest-asyncio`;
treat them as an optional stretch, not a phase gate.

## Verification checklist — Phase 2

- [ ] `just install` succeeds and installs `textual` (`pip show textual` confirms).
- [ ] `just tui` launches; the sidebar shows one button per recipe **except** `opus`
      and `tui`.
- [ ] Clicking a sidebar button switches the right-hand pane (`ContentSwitcher.current`).
- [ ] In the `demo` pane, **Run** streams `ssis2sql` output into the `Log` and ends with
      an `[exit 0]` line.
- [ ] In the `test` pane, **Run** streams pytest output live (UI does not freeze).
- [ ] `q` quits.
- [ ] `python -m py_compile ssis2sql/tui.py` — no syntax errors.
- [ ] `just test` still green (no regressions).

## Anti-pattern guards — Phase 2

- `grep -n "tab-placement\|TabbedContent" ssis2sql/tui.py` — must be **empty**; the
  left tabs are `ContentSwitcher` + `dock: left`.
- `grep -n "call_from_thread" ssis2sql/tui.py` — every `log.write_line` / widget
  mutation inside `_run_recipe` must go through `call_from_thread`.
- `grep -n "@work" ssis2sql/tui.py` — `_run_recipe` must be `@work(thread=True, ...)`.
- `grep -n "RichLog" ssis2sql/tui.py` — must be empty (this plan uses `Log`).
- The core CLI must stay Textual-free: `grep -rn "import textual" ssis2sql/cli.py
  ssis2sql/__init__.py ssis2sql/generator.py` — must be empty.

---

# Phase 3 — DirectoryTree picker panes

**Goal:** Replace the generic panes for `convert-tree`, `convert`, and `inspect` with
`DirectoryTree`-driven panes so the user picks paths visually.

## Files

- **Edit:** `ssis2sql/tui.py`

## What to implement

Read from `.repomix-textual.xml` before writing:
- `docs/examples/widgets/directory_tree.py` (XML 24961).
- `docs/examples/widgets/directory_tree_filtered.py` (XML 24938) — `filter_paths` override.
- `src/textual/widgets/_directory_tree.py:49358-49408` — `FileSelected` / `DirectorySelected`
  attributes; `:49408,49618` — re-rooting via `tree.path = ...`.

### 3.1 `convert-tree` pane (the primary feature)

Layout, top to bottom:
1. `Input(id="ct-input-path", placeholder="Input parent directory…")` — **source of
   truth** for the input root.
2. `DirectoryTree(Path.home(), id="ct-input-tree")` — a browser; selecting a directory
   fills `ct-input-path`.
3. `Input(id="ct-output-path", placeholder="Output directory…")` — source of truth for
   the output root.
4. `DirectoryTree(Path.home(), id="ct-output-tree")` — browser; selecting a directory
   fills `ct-output-path`.
5. `Button("Convert tree", id="run-convert-tree", variant="primary")`.
6. `Log(id="log-convert-tree")`.

Handlers:
- `on_directory_tree_directory_selected(event)` — a **single app-level handler** fires
  for both trees. Distinguish them via `event.control.id`:
  ```python
  def on_directory_tree_directory_selected(self, event):
      target = {"ct-input-tree": "ct-input-path",
                "ct-output-tree": "ct-output-path"}.get(event.control.id)
      if target:
          self.query_one(f"#{target}", Input).value = str(event.path)
  ```
  `event.path` is a `pathlib.Path`.
- `on_input_submitted(event)` — when the user types a path and presses Enter, **re-root
  the matching tree**: `self.query_one("#ct-input-tree", DirectoryTree).path = Path(value)`
  (the `path` reactive triggers a reload — verified, `_directory_tree.py:49618`). Guard
  with `Path(value).is_dir()`.
- The `run-convert-tree` branch in `on_button_pressed` reads both `Input.value`s and
  calls `self._run_recipe("convert-tree", [in_path, out_path], log)`. Validate both are
  non-empty and the input path exists; otherwise write an error line to the `Log` and do
  not launch.

This satisfies the requirement literally: input and output paths are both chosen via a
`DirectoryTree`, the `Input` lets the user *specify* an exact path, and the recipe
called is the `just convert-tree` recipe from Phase 1.

### 3.2 `convert` and `inspect` panes

These recipes take a single `FILE` (a `.dtsx`). Add a filtered directory tree:

```python
from collections.abc import Iterable

class DtsxTree(DirectoryTree):
    """A DirectoryTree that shows only directories and .dtsx files."""
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if p.is_dir() or p.suffix.lower() == ".dtsx"]
```
(Pattern copied from `directory_tree_filtered.py`, XML 24938.)

Pane layout: `Input(id=f"file-{recipe}")` + `DtsxTree(Path.home(), id=f"tree-{recipe}")`
+ Run button + `Log`. `on_directory_tree_file_selected` writes `event.path` into the
matching `Input`; Run passes `[input.value]` as the arg.

### 3.3 Wire-up

Extend `_build_pane` (or the pane classes) to branch on `recipe.name`:
`convert-tree` → §3.1 pane; `convert`/`inspect` → §3.2 pane; everything else → the
Phase-2 generic pane.

## Verification checklist — Phase 3

- [ ] `just tui` → `convert-tree` pane shows two `DirectoryTree`s and two `Input`s.
- [ ] Selecting a directory in the input tree fills the input `Input`; same for output.
- [ ] Typing a valid path into an `Input` + Enter re-roots that tree.
- [ ] **Convert tree** runs `just convert-tree <input> <output>`; output streams to the
      `Log`; `<output>` afterwards mirrors `<input>`'s `.dtsx` layout as `.sql` files.
- [ ] `convert` / `inspect` panes: the tree shows only folders and `.dtsx` files;
      selecting one fills the `Input`; **Run** converts/inspects that file.
- [ ] Empty / invalid path → a clear error line in the `Log`, no crash.
- [ ] `just test` still green.

## Anti-pattern guards — Phase 3

- `grep -n "FileSelected\|DirectorySelected" ssis2sql/tui.py` — handlers use the exact
  message classes; do not invent e.g. `PathSelected`.
- The two `DirectoryTree`s must be distinguished by `event.control.id`, not by widget
  order or a guessed attribute.
- Re-rooting uses `tree.path = Path(...)` — do not call a non-existent `set_path()`.
- `filter_paths` must be an **override on a `DirectoryTree` subclass**, not a kwarg.

---

# Phase 4 — Verification

**Goal:** Prove the whole feature works and matches the documented APIs.

## Steps

1. **Clean build:** `just clean && just install` — `.venv` rebuilt, `textual` installed.
2. **Test suite:** `just test` — all pass, including `tests/test_batch.py` and the new
   CLI test. `just cov` — coverage report; `batch.py` covered, overall not regressed.
3. **Compile check:** `python -m py_compile ssis2sql/batch.py ssis2sql/cli.py ssis2sql/tui.py`.
4. **Batch smoke test:**
   `just convert-tree examples /tmp/ssis-verify` — confirm `/tmp/ssis-verify` mirrors the
   `examples/` directory tree with `.sql` files; the summary line reports the count;
   `bin/`-nested packages are skipped.
5. **TUI smoke test:** `just tui` —
   - sidebar has a button for every recipe except `opus` and `tui`;
   - switching panes works;
   - a paramless recipe (`demo`) runs and streams output;
   - the `convert-tree` pane: pick input + output dirs via the trees, Convert, confirm
     the `.sql` tree appears;
   - `convert` pane: pick a `.dtsx`, Run, confirm SQL in the log;
   - `q` quits cleanly.
6. **Anti-pattern grep sweep** (all must return nothing / only intended hits):
   - `grep -rn "tab-placement" ssis2sql/` → empty.
   - `grep -rn "import textual" ssis2sql/cli.py ssis2sql/generator.py ssis2sql/__init__.py` → empty.
   - `grep -n "find " justfile` → only pre-existing `convert-samples` / `clean` lines,
     **not** the new `convert-tree` recipe.
   - In `ssis2sql/tui.py`: every widget mutation inside `_run_recipe` is wrapped in
     `call_from_thread`; `_run_recipe` is `@work(thread=True, ...)`.
7. **API conformance:** spot-check each Textual API used in `tui.py` against the cited
   `.repomix-textual.xml` lines in Phase 0 — no invented methods, no undocumented kwargs.

## Definition of done

- `just convert-tree IN OUT` recursively converts `.dtsx`→`.sql`, mirroring structure,
  via `pathlib`.
- `just tui` opens a Textual control panel with a left button per recipe; each runs its
  recipe and streams output; the `convert-tree` pane drives the conversion through
  `DirectoryTree` pickers.
- All tests green; no regressions; no anti-patterns present.

---

# Risks & caveats

- **`clean` button is destructive** — it deletes `.venv`, and the TUI itself runs from
  `.venv/bin/python` (via `just tui`). Running `clean` from the TUI removes the
  interpreter's environment out from under it. Mitigation options (implementer's call):
  give the `clean` pane a confirm step, or simply document it in the pane's description
  text. Not a phase blocker.
- **`textual` version drift** — the API citations come from Textual's `main` branch as
  packed into `.repomix-textual.xml`. `pip install textual` gets the latest *release*.
  All APIs used here (`DirectoryTree`, `ContentSwitcher`, `@work`, `Log`, `Button`,
  `Input`) are long-stable, but if a signature mismatch appears, trust the installed
  version (`python -c "import textual, inspect; ..."`) over the XML.
- **`run_worker` signature** was not in the packed XML — this plan deliberately uses the
  `@work` decorator only. If a later need arises for `run_worker`, re-run repomix with
  `src/textual/dom.py` added to `--include`.
- **`DirectoryTree` cannot navigate above its root.** The `Input`-field + re-root
  pattern in Phase 3.1 is what gives the user access to arbitrary paths; do not drop it.
- **TUI pilot tests need async** (`pytest-asyncio`). Kept optional so the existing plain
  pytest setup is untouched; the pure helpers (`find_repo_root`, `discover_recipes`) are
  the testable core.

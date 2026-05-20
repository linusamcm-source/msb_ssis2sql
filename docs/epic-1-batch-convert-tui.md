# Epic 1 — Recursive DTSX→SQL Batch Converter + Textual Control-Panel TUI

**Status:** Ready to execute. **Created:** 2026-05-19. **Repo:** `/Users/linus/Development/msb_ssis2sql`

> Restructured 2026-05-19 from `plan-batch-convert-tui.md` into three `## Story` blocks for
> a per-story team sprint. The original phase numbering survives only inside story bodies
> as implementation steps (§1.1, §2.1, …). TUI coverage is met with Textual pilot tests
> (`pytest-asyncio`), not waived — see Story 2 / Story 3 Definition of Done.

## Goal

Delivered across three stories:

1. **Story 1** — a recursive batch converter: the user picks a **parent directory**;
   every `.dtsx` beneath it (any depth) is converted to `.sql`, written to a user-chosen
   **output directory**, with the output tree mirroring the input tree. All path handling
   uses `pathlib.Path` so it is platform-agnostic. Exposed as a `just` recipe
   (`convert-tree INPUT OUTPUT`).
2. **Story 2** — a **Textual TUI** control panel for the whole `justfile`: a left-hand
   sidebar of buttons, one per recipe, each switching a content pane on the right. Every
   pane runs its recipe as a subprocess and streams the output into a log.
3. **Story 3** — `DirectoryTree` picker panes so `convert-tree`, `convert`, and `inspect`
   choose their paths visually.

## Architecture decisions (already made — do not re-litigate)

| Decision | Choice | Reason |
|---|---|---|
| Recursive walk | Python `Path.rglob("*.dtsx")` in a new `msb_ssis2sql/batch.py` module | User requirement: platform-agnostic `pathlib`. Bash `find` (as in `convert-samples`) is **not** allowed here. |
| How `convert-tree` is invoked | New `msb_ssis2sql` CLI subcommand `convert-tree`, wrapped by a thin `just` recipe | Matches existing `convert`/`inspect` pattern; keeps logic testable in Python. |
| Conversion engine | Reuse `msb_ssis2sql.generator.convert_file()` — **do not** write a new converter | Converter already exists and is tested. |
| TUI ↔ recipe coupling | TUI shells out to `just <recipe> [args]` as a subprocess | User requirement: "a justfile command that is called by the Textual UI". The TUI is a `just` launcher. |
| Left-hand tabs | `ContentSwitcher` + a `Vertical` of `Button`s docked left (`dock: left`) | **Textual has no left-side tab placement** — verified, see Reference. `TabbedContent` is top-docked only. |
| TUI location | New standalone module `msb_ssis2sql/tui.py`, run via `python -m msb_ssis2sql.tui` | Keeps the `textual` import out of the core `msb_ssis2sql` CLI. |
| TUI CSS | Inline `CSS` class attribute (not `CSS_PATH`) | Avoids shipping a `.tcss` data file with the package. |
| Recipe discovery | Parse `just --dump --dump-format json` at startup | Buttons stay in sync with the `justfile` automatically. |
| `textual` dependency | Add to `[project] dependencies` in `pyproject.toml` | So both `just install` and `just tui` work with no extra flags. |
| Recipes excluded from TUI buttons | `opus` (interactive Claude session), `tui` (cannot launch the TUI from inside itself) | The rest become buttons. |
| Output log widget | `Log` (not `RichLog`) | `Log.write_line` is simple and reliable for streaming plain subprocess text; `RichLog` defers writes until its size is known. |
| TUI test strategy | Textual pilot tests (`App.run_test()`) under `pytest-asyncio` | The sprint enforces an 80% coverage gate; the pure helpers alone cannot reach it, so pilot tests are **required**, not optional. |

---

# Reference — Documentation discovery (shared by all stories)

Two reference sources were generated for this plan. **Read the cited ranges before
writing code.**

- `.repomix-output.xml` — pack of *this* repo (`msb_ssis2sql`).
- `.repomix-textual.xml` — pack of the Textual framework (`github.com/Textualize/textual`,
  5.8 MB, 855 files). Generated with:
  ```
  npx --yes repomix --remote https://github.com/Textualize/textual \
    --include "docs/**,src/textual/widgets/_directory_tree.py,src/textual/widgets/_tabbed_content.py,src/textual/widgets/_tabs.py,src/textual/widgets/_content_switcher.py,src/textual/widgets/_button.py,src/textual/widgets/_input.py,src/textual/widgets/_log.py,src/textual/widgets/_rich_log.py,src/textual/app.py,src/textual/worker.py,src/textual/_work_decorator.py,src/textual/containers.py" \
    --output .repomix-textual.xml --style xml
  ```
  It is large — grep / line-offset Read it, never read it whole.

## Allowed APIs — `msb_ssis2sql` (this repo)

| Symbol | Location | Signature / fields |
|---|---|---|
| `convert_file` | `msb_ssis2sql/generator.py:49` | `convert_file(path: str \| pathlib.Path, options: ConvertOptions \| None = None) -> ConversionResult` |
| `ConvertOptions` | `msb_ssis2sql/generator.py:25` | dataclass: `wrap_in_procedure: bool=False`, `procedure_name: str="usp_Migrated_Package"`, `include_header: bool=True` |
| `ConversionResult` | `msb_ssis2sql/generator.py:34` | dataclass: `sql: str`, `warnings: list[str]`, `package: Package \| None`. `__str__` returns `.sql`. **These are the only three fields — do not invent others.** |
| `parse_file` | `msb_ssis2sql/parser.py:99` | `parse_file(path: str \| pathlib.Path) -> Package` |
| `@logged`, `logger` | `msb_ssis2sql/observability.py` | decorator + loguru logger; existing modules use it on public functions |
| `Ssis2SqlError` | `msb_ssis2sql/errors.py` | base exception; CLI `main()` catches `Ssis2SqlError` **and** `OSError` and exits 2 |
| CLI argparse setup | `msb_ssis2sql/cli.py:14-45` | `convert` subparser to copy from; shared `-v/-vv`, `-o/--output`, `--procedure`, `--no-header` flags live here |
| pytest fixtures | `conftest.py` (repo root) | `example_path` → `examples/sales_etl.dtsx`; `example_package` → parsed `Package` |

CLI invocation: `python -m msb_ssis2sql convert <dtsx> -o <out>`. Console-script `msb_ssis2sql`
registered at `pyproject.toml` `[project.scripts]`.

> Every `msb_ssis2sql` symbol, line number, and field list above is a **plan claim about the
> repo as it was packed**. Engineers and reviewers must re-verify each against the live
> source (`.repomix-output.xml` / direct Read) before relying on it — line numbers drift.

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
| Pilot testing | `async with App().run_test() as pilot:` — `pilot.click(selector)`, `pilot.press(key)`, `pilot.pause()`; `app.query_one(...)` to assert state | `src/textual/pilot.py`; Textual testing guide |

> Textual line numbers come from the framework's `main` branch as packed. `pip install
> textual` gets the latest *release*. All APIs used here are long-stable; on any
> signature mismatch, trust the installed version (`python -c "import textual,
> inspect; ..."`) over the XML.

**Copy-ready Textual examples** (inside `.repomix-textual.xml`):
- `docs/examples/widgets/content_switcher.py` (XML 24544) + `.tcss` (24612) — **the left-tab pattern.**
- `docs/examples/widgets/directory_tree.py` (XML 24961); filtered variant (XML 24938).
- `docs/examples/widgets/button.py` (XML 24306) + `.tcss` (24360).
- `docs/examples/widgets/input.py` (XML 25175); `log.py` (XML 25428).
- `docs/examples/guide/workers/weather05.py` (XML 18524) — thread worker + `call_from_thread`.
- `docs/examples/guide/workers/weather04.py` (XML 18474) — `on_worker_state_changed`.
- `src/textual/widgets/_directory_tree.py` `_load_directory` (XML 49769) — real `@work(thread=True)` doing blocking I/O.
- `docs/examples/guide/layout/dock_layout3_sidebar_header.{py,tcss}` (XML 15136/15162) — `dock: left` sidebar.

## Anti-patterns — do NOT do these (apply to every story)

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

## Story 1: Recursive batch converter (no Textual)

`just convert-tree INPUT OUTPUT` recursively converts every `.dtsx` under `INPUT` into
`.sql` under `OUTPUT`, mirroring the directory structure, using `pathlib`. No Textual.
Independently shippable.

### Files

- **New:** `msb_ssis2sql/batch.py`
- **New:** `tests/test_batch.py`
- **Edit:** `msb_ssis2sql/cli.py` — add a `convert-tree` subcommand
- **Edit:** `justfile` — add a `convert-tree` recipe
- **Edit:** `tests/test_cli.py` — add a `convert-tree` CLI test

### What to implement

#### §1.1 `msb_ssis2sql/batch.py`

Before writing: Read `msb_ssis2sql/generator.py:1-60` (for `convert_file`, `ConvertOptions`,
`ConversionResult`) and `msb_ssis2sql/observability.py` (for the `@logged` decorator usage,
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

#### §1.2 `convert-tree` CLI subcommand — `msb_ssis2sql/cli.py`

Read `msb_ssis2sql/cli.py` fully first. **Copy the structure of the existing `convert`
subparser and its handler `_cmd_convert`** — do not invent a new CLI style. (Verify the
exact line numbers against the live file; the Reference cites `cli.py:14-45` for argparse
setup, but confirm before relying on it.)

- Add `from .batch import convert_tree` to the imports.
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
  same way `_cmd_convert` does, call `convert_tree(Path(args.input), Path(args.output),
  options)`, print one line per file plus a final summary (`converted N, failed M`), and
  **return exit code 1 if `result.failed > 0`, else 0**.
- Route `"convert-tree"` to `_cmd_convert_tree` in `main()`'s dispatch.

#### §1.3 `justfile` recipe

Add after the `convert-samples` recipe:
```
# Recursively convert every .dtsx under INPUT into OUTPUT, mirroring the input tree.
# Usage: just convert-tree path/to/input path/to/output
convert-tree INPUT OUTPUT:
    .venv/bin/python -m msb_ssis2sql convert-tree {{INPUT}} {{OUTPUT}}
```

#### §1.4 Tests — `tests/test_batch.py`

Use `tmp_path` and the `examples/` packages as known-good inputs (the repo-root
`conftest.py` `example_path` fixture points at `examples/sales_etl.dtsx`).

Add one `convert-tree` test to `tests/test_cli.py` mirroring the existing CLI tests.

### Acceptance Criteria

- `convert_tree(input_root, output_root)` recursively converts every `.dtsx` under
  `input_root` into `.sql` under `output_root`, mirroring directory structure: a nested
  input `a/b/pkg.dtsx` produces a non-empty `output/a/b/pkg.sql`.
- `.dtsx` files nested under a `bin/` or `obj/` directory are skipped — not converted and
  not present in the output tree.
- `convert_tree` called with a non-existent input directory raises `NotADirectoryError`.
- `convert_tree` on an input directory containing no `.dtsx` files returns a `BatchResult`
  whose `converted` is `0` and `failed` is `0`.
- A malformed `.dtsx` (garbage XML) is recorded as a failed `FileOutcome` (`ok is False`,
  `error` is a non-empty string) and does not abort the run — a sibling valid package in
  the same tree still converts successfully.
- `convert_tree` accepts `str` and `pathlib.Path` for both `input_root` and `output_root`.
- The `msb_ssis2sql convert-tree <input> <output>` CLI subcommand converts a tree, prints one
  line per file and a final `converted N, failed M` summary, exits `1` when any file
  failed and `0` otherwise.
- `just convert-tree INPUT OUTPUT` runs the `convert-tree` CLI subcommand against a real
  example tree and writes the mirrored `.sql` output.

### Definition of Done

- `msb_ssis2sql/batch.py` exists with `convert_tree`, `BatchResult`, `FileOutcome`; recursion
  is `pathlib.Path.rglob` — `grep -n "rglob" msb_ssis2sql/batch.py` matches, no bash `find`.
- `convert_tree` reuses `msb_ssis2sql.generator.convert_file` — `grep -n "convert_file"
  msb_ssis2sql/batch.py` shows it being *called*, not reimplemented. No new conversion logic.
- Output paths derive from `with_suffix(".sql")`, not string concatenation.
- `msb_ssis2sql/cli.py` has a `convert-tree` subparser modelled on the existing `convert`
  subparser; `main()` routes `"convert-tree"` to its handler.
- `justfile` has a `convert-tree` recipe; `grep -n "find " justfile` shows the new recipe
  is **not** among the matches.
- `tests/test_batch.py` covers every Acceptance Criterion; `tests/test_cli.py` has a new
  `convert-tree` test.
- `python -m py_compile msb_ssis2sql/batch.py msb_ssis2sql/cli.py` — no syntax errors.
- `just test` is green (no regressions); line coverage of `msb_ssis2sql/batch.py` is ≥ 80%.
- `just convert-tree examples /tmp/ssis-verify` mirrors the `examples/` tree as `.sql`
  files; the summary line reports the count; `bin/`-nested packages are skipped.

---

## Story 2: Textual TUI scaffold + generic recipe runner

`just tui` launches a Textual app: a left sidebar of buttons (one per non-excluded
`justfile` recipe) and a right-hand `ContentSwitcher`. Each pane shows the recipe's doc,
a **Run** button, and a `Log`. Pressing **Run** executes `just <recipe>` as a subprocess
and streams its output into the `Log`. This story handles the **parameter-less** recipes
(`install`, `test`, `cov`, `demo`, `convert-samples`, `clean`); argful recipes
(`convert`, `inspect`, `convert-tree`) get a plain text-`Input` fallback now and proper
`DirectoryTree` pickers in Story 3.

### Files

- **Edit:** `pyproject.toml` — add `textual` to `[project] dependencies`; add
  `pytest-asyncio` to `[project.optional-dependencies] dev`; set `asyncio_mode` in
  `[tool.pytest.ini_options]`
- **New:** `msb_ssis2sql/tui.py`
- **Edit:** `justfile` — add a `tui` recipe
- **New:** `tests/test_tui.py` — pure-helper unit tests **and** Textual pilot tests

### What to implement

#### §2.1 Dependencies — `pyproject.toml`

- Add `textual` to `[project] dependencies` (currently `["loguru>=0.7"]`). Run
  `pip install textual` inside `.venv`, then `pip show textual` to read the installed
  version, and pin to that minor — e.g. `dependencies = ["loguru>=0.7", "textual>=1.0"]`.
- Add `pytest-asyncio` to `[project.optional-dependencies] dev` (currently
  `["pytest>=7.0", "pytest-cov>=4.0"]`).
- In `[tool.pytest.ini_options]` set `asyncio_mode = "auto"` so `async def test_*`
  functions run without a per-test marker.
- Re-run `just install`.

#### §2.2 `msb_ssis2sql/tui.py`

Read these from `.repomix-textual.xml` before writing:
- `docs/examples/widgets/content_switcher.py` (XML 24544) + `.tcss` (24612) — the
  sidebar-nav pattern: button `id` matches panel `id`.
- `docs/examples/guide/layout/dock_layout3_sidebar_header.tcss` (XML 15162) — `dock: left`.
- `docs/examples/guide/workers/weather05.py` (XML 18524) — `@work(thread=True)` +
  `call_from_thread`.
- `src/textual/widgets/_directory_tree.py:49769` — a real `@work(thread=True)` worker.

**Pure helper functions** (module-level, side-effect-free, unit-testable without the app):

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
        # Story 2: generic pane. Story 3 replaces the panes for
        # convert / inspect / convert-tree with DirectoryTree pickers.
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
        # Story 3: collect args from the pane's Input/DirectoryTree widgets.
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
- For argful recipes in this story, add a plain `Input(id=f"arg-{name}")` to the pane and
  pass `[input.value]` (or split on spaces) as `args`. Story 3 supersedes this.

#### §2.3 `justfile` recipe

```
# Launch the Textual control-panel UI for msb_ssis2sql.
tui:
    .venv/bin/python -m msb_ssis2sql.tui
```

#### §2.4 Tests — `tests/test_tui.py`

**Pure helpers** (no app, synchronous):
- `find_repo_root(Path(__file__))` returns the repo root (the dir with `justfile`);
  `find_repo_root` on a path with no justfile ancestor raises `FileNotFoundError`.
- `discover_recipes(repo_root)` returns a non-empty list, excludes `opus` and `tui`, and
  includes `convert-tree` (Story 1 is merged first).
- Feed `discover_recipes` a captured `just --dump --dump-format json` string (monkeypatch
  `subprocess.run`) and assert `Recipe.params` for `convert` is `["FILE"]`.

**Pilot tests** (`async def`, `App.run_test()` — required for the coverage gate):
- The app composes: one sidebar `Button` per non-excluded recipe; no button for `opus`
  or `tui`.
- Clicking a sidebar button sets `query_one(ContentSwitcher).current` to that pane's id.
- Running a fast paramless recipe streams output into the pane's `Log` and the worker
  reaches a terminal state. Keep the test hermetic: monkeypatch `subprocess.Popen` (or
  target a trivial recipe) so the pilot test does not depend on a real `just` build.
- `q` exits the app.

### Acceptance Criteria

- `just tui` launches the Textual app without raising.
- The sidebar shows exactly one button per non-excluded `justfile` recipe; there is no
  button for `opus` and none for `tui`.
- Clicking a sidebar button switches the right-hand `ContentSwitcher` to the matching
  pane (`ContentSwitcher.current` changes to the pane id).
- A parameter-less recipe pane (`demo`) has a **Run** button that executes the recipe as
  a subprocess and streams its output into the pane's `Log`, ending with an `[exit <code>]`
  line.
- The `test` pane streams output incrementally while the recipe runs — the UI does not
  freeze for the duration.
- Pressing `q` quits the app.
- `find_repo_root(start)` returns the nearest ancestor of `start` containing a `justfile`,
  and raises `FileNotFoundError` when no ancestor has one.
- `discover_recipes(repo_root)` parses `just --dump --dump-format json` into a sorted
  `Recipe` list that excludes private recipes, `opus`, and `tui`, with `Recipe.params`
  populated from each recipe's parameters.

### Definition of Done

- `pyproject.toml`: `[project] dependencies` includes `textual` pinned to the installed
  minor; `[project.optional-dependencies] dev` includes `pytest-asyncio`;
  `[tool.pytest.ini_options]` sets `asyncio_mode = "auto"`. `just install` succeeds and
  `pip show textual` confirms install.
- `msb_ssis2sql/tui.py` exists with `find_repo_root`, `discover_recipes`, `Recipe`, and the
  `Ssis2SqlTUI` app; `main()` runs it; `python -m msb_ssis2sql.tui` is the entry point.
- Left navigation is `ContentSwitcher` + `dock: left` — `grep -n
  "tab-placement\|TabbedContent" msb_ssis2sql/tui.py` is empty.
- `_run_recipe` is a `@work(thread=True, ...)` worker; every widget mutation inside it is
  wrapped in `call_from_thread` — `grep -n "call_from_thread" msb_ssis2sql/tui.py` covers
  each `log.write_line` in the worker.
- The log widget is `Log`, not `RichLog` — `grep -n "RichLog" msb_ssis2sql/tui.py` is empty.
- The core CLI stays Textual-free — `grep -rn "import textual" msb_ssis2sql/cli.py
  msb_ssis2sql/__init__.py msb_ssis2sql/generator.py` is empty.
- `justfile` has a `tui` recipe.
- `tests/test_tui.py` exists with the pure-helper unit tests **and** Textual pilot tests
  covering compose, sidebar navigation, and the recipe runner.
- `python -m py_compile msb_ssis2sql/tui.py` — no syntax errors.
- `just test` is green including the Story 1 tests (no regressions); line coverage of
  `msb_ssis2sql/tui.py` is ≥ 80%.

---

## Story 3: DirectoryTree picker panes

Replace the generic panes for `convert-tree`, `convert`, and `inspect` with
`DirectoryTree`-driven panes so the user picks paths visually.

### Files

- **Edit:** `msb_ssis2sql/tui.py`
- **Edit:** `tests/test_tui.py` — pilot tests for the picker panes

### What to implement

Read from `.repomix-textual.xml` before writing:
- `docs/examples/widgets/directory_tree.py` (XML 24961).
- `docs/examples/widgets/directory_tree_filtered.py` (XML 24938) — `filter_paths` override.
- `src/textual/widgets/_directory_tree.py:49358-49408` — `FileSelected` / `DirectorySelected`
  attributes; `:49408,49618` — re-rooting via `tree.path = ...`.

#### §3.1 `convert-tree` pane (the primary feature)

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
  the matching tree**: `self.query_one("#ct-input-tree", DirectoryTree).path =
  Path(value)` (the `path` reactive triggers a reload). Guard with `Path(value).is_dir()`.
- The `run-convert-tree` branch in `on_button_pressed` reads both `Input.value`s and
  calls `self._run_recipe("convert-tree", [in_path, out_path], log)`. Validate both are
  non-empty and the input path exists; otherwise write an error line to the `Log` and do
  not launch.

This satisfies the requirement literally: input and output paths are both chosen via a
`DirectoryTree`, the `Input` lets the user *specify* an exact path, and the recipe called
is the `just convert-tree` recipe from Story 1.

#### §3.2 `convert` and `inspect` panes

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

#### §3.3 Wire-up

Extend `_build_pane` (or the pane classes) to branch on `recipe.name`: `convert-tree` →
§3.1 pane; `convert`/`inspect` → §3.2 pane; everything else → the Story-2 generic pane.

#### §3.4 Tests — `tests/test_tui.py`

Extend with pilot tests:
- Selecting a directory in `ct-input-tree` fills `ct-input-path`; selecting in
  `ct-output-tree` fills `ct-output-path` (drive via the message handler / a synthetic
  `DirectorySelected`, or `pilot` interaction over a `tmp_path` tree).
- Submitting a valid path into `ct-input-path` re-roots `ct-input-tree`
  (`tree.path == Path(value)`).
- Pressing **Convert tree** with an empty or non-existent input path writes an error
  line to the `Log` and does **not** start the recipe worker.
- The `convert` pane's `DtsxTree.filter_paths` keeps directories and `.dtsx` files and
  drops other files.

### Acceptance Criteria

- The `convert-tree` pane shows two `DirectoryTree` widgets and two `Input` widgets — one
  pair for the input root, one for the output root.
- Selecting a directory in the input tree fills the input `Input`; selecting a directory
  in the output tree fills the output `Input`; the two trees are distinguished by
  `event.control.id`.
- Typing a valid directory path into an `Input` and pressing Enter re-roots the matching
  `DirectoryTree` (its `path` reactive is reassigned).
- Pressing **Convert tree** with valid input and output paths runs `just convert-tree
  <input> <output>`; output streams to the `Log`; afterwards `<output>` mirrors
  `<input>`'s `.dtsx` layout as `.sql` files.
- The `convert` and `inspect` panes each show a `DirectoryTree` filtered to directories
  and `.dtsx` files only; selecting a `.dtsx` fills the pane's `Input`; **Run** invokes
  the recipe on that file.
- An empty or invalid path produces a clear error line in the `Log` and does not crash
  the app or launch the recipe.

### Definition of Done

- `msb_ssis2sql/tui.py` updated: the `convert-tree` pane per §3.1, the `convert`/`inspect`
  panes per §3.2, `_build_pane` branching on `recipe.name` per §3.3.
- A `DtsxTree(DirectoryTree)` subclass overrides `filter_paths` — it is a method override
  on the subclass, not a constructor kwarg.
- Handlers use the exact message classes `DirectoryTree.FileSelected` and
  `DirectoryTree.DirectorySelected` — `grep -n "FileSelected\|DirectorySelected"
  msb_ssis2sql/tui.py` confirms; no invented message names.
- Re-rooting assigns `tree.path = Path(...)` — no call to a non-existent `set_path()`.
- The two `convert-tree` trees are distinguished by `event.control.id`, not by widget
  order or a guessed attribute.
- `tests/test_tui.py` extended with pilot tests for the picker panes (selection fills the
  `Input`, re-root on submit, invalid-path error line, `filter_paths` behaviour).
- `python -m py_compile msb_ssis2sql/tui.py` — no syntax errors.
- `just test` is green (no regressions); line coverage of `msb_ssis2sql/tui.py` is ≥ 80%
  including the Story 3 additions.

---

# Risks & caveats

- **`clean` button is destructive** — it deletes `.venv`, and the TUI itself runs from
  `.venv/bin/python` (via `just tui`). Running `clean` from the TUI removes the
  interpreter's environment out from under it. Mitigation options (implementer's call):
  give the `clean` pane a confirm step, or document it in the pane's description text.
  Not a story blocker.
- **`textual` version drift** — the API citations come from Textual's `main` branch as
  packed into `.repomix-textual.xml`. `pip install textual` gets the latest *release*.
  All APIs used here (`DirectoryTree`, `ContentSwitcher`, `@work`, `Log`, `Button`,
  `Input`, `App.run_test`) are long-stable, but on any signature mismatch, trust the
  installed version (`python -c "import textual, inspect; ..."`) over the XML.
- **`run_worker` signature** was not in the packed XML — this plan deliberately uses the
  `@work` decorator only. If a later need arises for `run_worker`, re-run repomix with
  `src/textual/dom.py` added to `--include`.
- **`DirectoryTree` cannot navigate above its root.** The `Input`-field + re-root pattern
  in §3.1 is what gives the user access to arbitrary paths; do not drop it.
- **TUI pilot tests need `pytest-asyncio`.** This is now a committed dependency (Story 2
  §2.1), not an optional stretch — the sprint's 80% coverage gate cannot be met on
  `tui.py` from the pure helpers alone. Pilot tests must be hermetic: monkeypatch
  `subprocess.Popen` / `subprocess.run` so they neither launch real builds nor depend on
  a populated `.venv`.

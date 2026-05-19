# Plan — Validation Runner pane for the ssis2sql TUI

**Goal:** Add a dedicated **Validation** pane to the existing Textual TUI
(`ssis2sql/tui.py`) that runs the three layers of the `validation/` testing
framework — Static, Unit, Differential — from inside the TUI, streams their
output, and shows a parsed pass/fail/skip summary.

**Status of the current integration (verified, not assumed):**
`discover_recipes()` (`tui.py:33-51`) parses `just --dump --dump-format json`
and turns *every* non-private recipe except `opus`/`tui` into a sidebar button.
The validation recipes (`validate`, `validate-static`, `validate-unit`,
`validate-cov`, `install-validation`) therefore **already appear** as plain
`RecipePane` buttons today — a button + a raw `Log`. That works but is crude:
the three layers are scattered among ~12 unrelated buttons, output is raw
pytest text with no summary, and the differential layer gives no hint that it
needs SQL Server. This plan replaces that crude state with one purpose-built
pane.

## Decisions locked (from user)

| Decision | Choice | Consequence |
|----------|--------|-------------|
| Execution model | **Subprocess via `just`** | Reuse the TUI's existing thread-worker + `subprocess.Popen` pattern. No new `validation/` code. `tui.py` stays framework-free (imports nothing from `validation/`). |
| Pane scope | **Minimal — layer runner** | Three layer buttons, one streamed `Log`, one parsed summary line. No per-test results table, no corpus-package picker. |

## Definition of done

1. A **Validation** entry appears in the TUI sidebar; clicking it shows a
   `ValidationPane`.
2. The pane has three buttons — **Static**, **Unit**, **Differential** — each
   running the matching `just` recipe and streaming output into one `Log`.
3. After each run, a summary line shows parsed counts (e.g. `17 passed`).
4. The Differential button prints a `.env`-missing note when SQL Server config
   is absent (the run still proceeds; tests skip gracefully).
5. The plain `validate` / `validate-static` / `validate-unit` sidebar buttons
   no longer appear (they moved into the pane). `validate-cov` and
   `install-validation` are intentionally left as ordinary buttons.
6. `just test` is green (existing 477+ TUI tests plus the new ones).

## Files touched

| File | Change |
|------|--------|
| `ssis2sql/tui.py` | New `parse_pytest_summary` helper, new `ValidationPane`, new `_launch_validation` + `_run_validation` methods, app-wiring edits, CSS additions. |
| `tests/test_tui.py` | New unit tests for `parse_pytest_summary`; new Pilot tests for the pane. |
| `README.md` | One-line note that the validation layers are runnable from the TUI. |

---

## Phase 0 — Documentation Discovery & Allowed APIs

*This phase is already complete — its findings are recorded here so every later
phase is self-contained. No code is written in Phase 0.*

### 0.1 Allowed APIs — Textual (all already used in `tui.py`, copy them)

| API | Where it is already used | Use for |
|-----|--------------------------|---------|
| `class X(VerticalScroll)` | `RecipePane` `tui.py:94`, `DtsxPickerPane` `tui.py:133` | Base class for `ValidationPane`. |
| `Horizontal` container | `tui.py:60` import, `tui.py:171` usage | Lay the three layer buttons in a row. **Already imported** — no new import. |
| `Static`, `Button`, `Log` | `RecipePane.compose` `tui.py:101-104` | Pane widgets. |
| `Button(label, id=…, variant="primary")` | `tui.py:103` | The three layer buttons. |
| `Static(text, id=…)` then `.update(text)` | `Static` used `tui.py:102`; `.update` is the standard Textual API | The summary line widget. |
| `Log(id=…)`, `.clear()`, `.write_line(text)` | `tui.py:104`, `tui.py:212`, `tui.py:259` | Streamed output. |
| `@work(thread=True, exclusive=True, group="recipe-run")` | `_run_recipe` decorator `tui.py:255` | The validation thread worker. |
| `get_current_worker()`, `worker.is_cancelled` | `tui.py:257`, `tui.py:267` | Cooperative cancellation. |
| `self.call_from_thread(fn, *args)` | `tui.py:259, 270, 272` | **Every** widget mutation from inside the worker. |
| `self.query_one("#id", Type)` | `tui.py:196, 211, 220` | Widget lookup. |

### 0.2 Allowed commands — the validation framework (verified)

The framework is driven entirely through `just` recipes (`justfile:61-74`).
This plan calls these recipes by subprocess — **it does not import any
`validation/` module**.

| TUI button | `just` recipe | Underlying command | SQL Server? |
|------------|---------------|--------------------|-------------|
| Static | `validate-static` | `pytest validation/test_static.py` | No |
| Unit | `validate-unit` | `pytest validation/tests` | No |
| Differential | `validate` | `pytest validation/ -m validation` | Yes — without it, tests **skip** (the `sqlserver_connection` fixture calls `pytest.skip()`); they do **not** error. |

SQL Server config lives in `.env` (`MSSQL_*` vars; see `README.md:280-297`).
`.env` is gitignored; `.env.example` is the template.

### 0.3 Copy-ready locations

| Need | Copy from |
|------|-----------|
| A `VerticalScroll` pane class | `RecipePane` — `tui.py:94-104` |
| The thread worker that streams a subprocess into a `Log` | `_run_recipe` — `tui.py:255-273` |
| A launcher that clears the `Log` and starts the worker | `_launch` — `tui.py:210-213` |
| `_build_pane` dispatch by recipe name | `tui.py:181-187` |
| Button routing in `on_button_pressed` | `tui.py:193-204` |
| Pilot test of a recipe run with a hermetic `subprocess.Popen` | `test_run_button_writes_to_log_and_exits` — `test_tui.py:288-316` |
| Pilot test of sidebar navigation | `test_clicking_nav_button_switches_content_pane` — `test_tui.py:272-285` |
| Pilot test asserting which sidebar buttons exist | `test_app_compose_one_button_per_recipe` — `test_tui.py:241-254` |

### 0.4 Anti-patterns to avoid

- **Do not** `import` anything from `validation/` inside `tui.py`. The chosen
  design is subprocess-only; `tui.py:54-55` explicitly keeps the core
  Textual-isolated, and the same discipline applies to the framework.
- **Do not** use `subprocess.run` for the streaming worker — it blocks until
  exit and yields no incremental output. Use `subprocess.Popen` exactly as
  `_run_recipe` does (`tui.py:260-264`).
- **Do not** mutate any widget directly from inside the thread worker. Every
  call goes through `self.call_from_thread(...)`.
- **Do not** modify `_run_recipe` (`tui.py:255-273`) — it is shared by the
  other panes. Add a sibling `_run_validation` instead.
- **Do not** treat a differential run with no SQL Server as a failure — pytest
  reports those tests as **skipped**. The summary must show skips honestly.
- **Do not** add a new pytest marker or new `just` recipe — the recipes already
  exist (`justfile:61-74`); only the marker `validation` exists in
  `pyproject.toml` and that is sufficient.
- **Do not** invent Textual APIs. Every widget/method in 0.1 is already present
  in `tui.py` — if a needed call is not listed there, stop and verify against
  Textual 8.2 docs before using it.

---

## Phase 1 — `parse_pytest_summary` helper (TDD)

**What to implement:** a pure, module-level function in `tui.py` that scans
captured pytest output lines and returns a one-line human summary. Pure and
side-effect-free so it is unit-tested without Textual.

### 1.1 Write the failing tests first

Add to `tests/test_tui.py` (these are plain `def` tests, **not** `async`):

```python
import pytest
from ssis2sql.tui import parse_pytest_summary

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
```

Run `.venv/bin/python -m pytest tests/test_tui.py -k parse_pytest_summary` —
all should fail with `ImportError` (the function does not exist yet).

### 1.2 Implement the helper

Add to `tui.py`, near `_slug` (after `tui.py:76`). Reference signature:

```python
import re  # add to the top-of-file imports (tui.py:3-8 block)

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
```

### 1.3 Verification checklist

- [ ] `.venv/bin/python -m pytest tests/test_tui.py -k parse_pytest_summary`
      — all 8 parametrised cases pass.
- [ ] `grep -n "subprocess.run" ssis2sql/tui.py` — only the pre-existing hit in
      `discover_recipes` (`tui.py:35`); the helper added no new one.

### 1.4 Anti-pattern guards

- The helper takes `list[str]` and returns `str` — **no** Textual import, **no**
  widget reference. If it touches a widget, it is wrong.

---

## Phase 2 — `ValidationPane`, workers, and app wiring (all `tui.py`)

**What to implement:** the pane widget, its launcher and worker, and the four
small wiring edits that surface it. This phase ends with `tui.py` importable
and the TUI launching with a working Validation pane.

### 2.1 Add the validation-layer constant

Below `_EXCLUDED_RECIPES` (`tui.py:13`):

```python
# Validation-framework recipes that move into the dedicated ValidationPane;
# they are dropped from the auto-discovered sidebar buttons to avoid duplication.
_VALIDATION_LAYER_RECIPES = frozenset({"validate", "validate-static", "validate-unit"})

# (recipe, button label) for the three layers, in display order.
_VALIDATION_LAYERS = (
    ("validate-static", "Static"),
    ("validate-unit", "Unit"),
    ("validate", "Differential"),
)
```

### 2.2 Add `ValidationPane` — copy `RecipePane` (`tui.py:94-104`) and adapt

Place after `DtsxPickerPane` (`tui.py:145`):

```python
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
```

Button ids are `run-validate-static`, `run-validate-unit`, `run-validate` —
deliberately the `run-<recipe>` shape so routing (2.5) is a one-liner.

### 2.3 Add `_launch_validation` — model on `_launch` (`tui.py:210-213`)

Add as an app method (near `_launch`):

```python
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
```

### 2.4 Add `_run_validation` worker — copy `_run_recipe` (`tui.py:255-273`)

Identical to `_run_recipe` except: it takes no `args`, collects each streamed
line, and after the exit line parses the summary. Same decorator and `group`.

```python
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
```

Sharing `group="recipe-run"` with `_run_recipe` means a new run cancels any
in-flight run — the existing single-run-at-a-time behaviour. Keep it.

### 2.5 Wire it into the app — four small edits

**Edit A — `__init__` (`tui.py:164-167`):** drop the layer recipes from the
discovered list, append one synthetic `Recipe` for the pane.

```python
def __init__(self) -> None:
    super().__init__()
    self._repo_root = find_repo_root(Path(__file__).resolve())
    recipes = discover_recipes(self._repo_root)
    recipes = [r for r in recipes if r.name not in _VALIDATION_LAYER_RECIPES]
    self._recipes = [
        *recipes,
        Recipe(name="validation", doc="Run the ssis2sql validation framework."),
    ]
```

Appending (not prepending) keeps `self._recipes[0]` unchanged, so the initial
pane shown on launch (`compose` `tui.py:175`) is unaffected. The `compose` loop
(`tui.py:173-178`) then builds the `nav-validation` button and `pane-validation`
automatically — no `compose` edit needed.

**Edit B — `_build_pane` (`tui.py:181-187`):** add one branch.

```python
def _build_pane(self, recipe: Recipe) -> VerticalScroll:
    """Return the appropriate pane widget for a recipe."""
    if recipe.name == "validation":
        return ValidationPane(recipe)
    if recipe.name == "convert-tree":
        return ConvertTreePane(recipe)
    if recipe.name in ("convert", "inspect"):
        return DtsxPickerPane(recipe)
    return RecipePane(recipe)
```

**Edit C — `on_button_pressed` (`tui.py:193-204`):** add one `elif`, placed
**before** the generic `run-` branch. Use an exact-membership test, **not** a
`startswith("run-validate")` prefix — that prefix would also capture
`run-validate-cov`, which is a normal `RecipePane` button.

```python
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
```

The `nav-validation` button needs **no** new handling — the existing
`bid.startswith("nav-")` branch already switches to `pane-validation`.

**Edit D — `CSS` (`tui.py:155-161`):** append three rules so the buttons sit in
a row and the summary is spaced.

```python
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
```

`#validation-buttons { height: auto; }` is required — a `Horizontal` defaults
to `height: 1fr` and would otherwise eat the pane.

### 2.6 Verification checklist

- [ ] `.venv/bin/python -c "import ssis2sql.tui"` — imports clean.
- [ ] `grep -n "from validation" ssis2sql/tui.py` and
      `grep -n "import validation" ssis2sql/tui.py` — **zero** hits (subprocess
      design; `tui.py` must not import the framework).
- [ ] `grep -n "subprocess.Popen" ssis2sql/tui.py` — two hits: `_run_recipe`
      and the new `_run_validation`. No `subprocess.run` added.
- [ ] `.venv/bin/python -m ssis2sql.tui` — TUI launches; a **validation**
      button is in the sidebar; no `validate`, `validate-static`,
      `validate-unit` buttons; `validate-cov` and `install-validation` still
      present. (`q` quits.)
- [ ] Clicking **validation**, then **Static** → output streams into the Log,
      ends with `[exit 0]`, summary line updates from `running…` to a count.

### 2.7 Anti-pattern guards

- `_run_recipe` (`tui.py:255-273`) is **unchanged** — confirm via `git diff`.
- No widget mutated outside `call_from_thread` inside `_run_validation`.
- The `on_button_pressed` validation branch uses exact membership
  (`removeprefix("run-") in _VALIDATION_LAYER_RECIPES`), never a
  `startswith("run-validate")` prefix.

---

## Phase 3 — Pilot integration tests for the Validation pane

**What to implement:** `async` Pilot tests in `tests/test_tui.py`, copied from
the existing run/nav tests. Use the hermetic `subprocess.Popen` monkeypatch from
`test_run_button_writes_to_log_and_exits` (`test_tui.py:288-316`).

### 3.1 Tests to add

Copy the structure of `test_tui.py:288-316` for each. Monkeypatch
`tui_mod.discover_recipes` to return a small recipe list, and
`tui_mod.find_repo_root` to a `tmp_path`.

1. **`test_validation_pane_is_present_and_navigable`** — copy
   `test_clicking_nav_button_switches_content_pane` (`test_tui.py:272-285`).
   After `pilot.click("#nav-validation")`, assert
   `app.query_one(ContentSwitcher).current == "pane-validation"`.

2. **`test_layer_recipes_have_no_plain_sidebar_button`** — copy
   `test_app_compose_one_button_per_recipe` (`test_tui.py:241-254`). Make
   `discover_recipes` return recipes including `validate-static`,
   `validate-cov`. Assert sidebar button ids contain `nav-validation` and
   `nav-validate-cov` but **not** `nav-validate-static` / `nav-validate-unit` /
   `nav-validate`.

3. **`test_static_layer_button_streams_into_log_and_summary`** — copy
   `test_run_button_writes_to_log_and_exits` (`test_tui.py:288-316`). Fake
   `subprocess.Popen` stdout = `iter(["collected 17 items\n",
   "===== 17 passed in 0.84s =====\n"])`, `returncode = 0`. Navigate to the
   pane, `pilot.click("#run-validate-static")`, `pilot.pause(delay=0.5)`.
   Assert the Log contains `[exit 0]` and `17 passed`, and
   `app.query_one("#validation-summary", Static)` renders `17 passed`.

4. **`test_differential_button_warns_when_dotenv_absent`** — `find_repo_root`
   points at a `tmp_path` with **no** `.env`. Click `#run-validate`, pause,
   assert the Log contains `note: .env not found`.

5. **`test_differential_button_no_warning_when_dotenv_present`** — same, but
   `(tmp_path / ".env").write_text("MSSQL_SERVER_ADDRESS=x\n")` first. Assert
   the Log does **not** contain `note: .env not found`.

### 3.2 Verification checklist

- [ ] `.venv/bin/python -m pytest tests/test_tui.py` — all TUI tests pass
      (pre-existing + the 8 helper cases + the 5 new Pilot tests).
- [ ] Each new Pilot test uses a hermetic `subprocess.Popen` monkeypatch — no
      test invokes a real `just`/`pytest`.

### 3.3 Anti-pattern guards

- New tests are `async def` and use `app.run_test()` — not `app.run()`.
- `discover_recipes` and `find_repo_root` are monkeypatched in every Pilot test
  (the existing tests all do this — `test_tui.py:246-247`).

---

## Phase 4 — Verification & docs

### 4.1 Full verification

- [ ] `just test` — full suite green, no regressions.
- [ ] `just validate-static` and `just validate-unit` — still green
      (untouched, but confirm the recipes themselves were not disturbed).
- [ ] `git diff --stat` — only `ssis2sql/tui.py`, `tests/test_tui.py`,
      `README.md`, and this plan file changed.
- [ ] `git diff ssis2sql/tui.py` — `_run_recipe`, `discover_recipes`,
      `RecipePane`, `ConvertTreePane`, `DtsxPickerPane` bodies unchanged;
      `Recipe`, `_EXCLUDED_RECIPES` unchanged.
- [ ] Anti-pattern grep sweep on `ssis2sql/tui.py`:
  - `grep -nE "import validation|from validation" ssis2sql/tui.py` → 0 hits.
  - `grep -n "subprocess.run" ssis2sql/tui.py` → 1 hit (`discover_recipes`).
  - `grep -n "pytest.main" ssis2sql/tui.py` → 0 hits.
- [ ] Manual smoke: `just tui` → Validation pane → run each of the three
      layers; confirm streamed output, `[exit N]`, and a summary line for each.
      (Differential will show skips unless a SQL Server `.env` is configured —
      that is correct.)

### 4.2 README note

In `README.md`, the `## Validation` section (`README.md:215-323`), after the
`### Three layers` table (~`README.md:228`), add one line:

> All three layers are also runnable from the Textual TUI (`just tui` →
> **Validation** pane) without leaving the terminal.

### 4.3 Done criteria

Every box in the DoD table at the top is checked, and `just test` is green.

---

## Out of scope (explicit non-goals)

- No per-test results `DataTable` — the user chose the **Minimal** scope.
- No corpus-package picker (`pytest -k <pkg>`) — Minimal scope.
- No in-process call into `validation/` — the user chose **subprocess**.
- No disabling of the layer buttons during a run — `_run_recipe` does not
  disable buttons either; `exclusive=True` already serialises runs. Matching
  existing behaviour keeps the change surgical.
- `validate-cov` and `install-validation` stay as ordinary sidebar buttons.
  Folding them into the Validation pane is a possible follow-up, deliberately
  deferred to keep this change minimal.

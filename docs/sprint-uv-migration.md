# Plan: migrate msb_ssis2sql to `uv` + single unified install

**Goal.** Replace `python3 -m venv` + `pip install -e ".[extra]"` with `uv` across
the whole repo, and collapse the multiple optional-dependency installs (`dev`,
`web`, `validation`) so a single `just install` makes every recipe in the
justfile runnable.

**Non-goals.** No changes to `msb_ssis2sql` runtime behaviour, no public-API change,
no test rewrites. Console scripts (`msb_ssis2sql`, `msb_ssis2sql-web`) keep their names.

**Source of truth.** All `uv` claims in this plan are sourced from the upstream
docs surfaced in Phase 0; each phase cites the doc section it follows.

---

## Phase 0 — Documentation discovery (read once, refer back per phase)

### Allowed `uv` APIs (verified against `/astral-sh/uv` Context7 docs)

| API | Behaviour | Source |
|-----|-----------|--------|
| `uv sync` | Creates `.venv`, resolves `pyproject.toml`, installs project + deps. Auto-creates/refreshes `uv.lock`. | `docs/concepts/projects/sync.md` |
| `uv sync --locked` | Fails if `uv.lock` would change — use in CI for reproducibility. | `docs/guides/integration/github.md` |
| `uv sync --all-extras` | Includes every `[project.optional-dependencies]` entry. | `docs/concepts/projects/sync.md` |
| `uv sync --all-groups` / `--group <name>` | Includes PEP 735 `[dependency-groups]`. `dev` group included by default; suppress with `--no-dev`. | `docs/concepts/projects/sync.md` |
| `uv run <cmd>` | Runs `<cmd>` inside `.venv`, auto-syncing first. Use `uv run pytest`, `uv run python -m msb_ssis2sql ...`, etc. | `docs/concepts/projects/run.md` |
| `uv lock` | Explicit lockfile (re)generation. | `docs/concepts/projects/sync.md` |
| `uv python pin 3.X` | Writes `.python-version`; `uv` will fetch the interpreter if missing. | upstream `README.md` |
| `[dependency-groups]` (PEP 735) | Project-local groups not exposed in published metadata; preferred over `[project.optional-dependencies]` for dev tooling. | `changelogs/0.4.x.md` (0.4.27) |
| `uv sync --no-group <name>` | Excludes a specific dependency group from the sync without removing it from `default-groups`. Used by README's "skip validation" escape hatch. | `docs/concepts/projects/sync.md` |

Note on `[tool.uv] package`: when `[build-system]` is defined (we keep setuptools), `uv` installs the project by default per `docs/concepts/projects/config.md` — `package = true` is NOT required and adds no behaviour. Plan deliberately omits it.

### Anti-patterns (do NOT do)

- **Do not** invent `uv install` — the command is `uv sync` (or `uv add` to add a dep).
- **Do not** call `.venv/bin/python` in justfile recipes once migration is done; use `uv run …` so sync happens automatically.
- **Do not** mix `pip install -e .` with `uv sync` — choose one. Plan picks `uv sync`.
- **Do not** drop `[build-system]`; without it `uv` won't install `msb_ssis2sql` itself, only deps (per `docs/concepts/projects/config.md`).
- **Do not** put `pytest`/`pytest-cov` in base `dependencies`; they belong in a PEP 735 group so wheels stay slim. (Single-install requirement is honoured by `default-groups`, not by polluting base deps.)
- **Do not** add `[tool.uv] package = true` — when `[build-system]` is defined the project is installed by default; the flag is for projects that have no build system or want to override an opt-out.

### Current-state evidence (Read this session)

- `pyproject.toml` lines 14–28: base deps `loguru,textual`; extras `dev`, `web`, `validation`.
- `justfile` lines 6–8, 57–59, 66–68: three separate `install*` recipes, each rebuilds `.venv` via `python3 -m venv` + `pip install -e ".[group]"`.
- `justfile` lines 11–84: every other recipe hard-codes `.venv/bin/python …`.
- `.github/workflows/*.yml` lines 38–47: CI calls `just install-validation` + `just validate-static` + `just validate-unit` on Python 3.14.
- `msb_ssis2sql/web.py:35`: user-visible error string `"Run 'just install-web' (or 'pip install msb_ssis2sql[web]')."` — must update.
- `README.md` lines 26–28, 321: install docs reference `just install` and `pip install -e ".[dev]"`.
- `validation/capture/RUNBOOK.md:33`: Windows install hint references `.venv\Scripts\pip install -e ".[validation]"`.

### Key decision recorded for this plan

The user requested "one installation for all of the repo's functionality." The
chosen shape:

- **Base `[project.dependencies]`** keeps only runtime deps actually imported by `msb_ssis2sql` (`loguru`, `textual`).
- **`[dependency-groups]`** holds three groups: `dev`, `web`, `validation`.
- **`[tool.uv] default-groups = ["dev", "web", "validation"]`** so plain `uv sync` (and any `uv run` from the justfile) installs *everything* — single command, single environment, no `--all-extras` / `--all-groups` flag needed on the happy path.
- **Caveat (surface in README, not hidden):** the `validation` group pulls `pyodbc`. With the post-bump floor (`pyodbc>=5.3`, see Phase 1) prebuilt cp314 arm64 wheels exist, so `uv sync` itself succeeds. The failure mode is a **runtime `ImportError`** the first time `import pyodbc` runs: the wheel ships without an ODBC driver, so the dynamic loader cannot find `libodbc.dylib`. macOS users need `brew install unixodbc` once to put the dylib on the loader path; Linux users need `unixodbc-dev` (CI image already has it). Users who don't need differential validation can skip the group entirely with `uv sync --no-group validation` — that is the only documented "less than everything" path.

If the user later objects to pyodbc being default, the trivial alternative is to
demote `validation` back to a non-default group: `default-groups = ["dev", "web"]`
and require `just install-validation` (which would call `uv sync --group validation`).

### Open questions to confirm before Phase 1

1. Commit `uv.lock`? (Plan assumes **yes** — standard `uv` practice, matches CI `--locked`.)
2. Keep `setuptools` as build backend, or switch to `hatchling`? (Plan assumes **keep setuptools** — no consumer benefit to switching, smaller diff.)
3. Pin Python via `.python-version`? (Plan assumes **yes**, value `3.14` to match CI.)

---

## Phase 1 — Restructure `pyproject.toml`

### What to do (copy this exact shape)

Replace the current `[project.optional-dependencies]` block with PEP 735
`[dependency-groups]` (per `docs/concepts/projects/dependencies.md`), add
`[tool.uv]` with `default-groups`, and add a `.python-version` file.

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "msb_ssis2sql"
version = "0.1.0"
description = "Convert SSIS data-flow transformations into consolidated, behaviour-equivalent T-SQL."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "Linus McManamey" }]
keywords = ["ssis", "dtsx", "etl", "t-sql", "sql-server", "transpiler"]
dependencies = ["loguru>=0.7", "textual>=8.2"]

[project.scripts]
msb_ssis2sql = "msb_ssis2sql.cli:main"
msb_ssis2sql-web = "msb_ssis2sql.web:main"

[dependency-groups]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "pytest-asyncio>=1.3",
    "ruff>=0.5",
    "mypy>=1.10",
]
web = ["textual-serve>=1.1"]
validation = [
    "pyodbc>=5.3",
    "pandas>=3.0",
    "pyarrow>=24.0",
    "sqlglot>=25.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[tool.uv]
default-groups = ["dev", "web", "validation"]

[tool.setuptools.packages.find]
include = ["msb_ssis2sql*", "validation*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
asyncio_mode = "auto"
markers = ["validation: differential validation (needs SQL Server)"]

[tool.coverage.run]
omit = ["validation/tests/*", "validation/test_*.py", "validation/conftest.py"]

[tool.coverage.report]
exclude_lines = ["pragma: no cover", "if __name__ == .__main__.:"]
```

Then create `.python-version`:

```
3.14
```

Note: `validation`'s `pytest` / `pytest-cov` duplicates from the old extra are
**removed** — they live in the `dev` group, which is default-on, so they're
always present.

### Verification checklist

- [ ] `uv sync` succeeds from a clean repo (no `.venv`, no `uv.lock`).
- [ ] `.venv` is created at repo root.
- [ ] `uv.lock` is created.
- [ ] `uv run python -c "import msb_ssis2sql, validation, textual_serve, pyodbc"` exits 0 (all four import).
- [ ] `uv run pytest -q` runs and is green (proves `dev` group active).
- [ ] `grep -n "optional-dependencies" pyproject.toml` returns nothing.

### Anti-pattern guards

- Don't leave `[project.optional-dependencies]` behind — purge it.
- Don't add a `pytest` to base `dependencies` — keep it in `dev` group.
- Don't add `[tool.uv] package = true` — redundant when `[build-system]` is defined; the project is installed by default. Reviewer caught this in round 1; do not re-introduce.
- Don't downgrade the pyodbc/pandas/pyarrow floors below the cp314-wheel-supporting versions (`pyodbc>=5.3`, `pandas>=3.0`, `pyarrow>=24.0`) — older floors force source builds on Python 3.14.

---

## Phase 2 — Rewrite `justfile` recipes to use `uv`

### What to do

Collapse the three `install*` recipes into one and replace every
`.venv/bin/python …` invocation with `uv run python …`. Copy this complete
file:

```make
# Default recipe: run Claude in max-effort auto mode.
opus:
    @claude --dangerously-skip-permissions --effort 'max' --enable-auto-mode "/caveman"

# Sync the project venv with every dependency group (single unified install).
install:
    uv sync

# Refresh the lockfile (use after editing pyproject.toml dependencies).
lock:
    uv lock

# Run the test suite.
test:
    uv run pytest

# Run the test suite with a line-coverage report.
cov:
    uv run pytest --cov=msb_ssis2sql --cov-report=term-missing

# Static lint via ruff (PEP 8 + pyflakes).
lint:
    uv run ruff check .

# Type-check the package with mypy.
typecheck:
    uv run mypy msb_ssis2sql validation

# Convert a .dtsx file to T-SQL and write to OUTFILE.
# Usage: just migrate-file path/to/pkg.dtsx path/to/output.sql
migrate-file FILE OUTFILE:
    uv run python -m msb_ssis2sql convert '{{FILE}}' -o '{{OUTFILE}}'

# Print the parsed component graph. Usage: just inspect path/to/pkg.dtsx
inspect FILE:
    uv run python -m msb_ssis2sql inspect '{{FILE}}'

# Convert the bundled example package and print the consolidated SQL.
demo:
    uv run python -m msb_ssis2sql convert examples/sales_etl.dtsx

# Recursively convert every .dtsx under INPUT into OUTPUT, mirroring the input tree.
# Usage: just migrate-directory path/to/input path/to/output
migrate-directory INPUT OUTPUT:
    uv run python -m msb_ssis2sql convert-tree '{{INPUT}}' '{{OUTPUT}}'

# Convert every .dtsx under examples/samples into generated_scripts/*.sql.
# Build copies under bin/ are skipped. Warnings are embedded in each .sql header.
convert-samples:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p generated_scripts
    count=0
    while IFS= read -r -d '' src; do
        out="generated_scripts/$(basename "${src%.dtsx}").sql"
        echo "converting ${src#examples/samples/} -> ${out}"
        uv run python -m msb_ssis2sql convert "$src" -o "$out" -vv
        count=$((count + 1))
    done < <(find examples/samples -name '*.dtsx' -not -path '*/bin/*' -print0 | sort -z)
    echo "done: ${count} package(s) converted into generated_scripts/"

# Launch the Textual control-panel UI for msb_ssis2sql.
tui:
    uv run python -m msb_ssis2sql.tui

# Serve the Textual TUI in a browser via textual-serve (default localhost:8000).
web HOST="localhost" PORT="8000":
    uv run python -m msb_ssis2sql.web --host '{{HOST}}' --port '{{PORT}}'

# Run the full differential validation suite (needs SQL Server; skips until golden exists).
validate:
    uv run pytest validation/ -m validation

# Run the framework's own unit tests (no SQL Server required).
validate-unit:
    uv run pytest validation/tests

# Run framework unit tests with a coverage report for the validation package.
validate-cov:
    uv run pytest validation/tests --cov=validation --cov-report=term-missing --cov-report=json

# Run the static structural checks (no SQL Server required).
validate-static:
    uv run pytest validation/test_static.py

# Remove the virtual environment, lockfile-tracked caches, build artefacts.
clean:
    rm -rf .venv .pytest_cache build dist *.egg-info
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
```

### Recipes removed

- `install-web` → merged into `install`.
- `install-validation` → merged into `install`.
- The `if [ ! -x .venv/bin/python ]` guard in `convert-samples` → unnecessary because `uv run` syncs automatically.

### Verification checklist

- [ ] `just install` from a clean repo creates `.venv` and `uv.lock`.
- [ ] `just test` is green.
- [ ] `just demo` prints SQL to stdout.
- [ ] `just tui` opens the TUI.
- [ ] `just web` boots the server on `localhost:8000`.
- [ ] `just validate-unit` and `just validate-static` both green.
- [ ] `grep -n "\.venv/bin\|python3 -m venv\|pip install" justfile` returns **nothing**.
- [ ] `just clean && just install` round-trips cleanly.

### Anti-pattern guards

- Don't add `uv pip install …` — use `uv sync` / `uv add` (project mode).
- Don't add `source .venv/bin/activate` to recipes — `uv run` handles activation.
- Don't keep the `install-web` / `install-validation` recipes "for compatibility" — delete them; the goal is one install path.

---

## Phase 3 — Propagate the change to docs, web error, and CI

### 3a. `msb_ssis2sql/web.py` — update the user-facing import-error hint

Current (lines 32–38):

```python
except ImportError as exc:
    print(
        "msb_ssis2sql-web: textual-serve is not installed. "
        "Run 'just install-web' (or 'pip install msb_ssis2sql[web]').",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc
```

After:

```python
except ImportError as exc:
    print(
        "msb_ssis2sql-web: textual-serve is not installed. "
        "Run 'just install' (or 'uv sync').",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc
```

Also update `tests/test_web.py:77`. Current assertion:

```python
assert "install-web" in err
```

Replace with:

```python
assert "just install" in err
```

(`just install` is the stable substring of the new error message. Don't pin to
`uv sync` — the prose order may change but `just install` is the canonical entry
point referenced first.)

### 3b. `README.md` — install section

Replace lines 23–32 (the old `## Install` heading through the stale "One
runtime dependency — loguru" sentence) with:

```markdown
## Install

Prerequisite: [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS).

```sh
just install            # one command — installs msb_ssis2sql + every dependency group
# or, manually:
uv sync
```

Single install covers the CLI, the TUI, the web server, and the differential
validation framework. Runtime deps: `loguru` (logging) and `textual` (TUI).
Python 3.14 is pinned via `.python-version`; `uv` will fetch it automatically
if it's not present.

macOS users running the validation layer also need `brew install unixodbc`
once so `import pyodbc` finds `libodbc.dylib` at runtime. Users who don't
need differential validation can skip the group entirely with
`uv sync --no-group validation`.
```

Note: the old "One runtime dependency — loguru" sentence is *intentionally
dropped* — base deps are now `loguru + textual` (see Phase 1 pyproject shape),
so the singular claim is stale.

Also patch:

**Live (non-historical) docs — full edit:**

- `README.md:212` (`.venv/bin/python -m pytest` → `uv run pytest`).
- `README.md:310` (`just install-validation` reference inside the CI/install diagram → `just install`).
- `README.md:321` (`just install-validation` → `just install`).
- `validation/capture/RUNBOOK.md` — Windows operator runbook, full migration:
  - **Lines 31–34 (collapse).** The current PowerShell fence contains two lines (`python -m venv .venv` then `.venv\Scripts\pip install -e ".[validation]"`). Replace the entire fenced block with a single line:
    ```powershell
    uv sync
    ```
    `uv sync` creates `.venv` itself; the explicit `python -m venv` step is redundant and would contradict the uv workflow.
  - Line 39: `.venv\Scripts\python -c "import validation.capture.capture; print('OK')"` → `uv run python -c "import validation.capture.capture; print('OK')"`
  - Line 70: `.venv\Scripts\python -c "` → `uv run python -c "`
  - Line 87: `.venv\Scripts\python -m validation.capture.capture --package-dir ...` → `uv run python -m validation.capture.capture --package-dir ...`
  - Line 110: `.venv\Scripts\python -c "` → `uv run python -c "`
- `validation/capture/capture.ps1` — PowerShell launcher, full migration:
  - Line 31 (docstring): `Defaults to .venv\Scripts\python.exe (Windows venv convention).` → drop the `.venv\Scripts\` reference; replace with `Defaults to running via 'uv run python'.`
  - Line 60 (param block): `[string] $VenvPython = ".venv\Scripts\python.exe"` → remove the `$VenvPython` parameter entirely (no path is needed because `uv` resolves the interpreter). The param block loses one line.
  - **Lines 75–79 (resolution + Test-Path).** The current shape is `$python = Join-Path $repoRoot $VenvPython` followed by `if (-not (Test-Path $python)) { Write-Error ...; exit ... }`. Replace BOTH with:
    ```powershell
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv not found on PATH. Install uv (https://docs.astral.sh/uv/) and run: uv sync"
        exit 1
    }
    ```
    Drop the `Join-Path` line entirely — `uv` is resolved on PATH, not inside `$repoRoot`.
  - Line 77 (the `Write-Error` string referenced above is replaced by the new Get-Command block; line 77 in the *new* file no longer exists in the old form).
  - Line 121 (invocation): `& $python @captureArgs` → `& uv run python @captureArgs`.
  - **Implementer hygiene:** Read the full file before editing — if any other `& $python …` or `$VenvPython` references appear that the line cites above don't cover, swap them with the same `uv run python` pattern.

**Historical sprint/epic docs — POLICY: leave content as-is, add grep exemption.**

These files are *records* of past sprints and reflect the install path in force
at the time. Rewriting them changes the historical record. The Phase 4 grep
gate at step 11 is amended (see below) to add a per-file exemption list rather
than touching the records:

- `docs/sprint-validation-framework.md` — historical, exempt.
- `docs/sprint-coverage-95.md` — historical, exempt.
- `docs/epic-1-batch-convert-tui.md` — historical, exempt.
- `docs/plan-tui-tabs.md` — historical, exempt.
- `docs/plan-tui-validation-runner.md` — historical, exempt.

The exempt list is enumerated explicitly in Phase 4 step 11 below so the gate is
satisfiable. If the user later prefers to rewrite them, that is a follow-up
ticket — out of scope for this sprint.

### 3c. `.github/workflows/*.yml` — CI

**Retain** the existing `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38` step
(explicit setup-python gives reliable interpreter caching even though `setup-uv`
can fetch via `.python-version`). **Replace** only the `Install validation
dependencies` step. The keep/add/remove diff:

- Keep: `Checkout`, `Set up Python 3.14` (existing `actions/setup-python` SHA), `Install just`.
- Add: `Install uv`, `Sync project`, `Lint`, `Typecheck`.
- Remove: `Install validation dependencies` (its `run: just install-validation` line).

```yaml
      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0

      - name: Sync project
        run: uv sync --locked

      - name: Lint
        continue-on-error: true
        run: just lint

      - name: Typecheck
        continue-on-error: true
        run: just typecheck
```

The `--locked` flag (per `docs/guides/integration/github.md`) makes CI fail if
`uv.lock` is stale. Keep the existing `just validate-static` and `just
validate-unit` steps unchanged — they now resolve to `uv run pytest …` and pick
up the synced env. `lint` and `typecheck` carry `continue-on-error: true` so
the first CI pass establishes a baseline without breaking the build; remove the
flag in a follow-up sprint once the codebase is ruff/mypy-clean.

**Action-pinning note.** The `setup-uv` SHA above is the one from upstream
docs; before commit, verify it points at `v8.1.0` by running
`gh api /repos/astral-sh/setup-uv/git/refs/tags/v8.1.0` and confirming the
returned SHA matches.

### Verification checklist

- [ ] `git grep -n "install-web\|install-validation\|pip install -e" -- ':!docs/sprint-*.md' ':!docs/epic-*.md' ':!docs/plan-tui-*.md' ':!.repomix-output.xml' ':!.repomix-textual.xml'` returns zero matches.
- [ ] `tests/test_web.py` passes after the line-77 assertion update.
- [ ] CI workflow renders valid YAML (`yamllint` or `actionlint` if available).
- [ ] CI green on a PR.

### Anti-pattern guards

- Don't bypass `--locked` in CI to "just make it pass" — fix the lockfile instead.
- Don't unpin actions to mutable tags (`@v8`) — keep SHA pinning per existing repo convention.

---

## Phase 4 — Final verification + cleanup

### Sequence

1. `rm -rf .venv *.egg-info` — start clean. `.venv-desloppify/` is another tool's state, already gitignored, and is intentionally NOT touched here.
2. `just install` — proves the single-install path.
3. `ls -la .venv uv.lock .python-version` — three artifacts exist.
4. `uv run python -c "import msb_ssis2sql, validation, textual, textual_serve, loguru, pyodbc, pandas, pyarrow, sqlglot, yaml, dotenv"` — every dep importable from one env.
5. `just test` — full existing pytest suite green; new behaviour adds zero tests (run `uv run pytest --collect-only -q | tail -1` first to record the baseline count, then verify it stays equal post-migration).
6. `just lint` and `just typecheck` — establish baseline. Failures here do NOT block the sprint; they enter a follow-up ticket. The sprint deliverable is the *plumbing*, not codebase cleanliness.
7. `just validate-static && just validate-unit` — validation framework green.
8. `just demo`, `just tui` (Ctrl-C immediately), `just web &` + `curl -s localhost:8000 | head` then kill — runtime entry points work.
9. `just clean && just install` — round-trip.
10. `git status` — every file listed below is the only one changed; nothing else drifted:
   - `pyproject.toml`
   - `justfile`
   - `.python-version` (new)
   - `uv.lock` (new)
   - `msb_ssis2sql/web.py`
   - `tests/test_web.py`
   - `README.md`
   - `validation/capture/RUNBOOK.md`
   - `validation/capture/capture.ps1`
   - `.github/workflows/validation.yml`
   - (Historical docs intentionally NOT in this list — see §3b policy.)
11. **Live-source grep gate.** Run:
    ```sh
    git grep -nE '\.venv/bin|\.venv\\Scripts|python3? -m venv' -- \
        ':!docs/sprint-*.md' ':!docs/epic-*.md' ':!docs/plan-tui-*.md' \
        ':!.repomix-output.xml' ':!.repomix-textual.xml'
    ```
    Must return zero matches. The Windows `.venv\Scripts` variant is included
    so the RUNBOOK and capture.ps1 migration is enforced. The exempt globs
    cover historical sprint/epic artifacts and the repomix snapshots —
    intentionally excluded per §3b.
12. **Install-string gate.** Same exemption set:
    ```sh
    git grep -n 'install-web\|install-validation\|pip install -e' -- \
        ':!docs/sprint-*.md' ':!docs/epic-*.md' ':!docs/plan-tui-*.md' \
        ':!.repomix-output.xml' ':!.repomix-textual.xml'
    ```
    Must return zero matches.
13. **Cross-platform `uv.lock` advisory.** After committing `uv.lock`, push a
    draft PR and confirm CI's `uv sync --locked` succeeds on the Linux runner.
    If it fails, regenerate locally with `uv lock` and re-push — do NOT bypass
    `--locked`. `uv` lockfiles are universal-resolution per `docs/concepts/resolution.md`,
    but pyodbc/pandas/pyarrow have platform-specific wheels and this is the
    cheapest confirmation that the lockfile travels.

### Anti-pattern guards (final sweep)

- No recipe still calls `.venv/bin/python` (Unix) or `.venv\Scripts\python` (Windows).
- No live (non-historical) doc still tells the user to run `pip install -e`.
- `validation/capture/capture.ps1` no longer hardcodes `.venv\Scripts\python.exe`; it invokes `uv run python ...` instead.
- `pyproject.toml` has no `[project.optional-dependencies]` table.
- `uv.lock` is committed.
- `.gitignore` is unchanged — verified clean (no `uv.lock` or `.python-version` entries needed, nothing to add).

---

## Rollback plan (if something blocks merge)

Each phase is one or two files. To roll back:

1. `git checkout HEAD~ -- pyproject.toml justfile` reverts the structural change.
2. `rm uv.lock .python-version` clears the new artifacts.
3. `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` reproduces the old environment.

No data migrations, no schema changes, no runtime API surface change — rollback is purely tooling.

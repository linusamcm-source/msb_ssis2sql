# Plan: migrate ssis2sql to `uv` + single unified install

**Goal.** Replace `python3 -m venv` + `pip install -e ".[extra]"` with `uv` across
the whole repo, and collapse the multiple optional-dependency installs (`dev`,
`web`, `validation`) so a single `just install` makes every recipe in the
justfile runnable.

**Non-goals.** No changes to `ssis2sql` runtime behaviour, no public-API change,
no test rewrites. Console scripts (`ssis2sql`, `ssis2sql-web`) keep their names.

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
| `uv run <cmd>` | Runs `<cmd>` inside `.venv`, auto-syncing first. Use `uv run pytest`, `uv run python -m ssis2sql ...`, etc. | `docs/concepts/projects/run.md` |
| `uv lock` | Explicit lockfile (re)generation. | `docs/concepts/projects/sync.md` |
| `uv python pin 3.X` | Writes `.python-version`; `uv` will fetch the interpreter if missing. | upstream `README.md` |
| `[tool.uv] package = true` | Force editable install when `[build-system]` is defined. | `docs/concepts/projects/config.md` |
| `[dependency-groups]` (PEP 735) | Project-local groups not exposed in published metadata; preferred over `[project.optional-dependencies]` for dev tooling. | `changelogs/0.4.x.md` (0.4.27) |

### Anti-patterns (do NOT do)

- **Do not** invent `uv install` — the command is `uv sync` (or `uv add` to add a dep).
- **Do not** call `.venv/bin/python` in justfile recipes once migration is done; use `uv run …` so sync happens automatically.
- **Do not** mix `pip install -e .` with `uv sync` — choose one. Plan picks `uv sync`.
- **Do not** drop `[build-system]`; without it `uv` won't install `ssis2sql` itself, only deps (per `docs/concepts/projects/config.md`).
- **Do not** put `pytest`/`pytest-cov` in base `dependencies`; they belong in a PEP 735 group so wheels stay slim. (Single-install requirement is honoured by `default-groups`, not by polluting base deps.)

### Current-state evidence (Read this session)

- `pyproject.toml` lines 14–28: base deps `loguru,textual`; extras `dev`, `web`, `validation`.
- `justfile` lines 6–8, 57–59, 66–68: three separate `install*` recipes, each rebuilds `.venv` via `python3 -m venv` + `pip install -e ".[group]"`.
- `justfile` lines 11–84: every other recipe hard-codes `.venv/bin/python …`.
- `.github/workflows/*.yml` lines 38–47: CI calls `just install-validation` + `just validate-static` + `just validate-unit` on Python 3.14.
- `ssis2sql/web.py:35`: user-visible error string `"Run 'just install-web' (or 'pip install ssis2sql[web]')."` — must update.
- `README.md` lines 26–28, 321: install docs reference `just install` and `pip install -e ".[dev]"`.
- `validation/capture/RUNBOOK.md:33`: Windows install hint references `.venv\Scripts\pip install -e ".[validation]"`.

### Key decision recorded for this plan

The user requested "one installation for all of the repo's functionality." The
chosen shape:

- **Base `[project.dependencies]`** keeps only runtime deps actually imported by `ssis2sql` (`loguru`, `textual`).
- **`[dependency-groups]`** holds three groups: `dev`, `web`, `validation`.
- **`[tool.uv] default-groups = ["dev", "web", "validation"]`** so plain `uv sync` (and any `uv run` from the justfile) installs *everything* — single command, single environment, no `--all-extras` / `--all-groups` flag needed on the happy path.
- **Caveat (surface in README, not hidden):** the `validation` group pulls `pyodbc`, which needs a system ODBC driver to build on first install. macOS users without `unixodbc` will see a build error. Users who don't need differential validation can run `uv sync --no-group validation` to skip it. This is the only documented "less than everything" path.

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
name = "ssis2sql"
version = "0.1.0"
description = "Convert SSIS data-flow transformations into consolidated, behaviour-equivalent T-SQL."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "Linus McManamey" }]
keywords = ["ssis", "dtsx", "etl", "t-sql", "sql-server", "transpiler"]
dependencies = ["loguru>=0.7", "textual>=8.2"]

[project.scripts]
ssis2sql = "ssis2sql.cli:main"
ssis2sql-web = "ssis2sql.web:main"

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
    "pyodbc>=5.1",
    "pandas>=2.2",
    "pyarrow>=16.0",
    "sqlglot>=25.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[tool.uv]
default-groups = ["dev", "web", "validation"]
package = true

[tool.setuptools.packages.find]
include = ["ssis2sql*", "validation*"]

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
- [ ] `uv run python -c "import ssis2sql, validation, textual_serve, pyodbc"` exits 0 (all four import).
- [ ] `uv run pytest -q` runs and is green (proves `dev` group active).
- [ ] `grep -n "optional-dependencies" pyproject.toml` returns nothing.

### Anti-pattern guards

- Don't leave `[project.optional-dependencies]` behind — purge it.
- Don't add a `pytest` to base `dependencies` — keep it in `dev` group.
- Don't drop `package = true` — without it, `uv` may skip building/installing `ssis2sql` itself when only deps are needed, breaking console scripts.

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
    uv run pytest --cov=ssis2sql --cov-report=term-missing

# Static lint via ruff (PEP 8 + pyflakes).
lint:
    uv run ruff check .

# Type-check the package with mypy.
typecheck:
    uv run mypy ssis2sql validation

# Convert a .dtsx file to T-SQL and write to OUTFILE.
# Usage: just migrate-file path/to/pkg.dtsx path/to/output.sql
migrate-file FILE OUTFILE:
    uv run python -m ssis2sql convert '{{FILE}}' -o '{{OUTFILE}}'

# Print the parsed component graph. Usage: just inspect path/to/pkg.dtsx
inspect FILE:
    uv run python -m ssis2sql inspect '{{FILE}}'

# Convert the bundled example package and print the consolidated SQL.
demo:
    uv run python -m ssis2sql convert examples/sales_etl.dtsx

# Recursively convert every .dtsx under INPUT into OUTPUT, mirroring the input tree.
# Usage: just migrate-directory path/to/input path/to/output
migrate-directory INPUT OUTPUT:
    uv run python -m ssis2sql convert-tree '{{INPUT}}' '{{OUTPUT}}'

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
        uv run python -m ssis2sql convert "$src" -o "$out" -vv
        count=$((count + 1))
    done < <(find examples/samples -name '*.dtsx' -not -path '*/bin/*' -print0 | sort -z)
    echo "done: ${count} package(s) converted into generated_scripts/"

# Launch the Textual control-panel UI for ssis2sql.
tui:
    uv run python -m ssis2sql.tui

# Serve the Textual TUI in a browser via textual-serve (default localhost:8000).
web HOST="localhost" PORT="8000":
    uv run python -m ssis2sql.web --host '{{HOST}}' --port '{{PORT}}'

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

### 3a. `ssis2sql/web.py` — update the user-facing import-error hint

Current (lines 32–38):

```python
except ImportError as exc:
    print(
        "ssis2sql-web: textual-serve is not installed. "
        "Run 'just install-web' (or 'pip install ssis2sql[web]').",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc
```

After:

```python
except ImportError as exc:
    print(
        "ssis2sql-web: textual-serve is not installed. "
        "Run 'just install' (or 'uv sync').",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc
```

Also update any matching test assertion in `tests/test_web.py` (verify with
`grep -n "install-web\|ssis2sql\[web\]" tests/test_web.py` before editing).

### 3b. `README.md` — install section

Replace lines 23–32 with:

```markdown
## Install

Prerequisite: [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS).

```sh
just install            # one command — installs ssis2sql + every dependency group
# or, manually:
uv sync
```

Single install covers the CLI, the TUI, the web server, and the differential
validation framework. macOS users without `unixodbc` who don't need the
validation layer can run `uv sync --no-group validation` instead.

Python 3.14 is pinned via `.python-version`; `uv` will fetch it automatically
if it's not present.
```

Also patch:

- `README.md:212` (`.venv/bin/python -m pytest` → `uv run pytest`).
- `README.md:321` (`just install-validation` → `just install`).
- `docs/sprint-validation-framework.md` lines 556, 766 (replace `pip install -e ".[validation]"` references with `uv sync` — these are historical docs, mark inline as "[updated for uv]" rather than rewriting the surrounding prose).
- `docs/sprint-coverage-95.md:156` and `docs/epic-1-batch-convert-tui.md` lines 111, 357, 740 — same surgical inline updates.
- `validation/capture/RUNBOOK.md:33` (Windows install hint) → `uv sync` (cross-platform).

> Scope discipline: do NOT rewrite the surrounding prose in these historical
> sprint/epic docs. Only swap the install command lines.

### 3c. `.github/workflows/*.yml` — CI

Replace the `Install just` + `Install validation dependencies` steps with:

```yaml
      - name: Install uv
        uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b  # v8.1.0

      - name: Install just
        uses: extractions/setup-just@dd310ad5a97d8e7b41793f8ef055398d51ad4de6  # v2.0.0

      - name: Sync project
        run: uv sync --locked

      - name: Lint
        run: just lint

      - name: Typecheck
        run: just typecheck
```

The `--locked` flag (per `docs/guides/integration/github.md`) makes CI fail if
`uv.lock` is stale, which is what we want. Keep the existing `just
validate-static` and `just validate-unit` steps unchanged — they now resolve to
`uv run pytest …` and pick up the synced env. `lint` and `typecheck` are
non-blocking initially (allow `continue-on-error: true` on each step if the
existing codebase is not yet ruff/mypy-clean — the first pass exists to
establish a baseline, not to break CI).

**Action-pinning note.** The `setup-uv` SHA above is the one from upstream
docs; before commit, verify it points at `v8.1.0` by running
`gh api /repos/astral-sh/setup-uv/git/refs/tags/v8.1.0` and confirming the
returned SHA matches.

### Verification checklist

- [ ] `grep -rn "install-web\|install-validation\|pip install -e" .` returns only intentional matches (e.g. doc archives explicitly marked historical).
- [ ] `tests/test_web.py` still passes after the error-string change.
- [ ] CI workflow renders valid YAML (`yamllint` or `actionlint` if available).
- [ ] CI green on a PR.

### Anti-pattern guards

- Don't bypass `--locked` in CI to "just make it pass" — fix the lockfile instead.
- Don't unpin actions to mutable tags (`@v8`) — keep SHA pinning per existing repo convention.

---

## Phase 4 — Final verification + cleanup

### Sequence

1. `rm -rf .venv .venv-desloppify *.egg-info` — start clean (the existing `.venv-desloppify` is unrelated to this migration and stays gitignored).
2. `just install` — proves the single-install path.
3. `ls -la .venv uv.lock .python-version` — three artifacts exist.
4. `uv run python -c "import ssis2sql, validation, textual, textual_serve, loguru, pyodbc, pandas, pyarrow, sqlglot, yaml, dotenv"` — every dep importable from one env.
5. `just test` — full suite green.
6. `just lint` and `just typecheck` — establish baseline. Failures here do NOT block the sprint; they enter a follow-up ticket. The sprint deliverable is the *plumbing*, not codebase cleanliness.
7. `just validate-static && just validate-unit` — validation framework green.
8. `just demo`, `just tui` (Ctrl-C immediately), `just web &` + `curl -s localhost:8000 | head` then kill — runtime entry points work.
9. `just clean && just install` — round-trip.
10. `git status` — every file listed below is the only one changed; nothing else drifted:
   - `pyproject.toml`
   - `justfile`
   - `.python-version` (new)
   - `uv.lock` (new)
   - `ssis2sql/web.py`
   - `tests/test_web.py`
   - `README.md`
   - `docs/sprint-validation-framework.md`
   - `docs/sprint-coverage-95.md`
   - `docs/epic-1-batch-convert-tui.md`
   - `validation/capture/RUNBOOK.md`
   - `.github/workflows/<workflow>.yml`
11. `git grep -n "\.venv/bin\|python3 -m venv"` — only matches should be inside `.repomix-output.xml` (stale snapshot, ignored) and inside `.venv*/` (ignored). No matches in tracked source/docs.
12. `git grep -n "install-web\|install-validation\|pip install -e"` — same exclusion rule.

### Anti-pattern guards (final sweep)

- No recipe still calls `.venv/bin/python`.
- No doc still tells the user to run `pip install -e`.
- `pyproject.toml` has no `[project.optional-dependencies]` table.
- `uv.lock` is committed (not gitignored).
- `.gitignore` does NOT add `uv.lock` (verify); it MAY add `.python-version`-style overrides per user preference, but the plan recommends committing `.python-version` too so the project pins consistently.

---

## Rollback plan (if something blocks merge)

Each phase is one or two files. To roll back:

1. `git checkout HEAD~ -- pyproject.toml justfile` reverts the structural change.
2. `rm uv.lock .python-version` clears the new artifacts.
3. `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` reproduces the old environment.

No data migrations, no schema changes, no runtime API surface change — rollback is purely tooling.

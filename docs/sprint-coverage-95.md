# Sprint Plan — Python Test Coverage 76% → 95%

**Repo:** `ssis2sql` · **Target branch:** `desloppify/code-health` · **Plan owner:** Linus McManamey
**Sprint type:** Test backfill (characterization tests against existing, working code — *not* TDD)
**Coverage gate:** `--cov-fail-under=95`

---

## 1. Objective

Raise `pytest` line coverage of the `ssis2sql` package from **76% to ≥95%** by adding
test files only. **No production source file may be modified.** If a test would fail,
that is either (a) a genuine bug — surface it to the team lead, do not fix it and do
not encode the bug as correct — or (b) a wrong test assumption — fix the test.

## 2. Baseline (measured 2026-05-18)

```
pytest --cov=ssis2sql --cov-report=term-missing
TOTAL  2061 stmts  499 miss  76%
```

`pytest-cov` is **not** yet a declared dependency (Story 0 fixes this). 4 existing test
files: `test_expressions.py`, `test_generator.py`, `test_observability.py`,
`test_parser.py` — **do not edit them**; each story adds a new file.

## 3. Coverage math

95% of 2061 statements = **1958 covered**. Currently 1562 covered. Gap = **+396**.
All six test-writing stories together deliver ≈ **+457**, landing ≈ **98%** — the
3-point buffer absorbs any branch that proves contrived.

| File | Stmts | Miss | Now | Target | Story |
|---|---|---|---|---|---|
| `cli.py` | 76 | 76 | 0% | 98% | 1 |
| `__main__.py` | 3 | 3 | 0% | 100% | 1 |
| `transforms/set_ops.py` | 105 | 64 | 39% | 96% | 2 |
| `transforms/flow.py` | 53 | 35 | 34% | 98% | 2 |
| `transforms/column_ops.py` | 67 | 31 | 54% | 97% | 2 |
| `transforms/source.py` | 45 | 23 | 49% | 98% | 2 |
| `transforms/lookup.py` | 91 | 23 | 75% | 96% | 3 |
| `transforms/destination.py` | 52 | 14 | 73% | 96% | 3 |
| `transforms/grouping.py` | 82 | 14 | 83% | 96% | 3 |
| `transforms/conditional_split.py` | 41 | 7 | 83% | 97% | 3 |
| `transforms/base.py` | 181 | 38 | 79% | 96% | 3 |
| `expressions/translator.py` | 160 | 38 | 76% | 95% | 4 |
| `expressions/lexer.py` | 116 | 17 | 85% | 96% | 4 |
| `expressions/parser.py` | 118 | 10 | 92% | 98% | 4 |
| `parser.py` | 215 | 25 | 88% | 95% | 5 |
| `generator.py` | 183 | 22 | 88% | 96% | 6 |
| `observability.py` | 64 | 15 | 77% | 96% | 6 |
| `sqltypes.py` | 51 | 15 | 71% | 98% | 6 |
| `component_types.py` | 18 | 7 | 61% | 100% | 6 |
| `graph.py` | 70 | 6 | 91% | 98% | 6 |
| `model.py` | 118 | 5 | 96% | 99% | 6 |
| `util.py` | 11 | 5 | 55% | 100% | 6 |
| `relation.py` | 28 | 4 | 86% | 98% | 6 |
| `dialect.py` | 33 | 2 | 94% | 100% | 6 |

## 4. Execution model

```
Story 0 (infra + builders)  ──blocks──►  Story 1 ┐
                                         Story 2 │
                                         Story 3 ├─ run in PARALLEL
                                         Story 4 │  (one agent each)
                                         Story 5 │
                                         Story 6 ┘
                                              │
                                              ▼
                                    Final verification (team lead)
```

- **Story 0 runs first, alone.** It is a hard dependency: it adds `pytest-cov`,
  coverage config, and `tests/_builders.py` (shared IR helpers Stories 2 & 3 import).
- **Stories 1–6 run concurrently.** Each owns exactly one new file and touches
  nothing else — zero merge conflicts by construction.
- Suggested agent for every story: **`python-pro`**.
- Environment per worktree: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
  (after Story 0 lands `pytest-cov` in the dev extra).

**Cumulative coverage if stories complete in order:**

| After | Coverage |
|---|---|
| Story 0 | 76% (config only) |
| + Story 1 | ~79% |
| + Story 2 | ~86% |
| + Story 3 | ~90% |
| + Story 4 | ~93% |
| + Story 5 | **~95%** ✅ |
| + Story 6 | ~98% |

## 5. Shared conventions — every agent reads this first

1. **Never modify any file under `ssis2sql/`.** Tests only.
2. **Never edit an existing test file.** Create the one new file your story names.
3. **Tests pass green on first run.** This is backfill against working code; a test
   asserts *current* behaviour. A red test means a real bug — report it, do not patch
   source, do not bury it.
4. **Style — match the repo.** See `tests/test_generator.py` and `tests/test_parser.py`:
   `from __future__ import annotations`, module docstring, plain `def test_*`, `assert`.
5. **Fixtures** live in `conftest.py`: `example_path` (str path to bundled
   `tests/examples/sales_etl.dtsx`) and `example_package` (parsed). Use them for
   end-to-end assertions.
6. **IR builders** for synthetic packages live in `tests/_builders.py` (created by
   Story 0). Import from there — do not hand-roll `Component`/`Package` graphs ad hoc.
7. **No network, no clock, no randomness** in assertions. `NEWID()`/`SYSDATETIME()`
   appear as literal SQL text — assert the text, never execute it.
8. **Target the exact uncovered lines.** Regenerate the gap map any time with:
   `.venv/bin/python -m pytest --cov=ssis2sql --cov-report=term-missing -q`
9. **Deliver via SendMessage** to the team lead when the story's DoD is met — do not
   describe results inline only.

---

## Story 0 — Coverage infrastructure & shared test builders

**Owner:** python-pro · **Files owned:** `pyproject.toml`, `justfile`, `conftest.py`, `tests/_builders.py`
**Depends on:** nothing · **Blocks:** Stories 1–6 · **Coverage delta:** +0 (enabling work)

**Developer notes**
- `pyproject.toml`: add `"pytest-cov>=4"` to `[project.optional-dependencies].dev`.
- `pyproject.toml`: add coverage config so unreachable lines leave the denominator —
  ```toml
  [tool.coverage.run]
  source = ["ssis2sql"]

  [tool.coverage.report]
  exclude_lines = ["pragma: no cover", "if __name__ == .__main__.:", "raise NotImplementedError"]
  ```
- `justfile`: add a `cov` recipe —
  ```
  cov:
      .venv/bin/python -m pytest --cov=ssis2sql --cov-report=term-missing --cov-fail-under=95
  ```
- `tests/_builders.py`: factory helpers so Stories 2 & 3 build synthetic component
  graphs without XML. Generalise the private `_minimal_package()` in
  `tests/test_generator.py` (read it first). Provide at minimum:
  - `make_source(name, columns, **props)` → an `OLEDB_SOURCE` `Component` with one output port
  - `make_component(kind, name, inputs=..., outputs=..., **props)` → a generic `Component`
  - `make_port(name, columns=...)`, `make_column(name, **kw)`
  - `single_flow_package(*components, paths=...)` → a `Package` with one `DataFlow`
  - `convert(package)` → thin wrapper over `convert_package` returning the result
- Keep `_builders.py` import-light (only `ssis2sql.model`) and **not** itself a test
  module (leading underscore keeps pytest from collecting it).

**Tasks**
1. Edit `pyproject.toml` (dep + coverage config).
2. Edit `justfile` (`cov` recipe).
3. Write `tests/_builders.py`.
4. Confirm `tests/__init__.py` does not shadow the new module.
5. Run `.venv/bin/pip install -e ".[dev]"` then `just cov` — suite still green, report prints.

**Acceptance criteria**
- GIVEN a fresh venv, WHEN `pip install -e ".[dev]"` runs, THEN `pytest-cov` installs.
- GIVEN `just cov`, WHEN it runs, THEN a `term-missing` table prints and the existing 57 tests pass.
- GIVEN `tests/_builders.py`, WHEN imported, THEN it builds a `Package` that
  `convert_package` consumes without error.

**DoD:** all four files in place, `just cov` green, builders importable, SendMessage sent.

---

## Story 1 — CLI & module entry point

**Owner:** python-pro · **File owned:** `tests/test_cli.py` (new)
**Targets:** `cli.py` 0%→98%, `__main__.py` 0%→100% · **Coverage delta:** ≈ +77

**Uncovered lines:** `cli.py` 2-122 (entire module); `__main__.py` 2-6 (entire module).

**Developer notes**
- `cli.py` public surface: `build_parser()`, `_cmd_convert`, `_cmd_inspect`,
  `_log_level(verbosity)`, `main(argv)`. Drive everything through `main(argv_list)`.
- Use `capsys` for stdout/stderr, `tmp_path` for `-o` output, the `example_path` fixture
  for a valid `.dtsx`.
- Cover `_log_level`: `0→"WARNING"`, `1→"INFO"`, `2→"DEBUG"`, `5→"DEBUG"`.
- `convert` paths: bare (SQL to stdout), `-o FILE` (file written + `wrote` notice on
  stderr), `--procedure NAME`, `--no-header`, `--quiet` (suppresses warning block),
  `-v` / `-vv`.
- `inspect` path: asserts on the printed graph dump for the example package.
- Error paths, both return exit code `2`: a non-existent `.dtsx` (raises
  `Ssis2SqlError` subclass `ParseError`); an unwritable `-o` target (e.g.
  `tmp_path/"nope"/"x.sql"` with no parent dir → `OSError`).
- `__main__.py`: execute it as a module so its body runs —
  ```python
  import runpy, pytest
  def test_module_entry_point(monkeypatch):
      monkeypatch.setattr("sys.argv", ["ssis2sql"])  # no subcommand → argparse exits 2
      with pytest.raises(SystemExit):
          runpy.run_module("ssis2sql", run_name="__main__")
  ```

**Tasks** — write tests for: `build_parser` structure · `_log_level` mapping · convert
to stdout · convert to file · `--procedure` · `--no-header` · `--quiet` · `-v`/`-vv` ·
inspect dump · `ParseError`→exit 2 · `OSError`→exit 2 · `__main__` entry.

**Acceptance criteria**
- GIVEN `main(["convert", example])`, WHEN run, THEN it returns `0` and writes T-SQL to stdout.
- GIVEN `main(["convert", example, "-o", path])`, WHEN run, THEN `path` holds the SQL and stderr notes the write.
- GIVEN a missing `.dtsx`, WHEN `main` runs, THEN it returns `2` and prints `ssis2sql: error:` to stderr.
- GIVEN `python -m ssis2sql` with no subcommand, WHEN run, THEN `SystemExit` is raised.
- `cli.py` ≥ 98%, `__main__.py` = 100% in the coverage report.

**DoD:** `tests/test_cli.py` green, per-file targets met, SendMessage sent.

---

## Story 2 — Data-flow transforms: sources, flow, column ops, set ops

**Owner:** python-pro · **File owned:** `tests/test_transforms_io.py` (new)
**Targets:** `set_ops.py` 39%→96%, `flow.py` 34%→98%, `column_ops.py` 54%→97%, `source.py` 49%→98%
**Depends on:** Story 0 (`tests/_builders.py`) · **Coverage delta:** ≈ +146

**Uncovered lines**
- `set_ops.py` 23-24, 33-34, 38, 51-55, 76-110, 115-127, 131-137, 141-166
- `flow.py` 21-26, 34-45, 50, 68-86, 108-116
- `column_ops.py` 27, 40, 55, 58-63, 76-90, 100-113
- `source.py` 21-26, 45-51, 57-73, 77-86

**Developer notes** — build synthetic packages with `tests/_builders.py`, run through
`convert(package)`, assert on `result.sql` and `result.warnings`. Each degenerate branch
is one test. Entire transpiler *classes* are currently unexercised by the example
package — `DataConversion`, `CopyColumn`, `MergeJoin`, `Audit`, `RowCount`, the
pass-through fallback.

- **set_ops** — `UnionAll`: no output port; no connected inputs; a branch missing an
  out column → `NULL`-fill warning; `MERGE` kind → interleave-order warning.
  `MergeJoin`: no output; only one side connected; no shared keys → `ON 1 = 1` cross-join
  warning; `NumKeyColumns` caps the key list; an output column matching neither side →
  `NULL`; both the explicit-`output.columns` path and the left-then-right-extras path.
- **flow** — `Multicast` with no input; `RowCount` with and without a `VariableName`;
  `Audit` exercising each `AuditType` `0`–`8` plus an unrecognised value → `NULL`;
  pass-through fallback for `SCRIPT`/`PIVOT`/`UNKNOWN`, with and without an input.
- **column_ops** — `DerivedColumn`: an untranslatable expression → `/* untranslatable
  ... */ NULL` + warning; an expression emitting translator warnings; `_merge_column`
  replacing a same-named column vs appending. `DataConversion`: a resolvable source col
  → `CAST(...)`; an unresolvable one → `NULL`. `CopyColumn`: resolvable vs `NULL`.
- **source** — flat-file source → staging-table warning; OLE DB with `OpenRowset`
  table name; `SqlCommandVariable` → placeholder warning; neither command nor table →
  placeholder warning; no output port; output with zero columns.

**Acceptance criteria**
- GIVEN a Union All whose branch lacks column `X`, WHEN converted, THEN the SQL has
  `NULL AS [X]` and `result.warnings` contains a `filled with NULL` message.
- GIVEN a Merge Join with no shared key columns, WHEN converted, THEN the join is
  `ON 1 = 1` and a cross-join warning is emitted.
- GIVEN a Data Conversion component, WHEN converted, THEN the output column SQL is `CAST(... AS ...)`.
- GIVEN a flat-file source, WHEN converted, THEN a staging-table warning is emitted.
- All four files at or above their target % in the coverage report.

**DoD:** `tests/test_transforms_io.py` green, per-file targets met, SendMessage sent.

---

## Story 3 — Data-flow transforms: lookup, destination, grouping, split, base

**Owner:** python-pro · **File owned:** `tests/test_transforms_join.py` (new)
**Targets:** `lookup.py` 75%→96%, `destination.py` 73%→96%, `grouping.py` 83%→96%, `conditional_split.py` 83%→97%, `base.py` 79%→96%
**Depends on:** Story 0 (`tests/_builders.py`) · **Coverage delta:** ≈ +86

**Uncovered lines**
- `lookup.py` 35-36, 40, 49-58, 75, 85, 89, 91, 121-129, 140-147, 156-158
- `destination.py` 20-21, 26-30, 36, 47, 63-67, 83-93
- `grouping.py` 47, 51-56, 66, 86, 94, 98-102, 107, 109-111, 131
- `conditional_split.py` 25-26, 55-58, 60
- `base.py` 24, 33-45, 58-60, 63, 98-114, 125, 128, 194, 203, 207, 332, 343-344

**Developer notes** — same builder-driven approach as Story 2. `base.py` carries
`BuildContext` plus shared helpers (`wrap_sql_command`, `table_name`,
`passthrough_columns`, `resolve_source_column`, `register`, the `Transpiler` ABC) — read
it first; many of its uncovered lines fall out naturally once the degenerate transform
branches in this story and Story 2 run, so write the transform tests first, then add
focused unit tests for whatever `base.py` lines remain.
- **lookup** — no input / no reference relation; full-cache vs partial; a join key that
  resolves vs one that does not; the no-match-output (error-row) branch.
- **destination** — no input port; missing `OpenRowset` table name; column list derived
  from the input vs explicit; the warning branches at 63-67 and 83-93.
- **grouping** — Aggregate with no group-by columns; multiple aggregate operations;
  an unrecognised aggregation op; Sort feeding `ORDER BY`.
- **conditional_split** — a split with no default output; the first-match `WHERE NOT (...)` exclusion.
- `base.py` 343-344 is the abstract `Transpiler.transpile` stub — call it directly on a
  bare subclass to cover the signature line, or rely on the `exclude_lines` config from
  Story 0 (`raise NotImplementedError`); do not contort tests for it.

**Acceptance criteria**
- GIVEN a Lookup with an unresolved join key, WHEN converted, THEN a warning names the key.
- GIVEN an OLE DB Destination with no `OpenRowset`, WHEN converted, THEN a warning is emitted and conversion still completes.
- GIVEN an Aggregate with no group-by columns, WHEN converted, THEN the SQL has no `GROUP BY` clause.
- All five files at or above target %; `base.py` ≥ 96%.

**DoD:** `tests/test_transforms_join.py` green, per-file targets met, SendMessage sent.

---

## Story 4 — Expression engine: lexer, parser, translator

**Owner:** python-pro · **File owned:** `tests/test_expressions_extra.py` (new)
**Targets:** `translator.py` 76%→95%, `lexer.py` 85%→96%, `expressions/parser.py` 92%→98%
**Depends on:** Story 0 · **Coverage delta:** ≈ +55

**Uncovered lines**
- `translator.py` 117, 120, 136, 153, 160, 162, 168-172, 178, 201, 210-213, 218-220, 224, 232, 236-244, 248, 251, 256, 261-264, 268, 274
- `lexer.py` 49-56, 72, 82, 87, 91, 105-108, 123, 125, 151
- `expressions/parser.py` 61, 120-121, 130, 154, 164-169

**Developer notes**
- Read `tests/test_expressions.py` for the existing entry points and helpers — do **not**
  edit it; put the new cases in `tests/test_expressions_extra.py`.
- `translator.py`: the gap is per-SSIS-function branches. Feed SSIS expressions through
  the public translate path and assert the emitted T-SQL — cover the date functions
  (`DATEADD`, `DATEDIFF`, `DATEPART`, `GETDATE`), string functions (`SUBSTRING`,
  `REPLACE`, `FINDSTRING`, `LEN`, `TRIM` family), `ISNULL`/`NULL(...)`, type casts
  `(DT_*)`, the conditional `? :` operator, and the unsupported-function warning branch.
- `lexer.py`: exotic tokens — string escapes, the `@[User::Var]` / `#{...}` reference
  forms, hex/real numeric literals, and the unrecognised-character error path.
- `expressions/parser.py`: operator-precedence corners and the parse-error branches
  (unexpected token, unterminated expression).
- For genuinely unsupported constructs the engine raises `ExpressionError` or returns a
  warning — assert the error/warning, do not assert a translation.

**Acceptance criteria**
- GIVEN an SSIS `DATEADD`/`SUBSTRING`/cast expression, WHEN translated, THEN the T-SQL equivalent is emitted.
- GIVEN a malformed expression, WHEN translated, THEN `ExpressionError` is raised (or a warning returned, matching current behaviour).
- GIVEN an unsupported SSIS function, WHEN translated, THEN a warning is produced.
- All three files at or above target %.

**DoD:** `tests/test_expressions_extra.py` green, per-file targets met, SendMessage sent.

---

## Story 5 — .dtsx parser: legacy formats & error paths

**Owner:** python-pro · **File owned:** `tests/test_parser_legacy.py` (new)
**Targets:** `parser.py` 88%→95% · **Depends on:** Story 0 · **Coverage delta:** ≈ +20

**Uncovered lines:** `parser.py` 44, 68-71, 87, 102-105, 127, 166, 183-186, 200-202, 232, 236, 287, 364, 407-419.

**Developer notes**
- Read `tests/test_parser.py` for the `parse_string(xml)` pattern — do **not** edit it;
  new cases go in `tests/test_parser_legacy.py`.
- The gap is legacy SQL Server 2005/2008 `.dtsx` layout handling and malformed-input
  branches. Lines 407-419 are a contiguous block — read it and craft the XML that
  exercises it.
- Bundled real-world packages live under `examples/` (e.g. `examples/sixty_ssis/`,
  `examples/samples/`) — `parse_file` over a legacy package is a fast way to hit the
  2005/2008 branches. Pick deterministically (e.g. `sorted(glob)[0]`); skip cleanly with
  `pytest.skip` if a directory is absent so the suite stays portable.
- Error paths: malformed XML, a non-package root, missing/again-nested metadata
  elements — assert `ParseError` (or current behaviour) for each.

**Acceptance criteria**
- GIVEN a legacy 2005/2008-format `.dtsx`, WHEN parsed, THEN a `Package` with its data flows is returned.
- GIVEN XML missing expected metadata, WHEN parsed, THEN behaviour matches current code (assert it explicitly).
- `parser.py` ≥ 95% in the coverage report.

**DoD:** `tests/test_parser_legacy.py` green, target met, SendMessage sent.

---

## Story 6 — Core modules: generator, graph, model, types, observability, utils

**Owner:** python-pro · **File owned:** `tests/test_core.py` (new)
**Targets:** `generator.py` 88%→96%, `observability.py` 77%→96%, `sqltypes.py` 71%→98%, `component_types.py` 61%→100%, `graph.py` 91%→98%, `model.py` 96%→99%, `util.py` 55%→100%, `relation.py` 86%→98%, `dialect.py` 94%→100%
**Depends on:** Story 0 · **Coverage delta:** ≈ +73

**Uncovered lines**
- `generator.py` 41, 67-69, 98, 101, 109-111, 123-128, 138-139, 148-153, 174, 180, 191, 296
- `observability.py` 114-115, 139-151
- `sqltypes.py` 61, 67-68, 74-85
- `component_types.py` 93, 98-103
- `graph.py` 57-58, 81, 95, 104-105
- `model.py` 84-85, 118, 124, 169
- `util.py` 11-15
- `relation.py` 43, 51, 56, 60
- `dialect.py` 24, 41

**Developer notes** — focused unit tests, direct function calls; group by source module
inside the one file with clear section comments.
- `util.py` `to_int`: `"1.5"` → 1 (float fallback), `"abc"` → default (both excepts),
  a non-string object → default, `None`/`""` → default. Four asserts → 100%.
- `observability.py`: the `classmethod` branch of the class decorator (decorate a class
  that has a `classmethod`); `instrument_module` (call it on a throwaway module object,
  assert the returned count and that imported/private/already-wrapped functions are
  skipped per its flags).
- `sqltypes.py` 74-85: the SSIS-type → T-SQL mapping branches — drive every type code
  (`i4`, `wstr`, `numeric`, `dbTimeStamp`, …) plus an unknown code.
- `component_types.py` 93/98-103: the legacy GUID → `ComponentKind` lookup — pass a
  2005/2008 class GUID and an unknown one.
- `graph.py` 57-58/81/95/104-105: cycle detection and disconnected-node handling — build
  a graph with a cycle, assert the error/behaviour.
- `generator.py`: `ConvertOptions` variants, the empty-data-flow warning path, header
  on/off, the 148-153 block — read it and target precisely.
- `model.py`, `relation.py`, `dialect.py`: small helpers/properties — `RelColumn.find`,
  quoting edge cases, `Component.property` misses.

**Acceptance criteria**
- GIVEN `to_int` with `"1.5"`, `"abc"`, `None`, an object, WHEN called, THEN results are `1`, default, default, default.
- GIVEN a graph containing a cycle, WHEN processed, THEN the cycle is detected (assert current behaviour).
- GIVEN an unknown SSIS type code, WHEN mapped, THEN behaviour matches current code.
- All nine files at or above their target %.

**DoD:** `tests/test_core.py` green, per-file targets met, SendMessage sent.

---

## 6. Sprint Definition of Done

1. All seven stories complete; every new test file green.
2. `just cov` reports **TOTAL ≥ 95%** and exits 0 (`--cov-fail-under=95` passes).
3. **Zero diffs under `ssis2sql/`** — `git diff --stat ssis2sql/` is empty.
4. No existing test file modified (`test_expressions.py`, `test_generator.py`,
   `test_observability.py`, `test_parser.py` unchanged).
5. Any genuine bug found while writing tests is reported to the team lead as a
   separate finding — not silently fixed, not encoded as correct behaviour.
6. Final `git status` clean after commit; branch is `desloppify/code-health` (or the
   sprint worktree merged back into it).

## 7. Risks & mitigations

- **Parallel agents, shared files** → eliminated by design: each story owns one new
  file; Story 0 alone touches `pyproject.toml` / `justfile` / `conftest.py`.
- **A backfill test goes red** → it is a real bug. Stop, report to team lead, do not
  patch source. (Backfill characterizes existing behaviour; red ≠ "fix the test to be green".)
- **`__main__.py` / abstract stub uncoverable** → `runpy` covers `__main__.py`; the
  `exclude_lines` config from Story 0 removes the `if __name__` guard and the abstract
  `raise NotImplementedError` from the denominator.
- **95% gate fragile** → the plan delivers ≈98%, a 3-point buffer; one contrived
  branch left untested will not breach the gate.

## 8. Deploy

```
/team-sprint docs/sprint-coverage-95.md
```

Set the sprint coverage gate to **95** (default is 80). Build/test command: `just cov`.
Target branch: `desloppify/code-health`. Story 0 must complete before Stories 1–6 fan out.

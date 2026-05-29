# Plan — Orchestrator-Only `main.dtsx` Collapse

Companion to [`sprint-main-procs-orchestrator.md`](./sprint-main-procs-orchestrator.md).
This plan removes the empty-body main proc that the converter currently emits
when `main.dtsx` is a pure control-flow orchestrator (zero Data Flow Tasks,
one or more `ExecutePackageTask`s).

---

## Problem statement

When `main.dtsx` contains **only** `ExecutePackageTask` (EPT) executables and
**no** Data Flow Tasks (DFTs), `convert_tree` currently emits two procs per
directory:

| File | Contents |
|---|---|
| `main.sql` | `CREATE OR ALTER PROCEDURE usp_main AS BEGIN SET NOCOUNT ON; END;` — empty body, warning "no Data Flow Task" |
| `usp_main_orchestrator.sql` | `CREATE OR ALTER PROCEDURE usp_main_orchestrator AS BEGIN ... EXEC usp_childa; EXEC usp_childb; END;` |

The caller has to know which of the two to invoke; the empty `usp_main` is
dead weight and the `_orchestrator` suffix is non-obvious. Verified against
`tests/fixtures/main_first/` on 2026-05-28 (`uv run python -m msb_ssis2sql
convert-tree tests/fixtures/main_first /tmp/out_test`).

## Goal

When `main.dtsx` has **zero DFTs and one or more EPTs**, emit a **single**
stored procedure named after `main` whose body is the EPT-ordered `EXEC`
sequence. No separate `_orchestrator` file. No empty proc.

Existing behaviour preserved in every other case:

| `main.dtsx` shape | Current files | Desired files |
|---|---|---|
| 0 DFT + 0 EPT | `main.sql` (empty body) | unchanged |
| 0 DFT + ≥1 EPT | `main.sql` (empty) + `usp_main_orchestrator.sql` | **`main.sql` with EXECs only** |
| ≥1 DFT + 0 EPT | `main.sql` (DFT body) | unchanged |
| ≥1 DFT + ≥1 EPT | `main.sql` (DFT body) + `usp_main_orchestrator.sql` | unchanged (see Decision D-2) |
| no `main.dtsx` | synth `usp_<Dir>_main` orchestrator | unchanged |

---

## Decisions (locked)

| ID | Topic | Decision |
|---|---|---|
| D-1 | Trigger | Collapse fires iff `len(main_pkg.data_flows) == 0 AND len(main_pkg.execute_package_tasks) >= 1`. |
| D-2 | Mixed-mode main | When main has both DFTs and EPTs, keep current dual-file output. Rationale: DFT body and orchestration body have different transactional semantics and need separate `CREATE OR ALTER` units. Out of scope here; tracked as future work. |
| D-3 | Proc name | The collapsed proc keeps the **main proc name** (`usp_<sanitised_main_stem>` or `usp_<dir>_<sanitised_main_stem>`). The `_orchestrator` suffix is dropped. |
| D-4 | Header comment | Replace `Data flow tasks : 0` with `Orchestration : N child EXECs` so the header reflects the proc's actual purpose. |
| D-5 | Warning suppression | The "package has no Data Flow Task" warning logged by the generator is suppressed for collapsed orchestrators only — it is correct for genuinely empty packages, misleading for pure orchestrators. |
| D-6 | EXEC order | Same topological order used today by `ControlFlowGraph.topological_order`. Cycle → declaration-order fallback with the existing pinned warning. Dangling refs → existing `missing child` warning. |
| D-7 | Determinism | Output byte-identical across runs given the same input tree (existing AC-3 / AC-8 / AC-9 contract from `sprint-main-procs-orchestrator.md` continues to hold). |

---

## Scope map

```
                  +-------------------------+
                  | main.dtsx (parsed)      |
                  | data_flows == []        |
                  | execute_package_tasks=N |
                  +------------+------------+
                               |
              ----------------------------------
              |                                |
              v                                v
   +-------------------+            +---------------------+
   | generator         |            | batch._emit_orch... |
   | convert_file(main)|            | (now skips emit     |
   |  -> SQL with      |            |  when collapse      |
   |  EXEC body, NOT   |            |  applies)           |
   |  empty body       |            +---------------------+
   +-------------------+
              |
              v
   +----------------------+
   | <dir>/main.sql       |
   | CREATE OR ALTER PROC |
   |   usp_main AS        |
   |   EXEC usp_childa;   |
   |   EXEC usp_childb;   |
   +----------------------+
```

---

## Acceptance criteria

| ID | Criterion |
|---|---|
| AC-1 | For `tests/fixtures/main_first/` (the canonical orch-only fixture), `convert_tree` emits **exactly three** `.sql` files: `main.sql`, `childa.sql`, `childb.sql`. No `_orchestrator.sql`. |
| AC-2 | `main.sql` for the AC-1 fixture contains `CREATE OR ALTER PROCEDURE usp_main` and the body lists `EXEC usp_childa;` and `EXEC usp_childb;` in topological order. |
| AC-3 | `main.sql` for the AC-1 fixture **must not** contain the literal "no Data Flow Task" warning string in its header. |
| AC-4 | For `tests/fixtures/main_first_main_with_dataflow/` (main has DFT + EPTs), both `main.sql` (DFT body) **and** `usp_<main_stem>_orchestrator.sql` (EXECs) are emitted — unchanged from today. |
| AC-5 | For a fixture with `main.dtsx` containing zero DFTs and zero EPTs, `main.sql` is emitted with the existing empty-body shape; no orchestrator file. (Unchanged.) |
| AC-6 | For `tests/fixtures/main_first_no_main/`, the synthesised `usp_<dir>_main` orchestrator is still emitted with the same alphabetical EXECs as today. |
| AC-7 | Existing cycle / dangling-child / sanitiser-collision warnings still fire with their pinned messages when collapse applies. |
| AC-8 | `_batch_warnings.log` byte-identical to the pre-change baseline for fixtures whose behaviour is unchanged (AC-4 / AC-5 / AC-6). |
| AC-9 | `tests/fixtures/main_first_url_encoded/` (added in commit `19cc779`) collapses correctly — EPT refs `Child%20A.dtsx` resolve to `EXEC usp_child_a` inside the collapsed `main.sql`. |
| AC-10 | All existing tests still pass (`uv run pytest` = 606 passing pre-change). New tests added for AC-1–AC-3 and AC-9. |

---

## Implementation tasks

### T-1 — `generator.py`: opt-in orchestration body

`msb_ssis2sql/generator.py` currently emits the DFT body only. Add a new
`ConvertOptions` field or `convert_package` parameter so the caller can pass
a pre-built `exec_lines: list[str]`; when present, the rendered proc body
becomes those `EXEC` lines instead of (or in addition to) the DFT body.

Touch points:
* `generator.py:18` — `from .parser import parse_file` (no change)
* `generator.py` `ConvertOptions` dataclass — add `orchestration_body: list[str] | None = None`
* `generator.py` `convert_package` — when `orchestration_body` is set and `package.data_flows` is empty, emit the EXEC-only body; otherwise render as today.
* Header builder — when collapsed, render `Orchestration : N child EXECs` instead of `Data flow tasks : 0` (D-4).
* Suppress the "no Data Flow Task" warning when `orchestration_body` is provided (D-5).

### T-2 — `batch.py`: detect collapse, route via T-1, skip orchestrator emit

`msb_ssis2sql/batch.py`:

* In the per-file conversion loop (`batch.py:107-155`), when processing
  `main_file`, pre-compute whether collapse applies:
  ```python
  collapse = (
      src == main_file
      and main_pkg_preview.data_flows == []
      and main_pkg_preview.execute_package_tasks != []
  )
  ```
  This requires either parsing main twice (rejected — costly) or moving the
  existing M-8 cached parse earlier. Prefer: parse main first, decide
  collapse, then pass `orchestration_body=exec_lines` into `wrap_opts`.
* The `exec_lines` for collapse are produced by the same logic currently
  inside `_emit_orchestrator` (`batch.py:276-287`). Extract that loop into a
  pure helper `_build_exec_lines(ordered_epts, proc_name_by_stem) -> list[str]`
  so both the collapse path and the legacy dual-file path share one
  implementation (D-6).
* When `collapse`, **do not** call `_emit_orchestrator` for this directory.
  Pass a `skip_orchestrator: bool` flag down or check `collapse` at the
  call site (`batch.py:158-170`).

### T-3 — `_emit_orchestrator`: factor out EXEC builder

Extract lines 276-287 of `_emit_orchestrator` into:

```python
def _build_exec_lines(
    ordered_epts: list[ExecutePackageTask],
    proc_name_by_stem: dict[str, str],
) -> list[str]:
    """Return formatted ``EXEC`` lines for an ordered EPT list.

    Skips EPTs whose package_name is empty, escapes parent dir, or has no
    matching proc in the directory. Caller has already emitted the
    'missing child' warnings via the dangling-ref check.
    """
```

Used by both `_emit_orchestrator` (legacy dual-file path) and the new
collapse path in `convert_tree`.

### T-4 — Cache main parse for collapse decision

The collapse decision needs `main_pkg.data_flows` and
`main_pkg.execute_package_tasks` **before** main is converted. Current
M-8 cache (`batch.py:110`) stores the result *after* conversion. Two options:

* **A (preferred)**: parse main eagerly at the top of the per-directory
  loop, use the parsed package for both the collapse decision and the
  later `_emit_orchestrator` cache. Single parse, slight reorder.
* **B**: parse twice. Rejected — measurable cost on directories with
  large `main.dtsx` packages.

Adopt A. Order:

```python
for dir_path, dir_files in sorted(by_dir.items()):
    main_file = find_main(dir_files)
    cached_main_pkg = parse_file(main_file) if main_file else None
    collapse = (
        cached_main_pkg is not None
        and not cached_main_pkg.data_flows
        and cached_main_pkg.execute_package_tasks
    )
    # ... rest of loop ...
```

### T-5 — Tests

New fixtures + tests in `tests/`:

* **`tests/fixtures/main_orch_only/`** — three files: `main.dtsx` (zero
  DFTs, two EPTs referencing `childa.dtsx` and `childb.dtsx`), plus the
  two child packages. (Copy from `main_first/` — they already match.)
  Wait: `main_first/` itself is already orch-only. Use it directly to
  avoid fixture duplication.
* **`tests/test_orchestrator_only_main.py`** — new test module:
  * `test_no_orchestrator_file_when_main_is_orch_only` — AC-1
  * `test_main_sql_contains_exec_in_topological_order` — AC-2
  * `test_main_header_does_not_claim_zero_data_flows` — AC-3
  * `test_collapse_works_with_url_encoded_disk_names` — AC-9 (uses the
    existing `main_first_url_encoded` fixture)
* Update `tests/test_batch_main_first.py` to drop assertions that
  expect a separate `usp_main_orchestrator.sql` for `main_first/`.
  Move those assertions onto `main_first_main_with_dataflow/` (AC-4)
  to keep coverage of the dual-file path.

### T-6 — Update determinism golden

`tests/test_generator_determinism.py` and `tests/test_agent_yaml_determinism.py`
hash the output bytes. Re-record any goldens that touch the affected
fixtures. Run `pytest` once with `--update-goldens` (if supported) or
regenerate manually and check the diff matches the AC table.

### T-7 — Docs

* `README.md` — short note in the "How it works" section: orchestrator-only
  `main.dtsx` is emitted as a single proc, no `_orchestrator` suffix.
* `docs/sprint-main-procs-orchestrator.md` — add a "Superseded by" link at
  the top of the dual-file orchestrator section pointing to this plan.

---

## Test plan

| Phase | Command | Pass criterion |
|---|---|---|
| Pre-change baseline | `uv run pytest tests/ -q` | 606 passed |
| RED (new tests, no impl) | `uv run pytest tests/test_orchestrator_only_main.py -q` | 4 failed (the new assertions reference behaviour that doesn't exist yet) |
| GREEN (impl T-1..T-4) | `uv run pytest tests/ -q` | ≥610 passed, 0 failed |
| Lint / type | `uv run ruff check . && uv run mypy msb_ssis2sql validation` | clean |
| Coverage | `uv run pytest --cov=msb_ssis2sql --cov-report=term-missing` | ≥80% per module (existing gate) |
| Determinism | re-run convert_tree twice, diff outputs | byte-identical |
| Manual smoke | `uv run python -m msb_ssis2sql convert-tree tests/fixtures/main_first /tmp/smoke` | exactly 3 .sql files + `_batch_warnings.log`, main.sql body contains both EXECs |

---

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Collapse changes proc name for downstream callers expecting `_orchestrator` suffix | Low | Medium | The previous design always exposed both procs; downstream callers were unspecified. Document in README; the canonical entry-point name (`usp_main`) is the more obvious target. |
| Mixed-mode main (D-2) confuses users — why does some main collapse and others don't? | Medium | Low | Header comment makes the choice explicit ("Orchestration : N child EXECs" vs "Data flow tasks : K"). Documented in the AC table. |
| Eager `parse_file(main_file)` (T-4) doubles parse cost when convert_file later re-parses | Medium | Low | Reuse the cache: pass `cached_main_pkg` into `convert_file` so the parser is bypassed. Already done via existing M-8 plumbing; just move the cache population earlier. |
| Sanitiser collisions between main's collapsed proc and a synth orchestrator in a sibling directory | Low | Low | The collapsed proc lives in the same directory as main; cross-directory collision impossible because proc names include the dir prefix (`usp_<dir>_<stem>`). |
| Cycle in EPTs produces non-deterministic declaration-order fallback that changes when collapse is applied | Low | Medium | The cycle fallback already exists and is ordered; collapse uses the same `topological_order()` call. Determinism test (AC-8) catches regressions. |

---

## Out of scope

* Mixed-mode main collapse (D-2) — DFT body and orchestration body merged
  into one proc. Requires deciding transactional semantics (single TXN?
  separate?) and may need user-facing config. File separate plan if needed.
* Cross-directory orchestration (`main.dtsx` referencing children outside
  its own directory) — already rejected by existing "outside-dir child
  reference rejected" warning.
* Replacing `_orchestrator` suffix in synth-orchestrator case (no
  `main.dtsx`) — synthesis remains as today.

---

## File-touch summary

| File | Change |
|---|---|
| `msb_ssis2sql/generator.py` | Add `orchestration_body` opt to `ConvertOptions`; route body builder; tweak header + warning suppression |
| `msb_ssis2sql/batch.py` | Eager-parse main, decide collapse, pass `orchestration_body`, skip `_emit_orchestrator` when collapse |
| `msb_ssis2sql/batch.py` | Extract `_build_exec_lines` helper from `_emit_orchestrator` |
| `tests/test_orchestrator_only_main.py` | **NEW** — AC-1..AC-3, AC-9 |
| `tests/test_batch_main_first.py` | Drop separate-orchestrator assertions for `main_first/`; reroute to dual-file fixture |
| `README.md` | One-paragraph note in "How it works" |
| `docs/sprint-main-procs-orchestrator.md` | Add "Superseded by" link at the dual-file section |

Estimated diff size: ~150 LOC source + ~120 LOC tests.

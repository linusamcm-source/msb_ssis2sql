# Plan v2 — Orchestrator-Only `main.dtsx` Collapse

Companion to [`sprint-main-procs-orchestrator.md`](../../../docs/sprint-main-procs-orchestrator.md).
This plan removes the empty-body main proc that the converter currently emits
when `main.dtsx` is a pure control-flow orchestrator (zero Data Flow Tasks,
one or more `ExecutePackageTask`s).

**Revision history:** v1 → v2 incorporates round-1 adversarial findings
C-1, C-2, H-1, H-2, H-3, M-1..M-4. All `batch.py:<line>` references
re-pinned against HEAD `d2d07aa` on 2026-05-28.

---

## Problem statement

When `main.dtsx` contains **only** `ExecutePackageTask` (EPT) executables and
**no** Data Flow Tasks (DFTs), `convert_tree` currently emits two procs per
directory:

| File | Contents |
|---|---|
| `main.sql` | `CREATE OR ALTER PROCEDURE usp_main AS BEGIN SET NOCOUNT ON; END;` — empty body, warning `"package has no Data Flow Task - there are no transformations to convert"` |
| `usp_main_orchestrator.sql` | `CREATE OR ALTER PROCEDURE usp_main_orchestrator AS BEGIN ... EXEC usp_childa; EXEC usp_childb; END;` |

The caller has to know which of the two to invoke; the empty `usp_main` is
dead weight and the `_orchestrator` suffix is non-obvious. Verified against
`tests/fixtures/main_first/` on 2026-05-28 (`uv run python -m msb_ssis2sql
convert-tree tests/fixtures/main_first /tmp/out_test`).

## Goal

When `main.dtsx` has **zero DFTs and one or more EPTs that resolve to at
least one in-directory child**, emit a **single** stored procedure named
after `main` whose body is the EPT-ordered `EXEC` sequence. No separate
`_orchestrator` file. No empty proc.

Existing behaviour preserved in every other case:

| `main.dtsx` shape | Current files | Desired files |
|---|---|---|
| 0 DFT + 0 EPT | `main.sql` (empty body) | unchanged |
| 0 DFT + ≥1 EPT (≥1 resolves) | `main.sql` (empty) + `usp_main_orchestrator.sql` | **`main.sql` with EXECs only** |
| 0 DFT + ≥1 EPT (zero resolve — all dangling/outside-dir) | as above | **unchanged** — fall back to legacy dual-file path so the warning still fires (D-1 / D-5 / AC-12) |
| ≥1 DFT + 0 EPT | `main.sql` (DFT body) | unchanged |
| ≥1 DFT + ≥1 EPT | `main.sql` (DFT body) + `usp_main_orchestrator.sql` | unchanged (D-2) |
| no `main.dtsx` | synth `usp_<Dir>_main` orchestrator | unchanged |
| main.dtsx parse fails | per-file error logged, orchestrator skipped | unchanged (T-4 wraps eager parse in try/except — AC-11) |

---

## Decisions (locked)

| ID | Topic | Decision |
|---|---|---|
| D-1 | Trigger | Collapse fires iff `len(main_pkg.data_flows) == 0 AND len(post_filter_exec_lines) >= 1`. The post-filter check is mandatory — if every EPT is dangling/outside-dir, `exec_lines` is empty and collapse does NOT apply (revert to legacy dual-file path so the no-Data-Flow-Task warning still surfaces). |
| D-2 | Mixed-mode main | When main has both DFTs and EPTs, keep current dual-file output. Rationale: DFT body and orchestration body have different transactional semantics. Out of scope here. |
| D-3 | Proc name | The collapsed proc keeps the **main proc name** (`usp_<sanitised_main_stem>` or `usp_<dir>_<sanitised_main_stem>`). The `_orchestrator` suffix is dropped. |
| D-4 | Header comment | Replace `Data flow tasks : 0` with `Orchestration : N child EXECs` when collapse applies. |
| D-5 | Warning suppression | The "no Data Flow Task" warning logged by `generator.py:67` is suppressed iff `orchestration_body` is **provided AND non-empty**. Empty orchestration_body never reaches the generator under D-1 (rejected at the batch layer). |
| D-6 | EXEC order & cycle fallback | Topological order via `ControlFlowGraph.topological_order()` (`control_graph.py:97-103`, raises `GraphError` on cycle). The **declaration-order fallback** with the pinned warning `"main orchestrator: cycle detected on edge {from} -> {to}; falling back to declaration order"` lives in the **caller** (today: `batch.py:251-266` inside `_emit_orchestrator`); under v2, this caller-side logic is extracted into the new `_build_ordered_exec_lines` helper (T-3) so both the collapse path and the legacy dual-file path share one implementation. |
| D-7 | Determinism | Output byte-identical across runs given the same input tree. Existing `tests/test_generator_determinism.py` contract holds. |
| D-8 | `--no-orchestrator` interaction | When the CLI flag `--no-orchestrator` is set, **collapse is also disabled**: `main.sql` reverts to today's empty-body output and no EXECs are emitted anywhere. Rationale: `--no-orchestrator` means the caller explicitly does not want any EXEC chain; collapse would silently put one back. Documented in CLI help. |

---

## Scope map

```
                  +-------------------------+
                  | main.dtsx (parsed once  |
                  |  EAGERLY at top of      |
                  |  per-dir loop — T-4)    |
                  | data_flows == []        |
                  | execute_package_tasks=N |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | _build_ordered_exec_    |
                  | lines() — T-3           |
                  | (topology + cycle fb +  |
                  |  dangling + outside-dir)|
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | post-filter exec_lines  |
                  | non-empty?              |
                  +------+-------------+----+
                         |             |
                       yes (D-1)       no
                         |             |
                         v             v
              +---------------+   +-------------------+
              | COLLAPSE      |   | legacy dual-file: |
              | convert_      |   |  empty main.sql + |
              | package(      |   |  orchestrator.sql |
              | cached_main_  |   | (no change)       |
              | pkg, opts w/  |   +-------------------+
              | orchestration_|
              | body=...) T-4b|
              +-------+-------+
                      |
                      v
              <dir>/main.sql
              (single proc, EXEC body)
```

---

## Acceptance criteria

| ID | Criterion |
|---|---|
| AC-1 | For `tests/fixtures/main_first/`, `convert_tree` emits **exactly three** `.sql` files: `main.sql`, `childa.sql`, `childb.sql`. No `_orchestrator.sql`. |
| AC-2 | `main.sql` for `main_first/` contains `CREATE OR ALTER PROCEDURE usp_main` and the body lists `EXEC usp_childa;` and `EXEC usp_childb;` in topological order (childa before childb based on precedence in `main_first/main.dtsx`). |
| AC-3 | `main.sql` for `main_first/` **must not** contain the literal substring `"no Data Flow Task"` in its header. |
| AC-4 | For the dual-mode fixture `tests/fixtures/main_first_main_with_dataflow/` (main has 1 DFT + 1 EPT), both `main.sql` (DFT body) **and** `usp_<main_stem>_orchestrator.sql` (with one EXEC) are emitted — unchanged from today. |
| AC-5 | For a fixture where `main.dtsx` has zero DFTs and zero EPTs, `main.sql` is emitted with the existing empty-body shape; no orchestrator file. (Unchanged.) |
| AC-6 | For `tests/fixtures/main_first_no_main/`, the synthesised `usp_<dir>_main` orchestrator is still emitted with the same alphabetical EXECs as today. |
| AC-7 | Existing **cycle** and **dangling-child** warnings still fire with their pinned messages when collapse applies — verified against `tests/fixtures/main_first_cycle/` and `tests/fixtures/main_first_dangling/`. (Sanitiser-collision is per-stem identifier resolution, not a warning emit site — removed from this AC after round-1 verification.) |
| AC-8 | `_batch_warnings.log` byte-identical to the pre-change baseline for fixtures whose behaviour is unchanged (AC-4 / AC-5 / AC-6 / `main_first_no_main/`). Fixtures whose behaviour changes (AC-1 family + `main_first_url_encoded/`) get fresh baselines re-recorded in this sprint. |
| AC-9 | `tests/fixtures/main_first_url_encoded/` collapses correctly — `main.sql` contains `EXEC usp_child_a;` and `EXEC usp_child_b;`. Proc-name `usp_child_a` was verified on 2026-05-28 against `sanitise(decode_package_name('Child A')) == 'child_a'`. |
| AC-10 | All existing tests still pass. Baseline: 606 passing. After: 606 − 3 (deleted) + 6 (orch_only module) + 1 (CLI no-orchestrator gate) = **610 passing, 0 failed**. |
| AC-11 | When `main.dtsx` fails to parse (malformed XML), `convert_tree` records a `FileOutcome(ok=False, error=...)` for main, skips collapse, continues converting siblings, and emits no `main.sql`. Verified against new fixture `tests/fixtures/main_first_malformed_main/`. |
| AC-12 | When `main.dtsx` has zero DFTs and one or more EPTs but every EPT references a missing/outside-dir child (post-filter exec_lines is empty), the **legacy dual-file path runs** (`main.sql` empty + `usp_<main>_orchestrator.sql` with no EXECs + the no-Data-Flow-Task warning). Verified against new fixture `tests/fixtures/main_first_all_dangling/`. |
| AC-13 | With `--no-orchestrator`, collapse is disabled. For `main_first/`, `main.sql` is emitted with the empty-body shape and no `_orchestrator.sql` file. (D-8) |

---

## Implementation tasks

### T-1 — `generator.py`: opt-in orchestration body

Touch points (current HEAD):
* `generator.py:23-24` `ConvertOptions` dataclass — add `orchestration_body: list[str] | None = None`.
* `generator.py:54` `convert_package` — when `options.orchestration_body` is non-empty AND `package.data_flows == []`, emit the EXEC-only body using the same proc-wrapper template; otherwise render as today.
* `generator.py:294` header builder (`lines.append(f' * Data flow tasks : {len(package.data_flows)}')`) — when collapse body is in use, render `Orchestration : N child EXECs` instead (D-4).
* `generator.py:66-69` warning emit — suppress the no-Data-Flow-Task warning iff `options.orchestration_body` is provided AND non-empty (D-5). Bool key: `bool(options.orchestration_body)`.

### T-2 — `batch.py`: detect collapse, route via T-1, skip orchestrator emit

Touch points (current HEAD):
* Per-file conversion loop runs `batch.py:119-164`. The main-cache initialiser is `batch.py:117`. The `_emit_orchestrator` call is gated at `batch.py:167` (`if not no_orchestrator:`) with the call body at `batch.py:168-179`.
* `proc_name_by_stem` is populated inside the per-file loop (line 140). The collapse decision needs `exec_lines`, which needs that map — so compute collapse on the FIRST iteration of the per-file loop (when `src == main_file`, which is guaranteed first by the `ordered` list at line 99-112). At that moment `proc_name_by_stem` already contains every entry needed because every other sibling's proc name follows the same deterministic formula (`usp_<dir>_<sanitised_decoded_stem>`); pre-compute the sibling entries via a one-line `dict` comprehension before the loop, OR populate them inline as siblings are processed and defer collapse-aware emission of main until after the loop. Pick the inline approach — minimal-diff: build a `pending_main_emit` flag on the first iteration; do the actual main file write at the end of the loop with full `proc_name_by_stem`.
* When the collapse path applies, build `wrap_opts` with `orchestration_body=exec_lines` and route through `convert_package(cached_main_pkg, wrap_opts)` (T-4b) instead of `convert_file(src, wrap_opts)`.
* When `collapse`, skip the `_emit_orchestrator` call entirely (in addition to the existing `no_orchestrator` flag). When `no_orchestrator` is True, force `collapse=False` per D-8.

### T-3 — Extract `_build_ordered_exec_lines` helper (WIDER than v1)

**v1 said "extract _build_exec_lines"; v2 widens it.** The new helper owns
**every** caller-side step that today lives inside `_emit_orchestrator`
(`batch.py:198-298`):

```python
def _build_ordered_exec_lines(
    main_pkg: Package,
    dir_files: list[Path],
    main_file: Path,
    proc_name_by_stem: dict[str, str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Build the EXEC body and the warning list for the orchestrator path.

    Returns:
        (exec_lines, warnings) where warnings is a list of (source_path, warning)
        pairs matching the existing _emit_orchestrator warning tuple shape.

    Owns:
        - ControlFlowGraph(main_pkg) construction
        - topological_order() call + GraphError -> declaration-order fallback
          with pinned warning "main orchestrator: cycle detected on edge
          {from} -> {to}; falling back to declaration order" (batch.py:251-266 today)
        - Dangling-ref scan (batch.py:269-285 today) — produces the pinned
          warning "missing child: {pkg_name!r} referenced by EPT but not found
          in directory"
        - Outside-dir filter (batch.py:266-270 today) — produces the pinned
          warning "outside-dir child reference rejected: {pkg_name!r}"
        - Nested-EPT child scan (batch.py:232-242 today) — produces the
          pinned warning "nested orchestration: {src.name} itself contains
          ExecutePackageTasks" so the collapse path preserves parity with
          _emit_orchestrator. AC-8 baselines for affected fixtures still
          hold.
        - EXEC line formatting (batch.py:287-298 today)
    """
```

Both the **new collapse path** in `convert_tree` AND the legacy
`_emit_orchestrator` call this helper. The orchestrator emitter shrinks to:
parse, call helper, render proc, write file.

### T-4 — Cache main parse eagerly (WITH error handling — H-2 fix)

Replace the per-file loop's after-the-fact cache with an eager parse at the
top of each per-directory iteration:

```python
for dir_path, dir_files in sorted(by_dir.items()):
    main_file = next((f for f in dir_files if f.name.lower() == "main.dtsx"), None)
    cached_main_pkg = None
    main_parse_error: str | None = None
    if main_file is not None:
        try:
            cached_main_pkg = parse_file(main_file)
        except Exception as exc:  # noqa: BLE001
            main_parse_error = f"main.dtsx parse failed: {exc!r}"
            # Record outcome NOW; main_file is excluded from the per-file loop below.
            dst = output_root / main_file.relative_to(input_root).with_suffix(".sql")
            result.outcomes.append(FileOutcome(main_file, dst, ok=False, error=main_parse_error))
            # collapse stays False; loop continues for siblings.
    # ... existing per-file loop, but skip main_file when main_parse_error set ...
```

The `if main_parse_error is not None:` branch also forces `collapse = False`
and skips the `_emit_orchestrator` call for that directory (the orchestrator
needs `cached_main_pkg`; without it, no orchestrator can be built).

### T-4b — Switch main-file conversion to `convert_package` (NEW — C-1 fix)

`generator.convert_file` has signature `(path, options)` — it has no
`package=` parameter, so the M-8 cache as documented in v1 silently
double-parses. v2 explicitly switches the main-file branch of the per-file
loop to `convert_package(cached_main_pkg, wrap_opts)` so the eager parse
from T-4 is reused. Child files continue to use `convert_file(src, wrap_opts)`.

The existing `if src == main_file and conversion.package is not None: cached_main_pkg = conversion.package` line at `batch.py:155-156` becomes redundant once T-4 lands and is removed.

### T-5 — Tests (FULLY ENUMERATED — H-1 fix)

**Changes to existing `tests/test_batch_main_first.py`:**

| Function | Action | Rationale |
|---|---|---|
| `test_main_dtsx_is_converted_first` | KEEP | Tests ordering, orthogonal to collapse |
| `test_every_emitted_sql_has_create_or_alter_procedure_header` | KEEP | Still true post-collapse |
| `test_orchestrator_emitted_to_distinct_file` | DELETE | After collapse, no `_orchestrator.sql` for `main_first/` — assertion `len(orch_files) == 1` becomes a hard fail |
| `test_orchestrator_proc_emitted_when_main_has_execute_package_tasks` | RETARGET to new fixture `main_first_main_with_dataflow_multi/` | Asserts `len(exec_lines) >= 2` and childa-before-childb ordering; current dual-file fixture has only 1 child |
| `test_emitted_exec_names_match_per_package_proc_names` | RETARGET to `main_first_main_with_dataflow_multi/` | Same reason |
| `test_orchestrator_handles_*` (cycle / dangling — if present) | KEEP — they should still pass under collapse because T-3 preserves the warnings |

**New fixture `tests/fixtures/main_first_main_with_dataflow_multi/`:**

Three files: `main.dtsx` (one DFT + two EPTs referencing `childa.dtsx` and
`childb.dtsx` with a precedence constraint), plus the two child packages.
Used by the retargeted dual-file tests.

**New fixture `tests/fixtures/main_first_malformed_main/`:**

`main.dtsx` with deliberately broken XML, plus one valid `childa.dtsx`.
Used by AC-11 test.

**New fixture `tests/fixtures/main_first_all_dangling/`:**

`main.dtsx` with two EPTs referencing `nonexistent_a.dtsx` and
`nonexistent_b.dtsx`. Directory contains only `main.dtsx`. Used by AC-12 test.

**New test module `tests/test_orchestrator_only_main.py`:**

| Function | Covers |
|---|---|
| `test_no_orchestrator_file_when_main_is_orch_only` | AC-1 |
| `test_main_sql_contains_exec_in_topological_order` | AC-2 |
| `test_main_header_does_not_claim_zero_data_flows` | AC-3 |
| `test_collapse_works_with_url_encoded_disk_names` | AC-9 (uses existing `main_first_url_encoded/`) |
| `test_malformed_main_does_not_abort_directory` | AC-11 |
| `test_all_dangling_epts_falls_back_to_legacy_dual_file` | AC-12 |

(6 new tests in this module.)

**New test in `tests/test_cli_exit_codes.py` (or new module):**

| Function | Covers |
|---|---|
| `test_no_orchestrator_flag_disables_collapse` | AC-13 (D-8) |

Final count: 606 baseline − 3 deleted + 6 new (orch_only module) + 1 new (CLI) − 0 (retargeted tests unchanged in count) = **610 passing, 0 failed**. Aligns with AC-10.

### T-6 — Update determinism golden

`tests/test_generator_determinism.py` and `tests/test_agent_yaml_determinism.py`
re-record golden hashes for `main_first/` and `main_first_url_encoded/`
(both change shape). All other fixtures preserve golden hashes per AC-8.

### T-7 — Docs

* `README.md` — short note in the "How it works" section.
* `docs/sprint-main-procs-orchestrator.md` — add a "Superseded by" link pointing here.

---

## Test plan

| Phase | Command | Pass criterion |
|---|---|---|
| Pre-change baseline | `uv run pytest tests/ -q` | 606 passed |
| RED phase A | `uv run pytest tests/test_orchestrator_only_main.py -q` | 6 failed (new tests reference behaviour that doesn't exist yet) |
| RED phase B (CLI) | `uv run pytest tests/test_cli_exit_codes.py::test_no_orchestrator_flag_disables_collapse -q` | 1 failed |
| Test rewrites land at GREEN time | Don't edit `test_batch_main_first.py` until impl is ready — running the suite mid-rewrite breaks ~3 tests | n/a |
| GREEN (impl T-1..T-4b, T-5 finalised) | `uv run pytest tests/ -q` | **610 passed, 0 failed** (AC-10) |
| Lint / type | `uv run ruff check . && uv run mypy msb_ssis2sql validation` | clean |
| Coverage | `uv run pytest --cov=msb_ssis2sql --cov-report=term-missing` | ≥80% per module |
| Determinism | re-run convert_tree twice, diff outputs | byte-identical |
| Manual smoke | `uv run python -m msb_ssis2sql convert-tree tests/fixtures/main_first /tmp/smoke` | exactly 3 .sql files + `_batch_warnings.log`, `main.sql` body contains both EXECs |

---

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Collapse changes proc name for downstream callers expecting `_orchestrator` suffix | Low | Medium | Document in README; canonical entry-point `usp_main` is more obvious. |
| Mixed-mode main (D-2) confuses users | Medium | Low | Header comment makes choice explicit. |
| Eager `parse_file(main_file)` (T-4) doubles parse cost vs M-8 | Medium | Low | T-4b switches main-file to `convert_package(cached_main_pkg, wrap_opts)` — single parse. **Note: v1's "already done via existing M-8 plumbing" claim was incorrect — `convert_file` has no `package=` parameter; T-4b is the actual mitigation.** |
| Cycle in EPTs produces non-deterministic declaration-order fallback that changes when collapse is applied | Low | Medium | T-3 extracts the GraphError fallback into a shared helper used by BOTH paths; identical ordering. AC-7 verifies. |
| Eager parse raising aborts the directory | Medium | Medium | T-4 wraps in try/except, records `FileOutcome(ok=False)` and continues. AC-11 verifies against `main_first_malformed_main/`. |
| Empty `exec_lines` post-filter silently collapses to a body-less main proc | Medium | Medium | D-1 explicitly rejects collapse when `exec_lines == []`; legacy dual-file path runs instead, preserving the warning. AC-12 verifies against `main_first_all_dangling/`. |
| `--no-orchestrator` + collapse interaction silently puts EXECs back | Low | High | D-8: `--no-orchestrator` forces `collapse=False`. AC-13 verifies. |

---

## Out of scope

* Mixed-mode main collapse (D-2) — merging DFT body with orchestration body in a single proc.
* Cross-directory orchestration — already rejected by outside-dir filter.
* Renaming the synth `usp_<dir>_main` orchestrator when no `main.dtsx` exists.

---

## File-touch summary

| File | Change |
|---|---|
| `msb_ssis2sql/generator.py` | Add `orchestration_body` to `ConvertOptions`; body builder routing; header tweak; warning suppression (`bool(orchestration_body)`) |
| `msb_ssis2sql/batch.py` | Eager-parse main (try/except), compute collapse via T-3 helper, skip `_emit_orchestrator` when collapse, switch main-file branch to `convert_package(cached_main_pkg, wrap_opts)`, force `collapse=False` when `no_orchestrator=True` |
| `msb_ssis2sql/batch.py` | Extract `_build_ordered_exec_lines` (T-3): owns topology + cycle fallback + dangling/outside-dir scan + exec-line formatting. Used by collapse path AND legacy `_emit_orchestrator` |
| `tests/test_orchestrator_only_main.py` | **NEW** — 6 tests covering AC-1..AC-3, AC-9, AC-11, AC-12 |
| `tests/test_batch_main_first.py` | DELETE 1 test (`test_orchestrator_emitted_to_distinct_file`); RETARGET 2 tests to `main_first_main_with_dataflow_multi/` |
| `tests/test_cli_exit_codes.py` | NEW test `test_no_orchestrator_flag_disables_collapse` (AC-13) |
| `tests/fixtures/main_first_main_with_dataflow_multi/` | **NEW** fixture: main + 2 children + precedence |
| `tests/fixtures/main_first_malformed_main/` | **NEW** fixture: broken main.dtsx + valid child |
| `tests/fixtures/main_first_all_dangling/` | **NEW** fixture: main with EPTs pointing nowhere |
| `tests/test_generator_determinism.py` | Re-record goldens for `main_first/` + `main_first_url_encoded/` |
| `README.md` | One-paragraph note in "How it works" |
| `docs/sprint-main-procs-orchestrator.md` | Add "Superseded by" link |

Estimated diff size: ~180 LOC source + ~280 LOC tests + 3 new fixtures.

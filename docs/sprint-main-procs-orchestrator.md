# Plan — Main-First Ordering, Stored-Proc Wrapping, Agent-Job Extraction

Companion to [`ssis-to-adf-conversion-plan.md`](../ssis-to-adf-conversion-plan.md).
This plan covers the conversion-pipeline changes needed before ADF/IaC work.

---

## Goals

1. In every directory of `.dtsx` packages, **`main.dtsx` is the entry point** and its converted artefact is emitted first.
2. Every per-package `.sql` is wrapped as an idempotent **stored procedure**.
3. The **main stored procedure orchestrates** the child procedures in the order dictated by SSIS precedence constraints inside `main.dtsx`.
4. **SQL Server Agent job definitions** that schedule these packages are extracted from `msdb` and emitted as version-controlled YAML, per the ADF conversion plan (Stream 2).

---

## Decisions (locked)

| Topic | Decision |
|---|---|
| Main file detection | Case-insensitive filename match: `main.dtsx` / `Main.dtsx` / `MAIN.dtsx`. One per directory. |
| SQL Agent metadata source | Live SQL Server — query `msdb.dbo.sysjobs`, `sysjobsteps`, `sysjobschedules`, `sysschedules`. |
| Missing-main fallback | Synthesize `usp_<DirName>_Main` that `EXEC`s each child proc alphabetically. Emit a warning. |
| Child invocation order | Parse `ExecutePackageTask` plus precedence constraints in main; emit `EXEC`s in that topological order. |

---

## Scope Map

```
+-----------------------------+        +-------------------------------+
|  *.dtsx tree (input)        |        |  Live SQL Server (msdb)       |
|  - main.dtsx (entry)        |        |  - sysjobs / sysjobsteps      |
|  - child_a.dtsx             |        |  - sysjobschedules            |
|  - child_b.dtsx             |        +---------------+---------------+
+--------------+--------------+                        |
               |                                       |
               v                                       v
        +-------------+                       +------------------+
        | Converter   |                       | Agent extractor  |
        | (extended)  |                       | (new module)     |
        +------+------+                       +---------+--------+
               |                                        |
               v                                        v
  +------------+-------------+         +----------------+-----------------+
  | <dir>/usp_<Child>.sql    |         | <out>/jobs/<JobName>.yaml        |
  | <dir>/usp_<Main>.sql     |         | (declarative job definitions)    |
  | (CREATE OR ALTER PROC)   |         +----------------------------------+
  +--------------------------+
```

---

## Code Changes

### 1. `msb_ssis2sql/model.py`
- Add `ExecutePackageTask` dataclass: `ref_id`, `name`, `package_name`, `package_path`, `precedence_predecessors: list[str]`.
- Extend `Package`:
  - `execute_package_tasks: list[ExecutePackageTask]`
  - `precedence_constraints: list[PrecedenceConstraint]`
- Add `PrecedenceConstraint`: `from_ref`, `to_ref`, `value` (Success/Failure/Completion), `eval_op`.

### 2. `msb_ssis2sql/parser.py`
- Extend `_collect_executables` to recognise `ExecutePackageTask` (`ObjectData/ExecutePackageTask` or legacy variant) and capture child package reference (`PackageName` / `PackageNameFromProjectReference` / file-system `PackagePath`).
- New `_collect_precedence_constraints` walks `<DTS:PrecedenceConstraints>` → populates `package.precedence_constraints`.
- No existing behaviour changes; new fields are additive.

### 3. `msb_ssis2sql/graph.py` (or new `control_graph.py`)
- New `ControlFlowGraph` over `execute_package_tasks` + `precedence_constraints` → topological order respecting Success/Completion edges. Failure edges ignored for now (warning emitted).
- Cycles → warning + fallback to declaration order.

### 4. `msb_ssis2sql/batch.py`
- Replace `for src in sorted(input_root.rglob("*.dtsx"))` with a two-pass walker:
  1. Group `.dtsx` by parent directory.
  2. For each directory: resolve main (case-insensitive `main.dtsx`); convert main first, then siblings.
- After per-package conversion, invoke new `emit_main_orchestrator(directory, package_outcomes, options)`:
  - If `main.dtsx` present → render `usp_<MainPackageName>` that `EXEC`s each child proc in ControlFlowGraph topological order.
  - If absent → synthesize `usp_<DirSanitised>_Main`, alpha order, emit warning to `BatchResult`.
- `FileOutcome` gains optional `procedure_name: str` so the orchestrator can call children by name.

### 5. `msb_ssis2sql/generator.py`
- Default `ConvertOptions.wrap_in_procedure = True` (was `False`).
- Default `procedure_name` becomes a derived `_default_procedure_name(package)` (e.g. `usp_<SanitisedPackageName>`), not the constant `usp_Migrated_Package`. The CLI flag still overrides.
- `_wrap_procedure` switch to `CREATE OR ALTER PROCEDURE` (already done) and ensure deterministic body so repeated runs diff-clean.

### 6. `msb_ssis2sql/agent/` (new package)
- `agent/extractor.py` — connects via `pyodbc` / `pymssql` (driver TBD), runs read-only msdb queries, returns list of `AgentJob` dataclasses (job, steps, schedules, notifications, enabled).
- `agent/yaml_emitter.py` — serialises `AgentJob` to YAML matching the schema declared in the ADF conversion plan (Stream 2 §4).
- Connection settings via `.env` (`MSDB_DSN` / `MSDB_USER` / `MSDB_PASSWORD`) — read by extractor only; never logged.
- No write-back to msdb. Read-only.

### 7. `msb_ssis2sql/cli.py`
- New subcommand `extract-agent-jobs`:
  - Args: `--out <dir>` (default `<output>/jobs/`), `--dsn`, `--filter <like-pattern>`.
  - Exit 0 on success, 2 on connection failure.
- `convert-tree`:
  - Already wraps procs by default after change 5; no flag needed.
  - New `--no-orchestrator` to disable main-proc emission for users who do not want it.

### 8. `tests/`
- Fixtures: add a small `tests/fixtures/main_first/` tree containing `main.dtsx` plus two child `.dtsx` referenced via ExecutePackageTask.
- Tests:
  - `test_batch_main_first.py` — main always first in `BatchResult.outcomes`; orchestrator file present.
  - `test_batch_no_main.py` — synthesized orchestrator + warning.
  - `test_control_graph.py` — precedence ordering, cycle detection.
  - `test_agent_extractor.py` — uses a fake DB cursor (no live connection in CI); golden YAML output.
  - `test_generator_proc_name.py` — proc name derivation + idempotency.

---

## Acceptance Criteria

1. `msb_ssis2sql convert-tree examples/ generated_scripts/` produces:
   - One `CREATE OR ALTER PROCEDURE usp_<PackageName>` per `.dtsx`.
   - For every directory containing `main.dtsx`, an additional file `usp_<MainPackageName>.sql` whose body is `EXEC usp_<Child1>; EXEC usp_<Child2>; …` in precedence-constraint order.
   - For directories without `main.dtsx`, a synthesized `usp_<Dir>_Main.sql` with children in alpha order and a warning in `BatchResult`.
2. `msb_ssis2sql extract-agent-jobs --dsn <dsn> --out generated_scripts/jobs/` writes one YAML per job, schema matching the ADF plan.
3. Re-running both commands is deterministic — `git diff` shows no churn from formatting / ordering.
4. All existing tests pass; new tests cover ≥ 90% of new lines.
5. Conversion of `examples/sales_etl.dtsx` (no main) still works and emits a synthesized orchestrator without changing its data-flow SQL output.

---

## Out of Scope (this plan)

- ADF pipeline JSON emission (Stream 1, runtime layer).
- Bicep / Terraform / Python provisioner (covered in main ADF plan).
- DevOps pipeline YAML.
- Translating SSIS Script Tasks, custom components.
- Cross-directory orchestration (each directory is independent; a top-level "master of masters" is a future iteration).

---

## Open Questions

1. **Driver** for msdb access — `pyodbc` (needs ODBC Driver 18 on dev machines) vs `pymssql` (pure-python wheel, easier CI). Default to `pyodbc`; document install in README.
2. **Schema** of generated procs — leave as `dbo.` or namespace by directory (`<dir>.usp_…`)? Default `dbo.`; expose `--schema` later if needed.
3. **Failure precedence** edges — currently dropped with a warning. Worth a `TRY…CATCH` wrapper in the orchestrator? Defer until a real SSIS package exhibits the pattern.
4. **ExecutePackageTask path resolution** — packages can reference siblings by name, by project-reference, or by SSISDB folder/project/package triplet. First pass handles file-name and project-reference; SSISDB triplets become a follow-up.

---

## Sequencing

```
Step 1  model + parser changes for ExecutePackageTask + precedence  -> tests pass
Step 2  control_graph + orchestrator emission                       -> tests pass
Step 3  generator default proc wrap + naming                        -> tests pass + sample diff reviewed
Step 4  batch.py main-first walker                                  -> tests pass
Step 5  agent extractor + YAML emitter                              -> tests pass with fake cursor
Step 6  CLI wiring (extract-agent-jobs, --no-orchestrator)          -> end-to-end manual run on examples/
Step 7  README + docs update                                        -> merge
```

Each step ships as its own PR. Steps 1-4 are blocking for the ADF Stream-1 work; Step 5 is blocking for Stream-2.

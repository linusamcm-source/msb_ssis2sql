# Sprint v2 — Agent-Step → Converted-Proc Rewriting

**Revision history.** v1 → v2 incorporates round-1 adversarial findings:
* C-1 (yaml emitter golden break) → T-6 rewritten with explicit dict-builder + new AC-18 + new golden fixture.
* H-1 (T-1 snippet referenced undefined `src`) → fixed to `outcome.source`.
* H-2 (batch.py:130-140 / 180-184 line drift) → re-pinned to `112-118` and `275-282` against HEAD.
* H-3 (AC-12 testability) → new D-11 declaring `_maybe_rewrite_step` as the unit-test seam.
* M-1 (`.dtsx` suffix normalisation orphan) → new D-12 placing append in `parse_ssis_command`.
* M-2 (UNC POSIX asymmetry) → new D-13: parser POSIX-normalises; `_posix` helper moves to `util.py`.
* M-3 (test count math) → AC-15 reworded; new AC-16 for per-module coverage.
* M-4 (`path_outside_input_root` orphan category) → removed; reuses `unresolved`.
* L-1 (cli.py:63 off-by-one) → re-pinned to `cli.py:62`.
* L-2 / L-3 → inline comment / D-7 wording fixes.

---

# Plan — Agent-Step → Converted-Proc Rewriting

Companion to [`sprint-main-procs-orchestrator.md`](./sprint-main-procs-orchestrator.md) and
[`sprint-orch-only-main.md`](./sprint-orch-only-main.md). This sprint closes the
last gap in the agent-extractor pipeline: rewriting `msdb.dbo.sysjobsteps`
commands that launch SSIS packages so they invoke the T-SQL procedures the
converter just emitted.

---

## Problem statement

`msb_ssis2sql extract-agent-jobs` (entry point at `msb_ssis2sql/agent/extractor.py:219-225`)
copies `sysjobsteps.command` verbatim into the YAML output. For SSIS-launched
jobs the command is typically:

```
DTExec /FILE "C:\src\etl\fact\nightly_load.dtsx" /CHECKPOINTING OFF ...
```

Once the operator runs `convert-tree`, that dtsx becomes the stored procedure
`usp_fact_nightly_load`. The emitted YAML still calls the original dtsx path,
which no longer exists on the target. Manual rewrite of every step is what
this sprint replaces.

The converter knows the dtsx → proc mapping (formula in the `_resolve_proc_name` closure at `batch.py:112-118`).
The extractor doesn't. They need a shared, deterministic interchange artifact.

---

## Goal

`extract-agent-jobs` honours an optional `_proc_manifest.json` produced by
`convert_tree` and rewrites SSIS-subsystem steps to call the matching
T-SQL procedure. TSQL-subsystem steps are untouched. Unresolved and
unparseable steps are left verbatim with a warning logged to a new
`_agent_warnings.log`.

---

## Decisions (locked)

| ID | Topic | Decision |
|---|---|---|
| D-1 | Manifest filename + location | `convert_tree` writes `<output_root>/_proc_manifest.json` next to the existing `_batch_warnings.log`. Always written, even when zero packages were converted (empty `entries: []`). |
| D-2 | Manifest schema | `{"version": 1, "input_root": "<absolute path>", "entries": [{"dtsx": "<relpath from input_root, POSIX separators>", "proc": "<usp_...>", "out_sql": "<relpath from output_root, POSIX separators>"}, ...]}`. Entries sorted by `dtsx` (ascii, case-sensitive) for byte-determinism (AC-2). |
| D-3 | Path-resolution algorithm | Three-pass matcher against manifest entries: (1) exact `endswith(dtsx_relpath)` on the parsed command path (POSIX-normalised); (2) basename match (`Path(parsed).name.lower() == Path(entry.dtsx).name.lower()`) WHEN exactly one entry matches; (3) miss. Multi-basename matches → miss + warning (ambiguous). |
| D-4 | Step-rewrite shape | When matched: `subsystem: TSQL`, `command: "EXEC <proc>;"`. Audit fields preserved at top of step: `original_subsystem: SSIS`, `original_command: <verbatim>`, `dtsx_source: <relpath from manifest>`. Other AgentStep fields (`database_name`, success/fail actions, retries) unchanged. |
| D-5 | TSQL-subsystem steps | Never rewritten. Pass through verbatim. No audit fields added. |
| D-6 | Unresolved / unparseable / no-manifest steps | Pass through verbatim with original subsystem. Append a line to `<out_dir>/_agent_warnings.log` with `<job_name>:<step_id>: <category>: <details>` where category ∈ `{unparseable, unresolved, ambiguous_basename, manifest_absent}`. Never fail the extractor. |
| D-7 | Manifest absence | Extractor still runs without `--proc-manifest`. Every SSIS step is logged once under category `manifest_absent` and emitted verbatim. No rewriting attempted. All warning lines (including `manifest_absent`) are accumulated during the build loop and written ONCE after the loop completes, sorted by `(job_name, step_id)`. The top-of-log notice line `manifest not supplied — all SSIS steps emitted verbatim` is always position 0 (prepended after sorting). |
| D-8 | Determinism | Given the same manifest and the same msdb query results, the emitted YAML files AND `_agent_warnings.log` are byte-identical across runs. Sorted iteration everywhere (steps by `step_id`, warnings by `(job_name, step_id)`). |
| D-9 | Command-line parsers | Three regex patterns matched in order: `(?i)\s/F(?:ILE)?\s+(?P<quoted>"[^"]+"|\S+)`, `(?i)\s/ISSERVER\s+(?P<quoted>"[^"]+"|\S+)`, `(?i)\s/SQ(?:L)?\s+(?P<quoted>"[^"]+"|\S+)`. First match wins. For `/ISSERVER` SSISDB catalog paths like `\SSISDB\Folder\Project\Package.dtsx`, treat the trailing token as basename. Unquoted whitespace-terminated arg accepted as a courtesy but flagged in audit. |
| D-10 | Env-var / config-file commands | Any command containing `%` (Windows env-var) or `/CONFIGFILE` is flagged `unparseable` and passed through verbatim — even if a regex match succeeds. Rewrite would be unsound without knowing the env. |
| D-11 | Test seam | `_maybe_rewrite_step` is module-importable (callable from `tests/test_agent_step_rewriter.py` directly). The warnings sink is a `list[tuple[str,int,str,str]]` parameter passed in by `extract_agent_jobs` — never an implicit file write inside the helper — so unit tests build in-memory `AgentStep` instances, pass an empty list, and assert against both the returned step and the sink contents. Orchestration-level tests reuse the existing `pyodbc.connect` monkey-patch fixture in `tests/test_agent_extractor.py`; do not introduce a second seam. |
| D-12 | `.dtsx` suffix normalisation | `parse_ssis_command` appends `.dtsx` (lowercase) to the captured path's basename when the basename lacks any `.dtsx` extension (case-insensitive check). Idempotent — existing `.dtsx`/`.DTSX` suffix is preserved unchanged for traceability (the audit-field `dtsx_source` retains original case so operators can grep the source command). The resolver compares basenames case-insensitively (D-3) so case preservation in audit fields doesn't break matching. Applies to all three flag forms (`/F`/`/FILE`, `/ISSERVER`, `/SQ`/`/SQL`). |
| D-13 | POSIX path normalisation symmetry | `parse_ssis_command` POSIX-normalises its captured path before returning (`replace('\\\\', '/')`). Manifest entries are already POSIX (T-1). Resolver compares apples to apples; no UNC asymmetry. The `_posix` helper lives in `msb_ssis2sql/util.py` so both the manifest writer (T-1) and the command parser (T-3) share one implementation. |

---

## Scope map

```
convert_tree (existing)
   │
   ├── writes  <out>/main.sql, child.sql, _orchestrator.sql, _batch_warnings.log   (today)
   └── writes  <out>/_proc_manifest.json                                            (NEW — T-1)
                       │
                       │ shared interchange
                       ▼
extract-agent-jobs --proc-manifest <out>/_proc_manifest.json   (NEW flag — T-5)
   │
   ├── msdb query → AgentJob/AgentStep (today)
   ├── rewriter (NEW — T-3/T-4):
   │       SSIS  ─ parse command  ─► resolve manifest  ─► rewrite step + audit fields
   │       TSQL  ─ pass through verbatim (D-5)
   │       miss  ─ verbatim + _agent_warnings.log entry (D-6)
   └── writes  <jobs>/<job_name>.yaml, <jobs>/_agent_warnings.log
```

---

## Acceptance criteria

| ID | Criterion |
|---|---|
| AC-1 | `convert_tree(input_root, output_root)` always writes `<output_root>/_proc_manifest.json`. JSON parses; `version == 1`; `input_root` is the absolute resolved path; `entries` is a list. |
| AC-2 | Two consecutive `convert_tree` runs against the same input produce byte-identical `_proc_manifest.json` (sorted entries, stable formatting). Determinism baseline: `diff -q`. |
| AC-3 | Each manifest entry has exactly three string fields: `dtsx`, `proc`, `out_sql`. All paths use POSIX separators (`/`, never `\`). |
| AC-4 | `extract-agent-jobs --proc-manifest <path>` accepts the flag. Missing flag → run as today (D-7). Invalid JSON → exit non-zero with `manifest invalid: <reason>`. Unknown `version` → exit non-zero with `manifest version unsupported: <n>` (forward-compat). |
| AC-5 | A msdb row with `subsystem=SSIS`, `command='DTExec /FILE "C:/etl/fact/nightly_load.dtsx" /CHECKPOINTING OFF'` AND a manifest entry `{"dtsx": "fact/nightly_load.dtsx", "proc": "usp_fact_nightly_load"}` produces a YAML step with `subsystem: TSQL`, `command: "EXEC usp_fact_nightly_load;"`, `original_subsystem: SSIS`, `original_command: <verbatim>`, `dtsx_source: "fact/nightly_load.dtsx"`. |
| AC-6 | `subsystem=SSIS`, `command='dtexec /ISSERVER "\SSISDB\Sales\Etl\NightlyLoad.dtsx"'` resolves by basename against a manifest entry whose `dtsx` ends with `NightlyLoad.dtsx` (case-insensitive). Same audit fields as AC-5. |
| AC-7 | `subsystem=SSIS`, `command='DTExec /SQ "\\Pkgs\\NightlyLoad"'` (msdb-stored package, no `.dtsx` suffix) appends `.dtsx` for matching, then resolves by basename. Same audit fields as AC-5. |
| AC-8 | `subsystem=TSQL`, `command='EXEC dbo.usp_something'` is emitted UNCHANGED. No `original_*` fields added. No warning. |
| AC-9 | `subsystem=SSIS`, `command='/UNKNOWN_FLAG x'` (no recognised flag) → step emitted verbatim with `subsystem: SSIS`; warning line in `_agent_warnings.log` matching `^<job>:<step_id>: unparseable: no /FILE, /ISSERVER, or /SQL flag found$`. |
| AC-10 | `subsystem=SSIS`, `command='DTExec /FILE "C:/etl/missing.dtsx"'` with no manifest entry for `missing.dtsx` → verbatim + warning category `unresolved`. |
| AC-11 | `subsystem=SSIS` step with TWO manifest entries whose basenames both match (e.g. two `nightly_load.dtsx` files in different dirs) → verbatim + warning category `ambiguous_basename` listing both candidates. |
| AC-12 | When `--proc-manifest` is omitted, every SSIS step is emitted verbatim. `_agent_warnings.log` opens with the literal line `manifest not supplied — all SSIS steps emitted verbatim` followed by one `manifest_absent` line per SSIS step. |
| AC-13 | `subsystem=SSIS`, `command='DTExec /FILE "%SSIS_ROOT%/foo.dtsx"'` (env var) OR `command='DTExec /FILE "foo.dtsx" /CONFIGFILE "bar.dtsConfig"'` (config file) → verbatim + warning category `unparseable` with reason `env var present` or `config file present`. (D-10) |
| AC-14 | `_agent_warnings.log` is byte-identical across two consecutive extractor runs given the same msdb data + manifest (D-8). Lines sorted by `(job_name, step_id)`. |
| AC-15 | All existing 612 tests still pass (`0 failed`). Final pytest count documented in the PR body; no hard floor — the gate is the coverage check in AC-16 plus 0-failed. |
| AC-16 | Coverage: each new module (`msb_ssis2sql/agent/manifest.py`, `msb_ssis2sql/agent/command_parser.py`) reports ≥ 80% line coverage per `coverage_check.sh`. |
| AC-17 | A parsed command path containing `\` separators (Windows / UNC form, e.g. `DTExec /FILE "C:\etl\fact\nightly_load.dtsx"`) resolves correctly against a manifest entry written with `/` separators (D-13 cross-OS contract). |
| AC-18 | `AgentStep` whose three audit fields (`original_subsystem`, `original_command`, `dtsx_source`) are all `None` emits YAML byte-identical to today's emitter output for that step. The existing golden `tests/fixtures/golden_jobs/example_job.yaml` is preserved unchanged. A NEW golden `tests/fixtures/golden_jobs/example_job_rewritten.yaml` locks the WITH-audit-fields path. (Closes round-1 C-1.) |

---

## Implementation tasks

### T-1 — Manifest emission in `batch.py`

Touch `msb_ssis2sql/batch.py` at **`batch.py:275-282`** (current `_batch_warnings.log` write site, re-pinned against HEAD). Add the manifest emit IMMEDIATELY AFTER the warnings log write — i.e. AFTER the `for dir_path, dir_files in sorted(by_dir.items()):` loop closes, NEVER inside it. Per-directory emission would produce N manifests and break AC-1.

```python
import json
manifest = {
    "version": 1,
    "input_root": str(input_root.resolve()),
    "entries": sorted(
        [
            {"dtsx": _posix(outcome.source.relative_to(input_root)),
             "proc": outcome.procedure_name,
             "out_sql": _posix(outcome.destination.relative_to(output_root))}
            for outcome in result.outcomes
            if outcome.ok and outcome.procedure_name
        ],
        key=lambda e: e["dtsx"],
    ),
}
(output_root / "_proc_manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
```

All path references in the dict literal MUST be attributes of `outcome` — never a loop variable from earlier in `batch.py`. The proc-name formula lives in `_resolve_proc_name` at `batch.py:112-118` and is reused via `outcome.procedure_name`; do not duplicate the formula.

**`_posix` helper lives in `msb_ssis2sql/util.py`** (D-13 — shared with `agent/command_parser.py`), defined as `def _posix(p: pathlib.Path | str) -> str: return str(p).replace("\\", "/")`. Synth orchestrators (no source dtsx) are excluded by the `outcome.ok and outcome.procedure_name` filter.

### T-2 — Manifest reader (new module `msb_ssis2sql/agent/manifest.py`)

Pure-data module. Two functions:

```python
def load_manifest(path: pathlib.Path) -> Manifest: ...  # JSON load + schema validation
def resolve(manifest: Manifest, parsed_path: str) -> ResolveResult: ...
```

`ResolveResult` is a tagged union — `Hit(proc, dtsx_source)`, `Miss()`, `Ambiguous(candidates)`. Algorithm per D-3.

`Manifest` is a frozen dataclass with `version`, `input_root`, `entries: tuple[ManifestEntry, ...]`. Loaders raise `ManifestError("invalid: <reason>" | "unsupported version: <n>")`.

### T-3 — SSIS command parser (new module `msb_ssis2sql/agent/command_parser.py`)

Pure-string transform. One function:

```python
def parse_ssis_command(command: str) -> ParseResult:  # Hit(path) | Unparseable(reason)
    ...
```

Implements D-9 regex set + D-10 env-var / config-file guard + D-12 `.dtsx` suffix normalisation + D-13 POSIX path normalisation. Returns `Unparseable("env var present")`, `Unparseable("config file present")`, or `Unparseable("no /FILE, /ISSERVER, or /SQL flag found")` when those gates fire.

**Inline-comment requirement**: the third regex `\s/SQ(?:L)?\s+(?P<quoted>"[^"]+"|\S+)` carries a load-bearing trailing `\s+`. Add a code comment: `# trailing \s+ is load-bearing — prevents matching /SQUASH, /SQLDBG, /SQLOUTPUT etc.` A negative test asserts `/SQUASH x` returns `Unparseable("no /FILE, /ISSERVER, or /SQL flag found")`.

### T-4 — Step rewriter wired into `extractor.py`

`extractor.py:185-199` builds `AgentStep` from msdb rows today. Add a rewrite pass AFTER the step is built but BEFORE it joins `steps_by_job`:

```python
step = _maybe_rewrite_step(step, job_name, manifest, warning_sink)
```

`_maybe_rewrite_step` lives in `extractor.py` (or a sibling module). For `subsystem == "SSIS"`:
1. Parse via T-3. Unparseable → warn, return step verbatim.
2. Resolve via T-2. Miss → warn `unresolved`, return verbatim. Ambiguous → warn `ambiguous_basename`, return verbatim.
3. Hit → return new `AgentStep` with `subsystem="TSQL"`, `command=f"EXEC {hit.proc};"`, plus a new dataclass field carrying the audit triple (`original_subsystem`, `original_command`, `dtsx_source`). For non-SSIS or unmatched, return original step unchanged.

`AgentStep` needs three new optional fields with default `None`: `original_subsystem`, `original_command`, `dtsx_source`. Emitter renders them only when populated (D-4 / D-8 determinism).

### T-5 — CLI flag `--proc-manifest`

`msb_ssis2sql/cli.py:62` adds the `extract-agent-jobs` subparser. Add `parser.add_argument("--proc-manifest", type=pathlib.Path, default=None)`. Pass to `extract_agent_jobs(...)`. Update the `_cmd_extract_agent_jobs` handler. Update `run.bat` option 12 to prompt for the manifest path (blank → no manifest, per D-7).

### T-6 — YAML emitter audit fields (**RESOLVES round-1 C-1**)

`msb_ssis2sql/agent/yaml_emitter.py` today is one line: `yaml.safe_dump(asdict(job), sort_keys=True, default_flow_style=False)`. `dataclasses.asdict()` includes `None`-valued fields verbatim, so adding three new optional fields to `AgentStep` would silently emit `original_subsystem: null`, `original_command: null`, `dtsx_source: null` on every step and break `tests/fixtures/golden_jobs/example_job.yaml` before any rewriter code runs.

**Required fix**: replace `safe_dump(asdict(job))` with a custom builder that:

1. Calls `asdict(job)`.
2. For each step dict, **removes** the three new audit keys (`original_subsystem`, `original_command`, `dtsx_source`) when their value is `None`. All other keys produced by `asdict()` are passed through unchanged regardless of value — this preserves today's golden, which has no None-valued fields anyway (e.g. `database_name: SalesDW`, `notify_email_operator: ops-team`). The filter predicate must be name-based, not value-based.
3. When audit fields are populated, renders them at the TOP of the step block (before `step_id`) via dict-insertion order, using `sort_keys=False` AT THE STEP LEVEL while keeping `sort_keys=True` for the job-level mapping so other ordering remains deterministic.

The existing `tests/fixtures/golden_jobs/example_job.yaml` is preserved byte-for-byte (audit fields absent because both step instances have `original_*=None`). A NEW golden `tests/fixtures/golden_jobs/example_job_rewritten.yaml` locks the WITH-audit-fields output for a synthesised rewritten job. `tests/test_agent_yaml_determinism.py` gains a `test_emit_job_yaml_matches_appendix_a_golden_rewritten` test against the new golden.

AC-18 explicitly locks the no-audit-fields path's byte identity.

### T-7 — `_agent_warnings.log` writer

In `extract_agent_jobs`, accumulate `(job_name, step_id, category, details)` tuples during the rewrite pass. Sort by `(job_name, step_id)`. Write `<out_dir>/_agent_warnings.log` with one line per warning. Empty file if zero warnings.

### T-8 — Tests + fixtures

New fixtures:

* `tests/fixtures/agent_manifest/` — directory with three fake manifest files: `valid.json` (3 entries), `invalid_json.json`, `wrong_version.json`.
* `tests/fixtures/agent_step_commands.json` — table of `(command_text, expected_parse_result)` covering 12 command-line shapes: `/F`, `/FILE`, `/F` unquoted, `/ISSERVER`, `/SQ`, `/SQL`, `/SQ` no .dtsx, env var, configfile, no flag, empty string, TSQL command.

New test modules:

* `tests/test_agent_manifest.py` — schema validation, version check, sorted-entries determinism, `_posix` correctness.
* `tests/test_agent_command_parser.py` — table-driven over the 12 shapes from `agent_step_commands.json`.
* `tests/test_agent_step_rewriter.py` — exhaustive over AC-5..AC-13: builds in-memory `AgentJob`/`AgentStep` instances, runs `_maybe_rewrite_step`, asserts output shape.
* `tests/test_agent_warnings_log.py` — AC-14 determinism + sorting.
* `tests/test_batch_proc_manifest.py` — AC-1, AC-2, AC-3 end-to-end against `tests/fixtures/main_first/` and `main_first_url_encoded/`.

Modify `tests/test_agent_yaml_determinism.py` to verify the new audit fields render deterministically when populated.

Estimated new tests: ~25-30 across the 5 new modules (12 parser shapes + ~13-17 across rewriter / warnings / manifest emission / yaml-rewritten-golden). Final pytest count documented in PR body per revised AC-15 — no hard floor.

### T-9 — Docs

* README.md: short paragraph in the "How it works" section explaining the manifest interchange.
* `docs/sprint-main-procs-orchestrator.md`: link forward to this sprint.

---

## Test plan

| Phase | Command | Pass criterion |
|---|---|---|
| Baseline | `uv run pytest tests/ -q` | 612 passed |
| RED | `uv run pytest tests/test_agent_manifest.py tests/test_agent_command_parser.py tests/test_agent_step_rewriter.py tests/test_agent_warnings_log.py tests/test_batch_proc_manifest.py -q` | ~18 failed (impl missing) |
| GREEN | `uv run pytest tests/ -q` | ≥630 passed, 0 failed |
| Lint / type | `uv run ruff check . && uv run mypy msb_ssis2sql validation` | clean |
| Coverage | `uv run pytest --cov=msb_ssis2sql --cov-report=term-missing` | ≥80% per module (existing gate) |
| Determinism | Run `convert_tree` + `extract-agent-jobs` twice; diff both `_proc_manifest.json` and the YAML outputs | byte-identical |
| Manual smoke | `uv run python -m msb_ssis2sql convert-tree tests/fixtures/main_first /tmp/smoke && cat /tmp/smoke/_proc_manifest.json` | valid JSON with 3 entries (childa, childb, main) |

---

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Real-world `sysjobsteps.command` strings have shapes the 3 regexes don't cover (multi-package call, `/REPORTING`, `/DECRYPT`) | High | Medium | Conservative: when no recognised flag matches, fall through to `unparseable` with reason naming the unrecognised prefix. Operator sees a clear log line and can patch the rewriter or fix the source job. |
| Two `.dtsx` files with the same basename in different dirs cause silent misroute | Medium | High | D-3 exact-suffix match is preferred; basename match requires UNIQUE entry — multi-candidate is `ambiguous_basename`, never picks one silently. Verified by AC-11. |
| Manifest path mismatches because operator ran convert_tree against a different input_root than the SSIS jobs reference | Medium | Medium | Manifest entries are always relative to `manifest.input_root`. Mismatched roots cause the D-3 three-pass matcher to miss, producing a normal `unresolved` warning. No additional warning category is needed; operator sees the misses and re-runs `convert-tree` against the correct root. (Round-1 M-4: original `path_outside_input_root` category dropped.) |
| TSQL steps reference tables/procs whose schemas changed via the SSIS conversion | Low | Medium | Out of scope (D-5). Document in README that TSQL steps may need manual review post-migration. |
| Env-var commands rewritten unsafely | Low | High | D-10 hard-rejects any command with `%` or `/CONFIGFILE`. Per AC-13. |
| `dtexec` command-line is case-insensitive in real-world SQL Server but our regex uses `(?i)` only on the flag — quoted paths case-preserved | Low | Low | Match by basename uses `.lower()` (D-3); exact suffix match preserves case. Tested via AC-5 (exact) + AC-6 (case-insensitive basename). |

---

## Out of scope

* Rewriting TSQL-subsystem step bodies for renamed schemas/procs (D-5).
* SSIS `/CONFIGFILE` or `.dtsConfig` parsing (D-10).
* Cross-server agent extraction (current extractor is single-DSN).
* Generating the SQL Server Agent CREATE scripts from the YAML (this sprint stops at the YAML).
* Rewriting `command` for non-T-SQL targets (CmdExec, PowerShell, ActiveScripting).
* Repairing the YAML emit pipeline's `notify_email_operator` field (orthogonal).

---

## File-touch summary

| File | Change |
|---|---|
| `msb_ssis2sql/batch.py` | Write `_proc_manifest.json` next to `_batch_warnings.log` (T-1). Add `_posix(p)` helper. |
| `msb_ssis2sql/agent/manifest.py` | **NEW** — `Manifest`, `ManifestEntry`, `ResolveResult`, `load_manifest`, `resolve` (T-2). |
| `msb_ssis2sql/agent/command_parser.py` | **NEW** — `ParseResult`, `parse_ssis_command` (T-3, D-9, D-10). |
| `msb_ssis2sql/agent/model.py` | `AgentStep` gains `original_subsystem`, `original_command`, `dtsx_source` (Optional[str] = None) (T-4). |
| `msb_ssis2sql/agent/extractor.py` | `_maybe_rewrite_step` wired into the build loop (T-4). `_agent_warnings.log` written (T-7). New `manifest_path` kwarg threaded through `extract_agent_jobs`. |
| `msb_ssis2sql/agent/yaml_emitter.py` | Render `original_subsystem` / `original_command` / `dtsx_source` when populated (T-6). |
| `msb_ssis2sql/cli.py` | `extract-agent-jobs` subparser gains `--proc-manifest` (T-5). Handler threads to extractor. |
| `run.bat` | Option 12 prompts for manifest path (T-5). |
| `tests/test_agent_manifest.py` | **NEW** — manifest schema + load/resolve unit tests. |
| `tests/test_agent_command_parser.py` | **NEW** — table-driven over command-line shapes. |
| `tests/test_agent_step_rewriter.py` | **NEW** — AC-5..AC-13. |
| `tests/test_agent_warnings_log.py` | **NEW** — AC-14 determinism. |
| `tests/test_batch_proc_manifest.py` | **NEW** — AC-1..AC-3 end-to-end. |
| `tests/fixtures/agent_manifest/` | **NEW** — valid/invalid JSON fixtures. |
| `tests/fixtures/agent_step_commands.json` | **NEW** — 12 command-line shapes. |
| `tests/test_agent_yaml_determinism.py` | Extend to cover audit-field rendering. |
| `README.md` | One-paragraph note on the manifest interchange. |
| `docs/sprint-main-procs-orchestrator.md` | Forward link to this sprint. |

Estimated diff size: ~250 LOC source + ~380 LOC tests + 2 new fixtures.

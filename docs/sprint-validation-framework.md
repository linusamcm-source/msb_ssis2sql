# Sprint Plan — SSIS-vs-SQL Data Validation Framework

**Repository:** `ssis2sql`
**Author:** generated 2026-05-18
**Status:** amended 2026-05-18 — remote SQL Server execution engine (was Docker/testcontainers); ready for `/team-sprint`
**Companion:** `docs/sprint-coverage-95.md` (unit-coverage sprint — separate effort)

---

## 1. Objective

Build a **validation framework** that proves the T-SQL produced by `ssis2sql`
is *behaviour-equivalent* to the SSIS package it was converted from — by
running both, against identical input data, and comparing the data each
produces in its destination table.

> Given one seeded source database, the rows the **SSIS package** writes to its
> destination (the *golden* reference) must equal the rows the **converted
> T-SQL** writes to the equivalent destination (the *actual* result), modulo a
> declared, reviewed ledger of expected divergences.

This is **differential testing** of the transpiler. The framework does not
re-implement SSIS semantics — the real SSIS engine *is* the oracle.

### Decisions taken (locked before planning)

| Question             | Decision                                                   | Consequence                                                                                                                                                                                                            |
| -------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ground truth         | **Real SSIS golden capture** (`dtexec` on Windows) | No Python oracle. A Windows capture step is part of the framework.                                                                                                                                                     |
| SQL execution engine | **Remote SQL Server** (operator-provisioned)         | Faithful T-SQL.`pyodbc`; connection config from `.env`.                                                                                                                                                            |
| Corpus connectivity  | **ODBC source + ODBC destination**                   | Every corpus package is DB→DB. Both the golden run and the converted-SQL run are pure database operations over ODBC; comparison is table-vs-table. Excel/flat-file endpoints are out of scope for*data* validation. |

---

## 2. The reality — constraints this plan is built around

These are not risks to mitigate later; they are facts the architecture is
shaped by.

1. **SSIS runtime (`dtexec`) is Windows-only.** A `.dtsx` cannot be executed on
   macOS or Linux. Golden capture is therefore a **Windows operator step**, run
   when a corpus package changes — not on every CI run. Its *output* (golden
   fixtures) is committed to the repo; its *consumption* (the comparison) runs
   anywhere.
2. **The converted SQL is genuine T-SQL.** `CAST(... AS NVARCHAR(n))`,
   `SYSDATETIME()`, bracket-quoting, `CREATE OR ALTER PROCEDURE`. It must run on
   SQL Server — a lightweight engine with a shim would produce false
   pass/fails. A real SQL Server instance is the only honest executor; this
   sprint targets a **remote SQL Server** the operator provisions, with
   connection parameters supplied via `.env`.
3. **No golden output exists today.** The bundled `examples/*.xls` files are
   empty SSIS tutorial *templates*, not captured results. The corpus and its
   golden fixtures are built from scratch by this sprint.
4. **The transpiler has *documented, intentional* divergences** (README §
   "Behaviour notes & limitations"): Lookup → `LEFT JOIN`, Sort order survives
   only into a direct destination, error outputs dropped, Row Count dropped,
   package variables become `DECLARE`d parameters. A blind comparator would be
   red forever. The framework encodes these in an **expected-divergence ledger**
   so it distinguishes *known accepted divergence* from *regression*.
5. **Non-deterministic columns exist.** An Audit component emits
   `SYSDATETIME()` / `HOST_NAME()`; a Derived Column may call `GETDATE()`. The
   SSIS run and the SQL run capture *different* timestamps. The comparison
   engine must apply a **per-column policy** (exact / float-epsilon /
   datetime-tolerance / non-null-only / exclude).
6. **The agent fleet building this sprint runs on macOS.** It can build and
   unit-test all framework code, connect to the remote SQL Server, and run the
   static layer. It **cannot execute Story 5's golden capture** — that needs
   Windows. See § 9 "What the sprint delivers vs what needs an operator."

---

## 3. Architecture

Two execution contexts, one shared input, one shared comparator.

```
                    ┌─────────────────── shared, committed to git ───────────────────┐
                    │  validation/corpus/<pkg>/                                       │
                    │    package.dtsx     schema.sql     seed/*.csv     ledger.yaml    │
                    └─────────────────────────────────────────────────────────────────┘
                                 │                                  │
        ╔════════════════════════▼═══════════╗      ╔═══════════════▼════════════════════╗
        ║  CAPTURE CONTEXT  (Windows, manual) ║      ║  VALIDATION CONTEXT  (any OS / CI)  ║
        ║                                     ║      ║                                     ║
        ║  Remote SQL Server                  ║      ║  Remote SQL Server (from .env)      ║
        ║  1. provision schema.sql            ║      ║  1. provision schema.sql            ║
        ║  2. seed source tables from seed/   ║      ║  2. seed source tables from seed/   ║
        ║  3. dtexec package.dtsx  (ODBC)     ║      ║  3. ssis2sql convert package.dtsx   ║
        ║  4. export dest table   ──────────┐ ║      ║  4. execute the .sql  (pyodbc)      ║
        ╚═══════════════════════════════════╪═╝      ║  5. read back dest table ─────────┐ ║
                                            │        ╚═══════════════════════════════════╪═╝
                          golden/*.parquet  │                                  actual    │
                          + manifest.json   │                                  DataFrame │
                                            ▼                                            ▼
                                     ╔══════════════════════════════════════════════════════╗
                                     ║  COMPARISON ENGINE                                    ║
                                     ║  • seed-checksum integrity gate (golden not stale)    ║
                                     ║  • per-column normalisation (schema.sql = type truth) ║
                                     ║  • multiset diff (+ optional ordered)                 ║
                                     ║  • expected-divergence ledger applied                ║
                                     ║  → ComparisonResult: PASS / FAIL / XFAIL(known)       ║
                                     ╚══════════════════════════════════════════════════════╝
```

**Three validation layers**, cheapest first:

| Layer                  | Runs                                      | Needs                                        | Catches                                                                         |
| ---------------------- | ----------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------- |
| **Static**       | every CI run, instant                     | nothing (uses repomix snapshot +`sqlglot`) | malformed SQL, broken column lineage, corpus that no longer covers a transpiler |
| **Execution**    | every CI run with DB access               | reachable remote SQL Server                  | SQL that does not run; runtime errors; wrong row counts                         |
| **Differential** | CI runs where golden fixtures are present | SQL Server + committed golden                | the real prize — data the converted SQL produces ≠ data SSIS produced         |

The differential layer is gated on golden fixtures existing; until an operator
runs Story 5's capture, it **skips with a clear message** rather than failing.

---

## 4. Repository layout (new files)

All framework code is a new top-level `validation/` package — isolated from
`ssis2sql/` (production source) and `tests/` (existing unit tests).

```
validation/
  __init__.py
  config.py              # ODBC connection config, paths, default tolerances
  conftest.py            # pytest: SQL Server connection fixture, corpus discovery
  sqlserver.py           # connection factory + per-test fresh database
  provisioning.py        # apply schema.sql DDL, seed source tables, truncate dest
  sql_runner.py          # ssis2sql convert -> execute .sql -> read back dest
  comparison.py          # the diff engine: multiset/ordered, per-column policy
  ledger.py              # parse ledger.yaml, expected-divergence rules
  static_checks.py       # sqlglot parse-validity + repomix completeness matrix
  reporting.py           # ComparisonResult -> human-readable diff report
  capture/
    capture.py           # Windows golden-capture harness (run by operator)
    capture.ps1          # thin PowerShell wrapper
    RUNBOOK.md           # step-by-step Windows capture instructions
  corpus/
    <package_name>/
      package.dtsx       # ODBC-source -> ... -> ODBC-destination
      schema.sql         # DDL for every source + destination table (TYPE TRUTH)
      seed/
        src_<table>.csv  # deterministic, hand-authored input data
      golden/            # produced by capture.py on Windows, committed
        <dest_table>.parquet
        manifest.json    # captured_at, ssis version, seed_checksum, row counts
      ledger.yaml        # per-column comparison policy + known divergences
  test_validation.py     # parametrized end-to-end differential test
  test_static.py         # static-layer tests
  tests/                 # unit tests for the framework's OWN modules
    test_comparison.py
    test_ledger.py
    test_provisioning.py
    test_sql_runner.py
    test_static_checks.py
```

`tests/` (existing, repo root) stays the `ssis2sql` unit suite — untouched.

---

## 5. The validation corpus (ODBC → ODBC)

Per the locked decision, every corpus package has an **ODBC Source** and an
**ODBC Destination** connection manager. This makes each package a self-contained
DB→DB transformation that both `dtexec` and the converted SQL can run against
the same SQL Server.

### Corpus conventions

- Each package gets its **own database** (`val_<package_name>`), created fresh
  per test run. Tables are bare-named in `dbo` (`src_*`, `dst_*`,
  `ref_*` for lookup reference sets).
- `schema.sql` is the **single source of type truth** — both the golden Parquet
  and the live destination table are loaded and coerced through these column
  types before comparison.
- `seed/*.csv` are small (tens to low-hundreds of rows), hand-authored, and
  **human-editable**. They are committed and version-controlled.
- ODBC connection strings are **parameterised** — the package references a
  connection-manager name; capture-time and validate-time inject the actual
  string (`dtexec /CONN`, or the `.dtsx` connection-manager edited by the
  capture harness).

### Coverage requirement

Every `ComponentKind` that has a registered transpiler **must be exercised by at
least one corpus package** (Story 7 enforces this via the repomix snapshot).
Registered transpilers today (verified from `.repomix-output.xml`):

`OLEDB_SOURCE`/`FLATFILE_SOURCE` (Source), `OLEDB_DESTINATION`/`FLATFILE_DESTINATION`
(Destination), `DERIVED_COLUMN`, `DATA_CONVERSION`, `COPY_COLUMN`,
`CONDITIONAL_SPLIT`, `LOOKUP`, `AGGREGATE`, `SORT`, `UNION_ALL`, `MERGE`,
`MERGE_JOIN`, `MULTICAST`, `ROW_COUNT`, `AUDIT`, plus the
`PassThroughFallback` kinds (`OLEDB_COMMAND`, `PIVOT`, `UNPIVOT`, `SCRIPT`,
`SCD`, `CHARACTER_MAP`).

### Proposed corpus packages (Story 6 detail)

| Package                 | Exercises                                                                        | Notes                                                                                                                 |
| ----------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `passthrough_basic`   | Source → Destination                                                            | smoke test; the simplest possible diff                                                                                |
| `derived_and_convert` | Derived Column, Data Conversion, Copy Column                                     | + expression-language feature coverage (arithmetic,`ISNULL`/`REPLACENULL`, `?:`, `TRIM`, `DATEPART`, casts) |
| `conditional_split`   | Conditional Split (multi-branch + default)                                       | one destination per branch                                                                                            |
| `aggregate_group`     | Aggregate (`SUM`/`AVG`/`MIN`/`MAX`/`COUNT`/`COUNT DISTINCT`), Sort   | Sort feeds destination directly → ordered comparison                                                                 |
| `lookup_match`        | Lookup (match output + no-match anti-join)                                       | ledger marks fail-mode divergence                                                                                     |
| `merge_join`          | Merge Join (`INNER`/`LEFT`/`FULL OUTER`)                                   |                                                                                                                       |
| `union_multicast`     | Union All, Merge, Multicast, Row Count, Audit                                    | Audit columns → ledger `exclude`                                                                                   |
| `etl_full`            | the rich pipeline (model on `examples/sales_etl.dtsx`, re-authored ODBC→ODBC) | end-to-end, multiple destinations                                                                                     |

The existing `examples/*.dtsx` (Excel/flat-file) remain parser/transpile fixtures
but are **not** in the data-validation corpus — they cannot be ODBC→ODBC without
re-authoring. State this explicitly so nobody expects them validated.

### Seed-data edge cases (mandatory)

A seed that is all clean happy-path rows validates nothing. Every seed dataset
must include, where the package's transforms can reach them:

- `NULL`s in join keys, aggregated columns, split-predicate columns;
- duplicate rows (multiset semantics — `UNION ALL` keeps them, `COUNT` counts them);
- lookup keys with **no** match and with **multiple** matches;
- numeric boundary values for `CAST` / Data Conversion (precision/scale edges);
- strings needing `TRIM`, with leading/trailing space, and non-ASCII;
- rows hitting **every** Conditional Split branch including the default;
- empty-string vs `NULL` distinction.

Seeds must **not** trigger SSIS error-row redirection (clean enough to not
overflow/convert-fail) — error outputs have no SQL equivalent and are out of
scope; a package that needs them is a ledger `xfail`, not a corpus member.

---

## 6. The comparison contract

The heart of the framework. `validation/comparison.py` + `validation/ledger.py`.

### 6.1 Multiset, not ordered

An SSIS data flow writing to a database destination has **no guaranteed row
order** (a relational table has no inherent order). The primary comparison is
therefore a **multiset (bag) diff**: normalise both sides, then compare
`collections.Counter` of row tuples. This reports exact *missing* rows (in
golden, absent from actual) and *extra* rows (in actual, absent from golden).

**Ordered comparison** is opt-in per destination via `ledger.yaml` and only
valid when the package ends in a Sort feeding the destination directly *and* a
deterministic sort key exists. It re-reads both sides `ORDER BY <key>` and
compares sequences.

### 6.2 Type normalisation — `schema.sql` is the authority

`pyodbc` returns SQL Server types (`float`→`float`, `nvarchar`→`str`,
`datetime2`→`datetime`, `bit`→`bool`, `decimal`→`Decimal`, `int`→`int`). The
golden Parquet preserves types. Both golden and actual are loaded and coerced
through the **same** typed path derived from the destination table's DDL in
`schema.sql`, so a `float` vs `Decimal` representation difference never causes a
false fail.

### 6.3 Per-column policy

Each destination column gets a comparison policy from `ledger.yaml`:

| Policy       | Behaviour                                                              |
| ------------ | ---------------------------------------------------------------------- |
| `exact`    | bit-for-bit after normalisation (default)                              |
| `float`    | `abs(a-b) <= epsilon` (`epsilon` configurable)                     |
| `datetime` | equal within a tolerance (`tolerance` configurable)                  |
| `non_null` | both sides non-null; values not compared (for `HOST_NAME()` etc.)    |
| `exclude`  | column dropped before comparison (for `SYSDATETIME()` audit columns) |

### 6.4 Expected-divergence ledger — `ledger.yaml`

```yaml
package: aggregate_group
destinations:
  dst_region_totals:
    comparison: ordered            # multiset | ordered
    order_key: [region]            # required when comparison: ordered
    columns:
      region:       { policy: exact }
      total_sales:  { policy: float, epsilon: 0.001 }
      loaded_at:    { policy: exclude, reason: "Audit SYSDATETIME() — non-deterministic" }
    known_divergences:
      - kind: lookup_left_join
        component: "Lookup Region"
        handling: xfail            # xfail | filter | accept
        reason: "Lookup set to fail-on-no-match; transpiler emits LEFT JOIN (README limitation)"
```

`known_divergences[].handling`:

- `xfail` — the destination's comparison is expected to fail; framework reports
  `XFAIL`. An *unexpected pass* is itself flagged (the transpiler may have been
  fixed — update the ledger).
- `filter` — apply a documented pre-comparison transform (e.g. drop rows where
  the lookup key did not match) then compare the remainder exactly.
- `accept` — divergence acknowledged, comparison still runs and must pass on the
  non-divergent columns/rows.

Every ledger entry **requires a `reason`** traceable to a README limitation or a
filed issue. The ledger is the auditable record of *where the transpiler is
known not to be faithful*.

### 6.5 The result

```python
@dataclass
class ComparisonResult:
    package: str
    destination: str
    verdict: Literal["PASS", "FAIL", "XFAIL", "XPASS", "SKIP"]
    golden_rows: int
    actual_rows: int
    missing_rows: list[dict]      # in golden, not in actual
    extra_rows: list[dict]        # in actual, not in golden
    cell_mismatches: list[dict]   # same key, differing non-divergent cell
    schema_mismatch: str | None
    applied_divergences: list[str]
```

`reporting.py` renders this as a readable diff (truncated row samples, counts,
which ledger rules fired) for the pytest assertion message and CI log.

---

## 7. Golden capture — Windows operator step (Story 5)

`validation/capture/capture.py`, run on a Windows host with SSIS installed.

### Prerequisites (documented in `RUNBOOK.md`)

- Windows with **SQL Server Integration Services** / `dtexec` on `PATH`
  (SQL Server Developer edition, or the standalone SSIS runtime).
- **Network access to the remote SQL Server** — the same instance the
  validation context uses.
- **Microsoft ODBC Driver 18 for SQL Server**.
- Python 3.10+ with the `validation` extra installed.

### Capture flow (per package)

1. Connect to the remote SQL Server; create `val_<package>`.
2. Apply `schema.sql`; seed `src_*` tables from `seed/*.csv`; **truncate** `dst_*`.
3. Override the package's ODBC connection managers to point at this server
   (`dtexec /FILE package.dtsx /CONN "<cm_name>";"<connstr>" ...`).
4. Run `dtexec`; assert exit code 0 and no error rows redirected.
5. Read each `dst_*` table; write `golden/<dst>.parquet`.
6. Write `golden/manifest.json`:
   ```json
   {
     "package": "aggregate_group",
     "captured_at": "2026-05-18T10:00:00",
     "ssis_product_version": "16.0.x",
     "dtexec_version": "...",
     "seed_checksum": "sha256:...",      // hash of sorted, concatenated seed CSVs
     "destinations": { "dst_region_totals": { "row_count": 12 } },
     "column_types": { "dst_region_totals": { "region": "nvarchar(50)", ... } }
   }
   ```

### Integrity gate

The validation context recomputes the seed checksum from the live `seed/*.csv`
and asserts it equals `manifest.seed_checksum`. **Mismatch → hard fail**: "golden
fixture is stale, re-run capture" — a changed seed silently invalidates a golden
file, and this catches it.

> The sprint **builds** `capture.py` + `RUNBOOK.md` and **commits empty golden/
> placeholders**. The actual capture is an operator action on Windows after the
> sprint merges. Story 8's test skips cleanly until golden fixtures land.

---

## 8. Execution model

### Dependency DAG

```
Story 0  (foundation — blocking)
   │
   ├──────────────┬──────────────┬───────────────┐
   ▼              ▼              ▼               ▼
Story 1        Story 4        Story 6         Story 7
(connection)   (compare)      (corpus)        (static)
   │              │              │
   ▼              │              │
Story 2           │              │
(provision)       │              │
   │              │              │
   ▼              │              │
Story 3           │              │
(sql runner)      │              │
   │              │              │
   │              ▼              ▼
   │           Story 5  ◀────────┘
   │           (capture harness — needs seed + corpus formats)
   │              │
   └──────┬───────┴──────┬───────────────┐
          ▼              ▼               ▼
                    Story 8
        (end-to-end test — needs 1,2,3,4,6; consumes 5's golden when present)
```

### Waves

| Wave | Stories            | Parallelism                      |
| ---- | ------------------ | -------------------------------- |
| 1    | Story 0            | blocking — must complete first  |
| 2    | Stories 1, 4, 6, 7 | fully parallel — disjoint files |
| 3    | Story 2            | after 1                          |
| 4    | Stories 3, 5       | 3 after 2; 5 after 2 & 6         |
| 5    | Story 8            | after 1, 2, 3, 4, 6              |

Stories 4, 6, 7 are off the `1→2→3` spine and run concurrently with it.

---

## 9. What the sprint delivers vs what needs an operator

**Delivered & verifiable by the sprint (macOS agent fleet):**

- All `validation/` framework code, with unit tests (Stories 1–4, 7).
- SQL Server connection fixture working against the remote server.
- The ODBC corpus: packages, schemas, seeds, ledgers (Story 6).
- The capture harness code + `RUNBOOK.md` (Story 5) — *written and lint/type
  clean, but not executed*.
- The end-to-end test (Story 8), which **runs** the provision→convert→execute→
  compare path and **skips the differential assertion** with a clear message
  while `golden/` is empty.
- Static layer + CI wiring (Story 7).

**Requires a Windows operator after merge:**

- Running `capture.py` per corpus package to produce `golden/*.parquet`.
- Once committed, Story 8's differential layer activates automatically.

The sprint's coverage gate (`/team-sprint` default 80%) applies to the
framework's **unit tests**, not the differential tests.

---

## 10. Shared conventions (all stories)

- **Never edit `ssis2sql/`.** This sprint adds a consumer, not a change to the
  transpiler. A failing comparison is a *finding to file*, not a licence to
  patch production source.
- **Never edit existing `tests/` files** or `conftest.py` at repo root.
- Each story owns a disjoint set of files (see per-story "Files owned") — no two
  parallel stories touch the same file.
- Framework unit tests live in `validation/tests/` and must pass green.
- Match repo style: `from __future__ import annotations`, module docstrings,
  type hints, dataclasses for plain data, `loguru` via `ssis2sql.observability`
  for any logging.
- New runtime/test dependencies go in a **new** `validation` optional-dependency
  group — never forced on unit-test contributors.
- Connection parameters (server address, port, SA username, password) come from
  environment / `.env` (gitignored), never hard-coded in committed files.

---

## 11. Stories

### Story 0 — Foundation (blocking)

**Scope.** Stand up the `validation/` package skeleton, declare dependencies,
wire `just` recipes, define config.

**Files owned.** `pyproject.toml`, `justfile`, `validation/__init__.py`,
`validation/config.py`, `validation/tests/__init__.py`, `.env.example`,
`docs/sprint-validation-framework.md` (this file — mark in-progress).

**Developer notes.**

- Add a `validation` optional-dependency group to `pyproject.toml`:
  ```toml
  [project.optional-dependencies]
  dev = ["pytest>=7.0", "pytest-cov>=4.0"]
  validation = [
      "pytest>=7.0",
      "pytest-cov>=4.0",
      "pyodbc>=5.1",
      "pandas>=2.2",
      "pyarrow>=16.0",
      "sqlglot>=25.0",
      "pyyaml>=6.0",
      "python-dotenv>=1.0",
  ]
  ```
- Register a pytest marker so the slow suite is opt-in. Add to
  `[tool.pytest.ini_options]`: `markers = ["validation: differential validation (needs SQL Server)"]`. Do **not** add `validation/` to `testpaths` — keep
  `just test` fast.
- `validation/config.py`: a frozen dataclass holding the ODBC driver name
  (`ODBC Driver 18 for SQL Server`) and the remote-server connection parameters
  — address (`MSSQL_SERVER_ADDRESS`), port (`MSSQL_SERVER_PORT`), SA username
  (`MSSQL_SA_USERNAME`), SA password (`MSSQL_SA_PASSWORD`) — all read from the
  environment, loaded from a gitignored `.env` via `python-dotenv`. Also holds
  the corpus root path, default tolerances (float epsilon, datetime tolerance),
  and a `TrustServerCertificate` flag. No insecure defaults for credentials: if
  the `MSSQL_*` variables are unset, the connection layer raises a clear
  "validation SQL Server not configured" error so dependent tests skip rather
  than fail. Provide a helper that builds the `pyodbc` connection string.
- Commit a `.env.example` at the repo root documenting the four `MSSQL_*`
  variables (address, port, SA username, SA password) with placeholder values.
  The real `.env` is gitignored and supplied by the operator.
- `justfile` recipes:
  - `validate` → `.venv/bin/python -m pytest validation/ -m validation`
  - `validate-unit` → `.venv/bin/python -m pytest validation/tests`
  - `validate-static` → `.venv/bin/python -m pytest validation/test_static.py`
  - extend `install` (or add `install-validation`) to install `.[validation]`.
- Document the **system prerequisite** (not pip-installable) in a comment and in
  the README later: Microsoft ODBC Driver 18 — macOS
  `brew install msodbcsql18`; plus `unixodbc`.

**Acceptance criteria.**

- `pip install -e ".[validation]"` succeeds on macOS.
- `just validate-unit` runs (zero tests collected is fine at this point).
- `import validation.config` works; config reads `MSSQL_SERVER_ADDRESS`,
  `MSSQL_SERVER_PORT`, `MSSQL_SA_USERNAME`, `MSSQL_SA_PASSWORD` from the
  environment (loaded from `.env`).
- `just test` (existing unit suite) still passes and is not slowed.

**Definition of done.** Skeleton committed; deps resolve; recipes present;
existing suite green.

---

### Story 1 — SQL Server connection fixture

**Scope.** A pytest fixture that connects to the operator-provisioned remote
SQL Server and a helper for a fresh per-test database.

**Files owned.** `validation/sqlserver.py`, `validation/conftest.py`,
`validation/tests/test_sqlserver.py`.

**Developer notes.**

- The SQL Server is **remote and operator-provisioned** — this sprint does not
  start, stop, or manage a server. A session-scoped fixture yields a `pyodbc`
  connection built from `config.py` (`MSSQL_SERVER_ADDRESS`,
  `MSSQL_SERVER_PORT`, `MSSQL_SA_USERNAME`, `MSSQL_SA_PASSWORD`).
- Connection via `pyodbc` — build the connection string from `config.py`:
  `DRIVER={ODBC Driver 18 for SQL Server};SERVER=<address>,<port>;UID=<user>;`
  `PWD=<password>;Encrypt=yes;TrustServerCertificate=yes;` — driver 18 defaults
  to encrypted and the server cert is likely self-signed, so
  `TrustServerCertificate=yes` is required (documented as a dev-trust choice).
- Provide `fresh_database(name) -> connection`: connect to `master` with
  `autocommit=True` (DDL `CREATE`/`DROP DATABASE` cannot run inside a
  transaction), `DROP DATABASE IF EXISTS <name>` then `CREATE DATABASE <name>`,
  and return a fresh connection scoped to that database. If a drop is blocked by
  open sessions, `ALTER DATABASE <name> SET SINGLE_USER WITH ROLLBACK IMMEDIATE`
  first. Function-scoped fixture so each corpus package is isolated.
- If the server is unreachable or the `MSSQL_*` environment is not configured,
  the fixture must **skip** (`pytest.skip`) with a clear message (e.g.
  `"validation SQL Server not configured or unreachable"`) — never error.
- Database names are framework-controlled (`val_<package>`); even so, never
  interpolate untrusted input into a DDL string.

**Acceptance criteria.**

- A test acquires a connection and runs `SELECT 1`.
- `fresh_database("val_demo")` yields a usable, empty database; a second call
  with the same name starts clean.
- Server unreachable / `.env` unconfigured → tests skip with a clear message,
  not error.

**Definition of done.** Connection fixture committed; unit test green against
the remote server; skips cleanly when the server is unreachable.

---

### Story 2 — Schema provisioning & seed loader

**Scope.** Given a corpus package directory, create its tables and load its seed
data — deterministically and idempotently.

**Files owned.** `validation/provisioning.py`,
`validation/tests/test_provisioning.py`, plus a tiny throwaway fixture package
under `validation/tests/fixtures/` for unit testing (not a real corpus member).

**Developer notes.**

- `provision(conn, package_dir)`: execute `schema.sql` (split on `GO` batch
  separators — `GO` is a client directive, not T-SQL; `pyodbc` will not accept
  a batch containing it).
- `seed(conn, package_dir)`: for each `seed/src_*.csv`, `TRUNCATE` the target
  table then bulk-insert. Use a parameterised `executemany`; respect column
  order from `schema.sql`.
- CSV `NULL` convention: empty field = `NULL`, unless the column is a string
  type — then define an explicit sentinel (e.g. the literal `\N`) so empty-string
  vs NULL is unambiguous. Document the convention in `provisioning.py` and apply
  it consistently (the same loader feeds Story 5's capture).
- `truncate_destinations(conn, package_dir)`: `TRUNCATE` every `dst_*` table —
  the converted SQL is `INSERT INTO`, which **appends**; a dest must be empty
  before each run or rows double.
- `seed_checksum(package_dir) -> str`: SHA-256 over the sorted, concatenated
  `seed/*.csv` bytes. Story 5 and Story 8 both call this — it is the integrity
  anchor between golden and seed.

**Acceptance criteria.**

- After `provision` + `seed`, source tables contain exactly the seed rows with
  correct types.
- `truncate_destinations` leaves every `dst_*` empty; provision is idempotent
  (re-run = same state).
- `seed_checksum` is stable across runs and changes when a seed CSV changes.

**Definition of done.** Provisioning + seeding + checksum committed; unit tests
green against the SQL Server fixture.

---

### Story 3 — Converted-SQL runner

**Scope.** Convert a corpus `.dtsx`, execute the resulting T-SQL against the
seeded database, and read back the destination tables.

**Files owned.** `validation/sql_runner.py`,
`validation/tests/test_sql_runner.py`.

**Developer notes.**

- Convert in-process via `ssis2sql.convert_file` with
  `ConvertOptions(wrap_in_procedure=False, include_header=False)`:
  - **no procedure** — wrapping yields `CREATE OR ALTER PROCEDURE`, which only
    *defines* the proc; we want the statements executed directly;
  - **no header** — the header carries a generation timestamp, making the file
    non-deterministic (harmless to execution, noisy for diffs/caching).
- Split the SQL on `GO` separators before executing (the generator emits `GO`
  only with a procedure, but split defensively). Execute each batch via `pyodbc`.
- Surface `result.warnings` — attach them to the run result so Story 8 can log
  them alongside the comparison (a warning often explains a divergence).
- `read_destination(conn, table, schema_sql) -> DataFrame`: `SELECT *`, coerced
  to the column types declared in `schema.sql` (the shared type authority).
- A SQL execution error is a **first-class result**, not an exception that aborts
  the suite — return it as `RunResult(error=...)` so Story 8 reports
  "converted SQL failed to execute" as a `FAIL`, not a crash.

**Acceptance criteria.**

- For `passthrough_basic`, the runner converts, executes, and reads back a
  destination DataFrame with the expected columns and row count.
- A deliberately broken package surfaces a structured error, no crash.
- Re-running is clean (depends on Story 2's `truncate_destinations`).

**Definition of done.** Runner committed; unit tests green; warnings captured.

---

### Story 4 — Comparison engine & divergence ledger

**Scope.** The diff engine and the `ledger.yaml` rules. Pure logic — no database,
no SQL — therefore the most thoroughly unit-testable story.

**Files owned.** `validation/comparison.py`, `validation/ledger.py`,
`validation/reporting.py`, `validation/tests/test_comparison.py`,
`validation/tests/test_ledger.py`.

**Developer notes.**

- `ledger.py`: parse `ledger.yaml` into typed objects — per-destination
  `comparison` mode, `order_key`, per-column `ColumnPolicy`, list of
  `KnownDivergence`. Validate on load: `ordered` requires `order_key`; every
  `known_divergence` requires a non-empty `reason`; unknown policy → error.
- `comparison.py` — `compare(golden_df, actual_df, dest_ledger) -> ComparisonResult`:
  1. **Schema check** — same column set after `exclude` columns dropped.
  2. **Normalise** — apply per-column policy: drop `exclude`; round `float` to
     epsilon-grid; round `datetime` to tolerance; replace `non_null` columns
     with a presence sentinel.
  3. **Multiset diff** — `Counter` of row tuples; `missing` = golden−actual,
     `extra` = actual−golden.
  4. **Ordered diff** (if `comparison: ordered`) — sort both by `order_key`,
     compare sequences positionally.
  5. **Cell localisation** — when counts match but rows differ and a key exists,
     key-join to report *which cell* in *which row* diverges (far more useful
     than "row X missing, row Y extra").
  6. **Apply divergences** — `xfail` flips a `FAIL` to `XFAIL` (and a `PASS` to
     `XPASS`, which is itself reportable); `filter` applies the documented
     pre-comparison transform; `accept` annotates without changing verdict.
- `reporting.py`: render `ComparisonResult` as a readable block — verdict, row
  counts, up to N sample missing/extra rows, cell mismatches, and which ledger
  rules fired. This text becomes the pytest assertion message.
- Decimal vs float: compare numerically, never by repr.

**Acceptance criteria.**

- Identical frames → `PASS`.
- An injected extra row → `FAIL` with that row in `extra_rows`.
- A float column off by < epsilon → `PASS`; off by > epsilon → `FAIL`.
- An `exclude` column with differing values → does not affect verdict.
- A destination with a `kind: lookup_left_join, handling: xfail` divergence and a
  genuine mismatch → `XFAIL`; the same with matching data → `XPASS` (flagged).
- `ledger.py` rejects a `known_divergence` with no `reason`.

**Definition of done.** Engine + ledger + reporting committed; unit tests cover
every policy and every `handling` mode; all green.

---

### Story 5 — Golden-capture harness & runbook

**Scope.** The Windows operator tooling that produces golden fixtures. Built and
type/lint-clean on macOS; executed later on Windows.

**Files owned.** `validation/capture/capture.py`,
`validation/capture/capture.ps1`, `validation/capture/RUNBOOK.md`,
`validation/tests/test_capture.py`.

**Developer notes.**

- `capture.py` reuses Story 2's `provisioning` (provision + seed + truncate) so
  capture and validation seed *identically* — same loader, same NULL convention.
- Override the package's ODBC connection managers for the capture-time server.
  Prefer `dtexec /FILE <pkg> /CONN "<cm_name>";"<connstr>"` per connection
  manager; fall back to editing the `.dtsx` connection-manager `ConnectionString`
  in a temp copy if `/CONN` matching proves fiddly. Document the chosen method.
- Invoke `dtexec` via `subprocess`; **fail on non-zero exit**; parse stdout for
  redirected error rows and fail if any (the corpus contract forbids them).
- Export each `dst_*` table to `golden/<dst>.parquet` via `pandas`/`pyarrow`.
- Write `manifest.json` per § 7, including `seed_checksum` from Story 2's helper.
- `RUNBOOK.md`: numbered, copy-pasteable — install SSIS/`dtexec` and
  ODBC Driver 18; `pip install -e ".[validation]"`; configure `.env` with the
  remote SQL Server connection; per-package
  `python -m validation.capture.capture <package_name>`; commit `golden/`.
- Commit a `.gitkeep` (or empty `golden/`) per corpus package so the directory
  exists pre-capture and Story 8 can detect "golden absent" cleanly.
- Unit-testable on macOS *without* `dtexec`: factor the `dtexec` call behind a
  seam and test manifest construction, checksum embedding, and Parquet export
  with a stub.

**Acceptance criteria.**

- `capture.py --help` runs on macOS; module imports clean; `mypy`/lint clean.
- Manifest construction unit-tested (correct `seed_checksum`, row counts, types).
- Parquet export round-trips a sample DataFrame.
- `RUNBOOK.md` is complete and self-contained.

**Definition of done.** Harness + runbook committed; macOS-runnable unit tests
green; `dtexec` invocation behind a tested seam.

---

### Story 6 — ODBC validation corpus

**Scope.** Author the corpus: ODBC→ODBC `.dtsx` packages with schemas, seeds,
and ledgers, covering every transpiler and the expression language.

**Files owned.** Everything under `validation/corpus/` (`package.dtsx`,
`schema.sql`, `seed/*.csv`, `ledger.yaml`, empty `golden/` per package).

**Developer notes.**

- Build the eight packages in § 5. Each `.dtsx`: an **ODBC Source**, the
  transform(s) under test, an **ODBC Destination**. Hand-author the XML or build
  in SSDT; keep them minimal — just enough to exercise the target component.
- Authoring reference: `examples/sales_etl.dtsx` (the existing rich worked
  example) and the parser tests show the `.dtsx` structures the parser accepts.
  Confirm each authored package **parses** (`ssis2sql inspect <pkg>`) and
  **transpiles** (`ssis2sql convert <pkg>`) before considering it done.
- `schema.sql`: DDL for every `src_*`, `ref_*`, `dst_*` table. Column types are
  the comparison's type authority — choose them deliberately (`int`,
  `decimal(18,4)`, `nvarchar(n)`, `datetime2`, `bit`).
- `seed/*.csv`: small, deterministic, and **carrying the edge cases in § 5** for
  each package's transforms. A seed that cannot make a transform branch is a gap.
- `ledger.yaml`: per destination — comparison mode, column policies (mark Audit/
  `GETDATE` columns `exclude`), and `known_divergences` with `reason`s tied to
  README limitations (e.g. `lookup_match` → `lookup_left_join` xfail).
- `golden/` stays empty (a `.gitkeep`) — Story 5's capture fills it later.

**Acceptance criteria.**

- Every transpiler-backed `ComponentKind` is exercised by ≥ 1 package
  (Story 7 enforces this mechanically).
- Every package parses and transpiles without an unhandled exception.
- Every package has `schema.sql`, ≥ 1 `seed/src_*.csv`, and a `ledger.yaml`
  whose columns match the destination DDL.
- Seeds include the mandated edge cases for each package's transforms.

**Definition of done.** Corpus committed; all packages parse + transpile; ledgers
schema-consistent; coverage matrix (Story 7) green.

---

### Story 7 — Static structural layer

**Scope.** Execution-free validation: SQL parse-validity, column lineage, and a
repomix-driven completeness matrix.

**Files owned.** `validation/static_checks.py`, `validation/test_static.py`,
`validation/tests/test_static_checks.py`.

**Developer notes.**

- **Parse-validity.** For each corpus package, `ssis2sql convert` then
  `sqlglot.parse(sql, dialect="tsql")`. A parse error = `FAIL` with the
  offending statement. Cheap, instant, catches gross regressions with no database.
- **Column lineage.** Use `sqlglot` (`optimize` / `lineage`) to confirm every
  column in each final `INSERT ... SELECT` resolves to a CTE column or a source
  column — i.e. the converted SQL invents no columns and drops none the
  destination mapping expects.
- **Completeness matrix — this is the repomix step.** Refresh the repomix
  snapshot (`.repomix-output.xml`), then parse it to assert structural invariants
  *without* importing the package:
  1. every `ComponentKind` in `ssis2sql/model.py` either has a
     `@register(...)` transpiler **or** is in an explicit
     `NO_TRANSPILER` allowlist;
  2. every transpiler-backed `ComponentKind` is exercised by ≥ 1
     `validation/corpus/*` package;
  3. the `validation/` package has the modules § 4 prescribes.
     This makes "the corpus keeps pace with the transpiler surface" a *test* — add a
     transpiler without a corpus package and CI goes red. Reuse the `use-repo-code`
     skill / repomix output as the structural source of truth.
- A stale repomix snapshot must be detected (compare mtime against
  `ssis2sql/`); refresh or fail with instructions rather than validating
  against stale structure.

**Acceptance criteria.**

- Every corpus package's converted SQL parses as T-SQL under `sqlglot`.
- The lineage check passes for every package (or reports a precise gap).
- The completeness matrix fails loudly if a `ComponentKind` has a transpiler but
  no corpus coverage.
- The static suite runs with **no database** and in a few seconds.

**Definition of done.** Static checks committed; `just validate-static` green;
completeness matrix enforced.

---

### Story 8 — End-to-end validation test & CI

**Scope.** Tie the layers into one parametrized differential test, and wire CI.

**Files owned.** `validation/test_validation.py`, a CI workflow file
(`.github/workflows/validation.yml` if the repo uses GitHub Actions — confirm
first), README "Validation" section.

**Developer notes.**

- Discover the corpus at collection time (scan `validation/corpus/`); parametrize
  over `(package, destination)` pairs.
- Per parameter, marked `@pytest.mark.validation`:
  1. `fresh_database` (Story 1);
  2. `provision` + `seed` + `truncate_destinations` (Story 2);
  3. run the converted SQL, read back the destination (Story 3);
  4. **golden gate** — if `golden/<dest>.parquet` is absent →
     `pytest.skip("golden fixture not captured — see validation/capture/RUNBOOK.md")`;
  5. **integrity gate** — recompute `seed_checksum`; assert it equals
     `manifest.seed_checksum`, else `FAIL` "golden stale, re-capture";
  6. load golden, `compare` against actual with the package's ledger (Story 4);
  7. assert on `verdict`: `PASS`/`XFAIL` pass the test; `FAIL`/`XPASS` fail it
     with the rendered diff report as the message.
- The test must also **skip cleanly** when the SQL Server is unreachable.
- CI: run `just validate-static` and `just validate-unit` on every push (fast,
  no database needed for the static layer — or gate execution bits behind a
  server-reachable check). Run the full `just validate` on a runner with
  network access to the SQL Server (connection parameters supplied as CI
  secrets); until golden fixtures exist it is all skips — green and honest.
- README: a "Validation framework" section — what it does, `just validate*`
  recipes, the Windows capture dependency, link to `RUNBOOK.md`.

**Acceptance criteria.**

- `just validate` collects one test per `(package, destination)`.
- With no golden fixtures: every differential test **skips** with the runbook
  message; the run is green.
- With a golden fixture present (verify by capturing one manually, or with a
  synthetic golden built from the converted SQL's own output as a
  framework-level self-test): a matching dataset → `PASS`; an injected mismatch
  → `FAIL` with a readable diff.
- A seed changed after capture → integrity-gate `FAIL`, not a misleading data
  diff.
- CI workflow valid; static + unit layers green.

**Definition of done.** End-to-end test committed; skip/integrity/pass/fail paths
all verified; CI wired; README updated.

---

## 12. Risks & mitigations

| Risk                                                                                         | Mitigation                                                                                                                                               |
| -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Remote SQL Server unreachable or not yet provisioned                                         | Connection fixture skips cleanly with a clear message; the sprint proceeds and live tests activate once the server is reachable.                         |
| Connection credentials committed by accident                                                 | `.env` is gitignored; `config.py` reads only from the environment; CI supplies credentials as secrets.                                               |
| Golden capture needs Windows the agent fleet lacks                                           | Sprint builds the harness + runbook; capture is a documented post-merge operator step; Story 8 skips cleanly until golden lands.                         |
| Hand-authored `.dtsx` files malformed / not parser-accepted                                | Story 6 gates every package on `ssis2sql inspect` + `convert`; reference `examples/sales_etl.dtsx` and parser tests for accepted structures.       |
| Comparison false-fails on type representation (`float` vs `Decimal`, datetime precision) | `schema.sql` is the single type authority; both sides coerced through it; per-column `float`/`datetime` tolerance policies.                        |
| Non-deterministic columns (Audit,`GETDATE`)                                                | Ledger `exclude` / `non_null` policy — explicit, reviewed, per column.                                                                              |
| Stale golden after a seed edit                                                               | `seed_checksum` in the manifest; integrity gate hard-fails before comparing.                                                                           |
| Transpiler's documented divergences make the suite permanently red                           | Expected-divergence ledger with `xfail`/`filter`/`accept` + mandatory `reason`; `XPASS` flagged so a fixed transpiler prompts a ledger update. |
| `GO` batch separators break `pyodbc` execution                                           | Provisioning and runner split on `GO` before executing.                                                                                                |
| `INSERT INTO` appends → doubled rows on re-run                                            | `truncate_destinations` before every run, both contexts.                                                                                               |
| ODBC Driver 18 encryption rejects the dev cert                                               | `TrustServerCertificate=yes` in the dev connection string (documented as dev-only).                                                                    |
| Scope creep into "fix the transpiler"                                                        | Convention § 10: a failed comparison is a*finding to file*, never a `ssis2sql/` edit in this sprint.                                                |

---

## 13. Dependencies & prerequisites

**Python (the `validation` extra):** `pyodbc`, `pandas`, `pyarrow`,
`sqlglot`, `pyyaml`, `python-dotenv`, `pytest`, `pytest-cov`.

**System (not pip-installable):**

- A reachable **SQL Server** instance — operator-provisioned (remote);
  connection parameters (address, port, SA username, password) supplied via
  `.env` / environment.
- Microsoft **ODBC Driver 18 for SQL Server** — macOS:
  `brew install msodbcsql18` (Microsoft tap) + `unixodbc`.

**Windows capture host (operator, post-merge):**

- SQL Server Integration Services / `dtexec`.
- Network access to the remote SQL Server.
- ODBC Driver 18.
- Python 3.10+ with `.[validation]`.

---

## 14. How to run

```sh
just install-validation     # venv + ssis2sql + the validation extra
just validate-static        # static layer — no database, seconds
just validate-unit          # framework's own unit tests
just validate               # full differential suite (needs SQL Server; skips until golden exists)
```

Golden capture (Windows operator, after merge):

```sh
# on a Windows host — see validation/capture/RUNBOOK.md
python -m validation.capture.capture <package_name>
git add validation/corpus/<package_name>/golden && git commit
```

---

## 15. Deploy notes

- `/team-sprint` requires a **clean working tree** — verified clean at sprint
  launch.
- The remote SQL Server connection is configured via a gitignored `.env`
  (`MSSQL_SERVER_ADDRESS`, `MSSQL_SERVER_PORT`, `MSSQL_SA_USERNAME`,
  `MSSQL_SA_PASSWORD`); `.env.example` documents the keys.
- Story 0 is **blocking** — it creates `pyproject.toml`/`justfile` changes and
  the `validation/` skeleton every other story builds on.
- Stories 1, 4, 6, 7 fan out in parallel after Story 0; the `1→2→3` spine and
  Story 5 follow; Story 8 closes.
- The 80% coverage gate applies to `validation/`'s **unit tests**
  (`validation/tests/`), not the Windows-dependent differential tests.
- This sprint does **not** modify `ssis2sql/`. If a comparison surfaces a real
  transpiler bug, file it — a separate fix sprint owns the change.

```

```

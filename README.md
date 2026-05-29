# msb_ssis2sql

Convert SSIS (`.dtsx`) data-flow transformations into **consolidated,
behaviour-equivalent T-SQL**.

An SSIS Data Flow Task is a graph of components — a source, a chain of
transformations, a destination — that SSIS executes row-buffer by row-buffer.
`msb_ssis2sql` reads that graph and re-expresses it as set-based SQL: one
consolidated `WITH … INSERT INTO … SELECT` statement per destination, where
each transformation is a common table expression (CTE) in the pipeline.

```
.dtsx  ──parse──▶  IR  ──graph──▶  DAG  ──transpile──▶  CTEs  ──generate──▶  T-SQL
```

## Why

SSIS packages are XML and opaque. Migrating off SSIS, or simply understanding
what a package *does*, means reading transformations one dialog box at a time.
`msb_ssis2sql` turns the whole data flow into a single SQL statement you can read,
diff, run, and version-control.

## Install

Prerequisite: [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS).

```sh
just install            # one command — installs msb_ssis2sql + every dependency group
# or, manually:
uv sync
```

Single install covers the CLI, the TUI, the web server, and the differential
validation framework. Runtime deps: `loguru` (logging), `textual` (TUI),
`pyodbc` (SQL Server / msdb connectivity), and `pyyaml` (Agent-job YAML).
Python 3.14 is pinned via `.python-version`; `uv` will fetch it automatically
if it's not present (the package itself supports Python ≥ 3.11).

macOS users need `brew install unixodbc` once so `import pyodbc` finds
`libodbc.dylib` at runtime — this matters for both differential validation and
SQL Server Agent extraction. The optional `validation` group adds the
differential-comparison stack (`pandas`, `pyarrow`, `sqlglot`,
`python-dotenv`); skip it with `uv sync --no-group validation` if you only
need conversion.

### Offline install (Windows, air-gapped)

`run.bat` option **21 (install-offline)** installs the whole project from a
local `wheels/` directory of pre-downloaded binary wheels (Python 3.14,
`win_amd64`), covering runtime, dev, web, and validation groups — no internet
required. The `wheels/` bundle is **gitignored** and not committed; generate it
once on an online box (see "Refresh the bundle" below), copy it into the repo
on the air-gapped host, then:

```bat
REM From cmd.exe in the repo root, with Python 3.14 (x64) on PATH:
run.bat
REM choose option 21: install-offline
```

Equivalent manual invocation:

```bat
python -m venv .venv
.venv\Scripts\python.exe -m ensurepip --upgrade
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ ^
    --upgrade pip setuptools wheel
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ ^
    --requirement wheels\requirements.txt
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels\ ^
    --no-build-isolation -e .
```

To build or refresh the bundle (on an online box) after dependency changes:

```sh
uv lock
uv export --no-hashes --all-groups --format requirements-txt 2>/dev/null \
    | grep -v '^-e' > wheels/requirements.txt
python -m pip download --dest wheels/ --requirement wheels/requirements.txt \
    --platform win_amd64 --python-version 3.14 \
    --implementation cp --only-binary=:all:
python -m pip download --dest wheels/ --platform win_amd64 \
    --python-version 3.14 --implementation cp --only-binary=:all: \
    setuptools wheel pip
```

## Usage

### Command line

```sh
msb_ssis2sql convert package.dtsx                 # T-SQL to stdout
msb_ssis2sql convert package.dtsx -o output.sql   # ... or to a file
msb_ssis2sql convert package.dtsx --procedure usp_Load   # wrap in a stored procedure
msb_ssis2sql convert package.dtsx --no-header --quiet    # bare SQL, no stderr warnings
msb_ssis2sql inspect package.dtsx                 # print the parsed component graph
```

The CLI has five sub-commands:

| Command | What it does |
|---------|--------------|
| `convert` | one `.dtsx` → consolidated T-SQL (stdout or `-o`, optionally `--procedure`) |
| `inspect` | print the parsed component graph and exit |
| `convert-tree IN OUT` | recursively convert a directory of `.dtsx` into a mirrored `.sql` tree (see [How it works](#how-it-works)); `--no-orchestrator` opts out of the collapsed main proc |
| `extract-agent-jobs` | read `msdb.dbo.sysjobs*` over ODBC and emit one YAML file per SQL Server Agent job; `--proc-manifest` rewrites SSIS steps to call the converted procedures |
| `extract-packages` | connect to a SQL Server store (Windows auth) and write every stored SSIS package to disk as `.dtsx`; auto-detects the SSISDB catalog, falling back to the legacy `msdb` store; `--expanded` also writes the project files. See [Extracting packages from SQL Server](#extracting-packages-from-sql-server) |

`-v` / `-vv` raise the log level on any command (see [Logging](#logging)).

Try it on the bundled example:

```sh
just demo
```

### TUI and web

A Textual control-panel UI wraps the justfile recipes — conversion, tests, and
all three validation layers — without leaving the terminal:

```sh
just tui                       # python -m msb_ssis2sql.tui
just web                       # serve the same TUI in a browser via textual-serve
msb_ssis2sql-web --port 8000   # the web server's own entry point
```

### As a library

```python
from msb_ssis2sql import convert_file, ConvertOptions

result = convert_file("package.dtsx", ConvertOptions(wrap_in_procedure=True))
print(result.sql)
for warning in result.warnings:
    print("warning:", warning)
```

## Logging

Every parse, conversion, and component transpiler is wrapped by the `@logged`
decorator (`msb_ssis2sql/observability.py`): each call is traced, and any exception
is logged with a full traceback before being re-raised. Logging is **off by
default** — importing the library emits nothing.

Turn it on from the CLI:

```sh
msb_ssis2sql convert -v package.dtsx     # -v: info-level    -vv: trace every call
```

or as a library:

```python
from msb_ssis2sql import configure_logging, convert_file

configure_logging(level="DEBUG")
convert_file("package.dtsx")
```

To instrument your own code: `@logged` on a function, `@log_methods` on a class,
or `instrument_module(sys.modules[__name__])` for a whole module. The decorator
**re-raises** by default — pass `reraise=False` only where swallowing the error
and returning `None` is genuinely correct, never as a blanket default.

## Extracting packages from SQL Server

`extract-packages` pulls SSIS packages straight out of a SQL Server instance
and writes each as a `.dtsx` file — the same format `convert-tree` consumes, so
the two compose into an end-to-end migration.

```sh
# Windows Integrated auth (the current process identity). Auto-detects the
# SSISDB catalog, else reads the legacy msdb package store.
msb_ssis2sql extract-packages --server sql01 --out ./packages
just extract-packages sql01 ./packages          # same thing via justfile
```

Two stores are supported, chosen by `--store {auto,msdb,ssisdb}`:

| Store | Source | On disk |
|-------|--------|---------|
| `msdb` | `msdb.dbo.sysssispackages` (the `packagedata` column *is* the `.dtsx`) | `<out>/<folder>/<name>.dtsx` |
| `ssisdb` | the SSIS catalog — packages live inside `.ispac` project archives fetched via `catalog.get_project` and unzipped | `<out>/<folder>/<project>/<name>.dtsx` |

`auto` (the default) probes `DB_ID('SSISDB')` and prefers the catalog when
present. The connection uses **Windows Integrated auth**
(`Trusted_Connection=yes`) — no username or password is ever read, passed, or
logged. Alongside the `.dtsx` tree the command writes a deterministic
`_packages_manifest.json` (every package → its output path) and, when a package
is skipped, a `_packages_warnings.log`. `--clean` wipes the output directory
first for idempotent re-runs; `--filter` selects by case-insensitive substring.

### From an Azure DevOps pipeline

`azure-pipelines.yaml` runs this from a pipeline. Every operator input (server,
port, store, database, filter, output directory) is a `parameters:` entry at the
top of the file, chosen at queue time. Because Windows Integrated auth carries
no password, **nothing secret is stored in the pipeline** — but it does require
a **self-hosted Windows agent** whose service account is the domain identity
with read access to SQL Server (Microsoft-hosted agents cannot join the domain
and cannot do integrated auth). The agent VM needs ODBC Driver 18 and `uv`
installed once. An optional `convertToSql` parameter chains the extracted
packages straight into `convert-tree`. See
`docs/plan-extract-packages-pipeline.md` for the full design and the read-only
SQL grants required.

## Project-deployment model (expanded `.ispac`)

A project-deployment SSIS project (the unzipped contents of an `.ispac`) keeps
parameters and shared connection managers *outside* the individual packages:

```
MyProject/
  @Project.manifest    protection level, version, package list
  Project.params       $Project::… parameters (typed, with defaults)
  Staging.conmgr        shared (project) connection managers
  LoadSales.dtsx        packages that reference the above
```

`msb_ssis2sql` reads these and threads them through conversion, so a package
that references `$Project::Param` or a project-scoped connection converts with
real values instead of empty placeholders:

```python
from msb_ssis2sql import convert_project
results = convert_project("MyProject/")        # {package_stem: ConversionResult}
```

`convert-tree` auto-detects an expanded project (any directory containing
`@Project.manifest`) and applies its context to every package in that
directory — no flag needed. What gets used:

- **Project & package parameters** → typed `DECLARE`s with their real default
  values (`$Project::BatchSize` → `DECLARE @BatchSize INT = 5000;`). Package
  parameters override project parameters of the same name. **Sensitive**
  parameters (and any project under an `Encrypt*WithPassword` protection level)
  are emitted as `NULL` with a warning — their values are not in the export.
- **Project connection managers** → a package referencing a shared connection
  resolves it (package scope first, then project scope). With the opt-in
  `ConvertOptions(qualify_from_connection=True)`, source/destination tables are
  prefixed with the database from the connection string
  (`[db].[schema].[table]`); off by default.

To get a faithful expanded project out of an SSISDB catalog in one step, use
`extract-packages --expanded`, which writes `@Project.manifest`,
`Project.params` and `*.conmgr` alongside the `.dtsx` — making extract →
`convert-tree` a lossless round-trip. See
`docs/plan-project-deployment-model.md` for the full design.

## How it works

The framework is four decoupled stages, each in its own module:

| Stage | Module | Responsibility |
|-------|--------|----------------|
| Parse | `parser.py` | `.dtsx` XML → `model.py` intermediate representation |
| Graph | `graph.py` | components + paths → a topologically-ordered DAG |
| Transpile | `transforms/` | one transpiler per component kind → a relation (CTE) |
| Generate | `generator.py` | assemble CTEs → one consolidated statement per sink |

Each component output is modelled as a `Relation` — a named result set that
becomes a CTE. A downstream transpiler never re-parses an upstream component;
it only reads the upstream `Relation`'s column list. The generator walks the
graph backwards from each destination so a statement's `WITH` block contains
exactly the CTEs that destination depends on.

### Orchestrator-only `main.dtsx` collapse

When `convert-tree` encounters a directory whose `main.dtsx` is a pure
control-flow orchestrator — zero Data Flow Tasks plus one or more
`ExecutePackageTask`s that resolve to siblings in the same directory —
`main.sql` is emitted as a *single* stored procedure whose body is the
topologically ordered `EXEC` sequence. No separate `*_orchestrator.sql` file
is produced, and the canonical entry point keeps the `usp_<main>` name.
Mixed-mode mains (DFTs + EPTs), dangling-only EPTs, and the `--no-orchestrator`
CLI flag all fall back to the legacy dual-file output (`main.sql` body + a
distinct `*_orchestrator.sql`). See `docs/plan-final-orch-only-main.md` for
the full decision matrix.

### Agent-step rewriting via `_proc_manifest.json`

`convert-tree` writes a deterministic `_proc_manifest.json` alongside
`_batch_warnings.log`, mapping each converted `.dtsx` to its T-SQL procedure
name. `extract-agent-jobs --proc-manifest <path>` consumes that manifest and
rewrites `msdb.dbo.sysjobsteps` rows whose `subsystem = SSIS` so the emitted
YAML calls the new procedure (`subsystem: TSQL`, `command: EXEC <proc>;`).
Unresolved, unparseable, or ambiguous steps are passed through verbatim with
an entry in `<out>/_agent_warnings.log`. See
`docs/plan-final-agent-step-procs.md` for the full decision table.

## Supported components

| SSIS component | T-SQL translation |
|----------------|-------------------|
| OLE DB / ADO.NET / ODBC / Excel / XML / Flat File **Source** | base CTE — `SELECT … FROM` table or SQL command |
| **Derived Column** | computed columns from translated SSIS expressions |
| **Data Conversion** | `CAST(…)` columns |
| **Copy Column** | duplicated columns |
| **Conditional Split** | one filtered CTE per output; first-match-wins via negation |
| **Lookup** | reference CTE + `LEFT JOIN`; no-match output as an anti-join |
| **Aggregate** | `GROUP BY` with `SUM` / `AVG` / `MIN` / `MAX` / `COUNT` / `COUNT(DISTINCT)` |
| **Sort** | `ORDER BY` (applied at a destination it feeds directly) |
| **Union All** / **Merge** | `UNION ALL` |
| **Merge Join** | `INNER` / `LEFT` / `FULL OUTER JOIN` |
| **Multicast** | shared-CTE reuse |
| **Row Count** | pass-through (the variable assignment is dropped) |
| **Audit** | system-context columns (`SYSDATETIME()`, `HOST_NAME()`, …) |
| OLE DB / Flat File **Destination** | terminal `INSERT INTO … SELECT` |
| Character Map / Script / Pivot / Unpivot / OLE DB Command / SCD | pass-through + warning |

## SSIS expression translation

The Derived Column and Conditional Split expression language is a distinct
mini-language with its own lexer, Pratt parser, and translator
(`expressions/`). It is **not** T-SQL, and the differences are translated, not
ignored:

| SSIS | T-SQL |
|------|-------|
| `==`, `!=` | `=`, `<>` |
| `&&`, `\|\|`, `!` | `AND`, `OR`, `NOT` |
| `ISNULL(x)` | `x IS NULL` *(a boolean — not a coalesce)* |
| `REPLACENULL(a, b)` | `COALESCE(a, b)` |
| `cond ? a : b` | `CASE WHEN cond THEN a ELSE b END` |
| `(DT_STR,n,cp) x` | `CAST(x AS VARCHAR(n))` |
| `TRIM(x)` | `LTRIM(RTRIM(x))` |
| `DATEPART("yyyy", d)` | `DATEPART(year, d)` |
| `"text"` | `N'text'` (control characters spliced as `NCHAR(n)`) |

Comparisons used where a value is expected become `CASE WHEN … THEN 1 ELSE 0
END`; bare values used as predicates become `… <> 0` — mirroring how SSIS
coerces between its boolean and integer worlds.

## Behaviour notes & limitations

`msb_ssis2sql` aims for behaviour equivalence and **flags every place it cannot
guarantee it** — read the warnings (printed to stderr and embedded in the SQL
header).

- **Lookups** are emitted as `LEFT JOIN`. A lookup configured to *fail* on a
  missing match is closer to an `INNER JOIN`; a warning marks each one.
- **Error outputs** have no set-based equivalent (SQL has no per-row
  redirection) and are dropped.
- **Sort** order only survives if the Sort feeds a destination directly — a
  CTE cannot carry an `ORDER BY`.
- **Row Count** variable assignments are dropped (rows pass through unchanged).
- **Control-flow** (precedence constraints, loops, Execute SQL Tasks) is not
  converted — only data-flow transformations. Execute SQL Tasks are copied into
  the output as comments for reference.
- **Character Map / Script / Pivot / Unpivot / OLE DB Command / SCD**
  components become pass-throughs with a warning; they need manual rework.
- Package **variables** referenced by expressions become `DECLARE`d parameters;
  confirm their types and values before running.

## Extending

Adding support for a component is one self-contained file. Subclass
`Transpiler`, register it against a `ComponentKind`, and build a relation:

```python
from msb_ssis2sql.model import ComponentKind
from msb_ssis2sql.transforms import Transpiler, register

@register(ComponentKind.MY_COMPONENT)
class MyTranspiler(Transpiler):
    def transpile(self, ctx, component):
        upstream = ctx.single_upstream(component)
        output = component.non_error_outputs()[0]
        ctx.make_relation(component, output, list(upstream.columns),
                          ctx.from_clause(upstream), name_hint=component.name)
```

Import the module from `transforms/__init__.py` so it self-registers.

## Project layout

```
msb_ssis2sql/
  parser.py            .dtsx XML  -> intermediate representation
  project.py           expanded .ispac (@Project.manifest / .params / .conmgr) -> Project
  model.py             the IR dataclasses
  relation.py          the Relation - a named result set that becomes a CTE
  component_types.py   componentClassID -> ComponentKind
  graph.py             the data-flow DAG + topological sort
  control_graph.py     control-flow DAG over ExecutePackageTasks + constraints
  expressions/         SSIS expression language: lexer, parser, translator
  transforms/          component transpilers, plus the build context and registry
  generator.py         CTE assembly -> consolidated T-SQL
  batch.py             convert-tree: a directory of .dtsx -> a mirrored .sql tree
  agent/               SQL Server Agent job extraction (msdb -> YAML) + step rewriting
  packages/            SSIS package extraction (msdb store / SSISDB catalog -> .dtsx)
  dialect.py           T-SQL identifier quoting
  sqltypes.py          SSIS data-type codes -> T-SQL types
  _naming.py           identifier sanitiser + per-directory collision suffixes
  errors.py            the Ssis2SqlError exception hierarchy
  util.py              small dependency-free shared helpers
  observability.py     loguru logging: @logged / log_methods / instrument_module
  cli.py               the `msb_ssis2sql` command line
  tui.py               the Textual control-panel UI
  web.py               serve the TUI in a browser (msb_ssis2sql-web)
examples/sales_etl.dtsx   a worked package exercising every transpiler
tests/                    pytest suite
validation/               differential validation framework (see below)
```

## Testing

```sh
just test          # or: uv run pytest
just cov           # tests with a line-coverage report
just lint          # ruff (PEP 8 + pyflakes)
just typecheck     # mypy over msb_ssis2sql + validation
```

## Validation

The `validation/` tree is a differential test harness that verifies the
`msb_ssis2sql` transpiler's converted T-SQL produces identical results to the
SSIS package's own execution.  It has three independent layers.

### Three layers

| Layer | Command | Requires |
|-------|---------|----------|
| Static | `just validate-static` | Nothing — pure analysis |
| Unit | `just validate-unit` | Nothing — no SQL Server |
| Differential | `just validate` | SQL Server + golden fixtures |

All three layers are also runnable from the Textual TUI (`just tui` →
**Validation** tab) without leaving the terminal.

**Static** (`validation/test_static.py`) runs structural checks on every
corpus package — sqlglot parse-validity of the converted T-SQL, column
lineage resolution, and a completeness matrix across all 15 must-cover
component kinds.  Runs in under a second with no external dependencies.

**Unit** (`validation/tests/`) exercises the framework's own modules —
schema provisioning, seed loading, SQL runner, comparison engine, capture
harness, and static check functions — with mocked or fixture-driven data.
No SQL Server required.

**Differential** (`validation/test_validation.py`) is the live gate: it
provisions the schema, seeds source data, runs the converted SQL, and
compares row-by-row against the SSIS golden output captured at
`validation/corpus/<package>/golden/`.  Requires a live SQL Server and
pre-captured golden fixtures (see [Golden capture](#golden-capture) below).

### Corpus

`validation/corpus/` contains eight SSIS packages exercising every
supported component kind:

```
validation/corpus/
  passthrough_basic/       OLEDB_SOURCE → OLEDB_DESTINATION
  derived_and_convert/     DERIVED_COLUMN + DATA_CONVERSION + COPY_COLUMN
  conditional_split/       CONDITIONAL_SPLIT (three branches + default)
  aggregate_group/         AGGREGATE + SORT
  lookup_match/            LOOKUP (match + no-match outputs)
  merge_join/              MERGE_JOIN (inner/left/full outer)
  union_multicast/         UNION_ALL + MERGE + MULTICAST + ROW_COUNT + AUDIT
  etl_full/                end-to-end pipeline: SOURCE + DERIVED_COLUMN + LOOKUP + CONDITIONAL_SPLIT + UNION_ALL + SORT + DESTINATION
```

Each package directory contains `package.dtsx`, `schema.sql`,
`seed/<table>.csv`, `golden/` (captured output), and `ledger.yaml`
(per-column comparison tolerance overrides).

### Golden capture

Before running the differential layer, capture golden output from SSIS
on a Windows host with dtexec:

```sh
# On the Windows host — see validation/capture/RUNBOOK.md for full steps
.\capture.ps1 --package-dir validation\corpus\passthrough_basic
```

The RUNBOOK at `validation/capture/RUNBOOK.md` covers prerequisites,
environment setup, troubleshooting, and the expected output layout.

### Configuration

The differential and capture layers connect to SQL Server via four
environment variables.  Copy `.env.example` to `.env` (gitignored) and
fill in your instance:

```sh
cp .env.example .env
```

```
MSSQL_SERVER_ADDRESS=your-sql-server-host-or-ip
MSSQL_SERVER_PORT=1433
MSSQL_SA_USERNAME=sa
MSSQL_SA_PASSWORD=YourStrong!Passw0rd
```

Never commit `.env` — it is gitignored.  The static and unit layers
require no `.env` at all.

### CI

`.github/workflows/validation.yml` runs the static and unit layers on
every push and pull request:

```
push / pull_request
  └── static-and-unit (ubuntu-latest)
        ├── just install
        ├── just validate-static   # no SQL Server
        └── just validate-unit     # no SQL Server
```

The differential layer is not run in CI — it requires an
operator-provisioned SQL Server.

### Quick reference

```sh
just install              # one command — installs msb_ssis2sql + every dependency group
just validate-static      # static checks — no SQL Server (< 1 s)
just validate-unit        # unit tests — no SQL Server
just validate-cov         # unit tests with coverage report
just validate             # full differential suite — SQL Server required
```

## License

MIT.

# ssis2sql

Convert SSIS (`.dtsx`) data-flow transformations into **consolidated,
behaviour-equivalent T-SQL**.

An SSIS Data Flow Task is a graph of components — a source, a chain of
transformations, a destination — that SSIS executes row-buffer by row-buffer.
`ssis2sql` reads that graph and re-expresses it as set-based SQL: one
consolidated `WITH … INSERT INTO … SELECT` statement per destination, where
each transformation is a common table expression (CTE) in the pipeline.

```
.dtsx  ──parse──▶  IR  ──graph──▶  DAG  ──transpile──▶  CTEs  ──generate──▶  T-SQL
```

## Why

SSIS packages are XML and opaque. Migrating off SSIS, or simply understanding
what a package *does*, means reading transformations one dialog box at a time.
`ssis2sql` turns the whole data flow into a single SQL statement you can read,
diff, run, and version-control.

## Install

```sh
just install            # creates .venv and installs ssis2sql + pytest
# or, manually:
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

One runtime dependency — [`loguru`](https://github.com/Delgan/loguru), for the
logging instrumentation. Python 3.10+.

## Usage

### Command line

```sh
ssis2sql convert package.dtsx                 # T-SQL to stdout
ssis2sql convert package.dtsx -o output.sql   # ... or to a file
ssis2sql convert package.dtsx --procedure usp_Load   # wrap in a stored procedure
ssis2sql inspect package.dtsx                 # print the parsed component graph
```

Try it on the bundled example:

```sh
just demo
```

### As a library

```python
from ssis2sql import convert_file, ConvertOptions

result = convert_file("package.dtsx", ConvertOptions(wrap_in_procedure=True))
print(result.sql)
for warning in result.warnings:
    print("warning:", warning)
```

## Logging

Every parse, conversion, and component transpiler is wrapped by the `@logged`
decorator (`ssis2sql/observability.py`): each call is traced, and any exception
is logged with a full traceback before being re-raised. Logging is **off by
default** — importing the library emits nothing.

Turn it on from the CLI:

```sh
ssis2sql convert -v package.dtsx     # -v: info-level    -vv: trace every call
```

or as a library:

```python
from ssis2sql import configure_logging, convert_file

configure_logging(level="DEBUG")
convert_file("package.dtsx")
```

To instrument your own code: `@logged` on a function, `@log_methods` on a class,
or `instrument_module(sys.modules[__name__])` for a whole module. The decorator
**re-raises** by default — pass `reraise=False` only where swallowing the error
and returning `None` is genuinely correct, never as a blanket default.

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

## Supported components

| SSIS component | T-SQL translation |
|----------------|-------------------|
| OLE DB / ADO.NET / Flat File **Source** | base CTE — `SELECT … FROM` table or SQL command |
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
| Script / Pivot / Unpivot / OLE DB Command / SCD | pass-through + warning |

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

`ssis2sql` aims for behaviour equivalence and **flags every place it cannot
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
- **Script / Pivot / Unpivot / SCD** components become pass-throughs with a
  warning; they need manual rework.
- Package **variables** referenced by expressions become `DECLARE`d parameters;
  confirm their types and values before running.

## Extending

Adding support for a component is one self-contained file. Subclass
`Transpiler`, register it against a `ComponentKind`, and build a relation:

```python
from ssis2sql.model import ComponentKind
from ssis2sql.transforms import Transpiler, register

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
ssis2sql/
  parser.py            .dtsx XML  -> intermediate representation
  model.py             the IR dataclasses
  component_types.py   componentClassID -> ComponentKind
  graph.py             the data-flow DAG + topological sort
  expressions/         SSIS expression language: lexer, parser, translator
  transforms/          component transpilers, plus the build context and registry
  generator.py         CTE assembly -> consolidated T-SQL
  dialect.py           T-SQL identifier quoting
  sqltypes.py          SSIS data-type codes -> T-SQL types
  observability.py     loguru logging: @logged / log_methods / instrument_module
  cli.py               the `ssis2sql` command line
examples/sales_etl.dtsx   a worked package exercising every transpiler
tests/                    pytest suite
```

## Testing

```sh
just test          # or: .venv/bin/python -m pytest
```

## License

MIT.

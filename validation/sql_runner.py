"""Converted-SQL runner — Story 3.

Transpiles a corpus package's ``package.dtsx`` via ``ssis2sql``, executes the
resulting T-SQL batches against a live SQL Server connection, then reads back
every ``dst_*`` table into a :class:`pandas.DataFrame`.

The runner is intentionally crash-free: any SQL execution failure is captured
and returned as :attr:`RunResult.error` rather than being raised.  This lets
callers distinguish a *transpiler* result from an *execution* result without
wrapping every call in try/except.

Typical call sequence::

    conn = fresh_database("val_passthrough_basic")
    provision(conn, package_dir)
    seed(conn, package_dir)
    result = run(conn, package_dir)
    if result.error:
        print("SQL failed:", result.error)
    else:
        df = result.data["dst_items"]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pyodbc

from ssis2sql import ConvertOptions, convert_file
from ssis2sql.observability import logger
from validation.provisioning import _quote, _safe_identifier

# ---------------------------------------------------------------------------
# Regex — shared with provisioning.py (duplication accepted; Story 3 scope)
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE: re.Pattern[str] = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)

_GO_RE: re.Pattern[str] = re.compile(r"^\s*GO\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Outcome of a single :func:`run` call.

    Attributes
    ----------
    data:
        Mapping of destination table name (e.g. ``"dst_items"``) to the
        :class:`pandas.DataFrame` read back after SQL execution.  Empty dict
        when :attr:`error` is set.
    warnings:
        List of warning strings emitted by ``ssis2sql.convert_file`` during
        transpilation.  May be empty even on success.
    error:
        Non-empty string when the SQL execution step failed.  Empty string
        ``""`` on success.  The runner never raises on execution failure.
    """

    data: dict[str, pd.DataFrame] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def split_sql_batches(sql: str) -> list[str]:
    """Split *sql* on standalone ``GO`` lines, discarding empty batches.

    ``GO`` is a SQL Server Management Studio / sqlcmd batch separator — it is
    not valid T-SQL and must be stripped before passing batches to pyodbc.

    Parameters
    ----------
    sql:
        Raw SQL text that may contain ``GO`` separators on their own lines
        (optionally surrounded by whitespace).

    Returns
    -------
    list[str]
        Non-empty batches in order.  Returns ``[]`` for empty or
        whitespace-only input.  Content after the last ``GO`` (or the entire
        input when there are no ``GO`` lines) is preserved as the final batch.
    """
    batches: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        if _GO_RE.match(line):
            batch = "\n".join(current).strip()
            if batch:
                batches.append(batch)
            current = []
        else:
            current.append(line)
    # Trailing content after the last GO (or the whole file if no GOs).
    batch = "\n".join(current).strip()
    if batch:
        batches.append(batch)
    return batches


def _dst_table_names(package_dir: Path) -> list[str]:
    """Return ``dst_*`` table names declared in ``package_dir/schema.sql``.

    Parses ``schema.sql`` with the same ``CREATE TABLE`` regex used by
    :mod:`validation.provisioning`.  Only names that begin with ``dst_`` are
    returned; ``src_*`` and ``ref_*`` tables are skipped.

    Parameters
    ----------
    package_dir:
        Corpus package directory containing ``schema.sql``.

    Returns
    -------
    list[str]
        Destination table names in declaration order (e.g. ``["dst_items"]``).
    """
    schema_sql = (package_dir / "schema.sql").read_text(encoding="utf-8")
    return [
        name
        for name in _CREATE_TABLE_RE.findall(schema_sql)
        if name.startswith("dst_")
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_destination(
    conn: pyodbc.Connection,
    table: str,
    schema_types: dict[str, str] | None,
) -> pd.DataFrame:
    """Read *table* from the database into a :class:`pandas.DataFrame`.

    Executes ``SELECT * FROM [dbo].[<table>]`` and converts the result to a
    DataFrame using column names from ``cursor.description``.  The table name
    is validated and bracket-quoted before interpolation.

    Parameters
    ----------
    conn:
        Active ``pyodbc.Connection`` to the target database.
    table:
        Unqualified table name (e.g. ``"dst_items"``).  Must be a plain SQL
        identifier (letters, digits, underscores only — validated by
        :func:`validation.provisioning._safe_identifier`).
    schema_types:
        Optional mapping of ``{col_name: sql_type_token}`` (e.g.
        ``{"amount": "NUMERIC"}``).  When provided, columns are coerced to
        the declared type via :func:`pandas.DataFrame.astype` on a
        best-effort basis.  A coercion failure for a column (caused by
        genuinely non-coercible data in that column) logs a warning via
        ``ssis2sql.observability.logger`` and leaves the column dtype
        unchanged.
        When ``None`` or empty, the DataFrame is returned with the types
        pyodbc inferred.

    Returns
    -------
    pandas.DataFrame
        One row per database row, columns in ``SELECT *`` order.
    """
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM [dbo].{_quote(_safe_identifier(table))}")
    columns = [desc[0] for desc in (cursor.description or [])]
    rows = cursor.fetchall()
    df = pd.DataFrame([tuple(row) for row in rows], columns=columns)

    if schema_types:
        _SQL_TO_PANDAS: dict[str, str] = {
            "INT": "Int64",
            "BIGINT": "Int64",
            "SMALLINT": "Int64",
            "TINYINT": "Int64",
            "BIT": "boolean",
            "FLOAT": "float64",
            "REAL": "float64",
            "NUMERIC": "float64",
            "DECIMAL": "float64",
        }
        for col, sql_type in schema_types.items():
            if col not in df.columns:
                continue
            pandas_type = _SQL_TO_PANDAS.get(sql_type.upper())
            if pandas_type is None:
                continue
            try:
                df[col] = df[col].astype(pandas_type)
            except (ValueError, TypeError) as exc:
                # The column contains values that cannot be cast to the target
                # dtype (e.g. non-numeric strings in a numeric column).
                # Nullable pandas dtypes (Int64, boolean, float64) handle
                # None/NaN without raising, so a NULL-bearing column does NOT
                # reach this branch.  Log each failure so callers see the
                # dtype mismatch rather than silently receiving a wrong-typed
                # DataFrame.
                logger.warning(
                    "read_destination: could not coerce column {!r} to {!r}"
                    " (sql_type={!r}): {}",
                    col,
                    pandas_type,
                    sql_type,
                    exc,
                )

    return df


def run(
    conn: pyodbc.Connection,
    package_dir: Path,
    *,
    schema_types: dict[str, str] | None = None,
) -> RunResult:
    """Transpile, execute, and read back a corpus package.

    Steps:

    1. Transpile ``package_dir/package.dtsx`` via
       :func:`ssis2sql.convert_file`.
    2. Split the resulting SQL on ``GO`` batch separators.
    3. Execute each batch against *conn*.
    4. Read back every ``dst_*`` table declared in ``schema.sql`` into a
       :class:`pandas.DataFrame`.
    5. Return a :class:`RunResult` capturing the data, transpiler warnings,
       and any execution error.

    The runner **never raises** on a SQL execution failure.  A
    :exc:`pyodbc.Error` from any ``cursor.execute`` or from the read-back
    ``SELECT`` is caught and stored in :attr:`RunResult.error`;
    :attr:`RunResult.data` is left as an empty dict.

    Parameters
    ----------
    conn:
        Active ``pyodbc.Connection`` to the target database (already
        provisioned and seeded by the caller).
    package_dir:
        Path to the corpus package directory containing ``package.dtsx`` and
        ``schema.sql``.
    schema_types:
        Optional ``{col_name: sql_type_token}`` mapping passed through to
        :func:`read_destination` for post-read type coercion.  ``None`` means
        no coercion.

    Returns
    -------
    RunResult
        On success: ``data`` populated, ``error`` is ``""``.
        On execution failure: ``data`` is ``{}``, ``error`` is non-empty.
        ``warnings`` is always set from the transpiler output.
    """
    dtsx_path = package_dir / "package.dtsx"
    opts = ConvertOptions(wrap_in_procedure=False, include_header=False)
    conversion = convert_file(dtsx_path, opts)
    warnings_list: list[str] = list(conversion.warnings)

    batches = split_sql_batches(conversion.sql)

    try:
        cursor = conn.cursor()
        for batch in batches:
            cursor.execute(batch)
        conn.commit()

        dst_names = _dst_table_names(package_dir)
        data: dict[str, pd.DataFrame] = {}
        for table in dst_names:
            data[table] = read_destination(conn, table, schema_types)
    except pyodbc.Error as exc:
        return RunResult(data={}, warnings=warnings_list, error=str(exc))

    return RunResult(data=data, warnings=warnings_list, error="")

"""Schema provisioning, seed loading, and checksum helpers.

This module handles the database-side setup for each validation corpus package:

1. ``provision`` — parse ``schema.sql``, drop-and-recreate all tables.
2. ``seed`` — load each ``seed/src_*.csv`` into its target table.
3. ``truncate_destinations`` — empty every ``dst_*`` table before a run.
4. ``seed_checksum`` — stable SHA-256 fingerprint of all seed CSVs.

CSV NULL convention (documented here; applied consistently in ``seed``):
- **String columns** (NVARCHAR, VARCHAR, NCHAR, CHAR, TEXT and variants):
    - Empty CSV field  →  ``''``  (empty string, stored as NOT NULL)
    - Literal ``\\N``  →  ``None``  (SQL NULL)
  This lets a genuine empty string be stored while still allowing NULL to be
  expressed unambiguously.
- **Non-string columns** (INT, DECIMAL, DATETIME2, BIT, FLOAT, NUMERIC, …):
    - Empty CSV field  →  ``None``  (SQL NULL)
    - Any other value  →  the literal string value (pyodbc coerces it to the
      column type via the parameterised INSERT).

The same convention is used by ``validation/capture/capture.py`` so that the
seed-data that feeds the golden capture and the seed-data that feeds the
validation run are loaded identically.
"""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyodbc

# ---------------------------------------------------------------------------
# Regex helpers for parsing schema.sql
# ---------------------------------------------------------------------------

# Matches: CREATE TABLE [dbo.]<name> (  or  CREATE TABLE dbo.<name> (
# Captures the table name (without schema prefix or brackets).
_CREATE_TABLE_RE: re.Pattern[str] = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)

# String-family types for which the CSV NULL convention differs from numerics.
_STRING_TYPES: frozenset[str] = frozenset(
    {"NVARCHAR", "VARCHAR", "NCHAR", "CHAR", "TEXT", "NTEXT"}
)

# Lines whose stripped content matches this (case-insensitive) are GO
# batch separators — not T-SQL, must be stripped before pyodbc sees them.
_GO_RE: re.Pattern[str] = re.compile(r"^\s*GO\s*$", re.IGNORECASE)

# Valid SQL identifier: letters/digits/underscores, must start with letter or _.
_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Column-definition entries whose first keyword is one of these are NOT columns
# — they are inline constraints and must be skipped by the column-type parser.
_NON_COLUMN_KEYWORDS: frozenset[str] = frozenset(
    {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "CHECK"}
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_identifier(name: str) -> str:
    """Validate *name* as a plain SQL identifier and return it unchanged.

    Raises ``ValueError`` if *name* contains characters outside
    ``[A-Za-z_][A-Za-z0-9_]*``.  This is defence-in-depth alongside
    bracket-quoting: a name that contains ``]`` would break out of the
    bracket-quoted context even after ``]`` → ``]]`` escaping, so we reject
    it outright.

    Parameters
    ----------
    name:
        A table name or column name from a CSV stem or header row.

    Returns
    -------
    str
        *name* unchanged.

    Raises
    ------
    ValueError
        If *name* does not match ``^[A-Za-z_][A-Za-z0-9_]*$``.
    """
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(
            f"Identifier {name!r} is not a valid plain SQL identifier "
            "(must match ^[A-Za-z_][A-Za-z0-9_]*$). "
            "Refusing to interpolate into DDL/DML."
        )
    return name


def _quote(name: str) -> str:
    """Return *name* bracket-quoted with ``]`` doubled as a backstop.

    Call ``_safe_identifier`` first for defence-in-depth; ``_quote`` alone
    is not sufficient against names that slip through the identifier check.
    """
    return f"[{name.replace(']', ']]')}]"


def _split_batches(sql: str) -> list[str]:
    """Split *sql* on ``GO`` batch separators, discarding empty batches."""
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
    # Trailing content after last GO (or the whole file if there are no GOs).
    batch = "\n".join(current).strip()
    if batch:
        batches.append(batch)
    return batches


def _parse_table_names(schema_sql: str) -> list[str]:
    """Return table names in declaration order from *schema_sql*."""
    return _CREATE_TABLE_RE.findall(schema_sql)


def _parse_column_types(schema_sql: str) -> dict[str, dict[str, str]]:
    """Return ``{table_name: {col_name: type_token}}`` from *schema_sql*.

    Handles both multi-line and single-line ``CREATE TABLE`` forms, and
    correctly skips inline constraint entries (``CONSTRAINT``, ``PRIMARY KEY``,
    ``FOREIGN KEY``, ``UNIQUE``, ``INDEX``, ``CHECK``).

    For each table, the column definitions are extracted from the text between
    the opening ``(`` and the block-closing ``)``, split on top-level commas
    (respecting nested parentheses), and each entry's first two tokens are
    parsed as ``(column_name, type_token)``.  Only the first token of the type
    is captured (e.g. ``NVARCHAR`` from ``NVARCHAR(50)``), which is sufficient
    for the string/non-string NULL-convention distinction.
    """
    result: dict[str, dict[str, str]] = {}

    # Find each CREATE TABLE block: capture name and the parenthesised body.
    # We scan for "CREATE TABLE ... (" then collect lines until the matching ")".
    lines = schema_sql.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        ct_match = _CREATE_TABLE_RE.search(line)
        if not ct_match:
            i += 1
            continue

        table_name = ct_match.group(1)
        result[table_name] = {}

        # Accumulate lines from this CREATE TABLE up to and including the
        # closing ");" / ")" line, tracking paren depth so nested parens
        # (e.g. DECIMAL(18,4)) do not prematurely end the block.
        block_lines: list[str] = [line]
        depth = line.count("(") - line.count(")")
        i += 1
        while i < len(lines) and depth > 0:
            block_lines.append(lines[i])
            depth += lines[i].count("(") - lines[i].count(")")
            i += 1

        block = " ".join(block_lines)

        # Extract the body between the outermost parens.
        open_idx = block.index("(")
        # Find the matching closing paren by counting depth.
        depth = 0
        close_idx = -1
        for j, ch in enumerate(block[open_idx:], start=open_idx):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_idx = j
                    break

        if close_idx == -1:
            continue  # malformed block — skip

        body = block[open_idx + 1 : close_idx]

        # Split body on top-level commas (skip commas inside parens).
        entries: list[str] = []
        current_entry: list[str] = []
        paren_depth = 0
        for ch in body:
            if ch == "(":
                paren_depth += 1
                current_entry.append(ch)
            elif ch == ")":
                paren_depth -= 1
                current_entry.append(ch)
            elif ch == "," and paren_depth == 0:
                entries.append("".join(current_entry).strip())
                current_entry = []
            else:
                current_entry.append(ch)
        last = "".join(current_entry).strip()
        if last:
            entries.append(last)

        # Parse each entry as a column definition.
        for entry in entries:
            # Strip brackets and normalise whitespace.
            tokens = re.split(r"\s+", entry.strip().lstrip("[").lstrip())
            if len(tokens) < 2:
                continue
            first_token = tokens[0].strip("[]").upper()
            # Skip inline constraints (not column definitions).
            if first_token in _NON_COLUMN_KEYWORDS:
                continue
            col_name = tokens[0].strip("[]")
            type_token = tokens[1].strip("[]()").upper()
            # type_token may contain size suffix like NVARCHAR(50) — keep only alpha.
            type_token = re.match(r"[A-Za-z]+", type_token)
            if type_token:
                result[table_name][col_name] = type_token.group(0).upper()

    return result


def _is_string_type(type_token: str) -> bool:
    """Return ``True`` if *type_token* is a string-family SQL type."""
    return type_token.upper() in _STRING_TYPES


def _coerce_csv_field(value: str, type_token: str) -> str | None:
    """Apply the CSV NULL convention and return the value for pyodbc.

    Parameters
    ----------
    value:
        Raw field value from the CSV reader (never ``None`` — csv.reader
        yields empty strings for missing fields).
    type_token:
        First token of the column's SQL type (e.g. ``NVARCHAR``, ``INT``).

    Returns
    -------
    str | None
        The value to pass to pyodbc's parameterised INSERT.  ``None`` becomes
        a SQL NULL; a string is coerced by pyodbc to the column type.
    """
    if _is_string_type(type_token):
        # String convention: empty field -> ''; \N sentinel -> NULL.
        if value == r"\N":
            return None
        return value  # includes ''
    else:
        # Non-string convention: empty field -> NULL; otherwise pass through.
        return None if value == "" else value


def _truncate_or_delete(conn: pyodbc.Connection, table: str) -> None:
    """``TRUNCATE`` *table*; fall back to ``DELETE FROM`` on FK constraint.

    SQL Server error 4712 is raised when ``TRUNCATE`` is blocked by a
    foreign-key reference from another table.  In that case ``DELETE FROM``
    achieves the same empty-table result (though it is slower and logged
    row-by-row).

    The FK error is detected by the parenthesised native error code
    ``(4712)`` in the exception message — an anchored match that avoids
    false positives from object IDs or table names containing that digit
    sequence.
    """
    import pyodbc as _pyodbc  # local import keeps module importable without pyodbc

    quoted_table = f"[dbo].{_quote(table)}"
    cursor = conn.cursor()
    try:
        cursor.execute(f"TRUNCATE TABLE {quoted_table}")
    except _pyodbc.DatabaseError as exc:
        # SQL Server error 4712: Cannot truncate table … because it is being
        # referenced by a FOREIGN KEY constraint.  Match the parenthesised
        # native code "(4712)" to avoid false positives on object IDs or
        # table names that happen to contain the substring "4712".
        if "(4712)" in str(exc):
            # TODO(story-6): FK DELETE-fallback exercised once the corpus has FK-bearing packages.
            cursor.execute(f"DELETE FROM {quoted_table}")
        else:
            raise
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def provision(conn: pyodbc.Connection, package_dir: Path) -> None:
    """Parse ``schema.sql`` and (re)create all tables in *package_dir*.

    **Idempotent** — a second call on the same database produces the same
    schema without error.  The implementation drops existing tables in
    reverse-declaration order (FK-safe) before recreating them, so bare
    ``CREATE TABLE`` statements in ``schema.sql`` work correctly.

    .. warning::
        Drops and recreates all tables; any existing **row data is lost**.
        Call ``provision`` before ``seed``, never after.  This is by design:
        ``provision`` is called once on a fresh database, followed immediately
        by ``seed``.

    ``GO`` batch separators are stripped before execution; ``pyodbc`` rejects
    any batch that contains a ``GO`` line.

    Parameters
    ----------
    conn:
        An active ``pyodbc.Connection`` to the target database.
    package_dir:
        Path to the corpus package directory containing ``schema.sql``.
    """
    schema_sql = (package_dir / "schema.sql").read_text(encoding="utf-8")
    table_names = _parse_table_names(schema_sql)

    cursor = conn.cursor()

    # Drop in reverse declaration order for FK-safety.
    for name in reversed(table_names):
        _safe_identifier(name)  # defensive check; names come from our own regex
        cursor.execute(f"DROP TABLE IF EXISTS [dbo].{_quote(name)}")
    conn.commit()

    # Execute each GO-delimited batch.
    for batch in _split_batches(schema_sql):
        cursor.execute(batch)
    conn.commit()


def seed(conn: pyodbc.Connection, package_dir: Path) -> None:
    """Load ``seed/src_*.csv`` files into their target tables.

    For each CSV file:

    1. ``TRUNCATE`` (or ``DELETE FROM``) the target table.
    2. Bulk-insert all rows via a parameterised ``executemany`` in column
       order from the CSV header.

    The CSV NULL convention is applied per-column according to the column's
    SQL type as declared in ``schema.sql`` — see the module docstring.

    Parameters
    ----------
    conn:
        An active ``pyodbc.Connection`` to the provisioned database.
    package_dir:
        Path to the corpus package directory containing ``schema.sql`` and
        ``seed/src_*.csv``.
    """
    schema_sql = (package_dir / "schema.sql").read_text(encoding="utf-8")
    col_types = _parse_column_types(schema_sql)

    seed_dir = package_dir / "seed"
    for csv_path in sorted(seed_dir.glob("src_*.csv")):
        table = _safe_identifier(csv_path.stem)  # e.g. src_widgets

        _truncate_or_delete(conn, table)

        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            headers = [_safe_identifier(h) for h in next(reader)]

            table_col_types = col_types.get(table, {})
            placeholders = ", ".join("?" * len(headers))
            col_list = ", ".join(_quote(h) for h in headers)
            insert_sql = (
                f"INSERT INTO [dbo].{_quote(table)} ({col_list})"
                f" VALUES ({placeholders})"
            )

            rows: list[tuple[str | None, ...]] = []
            for raw_row in reader:
                coerced = tuple(
                    _coerce_csv_field(
                        raw_row[i],
                        table_col_types.get(headers[i], ""),
                    )
                    for i in range(len(headers))
                )
                rows.append(coerced)

        if rows:
            cursor = conn.cursor()
            cursor.executemany(insert_sql, rows)
            conn.commit()


def truncate_destinations(conn: pyodbc.Connection, package_dir: Path) -> None:
    """Empty every ``dst_*`` table declared in ``schema.sql``.

    Uses ``TRUNCATE TABLE``; falls back to ``DELETE FROM`` if the table is
    referenced by a foreign key (SQL Server error 4712).

    Does not touch ``src_*`` or ``ref_*`` tables.

    Parameters
    ----------
    conn:
        An active ``pyodbc.Connection`` to the provisioned database.
    package_dir:
        Path to the corpus package directory containing ``schema.sql``.
    """
    schema_sql = (package_dir / "schema.sql").read_text(encoding="utf-8")
    table_names = _parse_table_names(schema_sql)

    for name in table_names:
        if name.startswith("dst_"):
            _safe_identifier(name)  # defensive; names come from our own regex
            _truncate_or_delete(conn, name)


def seed_checksum(package_dir: Path) -> str:
    """Return a SHA-256 hex digest over all ``seed/*.csv`` files.

    Files are sorted by name before concatenation so the digest is stable
    regardless of filesystem enumeration order.  The digest changes whenever
    any seed CSV is added, removed, or modified.

    Parameters
    ----------
    package_dir:
        Path to the corpus package directory containing a ``seed/`` sub-directory.

    Returns
    -------
    str
        A 64-character lowercase hex string (SHA-256).
    """
    hasher = hashlib.sha256()
    seed_dir = package_dir / "seed"
    for csv_path in sorted(seed_dir.glob("*.csv")):
        hasher.update(csv_path.read_bytes())
    return hasher.hexdigest()

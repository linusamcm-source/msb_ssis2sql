"""Static structural checks for the validation framework — Story 7.

Three pure-Python, database-free checks:

1. **Parse-validity** (:func:`check_sql_parse`) — parse converted T-SQL via
   sqlglot (dialect="tsql"); any :class:`sqlglot.errors.ParseError` is a
   hard failure.

2. **Column lineage** (:func:`check_column_lineage`) — for each
   ``INSERT … SELECT`` in the converted SQL, verify that the projected column
   set (SELECT aliases) matches the destination ``dst_*`` DDL declared in
   ``schema.sql``, and that every base-table column referenced in the CTE chain
   belongs to a known source table in the schema.  Failures are PRECISE: each
   gap entry names the column or mismatch, not just "failed".

3. **Completeness matrix** (:func:`check_completeness`) — compare the union of
   component kinds found across all corpus packages against the set returned by
   :func:`must_cover_kinds`.  Every must-be-covered kind with zero corpus
   coverage is an uncovered gap.

None of these functions import ``pyodbc`` or open a database connection — the
static layer is safe to run in any CI environment without an ODBC driver.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
import sqlglot.errors
from sqlglot import exp

if TYPE_CHECKING:
    from ssis2sql.component_types import ComponentKind

# ---------------------------------------------------------------------------
# Regex — CREATE TABLE parser (same pattern as provisioning.py / sql_runner.py)
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE: re.Pattern[str] = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)

_NON_COLUMN_KEYWORDS: frozenset[str] = frozenset(
    {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "CHECK"}
)

_GO_RE: re.Pattern[str] = re.compile(r"^\s*GO\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """Outcome of a :func:`check_sql_parse` call.

    Attributes
    ----------
    ok:
        ``True`` when sqlglot parses the SQL without error.
    error:
        Empty string on success; the sqlglot error message on failure.
    """

    ok: bool
    error: str = ""


@dataclass
class LineageResult:
    """Outcome of a :func:`check_column_lineage` call.

    Attributes
    ----------
    ok:
        ``True`` when every column resolves cleanly.
    gaps:
        Empty list on success; each entry is a human-readable string naming
        an unresolved column or an INSERT-projection-vs-DDL mismatch precisely.
    """

    ok: bool
    gaps: list[str] = field(default_factory=list)


@dataclass
class CompletenessResult:
    """Outcome of a :func:`check_completeness` call.

    Attributes
    ----------
    ok:
        ``True`` when every must-be-covered kind has at least one package in
        the corpus.
    uncovered:
        Empty list on success; each entry is a :attr:`ComponentKind.value`
        string (e.g. ``"lookup"``) that has zero corpus coverage.
    """

    ok: bool
    uncovered: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_sql_parse(sql: str) -> ParseResult:
    """Parse *sql* with sqlglot (dialect="tsql") and return a :class:`ParseResult`.

    Parameters
    ----------
    sql:
        T-SQL text to validate — typically the output of
        ``ssis2sql.convert_file``.

    Returns
    -------
    ParseResult
        ``ParseResult(ok=True, error="")`` on success.
        ``ParseResult(ok=False, error=<message>)`` when sqlglot raises
        :class:`sqlglot.errors.ParseError`.
    """
    try:
        sqlglot.parse(sql, dialect="tsql", error_level=sqlglot.ErrorLevel.RAISE)
        return ParseResult(ok=True, error="")
    except sqlglot.errors.ParseError as exc:
        return ParseResult(ok=False, error=str(exc))


def extract_schema(schema_sql: str) -> dict[str, dict[str, str]]:
    """Parse ``CREATE TABLE`` statements from *schema_sql*.

    Handles ``GO`` batch separators, ``NULL``/``NOT NULL`` modifiers, bracket
    quoting (``[col_name]``), and nested parentheses (e.g. ``DECIMAL(18,4)``).
    Inline constraint entries (``CONSTRAINT``, ``PRIMARY KEY``, ``FOREIGN KEY``,
    ``UNIQUE``, ``INDEX``, ``CHECK``) are skipped.

    Parameters
    ----------
    schema_sql:
        DDL text containing one or more ``CREATE TABLE`` statements.

    Returns
    -------
    dict[str, dict[str, str]]
        ``{table_name: {col_name: sql_type_token}}``.  The type token is the
        first word of the type declaration (e.g. ``"NVARCHAR"`` from
        ``NVARCHAR(100) NOT NULL``), uppercased.
    """
    result: dict[str, dict[str, str]] = {}
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

        # Accumulate lines until the matching closing ")" tracking paren depth.
        block_lines: list[str] = [line]
        depth = line.count("(") - line.count(")")
        i += 1
        while i < len(lines) and depth > 0:
            block_lines.append(lines[i])
            depth += lines[i].count("(") - lines[i].count(")")
            i += 1

        block = " ".join(block_lines)

        # Extract body between outermost parens.
        open_idx = block.index("(")
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
            continue

        body = block[open_idx + 1 : close_idx]

        # Split body on top-level commas.
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
            # Strip leading/trailing whitespace and remove inline comments.
            entry = re.sub(r"--.*$", "", entry).strip()
            if not entry:
                continue
            tokens = re.split(r"\s+", entry.strip().lstrip("[").lstrip())
            if len(tokens) < 2:
                continue
            first_token = tokens[0].strip("[]").upper()
            if first_token in _NON_COLUMN_KEYWORDS:
                continue
            col_name = tokens[0].strip("[]")
            type_token = tokens[1].strip("[]")
            type_match = re.match(r"[A-Za-z]+", type_token)
            if type_match:
                result[table_name][col_name] = type_match.group(0).upper()

    return result


def check_column_lineage(
    sql: str,
    schema: dict[str, dict[str, str]],
) -> LineageResult:
    """Check that each INSERT's projected columns match the dst_* DDL.

    For every ``INSERT … SELECT`` statement in *sql*:

    1. Extract the projected column names from the SELECT expression aliases.
    2. Look up the destination table in *schema*; compare the projected set
       against the DDL column set.  Any column in the projection but absent
       from the DDL (or vice-versa) is a gap named precisely.
    3. Walk the CTE chain and collect every base-table column reference
       (columns whose table qualifier is not a CTE alias) and verify each
       against *schema*:

       - **Qualified references** (``tbl.col``): the column must appear in
         the named table's schema entry.  If the table itself is absent from
         *schema*, a gap is emitted naming the unknown table.
       - **Unqualified references** (bare ``col``): validated against the
         *global* union of all source-table columns in *schema* — NOT
         per-CTE FROM-scope.  This means a bare column that belongs to a
         different source table than the one in the CTE's ``FROM`` is **not**
         flagged; per-CTE scope resolution is out of scope for this static
         pre-filter (Story 8's differential validation is the correctness
         gate).  True invented columns are still caught.

    Parameters
    ----------
    sql:
        Converted T-SQL (output of ``ssis2sql.convert_file``).
    schema:
        Mapping ``{table_name: {col_name: sql_type_token}}`` from
        :func:`extract_schema`.

    Returns
    -------
    LineageResult
        ``ok=True, gaps=[]`` when all columns resolve cleanly.
        ``ok=False, gaps=[...]`` with each entry naming a precise mismatch.
    """
    try:
        stmts = sqlglot.parse(sql, dialect="tsql", error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.ParseError as exc:
        return LineageResult(ok=False, gaps=[f"SQL parse error: {exc}"])

    gaps: list[str] = []
    schema_lower = {t.lower(): {c.lower(): v for c, v in cols.items()} for t, cols in schema.items()}

    # Build the set of all known columns across all source tables (non-dst).
    all_src_cols: set[str] = set()
    for table, cols in schema_lower.items():
        all_src_cols.update(cols.keys())

    for stmt in stmts:
        if not isinstance(stmt, exp.Insert):
            continue

        # --- Target table ---
        schema_node = stmt.args.get("this")
        if schema_node is None:
            continue
        target_table_node = schema_node.find(exp.Table)
        if target_table_node is None:
            continue
        target_table = target_table_node.name.lower()

        # --- Projected columns from SELECT aliases ---
        sel = stmt.args.get("expression")
        projected: list[str] = []
        if isinstance(sel, exp.Select):
            for expr in sel.expressions:
                alias = expr.alias if hasattr(expr, "alias") and expr.alias else None
                if alias:
                    projected.append(alias.lower())
                elif isinstance(expr, exp.Column):
                    projected.append(expr.name.lower())
                elif isinstance(expr, exp.Star):
                    # SELECT * — skip explicit check; can't enumerate without DB
                    projected = []
                    break

        # --- Compare projected columns against dst_* DDL ---
        if projected:
            dst_cols = set(schema_lower.get(target_table, {}).keys())
            proj_set = set(projected)
            surplus = proj_set - dst_cols
            missing = dst_cols - proj_set
            for col in sorted(surplus):
                gaps.append(
                    f"INSERT into {target_table!r} projects column {col!r}"
                    f" not declared in schema DDL"
                )
            for col in sorted(missing):
                gaps.append(
                    f"INSERT into {target_table!r} omits column {col!r}"
                    f" declared in schema DDL"
                )

        # --- CTE base-column resolution ---
        with_ = stmt.args.get("with_")
        if with_:
            cte_aliases: set[str] = {cte.alias.upper() for cte in with_.expressions}
            for cte in with_.expressions:
                # Collect FROM/JOIN table aliases used locally in this CTE body
                # (e.g. "FROM [Source] AS L" → alias "L") so that qualified
                # references like L.col are not mistaken for schema-table refs.
                local_aliases: set[str] = {
                    tbl_node.alias.upper()
                    for tbl_node in cte.this.find_all(exp.Table)
                    if tbl_node.alias
                }
                skip_qualifiers: set[str] = cte_aliases | local_aliases
                for col_node in cte.this.find_all(exp.Column):
                    tbl = (col_node.table or "").upper()
                    col_name = col_node.name.lower()
                    # Only check base-table refs (not CTE-to-CTE or alias refs).
                    if tbl and tbl not in skip_qualifiers:
                        tbl_lower = tbl.lower()
                        tbl_cols = schema_lower.get(tbl_lower, {})
                        if not tbl_cols:
                            gaps.append(
                                f"CTE {cte.alias!r} references unknown table {tbl_lower!r}"
                            )
                        elif col_name not in tbl_cols:
                            gaps.append(
                                f"Column {col_name!r} not found in table"
                                f" {tbl_lower!r} (schema has: {sorted(tbl_cols.keys())})"
                            )
                    elif not tbl and col_name not in all_src_cols:
                        gaps.append(
                            f"Column {col_name!r} referenced in CTE"
                            f" {cte.alias!r} is not found in any schema table"
                        )

    return LineageResult(ok=len(gaps) == 0, gaps=gaps)


def must_cover_kinds() -> list[ComponentKind]:
    """Return the list of :class:`~ssis2sql.component_types.ComponentKind` values
    that the corpus must cover.

    Reads ``ssis2sql.transforms.registry._REGISTRY`` live (not a repomix
    snapshot) and returns every kind that:

    - has a dedicated (non-:class:`~ssis2sql.transforms.flow.PassThroughFallbackTranspiler`)
      transpiler, **and**
    - is not a ``FLATFILE_*`` kind (the ODBC corpus exercises ODBC sources only).

    Returns
    -------
    list[ComponentKind]
        Sorted by :attr:`~ssis2sql.component_types.ComponentKind.value` for
        deterministic test output.  Contains exactly 15 kinds as of Story 7
        authorship.
    """
    from ssis2sql.transforms.flow import PassThroughFallbackTranspiler
    from ssis2sql.transforms.registry import _REGISTRY

    kinds = []
    for kind, cls in _REGISTRY.items():
        if issubclass(cls, PassThroughFallbackTranspiler):
            continue
        if "flatfile" in kind.value.lower():
            continue
        kinds.append(kind)
    return sorted(kinds, key=lambda k: k.value)


def check_completeness(
    corpus_coverage: dict[str, set[ComponentKind]],
) -> CompletenessResult:
    """Check that the corpus covers all must-be-covered component kinds.

    Parameters
    ----------
    corpus_coverage:
        Mapping of ``{package_name: set(ComponentKind)}`` — the component
        kinds found in each corpus package's ``package.dtsx``.

    Returns
    -------
    CompletenessResult
        ``ok=True, uncovered=[]`` when every must-be-covered kind appears in
        at least one package.
        ``ok=False, uncovered=[...]`` with each entry being a
        :attr:`ComponentKind.value` string of a kind with zero coverage.
    """
    required = set(must_cover_kinds())
    covered: set[ComponentKind] = set()
    for kinds in corpus_coverage.values():
        covered.update(kinds)

    uncovered = sorted(
        (k.value for k in required if k not in covered),
    )
    return CompletenessResult(ok=len(uncovered) == 0, uncovered=uncovered)

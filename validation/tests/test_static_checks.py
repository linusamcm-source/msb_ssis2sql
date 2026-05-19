"""Tests for validation.static_checks — the static structural layer.

RED phase: all tests import from ``validation.static_checks`` which does not
yet exist.  Every test therefore fails with ``ImportError`` on collection.
That is the correct TDD RED state before the engineer authors the module in
GREEN.

What is tested (grouped by AC):

AC1 — SQL parse-validity
    ``test_parse_valid_passthrough_basic``
        The converted SQL for ``passthrough_basic`` parses without error under
        sqlglot (dialect="tsql").
    ``test_parse_result_is_structured``
        ``check_sql_parse`` returns a typed dataclass (``ParseResult``) with
        ``ok`` and ``error`` fields — not a bare bool.
    ``test_parse_invalid_sql_returns_error``
        A deliberately broken SQL string returns ``ParseResult(ok=False)``
        with a non-empty ``error`` string.
    ``test_parse_valid_sql_error_is_empty``
        A valid SQL string returns ``ParseResult(ok=True, error='')``.

AC2 — Column lineage
    ``test_lineage_passes_for_passthrough_basic``
        The lineage check passes for ``passthrough_basic`` — every SELECT
        column in the generated CTE chain resolves to a real base table
        column declared in schema.sql.
    ``test_lineage_result_is_structured``
        ``check_column_lineage`` returns a typed dataclass (``LineageResult``)
        with ``ok`` and ``gaps`` fields — ``gaps`` is a list of strings
        naming unresolved column/mismatch descriptions.
    ``test_lineage_broken_case_names_the_gap``
        Fed a schema map that OMITS a column the converted SQL references,
        the lineage check returns ``LineageResult(ok=False)`` with
        ``gaps`` containing the name of the missing column.  The AC2 key
        assertion: failure is PRECISE, not generic.
    ``test_lineage_insert_projection_mismatch_named``
        When the schema DDL declares a column the INSERT projection omits,
        the result names that column with "omits ... declared in schema DDL".
    ``test_lineage_parse_error_returns_gap``
        Broken SQL fed to check_column_lineage returns ok=False with a gap
        starting "SQL parse error".
    ``test_lineage_cte_qualified_column_missing_in_table``
        A CTE referencing ``tbl.col`` where ``col`` is absent from ``tbl``'s
        schema entry produces a gap naming both the column and the table.
    ``test_lineage_cte_unknown_table_emits_gap``
        A CTE referencing a table qualifier absent from the schema produces a
        gap naming the unknown table (not silently passing).
    ``test_lineage_select_star_skips_projection_check``
        ``SELECT *`` in an INSERT causes the projection check to be skipped;
        no spurious gap is emitted.

AC3 — Completeness matrix
    ``test_completeness_all_15_kinds_covered``
        With the full 8-package corpus, ``check_completeness`` reports all 15
        must-be-covered kinds as covered.
    ``test_completeness_result_is_structured``
        ``check_completeness`` returns a typed dataclass (``CompletenessResult``)
        with ``ok`` and ``uncovered`` fields — ``uncovered`` is a list of
        kind-value strings.
    ``test_completeness_missing_kind_fails_loudly``
        Given a corpus-coverage map that DROPS one must-be-covered kind,
        the result is ``ok=False`` and ``uncovered`` contains exactly that
        kind's value string.  The AC3 key test: failure names the gap.
    ``test_completeness_must_cover_excludes_passthrough_fallback``
        ``must_cover_kinds()`` does not include any kind whose transpiler is
        ``PassThroughFallbackTranspiler``.
    ``test_completeness_must_cover_excludes_flatfile_kinds``
        ``must_cover_kinds()`` does not include ``flatfile_source`` or
        ``flatfile_destination``.
    ``test_completeness_must_cover_count``
        ``must_cover_kinds()`` returns exactly 15 items (the live registry
        count as of Story 7 authorship).

AC4 — No DB required
    ``test_static_checks_has_no_pyodbc_import``
        ``validation.static_checks`` must not import ``pyodbc`` at module level
        — it is a purely static layer.  Verified via ``sys.modules`` check.

Schema extraction helper
    ``test_extract_schema_returns_column_type_map``
        ``extract_schema(schema_sql)`` returns ``{table: {col: type_token}}``
        from a ``CREATE TABLE`` DDL string.
    ``test_extract_schema_passthrough_basic``
        Applied to ``passthrough_basic/schema.sql``, the result contains
        ``src_items`` and ``dst_items`` tables with the declared columns.

API contract pinned by these tests
-----------------------------------
``ParseResult`` — dataclass
    ok: bool          — True when sqlglot parses without error
    error: str        — empty when ok=True; sqlglot error message when ok=False

``LineageResult`` — dataclass
    ok: bool          — True when every column resolves cleanly
    gaps: list[str]   — empty when ok=True; each entry names an unresolved
                        column or INSERT-projection-vs-DDL mismatch precisely

``CompletenessResult`` — dataclass
    ok: bool          — True when every must-be-covered kind has corpus coverage
    uncovered: list[str]  — empty when ok=True; each entry is a kind.value
                            string (e.g. "lookup") that has zero coverage

``check_sql_parse(sql: str) -> ParseResult``
    Parse *sql* with sqlglot (dialect="tsql", error_level=RAISE-catching).
    Returns ParseResult(ok=True) on success or ParseResult(ok=False, error=msg)
    on ParseError.

``check_column_lineage(
    sql: str,
    schema: dict[str, dict[str, str]],
) -> LineageResult``
    Resolve every column reference in *sql* (a converted CTE-style INSERT)
    against *schema* (table → {col → type_token}).  Returns LineageResult.
    On failure: gaps contains a precise human-readable description of each
    unresolved column or projection/DDL mismatch.

``extract_schema(schema_sql: str) -> dict[str, dict[str, str]]``
    Parse ``CREATE TABLE`` statements from *schema_sql*.
    Returns ``{table_name: {col_name: sql_type_token}}``.

``must_cover_kinds() -> list[ComponentKind]``
    Read ``ssis2sql.transforms.registry._REGISTRY`` and return every kind that:
    - has a dedicated (non-``PassThroughFallbackTranspiler``) transpiler, AND
    - is not a ``FLATFILE_*`` kind.

``check_completeness(
    corpus_coverage: dict[str, set[ComponentKind]],
) -> CompletenessResult``
    *corpus_coverage*: mapping of package-name → set of ComponentKind values
    found in that package's package.dtsx.
    Compares the union of all covered kinds against ``must_cover_kinds()``.
    Returns CompletenessResult(ok=True) when every must-be-covered kind is
    present in at least one package; ok=False with uncovered=[...] otherwise.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from validation.static_checks import (
    CompletenessResult,
    LineageResult,
    ParseResult,
    check_column_lineage,
    check_completeness,
    check_sql_parse,
    extract_schema,
    must_cover_kinds,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parents[2] / "validation" / "corpus"
_PASSTHROUGH_DIR: Path = _CORPUS_ROOT / "passthrough_basic"


# ---------------------------------------------------------------------------
# Helpers — build corpus coverage from live corpus
# ---------------------------------------------------------------------------


def _build_corpus_coverage() -> dict[str, set]:
    """Return {pkg_name: set(ComponentKind)} from all 8 corpus packages."""
    import xml.etree.ElementTree as ET

    from ssis2sql.component_types import resolve

    coverage: dict[str, set] = {}
    for pkg_dir in sorted(_CORPUS_ROOT.iterdir()):
        dtsx = pkg_dir / "package.dtsx"
        if not dtsx.is_file():
            continue
        tree = ET.parse(dtsx)
        kinds: set = set()
        for comp in tree.getroot().iter("component"):
            cid = comp.get("componentClassID", "")
            if cid:
                kinds.add(resolve(cid))
        coverage[pkg_dir.name] = kinds
    return coverage


def _converted_sql(pkg_dir: Path) -> str:
    """Return the converted T-SQL for a corpus package (no-header, no proc)."""
    from ssis2sql import ConvertOptions, convert_file

    result = convert_file(
        pkg_dir / "package.dtsx",
        ConvertOptions(wrap_in_procedure=False, include_header=False),
    )
    return result.sql


# ---------------------------------------------------------------------------
# AC1 — SQL parse-validity
# ---------------------------------------------------------------------------


def test_parse_result_is_structured() -> None:
    """check_sql_parse returns a ParseResult dataclass with ok and error fields.

    AC1 — result must be a typed structured value, not a bare bool.
    SERVER-FREE.
    """
    result = check_sql_parse("SELECT 1;")
    assert isinstance(result, ParseResult)
    assert hasattr(result, "ok")
    assert hasattr(result, "error")


def test_parse_valid_sql_error_is_empty() -> None:
    """A valid SQL statement produces ParseResult(ok=True, error='').

    AC1 — SERVER-FREE.
    """
    result = check_sql_parse("SELECT 1;")
    assert result.ok is True
    assert result.error == ""


def test_parse_invalid_sql_returns_error() -> None:
    """Malformed SQL produces ParseResult(ok=False) with a non-empty error string.

    AC1 — the error field must contain the sqlglot parse error message so the
    engineer sees exactly what is wrong.  SERVER-FREE.
    """
    result = check_sql_parse("SELECT $$$ BROKEN SYNTAX $$$")
    assert result.ok is False
    assert result.error, "error must be non-empty when SQL fails to parse"


def test_parse_valid_passthrough_basic() -> None:
    """The converted SQL for passthrough_basic parses as valid T-SQL via sqlglot.

    AC1 — real corpus package, no database.  Validates that the transpiler
    produces syntactically correct T-SQL that sqlglot can parse.
    """
    sql = _converted_sql(_PASSTHROUGH_DIR)
    result = check_sql_parse(sql)
    assert result.ok is True, (
        f"passthrough_basic SQL failed sqlglot parse: {result.error}"
    )


@pytest.mark.parametrize(
    "pkg_name",
    [p.name for p in sorted(_CORPUS_ROOT.iterdir()) if (p / "package.dtsx").is_file()],
)
def test_parse_valid_all_corpus_packages(pkg_name: str) -> None:
    """Every corpus package's converted SQL parses as valid T-SQL.

    AC1 — parametrised over all 8 corpus packages.  No database required.
    """
    pkg_dir = _CORPUS_ROOT / pkg_name
    sql = _converted_sql(pkg_dir)
    result = check_sql_parse(sql)
    assert result.ok is True, (
        f"Package {pkg_name!r} SQL failed sqlglot parse: {result.error}"
    )


# ---------------------------------------------------------------------------
# AC2 — Column lineage
# ---------------------------------------------------------------------------


def test_lineage_result_is_structured() -> None:
    """check_column_lineage returns a LineageResult with ok and gaps fields.

    AC2 — structured result, SERVER-FREE.
    """
    schema_sql = (
        "CREATE TABLE dbo.src_items (id int NOT NULL, name nvarchar(50) NOT NULL);\n"
        "CREATE TABLE dbo.dst_items (id int NOT NULL, name nvarchar(50) NOT NULL);\n"
    )
    schema = extract_schema(schema_sql)
    sql = "INSERT INTO dbo.dst_items SELECT id, name FROM dbo.src_items;"
    result = check_column_lineage(sql, schema)
    assert isinstance(result, LineageResult)
    assert hasattr(result, "ok")
    assert hasattr(result, "gaps")
    assert isinstance(result.gaps, list)


def test_lineage_passes_for_passthrough_basic() -> None:
    """Column lineage check passes for passthrough_basic.

    AC2 — every column in the generated INSERT resolves to a declared column
    in schema.sql.  No database.
    """
    sql = _converted_sql(_PASSTHROUGH_DIR)
    schema_sql = (_PASSTHROUGH_DIR / "schema.sql").read_text(encoding="utf-8")
    schema = extract_schema(schema_sql)
    result = check_column_lineage(sql, schema)
    assert result.ok is True, (
        f"Lineage check failed for passthrough_basic — gaps: {result.gaps}"
    )


def test_lineage_broken_case_names_the_gap() -> None:
    """Lineage check on a schema missing a referenced column names that column precisely.

    AC2 key test — failure must be PRECISE, not generic.  Fed a schema that
    declares dst_items WITHOUT the 'amount' column, the result must name
    'amount' (or describe the mismatch containing 'amount') in its gaps list.
    SERVER-FREE.
    """
    # Schema intentionally omits 'amount' from dst_items.
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_items (
            id      int            NOT NULL,
            name    nvarchar(100)  NOT NULL,
            amount  decimal(18,4)  NOT NULL
        );
        CREATE TABLE dbo.dst_items (
            id      int            NOT NULL,
            name    nvarchar(100)  NOT NULL
            -- amount intentionally missing
        );
    """)
    schema = extract_schema(schema_sql)
    # Use a real corpus SQL that references amount so the gap is real.
    sql = _converted_sql(_PASSTHROUGH_DIR)
    result = check_column_lineage(sql, schema)
    assert result.ok is False, (
        "Expected lineage check to FAIL when dst_items is missing 'amount', "
        "but it returned ok=True"
    )
    gap_text = " ".join(result.gaps).lower()
    assert "amount" in gap_text, (
        f"Lineage failure must name the missing column 'amount'. "
        f"gaps: {result.gaps}"
    )


def test_lineage_valid_sql_has_empty_gaps() -> None:
    """A passing lineage check has an empty gaps list.

    AC2 — gaps must be [] on success (not None, not a list with empty strings).
    SERVER-FREE using passthrough_basic.
    """
    sql = _converted_sql(_PASSTHROUGH_DIR)
    schema_sql = (_PASSTHROUGH_DIR / "schema.sql").read_text(encoding="utf-8")
    schema = extract_schema(schema_sql)
    result = check_column_lineage(sql, schema)
    if result.ok:
        assert result.gaps == [], f"Passing result must have empty gaps, got: {result.gaps}"


def test_lineage_insert_projection_mismatch_named() -> None:
    """Lineage check names the column when dst DDL declares more than the INSERT projects.

    AC2 — "omits" branch (static_checks.py ~line 322): when the schema DDL
    declares a column that the INSERT projection does NOT include, the result is
    ok=False and gaps names that column with "omits ... declared in schema DDL".
    SERVER-FREE.
    """
    # dst_items DDL declares 'extra_col'; INSERT only projects id, name.
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_items (
            id        int           NOT NULL,
            name      nvarchar(100) NOT NULL
        );
        CREATE TABLE dbo.dst_items (
            id        int           NOT NULL,
            name      nvarchar(100) NOT NULL,
            extra_col int           NOT NULL
        );
    """)
    schema = extract_schema(schema_sql)
    # INSERT projects only id, name — extra_col is absent from the projection.
    sql = textwrap.dedent("""\
        WITH src AS (
            SELECT id, name FROM dbo.src_items
        )
        INSERT INTO dbo.dst_items
        SELECT id AS id, name AS name FROM src;
    """)
    result = check_column_lineage(sql, schema)
    assert result.ok is False, (
        "Expected lineage check to FAIL when dst_items DDL declares 'extra_col' "
        "but INSERT projection omits it"
    )
    gap_text = " ".join(result.gaps).lower()
    assert "extra_col" in gap_text, (
        f"gaps must name the omitted column 'extra_col'. gaps: {result.gaps}"
    )
    assert "omits" in gap_text, (
        f"gap message must use 'omits ... declared in schema DDL' wording. gaps: {result.gaps}"
    )


def test_lineage_parse_error_returns_gap() -> None:
    """check_column_lineage on broken SQL returns ok=False with a 'SQL parse error' gap.

    AC2 — parse-error path (~static_checks.py lines 271-272).  Verifies that
    sqlglot parse failures surface as a lineage gap rather than an unhandled
    exception.  SERVER-FREE.
    """
    result = check_column_lineage("SELECT $$$ BROKEN SYNTAX $$$", {})
    assert result.ok is False, "Broken SQL must produce ok=False"
    assert result.gaps, "Broken SQL must produce at least one gap entry"
    assert result.gaps[0].startswith("SQL parse error"), (
        f"First gap must start with 'SQL parse error'. Got: {result.gaps[0]!r}"
    )


def test_lineage_cte_qualified_column_missing_in_table() -> None:
    """Lineage check names a qualified CTE column that does not exist in the named table.

    AC2 — qualified-gap branch (~static_checks.py lines 337-343): when a CTE
    references 'tbl.col' where 'tbl' is in the schema but 'col' is not, the
    result names both the column and the table.  SERVER-FREE.
    """
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_x (
            id   int           NOT NULL,
            code nvarchar(10)  NOT NULL
        );
        CREATE TABLE dbo.dst_x (
            id   int           NOT NULL
        );
    """)
    schema = extract_schema(schema_sql)
    # CTE references dbo.src_x.nonexistent_col — not in src_x schema.
    sql = textwrap.dedent("""\
        WITH cte AS (
            SELECT src_x.nonexistent_col AS id FROM dbo.src_x
        )
        INSERT INTO dbo.dst_x
        SELECT id AS id FROM cte;
    """)
    result = check_column_lineage(sql, schema)
    assert result.ok is False, (
        "Expected ok=False when CTE references a column absent from the named table"
    )
    gap_text = " ".join(result.gaps).lower()
    assert "nonexistent_col" in gap_text, (
        f"gaps must name the missing column 'nonexistent_col'. gaps: {result.gaps}"
    )
    assert "src_x" in gap_text, (
        f"gaps must name the table 'src_x'. gaps: {result.gaps}"
    )


def test_lineage_cte_unknown_table_emits_gap() -> None:
    """Lineage check emits a gap when a CTE qualifies a column with an unknown table.

    FIX-5 — when a table qualifier is set, is not a CTE alias, and is absent
    from the schema entirely, the check must emit a gap naming the unknown
    table (not silently pass).  SERVER-FREE.
    """
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_known (
            id int NOT NULL
        );
        CREATE TABLE dbo.dst_known (
            id int NOT NULL
        );
    """)
    schema = extract_schema(schema_sql)
    # CTE references 'ghost_table.id' — ghost_table is not in schema.
    sql = textwrap.dedent("""\
        WITH cte AS (
            SELECT ghost_table.id AS id FROM dbo.src_known
        )
        INSERT INTO dbo.dst_known
        SELECT id AS id FROM cte;
    """)
    result = check_column_lineage(sql, schema)
    assert result.ok is False, (
        "Expected ok=False when CTE references a table not in the schema"
    )
    gap_text = " ".join(result.gaps).lower()
    assert "ghost_table" in gap_text, (
        f"gaps must name the unknown table 'ghost_table'. gaps: {result.gaps}"
    )


def test_lineage_select_star_skips_projection_check() -> None:
    """SELECT * in an INSERT skips the projection-vs-DDL column check.

    FIX-3 — the exp.Star handler (~static_checks.py lines 305-308) documents
    that enumeration requires a DB; the lineage check must not flag SELECT *
    as a gap.  The result must be ok=True (no spurious gaps from column count
    mismatch).  SERVER-FREE.
    """
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_items (
            id   int NOT NULL,
            name nvarchar(50) NOT NULL
        );
        CREATE TABLE dbo.dst_items (
            id   int NOT NULL
        );
    """)
    schema = extract_schema(schema_sql)
    # SELECT * — projection cannot be enumerated statically.
    sql = "INSERT INTO dbo.dst_items SELECT * FROM dbo.src_items;"
    result = check_column_lineage(sql, schema)
    assert result.ok is True, (
        f"SELECT * must not produce spurious projection gaps. gaps: {result.gaps}"
    )


# ---------------------------------------------------------------------------
# Schema extraction helper
# ---------------------------------------------------------------------------


def test_extract_schema_returns_column_type_map() -> None:
    """extract_schema returns {table: {col: type_token}} from a DDL string.

    SERVER-FREE.
    """
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.src_widgets (
            id      INT            NOT NULL,
            name    NVARCHAR(50)   NOT NULL,
            price   DECIMAL(18,4)  NOT NULL
        );
        GO
        CREATE TABLE dbo.dst_widgets (
            id      INT            NOT NULL,
            name    NVARCHAR(50)   NOT NULL
        );
        GO
    """)
    result = extract_schema(schema_sql)
    assert "src_widgets" in result
    assert "dst_widgets" in result
    assert result["src_widgets"]["id"].upper() in {"INT", "INTEGER"}
    assert result["src_widgets"]["name"].upper().startswith("NVARCHAR")
    assert result["src_widgets"]["price"].upper().startswith("DECIMAL")
    assert "id" in result["dst_widgets"]
    assert "name" in result["dst_widgets"]


def test_extract_schema_passthrough_basic() -> None:
    """extract_schema on passthrough_basic/schema.sql returns correct tables/columns.

    SERVER-FREE — reads the committed corpus file.
    """
    schema_sql = (_PASSTHROUGH_DIR / "schema.sql").read_text(encoding="utf-8")
    result = extract_schema(schema_sql)
    assert "src_items" in result, "src_items not found in schema"
    assert "dst_items" in result, "dst_items not found in schema"
    for expected_col in ("id", "name", "amount", "active", "loaded_at"):
        assert expected_col in result["src_items"], (
            f"Column {expected_col!r} missing from src_items schema"
        )
        assert expected_col in result["dst_items"], (
            f"Column {expected_col!r} missing from dst_items schema"
        )


def test_extract_schema_handles_nullable_and_not_null() -> None:
    """extract_schema correctly extracts columns regardless of NULL / NOT NULL.

    SERVER-FREE.
    """
    schema_sql = textwrap.dedent("""\
        CREATE TABLE dbo.mixed (
            a   INT         NOT NULL,
            b   NVARCHAR(10) NULL,
            c   DATETIME2   NULL
        );
    """)
    result = extract_schema(schema_sql)
    assert set(result["mixed"].keys()) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# AC3 — Completeness matrix
# ---------------------------------------------------------------------------


def test_completeness_result_is_structured() -> None:
    """check_completeness returns a CompletenessResult with ok and uncovered fields.

    AC3 — structured result.  SERVER-FREE.
    """
    result = check_completeness({})
    assert isinstance(result, CompletenessResult)
    assert hasattr(result, "ok")
    assert hasattr(result, "uncovered")
    assert isinstance(result.uncovered, list)


def test_completeness_must_cover_count() -> None:
    """must_cover_kinds() returns exactly 15 kinds (live registry, Story 7 authorship).

    AC3 — pins the expected count so any registry addition/removal is detected.
    SERVER-FREE.
    """
    kinds = must_cover_kinds()
    assert len(kinds) == 15, (
        f"Expected 15 must-cover kinds, got {len(kinds)}: {[k.value for k in kinds]}"
    )


def test_completeness_must_cover_excludes_passthrough_fallback() -> None:
    """must_cover_kinds() excludes kinds handled by PassThroughFallbackTranspiler.

    AC3 — the must-cover set is limited to kinds with DEDICATED transpilers.
    PassThroughFallback-handled kinds (character_map, pivot, unpivot, script,
    scd, oledb_command, unknown) must not appear.  SERVER-FREE.
    """
    from ssis2sql.component_types import ComponentKind

    passthrough_handled = frozenset({
        ComponentKind.CHARACTER_MAP,
        ComponentKind.PIVOT,
        ComponentKind.UNPIVOT,
        ComponentKind.SCRIPT,
        ComponentKind.SCD,
        ComponentKind.OLEDB_COMMAND,
        ComponentKind.UNKNOWN,
    })
    kinds = must_cover_kinds()
    overlap = passthrough_handled & set(kinds)
    assert not overlap, (
        f"must_cover_kinds() must not include PassThroughFallback-handled kinds. "
        f"Found: {[k.value for k in overlap]}"
    )


def test_completeness_must_cover_excludes_flatfile_kinds() -> None:
    """must_cover_kinds() excludes flatfile_source and flatfile_destination.

    AC3 — the ODBC corpus uses ODBC sources only; FLATFILE kinds are not
    exercisable by design.  SERVER-FREE.
    """
    from ssis2sql.component_types import ComponentKind

    kinds = must_cover_kinds()
    assert ComponentKind.FLATFILE_SOURCE not in kinds, (
        "flatfile_source must not be in must_cover_kinds()"
    )
    assert ComponentKind.FLATFILE_DESTINATION not in kinds, (
        "flatfile_destination must not be in must_cover_kinds()"
    )


def test_completeness_all_15_kinds_covered() -> None:
    """With the full 8-package corpus all 15 must-be-covered kinds are covered.

    AC3 — the full corpus must satisfy the completeness matrix.  SERVER-FREE
    (reads package.dtsx files from disk; no database).
    """
    coverage = _build_corpus_coverage()
    result = check_completeness(coverage)
    assert result.ok is True, (
        f"Completeness check failed — uncovered kinds: {result.uncovered}"
    )
    assert result.uncovered == [], (
        f"Expected no uncovered kinds, got: {result.uncovered}"
    )


def test_completeness_missing_kind_fails_loudly() -> None:
    """Given a corpus-coverage map missing one kind, check_completeness names it.

    AC3 key test — failure must be PRECISE: uncovered contains exactly the
    missing kind's value string.  SERVER-FREE.
    """
    from ssis2sql.component_types import ComponentKind

    # Build full coverage then remove 'lookup' from every package.
    coverage = _build_corpus_coverage()
    coverage_without_lookup = {
        pkg: kinds - {ComponentKind.LOOKUP}
        for pkg, kinds in coverage.items()
    }
    result = check_completeness(coverage_without_lookup)
    assert result.ok is False, (
        "check_completeness must return ok=False when 'lookup' is absent from all packages"
    )
    assert "lookup" in result.uncovered, (
        f"uncovered must contain 'lookup'. Got: {result.uncovered}"
    )


def test_completeness_missing_multiple_kinds_names_all() -> None:
    """When multiple kinds are absent, all are named in uncovered.

    AC3 — uncovered is a complete list, not just the first gap found.
    SERVER-FREE.
    """
    from ssis2sql.component_types import ComponentKind

    coverage = _build_corpus_coverage()
    dropped = {ComponentKind.LOOKUP, ComponentKind.AGGREGATE}
    coverage_reduced = {
        pkg: kinds - dropped for pkg, kinds in coverage.items()
    }
    result = check_completeness(coverage_reduced)
    assert result.ok is False
    for kind in dropped:
        assert kind.value in result.uncovered, (
            f"uncovered must contain {kind.value!r}. Got: {result.uncovered}"
        )


def test_completeness_empty_corpus_fails_loudly() -> None:
    """An empty coverage dict fails with all 15 must-cover kinds listed as uncovered.

    AC3 — edge case: zero packages → zero coverage → all 15 uncovered.
    SERVER-FREE.
    """
    result = check_completeness({})
    assert result.ok is False
    assert len(result.uncovered) == 15, (
        f"Empty corpus must produce 15 uncovered kinds, got {len(result.uncovered)}: "
        f"{result.uncovered}"
    )


# ---------------------------------------------------------------------------
# AC4 — No DB required
# ---------------------------------------------------------------------------


def test_static_checks_has_no_pyodbc_import() -> None:
    """validation.static_checks must not import pyodbc at module level.

    AC4 — static_checks is a pure analysis layer; it must be importable on a
    machine with no ODBC driver installed.  Verified by checking sys.modules
    BEFORE importing validation.static_checks, then confirming pyodbc is not
    pulled in as a side-effect.  SERVER-FREE.
    """
    import sys

    # If pyodbc is already in sys.modules from another test, note its presence.
    pyodbc_was_present = "pyodbc" in sys.modules

    import validation.static_checks  # noqa: F401

    pyodbc_now = "pyodbc" in sys.modules
    # If pyodbc was absent before and is present now, static_checks imported it.
    if not pyodbc_was_present and pyodbc_now:
        pytest.fail(
            "validation.static_checks imported pyodbc at module level. "
            "Static checks must not depend on a database connection."
        )

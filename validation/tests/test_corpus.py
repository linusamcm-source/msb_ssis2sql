"""Tests for the Story 6 ODBC validation corpus — RED phase.

``validation/corpus/`` does not exist yet; ``test_corpus_has_eight_packages``
fails immediately and the parametrized suite collects nothing.  Both are the
correct TDD RED state before the engineer authors the corpus in GREEN.

What is tested (all non-live — pure file/parse/transpile checks, no SQL Server):

``test_corpus_has_eight_packages``
    Asserts the eight expected package directories exist under
    ``validation/corpus/``.  Fails now (RED) because the directory is absent.

Per discovered package (parametrized over ``validation/corpus/*/``):

``test_package_has_required_files``
    ``package.dtsx``, ``schema.sql``, at least one ``seed/src_*.csv``,
    ``ledger.yaml``, and a ``golden/`` directory must all be present.

``test_package_parses``
    ``ssis2sql.convert_file`` parses ``package.dtsx`` without an unhandled
    exception.  A ``ConversionResult`` is returned (the SQL text may be empty
    for a skeletal package, but no exception is raised).

``test_package_transpiles``
    ``ssis2sql.convert_file`` with ``wrap_in_procedure=False`` and
    ``include_header=False`` returns a non-empty SQL string.

``test_ledger_consistent_with_schema``
    ``validation.ledger.parse_ledger`` parses ``ledger.yaml`` and, for each
    destination declared in the ledger, every column name the ledger references
    is present in that ``dst_*`` table's ``CREATE TABLE`` declaration inside
    ``schema.sql``.

Schema column extraction uses the same ``_CREATE_TABLE_RE`` / body-parsing
logic as ``validation.provisioning`` (the two modules share column-type
semantics), re-implemented here as a local helper so this test file has no
import dependency on provisioning internals.

API contract the engineer must satisfy
---------------------------------------
- ``validation/corpus/<name>/package.dtsx`` — a valid ``.dtsx`` accepted by
  ``ssis2sql.convert_file``.
- ``validation/corpus/<name>/schema.sql`` — T-SQL DDL with ``GO`` separators;
  ``CREATE TABLE dbo.<name>`` for each ``src_*``, ``ref_*``, and ``dst_*``
  table.
- ``validation/corpus/<name>/seed/src_*.csv`` — at least one seed file.
- ``validation/corpus/<name>/ledger.yaml`` — a ``ledger.yaml`` accepted by
  ``validation.ledger.parse_ledger``; column names match the ``dst_*`` DDL.
- ``validation/corpus/<name>/golden/`` — directory present (may be empty;
  capture fills it later).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from ssis2sql import convert_file
from ssis2sql.generator import ConvertOptions
from validation.ledger import parse_ledger

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parents[2] / "validation" / "corpus"

_EXPECTED_PACKAGES: list[str] = [
    "passthrough_basic",
    "derived_and_convert",
    "conditional_split",
    "aggregate_group",
    "lookup_match",
    "merge_join",
    "union_multicast",
    "etl_full",
]

# ---------------------------------------------------------------------------
# Schema-column extraction helper
#
# Mirrors the logic in validation.provisioning._parse_column_types but is
# self-contained here so the test has no dependency on provisioning internals.
# Returns {table_name: {col_name, ...}} from a schema.sql string.
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE: re.Pattern[str] = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)
_NON_COLUMN_KEYWORDS: frozenset[str] = frozenset(
    {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "CHECK"}
)


def _parse_schema_columns(schema_sql: str) -> dict[str, set[str]]:
    """Return ``{table_name: {col_name, ...}}`` from a ``schema.sql`` string.

    Handles multi-line ``CREATE TABLE`` blocks with nested parentheses (e.g.
    ``DECIMAL(18,4)``).  Skips inline constraint entries.
    """
    result: dict[str, set[str]] = {}
    lines = schema_sql.splitlines()
    i = 0
    while i < len(lines):
        ct_match = _CREATE_TABLE_RE.search(lines[i])
        if not ct_match:
            i += 1
            continue
        table_name = ct_match.group(1)
        result[table_name] = set()
        # Collect lines until paren depth returns to zero.
        block_lines: list[str] = [lines[i]]
        depth = lines[i].count("(") - lines[i].count(")")
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
        # Split on top-level commas.
        entries: list[str] = []
        current: list[str] = []
        pdepth = 0
        for ch in body:
            if ch == "(":
                pdepth += 1
                current.append(ch)
            elif ch == ")":
                pdepth -= 1
                current.append(ch)
            elif ch == "," and pdepth == 0:
                entries.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        tail = "".join(current).strip()
        if tail:
            entries.append(tail)
        for entry in entries:
            tokens = re.split(r"\s+", entry.strip().lstrip("["))
            if len(tokens) < 2:
                continue
            first = tokens[0].strip("[]").upper()
            if first in _NON_COLUMN_KEYWORDS:
                continue
            result[table_name].add(tokens[0].strip("[]"))
    return result


# ---------------------------------------------------------------------------
# Package discovery (drives the parametrized tests)
# ---------------------------------------------------------------------------


def _discover_packages() -> list[Path]:
    """Return sorted list of corpus package dirs that contain a package.dtsx."""
    if not _CORPUS_ROOT.is_dir():
        return []
    return sorted(
        p.parent for p in _CORPUS_ROOT.glob("*/package.dtsx")
    )


_PACKAGES: list[Path] = _discover_packages()
_PACKAGE_IDS: list[str] = [p.name for p in _PACKAGES]


# ---------------------------------------------------------------------------
# Non-parametrized — existence of all eight packages
# ---------------------------------------------------------------------------


def test_corpus_has_eight_packages() -> None:
    """All eight expected corpus package directories must exist.

    This test fails in RED (``validation/corpus/`` is absent) and turns GREEN
    once the engineer has authored and committed all eight packages.
    """
    missing = [
        name
        for name in _EXPECTED_PACKAGES
        if not (_CORPUS_ROOT / name / "package.dtsx").is_file()
    ]
    assert not missing, (
        f"Missing corpus packages (no package.dtsx found): {missing}\n"
        f"Expected under {_CORPUS_ROOT}"
    )


# ---------------------------------------------------------------------------
# Parametrized — per discovered package
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pkg_dir", _PACKAGES, ids=_PACKAGE_IDS)
def test_package_has_required_files(pkg_dir: Path) -> None:
    """Each corpus package must have all required files and directories.

    Required:
    - ``package.dtsx`` — the SSIS package (ODBC→ODBC).
    - ``schema.sql`` — DDL for all ``src_*``, ``ref_*``, and ``dst_*`` tables.
    - ``seed/src_*.csv`` — at least one seed file.
    - ``ledger.yaml`` — comparison policy and known divergences.
    - ``golden/`` — directory (may be empty; filled by the Windows capture step).
    """
    assert (pkg_dir / "package.dtsx").is_file(), "package.dtsx is missing"
    assert (pkg_dir / "schema.sql").is_file(), "schema.sql is missing"
    seed_files = list((pkg_dir / "seed").glob("src_*.csv"))
    assert seed_files, f"no seed/src_*.csv files found in {pkg_dir / 'seed'}"
    assert (pkg_dir / "ledger.yaml").is_file(), "ledger.yaml is missing"
    assert (pkg_dir / "golden").is_dir(), "golden/ directory is missing"


@pytest.mark.parametrize("pkg_dir", _PACKAGES, ids=_PACKAGE_IDS)
def test_package_parses(pkg_dir: Path) -> None:
    """``ssis2sql.convert_file`` must parse ``package.dtsx`` without raising.

    A parse failure (unhandled exception) means the package XML is malformed
    or uses a component structure the parser does not accept.  The test does
    not assert on the SQL output — only that no exception escapes.
    """
    dtsx = pkg_dir / "package.dtsx"
    # convert_file runs the full parse + transpile pipeline; a parse error
    # surfaces as an exception rather than a warning.
    result = convert_file(dtsx, ConvertOptions(wrap_in_procedure=False, include_header=False))
    # A ConversionResult is returned — the package parsed.
    assert result is not None


@pytest.mark.parametrize("pkg_dir", _PACKAGES, ids=_PACKAGE_IDS)
def test_package_transpiles(pkg_dir: Path) -> None:
    """``ssis2sql.convert_file`` must return non-empty SQL for ``package.dtsx``.

    An empty SQL string indicates the transpiler produced no output — either
    the package has no data flow or all components were silently skipped.
    Either would mean the corpus package does not exercise the transpiler.
    """
    dtsx = pkg_dir / "package.dtsx"
    result = convert_file(dtsx, ConvertOptions(wrap_in_procedure=False, include_header=False))
    assert result.sql.strip(), (
        f"convert_file returned empty SQL for {dtsx.name} — "
        "the package must contain at least one data flow"
    )


@pytest.mark.parametrize("pkg_dir", _PACKAGES, ids=_PACKAGE_IDS)
def test_ledger_consistent_with_schema(pkg_dir: Path) -> None:
    """Every column named in ``ledger.yaml`` must exist in the matching ``dst_*`` table.

    ``parse_ledger`` is the Story 4 module; it validates the ledger's internal
    structure.  This test adds the cross-file check: ledger column names are a
    subset of the destination table's columns as declared in ``schema.sql``.

    A column name present in the ledger but absent from the DDL means either
    the ledger is wrong (typo, stale after a schema change) or schema.sql is
    incomplete.
    """
    schema_sql = (pkg_dir / "schema.sql").read_text(encoding="utf-8")
    schema_cols = _parse_schema_columns(schema_sql)

    ledger = parse_ledger(pkg_dir / "ledger.yaml")

    for dest_name, dest_ledger in ledger.items():
        ddl_cols = schema_cols.get(dest_name, set())
        assert ddl_cols, (
            f"destination '{dest_name}' declared in ledger.yaml has no "
            f"CREATE TABLE entry in schema.sql"
        )
        ledger_cols = set(dest_ledger.columns.keys())
        unknown = ledger_cols - ddl_cols
        assert not unknown, (
            f"ledger.yaml [{dest_name}] references columns not in schema.sql: "
            f"{sorted(unknown)}"
        )

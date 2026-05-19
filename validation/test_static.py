"""Static structural enforcement suite — Story 7.

Run via ``just validate-static`` (invokes
``pytest validation/test_static.py``).

This suite applies the static-check functions from
:mod:`validation.static_checks` to every corpus package.  It is intentionally
placed at ``validation/test_static.py`` (not under ``validation/tests/``) so
that ``just test`` and the coverage gate (``pytest validation/tests``) do not
execute it — the corpus-walking iteration and transpile calls are slow relative
to unit tests.

Three enforcement checks:

AC1 — Parse-validity
    For every corpus package, the converted T-SQL produced by
    :func:`ssis2sql.convert_file` must parse without error under
    sqlglot (dialect="tsql").

AC2 — Column lineage
    For every corpus package, every column projected by the converted SQL's
    ``INSERT … SELECT`` must match the ``dst_*`` DDL declared in
    ``schema.sql``, and every base-table column reference in the CTE chain
    must resolve to a known source table column.  Failures name the precise
    unresolved column.

AC3 — Completeness matrix
    The union of component kinds found across all 8 corpus packages must
    cover every kind in :func:`validation.static_checks.must_cover_kinds`
    (15 kinds).

AC4 — No database
    These checks are purely static.  No pyodbc connection is opened; the
    suite runs in a few seconds with no SQL Server required.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ssis2sql import ConvertOptions, convert_file
from ssis2sql.component_types import ComponentKind, resolve
from validation.static_checks import (
    check_column_lineage,
    check_completeness,
    check_sql_parse,
    extract_schema,
)

# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parent / "corpus"

_PACKAGES: list[Path] = sorted(
    p for p in _CORPUS_ROOT.iterdir() if (p / "package.dtsx").is_file()
)

_PKG_NAMES: list[str] = [p.name for p in _PACKAGES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _converted_sql(pkg_dir: Path) -> str:
    """Return the converted T-SQL for a corpus package (no header, no proc)."""
    result = convert_file(
        pkg_dir / "package.dtsx",
        ConvertOptions(wrap_in_procedure=False, include_header=False),
    )
    return result.sql


def _build_corpus_coverage() -> dict[str, set[ComponentKind]]:
    """Return ``{pkg_name: set(ComponentKind)}`` from all corpus packages.

    Parses ``componentClassID`` attributes directly from the ``package.dtsx``
    XML; no database required.
    """
    coverage: dict[str, set[ComponentKind]] = {}
    for pkg_dir in _PACKAGES:
        tree = ET.parse(pkg_dir / "package.dtsx")
        kinds: set[ComponentKind] = set()
        for comp in tree.getroot().iter("component"):
            cid = comp.get("componentClassID", "")
            if cid:
                kinds.add(resolve(cid))
        coverage[pkg_dir.name] = kinds
    return coverage


# ---------------------------------------------------------------------------
# AC1 — Parse-validity (parametrised over all packages)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pkg_name", _PKG_NAMES)
def test_parse_valid(pkg_name: str) -> None:
    """Converted SQL for every corpus package parses as valid T-SQL.

    AC1 — sqlglot dialect="tsql".  No database.
    """
    pkg_dir = _CORPUS_ROOT / pkg_name
    sql = _converted_sql(pkg_dir)
    result = check_sql_parse(sql)
    assert result.ok is True, (
        f"Package {pkg_name!r}: sqlglot T-SQL parse failed.\n{result.error}"
    )


# ---------------------------------------------------------------------------
# AC2 — Column lineage (parametrised over all packages)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pkg_name", _PKG_NAMES)
def test_column_lineage(pkg_name: str) -> None:
    """Column lineage resolves cleanly for every corpus package.

    AC2 — every SELECT alias in the INSERT projection must match the dst_*
    DDL in schema.sql; every base-table column reference must resolve to a
    declared schema column.  No database.
    """
    pkg_dir = _CORPUS_ROOT / pkg_name
    sql = _converted_sql(pkg_dir)
    schema_sql = (pkg_dir / "schema.sql").read_text(encoding="utf-8")
    schema = extract_schema(schema_sql)
    result = check_column_lineage(sql, schema)
    assert result.ok is True, (
        f"Package {pkg_name!r}: column lineage check failed.\n"
        f"Gaps:\n" + "\n".join(f"  - {g}" for g in result.gaps)
    )


# ---------------------------------------------------------------------------
# AC3 — Completeness matrix (one test across all packages)
# ---------------------------------------------------------------------------


def test_completeness_all_kinds_covered() -> None:
    """All 15 must-be-covered ComponentKinds are present in the corpus.

    AC3 — the union of component kinds across all 8 packages must satisfy
    the must_cover_kinds() list.  No database.
    """
    coverage = _build_corpus_coverage()
    result = check_completeness(coverage)
    assert result.ok is True, (
        "Completeness check failed — uncovered kinds:\n"
        + "\n".join(f"  - {k}" for k in result.uncovered)
    )
    assert result.uncovered == [], (
        f"Expected no uncovered kinds; got: {result.uncovered}"
    )

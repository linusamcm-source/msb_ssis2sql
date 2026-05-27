"""Tests for ``msb_ssis2sql._naming.resolve_procedure_name``.

Per plan-final.md §Decisions:
  ``usp_<SanitisedRelativeDir>_<SanitisedPackageName>``
  + special-case: when rel-dir sanitises to empty (top-level files), drop the
    segment entirely → ``usp_<SanitisedPackageName>``.

The "PackageName" in the proc-name comes from the source FILE basename per AC-6
(``examples/sales_etl.dtsx`` -> ``usp_sales_etl``), even though the SSIS
ObjectName of that file is ``CustomerSalesETL``.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql._naming import resolve_procedure_name


def test_top_level_drops_empty_reldir_segment():
    """An empty rel-dir yields usp_<sanitised_filename>."""
    name = resolve_procedure_name(rel_dir=Path("."), file_stem="sales_etl")
    assert name == "usp_sales_etl", name


def test_top_level_drops_when_reldir_is_dot():
    name = resolve_procedure_name(rel_dir=Path(""), file_stem="sales_etl")
    assert name == "usp_sales_etl", name


def test_with_reldir_segments():
    """A rel-dir produces usp_<sanitised_reldir>_<sanitised_filename>."""
    name = resolve_procedure_name(
        rel_dir=Path("nightly/loads"),
        file_stem="sales_etl",
    )
    assert name == "usp_nightly_loads_sales_etl", name


def test_reldir_uppercase_lowered():
    name = resolve_procedure_name(
        rel_dir=Path("MAIN_FIRST"),
        file_stem="main",
    )
    assert name == "usp_main_first_main", name


def test_filename_with_spaces():
    name = resolve_procedure_name(
        rel_dir=Path("."),
        file_stem="Foo Bar",
    )
    assert name == "usp_foo_bar", name


def test_reldir_with_spaces_and_dots():
    name = resolve_procedure_name(
        rel_dir=Path("Some Dir.v1"),
        file_stem="main",
    )
    assert name == "usp_some_dir_v1_main", name

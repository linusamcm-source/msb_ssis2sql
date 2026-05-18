"""Tests for resolving an SSIS componentClassID to a ComponentKind."""
from __future__ import annotations

from ssis2sql.component_types import resolve
from ssis2sql.model import ComponentKind


# --------------------------------------------------------------------------- #
# exact friendly-name lookup
# --------------------------------------------------------------------------- #
def test_exact_friendly_name():
    assert resolve("Microsoft.DerivedColumn") == ComponentKind.DERIVED_COLUMN


def test_friendly_name_is_case_insensitive():
    assert resolve("MICROSOFT.LOOKUP") == ComponentKind.LOOKUP


def test_friendly_name_aliases_collapse_to_one_kind():
    # adonetsource is one of several source aliases mapping to OLEDB_SOURCE.
    assert resolve("Microsoft.ADONETSource") == ComponentKind.OLEDB_SOURCE


# --------------------------------------------------------------------------- #
# assembly-qualified strings - the suffix after the comma is dropped
# --------------------------------------------------------------------------- #
def test_assembly_qualified_string_drops_suffix():
    class_id = (
        "Microsoft.Sort, Microsoft.SqlServer.PipelineHost, "
        "Version=13.0.0.0, Culture=neutral, PublicKeyToken=89845dcd8080cc91"
    )
    assert resolve(class_id) == ComponentKind.SORT


def test_assembly_qualified_unknown_base_falls_through_to_heuristics():
    # The base is not an exact friendly name but contains the "aggregate" needle.
    class_id = "DTS.Aggregate.Component, SomeAssembly, Version=1.0.0.0"
    assert resolve(class_id) == ComponentKind.AGGREGATE


# --------------------------------------------------------------------------- #
# substring heuristics - more specific needles must win
# --------------------------------------------------------------------------- #
def test_mergejoin_heuristic_wins_over_merge():
    assert resolve("Acme.MergeJoinThing") == ComponentKind.MERGE_JOIN


def test_plain_merge_still_resolves_to_merge():
    assert resolve("Acme.MergeThing") == ComponentKind.MERGE


def test_unpivot_heuristic_wins_over_pivot():
    assert resolve("Acme.UnpivotThing") == ComponentKind.UNPIVOT


def test_plain_pivot_still_resolves_to_pivot():
    assert resolve("Acme.PivotThing") == ComponentKind.PIVOT


def test_destination_heuristic_resolves_to_oledb_destination():
    assert resolve("Vendor.CustomDestination") == ComponentKind.OLEDB_DESTINATION


# --------------------------------------------------------------------------- #
# legacy GUID class ids
# --------------------------------------------------------------------------- #
def test_legacy_guid_resolves_to_kind():
    assert (
        resolve("{2C0A8BE5-1EDC-4353-A0EF-B778599C65A0}")
        == ComponentKind.OLEDB_SOURCE
    )


def test_legacy_guid_without_braces_resolves():
    assert (
        resolve("9cf90bf0-5bcc-4c63-b91d-1f322dc12c26")
        == ComponentKind.DERIVED_COLUMN
    )


# --------------------------------------------------------------------------- #
# empty / unknown
# --------------------------------------------------------------------------- #
def test_empty_string_is_unknown():
    assert resolve("") == ComponentKind.UNKNOWN


def test_none_is_unknown():
    assert resolve(None) == ComponentKind.UNKNOWN


def test_unrecognised_class_id_is_unknown():
    assert resolve("Acme.SomethingEntirelyDifferent") == ComponentKind.UNKNOWN

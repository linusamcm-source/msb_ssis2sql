"""Tests for the Relation IR - the relational unit between transpilers."""
from __future__ import annotations

from msb_ssis2sql.relation import RelColumn, Relation


def _sample_relation() -> Relation:
    return Relation(
        name="cte_enrich",
        columns=[
            RelColumn(name="OrderID", expr="[OrderID]", data_type="i4", lineage_id="L1"),
            RelColumn(name="Amount", expr="[Amount]", data_type="numeric", lineage_id="L2"),
            RelColumn(name="Region", expr="N'AU'", data_type="wstr", lineage_id="L3"),
        ],
    )


# --------------------------------------------------------------------------- #
# RelColumn - field defaults
# --------------------------------------------------------------------------- #
def test_relcolumn_carries_name_and_expr():
    col = RelColumn(name="Total", expr="[A] + [B]")
    assert col.name == "Total"
    assert col.expr == "[A] + [B]"


def test_relcolumn_defaults_are_empty_strings():
    col = RelColumn(name="X", expr="[X]")
    assert col.data_type == ""
    assert col.lineage_id == ""


# --------------------------------------------------------------------------- #
# Relation - construction and defaults
# --------------------------------------------------------------------------- #
def test_relation_defaults_to_no_columns_and_no_order():
    rel = Relation(name="cte_empty")
    assert rel.columns == []
    assert rel.order_by == ""


def test_relation_order_by_field_is_retained():
    rel = Relation(name="cte_sorted", order_by="[OrderDate] DESC")
    assert rel.order_by == "[OrderDate] DESC"


# --------------------------------------------------------------------------- #
# Relation.column_names
# --------------------------------------------------------------------------- #
def test_column_names_lists_exposed_columns_in_order():
    assert _sample_relation().column_names() == ["OrderID", "Amount", "Region"]


def test_column_names_is_empty_for_a_bare_relation():
    assert Relation(name="cte_bare").column_names() == []


# --------------------------------------------------------------------------- #
# Relation.find - case-insensitive lookup by name
# --------------------------------------------------------------------------- #
def test_find_returns_the_matching_column():
    col = _sample_relation().find("Amount")
    assert col is not None
    assert col.name == "Amount"


def test_find_is_case_insensitive():
    col = _sample_relation().find("rEgIoN")
    assert col is not None
    assert col.name == "Region"


def test_find_returns_none_when_absent():
    assert _sample_relation().find("Missing") is None


# --------------------------------------------------------------------------- #
# Relation.find_by_lineage - lookup by upstream lineage id
# --------------------------------------------------------------------------- #
def test_find_by_lineage_returns_the_matching_column():
    col = _sample_relation().find_by_lineage("L2")
    assert col is not None
    assert col.name == "Amount"


def test_find_by_lineage_returns_none_for_unknown_id():
    assert _sample_relation().find_by_lineage("L999") is None


def test_find_by_lineage_returns_none_for_empty_id():
    # An empty lineage id must not match columns that also have empty ids.
    rel = Relation(name="cte", columns=[RelColumn(name="X", expr="[X]")])
    assert rel.find_by_lineage("") is None

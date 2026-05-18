"""Tests for the stateless SQL helpers in ``ssis2sql.transforms.base``.

These cover identifier sanitising, trailing-ORDER-BY stripping, table-name
resolution, column re-shaping and source-column resolution. The pure helpers
need no build context; the rest are exercised against a minimally-wired
:class:`BuildContext`.
"""
from __future__ import annotations

from ssis2sql.dialect import TSqlDialect
from ssis2sql.generator import ConvertOptions
from ssis2sql.graph import DataFlowGraph
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Port
from ssis2sql.relation import RelColumn, Relation
from ssis2sql.transforms.base import (
    merge_column,
    passthrough_columns,
    resolve_source_column,
    sanitise_identifier,
    strip_trailing_order_by,
    table_name,
    wrap_sql_command,
)
from ssis2sql.transforms.context import BuildContext


# --------------------------------------------------------------------------- #
# helpers - build a usable BuildContext with no real data flow content
# --------------------------------------------------------------------------- #
def _context() -> BuildContext:
    """A BuildContext over an empty data flow - enough for the base helpers."""
    flow = DataFlow(name="DF", ref_id="DF", components=[], paths=[])
    package = Package(name="Pkg", data_flows=[flow])
    return BuildContext(DataFlowGraph(flow), package, TSqlDialect(), ConvertOptions())


# --------------------------------------------------------------------------- #
# sanitise_identifier
# --------------------------------------------------------------------------- #
def test_sanitise_identifier_leaves_a_plain_name_untouched():
    assert sanitise_identifier("Orders") == "Orders"


def test_sanitise_identifier_collapses_runs_of_non_word_chars():
    assert sanitise_identifier("My Cool Table!!") == "My_Cool_Table"


def test_sanitise_identifier_strips_leading_and_trailing_underscores():
    assert sanitise_identifier("  -weird name-  ") == "weird_name"


def test_sanitise_identifier_prefixes_a_leading_digit():
    assert sanitise_identifier("2023Sales") == "_2023Sales"


def test_sanitise_identifier_empty_input_falls_back_to_x():
    assert sanitise_identifier("") == "x"


def test_sanitise_identifier_all_punctuation_falls_back_to_x():
    assert sanitise_identifier("---") == "x"


def test_sanitise_identifier_none_falls_back_to_x():
    assert sanitise_identifier(None) == "x"


# --------------------------------------------------------------------------- #
# strip_trailing_order_by
# --------------------------------------------------------------------------- #
def test_strip_trailing_order_by_removes_a_top_level_clause():
    cleaned, stripped = strip_trailing_order_by("SELECT a FROM t ORDER BY a")
    assert stripped is True
    assert cleaned == "SELECT a FROM t"


def test_strip_trailing_order_by_no_clause_returns_input_unchanged():
    cleaned, stripped = strip_trailing_order_by("SELECT a FROM t")
    assert stripped is False
    assert cleaned == "SELECT a FROM t"


def test_strip_trailing_order_by_keeps_clause_nested_in_parentheses():
    sql = "SELECT a FROM (SELECT a FROM t ORDER BY a) x"
    cleaned, stripped = strip_trailing_order_by(sql)
    assert stripped is False
    assert cleaned == sql


def test_strip_trailing_order_by_keeps_clause_inside_an_over_clause():
    sql = "SELECT ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t"
    cleaned, stripped = strip_trailing_order_by(sql)
    assert stripped is False
    assert cleaned == sql


def test_strip_trailing_order_by_ignores_the_phrase_inside_a_string_literal():
    sql = "SELECT 'order by me' AS lbl FROM t"
    cleaned, stripped = strip_trailing_order_by(sql)
    assert stripped is False
    assert cleaned == sql


def test_strip_trailing_order_by_drops_a_trailing_semicolon_with_the_clause():
    cleaned, stripped = strip_trailing_order_by("SELECT a FROM t ORDER BY a;")
    assert stripped is True
    assert cleaned == "SELECT a FROM t"


def test_strip_trailing_order_by_is_case_insensitive():
    cleaned, stripped = strip_trailing_order_by("select a from t OrDeR By a")
    assert stripped is True
    assert cleaned == "select a from t"


# --------------------------------------------------------------------------- #
# table_name
# --------------------------------------------------------------------------- #
def test_table_name_prefers_open_rowset():
    comp = Component(properties={"OpenRowset": "dbo.Target", "TableName": "dbo.Other"})
    assert table_name(comp) == "dbo.Target"


def test_table_name_falls_back_to_table_name_property():
    comp = Component(properties={"TableName": "dbo.Customers"})
    assert table_name(comp) == "dbo.Customers"


def test_table_name_returns_empty_when_neither_property_present():
    assert table_name(Component()) == ""


def test_table_name_strips_surrounding_whitespace():
    comp = Component(properties={"OpenRowset": "  dbo.Spaced  "})
    assert table_name(comp) == "dbo.Spaced"


# --------------------------------------------------------------------------- #
# passthrough_columns
# --------------------------------------------------------------------------- #
def test_passthrough_columns_without_alias_copies_expressions_verbatim():
    ctx = _context()
    relation = Relation(
        name="Src",
        columns=[
            RelColumn("Id", "[Id]", "i4", "lin.Id"),
            RelColumn("Name", "UPPER([Name])", "wstr", "lin.Name"),
        ],
    )
    out = passthrough_columns(ctx, relation)
    assert [c.name for c in out] == ["Id", "Name"]
    assert [c.expr for c in out] == ["[Id]", "UPPER([Name])"]
    assert [c.lineage_id for c in out] == ["lin.Id", "lin.Name"]


def test_passthrough_columns_without_alias_returns_a_fresh_list():
    ctx = _context()
    relation = Relation(name="Src", columns=[RelColumn("Id", "[Id]")])
    out = passthrough_columns(ctx, relation)
    assert out is not relation.columns
    assert out[0] is not relation.columns[0]


def test_passthrough_columns_with_alias_qualifies_each_expression():
    ctx = _context()
    relation = Relation(
        name="Src",
        columns=[RelColumn("Id", "[Id]"), RelColumn("Name", "[Name]")],
    )
    out = passthrough_columns(ctx, relation, alias="L")
    assert [c.expr for c in out] == ["[L].[Id]", "[L].[Name]"]
    assert [c.name for c in out] == ["Id", "Name"]


# --------------------------------------------------------------------------- #
# merge_column
# --------------------------------------------------------------------------- #
def test_merge_column_appends_a_new_column_and_records_its_index():
    columns: list[RelColumn] = []
    index: dict[str, int] = {}
    merge_column(columns, index, RelColumn("Id", "[Id]"))
    assert [c.name for c in columns] == ["Id"]
    assert index == {"id": 0}


def test_merge_column_replaces_a_same_named_column_in_place():
    columns = [RelColumn("Id", "[Id]"), RelColumn("Name", "[Name]")]
    index = {"id": 0, "name": 1}
    merge_column(columns, index, RelColumn("Name", "UPPER([Name])"))
    assert len(columns) == 2
    assert columns[1].expr == "UPPER([Name])"
    assert index == {"id": 0, "name": 1}


def test_merge_column_matches_case_insensitively():
    columns = [RelColumn("Name", "[Name]")]
    index = {"name": 0}
    merge_column(columns, index, RelColumn("NAME", "LOWER([Name])"))
    assert len(columns) == 1
    assert columns[0].name == "NAME"
    assert columns[0].expr == "LOWER([Name])"


def test_merge_column_keeps_index_in_sync_across_several_appends():
    columns: list[RelColumn] = []
    index: dict[str, int] = {}
    for name in ("A", "B", "C"):
        merge_column(columns, index, RelColumn(name, f"[{name}]"))
    assert index == {"a": 0, "b": 1, "c": 2}


# --------------------------------------------------------------------------- #
# resolve_source_column
# --------------------------------------------------------------------------- #
def test_resolve_source_column_uses_explicit_lineage_id():
    ctx = _context()
    component = Component(name="Conv", kind=ComponentKind.DATA_CONVERSION)
    upstream = Relation(
        name="Up",
        columns=[RelColumn("Code", "[Code]", "wstr", "S.out.Code")],
    )
    output_col = Column(
        name="CodeNum",
        properties={"SourceInputColumnLineageID": "S.out.Code"},
    )
    assert resolve_source_column(ctx, component, output_col, upstream) == "Code"


def test_resolve_source_column_falls_back_to_input_columns_by_lineage():
    ctx = _context()
    inp = Port(ref_id="C.in", name="Input")
    inp.columns = [Column(name="Code", upstream_lineage_id="S.out.Code")]
    component = Component(name="Conv", kind=ComponentKind.DATA_CONVERSION, inputs=[inp])
    # Upstream relation has the column by name but NOT by lineage id.
    upstream = Relation(name="Up", columns=[RelColumn("Code", "[Code]")])
    output_col = Column(
        name="CodeNum",
        properties={"SourceInputColumnLineageID": "S.out.Code"},
    )
    assert resolve_source_column(ctx, component, output_col, upstream) == "Code"


def test_resolve_source_column_strips_a_copy_of_prefix():
    ctx = _context()
    component = Component(name="Copy", kind=ComponentKind.COPY_COLUMN)
    upstream = Relation(name="Up", columns=[RelColumn("Region", "[Region]")])
    output_col = Column(name="Copy of Region")
    assert resolve_source_column(ctx, component, output_col, upstream) == "Region"


def test_resolve_source_column_matches_by_plain_name():
    ctx = _context()
    component = Component(name="Conv", kind=ComponentKind.DATA_CONVERSION)
    upstream = Relation(name="Up", columns=[RelColumn("Amount", "[Amount]")])
    output_col = Column(name="Amount")
    assert resolve_source_column(ctx, component, output_col, upstream) == "Amount"


def test_resolve_source_column_returns_empty_and_warns_on_failure():
    ctx = _context()
    component = Component(name="Conv", kind=ComponentKind.DATA_CONVERSION)
    upstream = Relation(name="Up", columns=[RelColumn("Other", "[Other]")])
    output_col = Column(name="Missing")
    assert resolve_source_column(ctx, component, output_col, upstream) == ""
    assert any("could not resolve the source column" in w for w in ctx.warnings)
    assert any("[Missing]" in w for w in ctx.warnings)


# --------------------------------------------------------------------------- #
# wrap_sql_command
# --------------------------------------------------------------------------- #
def test_wrap_sql_command_returns_none_when_no_sql_command():
    ctx = _context()
    component = Component(name="Src", kind=ComponentKind.OLEDB_SOURCE)
    assert wrap_sql_command(ctx, component, "Src") is None


def test_wrap_sql_command_wraps_the_query_as_a_derived_table():
    ctx = _context()
    component = Component(
        name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    wrapped = wrap_sql_command(ctx, component, "Src")
    assert wrapped is not None
    assert wrapped.startswith("(\n")
    assert wrapped.endswith("\n) AS Src")
    assert "    SELECT Id FROM dbo.T" in wrapped
    assert ctx.warnings == []


def test_wrap_sql_command_strips_a_trailing_order_by_and_warns():
    ctx = _context()
    component = Component(
        name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T ORDER BY Id"},
    )
    wrapped = wrap_sql_command(ctx, component, "Src")
    assert wrapped is not None
    assert "ORDER BY" not in wrapped
    assert any("trailing ORDER BY was removed" in w for w in ctx.warnings)


def test_wrap_sql_command_indents_every_line_of_a_multiline_query():
    ctx = _context()
    component = Component(
        name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id,\nName\nFROM dbo.T"},
    )
    wrapped = wrap_sql_command(ctx, component, "Q")
    body = wrapped.splitlines()[1:-1]
    assert all(line.startswith("    ") for line in body)
    assert body == ["    SELECT Id,", "    Name", "    FROM dbo.T"]

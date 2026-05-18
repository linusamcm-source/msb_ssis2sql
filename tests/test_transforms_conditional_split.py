"""Direct unit tests for the ConditionalSplitTranspiler.

A Conditional Split routes rows to the first matching output; each output
becomes its own filtered CTE, and later branches negate every earlier
condition to preserve first-match-wins semantics.
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.conditional_split import ConditionalSplitTranspiler


def test_conditional_split_transpiler_is_registered():
    assert ConditionalSplitTranspiler.kinds == (ComponentKind.CONDITIONAL_SPLIT,)


def _split_to_two_destinations_package() -> Package:
    """Source -> Conditional Split (one condition + default) -> two destinations."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Amount FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Id", name="Id", data_type="i4"),
        Column(ref_id="S.out.Amount", name="Amount", data_type="i4"),
    ]
    source.outputs = [source_out]

    split = Component(
        ref_id="C", name="Route", class_id="Microsoft.ConditionalSplit",
        kind=ComponentKind.CONDITIONAL_SPLIT,
    )
    split.inputs = [Port(ref_id="C.in", name="Input")]
    high = Port(ref_id="C.high", name="High")
    high.properties = {"Expression": "[Amount] > 100", "EvaluationOrder": "0"}
    low = Port(ref_id="C.low", name="Low")              # default output, no expression
    split.outputs = [high, low]

    dest_high = Component(
        ref_id="DH", name="DstHigh", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.HighTbl"},
    )
    dest_high_in = Port(ref_id="DH.in", name="Input")
    dest_high_in.columns = [Column(name="Id"), Column(name="Amount")]
    dest_high.inputs = [dest_high_in]

    dest_low = Component(
        ref_id="DL", name="DstLow", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.LowTbl"},
    )
    dest_low_in = Port(ref_id="DL.in", name="Input")
    dest_low_in.columns = [Column(name="Id"), Column(name="Amount")]
    dest_low.inputs = [dest_low_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, split, dest_high, dest_low],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.high", end_id="DH.in"),
            Path(ref_id="p3", name="p3", start_id="C.low", end_id="DL.in"),
        ],
    )
    return Package(name="SplitPkg", data_flows=[flow])


def test_conditional_split_emits_one_filtered_cte_per_output():
    sql = convert_package(_split_to_two_destinations_package()).sql
    assert sql.count("INSERT INTO") == 2
    # The conditional branch carries its predicate as a WHERE clause.
    assert "WHERE ([Amount] > 100)" in sql
    # Both outputs become CTEs named "<component>_<output>".
    assert "[Route_High] AS (" in sql
    assert "[Route_Low] AS (" in sql


def test_conditional_split_default_branch_negates_earlier_conditions():
    sql = convert_package(_split_to_two_destinations_package()).sql
    # The default (Low) output excludes every earlier condition.
    low_chunk = next(c for c in sql.split("WITH ") if "INSERT INTO [dbo].[LowTbl]" in c)
    assert "WHERE NOT ([Amount] > 100)" in low_chunk


def test_conditional_split_orders_conditions_by_evaluation_order():
    """Two conditions: the lower EvaluationOrder negates first."""
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Amount FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Id", name="Id", data_type="i4"),
        Column(ref_id="S.out.Amount", name="Amount", data_type="i4"),
    ]
    source.outputs = [source_out]

    split = Component(ref_id="C", name="Route", kind=ComponentKind.CONDITIONAL_SPLIT)
    split.inputs = [Port(ref_id="C.in", name="Input")]
    second = Port(ref_id="C.second", name="Second")
    second.properties = {"Expression": "[Amount] > 50", "EvaluationOrder": "1"}
    first = Port(ref_id="C.first", name="First")
    first.properties = {"Expression": "[Amount] > 100", "EvaluationOrder": "0"}
    # Document order deliberately puts the later-evaluating output first.
    split.outputs = [second, first]

    dest = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.SecondTbl"},
    )
    dest_in = Port(ref_id="D.in", name="Input")
    dest_in.columns = [Column(name="Id"), Column(name="Amount")]
    dest.inputs = [dest_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, split, dest],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.second", end_id="D.in"),
        ],
    )
    sql = convert_package(Package(name="OrderPkg", data_flows=[flow])).sql
    # "Second" (EvaluationOrder 1) must negate the EvaluationOrder-0 condition.
    assert "WHERE NOT ([Amount] > 100) AND ([Amount] > 50)" in sql


# --------------------------------------------------------------------------- #
# Conditional-split edge cases: no input, untranslatable and warning conditions.
# --------------------------------------------------------------------------- #
def _split_with_one_condition(expr_text: str, target: str) -> Package:
    """Source -> Conditional Split (single conditional output) -> destination."""
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    split = Component(ref_id="C", name="Route", kind=ComponentKind.CONDITIONAL_SPLIT)
    split.inputs = [Port(ref_id="C.in", name="Input")]
    case = Port(ref_id="C.case", name="Case")
    case.properties = {"Expression": expr_text, "EvaluationOrder": "0"}
    split.outputs = [case]

    dest = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": target},
    )
    dest_in = Port(ref_id="D.in", name="Input")
    dest_in.columns = [Column(name="Id")]
    dest.inputs = [dest_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, split, dest],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.case", end_id="D.in"),
        ],
    )
    return Package(name="SplitCasePkg", data_flows=[flow])


def test_conditional_split_with_no_input_is_skipped():
    split = Component(ref_id="C", name="Orphan Split",
                      kind=ComponentKind.CONDITIONAL_SPLIT)
    split.inputs = [Port(ref_id="C.in", name="Input")]
    split.outputs = [Port(ref_id="C.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[split], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Split" not in result.sql


def test_conditional_split_untranslatable_condition_falls_back_to_false():
    # A structurally invalid condition; the transpiler emits 1 = 0 and warns.
    result = convert_package(_split_with_one_condition("[Id] [Id]", "dbo.Bad"))
    assert "WHERE (1 = 0)" in result.sql
    assert any("conditional split case" in w for w in result.warnings)


def test_conditional_split_condition_with_a_warning_is_reported():
    # An unmapped SSIS function raises a translator warning, not an error.
    result = convert_package(
        _split_with_one_condition("WIDGETIZE([Id]) > 0", "dbo.Warned")
    )
    assert any("WIDGETIZE" in w for w in result.warnings)

"""Direct unit tests for the set-operation transpilers.

Covers UnionAllTranspiler (several inputs stacked with UNION ALL) and
MergeJoinTranspiler (two inputs combined horizontally with a JOIN).
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.set_ops import MergeJoinTranspiler, UnionAllTranspiler


def test_set_op_transpilers_are_registered():
    assert ComponentKind.UNION_ALL in UnionAllTranspiler.kinds
    assert ComponentKind.MERGE in UnionAllTranspiler.kinds
    assert MergeJoinTranspiler.kinds == (ComponentKind.MERGE_JOIN,)


def _union_all_package() -> Package:
    """Two sources -> Union All -> destination."""
    src_a = Component(
        ref_id="A", name="SrcA", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Name FROM dbo.A"},
    )
    a_out = Port(ref_id="A.out", name="Output")
    a_out.columns = [
        Column(ref_id="A.out.Id", name="Id", data_type="i4"),
        Column(ref_id="A.out.Name", name="Name", data_type="wstr", length=50),
    ]
    src_a.outputs = [a_out]

    src_b = Component(
        ref_id="B", name="SrcB", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Name FROM dbo.B"},
    )
    b_out = Port(ref_id="B.out", name="Output")
    b_out.columns = [
        Column(ref_id="B.out.Id", name="Id", data_type="i4"),
        Column(ref_id="B.out.Name", name="Name", data_type="wstr", length=50),
    ]
    src_b.outputs = [b_out]

    union = Component(
        ref_id="U", name="Combine", class_id="Microsoft.UnionAll",
        kind=ComponentKind.UNION_ALL,
    )
    union.inputs = [Port(ref_id="U.in1", name="Union All Input 1"),
                    Port(ref_id="U.in2", name="Union All Input 2")]
    union_out = Port(ref_id="U.out", name="Union All Output 1")
    union_out.columns = [
        Column(ref_id="U.out.Id", name="Id", data_type="i4"),
        Column(ref_id="U.out.Name", name="Name", data_type="wstr", length=50),
    ]
    union.outputs = [union_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Merged"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id"), Column(name="Name")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[src_a, src_b, union, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="A.out", end_id="U.in1"),
            Path(ref_id="p2", name="p2", start_id="B.out", end_id="U.in2"),
            Path(ref_id="p3", name="p3", start_id="U.out", end_id="D.in"),
        ],
    )
    return Package(name="UnionPkg", data_flows=[flow])


def test_union_all_stacks_inputs_with_union_all():
    sql = convert_package(_union_all_package()).sql
    assert "UNION ALL" in sql
    assert "INSERT INTO [dbo].[Merged]" in sql
    # The union CTE projects both branches into the output's columns.
    union_cte = sql.split("[Combine] AS (")[1].split("\n)")[0]
    assert union_cte.count("[Id] AS [Id]") == 2
    assert union_cte.count("[Name] AS [Name]") == 2
    # Each branch reads from its own source CTE.
    assert "FROM [SrcA]" in union_cte and "FROM [SrcB]" in union_cte


def _merge_join_package(join_type: str = "2") -> Package:
    """Two sources -> Merge Join -> destination. JoinType 2 => INNER JOIN."""
    left = Component(
        ref_id="L", name="Left", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Name FROM dbo.A"},
    )
    left_out = Port(ref_id="L.out", name="Output")
    left_out.columns = [
        Column(ref_id="L.out.Id", name="Id", data_type="i4"),
        Column(ref_id="L.out.Name", name="Name", data_type="wstr", length=50),
    ]
    left.outputs = [left_out]

    right = Component(
        ref_id="R", name="Right", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Score FROM dbo.B"},
    )
    right_out = Port(ref_id="R.out", name="Output")
    right_out.columns = [
        Column(ref_id="R.out.Id", name="Id", data_type="i4"),
        Column(ref_id="R.out.Score", name="Score", data_type="i4"),
    ]
    right.outputs = [right_out]

    join = Component(
        ref_id="J", name="Join", class_id="Microsoft.MergeJoin",
        kind=ComponentKind.MERGE_JOIN, properties={"JoinType": join_type},
    )
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join.outputs = [Port(ref_id="J.out", name="Output")]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Joined"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id"), Column(name="Name"), Column(name="Score")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, right, join, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.left"),
            Path(ref_id="p2", name="p2", start_id="R.out", end_id="J.right"),
            Path(ref_id="p3", name="p3", start_id="J.out", end_id="D.in"),
        ],
    )
    return Package(name="MergeJoinPkg", data_flows=[flow])


def test_merge_join_emits_an_inner_join():
    result = convert_package(_merge_join_package(join_type="2"))
    sql = result.sql
    assert "INNER JOIN [Right] AS R" in sql
    # Join key resolved by shared column name.
    assert "ON L.[Id] = R.[Id]" in sql
    assert any("emitted as INNER JOIN" in w for w in result.warnings)


def test_merge_join_left_join_type():
    sql = convert_package(_merge_join_package(join_type="1")).sql
    assert "LEFT OUTER JOIN [Right] AS R" in sql

"""Direct unit tests for the set-operation transpilers.

Covers UnionAllTranspiler (several inputs stacked with UNION ALL) and
MergeJoinTranspiler (two inputs combined horizontally with a JOIN).
"""
from __future__ import annotations

from msb_ssis2sql.generator import convert_package
from msb_ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from msb_ssis2sql.transforms.set_ops import MergeJoinTranspiler, UnionAllTranspiler


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


# --------------------------------------------------------------------------- #
# Degenerate set-operation inputs: missing ports, disconnected inputs,
# NULL-filled branches, cross joins and explicit output column lists.
# --------------------------------------------------------------------------- #
def _source(ref: str, name: str, columns: list[tuple[str, str]]) -> Component:
    """A minimal OLE DB source exposing the given (name, data_type) columns."""
    comp = Component(
        ref_id=ref, name=name, class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": f"SELECT * FROM dbo.{name}"},
    )
    port = Port(ref_id=f"{ref}.out", name="Output")
    port.columns = [
        Column(ref_id=f"{ref}.out.{cn}", name=cn, data_type=dt) for cn, dt in columns
    ]
    comp.outputs = [port]
    return comp


def test_union_with_no_output_port_is_skipped():
    union = Component(ref_id="U", name="No Output Union", kind=ComponentKind.UNION_ALL)
    union.inputs = [Port(ref_id="U.in", name="Union All Input 1")]
    union.outputs = []
    flow = DataFlow(name="DF", ref_id="DF", components=[union], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert any("union" in w and "no output" in w for w in result.warnings)


def test_union_with_no_connected_inputs_is_skipped():
    union = Component(ref_id="U", name="Disconnected Union", kind=ComponentKind.UNION_ALL)
    union.inputs = [Port(ref_id="U.in", name="Union All Input 1")]
    out = Port(ref_id="U.out", name="Union All Output 1")
    out.columns = [Column(name="Id", data_type="i4")]
    union.outputs = [out]
    flow = DataFlow(name="DF", ref_id="DF", components=[union], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert any("no connected inputs" in w for w in result.warnings)


def test_merge_component_is_emitted_as_union_all_with_a_warning():
    pkg = _union_all_package()
    pkg.data_flows[0].components[2].kind = ComponentKind.MERGE
    result = convert_package(pkg)
    assert "UNION ALL" in result.sql
    assert any("interleaved sort order" in w for w in result.warnings)


def test_union_fills_a_missing_branch_column_with_null():
    src_a = _source("A", "SrcA", [("Id", "i4"), ("Name", "wstr")])
    src_b = _source("B", "SrcB", [("Id", "i4")])              # no Name column
    union = Component(ref_id="U", name="Combine", kind=ComponentKind.UNION_ALL)
    union.inputs = [Port(ref_id="U.in1", name="Union All Input 1"),
                    Port(ref_id="U.in2", name="Union All Input 2")]
    union_out = Port(ref_id="U.out", name="Union All Output 1")
    union_out.columns = [Column(name="Id", data_type="i4"),
                         Column(name="Name", data_type="wstr")]
    union.outputs = [union_out]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[src_a, src_b, union],
        paths=[
            Path(ref_id="p1", name="p1", start_id="A.out", end_id="U.in1"),
            Path(ref_id="p2", name="p2", start_id="B.out", end_id="U.in2"),
        ],
    )
    result = convert_package(Package(name="NullFillPkg", data_flows=[flow]))
    assert "NULL AS [Name]" in result.sql
    assert any("filled with NULL" in w for w in result.warnings)


def test_merge_join_with_no_output_port_is_skipped():
    join = Component(ref_id="J", name="No Output Join", kind=ComponentKind.MERGE_JOIN)
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join.outputs = []
    flow = DataFlow(name="DF", ref_id="DF", components=[join], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert any("merge join" in w and "no output" in w for w in result.warnings)


def test_merge_join_with_only_one_connected_input_is_skipped():
    left = _source("L", "Left", [("Id", "i4")])
    join = Component(ref_id="J", name="Half Join", kind=ComponentKind.MERGE_JOIN)
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join.outputs = [Port(ref_id="J.out", name="Output")]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, join],
        paths=[Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.left")],
    )
    result = convert_package(Package(name="HalfJoinPkg", data_flows=[flow]))
    assert any("two connected inputs" in w for w in result.warnings)


def test_merge_join_without_shared_keys_emits_a_cross_join():
    left = _source("L", "Left", [("LeftId", "i4")])
    right = _source("R", "Right", [("RightId", "i4")])
    join = Component(ref_id="J", name="KeylessJoin", kind=ComponentKind.MERGE_JOIN,
                     properties={"JoinType": "2"})
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join.outputs = [Port(ref_id="J.out", name="Output")]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, right, join],
        paths=[
            Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.left"),
            Path(ref_id="p2", name="p2", start_id="R.out", end_id="J.right"),
        ],
    )
    result = convert_package(Package(name="KeylessJoinPkg", data_flows=[flow]))
    assert "ON 1 = 1" in result.sql
    assert any("cross join" in w for w in result.warnings)


def test_merge_join_inputs_without_left_right_labels_fall_back_to_order():
    left = _source("L", "First", [("Id", "i4"), ("Name", "wstr")])
    right = _source("R", "Second", [("Id", "i4"), ("Score", "i4")])
    join = Component(ref_id="J", name="OrderedJoin", kind=ComponentKind.MERGE_JOIN,
                     properties={"JoinType": "2"})
    # Neither input name contains 'left' or 'right'.
    join.inputs = [Port(ref_id="J.in1", name="Input 1"),
                   Port(ref_id="J.in2", name="Input 2")]
    join.outputs = [Port(ref_id="J.out", name="Output")]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, right, join],
        paths=[
            Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.in1"),
            Path(ref_id="p2", name="p2", start_id="R.out", end_id="J.in2"),
        ],
    )
    sql = convert_package(Package(name="OrderedJoinPkg", data_flows=[flow])).sql
    assert "INNER JOIN [Second] AS R" in sql
    assert "ON L.[Id] = R.[Id]" in sql


def test_merge_join_respects_num_key_columns():
    left = _source("L", "Left", [("Id", "i4"), ("Code", "wstr")])
    right = _source("R", "Right", [("Id", "i4"), ("Code", "wstr")])
    join = Component(ref_id="J", name="LimitedJoin", kind=ComponentKind.MERGE_JOIN,
                     properties={"JoinType": "2", "NumKeyColumns": "1"})
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join.outputs = [Port(ref_id="J.out", name="Output")]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, right, join],
        paths=[
            Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.left"),
            Path(ref_id="p2", name="p2", start_id="R.out", end_id="J.right"),
        ],
    )
    sql = convert_package(Package(name="LimitedJoinPkg", data_flows=[flow])).sql
    # Two shared columns, but NumKeyColumns caps the join to a single key.
    assert "L.[Id] = R.[Id]" in sql
    assert "L.[Code] = R.[Code]" not in sql


def test_merge_join_with_an_explicit_output_column_list():
    left = _source("L", "Left", [("Id", "i4"), ("Name", "wstr")])
    right = _source("R", "Right", [("Id", "i4"), ("Score", "i4")])
    join = Component(ref_id="J", name="ColJoin", kind=ComponentKind.MERGE_JOIN,
                     properties={"JoinType": "2"})
    join.inputs = [Port(ref_id="J.left", name="Left Input"),
                   Port(ref_id="J.right", name="Right Input")]
    join_out = Port(ref_id="J.out", name="Output")
    join_out.columns = [
        Column(name="Name", data_type="wstr"),       # from the left input
        Column(name="Score", data_type="i4"),        # from the right input
        Column(name="Ghost", data_type="i4"),        # matches neither input
    ]
    join.outputs = [join_out]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[left, right, join],
        paths=[
            Path(ref_id="p1", name="p1", start_id="L.out", end_id="J.left"),
            Path(ref_id="p2", name="p2", start_id="R.out", end_id="J.right"),
        ],
    )
    result = convert_package(Package(name="ColJoinPkg", data_flows=[flow]))
    sql = result.sql
    assert "L.[Name] AS [Name]" in sql
    assert "R.[Score] AS [Score]" in sql
    assert "NULL AS [Ghost]" in sql
    assert any("neither input" in w for w in result.warnings)

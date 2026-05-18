"""End-to-end tests for the generator."""
from __future__ import annotations

import pytest

from ssis2sql.errors import GraphError
from ssis2sql.generator import ConvertOptions, convert_file, convert_package
from ssis2sql.graph import DataFlowGraph
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port


def test_example_converts_to_consolidated_sql(example_path):
    sql = convert_file(example_path).sql
    # One consolidated WITH-pipeline per destination.
    assert sql.count("INSERT INTO") == 2
    assert "WITH [Sales_Orders_Source] AS" in sql
    assert "INSERT INTO [dbo].[FactSalesEnriched]" in sql
    assert "INSERT INTO [dbo].[RegionSummary]" in sql


def test_derived_column_expressions_translated(example_path):
    sql = convert_file(example_path).sql
    assert "DATEPART(year, [OrderDate])" in sql
    assert "UPPER(LTRIM(RTRIM([Region])))" in sql
    assert "([Amount] - ([Amount] * 0.10))" in sql


def test_lookup_becomes_left_join(example_path):
    sql = convert_file(example_path).sql
    assert "LEFT JOIN [Customer_Lookup_Ref] AS R" in sql
    assert "ON L.[CustomerID] = R.[CustomerID]" in sql


def test_conditional_split_first_match_semantics(example_path):
    # The default branch must exclude every earlier condition.
    sql = convert_file(example_path).sql
    assert "WHERE NOT (" in sql


def test_union_all_recombines_branches(example_path):
    assert "UNION ALL" in convert_file(example_path).sql


def test_aggregate_emits_group_by(example_path):
    sql = convert_file(example_path).sql
    assert "GROUP BY [Region]" in sql
    assert "SUM([Amount])" in sql
    assert "COUNT([OrderID])" in sql


def test_sort_feeds_order_by_into_destination(example_path):
    sql = convert_file(example_path).sql
    assert "ORDER BY [RegionClean] ASC, [NetAmount] DESC" in sql


def test_variable_declaration_emitted(example_path):
    sql = convert_file(example_path).sql
    assert "DECLARE @MinThreshold" in sql


def test_procedure_wrapping(example_path):
    options = ConvertOptions(wrap_in_procedure=True, procedure_name="usp_Load")
    sql = convert_file(example_path, options).sql
    assert "CREATE OR ALTER PROCEDURE usp_Load" in sql
    assert "SET NOCOUNT ON;" in sql


def test_warnings_are_collected(example_path):
    result = convert_file(example_path)
    assert isinstance(result.warnings, list)
    assert any("LEFT JOIN" in warning for warning in result.warnings)


def _minimal_package() -> Package:
    """A two-component package built straight from the IR, no XML involved."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"AccessMode": "2", "SqlCommand": "SELECT Id, Name FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Id", name="Id", data_type="i4"),
        Column(ref_id="S.out.Name", name="Name", data_type="wstr", length=50),
    ]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", class_id="Microsoft.OLEDBDestination",
        kind=ComponentKind.OLEDB_DESTINATION, properties={"OpenRowset": "dbo.Target"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id"), Column(name="Name")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    return Package(name="Mini", data_flows=[flow])


def test_programmatic_package_converts():
    result = convert_package(_minimal_package())
    assert "WITH [Src] AS" in result.sql
    assert "INSERT INTO [dbo].[Target]" in result.sql
    assert "FROM dbo.T" in result.sql


# --------------------------------------------------------------------------- #
# Data Conversion - a transpiler kind the bundled example never exercises.
# --------------------------------------------------------------------------- #
def _data_conversion_package() -> Package:
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Code FROM dbo.Raw"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Code", name="Code", data_type="wstr", length=20,
               lineage_id="S.out.Code"),
    ]
    source.outputs = [source_out]

    convert = Component(
        ref_id="C", name="Cast Code", class_id="Microsoft.DataConvert",
        kind=ComponentKind.DATA_CONVERSION,
    )
    convert_in = Port(ref_id="C.in", name="Input")
    convert_in.columns = [Column(name="Code", upstream_lineage_id="S.out.Code")]
    convert_out = Port(ref_id="C.out", name="Output")
    convert_out.columns = [
        Column(ref_id="C.out.CodeNum", name="CodeNum", data_type="i4",
               lineage_id="C.out.CodeNum",
               properties={"SourceInputColumnLineageID": "S.out.Code"}),
    ]
    convert.inputs = [convert_in]
    convert.outputs = [convert_out]

    destination = Component(
        ref_id="D", name="Dst", class_id="Microsoft.OLEDBDestination",
        kind=ComponentKind.OLEDB_DESTINATION, properties={"OpenRowset": "dbo.Clean"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Code"), Column(name="CodeNum")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, convert, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.out", end_id="D.in"),
        ],
    )
    return Package(name="ConvPkg", data_flows=[flow])


def test_data_conversion_emits_a_cast():
    sql = convert_package(_data_conversion_package()).sql
    assert "CAST([Code] AS INT) AS [CodeNum]" in sql


# --------------------------------------------------------------------------- #
# Multicast - one input fans out to several identical destinations.
# --------------------------------------------------------------------------- #
def _multicast_package() -> Package:
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    multicast = Component(ref_id="M", name="Fan Out", kind=ComponentKind.MULTICAST)
    multicast.inputs = [Port(ref_id="M.in", name="Input")]
    multicast.outputs = [Port(ref_id="M.outA", name="Output 1"),
                         Port(ref_id="M.outB", name="Output 2")]

    dest_a = Component(
        ref_id="DA", name="DstA", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.TargetA"},
    )
    dest_a_in = Port(ref_id="DA.in", name="Input")
    dest_a_in.columns = [Column(name="Id")]
    dest_a.inputs = [dest_a_in]

    dest_b = Component(
        ref_id="DB", name="DstB", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.TargetB"},
    )
    dest_b_in = Port(ref_id="DB.in", name="Input")
    dest_b_in.columns = [Column(name="Id")]
    dest_b.inputs = [dest_b_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, multicast, dest_a, dest_b],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="M.in"),
            Path(ref_id="p2", name="p2", start_id="M.outA", end_id="DA.in"),
            Path(ref_id="p3", name="p3", start_id="M.outB", end_id="DB.in"),
        ],
    )
    return Package(name="MulticastPkg", data_flows=[flow])


def test_multicast_fans_out_to_two_destinations():
    sql = convert_package(_multicast_package()).sql
    assert sql.count("INSERT INTO") == 2
    assert "INSERT INTO [dbo].[TargetA]" in sql
    assert "INSERT INTO [dbo].[TargetB]" in sql


# --------------------------------------------------------------------------- #
# Merge Join - two inputs combined horizontally.
# --------------------------------------------------------------------------- #
def _merge_join_package() -> Package:
    left = Component(
        ref_id="L", name="Left", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Name FROM dbo.A"},
    )
    left_out = Port(ref_id="L.out", name="Output")
    left_out.columns = [Column(ref_id="L.out.Id", name="Id", data_type="i4"),
                        Column(ref_id="L.out.Name", name="Name", data_type="wstr", length=50)]
    left.outputs = [left_out]

    right = Component(
        ref_id="R", name="Right", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Score FROM dbo.B"},
    )
    right_out = Port(ref_id="R.out", name="Output")
    right_out.columns = [Column(ref_id="R.out.Id", name="Id", data_type="i4"),
                         Column(ref_id="R.out.Score", name="Score", data_type="i4")]
    right.outputs = [right_out]

    join = Component(
        ref_id="J", name="Join", kind=ComponentKind.MERGE_JOIN,
        properties={"JoinType": "2"},                    # 2 => INNER JOIN
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
    sql = convert_package(_merge_join_package()).sql
    assert "INNER JOIN [Right] AS R" in sql
    assert "ON L.[Id] = R.[Id]" in sql


# --------------------------------------------------------------------------- #
# Cycle handling - the graph rejects it, the generator degrades gracefully.
# --------------------------------------------------------------------------- #
def _cyclic_flow() -> DataFlow:
    left = Component(ref_id="A", name="A", kind=ComponentKind.DERIVED_COLUMN)
    left.inputs = [Port(ref_id="A.in", name="In")]
    left.outputs = [Port(ref_id="A.out", name="Out")]
    right = Component(ref_id="B", name="B", kind=ComponentKind.DERIVED_COLUMN)
    right.inputs = [Port(ref_id="B.in", name="In")]
    right.outputs = [Port(ref_id="B.out", name="Out")]
    return DataFlow(
        name="Cyclic Flow", ref_id="DF", components=[left, right],
        paths=[
            Path(ref_id="p1", name="p1", start_id="A.out", end_id="B.in"),
            Path(ref_id="p2", name="p2", start_id="B.out", end_id="A.in"),
        ],
    )


def test_graph_topological_order_raises_on_a_cycle():
    with pytest.raises(GraphError):
        DataFlowGraph(_cyclic_flow()).topological_order()


def test_cyclic_data_flow_warns_instead_of_crashing():
    result = convert_package(Package(name="CyclicPkg", data_flows=[_cyclic_flow()]))
    assert any("not acyclic" in warning for warning in result.warnings)


# --------------------------------------------------------------------------- #
# Per-sink WITH blocks include only the CTEs that sink actually depends on.
# --------------------------------------------------------------------------- #
def _split_to_two_destinations_package() -> Package:
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Amount FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4"),
                          Column(ref_id="S.out.Amount", name="Amount", data_type="i4")]
    source.outputs = [source_out]

    split = Component(ref_id="C", name="Route", kind=ComponentKind.CONDITIONAL_SPLIT)
    split.inputs = [Port(ref_id="C.in", name="Input")]
    high = Port(ref_id="C.high", name="High")
    high.properties = {"Expression": "[Amount] > 100"}
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


def test_each_sink_with_block_excludes_the_other_branch():
    sql = convert_package(_split_to_two_destinations_package()).sql
    chunks = sql.split("WITH ")
    high = next(c for c in chunks if "INSERT INTO [dbo].[HighTbl]" in c)
    low = next(c for c in chunks if "INSERT INTO [dbo].[LowTbl]" in c)
    assert "[Route_High] AS (" in high and "[Route_Low] AS (" not in high
    assert "[Route_Low] AS (" in low and "[Route_High] AS (" not in low


# --------------------------------------------------------------------------- #
# ConversionResult, empty packages and the no-destination preview.
# --------------------------------------------------------------------------- #
def test_conversion_result_str_returns_the_sql():
    result = convert_package(_minimal_package())
    assert str(result) == result.sql


def test_package_with_no_data_flows_warns():
    result = convert_package(Package(name="EmptyPkg", data_flows=[]))
    assert any("no Data Flow Task" in w for w in result.warnings)


def _source_only_flow() -> DataFlow:
    """A source with output columns but no destination downstream."""
    source = Component(
        ref_id="S", name="Lonely Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]
    return DataFlow(name="DF", ref_id="DF", components=[source], paths=[])


def test_data_flow_without_a_destination_emits_a_select_preview():
    result = convert_package(Package(name="PreviewPkg", data_flows=[_source_only_flow()]))
    assert "SELECT *" in result.sql
    assert any("no destination" in w for w in result.warnings)


def test_header_truncates_a_long_warning_list():
    # 45 column-less sources each raise one warning; the header caps the list at 40.
    sources = []
    for n in range(45):
        src = Component(
            ref_id=f"S{n}", name=f"Src{n}", kind=ComponentKind.OLEDB_SOURCE,
            properties={"SqlCommand": "SELECT 1"},
        )
        src.outputs = [Port(ref_id=f"S{n}.out", name="Output")]   # no columns -> warns
        sources.append(src)
    flow = DataFlow(name="DF", ref_id="DF", components=sources, paths=[])
    result = convert_package(Package(name="NoisyPkg", data_flows=[flow]))
    assert len(result.warnings) > 40
    assert "more." in result.sql


def test_dangling_path_is_warned_about():
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]
    # A path whose endpoints resolve to no real port.
    flow = DataFlow(
        name="DF", ref_id="DF", components=[source],
        paths=[Path(ref_id="dp", name="Dangling", start_id="ghost.out", end_id="ghost.in")],
    )
    result = convert_package(Package(name="DanglePkg", data_flows=[flow]))
    assert any("dangling" in w for w in result.warnings)


def test_connected_error_output_is_warned_about():
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    error_out = Port(ref_id="S.err", name="Error Output")
    error_out.is_error = True
    source.outputs = [source_out, error_out]

    dest = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Errors"},
    )
    dest_in = Port(ref_id="D.in", name="Input")
    dest_in.columns = [Column(name="Id")]
    dest.inputs = [dest_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, dest],
        paths=[Path(ref_id="e", name="e", start_id="S.err", end_id="D.in")],
    )
    result = convert_package(Package(name="ErrOutPkg", data_flows=[flow]))
    assert any("error output" in w for w in result.warnings)

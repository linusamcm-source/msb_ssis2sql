"""Direct unit tests for the grouping transpilers.

Covers AggregateTranspiler (GROUP BY + aggregate functions) and
SortTranspiler (optional DISTINCT, with the ORDER BY stashed for a
destination it feeds directly).
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.grouping import AggregateTranspiler, SortTranspiler


def test_grouping_transpilers_are_registered():
    assert AggregateTranspiler.kinds == (ComponentKind.AGGREGATE,)
    assert SortTranspiler.kinds == (ComponentKind.SORT,)


def _aggregate_package() -> Package:
    """Source -> Aggregate (GROUP BY Region, SUM/COUNT) -> destination."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Region, Amount, OrderId FROM dbo.Sales"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Region", name="Region", data_type="wstr", length=20,
               lineage_id="S.out.Region"),
        Column(ref_id="S.out.Amount", name="Amount", data_type="i4",
               lineage_id="S.out.Amount"),
        Column(ref_id="S.out.OrderId", name="OrderId", data_type="i4",
               lineage_id="S.out.OrderId"),
    ]
    source.outputs = [source_out]

    aggregate = Component(
        ref_id="A", name="Summarise", class_id="Microsoft.Aggregate",
        kind=ComponentKind.AGGREGATE,
    )
    agg_in = Port(ref_id="A.in", name="Input")
    agg_in.columns = [
        Column(name="Region", upstream_lineage_id="S.out.Region"),
        Column(name="Amount", upstream_lineage_id="S.out.Amount"),
        Column(name="OrderId", upstream_lineage_id="S.out.OrderId"),
    ]
    agg_out = Port(ref_id="A.out", name="Aggregate Output")
    # AggregationType: 0=group by, 3=sum, 1=count.
    agg_out.columns = [
        Column(ref_id="A.out.Region", name="Region", data_type="wstr", length=20,
               properties={"AggregationType": "0",
                           "SourceInputColumnLineageID": "S.out.Region"}),
        Column(ref_id="A.out.TotalAmount", name="TotalAmount", data_type="i4",
               properties={"AggregationType": "3",
                           "SourceInputColumnLineageID": "S.out.Amount"}),
        Column(ref_id="A.out.OrderCount", name="OrderCount", data_type="i4",
               properties={"AggregationType": "1",
                           "SourceInputColumnLineageID": "S.out.OrderId"}),
    ]
    aggregate.inputs = [agg_in]
    aggregate.outputs = [agg_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.RegionSummary"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [
        Column(name="Region"), Column(name="TotalAmount"), Column(name="OrderCount"),
    ]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, aggregate, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="A.in"),
            Path(ref_id="p2", name="p2", start_id="A.out", end_id="D.in"),
        ],
    )
    return Package(name="AggPkg", data_flows=[flow])


def test_aggregate_emits_group_by_and_aggregate_functions():
    sql = convert_package(_aggregate_package()).sql
    assert "GROUP BY [Region]" in sql
    assert "SUM([Amount]) AS [TotalAmount]" in sql
    assert "COUNT([OrderId]) AS [OrderCount]" in sql


def _sort_package() -> Package:
    """Source -> Sort (two keys, one descending) -> destination."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Region, NetAmount FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Region", name="Region", data_type="wstr", length=20,
               lineage_id="S.out.Region"),
        Column(ref_id="S.out.NetAmount", name="NetAmount", data_type="i4",
               lineage_id="S.out.NetAmount"),
    ]
    source.outputs = [source_out]

    sort = Component(
        ref_id="So", name="Order Rows", class_id="Microsoft.Sort",
        kind=ComponentKind.SORT,
    )
    sort_in = Port(ref_id="So.in", name="Input")
    # NewSortKeyPosition 1 => first key ascending; -2 => second key descending.
    sort_in.columns = [
        Column(name="Region", upstream_lineage_id="S.out.Region",
               properties={"NewSortKeyPosition": "1"}),
        Column(name="NetAmount", upstream_lineage_id="S.out.NetAmount",
               properties={"NewSortKeyPosition": "-2"}),
    ]
    sort_out = Port(ref_id="So.out", name="Sort Output")
    sort.inputs = [sort_in]
    sort.outputs = [sort_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Ordered"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Region"), Column(name="NetAmount")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, sort, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="So.in"),
            Path(ref_id="p2", name="p2", start_id="So.out", end_id="D.in"),
        ],
    )
    return Package(name="SortPkg", data_flows=[flow])


def test_sort_feeds_order_by_into_the_destination():
    result = convert_package(_sort_package())
    # The destination it feeds applies the stashed ORDER BY.
    assert "ORDER BY [Region] ASC, [NetAmount] DESC" in result.sql
    assert any("intermediate row order is not preserved" in w for w in result.warnings)


def test_sort_with_eliminate_duplicates_emits_select_distinct():
    pkg = _sort_package()
    pkg.data_flows[0].components[1].properties["EliminateDuplicates"] = "true"
    sql = convert_package(pkg).sql
    assert "SELECT DISTINCT" in sql

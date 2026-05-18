"""Direct unit tests for the LookupTranspiler.

An SSIS Lookup is emitted as a LEFT JOIN: the reference query becomes its
own CTE, the matched output joins the upstream relation to it, and a
no-match output (if present) becomes the anti-join.
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.lookup import LookupTranspiler


def test_lookup_transpiler_is_registered():
    assert LookupTranspiler.kinds == (ComponentKind.LOOKUP,)


def _lookup_package(with_nomatch: bool = False) -> Package:
    """Source -> Lookup (CustomerID join key) -> destination."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT OrderId, CustomerID FROM dbo.Orders"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.OrderId", name="OrderId", data_type="i4"),
        Column(ref_id="S.out.CustomerID", name="CustomerID", data_type="i4"),
    ]
    source.outputs = [source_out]

    lookup = Component(
        ref_id="LK", name="Customer Lookup", class_id="Microsoft.Lookup",
        kind=ComponentKind.LOOKUP,
        properties={"SqlCommand": "SELECT CustomerID, CustomerName FROM dbo.DimCustomer"},
    )
    lookup_in = Port(ref_id="LK.in", name="Input")
    lookup_in.columns = [
        Column(name="OrderId", upstream_lineage_id="S.out.OrderId"),
        Column(name="CustomerID", upstream_lineage_id="S.out.CustomerID",
               properties={"JoinToReferenceColumn": "CustomerID"}),
    ]
    match_out = Port(ref_id="LK.match", name="Lookup Match Output")
    match_out.columns = [
        Column(ref_id="LK.match.CustomerName", name="CustomerName", data_type="wstr",
               length=50, properties={"CopyFromReferenceColumn": "CustomerName"}),
    ]
    lookup.inputs = [lookup_in]
    lookup.outputs = [match_out]

    components = [source, lookup]
    paths = [
        Path(ref_id="p1", name="p1", start_id="S.out", end_id="LK.in"),
    ]

    dest_match = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Enriched"},
    )
    dest_match_in = Port(ref_id="D.in", name="Input")
    dest_match_in.columns = [
        Column(name="OrderId"), Column(name="CustomerID"), Column(name="CustomerName"),
    ]
    dest_match.inputs = [dest_match_in]
    components.append(dest_match)
    paths.append(Path(ref_id="p2", name="p2", start_id="LK.match", end_id="D.in"))

    if with_nomatch:
        nomatch_out = Port(ref_id="LK.nomatch", name="Lookup No Match Output")
        lookup.outputs.append(nomatch_out)
        dest_nomatch = Component(
            ref_id="DN", name="DstUnmatched", kind=ComponentKind.OLEDB_DESTINATION,
            properties={"OpenRowset": "dbo.Unmatched"},
        )
        dest_nomatch_in = Port(ref_id="DN.in", name="Input")
        dest_nomatch_in.columns = [Column(name="OrderId"), Column(name="CustomerID")]
        dest_nomatch.inputs = [dest_nomatch_in]
        components.append(dest_nomatch)
        paths.append(Path(ref_id="p3", name="p3", start_id="LK.nomatch", end_id="DN.in"))

    flow = DataFlow(name="DF", ref_id="DF", components=components, paths=paths)
    return Package(name="LookupPkg", data_flows=[flow])


def test_lookup_emits_a_left_join_against_a_reference_cte():
    result = convert_package(_lookup_package())
    sql = result.sql
    # The reference query becomes its own CTE.
    assert "[Customer_Lookup_Ref] AS (" in sql
    assert "SELECT CustomerID, CustomerName FROM dbo.DimCustomer" in sql
    # The match output joins the upstream relation to the reference CTE.
    assert "LEFT JOIN [Customer_Lookup_Ref] AS R" in sql
    assert "ON L.[CustomerID] = R.[CustomerID]" in sql
    # The copied reference column is appended.
    assert "R.[CustomerName] AS [CustomerName]" in sql
    # The LEFT JOIN choice is flagged for review.
    assert any("LEFT JOIN" in w and "INNER JOIN" in w for w in result.warnings)


def test_lookup_no_match_output_becomes_an_anti_join():
    sql = convert_package(_lookup_package(with_nomatch=True)).sql
    assert sql.count("INSERT INTO") == 2
    # The no-match branch filters to rows where the reference key is NULL.
    assert "WHERE R.[CustomerID] IS NULL" in sql
    assert "INSERT INTO [dbo].[Unmatched]" in sql


def test_lookup_without_join_keys_falls_back_to_passthrough():
    """No JoinToReferenceColumn property -> pass-through with a warning."""
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    lookup = Component(
        ref_id="LK", name="Keyless Lookup", kind=ComponentKind.LOOKUP,
        properties={"SqlCommand": "SELECT Id FROM dbo.Ref"},
    )
    lookup_in = Port(ref_id="LK.in", name="Input")
    lookup_in.columns = [Column(name="Id", upstream_lineage_id="S.out.Id")]
    match_out = Port(ref_id="LK.match", name="Lookup Match Output")
    lookup.inputs = [lookup_in]
    lookup.outputs = [match_out]

    dest = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Out"},
    )
    dest_in = Port(ref_id="D.in", name="Input")
    dest_in.columns = [Column(name="Id")]
    dest.inputs = [dest_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, lookup, dest],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="LK.in"),
            Path(ref_id="p2", name="p2", start_id="LK.match", end_id="D.in"),
        ],
    )
    result = convert_package(Package(name="KeylessPkg", data_flows=[flow]))
    # No join is emitted in the data-flow SQL body (the header comment may mention
    # LEFT JOIN generically, so check only the section after the header).
    body = result.sql.split("Data Flow Task")[-1]
    assert "LEFT JOIN" not in body
    assert "INSERT INTO [dbo].[Out]" in body
    assert any("no join keys found" in w for w in result.warnings)

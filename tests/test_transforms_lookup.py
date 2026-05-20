"""Direct unit tests for the LookupTranspiler.

An SSIS Lookup is emitted as a LEFT JOIN: the reference query becomes its
own CTE, the matched output joins the upstream relation to it, and a
no-match output (if present) becomes the anti-join.
"""
from __future__ import annotations

from msb_ssis2sql.generator import convert_package
from msb_ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from msb_ssis2sql.transforms.lookup import LookupTranspiler


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


# --------------------------------------------------------------------------- #
# Lookup edge cases: disconnected lookup, missing outputs, a no-match-only
# lookup, a table-based reference, a placeholder reference and a name collision.
# --------------------------------------------------------------------------- #
def _lookup_into(lookup: Component) -> Package:
    """Source -> ``lookup``; the source exposes one CustomerID column."""
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT CustomerID FROM dbo.Orders"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.CustomerID", name="CustomerID", data_type="i4"),
    ]
    source.outputs = [source_out]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, lookup],
        paths=[Path(ref_id="p1", name="p1", start_id="S.out", end_id="LK.in")],
    )
    return Package(name="LookupEdgePkg", data_flows=[flow])


def _keyed_input() -> Port:
    """An input port carrying a CustomerID join key."""
    port = Port(ref_id="LK.in", name="Input")
    port.columns = [
        Column(name="CustomerID", upstream_lineage_id="S.out.CustomerID",
               properties={"JoinToReferenceColumn": "CustomerID"}),
    ]
    return port


def test_lookup_with_no_input_is_skipped():
    lookup = Component(ref_id="LK", name="Orphan Lookup", kind=ComponentKind.LOOKUP)
    lookup.inputs = [Port(ref_id="LK.in", name="Input")]
    lookup.outputs = [Port(ref_id="LK.match", name="Lookup Match Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[lookup], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Lookup" not in result.sql


def test_lookup_with_no_usable_output_is_skipped():
    lookup = Component(
        ref_id="LK", name="No Output Lookup", kind=ComponentKind.LOOKUP,
        properties={"SqlCommand": "SELECT CustomerID FROM dbo.Ref"},
    )
    lookup.inputs = [_keyed_input()]
    lookup.outputs = []
    result = convert_package(_lookup_into(lookup))
    assert any("no usable output" in w for w in result.warnings)


def test_lookup_with_only_a_no_match_output_uses_it_as_the_match_output():
    lookup = Component(
        ref_id="LK", name="NoMatch Only", kind=ComponentKind.LOOKUP,
        properties={"SqlCommand": "SELECT CustomerID FROM dbo.Ref"},
    )
    lookup.inputs = [_keyed_input()]
    lookup.outputs = [Port(ref_id="LK.match", name="Lookup No Match Output")]
    result = convert_package(_lookup_into(lookup))
    # The lone no-match output is promoted to the match output, so a join is emitted.
    assert "LEFT JOIN" in result.sql


def test_lookup_reference_from_a_table_name():
    lookup = Component(
        ref_id="LK", name="Table Lookup", kind=ComponentKind.LOOKUP,
        properties={"OpenRowset": "dbo.DimCustomer"},      # a table, not a query
    )
    lookup.inputs = [_keyed_input()]
    lookup.outputs = [Port(ref_id="LK.match", name="Lookup Match Output")]
    sql = convert_package(_lookup_into(lookup)).sql
    assert "FROM [dbo].[DimCustomer]" in sql


def test_lookup_without_a_reference_query_or_table_emits_a_placeholder():
    lookup = Component(ref_id="LK", name="Vague Lookup", kind=ComponentKind.LOOKUP)
    lookup.inputs = [_keyed_input()]
    lookup.outputs = [Port(ref_id="LK.match", name="Lookup Match Output")]
    result = convert_package(_lookup_into(lookup))
    assert "lookup reference table" in result.sql
    assert any("no reference query or table" in w for w in result.warnings)


def test_lookup_reference_column_colliding_with_an_upstream_column_warns():
    lookup = Component(
        ref_id="LK", name="Collide Lookup", kind=ComponentKind.LOOKUP,
        properties={"SqlCommand": "SELECT CustomerID FROM dbo.Ref"},
    )
    lookup.inputs = [_keyed_input()]
    match_out = Port(ref_id="LK.match", name="Lookup Match Output")
    # The copied reference column is named the same as an upstream column.
    match_out.columns = [
        Column(ref_id="LK.match.CustomerID", name="CustomerID", data_type="i4",
               properties={"CopyFromReferenceColumn": "CustomerID"}),
    ]
    lookup.outputs = [match_out]
    result = convert_package(_lookup_into(lookup))
    assert any("collides with an upstream column" in w for w in result.warnings)

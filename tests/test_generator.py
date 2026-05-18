"""End-to-end tests for the generator."""
from __future__ import annotations

from ssis2sql.generator import ConvertOptions, convert_file, convert_package
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

"""Direct unit tests for the SourceTranspiler.

Builds small in-memory IR packages and converts them through
``convert_package`` so the SourceTranspiler is exercised in isolation
(only a source feeding a destination, no XML parsing involved).
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.source import SourceTranspiler


def test_source_transpiler_is_registered_for_source_kinds():
    assert ComponentKind.OLEDB_SOURCE in SourceTranspiler.kinds
    assert ComponentKind.FLATFILE_SOURCE in SourceTranspiler.kinds


def _oledb_sql_command_package() -> Package:
    """OLE DB source whose query is an explicit SqlCommand."""
    source = Component(
        ref_id="S", name="Orders Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"AccessMode": "2", "SqlCommand": "SELECT Id, Name FROM dbo.Orders"},
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
    return Package(name="OledbSqlPkg", data_flows=[flow])


def test_oledb_source_wraps_sql_command_as_derived_table():
    sql = convert_package(_oledb_sql_command_package()).sql
    # The source CTE is named after the component.
    assert "WITH [Orders_Src] AS" in sql
    # SqlCommand becomes a derived table the projection selects from.
    assert "SELECT Id, Name FROM dbo.Orders" in sql
    assert ") AS _src" in sql
    assert "[Id]" in sql and "[Name]" in sql


def _oledb_table_package() -> Package:
    """OLE DB source with a table name (OpenRowset) instead of a SqlCommand."""
    source = Component(
        ref_id="S", name="TableSrc", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"AccessMode": "0", "OpenRowset": "dbo.Customers"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Target"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    return Package(name="TablePkg", data_flows=[flow])


def test_oledb_source_reads_table_when_no_sql_command():
    sql = convert_package(_oledb_table_package()).sql
    # OpenRowset table becomes a quoted, qualified FROM.
    assert "FROM [dbo].[Customers]" in sql


def _flat_file_source_package() -> Package:
    """A flat-file source - has no SQL reader, so it is emitted over a staging table."""
    source = Component(
        ref_id="S", name="CsvSrc", class_id="Microsoft.FlatFileSource",
        kind=ComponentKind.FLATFILE_SOURCE,
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Code", name="Code", data_type="wstr", length=10),
    ]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Target"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Code")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    return Package(name="FlatFilePkg", data_flows=[flow])


def test_flat_file_source_emits_staging_table_with_warning():
    result = convert_package(_flat_file_source_package())
    # Emitted as a read of a staging table named after the component.
    assert "FROM [CsvSrc]" in result.sql
    assert any("flat-file source" in w and "staging table" in w for w in result.warnings)


def test_source_with_no_output_columns_warns_and_skips():
    source = Component(
        ref_id="S", name="EmptySrc", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT 1"},
    )
    source.outputs = [Port(ref_id="S.out", name="Output")]  # no columns
    flow = DataFlow(name="DF", ref_id="DF", components=[source], paths=[])
    result = convert_package(Package(name="EmptySrcPkg", data_flows=[flow]))
    assert any("declares no output columns" in w for w in result.warnings)

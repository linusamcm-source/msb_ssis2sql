"""Direct unit tests for the DestinationTranspiler.

An OLE DB destination becomes the terminal ``INSERT INTO ... SELECT ...``;
a flat-file destination has no target table and is emitted as a SELECT.
Column mapping comes from external metadata when present, else by name.
"""
from __future__ import annotations

from ssis2sql.generator import convert_package
from ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from ssis2sql.transforms.destination import DestinationTranspiler


def test_destination_transpiler_is_registered():
    assert ComponentKind.OLEDB_DESTINATION in DestinationTranspiler.kinds
    assert ComponentKind.FLATFILE_DESTINATION in DestinationTranspiler.kinds


def _oledb_destination_package() -> Package:
    """Source -> OLE DB destination, mapping columns by name."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id, Name FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Id", name="Id", data_type="i4"),
        Column(ref_id="S.out.Name", name="Name", data_type="wstr", length=50),
    ]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Load Target", class_id="Microsoft.OLEDBDestination",
        kind=ComponentKind.OLEDB_DESTINATION, properties={"OpenRowset": "dbo.FactTarget"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id"), Column(name="Name")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    return Package(name="DestPkg", data_flows=[flow])


def test_oledb_destination_emits_insert_into_select():
    sql = convert_package(_oledb_destination_package()).sql
    # The OpenRowset table is the INSERT target, quoted and qualified.
    assert "INSERT INTO [dbo].[FactTarget] (" in sql
    # Every mapped column appears in both the target list and the SELECT.
    assert "[Id]" in sql and "[Name]" in sql
    assert "SELECT" in sql
    # The INSERT selects from the upstream source CTE.
    assert "FROM [Src]" in sql
    assert sql.rstrip().endswith(";")


def test_destination_maps_columns_through_external_metadata():
    """When externalMetadataColumnId is set, the target name comes from external columns."""
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT SrcId FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.SrcId", name="SrcId", data_type="i4")]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Target"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    # The input column SrcId maps onto the external (table) column TargetId.
    destination_in.columns = [
        Column(name="SrcId", properties={"externalMetadataColumnId": "emc1"}),
    ]
    destination_in.external_columns = [
        Column(ref_id="emc1", name="TargetId", data_type="i4"),
    ]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    sql = convert_package(Package(name="ExtMapPkg", data_flows=[flow])).sql
    # Source expression SrcId, but the INSERT target is the external column name.
    assert "[SrcId] AS [TargetId]" in sql
    assert "INSERT INTO [dbo].[Target]" in sql


def test_flat_file_destination_emits_a_select_not_an_insert():
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="CsvOut", class_id="Microsoft.FlatFileDestination",
        kind=ComponentKind.FLATFILE_DESTINATION,
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    result = convert_package(Package(name="FlatDestPkg", data_flows=[flow]))
    # A flat-file destination has no table to INSERT into.
    assert "INSERT INTO" not in result.sql
    assert "SELECT" in result.sql
    assert any("flat-file destination" in w and "SELECT" in w for w in result.warnings)


def test_destination_without_target_table_emits_placeholder():
    source = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,  # no OpenRowset
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, destination],
        paths=[Path(ref_id="p", name="p", start_id="S.out", end_id="D.in")],
    )
    result = convert_package(Package(name="NoTablePkg", data_flows=[flow]))
    assert "[UnknownTarget]" in result.sql
    assert any("no target table" in w for w in result.warnings)

"""Direct unit tests for the column-shaping transpilers.

Covers DerivedColumnTranspiler, DataConversionTranspiler and
CopyColumnTranspiler by building minimal source -> transform ->
destination IR packages and converting them through ``convert_package``.
"""
from __future__ import annotations

from msb_ssis2sql.generator import convert_package
from msb_ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from msb_ssis2sql.transforms.column_ops import (
    CopyColumnTranspiler,
    DataConversionTranspiler,
    DerivedColumnTranspiler,
)


def test_column_ops_transpilers_are_registered():
    assert DerivedColumnTranspiler.kinds == (ComponentKind.DERIVED_COLUMN,)
    assert DataConversionTranspiler.kinds == (ComponentKind.DATA_CONVERSION,)
    assert CopyColumnTranspiler.kinds == (ComponentKind.COPY_COLUMN,)


def _source() -> tuple[Component, Port]:
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Amount, Region FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Amount", name="Amount", data_type="i4",
               lineage_id="S.out.Amount"),
        Column(ref_id="S.out.Region", name="Region", data_type="wstr", length=20,
               lineage_id="S.out.Region"),
    ]
    source.outputs = [source_out]
    return source, source_out


def _derived_column_package() -> Package:
    source, _ = _source()

    derived = Component(
        ref_id="C", name="Add Cols", class_id="Microsoft.DerivedColumn",
        kind=ComponentKind.DERIVED_COLUMN,
    )
    derived_in = Port(ref_id="C.in", name="Input")
    derived_in.columns = [
        Column(name="Amount", upstream_lineage_id="S.out.Amount"),
        Column(name="Region", upstream_lineage_id="S.out.Region"),
    ]
    derived_out = Port(ref_id="C.out", name="Output", synchronous_input_id="C.in")
    derived_out.columns = [
        Column(ref_id="C.out.NetAmount", name="NetAmount", data_type="i4",
               lineage_id="C.out.NetAmount",
               properties={"Expression": "[Amount] - [Amount] * 0.10"}),
        Column(ref_id="C.out.RegionUpper", name="RegionUpper", data_type="wstr",
               lineage_id="C.out.RegionUpper",
               properties={"Expression": "UPPER(TRIM([Region]))"}),
    ]
    derived.inputs = [derived_in]
    derived.outputs = [derived_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Out"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [
        Column(name="Amount"), Column(name="Region"),
        Column(name="NetAmount"), Column(name="RegionUpper"),
    ]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, derived, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.out", end_id="D.in"),
        ],
    )
    return Package(name="DerivedPkg", data_flows=[flow])


def test_derived_column_translates_ssis_expressions():
    sql = convert_package(_derived_column_package()).sql
    # Arithmetic SSIS expression -> T-SQL expression.
    assert "([Amount] - ([Amount] * 0.10)) AS [NetAmount]" in sql
    # Nested function call SSIS expression -> T-SQL.
    assert "UPPER(LTRIM(RTRIM([Region]))) AS [RegionUpper]" in sql
    # Upstream columns pass straight through.
    assert "WITH [Src] AS" in sql


def test_derived_column_passes_upstream_columns_through():
    sql = convert_package(_derived_column_package()).sql
    add_cols = sql.split("[Add_Cols] AS (")[1]
    # The derived CTE re-exposes the untouched upstream columns by bare name.
    assert "    [Amount]" in add_cols
    assert "    [Region]" in add_cols


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
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Clean"},
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
    # i4 output column -> CAST(... AS INT).
    assert "CAST([Code] AS INT) AS [CodeNum]" in sql


def _copy_column_package() -> Package:
    source, _ = _source()

    copy = Component(
        ref_id="C", name="Dupe", class_id="Microsoft.CopyColumn",
        kind=ComponentKind.COPY_COLUMN,
    )
    copy_in = Port(ref_id="C.in", name="Input")
    copy_in.columns = [Column(name="Region", upstream_lineage_id="S.out.Region")]
    copy_out = Port(ref_id="C.out", name="Output")
    copy_out.columns = [
        Column(ref_id="C.out.RegionCopy", name="Copy of Region", data_type="wstr",
               length=20, lineage_id="C.out.RegionCopy"),
    ]
    copy.inputs = [copy_in]
    copy.outputs = [copy_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Out"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [
        Column(name="Amount"), Column(name="Region"), Column(name="Copy of Region"),
    ]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, copy, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in"),
            Path(ref_id="p2", name="p2", start_id="C.out", end_id="D.in"),
        ],
    )
    return Package(name="CopyPkg", data_flows=[flow])


def test_copy_column_duplicates_an_upstream_column():
    sql = convert_package(_copy_column_package()).sql
    # "Copy of Region" resolves to [Region] via the name heuristic.
    assert '[Region] AS [Copy of Region]' in sql


# --------------------------------------------------------------------------- #
# Column-op edge cases: pass-through columns, untranslatable expressions,
# expression warnings, unresolved sources and disconnected transforms.
# --------------------------------------------------------------------------- #
def _derived_edge_cases_package() -> Package:
    source, _ = _source()
    derived = Component(ref_id="C", name="Edge Cols", kind=ComponentKind.DERIVED_COLUMN)
    derived_in = Port(ref_id="C.in", name="Input")
    derived_in.columns = [
        Column(name="Amount", upstream_lineage_id="S.out.Amount"),
        Column(name="Region", upstream_lineage_id="S.out.Region"),
    ]
    derived_out = Port(ref_id="C.out", name="Output")
    derived_out.columns = [
        # No Expression property -> the column is a pass-through.
        Column(ref_id="C.out.Region", name="Region", data_type="wstr"),
        # A structurally invalid SSIS expression -> untranslatable, emitted as NULL.
        Column(ref_id="C.out.Broken", name="Broken", data_type="i4",
               properties={"Expression": "[Amount] [Region]"}),
        # An unmapped SSIS function -> a warning, but still translated.
        Column(ref_id="C.out.Widget", name="Widget", data_type="i4",
               properties={"Expression": "WIDGETIZE([Amount])"}),
    ]
    derived.inputs = [derived_in]
    derived.outputs = [derived_out]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, derived],
        paths=[Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in")],
    )
    return Package(name="DerivedEdgePkg", data_flows=[flow])


def test_derived_column_handles_passthrough_untranslatable_and_warning_expressions():
    result = convert_package(_derived_edge_cases_package())
    sql = result.sql
    assert "/* untranslatable SSIS expression: [Amount] [Region] */ NULL" in sql
    assert "WIDGETIZE([Amount])" in sql
    assert any("WIDGETIZE" in w for w in result.warnings)


def test_data_conversion_with_no_input_is_skipped():
    convert = Component(ref_id="C", name="Orphan Convert",
                        kind=ComponentKind.DATA_CONVERSION)
    convert.inputs = [Port(ref_id="C.in", name="Input")]
    convert.outputs = [Port(ref_id="C.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[convert], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Convert" not in result.sql


def test_data_conversion_with_an_unresolved_source_emits_null():
    source, _ = _source()
    convert = Component(ref_id="C", name="Convert", kind=ComponentKind.DATA_CONVERSION)
    convert_in = Port(ref_id="C.in", name="Input")
    convert_in.columns = [Column(name="Amount", upstream_lineage_id="S.out.Amount")]
    convert_out = Port(ref_id="C.out", name="Output")
    # An output column whose source resolves to nothing -> NULL.
    convert_out.columns = [Column(ref_id="C.out.Ghost", name="Ghost", data_type="i4")]
    convert.inputs = [convert_in]
    convert.outputs = [convert_out]
    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, convert],
        paths=[Path(ref_id="p1", name="p1", start_id="S.out", end_id="C.in")],
    )
    sql = convert_package(Package(name="ConvNullPkg", data_flows=[flow])).sql
    assert "NULL AS [Ghost]" in sql


def test_copy_column_with_no_input_is_skipped():
    copy = Component(ref_id="C", name="Orphan Copy", kind=ComponentKind.COPY_COLUMN)
    copy.inputs = [Port(ref_id="C.in", name="Input")]
    copy.outputs = [Port(ref_id="C.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[copy], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Copy" not in result.sql

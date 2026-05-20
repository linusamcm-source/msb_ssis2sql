"""Direct unit tests for the flow / pass-through transpilers.

Covers MulticastTranspiler (one input fans out to many outputs),
RowCountTranspiler (passes rows through, drops the variable assignment),
AuditTranspiler (appends system-context columns) and
PassThroughFallbackTranspiler (untranslatable kinds reduced to a
pass-through with a loud warning).
"""
from __future__ import annotations

from msb_ssis2sql.generator import convert_package
from msb_ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Path, Port
from msb_ssis2sql.transforms.flow import (
    AuditTranspiler,
    MulticastTranspiler,
    PassThroughFallbackTranspiler,
    RowCountTranspiler,
)


def test_flow_transpilers_are_registered():
    assert MulticastTranspiler.kinds == (ComponentKind.MULTICAST,)
    assert RowCountTranspiler.kinds == (ComponentKind.ROW_COUNT,)
    assert AuditTranspiler.kinds == (ComponentKind.AUDIT,)
    # The fallback covers several untranslatable kinds.
    assert ComponentKind.SCRIPT in PassThroughFallbackTranspiler.kinds
    assert ComponentKind.PIVOT in PassThroughFallbackTranspiler.kinds
    assert ComponentKind.UNKNOWN in PassThroughFallbackTranspiler.kinds


def _multicast_package() -> Package:
    """Source -> Multicast -> two identical destinations."""
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
    # Both destinations read the single upstream source CTE - no extra CTE for the multicast.
    assert sql.count("WITH [Src] AS") == 2


def _single_chain(transform: Component, transform_in_id: str, transform_out_id: str) -> Package:
    """Source -> ``transform`` -> destination, all carrying a single Id column."""
    source = Component(
        ref_id="S", name="Src", class_id="Microsoft.OLEDBSource",
        kind=ComponentKind.OLEDB_SOURCE,
        properties={"SqlCommand": "SELECT Id FROM dbo.T"},
    )
    source_out = Port(ref_id="S.out", name="Output")
    source_out.columns = [
        Column(ref_id="S.out.Id", name="Id", data_type="i4", lineage_id="S.out.Id"),
    ]
    source.outputs = [source_out]

    destination = Component(
        ref_id="D", name="Dst", kind=ComponentKind.OLEDB_DESTINATION,
        properties={"OpenRowset": "dbo.Out"},
    )
    destination_in = Port(ref_id="D.in", name="Input")
    destination_in.columns = [Column(name="Id")]
    destination.inputs = [destination_in]

    flow = DataFlow(
        name="DF", ref_id="DF", components=[source, transform, destination],
        paths=[
            Path(ref_id="p1", name="p1", start_id="S.out", end_id=transform_in_id),
            Path(ref_id="p2", name="p2", start_id=transform_out_id, end_id="D.in"),
        ],
    )
    return Package(name="ChainPkg", data_flows=[flow])


def test_row_count_passes_rows_through_and_drops_the_variable():
    row_count = Component(
        ref_id="RC", name="Count Rows", kind=ComponentKind.ROW_COUNT,
        properties={"VariableName": "User::RowsLoaded"},
    )
    row_count.inputs = [Port(ref_id="RC.in", name="Input")]
    row_count.outputs = [Port(ref_id="RC.out", name="Output")]
    result = convert_package(_single_chain(row_count, "RC.in", "RC.out"))
    # Rows pass straight through to the destination unchanged.
    assert "INSERT INTO [dbo].[Out]" in result.sql
    assert any("row count" in w and "no SQL equivalent" in w for w in result.warnings)


def test_audit_appends_system_context_columns():
    audit = Component(ref_id="AU", name="Tag Rows", kind=ComponentKind.AUDIT)
    audit_in = Port(ref_id="AU.in", name="Input")
    audit_in.columns = [Column(name="Id", upstream_lineage_id="S.out.Id")]
    audit_out = Port(ref_id="AU.out", name="Audit Output")
    # AuditType 2 => PackageName; AuditType 6 => UserName.
    audit_out.columns = [
        Column(ref_id="AU.out.PkgName", name="PkgName", data_type="wstr", length=255,
               properties={"AuditType": "2"}),
        Column(ref_id="AU.out.RunBy", name="RunBy", data_type="wstr", length=255,
               properties={"AuditType": "6"}),
    ]
    audit.inputs = [audit_in]
    audit.outputs = [audit_out]

    pkg = _single_chain(audit, "AU.in", "AU.out")
    # The destination needs the appended audit columns.
    pkg.data_flows[0].components[2].inputs[0].columns = [
        Column(name="Id"), Column(name="PkgName"), Column(name="RunBy"),
    ]
    sql = convert_package(pkg).sql
    # AuditType 2 emits the package name as a string literal.
    assert "N'ChainPkg' AS [PkgName]" in sql
    # AuditType 6 emits SUSER_SNAME().
    assert "SUSER_SNAME() AS [RunBy]" in sql


def test_pass_through_fallback_warns_and_preserves_rows_for_a_script():
    script = Component(ref_id="SC", name="Custom Script", kind=ComponentKind.SCRIPT)
    script.inputs = [Port(ref_id="SC.in", name="Input")]
    script.outputs = [Port(ref_id="SC.out", name="Output")]
    result = convert_package(_single_chain(script, "SC.in", "SC.out"))
    # Rows still reach the destination so the rest of the flow resolves.
    assert "INSERT INTO [dbo].[Out]" in result.sql
    assert any(
        "no behaviour-preserving" in w and "pass-through" in w for w in result.warnings
    )


# --------------------------------------------------------------------------- #
# Flow edge cases: disconnected transforms, a Row Count with no variable,
# an Audit with an unrecognised AuditType.
# --------------------------------------------------------------------------- #
def test_multicast_with_no_input_is_skipped():
    multicast = Component(ref_id="M", name="Orphan Cast", kind=ComponentKind.MULTICAST)
    multicast.inputs = [Port(ref_id="M.in", name="Input")]
    multicast.outputs = [Port(ref_id="M.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[multicast], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Cast" not in result.sql


def test_row_count_without_a_variable_still_passes_rows_through():
    row_count = Component(ref_id="RC", name="Count", kind=ComponentKind.ROW_COUNT)
    row_count.inputs = [Port(ref_id="RC.in", name="Input")]
    row_count.outputs = [Port(ref_id="RC.out", name="Output")]
    result = convert_package(_single_chain(row_count, "RC.in", "RC.out"))
    assert "INSERT INTO [dbo].[Out]" in result.sql
    assert any("row count" in w for w in result.warnings)


def test_audit_with_no_input_is_skipped():
    audit = Component(ref_id="AU", name="Orphan Audit", kind=ComponentKind.AUDIT)
    audit.inputs = [Port(ref_id="AU.in", name="Input")]
    audit.outputs = [Port(ref_id="AU.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[audit], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    assert "Orphan_Audit" not in result.sql


def test_audit_with_an_unrecognised_audit_type_emits_null():
    audit = Component(ref_id="AU", name="Tag", kind=ComponentKind.AUDIT)
    audit_in = Port(ref_id="AU.in", name="Input")
    audit_in.columns = [Column(name="Id", upstream_lineage_id="S.out.Id")]
    audit_out = Port(ref_id="AU.out", name="Audit Output")
    audit_out.columns = [
        Column(ref_id="AU.out.Bogus", name="Bogus", data_type="wstr",
               properties={"AuditType": "99"}),    # not a real AuditType
    ]
    audit.inputs = [audit_in]
    audit.outputs = [audit_out]
    pkg = _single_chain(audit, "AU.in", "AU.out")
    pkg.data_flows[0].components[2].inputs[0].columns = [
        Column(name="Id"), Column(name="Bogus"),
    ]
    result = convert_package(pkg)
    assert "NULL AS [Bogus]" in result.sql
    assert any("unrecognised AuditType" in w for w in result.warnings)


def test_pass_through_fallback_with_no_input_is_skipped():
    script = Component(ref_id="SC", name="Orphan Script", kind=ComponentKind.SCRIPT)
    script.inputs = [Port(ref_id="SC.in", name="Input")]
    script.outputs = [Port(ref_id="SC.out", name="Output")]
    flow = DataFlow(name="DF", ref_id="DF", components=[script], paths=[])
    result = convert_package(Package(name="P", data_flows=[flow]))
    # The fallback still warns even when it cannot resolve an upstream.
    assert any("no behaviour-preserving" in w for w in result.warnings)

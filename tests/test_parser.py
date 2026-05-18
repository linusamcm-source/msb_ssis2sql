"""Tests for the .dtsx parser."""
from __future__ import annotations

import pytest

from ssis2sql.errors import ParseError
from ssis2sql.model import ComponentKind
from ssis2sql.parser import parse_string


def _component(package, name):
    for data_flow in package.data_flows:
        for component in data_flow.components:
            if component.name == name:
                return component
    raise AssertionError(f"component {name!r} not found")


def test_package_metadata(example_package):
    assert example_package.name == "CustomerSalesETL"
    assert len(example_package.data_flows) == 2
    assert len(example_package.connection_managers) == 1
    assert example_package.connection_managers[0].name == "LocalDW"
    assert example_package.connection_managers[0].creation_name == "OLEDB"


def test_variables_parsed(example_package):
    assert len(example_package.variables) == 1
    var = example_package.variables[0]
    assert var.namespace == "User"
    assert var.name == "MinThreshold"
    assert var.value == "1000"


def test_execute_sql_task_captured(example_package):
    assert len(example_package.exec_sql_tasks) == 1
    assert "TRUNCATE TABLE dbo.FactSalesEnriched" in example_package.exec_sql_tasks[0]


def test_component_kinds_resolved(example_package):
    expected = {
        "Sales Orders Source": ComponentKind.OLEDB_SOURCE,
        "Enrich Columns": ComponentKind.DERIVED_COLUMN,
        "Customer Lookup": ComponentKind.LOOKUP,
        "Route By Value": ComponentKind.CONDITIONAL_SPLIT,
        "Recombine Branches": ComponentKind.UNION_ALL,
        "Sort Output": ComponentKind.SORT,
        "Aggregate By Region": ComponentKind.AGGREGATE,
        "Load Fact Table": ComponentKind.OLEDB_DESTINATION,
    }
    for name, kind in expected.items():
        assert _component(example_package, name).kind == kind, name


def test_source_columns_and_properties(example_package):
    src = _component(example_package, "Sales Orders Source")
    assert src.property("AccessMode") == "2"
    assert "SELECT OrderID" in src.property("SqlCommand")
    output = src.non_error_outputs()[0]
    assert [c.name for c in output.columns] == [
        "OrderID", "CustomerID", "OrderDate", "Amount", "Region",
    ]
    amount = next(c for c in output.columns if c.name == "Amount")
    assert amount.data_type == "numeric"
    assert amount.precision == 18
    assert amount.scale == 2


def test_derived_column_expression_property(example_package):
    enrich = _component(example_package, "Enrich Columns")
    net = next(c for c in enrich.outputs[0].columns if c.name == "NetAmount")
    assert net.properties["Expression"] == "[Amount] - [Amount] * 0.10"


def test_lookup_join_metadata(example_package):
    lookup = _component(example_package, "Customer Lookup")
    join_col = lookup.inputs[0].columns[0]
    assert join_col.name == "CustomerID"
    assert join_col.properties["JoinToReferenceColumn"] == "CustomerID"


def test_paths_parsed(example_package):
    flow = example_package.data_flows[0]
    assert len(flow.paths) == 9
    assert flow.paths[0].start_id == "SalesSrc.Output"
    assert flow.paths[0].end_id == "Enrich.Input"


def test_parse_string_minimal_package():
    xml = (
        '<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts" '
        'DTS:ObjectName="Tiny"><DTS:Executables/></DTS:Executable>'
    )
    package = parse_string(xml)
    assert package.name == "Tiny"
    assert package.data_flows == []


def test_malformed_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse_string("<not-closed")


def test_non_package_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse_string("<root><child/></root>")

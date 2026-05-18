"""Tests for the data-flow graph: edge building, dangling paths, ordering."""
from __future__ import annotations

import pytest

from ssis2sql.errors import GraphError
from ssis2sql.graph import DataFlowGraph
from ssis2sql.model import Component, ComponentKind, DataFlow, Path, Port


def _component(ref: str, name: str) -> Component:
    comp = Component(ref_id=ref, name=name, kind=ComponentKind.DERIVED_COLUMN)
    comp.inputs = [Port(ref_id=f"{ref}.in", name="Input")]
    comp.outputs = [Port(ref_id=f"{ref}.out", name="Output")]
    return comp


def test_path_with_unresolved_endpoints_is_recorded_as_dangling():
    comp = _component("A", "A")
    flow = DataFlow(
        name="DF", ref_id="DF", components=[comp],
        paths=[Path(ref_id="p", name="Ghost Path",
                    start_id="nonexistent.out", end_id="nonexistent.in")],
    )
    graph = DataFlowGraph(flow)
    assert graph.dangling_paths == ["Ghost Path"]
    assert graph.edges == []


def test_a_self_loop_edge_is_ignored_by_the_topological_sort():
    comp = _component("A", "A")
    # A path from A's own output back into A's own input.
    flow = DataFlow(
        name="DF", ref_id="DF", components=[comp],
        paths=[Path(ref_id="p", name="p", start_id="A.out", end_id="A.in")],
    )
    order = DataFlowGraph(flow).topological_order()
    assert [c.ref_id for c in order] == ["A"]


def test_topological_order_raises_on_a_cycle():
    a = _component("A", "A")
    b = _component("B", "B")
    flow = DataFlow(
        name="Cyclic", ref_id="DF", components=[a, b],
        paths=[
            Path(ref_id="p1", name="p1", start_id="A.out", end_id="B.in"),
            Path(ref_id="p2", name="p2", start_id="B.out", end_id="A.in"),
        ],
    )
    with pytest.raises(GraphError, match="not acyclic"):
        DataFlowGraph(flow).topological_order()

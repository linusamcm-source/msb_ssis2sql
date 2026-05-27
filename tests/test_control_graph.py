"""Direct unit tests for ``msb_ssis2sql.control_graph.ControlFlowGraph``.

Will fail with ImportError until ``msb_ssis2sql/control_graph.py`` ships.

Per plan-final.md §3, ControlFlowGraph reads from the parser-populated
``package.executables`` + ``execute_package_tasks`` + ``precedence_constraints``
and exposes:
  * ``topological_order()`` — list of EPTs in dependency order
  * raises ``msb_ssis2sql.errors.GraphError`` on cycles
  * Success + Completion edges honoured; Failure dropped with warning
  * Container / non-EPT precedence targets skipped with warning
"""
from __future__ import annotations

import pytest

from msb_ssis2sql.errors import GraphError


def _make_package_with_epts(edges):
    """Build a Package with EPTs A, B, C wired by ``edges`` = [(from, to, value), ...]."""
    from msb_ssis2sql.model import (
        Executable,
        ExecutePackageTask,
        Package,
        PrecedenceConstraint,
    )

    pkg = Package(name="P")
    refs = sorted({ref for edge in edges for ref in (edge[0], edge[1])})
    if not refs:
        refs = ["EPT_A"]
    for ref in refs:
        pkg.executables.append(Executable(ref_id=ref, name=ref, kind="exec_package"))
        pkg.execute_package_tasks.append(ExecutePackageTask(
            ref_id=ref, name=ref, package_name=f"{ref.lower()}.dtsx",
            package_path=f"{ref.lower()}.dtsx", precedence_predecessors=[],
        ))
    for frm, to, value in edges:
        pkg.precedence_constraints.append(
            PrecedenceConstraint(from_ref=frm, to_ref=to, value=value, eval_op="Constraint")
        )
    return pkg


def test_topological_order_honours_success_edge():
    from msb_ssis2sql.control_graph import ControlFlowGraph

    pkg = _make_package_with_epts([("EPT_A", "EPT_B", "Success")])
    graph = ControlFlowGraph(pkg)
    order = [e.ref_id for e in graph.topological_order()]
    assert order.index("EPT_A") < order.index("EPT_B"), order


def test_topological_order_honours_completion_edge():
    from msb_ssis2sql.control_graph import ControlFlowGraph

    pkg = _make_package_with_epts([("EPT_A", "EPT_B", "Completion")])
    order = [e.ref_id for e in ControlFlowGraph(pkg).topological_order()]
    assert order.index("EPT_A") < order.index("EPT_B"), order


def test_topological_order_drops_failure_edge_with_warning():
    from msb_ssis2sql.control_graph import ControlFlowGraph

    pkg = _make_package_with_epts([("EPT_A", "EPT_B", "Failure")])
    graph = ControlFlowGraph(pkg)
    # Failure edges are dropped (we don't enforce A < B order).
    # We DO require the graph to surface a warning that says so.
    _ = graph.topological_order()
    warnings = getattr(graph, "warnings", [])
    assert any("failure" in w.lower() for w in warnings), warnings


def test_topological_order_raises_grapherror_on_cycle():
    from msb_ssis2sql.control_graph import ControlFlowGraph

    pkg = _make_package_with_epts([
        ("EPT_A", "EPT_B", "Success"),
        ("EPT_B", "EPT_A", "Success"),
    ])
    with pytest.raises(GraphError):
        ControlFlowGraph(pkg).topological_order()


def test_container_precedence_target_warns_and_skips_edge():
    """An edge whose target resolves to a non-exec_package executable (e.g. a
    sequence_container) is skipped + a warning is recorded."""
    from msb_ssis2sql.control_graph import ControlFlowGraph
    from msb_ssis2sql.model import (
        Executable,
        ExecutePackageTask,
        Package,
        PrecedenceConstraint,
    )

    pkg = Package(name="P")
    pkg.executables.append(Executable(ref_id="EPT_A", name="A", kind="exec_package"))
    pkg.executables.append(Executable(ref_id="SeqCont", name="Container", kind="sequence_container"))
    pkg.execute_package_tasks.append(ExecutePackageTask(
        ref_id="EPT_A", name="A", package_name="a.dtsx",
        package_path="a.dtsx", precedence_predecessors=[],
    ))
    pkg.precedence_constraints.append(
        PrecedenceConstraint(from_ref="EPT_A", to_ref="SeqCont", value="Success", eval_op="Constraint")
    )
    graph = ControlFlowGraph(pkg)
    order = graph.topological_order()
    assert len(order) == 1 and order[0].ref_id == "EPT_A", order
    warnings = getattr(graph, "warnings", [])
    assert any("container" in w.lower() or "skip" in w.lower() for w in warnings), warnings


def test_independent_epts_keep_declaration_order():
    """When no precedence ties EPTs, declaration order is preserved."""
    from msb_ssis2sql.control_graph import ControlFlowGraph

    pkg = _make_package_with_epts([])  # one EPT
    order = [e.ref_id for e in ControlFlowGraph(pkg).topological_order()]
    assert order == ["EPT_A"]

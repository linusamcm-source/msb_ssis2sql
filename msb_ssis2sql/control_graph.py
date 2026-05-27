"""Control-flow graph over EPTs + precedence constraints.

Reads from ``package.executables``, ``execute_package_tasks``, and
``precedence_constraints`` (all populated by the parser).
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from .errors import GraphError

if TYPE_CHECKING:
    from .model import ExecutePackageTask, Package


class ControlFlowGraph:
    """DAG of ExecutePackageTasks connected by precedence constraints."""

    def __init__(self, package: "Package") -> None:
        self._package = package
        self.warnings: list[str] = []

    def topological_order(self) -> list["ExecutePackageTask"]:
        """Return EPTs in topological order, honouring Success + Completion edges.

        Failure edges are dropped with a warning. Edges whose target is not an
        exec_package executable (e.g. a sequence container) are also skipped
        with a warning. Raises ``GraphError`` on cycles.
        """
        pkg = self._package
        ept_by_ref: dict[str, "ExecutePackageTask"] = {
            e.ref_id: e for e in pkg.execute_package_tasks
        }
        kind_by_ref: dict[str, str] = {e.ref_id: e.kind for e in pkg.executables}

        # Build adjacency: edges that count toward ordering.
        in_degree: dict[str, int] = {ref: 0 for ref in ept_by_ref}
        adj: dict[str, list[str]] = defaultdict(list)

        for pc in pkg.precedence_constraints:
            if pc.value == "Failure":
                self.warnings.append(
                    f"precedence failure edge {pc.from_ref!r} -> {pc.to_ref!r} dropped"
                )
                continue

            from_ref = pc.from_ref
            to_ref = pc.to_ref

            # Check if target is a non-EPT (e.g. sequence container).
            to_kind = kind_by_ref.get(to_ref, "")
            if to_ref not in ept_by_ref:
                if to_kind and to_kind != "exec_package":
                    self.warnings.append(
                        f"precedence edge {from_ref!r} -> {to_ref!r} skipped: "
                        f"target is {to_kind!r}, not an exec_package"
                    )
                elif to_ref:
                    self.warnings.append(
                        f"precedence edge {from_ref!r} -> {to_ref!r} skipped: "
                        f"target {to_ref!r} not found in execute_package_tasks"
                    )
                continue

            # Skip edges where the source is also not an EPT (e.g. container).
            if from_ref not in ept_by_ref:
                from_kind = kind_by_ref.get(from_ref, "")
                self.warnings.append(
                    f"precedence edge {from_ref!r} -> {to_ref!r} skipped: "
                    f"source is {from_kind!r}, not an exec_package"
                )
                continue

            if to_ref not in in_degree:
                in_degree[to_ref] = 0
            in_degree[to_ref] += 1
            adj[from_ref].append(to_ref)

        # Kahn's algorithm — preserves declaration order for ties.
        ept_order = list(ept_by_ref.keys())  # declaration order
        queue: deque[str] = deque(
            ref for ref in ept_order if in_degree.get(ref, 0) == 0
        )
        result: list["ExecutePackageTask"] = []
        visited = 0
        while queue:
            ref = queue.popleft()
            if ref in ept_by_ref:
                result.append(ept_by_ref[ref])
                visited += 1
            for nxt in adj.get(ref, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if visited < len(ept_by_ref):
            # Find an edge in the cycle for the pinned message.
            cycle_edge = _find_cycle_edge(ept_by_ref, adj)
            raise GraphError(
                f"cycle detected in control-flow graph: "
                f"{cycle_edge[0]!r} -> {cycle_edge[1]!r}"
            )

        return result


def _find_cycle_edge(
    ept_by_ref: dict[str, "ExecutePackageTask"], adj: dict[str, list[str]]
) -> tuple[str, str]:
    """Return one (from, to) edge that participates in a cycle."""
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def _dfs(node: str) -> tuple[str, str] | None:
        visited.add(node)
        rec_stack.add(node)
        for nxt in adj.get(node, []):
            if nxt not in visited:
                result = _dfs(nxt)
                if result:
                    return result
            elif nxt in rec_stack:
                return (node, nxt)
        rec_stack.discard(node)
        return None

    for ref in ept_by_ref:
        if ref not in visited:
            found = _dfs(ref)
            if found:
                return found
    return ("?", "?")

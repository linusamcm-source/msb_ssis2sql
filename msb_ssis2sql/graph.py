"""The data-flow graph: components as nodes, paths as directed edges.

A Data Flow Task is a DAG. Sources have no incoming edges, destinations no
outgoing edges. The generator walks the components in topological order so that
when a transpiler runs, every relation it depends on already exists.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .errors import GraphError
from .model import Component, DataFlow, Port


@dataclass
class Edge:
    """A directed connection from one component output to one component input."""

    src_component: Component
    src_output: Port
    dst_component: Component
    dst_input: Port
    path_name: str = ""


class DataFlowGraph:
    """Indexed, traversable view over a :class:`~msb_ssis2sql.model.DataFlow`."""

    def __init__(self, data_flow: DataFlow):
        self.data_flow = data_flow
        self.components: list[Component] = list(data_flow.components)

        self._component_by_ref: dict[str, Component] = {}
        self._owner_of_port: dict[str, Component] = {}
        self._port_by_ref: dict[str, Port] = {}
        for comp in self.components:
            if comp.ref_id:
                self._component_by_ref[comp.ref_id] = comp
            for port in (*comp.inputs, *comp.outputs):
                if port.ref_id:
                    self._owner_of_port[port.ref_id] = comp
                    self._port_by_ref[port.ref_id] = port

        self.edges: list[Edge] = []
        self.dangling_paths: list[str] = []
        self._edge_by_input: dict[str, Edge] = {}        # dst input ref_id -> Edge
        self._build_edges()

    def _build_edges(self) -> None:
        for path in self.data_flow.paths:
            src_port = self._port_by_ref.get(path.start_id)
            dst_port = self._port_by_ref.get(path.end_id)
            src_comp = self._owner_of_port.get(path.start_id)
            dst_comp = self._owner_of_port.get(path.end_id)
            if not (src_port and dst_port and src_comp and dst_comp):
                self.dangling_paths.append(path.name or path.ref_id or "<unnamed path>")
                continue
            edge = Edge(
                src_component=src_comp,
                src_output=src_port,
                dst_component=dst_comp,
                dst_input=dst_port,
                path_name=path.name,
            )
            self.edges.append(edge)
            self._edge_by_input[dst_port.ref_id] = edge   # SSIS inputs are 1:1

    def edge_into(self, input_port: Port) -> Edge | None:
        """The single edge feeding a given input port (SSIS inputs are 1:1)."""
        return self._edge_by_input.get(input_port.ref_id)

    def topological_order(self) -> list[Component]:
        """Return components in dependency order. Raises on a cycle."""
        indegree: dict[str, int] = {c.ref_id: 0 for c in self.components}
        adjacency: dict[str, set[str]] = {c.ref_id: set() for c in self.components}

        for edge in self.edges:
            src, dst = edge.src_component.ref_id, edge.dst_component.ref_id
            if src == dst or dst in adjacency[src]:
                continue
            adjacency[src].add(dst)
            indegree[dst] += 1

        # Seed with in-degree-zero nodes, preserving document order for stability.
        queue: deque[Component] = deque(
            c for c in self.components if indegree[c.ref_id] == 0
        )
        ordered: list[Component] = []
        seen: set[str] = set()

        while queue:
            comp = queue.popleft()
            if comp.ref_id in seen:
                continue
            ordered.append(comp)
            seen.add(comp.ref_id)
            for dst_ref in sorted(adjacency[comp.ref_id]):
                indegree[dst_ref] -= 1
                if indegree[dst_ref] == 0:
                    queue.append(self._component_by_ref[dst_ref])

        if len(ordered) != len(self.components):
            stuck = [c.name or c.ref_id for c in self.components if c.ref_id not in seen]
            raise GraphError(
                f"data flow {self.data_flow.name!r} is not acyclic - "
                f"unresolved components: {', '.join(stuck)}"
            )
        return ordered

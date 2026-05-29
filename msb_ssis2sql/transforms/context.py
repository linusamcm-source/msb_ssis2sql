"""The shared build context threaded through one data flow's transpilers.

:class:`BuildContext` owns the relation registry, the CTE accumulator, the
sink list and the warning list for a single data flow. Transpilers never touch
those structures directly; they go through the helper methods so that CTE
naming, column re-exposure and dependency tracking stay consistent.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model import Component, Port
from ..observability import logger
from ..relation import RelColumn, Relation
from .base import sanitise_identifier

if TYPE_CHECKING:
    from ..dialect import TSqlDialect
    from ..generator import ConvertOptions
    from ..graph import DataFlowGraph
    from ..model import Package, Project


@dataclass
class Sink:
    """A terminal statement - an INSERT (or SELECT) produced by a destination."""

    component: Component
    sql: str
    reads_cte: str = ""              # CTE name the sink selects from


class BuildContext:
    """Mutable state threaded through every transpiler for one data flow.

    It owns the relation registry (output port -> :class:`Relation`), the CTE
    accumulator, the sink list, and the warning list. Transpilers never touch
    these structures directly; they go through the helper methods so that CTE
    naming, column re-exposure and provenance tracking stay consistent.
    """

    def __init__(
        self,
        graph: DataFlowGraph,
        package: Package,
        dialect: TSqlDialect,
        options: ConvertOptions,
        project: Project | None = None,
    ) -> None:
        self.graph = graph
        self.package = package
        self.dialect = dialect
        self.options = options
        self.project = project

        self.relations: dict[str, Relation] = {}          # output ref_id -> Relation
        self.ctes: dict[str, str] = {}                     # cte name -> SELECT body (insertion order)
        self.cte_dependencies: dict[str, set[str]] = {}    # cte name -> upstream cte names
        self.sinks: list[Sink] = []
        self.warnings: list[str] = []
        self.referenced_variables: set[tuple[str, str]] = set()

        self._used_names: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # naming & diagnostics
    # ------------------------------------------------------------------ #
    def unique_name(self, hint: str) -> str:
        """Allocate a CTE name unique within this data flow."""
        base = sanitise_identifier(hint)
        seen = self._used_names.get(base, 0)
        self._used_names[base] = seen + 1
        return base if seen == 0 else f"{base}_{seen}"

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            logger.warning(message)

    def quote(self, name: str) -> str:
        return self.dialect.quote(name)

    # ------------------------------------------------------------------ #
    # relation registry
    # ------------------------------------------------------------------ #
    def upstream_relation(self, input_port: Port) -> Relation | None:
        """The relation feeding ``input_port`` (via its incoming path)."""
        edge = self.graph.edge_into(input_port)
        if edge is None:
            return None
        return self.relations.get(edge.src_output.ref_id)

    def single_upstream(self, component: Component) -> Relation | None:
        """The relation feeding a single-input component's first connected input."""
        for inp in component.inputs:
            relation = self.upstream_relation(inp)
            if relation is not None:
                return relation
        return None

    def bind_output(self, output_port: Port, relation: Relation) -> None:
        """Point an output port at an already-built relation (no new CTE)."""
        self.relations[output_port.ref_id] = relation

    def from_clause(self, relation: Relation, alias: str | None = None) -> str:
        ref = self.dialect.quote(relation.name)
        return f"FROM {ref} AS {self.dialect.quote(alias)}" if alias else f"FROM {ref}"

    # ------------------------------------------------------------------ #
    # CTE construction
    # ------------------------------------------------------------------ #
    def _register(
        self,
        component: Component,
        output_port: Port | None,
        name_hint: str | None,
        columns: list[RelColumn],
        body: str,
        depends_on: tuple[Relation, ...],
    ) -> Relation:
        hint = name_hint or component.name
        if not hint:
            assert output_port is not None
            hint = output_port.name
        name = self.unique_name(hint)
        exposed = [
            RelColumn(
                name=col.name,
                expr=self.dialect.quote(col.name),
                data_type=col.data_type,
                lineage_id=col.lineage_id,
            )
            for col in columns
        ]
        relation = Relation(name=name, columns=exposed)
        self.ctes[name] = body
        self.cte_dependencies[name] = {dep.name for dep in depends_on if dep is not None}
        logger.debug(
            "emitted CTE [{}] from {!r} ({} column(s): {})",
            name, component.name, len(exposed),
            ", ".join(c.name for c in exposed),
        )
        if output_port is not None:
            self.relations[output_port.ref_id] = relation
        return relation

    def make_relation(
        self,
        component: Component,
        output_port: Port,
        columns: list[RelColumn],
        from_sql: str,
        where: str | None = None,
        group_by: list[str] | None = None,
        distinct: bool = False,
        name_hint: str | None = None,
        depends_on: tuple[Relation, ...] = (),
    ) -> Relation:
        """Build a relation from a column list and a FROM clause.

        ``columns`` is a list of :class:`RelColumn` whose ``expr`` is valid
        *inside this SELECT*. The returned relation re-exposes them as bare
        names so downstream components can reference them through the CTE.

        ``depends_on`` lists the upstream relations this one reads; the
        generator assembles each sink's ``WITH`` block from those recorded
        edges.
        """
        body = self.render_select(columns, from_sql, where, group_by, distinct)
        return self._register(
            component, output_port, name_hint, columns, body, depends_on
        )

    def emit_raw_cte(
        self,
        component: Component,
        output_port: Port,
        columns: list[RelColumn],
        body_sql: str,
        name_hint: str | None = None,
        depends_on: tuple[Relation, ...] = (),
    ) -> Relation:
        """Register a CTE whose body is supplied verbatim (used by sources)."""
        return self._register(
            component, output_port, name_hint, columns, body_sql, depends_on
        )

    def emit_internal_cte(
        self,
        component: Component,
        columns: list[RelColumn],
        body_sql: str,
        name_hint: str | None = None,
        depends_on: tuple[Relation, ...] = (),
    ) -> Relation:
        """Emit a CTE not bound to any output port (e.g. a lookup reference set)."""
        return self._register(
            component, None, name_hint, columns, body_sql, depends_on
        )

    def render_select(
        self,
        columns: list[RelColumn],
        from_sql: str,
        where: str | None = None,
        group_by: list[str] | None = None,
        distinct: bool = False,
    ) -> str:
        """Render a SELECT body. Trivial pass-through columns drop the alias."""
        keyword = "SELECT DISTINCT" if distinct else "SELECT"
        lines = []
        for col in columns:
            quoted = self.dialect.quote(col.name)
            if col.expr.strip() == quoted:
                lines.append(f"    {quoted}")
            else:
                lines.append(f"    {col.expr} AS {quoted}")
        body = keyword + "\n" + ",\n".join(lines) + "\n" + from_sql.rstrip()
        if where:
            body += "\nWHERE " + where
        if group_by:
            body += "\nGROUP BY " + ", ".join(group_by)
        return body

    # ------------------------------------------------------------------ #
    # sinks & variables
    # ------------------------------------------------------------------ #
    def add_sink(self, sink: Sink) -> None:
        self.sinks.append(sink)

    def make_variable_resolver(self) -> Callable[[str, str], str]:
        """Return a resolver that records every variable an expression touches."""

        def resolve(namespace: str, name: str) -> str:
            self.referenced_variables.add((namespace, name))
            return "@" + sanitise_identifier(name)

        return resolve

    def column_resolver(self, alias: str | None = None) -> Callable[[str], str]:
        """Return a column resolver, optionally qualifying with a table alias."""
        if alias is None:
            return lambda name: self.dialect.quote(name)
        prefix = self.dialect.quote(alias)
        return lambda name: f"{prefix}.{self.dialect.quote(name)}"

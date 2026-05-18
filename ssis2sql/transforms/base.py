"""Transpiler base class, registry, and the shared build context.

A *transpiler* turns one pipeline component into SQL. It reads the relations
its upstream components produced, builds its own, and registers them on the
:class:`BuildContext`. Each transpiler is registered against one or more
:class:`~ssis2sql.model.ComponentKind` values via the :func:`register`
decorator, so adding support for a new component is a self-contained file.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..model import Component, ComponentKind, Port
from ..observability import log_methods, logger
from ..relation import RelColumn, Relation


def sanitise_identifier(name: str) -> str:
    """Reduce an arbitrary SSIS name to a safe bare SQL identifier."""
    cleaned = re.sub(r"\W+", "_", (name or "").strip()).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned or "x"


_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def _scan_context(text: str, pos: int) -> tuple[int, bool]:
    """Parenthesis depth and string-literal state at ``pos`` within ``text``."""
    depth = 0
    in_string = False
    for ch in text[:pos]:
        if in_string:
            if ch == "'":
                in_string = False
        elif ch == "'":
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth, in_string


def strip_trailing_order_by(sql: str) -> tuple[str, bool]:
    """Drop a trailing top-level ORDER BY so a query is valid as a derived table.

    SQL Server rejects ORDER BY inside a derived table or CTE unless TOP / OFFSET
    is present. A source query's ORDER BY only sets buffer order, which set-based
    SQL does not preserve anyway, so removing it is behaviour-safe. ORDER BY
    nested in a sub-select or an ``OVER(...)`` clause (paren depth > 0) is kept.
    """
    cut = -1
    for match in _ORDER_BY_RE.finditer(sql):
        depth, in_string = _scan_context(sql, match.start())
        if depth == 0 and not in_string:
            cut = match.start()
    if cut < 0:
        return sql, False
    return sql[:cut].rstrip().rstrip(";").rstrip(), True


def table_name(component: Component) -> str:
    """The table a source or destination refers to (``OpenRowset``, else ``TableName``)."""
    return (component.property("OpenRowset") or component.property("TableName") or "").strip()


def passthrough_columns(ctx: BuildContext, relation: Relation, alias: str | None = None) -> list:
    """A fresh, mutable copy of a relation's columns for building a new relation.

    With ``alias`` the expressions are table-qualified (``L.[col]``) for a join.
    """
    if alias is None:
        return [RelColumn(c.name, c.expr, c.data_type, c.lineage_id) for c in relation.columns]
    resolve = ctx.column_resolver(alias)
    return [RelColumn(c.name, resolve(c.name), c.data_type, c.lineage_id) for c in relation.columns]


def resolve_source_column(ctx: BuildContext, component: Component, output_col, upstream) -> str:
    """Find the upstream column name an output column derives from.

    Tries the explicit lineage property, then the component's input columns,
    then a couple of name heuristics. Returns ``""`` (and warns) on failure.
    """
    lineage = (
        output_col.properties.get("SourceInputColumnLineageID")
        or output_col.properties.get("copyColumnId")
        or output_col.properties.get("AggregationColumnId")
        or ""
    )
    if lineage:
        match = upstream.find_by_lineage(lineage)
        if match is not None:
            return match.name
        for inp in component.inputs:
            for ic in inp.columns:
                if ic.upstream_lineage_id == lineage and upstream.find(ic.name):
                    return ic.name

    name = output_col.name
    for prefix in ("Copy of ", "Conv_", "Converted ", "cnv_"):
        if name.startswith(prefix) and upstream.find(name[len(prefix):]):
            return name[len(prefix):]
    if upstream.find(name):
        return name

    ctx.warn(
        f"{component.name!r}: could not resolve the source column for [{output_col.name}] "
        f"- emitted NULL"
    )
    return ""


def wrap_sql_command(ctx: BuildContext, component: Component, alias: str) -> str | None:
    """Wrap a component's ``SqlCommand`` as a derived table ``(...) AS alias``.

    Returns ``None`` when there is no SqlCommand. A trailing top-level ORDER BY
    is removed - it is invalid inside a derived table.
    """
    raw = (component.property("SqlCommand") or "").strip().rstrip(";").strip()
    if not raw:
        return None
    cleaned, stripped = strip_trailing_order_by(raw)
    if stripped:
        ctx.warn(
            f"{component.kind.value} {component.name!r}: a trailing ORDER BY was removed from "
            f"its query - it is invalid inside a derived table and set-based SQL does not "
            f"preserve row order"
        )
    indented = "\n".join("    " + line for line in cleaned.splitlines())
    return f"(\n{indented}\n) AS {alias}"


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

    def __init__(self, graph, package, dialect, options):
        self.graph = graph
        self.package = package
        self.dialect = dialect
        self.options = options

        self.relations: dict[str, Relation] = {}          # output ref_id -> Relation
        self.ctes: dict[str, str] = {}                     # cte name -> SELECT body (insertion order)
        self.sinks: list[Sink] = []
        self.warnings: list[str] = []
        self.referenced_variables: set[tuple] = set()
        # output ref_id -> ORDER BY clause, set by Sort, consumed by a destination
        self.sort_orders: dict[str, str] = {}

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
    def _register(self, component, output_port, name_hint, columns, body, emit) -> Relation:
        name = self.unique_name(name_hint or component.name or output_port.name)
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
        if emit:
            self.ctes[name] = body
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
        columns: list,
        from_sql: str,
        where: str | None = None,
        group_by: list | None = None,
        distinct: bool = False,
        name_hint: str | None = None,
        emit: bool = True,
    ) -> Relation:
        """Build a relation from a column list and a FROM clause.

        ``columns`` is a list of :class:`RelColumn` whose ``expr`` is valid
        *inside this SELECT*. The returned relation re-exposes them as bare
        names so downstream components can reference them through the CTE.
        """
        body = self.render_select(columns, from_sql, where, group_by, distinct)
        return self._register(component, output_port, name_hint, columns, body, emit)

    def emit_raw_cte(
        self,
        component: Component,
        output_port: Port,
        columns: list,
        body_sql: str,
        name_hint: str | None = None,
    ) -> Relation:
        """Register a CTE whose body is supplied verbatim (used by sources)."""
        return self._register(component, output_port, name_hint, columns, body_sql, emit=True)

    def emit_internal_cte(
        self,
        component: Component,
        name_hint: str,
        columns: list,
        body_sql: str,
    ) -> Relation:
        """Emit a CTE not bound to any output port (e.g. a lookup reference set)."""
        return self._register(component, None, name_hint, columns, body_sql, emit=True)

    def render_select(self, columns, from_sql, where=None, group_by=None, distinct=False) -> str:
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

    def make_variable_resolver(self):
        """Return a resolver that records every variable an expression touches."""

        def resolve(namespace: str, name: str) -> str:
            self.referenced_variables.add((namespace, name))
            return "@" + sanitise_identifier(name)

        return resolve

    def column_resolver(self, alias: str | None = None):
        """Return a column resolver, optionally qualifying with a table alias."""
        if alias is None:
            return lambda name: self.dialect.quote(name)
        prefix = self.dialect.quote(alias)
        return lambda name: f"{prefix}.{self.dialect.quote(name)}"


# --------------------------------------------------------------------------- #
# transpiler base class + registry
# --------------------------------------------------------------------------- #
class Transpiler(ABC):
    """Base class for a component transpiler."""

    kinds: tuple = ()

    @abstractmethod
    def transpile(self, ctx: BuildContext, component: Component) -> None:
        """Consume the component, registering its relations / sinks on ``ctx``."""
        raise NotImplementedError

    def _single_io(self, ctx: BuildContext, component: Component):
        """Upstream relation and the single output of a one-in/one-out transform.

        Warns and returns ``None`` when the component is not fully connected,
        which lets a transpiler bail with one guard line.
        """
        upstream = ctx.single_upstream(component)
        outputs = component.non_error_outputs()
        if upstream is None or not outputs:
            ctx.warn(f"{component.kind.value} {component.name!r} is not fully connected - skipped")
            return None
        return upstream, outputs[0]


_REGISTRY: dict[ComponentKind, type] = {}


def register(*kinds: ComponentKind):
    """Class decorator: bind a :class:`Transpiler` subclass to component kinds.

    The transpiler is also instrumented with :func:`log_methods`, so every one
    of its methods is traced and any failure is logged with a traceback.
    """

    def decorator(cls):
        cls.kinds = tuple(kinds)
        cls = log_methods(cls)
        for kind in kinds:
            _REGISTRY[kind] = cls
        return cls

    return decorator


def get_transpiler(kind: ComponentKind) -> Transpiler | None:
    """Instantiate the transpiler registered for ``kind``, or ``None``."""
    cls = _REGISTRY.get(kind)
    return cls() if cls is not None else None


def registered_kinds() -> set:
    """The set of component kinds with a registered transpiler."""
    return set(_REGISTRY)

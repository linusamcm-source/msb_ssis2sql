"""Stateless SQL helpers shared across the component transpilers.

These functions carry no per-data-flow state - identifier sanitising, trailing
ORDER BY stripping, table-name resolution, column re-shaping. The mutable build
context lives in :mod:`ssis2sql.transforms.context`; the transpiler base class
and registry in :mod:`ssis2sql.transforms.registry`.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..model import Column, Component, Port
from ..relation import RelColumn, Relation

if TYPE_CHECKING:
    from .context import BuildContext


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


def passthrough_columns(
    ctx: BuildContext, relation: Relation, alias: str | None = None
) -> list[RelColumn]:
    """A fresh, mutable copy of a relation's columns for building a new relation.

    With ``alias`` the expressions are table-qualified (``L.[col]``) for a join.
    """
    if alias is None:
        return [RelColumn(c.name, c.expr, c.data_type, c.lineage_id) for c in relation.columns]
    resolve = ctx.column_resolver(alias)
    return [RelColumn(c.name, resolve(c.name), c.data_type, c.lineage_id) for c in relation.columns]


def merge_column(columns: list[RelColumn], index: dict[str, int], new_col: RelColumn) -> None:
    """Replace a same-named column in ``columns`` in place, or append a new one.

    ``index`` maps a lower-cased column name to its position and is kept in
    sync, so a caller merging many columns never rescans the list.
    """
    key = new_col.name.lower()
    if key in index:
        columns[index[key]] = new_col
    else:
        index[key] = len(columns)
        columns.append(new_col)


def column_mapped_relation(
    ctx: BuildContext,
    component: Component,
    upstream: Relation,
    output: Port,
    make_expr: Callable[[Column], str | None],
) -> Relation:
    """Build the output relation of a synchronous column-shaping transpiler.

    Starts from a pass-through copy of ``upstream``'s columns, then for each
    output column calls ``make_expr(output_column)``: a returned SQL string
    replaces (or appends) that column; ``None`` leaves it out. This is the
    shared skeleton of the Derived Column, Data Conversion and Copy Column
    transpilers, which differ only in how they compute each expression.
    """
    columns = passthrough_columns(ctx, upstream)
    index = {c.name.lower(): i for i, c in enumerate(columns)}
    for oc in output.columns:
        expr = make_expr(oc)
        if expr is None:
            continue
        merge_column(columns, index, RelColumn(oc.name, expr, oc.data_type, oc.lineage_id))
    return ctx.make_relation(
        component, output, columns, ctx.from_clause(upstream),
        name_hint=component.name, depends_on=(upstream,),
    )


def resolve_source_column(
    ctx: BuildContext, component: Component, output_col: Column, upstream: Relation
) -> str:
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

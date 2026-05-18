"""Grouping transpilers: Aggregate and Sort.

Aggregate becomes ``GROUP BY`` plus aggregate functions. Sort becomes an
``ORDER BY`` - but a CTE cannot carry an ``ORDER BY``, so the clause is stashed
on the context and applied by a destination if the Sort feeds one directly
(the only place row order is observable in plain SQL).
"""
from __future__ import annotations

from ..model import Component, ComponentKind
from ..relation import RelColumn
from ..util import to_int
from .base import (
    BuildContext,
    Transpiler,
    passthrough_columns,
    register,
    resolve_source_column,
)

# SSIS Aggregate 'AggregationType' enum (integer form).
_AGG_BY_INT = {0: "groupby", 1: "count", 2: "countdistinct", 3: "sum", 4: "avg", 5: "max", 6: "min"}

# ... and its textual spellings (case / space insensitive).
_AGG_BY_NAME = {
    "groupby": "groupby",
    "count": "count",
    "countall": "countall",
    "countdistinct": "countdistinct",
    "sum": "sum",
    "average": "avg",
    "avg": "avg",
    "minimum": "min",
    "min": "min",
    "maximum": "max",
    "max": "max",
}


@register(ComponentKind.AGGREGATE)
class AggregateTranspiler(Transpiler):
    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        if not output.columns:
            ctx.warn(f"aggregate {component.name!r} lists no output columns - emitted a pass-through")
            ctx.make_relation(
                component, output, passthrough_columns(ctx, upstream),
                ctx.from_clause(upstream), name_hint=component.name,
            )
            return

        input_hint = self._input_hints(component)
        columns: list = []
        group_by: list[str] = []

        for oc in output.columns:
            source = resolve_source_column(ctx, component, oc, upstream)
            agg_raw = oc.properties.get("AggregationType")
            if agg_raw is None and source:
                agg_raw = input_hint.get(source.lower())
            agg = self._normalise(ctx, agg_raw, component, oc)
            expr = self._aggregate_expr(ctx, agg, source)
            if agg == "groupby" and source:
                group_by.append(ctx.quote(source))
            columns.append(RelColumn(oc.name, expr, oc.data_type, oc.lineage_id))

        ctx.make_relation(
            component, output, columns, ctx.from_clause(upstream),
            group_by=group_by or None, name_hint=component.name,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _input_hints(component) -> dict:
        hints: dict = {}
        for inp in component.inputs:
            for ic in inp.columns:
                agg = ic.properties.get("AggregationType")
                if agg is not None and ic.name:
                    hints[ic.name.lower()] = agg
        return hints

    @staticmethod
    def _normalise(ctx, raw, component, output_col) -> str:
        if raw is not None:
            text = str(raw).strip().lower().replace(" ", "")
            if text in _AGG_BY_NAME:
                return _AGG_BY_NAME[text]
            as_int = to_int(raw)
            if as_int is not None and as_int in _AGG_BY_INT:
                return _AGG_BY_INT[as_int]
        ctx.warn(
            f"aggregate {component.name!r}: column [{output_col.name}] has no recognisable "
            f"AggregationType - treated as GROUP BY"
        )
        return "groupby"

    @staticmethod
    def _aggregate_expr(ctx, agg: str, source: str) -> str:
        if agg == "countall":
            return "COUNT(*)"
        if not source:
            if agg in ("count", "countdistinct"):
                return "COUNT(*)"
            return "NULL"
        col = ctx.quote(source)
        return {
            "groupby": col,
            "sum": f"SUM({col})",
            "avg": f"AVG({col})",
            "min": f"MIN({col})",
            "max": f"MAX({col})",
            "count": f"COUNT({col})",
            "countdistinct": f"COUNT(DISTINCT {col})",
        }.get(agg, col)


@register(ComponentKind.SORT)
class SortTranspiler(Transpiler):
    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        eliminate = (component.property("EliminateDuplicates", "") or "").strip().lower()
        distinct = eliminate in ("true", "1", "-1", "yes")

        keys: list = []   # (position, name, descending)
        for inp in component.inputs:
            for ic in inp.columns:
                pos = to_int(
                    ic.properties.get("NewSortKeyPosition")
                    or ic.properties.get("SortKeyPosition")
                )
                if pos:
                    keys.append((abs(pos), ic.name, pos < 0))
        keys.sort(key=lambda k: k[0])

        ctx.make_relation(
            component, output, passthrough_columns(ctx, upstream),
            ctx.from_clause(upstream), distinct=distinct, name_hint=component.name,
        )

        if keys:
            order_by = ", ".join(
                f"{ctx.quote(name)}{' DESC' if desc else ' ASC'}" for _, name, desc in keys
            )
            ctx.sort_orders[output.ref_id] = order_by
            ctx.warn(
                f"sort {component.name!r}: intermediate row order is not preserved through a "
                f"CTE - the ORDER BY is applied only if this Sort feeds a destination directly"
            )

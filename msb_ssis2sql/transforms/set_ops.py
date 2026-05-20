"""Set-operation transpilers: Union All, Merge, and Merge Join.

Union All / Merge combine several inputs vertically (``UNION ALL``); Merge Join
combines two inputs horizontally (``JOIN``). Column correspondence is resolved
by name - the common case, and the only one that survives without the SSIS
designer's explicit mapping metadata.
"""
from __future__ import annotations

from ..model import Component, ComponentKind, Port
from ..relation import RelColumn, Relation
from ..util import to_int
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.UNION_ALL, ComponentKind.MERGE)
class UnionAllTranspiler(Transpiler):
    """Stack every input vertically with ``UNION ALL``."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        outputs = component.non_error_outputs()
        if not outputs:
            ctx.warn(f"union {component.name!r} has no output - skipped")
            return
        output = outputs[0]

        branches: list[Relation] = []
        for inp in component.inputs:
            relation = ctx.upstream_relation(inp)
            if relation is not None:
                branches.append(relation)
        if not branches:
            ctx.warn(f"union {component.name!r} has no connected inputs - skipped")
            return

        out_cols = output.columns or branches[0].columns
        if component.kind == ComponentKind.MERGE:
            ctx.warn(
                f"merge {component.name!r}: emitted as UNION ALL - the interleaved sort order "
                f"of a Merge is not preserved"
            )

        selects: list[str] = []
        for relation in branches:
            projection: list[str] = []
            for oc in out_cols:
                match = relation.find(oc.name)
                if match is not None:
                    expr = ctx.quote(match.name)
                else:
                    ctx.warn(
                        f"union {component.name!r}: branch {relation.name!r} has no column "
                        f"[{oc.name}] - filled with NULL"
                    )
                    expr = "NULL"
                projection.append(f"    {expr} AS {ctx.quote(oc.name)}")
            selects.append("SELECT\n" + ",\n".join(projection) + "\n" + ctx.from_clause(relation))

        body = "\nUNION ALL\n".join(selects)
        columns = [
            RelColumn(oc.name, ctx.quote(oc.name), getattr(oc, "data_type", ""),
                      getattr(oc, "lineage_id", ""))
            for oc in out_cols
        ]
        ctx.emit_raw_cte(
            component, output, columns, body,
            name_hint=component.name, depends_on=tuple(branches),
        )


@register(ComponentKind.MERGE_JOIN)
class MergeJoinTranspiler(Transpiler):
    """Join two sorted inputs. In SQL the sortedness is irrelevant - it is a JOIN."""

    # SSIS Merge Join 'JoinType' property. Verified by warning, not assumed.
    _JOIN_TYPES = {0: "FULL OUTER JOIN", 1: "LEFT OUTER JOIN", 2: "INNER JOIN"}

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        outputs = component.non_error_outputs()
        if not outputs:
            ctx.warn(f"merge join {component.name!r} has no output - skipped")
            return
        output = outputs[0]

        left, right = self._sides(ctx, component)
        if left is None or right is None:
            ctx.warn(f"merge join {component.name!r} needs two connected inputs - skipped")
            return

        join_type = self._JOIN_TYPES.get(to_int(component.property("JoinType"), 2), "INNER JOIN")
        ctx.warn(
            f"merge join {component.name!r}: emitted as {join_type} - verify the join type "
            f"and join keys against the SSIS component"
        )

        keys = self._join_keys(component, left, right)
        if keys:
            on_clause = " AND ".join(
                f"L.{ctx.quote(lk)} = R.{ctx.quote(rk)}" for lk, rk in keys
            )
        else:
            ctx.warn(
                f"merge join {component.name!r}: no join keys resolved - emitted ON 1 = 1 "
                f"(a cross join); supply the keys manually"
            )
            on_clause = "1 = 1"

        from_sql = (
            f"FROM {ctx.quote(left.name)} AS L\n"
            f"{join_type} {ctx.quote(right.name)} AS R ON {on_clause}"
        )
        columns = self._output_columns(ctx, component, output, left, right)
        ctx.make_relation(
            component, output, columns, from_sql,
            name_hint=component.name, depends_on=(left, right),
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _sides(
        ctx: BuildContext, component: Component
    ) -> tuple[Relation | None, Relation | None]:
        left = right = None
        for inp in component.inputs:
            relation = ctx.upstream_relation(inp)
            label = inp.name.lower()
            if "left" in label and left is None:
                left = relation
            elif "right" in label and right is None:
                right = relation
            elif left is None:
                left = relation
            elif right is None:
                right = relation
        return left, right

    @staticmethod
    def _join_keys(
        component: Component, left: Relation, right: Relation
    ) -> list[tuple[str, str]]:
        left_cols = {c.name.lower(): c.name for c in left.columns}
        right_cols = {c.name.lower(): c.name for c in right.columns}
        keys = [(left_cols[k], right_cols[k]) for k in left_cols if k in right_cols]
        limit = to_int(component.property("NumKeyColumns"))
        if limit and 0 < limit < len(keys):
            keys = keys[:limit]
        return keys

    @staticmethod
    def _output_columns(
        ctx: BuildContext,
        component: Component,
        output: Port,
        left: Relation,
        right: Relation,
    ) -> list[RelColumn]:
        columns: list[RelColumn] = []
        if output.columns:
            for oc in output.columns:
                if left.find(oc.name) is not None:
                    columns.append(RelColumn(oc.name, f"L.{ctx.quote(oc.name)}",
                                             oc.data_type, oc.lineage_id))
                elif right.find(oc.name) is not None:
                    columns.append(RelColumn(oc.name, f"R.{ctx.quote(oc.name)}",
                                             oc.data_type, oc.lineage_id))
                else:
                    ctx.warn(
                        f"merge join {component.name!r}: output column [{oc.name}] matches "
                        f"neither input - emitted NULL"
                    )
                    columns.append(RelColumn(oc.name, "NULL", oc.data_type, oc.lineage_id))
            return columns

        # No explicit output column list - take left, then right's extras.
        seen: set[str] = set()
        for col in left.columns:
            columns.append(RelColumn(col.name, f"L.{ctx.quote(col.name)}", col.data_type))
            seen.add(col.name.lower())
        for col in right.columns:
            if col.name.lower() not in seen:
                columns.append(RelColumn(col.name, f"R.{ctx.quote(col.name)}", col.data_type))
        return columns

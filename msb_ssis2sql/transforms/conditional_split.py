"""Conditional Split transpiler.

A Conditional Split routes each row to the *first* output whose condition is
true, in ``EvaluationOrder``; rows matching nothing fall to the default output.
Each output becomes its own CTE: a filtered ``SELECT`` over the upstream
relation. To preserve first-match-wins, output *n*'s WHERE clause negates every
earlier condition.
"""
from __future__ import annotations

from ..errors import ExpressionError
from ..expressions import translate_condition
from ..model import Component, ComponentKind, Port
from ..util import to_int
from .base import passthrough_columns
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.CONDITIONAL_SPLIT)
class ConditionalSplitTranspiler(Transpiler):
    """Conditional Split: one filtered CTE per output, first-match-wins."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return

        resolve_col = ctx.column_resolver()
        resolve_var = ctx.make_variable_resolver()

        # Classify every non-error output as conditional or default.
        conditional: list[tuple[Port, str, int | None, int]] = []  # output, expr, eval order, doc index
        defaults: list[Port] = []
        for idx, out in enumerate(component.non_error_outputs()):
            expr_text = (
                out.properties.get("FriendlyExpression")
                or out.properties.get("Expression")
                or ""
            ).strip()
            if expr_text:
                conditional.append((out, expr_text, to_int(out.properties.get("EvaluationOrder")), idx))
            else:
                defaults.append(out)

        # Order conditional outputs by EvaluationOrder, stable on document order.
        conditional.sort(key=lambda row: (row[2] is None, row[2] if row[2] is not None else 0, row[3]))

        passthrough = passthrough_columns(ctx, upstream)
        from_sql = ctx.from_clause(upstream)
        negations: list[str] = []

        for out, expr_text, _order, _idx in conditional:
            try:
                predicate, warnings = translate_condition(expr_text, resolve_col, resolve_var)
            except ExpressionError as exc:
                ctx.warn(f"conditional split case {out.name!r} in {component.name!r}: {exc}")
                predicate = "1 = 0"
                warnings = []
            for warning in warnings:
                ctx.warn(f"conditional split case {out.name!r}: {warning}")

            where: str | None = " AND ".join([*negations, f"({predicate})"])
            ctx.make_relation(
                component, out, passthrough, from_sql,
                where=where, name_hint=f"{component.name}_{out.name}",
                depends_on=(upstream,),
            )
            negations.append(f"NOT ({predicate})")

        for out in defaults:
            where = " AND ".join(negations) if negations else None
            ctx.make_relation(
                component, out, passthrough, from_sql,
                where=where, name_hint=f"{component.name}_{out.name}",
                depends_on=(upstream,),
            )

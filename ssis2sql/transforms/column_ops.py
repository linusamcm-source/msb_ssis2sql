"""Column-shaping transpilers: Derived Column, Data Conversion, Copy Column.

All three are *synchronous*: they pass every upstream column through untouched
and add (or replace) a handful of computed columns. The output relation is the
upstream relation's columns plus the new ones.
"""
from __future__ import annotations

from ..errors import ExpressionError
from ..expressions import translate_expression
from ..model import Component, ComponentKind
from ..relation import RelColumn
from ..sqltypes import tsql_type_from_column
from .base import (
    BuildContext,
    Transpiler,
    passthrough_columns,
    register,
    resolve_source_column,
)


def _merge_column(columns: list, index: dict, new_col: RelColumn) -> None:
    """Replace a same-named column in place, or append a new one."""
    key = new_col.name.lower()
    if key in index:
        columns[index[key]] = new_col
    else:
        index[key] = len(columns)
        columns.append(new_col)


@register(ComponentKind.DERIVED_COLUMN)
class DerivedColumnTranspiler(Transpiler):
    """Derived Column: each output column carries an SSIS expression."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        columns = passthrough_columns(ctx, upstream)
        index = {c.name.lower(): i for i, c in enumerate(columns)}
        resolve_col = ctx.column_resolver()
        resolve_var = ctx.make_variable_resolver()

        for oc in output.columns:
            expr_text = (
                oc.properties.get("FriendlyExpression")
                or oc.properties.get("Expression")
                or ""
            ).strip()
            if not expr_text:
                continue
            try:
                sql, warnings = translate_expression(expr_text, resolve_col, resolve_var)
            except ExpressionError as exc:
                ctx.warn(f"derived column [{oc.name}] in {component.name!r}: {exc}")
                sql = f"/* untranslatable SSIS expression: {expr_text} */ NULL"
                warnings = []
            for warning in warnings:
                ctx.warn(f"derived column [{oc.name}] in {component.name!r}: {warning}")
            _merge_column(columns, index, RelColumn(oc.name, sql, oc.data_type, oc.lineage_id))

        ctx.make_relation(
            component, output, columns, ctx.from_clause(upstream), name_hint=component.name
        )


@register(ComponentKind.DATA_CONVERSION)
class DataConversionTranspiler(Transpiler):
    """Data Conversion: each output column is a CAST of an input column."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        columns = passthrough_columns(ctx, upstream)
        index = {c.name.lower(): i for i, c in enumerate(columns)}

        for oc in output.columns:
            source = resolve_source_column(ctx, component, oc, upstream)
            target_type = tsql_type_from_column(oc)
            expr = f"CAST({ctx.quote(source)} AS {target_type})" if source else "NULL"
            _merge_column(columns, index, RelColumn(oc.name, expr, oc.data_type, oc.lineage_id))

        ctx.make_relation(
            component, output, columns, ctx.from_clause(upstream), name_hint=component.name
        )


@register(ComponentKind.COPY_COLUMN)
class CopyColumnTranspiler(Transpiler):
    """Copy Column: each output column duplicates an input column verbatim."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        columns = passthrough_columns(ctx, upstream)
        index = {c.name.lower(): i for i, c in enumerate(columns)}

        for oc in output.columns:
            source = resolve_source_column(ctx, component, oc, upstream)
            expr = ctx.quote(source) if source else "NULL"
            _merge_column(columns, index, RelColumn(oc.name, expr, oc.data_type, oc.lineage_id))

        ctx.make_relation(
            component, output, columns, ctx.from_clause(upstream), name_hint=component.name
        )

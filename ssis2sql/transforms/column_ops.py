"""Column-shaping transpilers: Derived Column, Data Conversion, Copy Column.

All three are *synchronous*: they pass every upstream column through untouched
and add (or replace) a handful of computed columns. The output relation is the
upstream relation's columns plus the new ones. The three differ only in how
they compute the expression for one output column; the shared pass-through and
column-merge skeleton lives in
:func:`~ssis2sql.transforms.base.column_mapped_relation`.
"""
from __future__ import annotations

from ..errors import ExpressionError
from ..expressions import translate_expression
from ..model import Column, Component, ComponentKind
from ..sqltypes import tsql_type_from_column
from .base import column_mapped_relation, resolve_source_column
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.DERIVED_COLUMN)
class DerivedColumnTranspiler(Transpiler):
    """Derived Column: each output column carries an SSIS expression."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io
        resolve_col = ctx.column_resolver()
        resolve_var = ctx.make_variable_resolver()

        def make_expr(oc: Column) -> str | None:
            expr_text = (
                oc.properties.get("FriendlyExpression")
                or oc.properties.get("Expression")
                or ""
            ).strip()
            if not expr_text:
                return None                       # no expression - a pass-through column
            try:
                sql, warnings = translate_expression(expr_text, resolve_col, resolve_var)
            except ExpressionError as exc:
                ctx.warn(f"derived column [{oc.name}] in {component.name!r}: {exc}")
                return f"/* untranslatable SSIS expression: {expr_text} */ NULL"
            for warning in warnings:
                ctx.warn(f"derived column [{oc.name}] in {component.name!r}: {warning}")
            return sql

        column_mapped_relation(ctx, component, upstream, output, make_expr)


@register(ComponentKind.DATA_CONVERSION)
class DataConversionTranspiler(Transpiler):
    """Data Conversion: each output column is a CAST of an input column."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        def make_expr(oc: Column) -> str:
            source = resolve_source_column(ctx, component, oc, upstream)
            if not source:
                return "NULL"
            return f"CAST({ctx.quote(source)} AS {tsql_type_from_column(oc)})"

        column_mapped_relation(ctx, component, upstream, output, make_expr)


@register(ComponentKind.COPY_COLUMN)
class CopyColumnTranspiler(Transpiler):
    """Copy Column: each output column duplicates an input column verbatim."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        def make_expr(oc: Column) -> str:
            source = resolve_source_column(ctx, component, oc, upstream)
            return ctx.quote(source) if source else "NULL"

        column_mapped_relation(ctx, component, upstream, output, make_expr)

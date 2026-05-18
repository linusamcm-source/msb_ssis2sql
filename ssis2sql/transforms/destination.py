"""Destination transpilers - the terminal statement of a data flow.

An OLE DB destination becomes an ``INSERT INTO target (...) SELECT ... FROM
<final CTE>``. The input-column-to-target-column mapping is taken from the
external metadata columns when present, and falls back to matching names.
"""
from __future__ import annotations

from ..model import Component, ComponentKind
from .base import BuildContext, Sink, Transpiler, register, table_name


@register(ComponentKind.OLEDB_DESTINATION, ComponentKind.FLATFILE_DESTINATION)
class DestinationTranspiler(Transpiler):
    def transpile(self, ctx: BuildContext, component: Component) -> None:
        upstream = ctx.single_upstream(component)
        if upstream is None:
            ctx.warn(f"destination {component.name!r} has no input - skipped")
            return

        input_port = component.inputs[0] if component.inputs else None
        mapping = self._column_mapping(ctx, component, input_port, upstream)
        if not mapping:
            ctx.warn(
                f"destination {component.name!r}: no column mapping found - "
                f"mapping every upstream column by name"
            )
            mapping = [(c.name, ctx.quote(c.name)) for c in upstream.columns]

        order_by = ""
        if input_port is not None:
            edge = ctx.graph.edge_into(input_port)
            if edge is not None:
                order_by = ctx.sort_orders.get(edge.src_output.ref_id, "")

        if component.kind == ComponentKind.FLATFILE_DESTINATION:
            sql = self._flat_file(ctx, component, upstream, mapping, order_by)
        else:
            sql = self._insert(ctx, component, upstream, mapping, order_by)

        ctx.add_sink(Sink(component=component, sql=sql, reads_cte=upstream.name))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _column_mapping(ctx, component, input_port, upstream) -> list:
        """Return ``[(target_column, source_expression), ...]``."""
        if input_port is None:
            return []
        external = {ec.ref_id: ec for ec in input_port.external_columns if ec.ref_id}
        mapping: list = []
        for ic in input_port.columns:
            source = upstream.find(ic.name)
            source_expr = ctx.quote(source.name) if source is not None else ctx.quote(ic.name)
            emc_id = ic.properties.get("externalMetadataColumnId", "")
            target = external[emc_id].name if emc_id in external else ic.name
            mapping.append((target, source_expr))
        return mapping

    def _insert(self, ctx, component, upstream, mapping, order_by) -> str:
        table = table_name(component)
        if table:
            table_sql = ctx.dialect.quote_qualified(table)
        else:
            ctx.warn(
                f"destination {component.name!r}: no target table (OpenRowset) - "
                f"emitted a placeholder table name"
            )
            table_sql = "[UnknownTarget] /* TODO: set the target table */"

        target_cols = ",\n".join("    " + ctx.quote(target) for target, _ in mapping)
        select_cols = ",\n".join(
            f"    {expr} AS {ctx.quote(target)}" for target, expr in mapping
        )
        sql = (
            f"INSERT INTO {table_sql} (\n{target_cols}\n)\n"
            f"SELECT\n{select_cols}\n"
            f"FROM {ctx.quote(upstream.name)}"
        )
        if order_by:
            sql += f"\nORDER BY {order_by}"
        return sql + ";"

    def _flat_file(self, ctx, component, upstream, mapping, order_by) -> str:
        ctx.warn(
            f"flat-file destination {component.name!r}: emitted as a SELECT - there is no "
            f"target table to INSERT into"
        )
        select_cols = ",\n".join(
            f"    {expr} AS {ctx.quote(target)}" for target, expr in mapping
        )
        sql = f"SELECT\n{select_cols}\nFROM {ctx.quote(upstream.name)}"
        if order_by:
            sql += f"\nORDER BY {order_by}"
        return sql + ";"

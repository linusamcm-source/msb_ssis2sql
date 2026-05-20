"""Source transpilers - the origin of every relation chain.

A source has no upstream relation. It emits the first CTE of a chain: either
the OLE DB source's SQL command (wrapped so its projection is explicit) or a
``SELECT`` over the table named in ``OpenRowset``.
"""
from __future__ import annotations

from ..model import Component, ComponentKind
from ..relation import RelColumn
from .base import table_name, wrap_sql_command
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.OLEDB_SOURCE, ComponentKind.FLATFILE_SOURCE)
class SourceTranspiler(Transpiler):
    """OLE DB / ADO.NET / ODBC / flat-file sources."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        outputs = component.non_error_outputs()
        if not outputs:
            ctx.warn(f"source {component.name!r} exposes no output - skipped")
            return
        output = outputs[0]
        if not output.columns:
            ctx.warn(f"source {component.name!r} declares no output columns - skipped")
            return

        columns = [
            RelColumn(
                name=col.name,
                expr=ctx.quote(col.name),
                data_type=col.data_type,
                lineage_id=col.lineage_id,
            )
            for col in output.columns
        ]
        body = self._source_body(ctx, component, columns)
        ctx.emit_raw_cte(component, output, columns, body, name_hint=component.name or "Source")

    # ------------------------------------------------------------------ #
    def _source_body(self, ctx: BuildContext, component: Component, columns: list) -> str:
        projection = ",\n".join("    " + ctx.quote(c.name) for c in columns)

        if component.kind == ComponentKind.FLATFILE_SOURCE:
            staging = self._connection_name(ctx, component) or component.name or "FlatFileStaging"
            ctx.warn(
                f"flat-file source {component.name!r}: SQL has no flat-file reader - "
                f"emitted as a read of staging table {staging!r}; replace with a real "
                f"staging table or OPENROWSET(BULK ...)"
            )
            return f"SELECT\n{projection}\nFROM {ctx.dialect.quote_qualified(staging)}"

        derived = wrap_sql_command(ctx, component, "_src")
        if derived is not None:
            return f"SELECT\n{projection}\nFROM {derived}"

        table = table_name(component)
        if table:
            return f"SELECT\n{projection}\nFROM {ctx.dialect.quote_qualified(table)}"

        sql_var = component.property("SqlCommandVariable")
        if sql_var:
            ctx.warn(
                f"source {component.name!r} takes its query from variable {sql_var!r} - "
                f"emitted as a placeholder; supply the query manually"
            )
            return f"SELECT\n{projection}\nFROM /* query from variable {sql_var} */ _src"

        ctx.warn(
            f"source {component.name!r} has no SQL command and no table name - "
            f"emitted as a placeholder"
        )
        return f"SELECT\n{projection}\nFROM /* unresolved source */ _src"

    @staticmethod
    def _connection_name(ctx: BuildContext, component: Component) -> str:
        for conn in component.connections:
            for cm in ctx.package.connection_managers:
                if cm.ref_id and cm.ref_id in (
                    conn.connection_manager_ref_id,
                    conn.connection_manager_id,
                ):
                    return cm.name
            if conn.name:
                return conn.name
        return ""

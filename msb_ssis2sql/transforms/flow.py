"""Flow / pass-through transpilers, plus the fallback for untranslatable kinds.

Multicast and Row Count change nothing about the row set, so they reuse their
upstream relation directly. Audit appends system-context columns. Anything with
no behaviour-preserving SQL form (Script, Pivot, OLE DB Command, ...) falls to
the pass-through fallback with a prominent warning.
"""
from __future__ import annotations

from ..model import Component, ComponentKind
from ..relation import RelColumn
from ..sqltypes import sql_string_literal
from .base import passthrough_columns
from .context import BuildContext
from .registry import Transpiler, register


@register(ComponentKind.MULTICAST)
class MulticastTranspiler(Transpiler):
    """One input fans out to many identical outputs - all reuse the upstream relation."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return
        for out in component.non_error_outputs():
            ctx.bind_output(out, upstream)


@register(ComponentKind.ROW_COUNT)
class RowCountTranspiler(Transpiler):
    """Row Count writes a variable and passes every row through unchanged."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return
        variable = component.property("VariableName") or component.property("VariableExpression")
        ctx.warn(
            f"row count {component.name!r}: the row-count assignment"
            + (f" to {variable}" if variable else "")
            + " has no SQL equivalent and was dropped (rows pass through unchanged)"
        )
        for out in component.non_error_outputs():
            ctx.bind_output(out, upstream)


def _audit_expressions(package_name: str) -> dict[str, str]:
    """SSIS Audit 'AuditType' enum -> a T-SQL expression for the audit value."""
    return {
        "0": "CONVERT(NVARCHAR(36), NEWID())",   # ExecutionInstanceGUID
        "1": "CONVERT(NVARCHAR(36), NEWID())",   # PackageID
        "2": sql_string_literal(package_name or "Package"),   # PackageName
        "3": "CONVERT(NVARCHAR(36), NEWID())",   # VersionID
        "4": "SYSDATETIME()",                    # ExecutionStartTime
        "5": "HOST_NAME()",                      # MachineName
        "6": "SUSER_SNAME()",                    # UserName
        "7": "N'Data Flow Task'",                # TaskName
        "8": "CONVERT(NVARCHAR(36), NEWID())",   # TaskID
    }


@register(ComponentKind.AUDIT)
class AuditTranspiler(Transpiler):
    """Audit appends system-context columns (package name, start time, user, ...)."""

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        io = self._single_io(ctx, component)
        if io is None:
            return
        upstream, output = io

        expressions = _audit_expressions(ctx.package.name)
        columns = passthrough_columns(ctx, upstream)
        for oc in output.columns:
            audit_type = (oc.properties.get("AuditType") or "").strip()
            expr = expressions.get(audit_type)
            if expr is None:
                ctx.warn(
                    f"audit {component.name!r}: unrecognised AuditType {audit_type!r} for "
                    f"[{oc.name}] - emitted NULL"
                )
                expr = "NULL"
            columns.append(RelColumn(oc.name, expr, oc.data_type, oc.lineage_id))

        ctx.make_relation(
            component, output, columns, ctx.from_clause(upstream),
            name_hint=component.name, depends_on=(upstream,),
        )


@register(
    ComponentKind.CHARACTER_MAP,
    ComponentKind.PIVOT,
    ComponentKind.UNPIVOT,
    ComponentKind.SCRIPT,
    ComponentKind.SCD,
    ComponentKind.OLEDB_COMMAND,
    ComponentKind.UNKNOWN,
)
class PassThroughFallbackTranspiler(Transpiler):
    """Fallback for components with no behaviour-preserving SQL translation.

    The component is reduced to a pass-through so the remainder of the data
    flow still resolves; a loud warning records exactly what was not translated.
    """

    def transpile(self, ctx: BuildContext, component: Component) -> None:
        ctx.warn(
            f"component {component.name!r} ({component.kind.value}) has no behaviour-preserving "
            f"T-SQL translation - emitted as a pass-through; manual rework required"
        )
        upstream = self._require_upstream(ctx, component)
        if upstream is None:
            return
        for out in component.non_error_outputs():
            ctx.bind_output(out, upstream)

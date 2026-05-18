"""Assemble parsed components into consolidated, behaviour-equivalent T-SQL.

For every data flow the generator runs each component's transpiler in
topological order, then assembles the resulting CTEs into one consolidated
statement per destination: a single ``WITH ... INSERT INTO ... SELECT``. Only
the CTEs a destination actually depends on are included in its ``WITH`` block.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field

from .dialect import TSqlDialect
from .errors import GraphError
from .graph import DataFlowGraph
from .model import DataFlow, Package
from .observability import logged, logger
from .parser import parse_file
from .sqltypes import sql_string_literal
from .transforms import BuildContext, get_transpiler, sanitise_identifier


@dataclass
class ConvertOptions:
    """Knobs for a conversion run."""

    wrap_in_procedure: bool = False
    procedure_name: str = "usp_Migrated_Package"
    include_header: bool = True


@dataclass
class ConversionResult:
    """The output of a conversion: the SQL text plus any warnings raised."""

    sql: str
    warnings: list[str] = field(default_factory=list)
    package: Package | None = None

    def __str__(self) -> str:
        return self.sql


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
@logged
def convert_file(path: str | os.PathLike, options: ConvertOptions | None = None) -> ConversionResult:
    """Parse a .dtsx file and convert it in one call."""
    return convert_package(parse_file(path), options)


@logged
def convert_package(package: Package, options: ConvertOptions | None = None) -> ConversionResult:
    """Convert an already-parsed :class:`~ssis2sql.model.Package`."""
    options = options or ConvertOptions()
    dialect = TSqlDialect()
    logger.info(
        "converting package {!r}: {} data flow(s)", package.name, len(package.data_flows)
    )

    sections: list[str] = []
    warnings: list[str] = []
    referenced_vars: set[tuple[str, str]] = set()

    if not package.data_flows:
        no_flows = "package has no Data Flow Task - there are no transformations to convert"
        warnings.append(no_flows)
        logger.warning(no_flows)

    for data_flow in package.data_flows:
        section, ctx = _convert_data_flow(data_flow, package, dialect, options)
        sections.append(section)
        warnings.extend(ctx.warnings)
        referenced_vars |= set(ctx.referenced_variables)

    sql = _assemble(package, sections, referenced_vars, warnings, options)
    logger.info(
        "conversion complete: {} data flow(s), {} warning(s)",
        len(package.data_flows), len(warnings),
    )
    return ConversionResult(sql=sql, warnings=warnings, package=package)


# --------------------------------------------------------------------------- #
# per-data-flow conversion
# --------------------------------------------------------------------------- #
@logged
def _convert_data_flow(
    data_flow: DataFlow,
    package: Package,
    dialect: TSqlDialect,
    options: ConvertOptions,
) -> tuple[str, BuildContext]:
    """Transpile one data flow; return its rendered section and the build context.

    The caller reads ``ctx.warnings`` and ``ctx.referenced_variables`` off the
    returned context rather than having them unpacked into a positional tuple.
    """
    graph = DataFlowGraph(data_flow)
    ctx = BuildContext(graph, package, dialect, options)
    logger.info(
        "data flow {!r}: {} component(s), {} path(s)",
        data_flow.name, len(data_flow.components), len(data_flow.paths),
    )

    for path_name in graph.dangling_paths:
        ctx.warn(f"data flow {data_flow.name!r}: path {path_name!r} is dangling - ignored")
    for edge in graph.edges:
        if edge.src_output.is_error:
            ctx.warn(
                f"data flow {data_flow.name!r}: the error output of "
                f"{edge.src_component.name!r} is connected - error-row redirection has no "
                f"SQL equivalent and is ignored"
            )

    try:
        order = graph.topological_order()
    except GraphError as exc:
        ctx.warn(str(exc))
        order = list(data_flow.components)

    logger.info("  execution order: {}", " → ".join(c.name or c.ref_id for c in order))

    total = len(order)
    for index, component in enumerate(order, start=1):
        logger.info(
            "  [{}/{}] transpiling {} {!r}",
            index, total, component.kind.value, component.name,
        )
        transpiler = get_transpiler(component.kind)
        if transpiler is None:
            ctx.warn(f"component {component.name!r}: no transpiler for kind {component.kind.value!r}")
            continue
        try:
            transpiler.transpile(ctx, component)
        except Exception as exc:  # noqa: BLE001 - one bad component must not abort the run
            ctx.warn(f"component {component.name!r} failed to transpile: {exc}")

    return _render_data_flow(data_flow, ctx), ctx


def _render_data_flow(data_flow: DataFlow, ctx: BuildContext) -> str:
    rule = "-- " + "=" * 70
    lines = [rule, f"-- Data Flow Task: {data_flow.name}", rule]

    if not ctx.ctes and not ctx.sinks:
        lines.append("-- (no translatable components in this data flow)")
        return "\n".join(lines)

    statements: list[str] = []
    if ctx.sinks:
        for sink in ctx.sinks:
            reachable = _reachable_ctes(ctx, sink.reads_cte)
            statements.append(_with_block(ctx, reachable) + sink.sql)
    else:
        # The early return above guarantees ctx.ctes is non-empty on this path.
        names = list(ctx.ctes)
        ctx.warn(
            f"data flow {data_flow.name!r} has no destination - emitted a SELECT preview "
            f"of {names[-1]!r}"
        )
        statements.append(
            _with_block(ctx, names)
            + f"SELECT *\nFROM {ctx.dialect.quote(names[-1])};"
        )

    logger.info(
        "  data flow {!r} -> {} consolidated statement(s) from {} CTE(s)",
        data_flow.name, len(statements), len(ctx.ctes),
    )
    lines.append("")
    lines.append("\n\n".join(statements))
    return "\n".join(lines)


def _reachable_ctes(ctx: BuildContext, start_cte: str) -> list[str]:
    """CTE names a sink depends on, as the transitive closure of recorded edges.

    Each CTE records the upstream relations it was built from
    (``ctx.cte_dependencies``), so reachability follows real wiring rather than
    a scan of SQL text. ``ctx.ctes`` preserves emission order, a valid
    topological order, so the reachable subset keeps that order.
    """
    if not start_cte or start_cte not in ctx.ctes:
        return list(ctx.ctes)
    reached: set[str] = set()
    stack = [start_cte]
    while stack:
        name = stack.pop()
        if name in reached or name not in ctx.ctes:
            continue
        reached.add(name)
        for dependency in ctx.cte_dependencies.get(name, ()):
            if dependency not in reached:
                stack.append(dependency)
    return [name for name in ctx.ctes if name in reached]


def _with_block(ctx: BuildContext, names: list[str]) -> str:
    if not names:
        return ""
    parts = []
    for name in names:
        indented = _indent(ctx.ctes[name], 1)
        parts.append(f"{ctx.dialect.quote(name)} AS (\n{indented}\n)")
    return "WITH " + ",\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# document assembly
# --------------------------------------------------------------------------- #
def _assemble(
    package: Package,
    sections: list[str],
    referenced_vars: set[tuple[str, str]],
    warnings: list[str],
    options: ConvertOptions,
) -> str:
    blocks: list[str] = []
    if options.include_header:
        blocks.append(_header(package, warnings))

    inner_parts: list[str] = []
    declarations = _declarations(package, referenced_vars)
    if declarations:
        inner_parts.append(declarations)
    exec_sql = _exec_sql_section(package)
    if exec_sql:
        inner_parts.append(exec_sql)
    inner_parts.extend(s for s in sections if s.strip())
    inner = "\n\n\n".join(inner_parts)

    if options.wrap_in_procedure:
        inner = _wrap_procedure(inner, options)
    blocks.append(inner)

    return "\n\n".join(b for b in blocks if b.strip()).rstrip() + "\n"


def _declarations(package: Package, referenced_vars: set[tuple[str, str]]) -> str:
    if not referenced_vars:
        return ""
    by_key = {(v.namespace, v.name): v for v in package.variables}
    lines = [
        "-- " + "-" * 66,
        "-- Package variables referenced by SSIS expressions.",
        "-- Confirm the data types and values before running.",
        "-- " + "-" * 66,
    ]
    for namespace, name in sorted(referenced_vars):
        ident = sanitise_identifier(name)
        var = by_key.get((namespace, name))
        value = (var.value if var else "") or ""
        literal = sql_string_literal(value)
        lines.append(
            f"DECLARE @{ident} NVARCHAR(4000) = {literal};"
            f"  -- SSIS variable {namespace}::{name}"
        )
    return "\n".join(lines)


def _exec_sql_section(package: Package) -> str:
    if not package.exec_sql_tasks:
        return ""
    lines = [
        "-- " + "-" * 66,
        "-- Control-flow Execute SQL Task(s), shown verbatim for completeness.",
        "-- These are NOT data-flow transformations; their order is not modelled.",
        "-- " + "-" * 66,
    ]
    for index, statement in enumerate(package.exec_sql_tasks, start=1):
        lines.append(f"-- [Execute SQL Task {index}]")
        lines.extend("-- " + line for line in statement.splitlines())
    return "\n".join(lines)


def _wrap_procedure(body: str, options: ConvertOptions) -> str:
    return (
        f"CREATE OR ALTER PROCEDURE {options.procedure_name}\n"
        f"AS\n"
        f"BEGIN\n"
        f"    SET NOCOUNT ON;\n\n"
        f"{_indent(body, 1)}\n"
        f"END;\n"
        f"GO"
    )


def _header(package: Package, warnings: list[str]) -> str:
    lines = ["/" + "*" * 74]
    lines.append(" * Generated by ssis2sql - SSIS to consolidated T-SQL transpiler.")
    lines.append(" *")
    lines.append(f" * Source package : {package.name}")
    if package.source_path:
        lines.append(f" * Source file    : {package.source_path}")
    lines.append(f" * Generated       : {_dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f" * Data flow tasks : {len(package.data_flows)}")
    for data_flow in package.data_flows:
        lines.append(f" *   - {data_flow.name} ({len(data_flow.components)} components)")
    lines.append(" *")
    lines.append(" * Behaviour notes:")
    lines.append(" *   - Every data flow becomes one consolidated CTE pipeline per destination.")
    lines.append(" *   - SSIS row-buffer semantics are re-expressed as set-based SQL.")
    lines.append(" *   - Error outputs and Row Count variables have no SQL equivalent and are")
    lines.append(" *     dropped; Lookups are emitted as LEFT JOINs (see warnings).")
    if warnings:
        lines.append(" *")
        lines.append(f" * Conversion warnings ({len(warnings)}) - review before use:")
        for warning in warnings[:40]:
            lines.append(f" *   ! {warning}")
        if len(warnings) > 40:
            lines.append(f" *   ... and {len(warnings) - 40} more.")
    lines.append(" " + "*" * 74 + "/")
    return "\n".join(lines)


def _indent(text: str, levels: int) -> str:
    pad = "    " * levels
    return "\n".join((pad + line) if line.strip() else line for line in text.splitlines())

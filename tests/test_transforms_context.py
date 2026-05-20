"""Tests for ``msb_ssis2sql.transforms.context`` - ``BuildContext`` and ``Sink``.

These exercise the mutable build state threaded through one data flow's
transpilers: unique CTE naming, warning deduplication, CTE registration with
dependency tracking, SELECT rendering, the variable resolver and the column
resolver. Each test wires a minimal :class:`BuildContext` from the IR.
"""
from __future__ import annotations

from msb_ssis2sql.dialect import TSqlDialect
from msb_ssis2sql.generator import ConvertOptions
from msb_ssis2sql.graph import DataFlowGraph
from msb_ssis2sql.model import Column, Component, ComponentKind, DataFlow, Package, Port
from msb_ssis2sql.relation import RelColumn
from msb_ssis2sql.transforms.context import BuildContext, Sink


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _flow_with_one_component() -> tuple[DataFlow, Component, Port]:
    """A one-component data flow plus its component and single output port."""
    out = Port(ref_id="S.out", name="Output")
    out.columns = [Column(ref_id="S.out.Id", name="Id", data_type="i4")]
    component = Component(
        ref_id="S", name="Src", kind=ComponentKind.OLEDB_SOURCE, outputs=[out],
    )
    flow = DataFlow(name="DF", ref_id="DF", components=[component], paths=[])
    return flow, component, out


def _context(flow: DataFlow | None = None) -> BuildContext:
    """A BuildContext over ``flow`` (an empty data flow when none given)."""
    flow = flow or DataFlow(name="DF", ref_id="DF", components=[], paths=[])
    package = Package(name="Pkg", data_flows=[flow])
    return BuildContext(DataFlowGraph(flow), package, TSqlDialect(), ConvertOptions())


# --------------------------------------------------------------------------- #
# Sink dataclass
# --------------------------------------------------------------------------- #
def test_sink_stores_component_sql_and_defaults_reads_cte_to_empty():
    component = Component(name="Dst", kind=ComponentKind.OLEDB_DESTINATION)
    sink = Sink(component=component, sql="INSERT INTO ...")
    assert sink.component is component
    assert sink.sql == "INSERT INTO ..."
    assert sink.reads_cte == ""


def test_sink_accepts_an_explicit_reads_cte():
    sink = Sink(component=Component(name="Dst"), sql="...", reads_cte="Final")
    assert sink.reads_cte == "Final"


# --------------------------------------------------------------------------- #
# constructor wiring
# --------------------------------------------------------------------------- #
def test_build_context_exposes_its_constructor_arguments():
    flow, _, _ = _flow_with_one_component()
    graph = DataFlowGraph(flow)
    package = Package(name="Pkg", data_flows=[flow])
    dialect = TSqlDialect()
    options = ConvertOptions()
    ctx = BuildContext(graph, package, dialect, options)
    assert ctx.graph is graph
    assert ctx.package is package
    assert ctx.dialect is dialect
    assert ctx.options is options


def test_build_context_starts_with_empty_registries():
    ctx = _context()
    assert ctx.relations == {}
    assert ctx.ctes == {}
    assert ctx.cte_dependencies == {}
    assert ctx.sinks == []
    assert ctx.warnings == []
    assert ctx.referenced_variables == set()


# --------------------------------------------------------------------------- #
# unique_name
# --------------------------------------------------------------------------- #
def test_unique_name_returns_the_hint_unchanged_on_first_use():
    ctx = _context()
    assert ctx.unique_name("Source") == "Source"


def test_unique_name_suffixes_repeated_hints():
    ctx = _context()
    assert ctx.unique_name("Source") == "Source"
    assert ctx.unique_name("Source") == "Source_1"
    assert ctx.unique_name("Source") == "Source_2"


def test_unique_name_sanitises_the_hint_before_allocating():
    ctx = _context()
    assert ctx.unique_name("My Cool CTE!") == "My_Cool_CTE"


def test_unique_name_counts_per_sanitised_base():
    ctx = _context()
    # "A B" and "A-B" both sanitise to "A_B" and so collide.
    assert ctx.unique_name("A B") == "A_B"
    assert ctx.unique_name("A-B") == "A_B_1"


# --------------------------------------------------------------------------- #
# warn - deduplication
# --------------------------------------------------------------------------- #
def test_warn_records_a_message():
    ctx = _context()
    ctx.warn("something happened")
    assert ctx.warnings == ["something happened"]


def test_warn_deduplicates_repeated_messages():
    ctx = _context()
    ctx.warn("dup")
    ctx.warn("dup")
    ctx.warn("dup")
    assert ctx.warnings == ["dup"]


def test_warn_keeps_distinct_messages_in_order():
    ctx = _context()
    ctx.warn("first")
    ctx.warn("second")
    assert ctx.warnings == ["first", "second"]


# --------------------------------------------------------------------------- #
# quote
# --------------------------------------------------------------------------- #
def test_quote_delegates_to_the_dialect():
    ctx = _context()
    assert ctx.quote("Orders") == "[Orders]"


# --------------------------------------------------------------------------- #
# render_select
# --------------------------------------------------------------------------- #
def test_render_select_drops_the_alias_for_a_passthrough_column():
    ctx = _context()
    body = ctx.render_select([RelColumn("Id", "[Id]")], "FROM [T]")
    assert body == "SELECT\n    [Id]\nFROM [T]"


def test_render_select_aliases_a_computed_column():
    ctx = _context()
    body = ctx.render_select([RelColumn("Upper", "UPPER([Name])")], "FROM [T]")
    assert "    UPPER([Name]) AS [Upper]" in body


def test_render_select_emits_distinct_keyword():
    ctx = _context()
    body = ctx.render_select([RelColumn("Id", "[Id]")], "FROM [T]", distinct=True)
    assert body.startswith("SELECT DISTINCT\n")


def test_render_select_appends_a_where_clause():
    ctx = _context()
    body = ctx.render_select([RelColumn("Id", "[Id]")], "FROM [T]", where="[Id] > 0")
    assert body.endswith("WHERE [Id] > 0")


def test_render_select_appends_a_group_by_clause():
    ctx = _context()
    body = ctx.render_select(
        [RelColumn("Region", "[Region]")], "FROM [T]", group_by=["[Region]"]
    )
    assert body.endswith("GROUP BY [Region]")


def test_render_select_joins_multiple_group_by_keys():
    ctx = _context()
    body = ctx.render_select(
        [RelColumn("R", "[R]")], "FROM [T]", group_by=["[R]", "[Y]"]
    )
    assert body.endswith("GROUP BY [R], [Y]")


# --------------------------------------------------------------------------- #
# make_relation
# --------------------------------------------------------------------------- #
def test_make_relation_registers_a_cte_and_binds_the_output_port():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    relation = ctx.make_relation(
        component, out, [RelColumn("Id", "[Id]", "i4")], "FROM [T]"
    )
    assert relation.name == "Src"
    assert relation.name in ctx.ctes
    assert ctx.relations[out.ref_id] is relation


def test_make_relation_re_exposes_columns_as_bare_names():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    relation = ctx.make_relation(
        component, out, [RelColumn("Total", "SUM([Amount])")], "FROM [T]"
    )
    # The relation re-exposes the column by name for downstream consumers.
    assert relation.columns[0].name == "Total"
    assert relation.columns[0].expr == "[Total]"


def test_make_relation_records_declared_dependencies():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    upstream = ctx.emit_internal_cte(
        component, [RelColumn("Id", "[Id]")], "SELECT [Id] FROM [T]", name_hint="Up"
    )
    relation = ctx.make_relation(
        component, out, [RelColumn("Id", "[Id]")], "FROM [Up]",
        depends_on=(upstream,),
    )
    assert ctx.cte_dependencies[relation.name] == {"Up"}


def test_make_relation_honours_a_name_hint():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    relation = ctx.make_relation(
        component, out, [RelColumn("Id", "[Id]")], "FROM [T]", name_hint="Custom"
    )
    assert relation.name == "Custom"


# --------------------------------------------------------------------------- #
# emit_raw_cte
# --------------------------------------------------------------------------- #
def test_emit_raw_cte_stores_the_body_verbatim():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    body = "SELECT [Id] FROM dbo.T"
    relation = ctx.emit_raw_cte(component, out, [RelColumn("Id", "[Id]")], body)
    assert ctx.ctes[relation.name] == body
    assert ctx.relations[out.ref_id] is relation


def test_emit_raw_cte_records_dependencies():
    flow, component, out = _flow_with_one_component()
    ctx = _context(flow)
    base = ctx.emit_internal_cte(
        component, [RelColumn("Id", "[Id]")], "SELECT 1", name_hint="Base"
    )
    relation = ctx.emit_raw_cte(
        component, out, [RelColumn("Id", "[Id]")], "SELECT [Id] FROM [Base]",
        depends_on=(base,),
    )
    assert ctx.cte_dependencies[relation.name] == {"Base"}


# --------------------------------------------------------------------------- #
# emit_internal_cte
# --------------------------------------------------------------------------- #
def test_emit_internal_cte_registers_a_cte_bound_to_no_port():
    flow, component, _ = _flow_with_one_component()
    ctx = _context(flow)
    relation = ctx.emit_internal_cte(
        component, [RelColumn("Id", "[Id]")], "SELECT [Id] FROM [Ref]",
        name_hint="Lookup",
    )
    assert relation.name == "Lookup"
    assert ctx.ctes["Lookup"] == "SELECT [Id] FROM [Ref]"
    # Not bound to any output port.
    assert relation not in ctx.relations.values()


def test_emit_internal_cte_records_an_empty_dependency_set_by_default():
    flow, component, _ = _flow_with_one_component()
    ctx = _context(flow)
    relation = ctx.emit_internal_cte(
        component, [RelColumn("Id", "[Id]")], "SELECT 1", name_hint="X"
    )
    assert ctx.cte_dependencies[relation.name] == set()


# --------------------------------------------------------------------------- #
# add_sink
# --------------------------------------------------------------------------- #
def test_add_sink_appends_to_the_sink_list():
    ctx = _context()
    sink = Sink(component=Component(name="Dst"), sql="INSERT ...")
    ctx.add_sink(sink)
    assert ctx.sinks == [sink]


# --------------------------------------------------------------------------- #
# make_variable_resolver
# --------------------------------------------------------------------------- #
def test_make_variable_resolver_returns_an_at_prefixed_identifier():
    ctx = _context()
    resolve = ctx.make_variable_resolver()
    assert resolve("User", "MinThreshold") == "@MinThreshold"


def test_make_variable_resolver_records_each_referenced_variable():
    ctx = _context()
    resolve = ctx.make_variable_resolver()
    resolve("User", "MinThreshold")
    resolve("System", "PackageName")
    assert ctx.referenced_variables == {
        ("User", "MinThreshold"),
        ("System", "PackageName"),
    }


def test_make_variable_resolver_sanitises_the_variable_name():
    ctx = _context()
    resolve = ctx.make_variable_resolver()
    assert resolve("User", "Odd Name!") == "@Odd_Name"


# --------------------------------------------------------------------------- #
# column_resolver
# --------------------------------------------------------------------------- #
def test_column_resolver_without_alias_just_quotes_the_name():
    ctx = _context()
    resolve = ctx.column_resolver()
    assert resolve("Amount") == "[Amount]"


def test_column_resolver_with_alias_qualifies_the_name():
    ctx = _context()
    resolve = ctx.column_resolver("L")
    assert resolve("Amount") == "[L].[Amount]"

"""Phase 3: connection-manager resolution (project fallback) + opt-in qualification."""
from __future__ import annotations

from msb_ssis2sql.dialect import TSqlDialect
from msb_ssis2sql.generator import ConvertOptions
from msb_ssis2sql.graph import DataFlowGraph
from msb_ssis2sql.model import (
    Component,
    Connection,
    ConnectionManager,
    DataFlow,
    Package,
    Project,
)
from msb_ssis2sql.transforms.base import database_from_connection, parse_connection_string
from msb_ssis2sql.transforms.context import BuildContext


def _ctx(package: Package, project: Project | None = None, *, qualify: bool = False) -> BuildContext:
    options = ConvertOptions(qualify_from_connection=qualify)
    return BuildContext(DataFlowGraph(DataFlow()), package, TSqlDialect(), options, project)


def _component_referencing(ref_id: str = "", name: str = "") -> Component:
    return Component(
        name="Src",
        connections=[Connection(connection_manager_ref_id=ref_id, name=name)],
    )


# --------------------------------------------------------------------------- #
# connection-string helpers
# --------------------------------------------------------------------------- #

def test_database_from_connection():
    cs = "Data Source=h;Initial Catalog=Sales;Integrated Security=SSPI;"
    assert database_from_connection(cs) == "Sales"
    assert parse_connection_string(cs)["data source"] == "h"
    assert database_from_connection("Server=h;Database=Stg;") == "Stg"
    assert database_from_connection("Server=h;") == ""


# --------------------------------------------------------------------------- #
# resolution precedence
# --------------------------------------------------------------------------- #

def test_resolves_package_connection_by_ref_id():
    pkg = Package(
        connection_managers=[ConnectionManager(ref_id="cm1", name="PkgConn", creation_name="OLEDB")]
    )
    ctx = _ctx(pkg)
    cm = ctx.resolve_connection_manager(_component_referencing(ref_id="cm1"))
    assert cm is not None and cm.name == "PkgConn"


def test_falls_back_to_project_connection():
    pkg = Package()  # no package-scoped connection managers
    project = Project(
        connection_managers=[
            ConnectionManager(ref_id="proj1", name="StagingDB", scope="project")
        ]
    )
    ctx = _ctx(pkg, project)
    cm = ctx.resolve_connection_manager(_component_referencing(ref_id="proj1"))
    assert cm is not None and cm.name == "StagingDB" and cm.scope == "project"


def test_package_connection_wins_over_project_on_id_clash():
    pkg = Package(connection_managers=[ConnectionManager(ref_id="x", name="Pkg")])
    project = Project(connection_managers=[ConnectionManager(ref_id="x", name="Proj")])
    ctx = _ctx(pkg, project)
    assert ctx.resolve_connection_manager(_component_referencing(ref_id="x")).name == "Pkg"


def test_name_fallback_match():
    project = Project(connection_managers=[ConnectionManager(ref_id="p", name="Shared")])
    ctx = _ctx(Package(), project)
    cm = ctx.resolve_connection_manager(_component_referencing(name="Shared"))
    assert cm is not None and cm.name == "Shared"


def test_unresolved_returns_none():
    ctx = _ctx(Package())
    assert ctx.resolve_connection_manager(_component_referencing(ref_id="nope")) is None


# --------------------------------------------------------------------------- #
# qualified_table
# --------------------------------------------------------------------------- #

def _pkg_with_oledb() -> Package:
    return Package(
        connection_managers=[
            ConnectionManager(
                ref_id="cm1",
                name="SalesDB",
                creation_name="OLEDB",
                connection_string="Data Source=h;Initial Catalog=Sales;",
            )
        ]
    )


def test_qualify_off_is_plain_quoting():
    ctx = _ctx(_pkg_with_oledb(), qualify=False)
    comp = _component_referencing(ref_id="cm1")
    assert ctx.qualified_table(comp, "Customers") == "[Customers]"
    assert ctx.qualified_table(comp, "dbo.Customers") == "[dbo].[Customers]"


def test_qualify_on_prefixes_database():
    ctx = _ctx(_pkg_with_oledb(), qualify=True)
    comp = _component_referencing(ref_id="cm1")
    assert ctx.qualified_table(comp, "Customers") == "[Sales].[dbo].[Customers]"
    assert ctx.qualified_table(comp, "stg.Customers") == "[Sales].[stg].[Customers]"
    # already three-part -> untouched
    assert ctx.qualified_table(comp, "Other.dbo.Customers") == "[Other].[dbo].[Customers]"


def test_qualify_on_without_resolvable_db_is_plain():
    pkg = Package(connection_managers=[ConnectionManager(ref_id="cm1", name="C", connection_string="Server=h;")])
    ctx = _ctx(pkg, qualify=True)
    comp = _component_referencing(ref_id="cm1")
    assert ctx.qualified_table(comp, "Customers") == "[Customers]"

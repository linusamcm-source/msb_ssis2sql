"""Parse a .dtsx (SSIS package) XML document into the :mod:`msb_ssis2sql.model` IR.

Two on-disk formats are handled transparently:

* **Modern** (SQL Server 2012+) - package metadata lives in ``DTS:`` attributes;
  connection managers, variables and executables sit inside wrapper elements;
  pipeline objects use string ``refId`` identifiers.
* **Legacy** (SQL Server 2005/2008) - package metadata lives in child
  ``<DTS:Property DTS:Name="...">`` elements; connection managers, variables
  (``<DTS:PackageVariable>``) and executables are direct children with no
  wrapper; pipeline objects use integer ``id`` identifiers and input columns
  carry only a ``lineageId``, not a name.

Every lookup is namespace-agnostic (matched on local name) and falls back from
the modern form to the legacy form, so one parser serves both.
"""
from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from .component_types import resolve as resolve_kind
from .errors import ParseError
from .observability import logged, logger
from .util import to_int
from .model import (
    Column,
    Component,
    Connection,
    ConnectionManager,
    DataFlow,
    Executable,
    ExecutePackageTask,
    Package,
    Path,
    Port,
    PrecedenceConstraint,
    Variable,
)


# --------------------------------------------------------------------------- #
# namespace-agnostic XML helpers
# --------------------------------------------------------------------------- #
def _local(tag) -> str:
    """Strip any ``{namespace}`` prefix from an element tag or attribute key."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(elem: ET.Element, name: str, default=None):
    """Look up an attribute by local name, ignoring namespace prefixes."""
    if name in elem.attrib:
        return elem.attrib[name]
    for key, value in elem.attrib.items():
        if _local(key) == name:
            return value
    return default


def _prop(elem: ET.Element, name: str, default=None):
    """Read a value as a ``DTS:`` attribute, or a legacy ``<DTS:Property>`` child.

    Legacy packages store package/connection/task metadata as child elements
    ``<DTS:Property DTS:Name="ObjectName">value</DTS:Property>`` rather than as
    attributes; this resolves either shape.
    """
    value = _attr(elem, name, None)
    if value is not None:
        return value
    for child in elem:
        if _local(child.tag) == "Property" and _attr(child, "Name") == name:
            return child.text or ""
    return default


def _ref(elem: ET.Element) -> str:
    """Identity of a component / port / column: modern ``refId`` or legacy ``id``."""
    return _attr(elem, "refId", "") or _attr(elem, "id", "") or ""


def _children(elem: ET.Element, name: str) -> list[ET.Element]:
    """Direct child elements whose local name matches ``name``."""
    return [c for c in elem if _local(c.tag) == name]


def _child(elem: ET.Element | None, name: str) -> ET.Element | None:
    """First direct child element whose local name matches ``name``."""
    if elem is None:
        return None
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
@logged
def parse_file(path: str | pathlib.Path) -> Package:
    """Parse a .dtsx file from disk."""
    path = pathlib.Path(path)
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ParseError(f"{path}: malformed XML - {exc}") from exc
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    package = parse_root(tree.getroot())
    package.source_path = str(path)
    return package


@logged
def parse_string(text: str) -> Package:
    """Parse a .dtsx document held in memory."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"malformed XML - {exc}") from exc
    return parse_root(root)


def parse_root(root: ET.Element) -> Package:
    """Parse from a pre-loaded ``ElementTree`` root element."""
    if _local(root.tag) != "Executable":
        found = next((e for e in root.iter() if _local(e.tag) == "Executable"), None)
        if found is None:
            raise ParseError("no <DTS:Executable> root element found - not a .dtsx package")
        root = found

    package = Package(name=_prop(root, "ObjectName", "Package") or "Package")
    _parse_connection_managers(root, package)
    _parse_variables(root, package)
    _collect_executables(root, package)
    _collect_precedence_constraints(root, package)
    logger.info(
        "parsed package {!r}: {} data flow(s), {} connection manager(s), {} variable(s)",
        package.name,
        len(package.data_flows),
        len(package.connection_managers),
        len(package.variables),
    )
    return package


# --------------------------------------------------------------------------- #
# package-level objects
# --------------------------------------------------------------------------- #
def _parse_connection_managers(root: ET.Element, package: Package) -> None:
    # Modern: <DTS:ConnectionManagers> wrapper. Legacy: bare children.
    wrapper = _child(root, "ConnectionManagers")
    source = wrapper if wrapper is not None else root
    for cm in _children(source, "ConnectionManager"):
        package.connection_managers.append(_parse_connection_manager(cm))


def _parse_connection_manager(elem: ET.Element) -> ConnectionManager:
    name = _prop(elem, "ObjectName", "") or ""
    ref = _ref(elem) or _prop(elem, "DTSID", "") or name
    creation = _prop(elem, "CreationName", "") or ""

    connection_string = ""
    obj_data = _child(elem, "ObjectData")
    if obj_data is not None:
        inner = _child(obj_data, "ConnectionManager")
        if inner is not None:
            connection_string = _prop(inner, "ConnectionString", "") or ""
    if not connection_string:
        connection_string = _prop(elem, "ConnectionString", "") or ""

    return ConnectionManager(
        ref_id=ref,
        name=name,
        creation_name=creation,
        connection_string=connection_string,
    )


def _parse_variables(root: ET.Element, package: Package) -> None:
    wrapper = _child(root, "Variables")
    if wrapper is not None:                                  # modern
        for var in _children(wrapper, "Variable"):
            package.variables.append(_parse_variable(var))
        return
    for var in _children(root, "PackageVariable"):           # legacy
        parsed = _parse_variable(var)
        # dts-designer-1.0 variables are diagram-layout metadata, not real ones.
        if parsed.namespace.lower() != "dts-designer-1.0":
            package.variables.append(parsed)


def _parse_variable(elem: ET.Element) -> Variable:
    namespace = _prop(elem, "Namespace", "User") or "User"
    name = _prop(elem, "ObjectName", "") or ""
    value = ""
    data_type = ""

    value_elem = _child(elem, "VariableValue")
    if value_elem is not None:                               # modern
        value = (value_elem.text or "").strip()
        data_type = _attr(value_elem, "DataType", "") or ""
    else:                                                    # legacy
        legacy_value = _prop(elem, "PackageVariableValue", None)
        if legacy_value is not None:
            value = legacy_value.strip()

    return Variable(namespace=namespace, name=name, value=value, data_type=data_type)


# --------------------------------------------------------------------------- #
# control flow
# --------------------------------------------------------------------------- #
def _executable_kind(ex: ET.Element) -> str:
    exec_type = (_attr(ex, "ExecutableType", "") or "").lower()
    if "pipeline" in exec_type or "dataflow" in exec_type:
        return "data_flow"
    if "executepackagetask" in exec_type or "executesqlpackage" in exec_type:
        return "exec_package"
    if "executesqltask" in exec_type:
        return "exec_sql"
    if "sequence" in exec_type or "stock:sequence" in exec_type:
        return "sequence_container"
    if "foreachloop" in exec_type or "forloop" in exec_type:
        return "sequence_container"
    return "other"


def _collect_executables(parent: ET.Element, package: Package, _top_level: bool = True) -> None:
    """Walk the control flow, picking out data flows and Execute SQL tasks.

    Modern packages nest executables inside a ``<DTS:Executables>`` wrapper;
    legacy packages make them direct children of the containing executable.
    """
    wrapper = _child(parent, "Executables")
    container = wrapper if wrapper is not None else parent
    for ex in _children(container, "Executable"):
        ref_id = _attr(ex, "refId", "") or _attr(ex, "DTSID", "") or _prop(ex, "DTSID", "") or ""
        name = _prop(ex, "ObjectName", "") or ""
        kind = _executable_kind(ex)
        obj_data = _child(ex, "ObjectData")
        pipeline = _child(obj_data, "pipeline") if obj_data is not None else None

        # Only add top-level executables (direct children of the root package).
        if _top_level:
            package.executables.append(Executable(ref_id=ref_id, name=name, kind=kind))

        if pipeline is not None:
            package.data_flows.append(_parse_data_flow(ex, pipeline))
        elif kind == "exec_package":
            ept = _parse_execute_package_task(ex, ref_id, name, obj_data)
            if ept is not None:
                package.execute_package_tasks.append(ept)
        else:
            sql = _extract_exec_sql(obj_data)
            if sql:
                package.exec_sql_tasks.append(sql)
        # Recurse into sequence containers (not top-level for sub-children).
        _collect_executables(ex, package, _top_level=False)


def _parse_execute_package_task(
    ex: ET.Element,
    ref_id: str,
    name: str,
    obj_data: ET.Element | None,
) -> ExecutePackageTask | None:
    if obj_data is None:
        return None
    # Inner element is namespaceless: <ExecutePackageTask>
    ept_elem = _child(obj_data, "ExecutePackageTask")
    if ept_elem is None:
        return None
    pkg_name = ""
    pkg_path = ""
    for child in ept_elem:
        local = _local(child.tag)
        if local == "PackageName":
            pkg_name = (child.text or "").strip()
        elif local == "PackagePath":
            pkg_path = (child.text or "").strip()
        elif local == "PackageNameFromProjectReference":
            pkg_name = pkg_name or (child.text or "").strip()
    return ExecutePackageTask(
        ref_id=ref_id,
        name=name,
        package_name=pkg_name,
        package_path=pkg_path or pkg_name,
        precedence_predecessors=[],
    )


def _collect_precedence_constraints(root: ET.Element, package: Package) -> None:
    """Parse ``<DTS:PrecedenceConstraints>`` from the package root (not nested)."""
    pc_wrapper = _child(root, "PrecedenceConstraints")
    if pc_wrapper is None:
        return
    _value_map = {"0": "Success", "1": "Failure", "2": "Completion"}
    for pc in _children(pc_wrapper, "PrecedenceConstraint"):
        from_ref = _attr(pc, "From", "") or ""
        to_ref = _attr(pc, "To", "") or ""
        raw_value = _attr(pc, "Value", "0") or "0"
        value = _value_map.get(raw_value, "Success")
        eval_op = _attr(pc, "EvalOp", "Constraint") or "Constraint"
        package.precedence_constraints.append(
            PrecedenceConstraint(from_ref=from_ref, to_ref=to_ref, value=value, eval_op=eval_op)
        )


def _extract_exec_sql(obj_data: ET.Element | None) -> str:
    if obj_data is None:
        return ""
    task = _child(obj_data, "SqlTaskData")
    if task is not None:
        return (_attr(task, "SqlStatementSource", "") or "").strip()
    return ""


# --------------------------------------------------------------------------- #
# data flow
# --------------------------------------------------------------------------- #
def _parse_data_flow(executable: ET.Element, pipeline: ET.Element) -> DataFlow:
    name = _prop(executable, "ObjectName", "Data Flow Task") or "Data Flow Task"
    ref = _ref(executable) or _prop(executable, "DTSID", "") or name
    flow = DataFlow(name=name, ref_id=ref)

    components = _child(pipeline, "components")
    if components is not None:
        for comp in _children(components, "component"):
            flow.components.append(_parse_component(comp))

    paths = _child(pipeline, "paths")
    if paths is not None:
        for path in _children(paths, "path"):
            flow.paths.append(
                Path(
                    ref_id=_ref(path),
                    name=_attr(path, "name", "") or "",
                    start_id=_attr(path, "startId", "") or "",
                    end_id=_attr(path, "endId", "") or "",
                )
            )

    _fill_input_column_names(flow)
    return flow


def _fill_input_column_names(flow: DataFlow) -> None:
    """Recover input-column names from lineage ids.

    Legacy input columns reference an upstream column by integer ``lineageId``
    and carry an empty ``name``. Resolve each one against the output column it
    points at so downstream transpilers can address columns by name.
    """
    name_by_lineage: dict[str, str] = {}
    for component in flow.components:
        for output in component.outputs:
            for col in output.columns:
                for key in (col.lineage_id, col.ref_id):
                    if key and key not in name_by_lineage:
                        name_by_lineage[key] = col.name

    for component in flow.components:
        for inp in component.inputs:
            for col in inp.columns:
                if not col.name and col.upstream_lineage_id:
                    col.name = name_by_lineage.get(col.upstream_lineage_id, "")


def _parse_component(elem: ET.Element) -> Component:
    comp = Component(
        ref_id=_ref(elem),
        name=_attr(elem, "name", "") or "",
        class_id=_attr(elem, "componentClassID", "") or "",
        description=_attr(elem, "description", "") or "",
    )
    comp.kind = resolve_kind(comp.class_id)
    comp.properties = _parse_properties(elem)

    connections = _child(elem, "connections")
    if connections is not None:
        for conn in _children(connections, "connection"):
            comp.connections.append(
                Connection(
                    ref_id=_ref(conn),
                    connection_manager_id=_attr(conn, "connectionManagerID", "") or "",
                    connection_manager_ref_id=_attr(conn, "connectionManagerRefId", "") or "",
                    name=_attr(conn, "name", "") or "",
                )
            )

    inputs = _child(elem, "inputs")
    if inputs is not None:
        for inp in _children(inputs, "input"):
            comp.inputs.append(_parse_input(inp))

    outputs = _child(elem, "outputs")
    if outputs is not None:
        for out in _children(outputs, "output"):
            comp.outputs.append(_parse_output(out))

    logger.debug(
        "parsed component {!r}: kind={}, class_id={!r}, {} input(s), {} output(s)",
        comp.name, comp.kind.value, comp.class_id, len(comp.inputs), len(comp.outputs),
    )
    return comp


def _parse_properties(elem: ET.Element) -> dict[str, str]:
    """Read a pipeline ``<properties>`` block into a ``{name: text}`` dict."""
    result: dict[str, str] = {}
    container = _child(elem, "properties")
    if container is None:
        return result
    for prop in _children(container, "property"):
        name = _attr(prop, "name", "")
        if name:
            result[name] = (prop.text or "").strip()
    return result


def _parse_input(elem: ET.Element) -> Port:
    port = Port(
        ref_id=_ref(elem),
        name=_attr(elem, "name", "") or "",
    )
    port.properties = _parse_properties(elem)

    columns = _child(elem, "inputColumns")
    if columns is not None:
        for ic in _children(columns, "inputColumn"):
            col = Column(
                ref_id=_ref(ic),
                name=_attr(ic, "cachedName", "") or _attr(ic, "name", "") or "",
                data_type=_attr(ic, "cachedDataType", "") or "",
                length=to_int(_attr(ic, "cachedLength", "")),
                lineage_id=_attr(ic, "lineageId", "") or "",
                upstream_lineage_id=_attr(ic, "lineageId", "") or "",
                usage_type=_attr(ic, "usageType", "") or "",
            )
            col.properties = _parse_properties(ic)
            emc = _attr(ic, "externalMetadataColumnId", "")
            if emc:
                col.properties["externalMetadataColumnId"] = emc
            port.columns.append(col)

    port.external_columns = _parse_external_columns(elem)
    return port


def _parse_output(elem: ET.Element) -> Port:
    port = Port(
        ref_id=_ref(elem),
        name=_attr(elem, "name", "") or "",
        synchronous_input_id=_attr(elem, "synchronousInputId", "") or "",
        is_error=(_attr(elem, "isErrorOut", "") or "").lower() == "true",
        exclusion_group=to_int(_attr(elem, "exclusionGroup", "")) or 0,
    )
    port.properties = _parse_properties(elem)

    columns = _child(elem, "outputColumns")
    if columns is not None:
        for oc in _children(columns, "outputColumn"):
            col = Column(
                ref_id=_ref(oc),
                name=_attr(oc, "name", "") or "",
                data_type=_attr(oc, "dataType", "") or "",
                length=to_int(_attr(oc, "length", "")),
                precision=to_int(_attr(oc, "precision", "")),
                scale=to_int(_attr(oc, "scale", "")),
                code_page=to_int(_attr(oc, "codePage", "")),
                lineage_id=_attr(oc, "lineageId", "") or _ref(oc),
            )
            col.properties = _parse_properties(oc)
            port.columns.append(col)

    port.external_columns = _parse_external_columns(elem)
    return port


def _parse_external_columns(elem: ET.Element) -> list[Column]:
    """Parse ``<externalMetadataColumns>`` - the shape of a real source/target table."""
    result: list[Column] = []
    container = _child(elem, "externalMetadataColumns")
    if container is None:
        return result
    for ec in _children(container, "externalMetadataColumn"):
        result.append(
            Column(
                ref_id=_ref(ec),
                name=_attr(ec, "name", "") or "",
                data_type=_attr(ec, "dataType", "") or "",
                length=to_int(_attr(ec, "length", "")),
                precision=to_int(_attr(ec, "precision", "")),
                scale=to_int(_attr(ec, "scale", "")),
                code_page=to_int(_attr(ec, "codePage", "")),
            )
        )
    return result

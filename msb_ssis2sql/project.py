"""Parse an expanded SSIS project (the unzipped contents of an ``.ispac``).

A project-deployment ``.ispac`` carries project-scoped artefacts that its
packages reference but do not contain:

* ``Project.params``    — project parameters (``$Project::Name``)
* ``*.conmgr``          — shared (project) connection managers
* ``@Project.manifest`` — protection level, version, the package list

:func:`load_project` reads them into a :class:`~msb_ssis2sql.model.Project`.
The presence of ``@Project.manifest`` is what marks a directory as a project.

Every lookup reuses the namespace-agnostic helpers from :mod:`msb_ssis2sql.parser`,
so the SSIS/DTS XML namespaces are matched on local name and never hard-coded.
"""
from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from .errors import ParseError
from .model import ConnectionManager, Parameter, Project
from .observability import logged, logger
from .parser import (
    _attr,
    _child,
    _children,
    _local,
    _parse_connection_manager,
    truthy,
)

MANIFEST_NAME = "@Project.manifest"
PARAMS_NAME = "Project.params"


def _root(path: pathlib.Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ParseError(f"{path}: malformed XML - {exc}") from exc
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc


def _ssis_properties(elem: ET.Element) -> dict[str, str]:
    """Read an ``<SSIS:Properties>`` block into ``{property-name: text}``."""
    result: dict[str, str] = {}
    container = _child(elem, "Properties")
    if container is None:
        return result
    for prop in _children(container, "Property"):
        name = _attr(prop, "Name")
        if name:
            result[name] = (prop.text or "").strip()
    return result


def parse_project_params(path: pathlib.Path) -> list[Parameter]:
    """Parse ``Project.params`` into a list of project :class:`Parameter`."""
    root = _root(path)
    params: list[Parameter] = []
    for elem in _children(root, "Parameter"):
        name = _attr(elem, "Name", "") or ""
        if not name:
            continue
        props = _ssis_properties(elem)
        params.append(
            Parameter(
                namespace="Project",
                name=name,
                data_type=props.get("DataType", ""),
                value=props.get("Value", ""),
                sensitive=truthy(props.get("Sensitive", "")),
                required=truthy(props.get("Required", "")),
            )
        )
    return params


def parse_conmgr_file(path: pathlib.Path) -> ConnectionManager:
    """Parse a single ``*.conmgr`` file into a project-scoped connection manager."""
    root = _root(path)
    # The file root is itself a <DTS:ConnectionManager>; reuse the package parser.
    cm = _parse_connection_manager(root)
    cm.scope = "project"
    # The on-disk file stem is the canonical connection name when the XML omits it.
    if not cm.name:
        cm.name = path.stem
    return cm


def parse_project_manifest(path: pathlib.Path) -> tuple[str, str, list[str]]:
    """Return ``(project_name, protection_level, package_names)`` from the manifest."""
    root = _root(path)
    protection = _attr(root, "ProtectionLevel", "") or ""
    top_props = _ssis_properties(root)
    name = top_props.get("Name", "")
    if not protection:
        protection = top_props.get("ProtectionLevel", "")

    package_names: list[str] = []
    for info in root.iter():
        if _local(info.tag) != "PackageInfo":
            continue
        pkg_name = _ssis_properties(info).get("Name", "") or _attr(info, "Name", "")
        if pkg_name:
            package_names.append(pkg_name)
    return name, protection, package_names


@logged
def load_project(project_dir: str | pathlib.Path) -> Project | None:
    """Load an expanded project from a directory.

    Returns ``None`` when the directory has no ``@Project.manifest`` (i.e. it is
    not a project-deployment export), so callers can fall back to plain
    per-package conversion.
    """
    project_dir = pathlib.Path(project_dir)
    manifest_path = project_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return None

    name, protection, package_names = parse_project_manifest(manifest_path)

    params: list[Parameter] = []
    params_path = project_dir / PARAMS_NAME
    if params_path.is_file():
        params = parse_project_params(params_path)

    connection_managers = [
        parse_conmgr_file(p) for p in sorted(project_dir.glob("*.conmgr"))
    ]

    project = Project(
        name=name or project_dir.name,
        protection_level=protection,
        parameters=params,
        connection_managers=connection_managers,
        package_names=package_names,
        source_dir=str(project_dir),
    )
    logger.info(
        "loaded project {!r}: protection={!r}, {} param(s), {} project connection(s)",
        project.name, project.protection_level,
        len(project.parameters), len(project.connection_managers),
    )
    return project

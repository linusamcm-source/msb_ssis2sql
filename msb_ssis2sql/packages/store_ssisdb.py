"""SSIS catalog store: ``SSISDB.catalog.*``.

Catalog packages live inside ``.ispac`` *project* archives (a zip of ``.dtsx``
plus ``.params`` / ``.conmgr`` members). We enumerate every package, fetch each
project's binary once via ``catalog.get_project``, and unzip the ``.dtsx``
members back out.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any

from .model import ExtractedPackage

ENUMERATE_SQL = (
    "SELECT f.name, prj.name, pkg.name "
    "FROM SSISDB.catalog.folders f "
    "JOIN SSISDB.catalog.projects prj ON prj.folder_id = f.folder_id "
    "JOIN SSISDB.catalog.packages pkg ON pkg.project_id = prj.project_id "
    "ORDER BY f.name, prj.name, pkg.name"
)

# Parameterised — folder/project names are bound, never interpolated.
GET_PROJECT_SQL = (
    "EXEC SSISDB.catalog.get_project @folder_name = ?, @project_name = ?"
)


def _dtsx_stem(name: str) -> str:
    """Strip a trailing ``.dtsx`` (case-insensitive) so names match across sources."""
    return name[:-5] if name.lower().endswith(".dtsx") else name


def ispac_to_dtsx(blob: bytes) -> dict[str, bytes]:
    """Unzip an ``.ispac`` archive and return ``{package-stem: dtsx-bytes}``.

    Keyed by the ``.dtsx`` member's stem (extension stripped) so callers can
    match against ``catalog.packages.name`` whether or not it carries the
    extension. Raises :class:`zipfile.BadZipFile` if *blob* is not a zip.
    """
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            if info.filename.lower().endswith(".dtsx"):
                stem = _dtsx_stem(info.filename.rsplit("/", 1)[-1])
                members[stem] = zf.read(info)
    return members


def fetch_packages(cursor: Any) -> tuple[list[ExtractedPackage], list[str]]:
    """Enumerate the catalog and return ``(packages, warnings)``.

    One ``get_project`` call per distinct ``(folder, project)`` — its ``.ispac``
    is unzipped once and shared across that project's packages. A package whose
    ``.dtsx`` member is missing from the project archive, or a project whose
    binary will not unzip, is skipped with a warning rather than aborting the run.
    """
    cursor.execute(ENUMERATE_SQL)
    rows = cursor.fetchall()

    # Preserve first-seen order of (folder, project) pairs for determinism.
    projects: dict[tuple[str, str], list[str]] = {}
    for folder, project, package in rows:
        projects.setdefault((folder, project), []).append(package)

    packages: list[ExtractedPackage] = []
    warnings: list[str] = []

    for (folder, project), package_names in projects.items():
        cursor.execute(GET_PROJECT_SQL, folder, project)
        row = cursor.fetchone()
        if row is None or row[0] is None:
            warnings.append(f"{folder}/{project}: get_project returned no binary")
            continue
        blob = bytes(row[0])
        try:
            members = ispac_to_dtsx(blob)
        except zipfile.BadZipFile:
            warnings.append(f"{folder}/{project}: project binary is not a valid .ispac archive")
            continue

        for package in package_names:
            stem = _dtsx_stem(package)
            payload = members.get(stem)
            if payload is None:
                warnings.append(
                    f"{folder}/{project}/{package}: no matching .dtsx member in project archive"
                )
                continue
            packages.append(
                ExtractedPackage(
                    folder=folder,
                    project=project,
                    name=package,
                    payload=payload,
                    store="ssisdb",
                )
            )

    return packages, warnings

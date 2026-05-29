"""Connect to SQL Server (Windows auth), extract SSIS packages, write them out.

Connection uses Windows Integrated authentication (``Trusted_Connection=yes``):
the process authenticates as its own identity — on an Azure DevOps self-hosted
agent that is the agent service's domain account. No username or password is
ever read, passed, or logged.

Public API
----------
extract_packages(...) -> list[Path]
    Connect, resolve the store, fetch every package, and write each as a
    ``.dtsx`` file under ``out_dir``. Also writes ``_packages_manifest.json``
    and, when any package is skipped, ``_packages_warnings.log``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pyodbc

from .._naming import resolve_collisions, sanitise
from ..errors import PackageExtractError
from ..observability import logger
from . import store_msdb, store_ssisdb
from .model import ExtractedPackage

MANIFEST_NAME = "_packages_manifest.json"
WARNINGS_NAME = "_packages_warnings.log"
MANIFEST_VERSION = 1

_DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"
_DETECT_SSISDB_SQL = "SELECT DB_ID('SSISDB')"


def build_connection_string(
    server: str,
    port: str = "1433",
    database: str = "master",
    driver: str = _DEFAULT_DRIVER,
    trust_cert: bool = True,
) -> str:
    """Build a Windows-auth ODBC connection string (no UID/PWD).

    ``Trusted_Connection=yes`` carries the caller's Windows identity; encryption
    is always on, certificate trust follows *trust_cert*.
    """
    trust = "yes" if trust_cert else "no"
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server},{port};"
        f"DATABASE={database};"
        f"Trusted_Connection=yes;"
        f"Encrypt=yes;"
        f"TrustServerCertificate={trust};"
    )


def resolve_store(cursor: Any, requested: str) -> str:
    """Return the concrete store (``"msdb"`` / ``"ssisdb"``).

    For ``"auto"``, probe ``DB_ID('SSISDB')``: a non-null id means the catalog
    is present, so prefer it; otherwise fall back to the legacy msdb store.
    """
    if requested in ("msdb", "ssisdb"):
        return requested
    cursor.execute(_DETECT_SSISDB_SQL)
    row = cursor.fetchone()
    return "ssisdb" if row is not None and row[0] is not None else "msdb"


def _matches(name: str, name_filter: str | None) -> bool:
    return not name_filter or name_filter.lower() in name.lower()


def collect_packages(
    cursor: Any, store: str, name_filter: str | None = None
) -> tuple[list[ExtractedPackage], list[str]]:
    """Fetch packages from *store* and apply the optional name filter."""
    if store == "msdb":
        packages = store_msdb.fetch_packages(cursor)
        warnings: list[str] = []
    elif store == "ssisdb":
        packages, warnings = store_ssisdb.fetch_packages(cursor)
    else:  # pragma: no cover - guarded by argparse choices upstream
        raise PackageExtractError(f"unknown store {store!r}")

    filtered = [p for p in packages if _matches(p.name, name_filter)]
    return filtered, warnings


def _relative_dir(pkg: ExtractedPackage) -> Path:
    """Sanitised output directory (relative to out_dir) for *pkg*."""
    parts = [sanitise(pkg.folder)] if pkg.folder else []
    if pkg.project:
        parts.append(sanitise(pkg.project))
    return Path(*parts) if parts else Path(".")


def _stem(name: str) -> str:
    return name[:-5] if name.lower().endswith(".dtsx") else name


def plan_paths(packages: list[ExtractedPackage]) -> dict[int, Path]:
    """Map each package (by list index) to its collision-free relative path.

    Filenames are sanitised and de-duplicated *within each target directory*
    using the shared :func:`resolve_collisions` algorithm, so two packages whose
    names sanitise alike (e.g. ``Foo Bar`` and ``Foo.Bar``) get ``_2`` / ``_3``
    suffixes deterministically. Within one directory the source names are
    already unique (msdb is keyed by folder+name, the catalog by
    folder+project+name), so each stem maps to exactly one index.
    """
    by_dir: dict[Path, list[int]] = {}
    for i, pkg in enumerate(packages):
        by_dir.setdefault(_relative_dir(pkg), []).append(i)

    paths: dict[int, Path] = {}
    for rel_dir, indices in by_dir.items():
        resolved = resolve_collisions([_stem(packages[i].name) for i in indices])
        for i in indices:
            paths[i] = rel_dir / f"{resolved[_stem(packages[i].name)]}.dtsx"
    return paths


def write_packages(
    packages: list[ExtractedPackage], out_dir: Path
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Write every package as a ``.dtsx`` file; return ``(paths, manifest_entries)``.

    Manifest entries are sorted by ``(folder, project, name)`` for determinism.
    """
    paths = plan_paths(packages)
    written: list[Path] = []
    entries: list[dict[str, Any]] = []

    order = sorted(
        range(len(packages)),
        key=lambda i: (packages[i].folder, packages[i].project or "", packages[i].name),
    )
    for i in order:
        pkg = packages[i]
        rel = paths[i]
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pkg.payload)
        written.append(target)
        entries.append(
            {
                "store": pkg.store,
                "folder": pkg.folder,
                "project": pkg.project,
                "name": pkg.name,
                "path": rel.as_posix(),
            }
        )
    return written, entries


def _write_manifest(out_dir: Path, store: str, entries: list[dict[str, Any]]) -> None:
    manifest = {"version": MANIFEST_VERSION, "store": store, "packages": entries}
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def _write_warnings(out_dir: Path, warnings: list[str]) -> None:
    if not warnings:
        return
    # Strip CR/LF so a crafted folder/package name cannot forge log lines.
    lines = [w.replace("\n", " ").replace("\r", " ") for w in sorted(warnings)]
    (out_dir / WARNINGS_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_packages(
    server: str,
    port: str = "1433",
    database: str = "",
    store: str = "auto",
    name_filter: str | None = None,
    out_dir: Path | str | None = None,
    driver: str = _DEFAULT_DRIVER,
    trust_cert: bool = True,
    clean: bool = False,
) -> list[Path]:
    """Connect, extract every SSIS package, write ``.dtsx`` files + a manifest.

    Parameters mirror the CLI. *database* may be blank — the queries are
    three-part qualified (``msdb.dbo.*`` / ``SSISDB.catalog.*``), so we default
    the connection scope to ``master``.

    Raises
    ------
    PackageExtractError
        On connection failure, query failure, or an unknown store.
    """
    out_path = Path(out_dir) if out_dir else Path("packages")
    if clean and out_path.exists():
        shutil.rmtree(out_path)
    out_path.mkdir(parents=True, exist_ok=True)

    connstr = build_connection_string(
        server=server,
        port=port,
        database=database or "master",
        driver=driver,
        trust_cert=trust_cert,
    )

    try:
        conn = pyodbc.connect(connstr, timeout=10)
    except pyodbc.Error as exc:
        logger.error("extract-packages: error: connection failed")
        raise PackageExtractError("connection failed") from exc

    try:
        cursor = conn.cursor()
        resolved_store = resolve_store(cursor, store)
        logger.info("extract-packages: store resolved to {}", resolved_store)
        packages, warnings = collect_packages(cursor, resolved_store, name_filter)
    except PackageExtractError:
        conn.close()
        raise
    except pyodbc.Error as exc:
        conn.close()
        logger.error("extract-packages: error: query failed")
        raise PackageExtractError("query failed") from exc
    finally:
        try:
            conn.close()
        except pyodbc.Error:
            pass

    written, entries = write_packages(packages, out_path)
    _write_manifest(out_path, resolved_store, entries)
    _write_warnings(out_path, warnings)
    return written

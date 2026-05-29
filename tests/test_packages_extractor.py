"""Unit tests for ``msb_ssis2sql.packages`` — no live SQL Server.

A fake pyodbc cursor returns canned rows for the queries the extractor runs,
so the msdb path, the SSISDB catalog path (including .ispac unzipping), store
auto-detection, name filtering, collision-safe paths, the manifest, and the
warnings log are all exercised offline.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

from msb_ssis2sql.packages import extractor
from msb_ssis2sql.packages.model import ExtractedPackage
from msb_ssis2sql.packages.store_ssisdb import ispac_to_dtsx


# --------------------------------------------------------------------------- #
# fake pyodbc cursor / connection
# --------------------------------------------------------------------------- #

class _FakeCursor:
    def __init__(
        self,
        *,
        ssisdb_dbid: Any = None,
        modern: bool = True,
        msdb_rows: list[Any] | None = None,
        enumerate_rows: list[Any] | None = None,
        project_blobs: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self._ssisdb_dbid = ssisdb_dbid
        self._modern = modern
        self._msdb_rows = msdb_rows or []
        self._enumerate_rows = enumerate_rows or []
        self._project_blobs = project_blobs or {}
        self._last = ""
        self._params: tuple[Any, ...] = ()

    def execute(self, query: str, *params: Any) -> "_FakeCursor":
        self._last = query
        self._params = params
        return self

    def fetchone(self) -> Any:
        q = self._last.lower()
        if "db_id('ssisdb')" in q:
            return (self._ssisdb_dbid,)
        if "object_id('msdb.dbo.sysssispackages')" in q:
            return (12345 if self._modern else None,)
        if "get_project" in q:
            folder, project = self._params
            blob = self._project_blobs.get((folder, project))
            return (blob,) if blob is not None else None
        return None

    def fetchall(self) -> list[Any]:
        q = self._last.lower()
        if "object_id" in q:
            return []
        if "sysssispackages" in q or "sysdtspackages90" in q:
            return list(self._msdb_rows)
        if "catalog.folders" in q:
            return list(self._enumerate_rows)
        return []

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        pass


def _install(monkeypatch, cursor: _FakeCursor) -> list[dict[str, Any]]:
    import pyodbc  # noqa: F401 - present on dev/CI machines

    calls: list[dict[str, Any]] = []

    def _fake_connect(*args: Any, **kwargs: Any) -> _FakeConnection:
        calls.append({"args": args, "kwargs": kwargs})
        return _FakeConnection(cursor)

    monkeypatch.setattr("pyodbc.connect", _fake_connect)
    return calls


def _make_ispac(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# build_connection_string — Windows auth, no credentials
# --------------------------------------------------------------------------- #

def test_connection_string_uses_windows_auth_and_no_credentials():
    cs = extractor.build_connection_string("sql01", "1433", "master")
    assert "Trusted_Connection=yes" in cs
    assert "Encrypt=yes" in cs
    assert "TrustServerCertificate=yes" in cs
    assert "UID=" not in cs and "PWD=" not in cs


def test_connection_string_no_trust_cert():
    cs = extractor.build_connection_string("sql01", trust_cert=False)
    assert "TrustServerCertificate=no" in cs


# --------------------------------------------------------------------------- #
# store auto-detection
# --------------------------------------------------------------------------- #

def test_resolve_store_auto_prefers_catalog_when_present():
    cur = _FakeCursor(ssisdb_dbid=7)
    assert extractor.resolve_store(cur, "auto") == "ssisdb"


def test_resolve_store_auto_falls_back_to_msdb():
    cur = _FakeCursor(ssisdb_dbid=None)
    assert extractor.resolve_store(cur, "auto") == "msdb"


def test_resolve_store_explicit_does_not_probe():
    cur = _FakeCursor(ssisdb_dbid=None)
    assert extractor.resolve_store(cur, "ssisdb") == "ssisdb"


# --------------------------------------------------------------------------- #
# msdb store
# --------------------------------------------------------------------------- #

def test_extract_msdb_writes_dtsx_and_manifest(monkeypatch, tmp_path):
    rows = [
        ("Maintenance", "LoadFacts", b"<DTS:Executable>facts</DTS:Executable>"),
        ("", "RootPkg", b"<DTS:Executable>root</DTS:Executable>"),
    ]
    cur = _FakeCursor(ssisdb_dbid=None, msdb_rows=rows)
    _install(monkeypatch, cur)

    written = extractor.extract_packages(server="sql01", store="auto", out_dir=tmp_path)

    assert (tmp_path / "maintenance" / "loadfacts.dtsx").read_bytes() == rows[0][2]
    assert (tmp_path / "rootpkg.dtsx").read_bytes() == rows[1][2]
    assert len(written) == 2

    manifest = json.loads((tmp_path / "_packages_manifest.json").read_text())
    assert manifest["version"] == 1
    assert manifest["store"] == "msdb"
    names = [e["name"] for e in manifest["packages"]]
    assert names == ["RootPkg", "LoadFacts"]  # sorted by (folder, project, name): "" before "Maintenance"
    # no warnings -> no warnings log
    assert not (tmp_path / "_packages_warnings.log").exists()


def test_msdb_falls_back_to_legacy_90_tables(monkeypatch, tmp_path):
    rows = [("", "OldPkg", b"<old/>")]
    cur = _FakeCursor(ssisdb_dbid=None, modern=False, msdb_rows=rows)
    _install(monkeypatch, cur)

    extractor.extract_packages(server="sql01", store="msdb", out_dir=tmp_path)
    assert (tmp_path / "oldpkg.dtsx").read_bytes() == b"<old/>"


def test_name_filter_is_case_insensitive_substring(monkeypatch, tmp_path):
    rows = [
        ("", "LoadFacts", b"a"),
        ("", "LoadDims", b"b"),
        ("", "Cleanup", b"c"),
    ]
    cur = _FakeCursor(ssisdb_dbid=None, msdb_rows=rows)
    _install(monkeypatch, cur)

    written = extractor.extract_packages(
        server="sql01", store="msdb", name_filter="load", out_dir=tmp_path
    )
    stems = sorted(p.stem for p in written)
    assert stems == ["loaddims", "loadfacts"]


# --------------------------------------------------------------------------- #
# ssisdb catalog store
# --------------------------------------------------------------------------- #

def test_ispac_to_dtsx_extracts_only_dtsx_members():
    ispac = _make_ispac(
        {
            "Package.dtsx": b"<pkg1/>",
            "Load.dtsx": b"<pkg2/>",
            "Project.params": b"<params/>",
            "@Project.manifest": b"<manifest/>",
        }
    )
    members = ispac_to_dtsx(ispac)
    assert members == {"Package": b"<pkg1/>", "Load": b"<pkg2/>"}


def test_extract_ssisdb_unzips_packages(monkeypatch, tmp_path):
    ispac = _make_ispac({"Extract.dtsx": b"<e/>", "Load.dtsx": b"<l/>"})
    enumerate_rows = [
        ("Finance", "ETL", "Extract.dtsx"),
        ("Finance", "ETL", "Load.dtsx"),
    ]
    cur = _FakeCursor(
        ssisdb_dbid=9,
        enumerate_rows=enumerate_rows,
        project_blobs={("Finance", "ETL"): ispac},
    )
    _install(monkeypatch, cur)

    written = extractor.extract_packages(server="sql01", store="auto", out_dir=tmp_path)

    assert (tmp_path / "finance" / "etl" / "extract.dtsx").read_bytes() == b"<e/>"
    assert (tmp_path / "finance" / "etl" / "load.dtsx").read_bytes() == b"<l/>"
    assert len(written) == 2

    manifest = json.loads((tmp_path / "_packages_manifest.json").read_text())
    assert manifest["store"] == "ssisdb"
    assert manifest["packages"][0]["project"] == "ETL"


def test_expanded_writes_project_files_and_manifest_lists_them(monkeypatch, tmp_path):
    ispac = _make_ispac(
        {
            "Extract.dtsx": b"<e/>",
            "Project.params": b"<SSIS:Parameters/>",
            "Conn.conmgr": b"<DTS:ConnectionManager/>",
            "@Project.manifest": b"<SSIS:Project/>",
        }
    )
    enumerate_rows = [("Finance", "ETL", "Extract.dtsx")]
    cur = _FakeCursor(
        ssisdb_dbid=9,
        enumerate_rows=enumerate_rows,
        project_blobs={("Finance", "ETL"): ispac},
    )
    _install(monkeypatch, cur)

    extractor.extract_packages(
        server="sql01", store="ssisdb", out_dir=tmp_path, expanded=True
    )

    proj_dir = tmp_path / "finance" / "etl"
    assert (proj_dir / "extract.dtsx").read_bytes() == b"<e/>"
    # Project files keep their exact basenames so convert-tree/load_project find them.
    assert (proj_dir / "Project.params").read_bytes() == b"<SSIS:Parameters/>"
    assert (proj_dir / "Conn.conmgr").exists()
    assert (proj_dir / "@Project.manifest").exists()

    manifest = json.loads((tmp_path / "_packages_manifest.json").read_text())
    assert sorted(manifest["project_files"]) == [
        "finance/etl/@Project.manifest",
        "finance/etl/Conn.conmgr",
        "finance/etl/Project.params",
    ]


def test_not_expanded_omits_project_files(monkeypatch, tmp_path):
    ispac = _make_ispac({"Extract.dtsx": b"<e/>", "Project.params": b"<p/>"})
    cur = _FakeCursor(
        ssisdb_dbid=9,
        enumerate_rows=[("Finance", "ETL", "Extract.dtsx")],
        project_blobs={("Finance", "ETL"): ispac},
    )
    _install(monkeypatch, cur)
    extractor.extract_packages(server="sql01", store="ssisdb", out_dir=tmp_path)
    assert not (tmp_path / "finance" / "etl" / "Project.params").exists()
    manifest = json.loads((tmp_path / "_packages_manifest.json").read_text())
    assert manifest["project_files"] == []


def test_ssisdb_missing_member_is_warned_not_fatal(monkeypatch, tmp_path):
    ispac = _make_ispac({"Extract.dtsx": b"<e/>"})
    enumerate_rows = [
        ("Finance", "ETL", "Extract.dtsx"),
        ("Finance", "ETL", "Ghost.dtsx"),  # not in the archive
    ]
    cur = _FakeCursor(
        ssisdb_dbid=9,
        enumerate_rows=enumerate_rows,
        project_blobs={("Finance", "ETL"): ispac},
    )
    _install(monkeypatch, cur)

    written = extractor.extract_packages(server="sql01", store="ssisdb", out_dir=tmp_path)
    assert len(written) == 1
    log = (tmp_path / "_packages_warnings.log").read_text()
    assert "Ghost.dtsx" in log


# --------------------------------------------------------------------------- #
# collision-safe filenames
# --------------------------------------------------------------------------- #

def test_plan_paths_disambiguates_sanitiser_collisions():
    pkgs = [
        ExtractedPackage(folder="", project=None, name="Foo Bar", payload=b"", store="msdb"),
        ExtractedPackage(folder="", project=None, name="Foo.Bar", payload=b"", store="msdb"),
    ]
    paths = extractor.plan_paths(pkgs)
    names = sorted(p.name for p in paths.values())
    assert names == ["foo_bar.dtsx", "foo_bar_2.dtsx"]


# --------------------------------------------------------------------------- #
# --clean wipes the directory first
# --------------------------------------------------------------------------- #

def test_clean_removes_stale_output(monkeypatch, tmp_path):
    stale = tmp_path / "out" / "stale.dtsx"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")

    rows = [("", "Fresh", b"<new/>")]
    cur = _FakeCursor(ssisdb_dbid=None, msdb_rows=rows)
    _install(monkeypatch, cur)

    extractor.extract_packages(
        server="sql01", store="msdb", out_dir=tmp_path / "out", clean=True
    )
    assert not stale.exists()
    assert (tmp_path / "out" / "fresh.dtsx").exists()


# --------------------------------------------------------------------------- #
# connection failure is categorised
# --------------------------------------------------------------------------- #

def test_connection_failure_raises_package_extract_error(monkeypatch, tmp_path):
    import pyodbc

    from msb_ssis2sql.errors import PackageExtractError

    def _boom(*a: Any, **k: Any):
        raise pyodbc.OperationalError("08001", "refused")

    monkeypatch.setattr("pyodbc.connect", _boom)

    with pytest.raises(PackageExtractError):
        extractor.extract_packages(server="sql01", out_dir=tmp_path)

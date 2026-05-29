"""CLI tests for ``msb_ssis2sql extract-packages`` — no live SQL Server."""
from __future__ import annotations

import json
from typing import Any

import pytest

from msb_ssis2sql.cli import main
from msb_ssis2sql.observability import logger


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    logger.remove()
    logger.disable("msb_ssis2sql")


class _Cursor:
    def __init__(self, rows: list[Any]):
        self._rows = rows
        self._last = ""

    def execute(self, q: str, *p: Any) -> "_Cursor":
        self._last = q
        return self

    def fetchone(self) -> Any:
        q = self._last.lower()
        if "db_id('ssisdb')" in q:
            return (None,)  # force msdb path
        if "object_id" in q:
            return (1,)
        return None

    def fetchall(self) -> list[Any]:
        if "object_id" in self._last.lower():
            return []
        return list(self._rows)

    def close(self) -> None:
        pass


class _Conn:
    def __init__(self, cur: _Cursor):
        self._cur = cur

    def cursor(self) -> _Cursor:
        return self._cur

    def close(self) -> None:
        pass


def _install(monkeypatch, rows: list[Any]) -> None:
    import pyodbc  # noqa: F401

    monkeypatch.setattr("pyodbc.connect", lambda *a, **k: _Conn(_Cursor(rows)))


def test_extract_packages_happy_path(monkeypatch, tmp_path, capsys):
    _install(monkeypatch, [("", "PkgOne", b"<a/>")])
    rc = main(["extract-packages", "--server", "sql01", "--out", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "extracted 1 package(s)" in out
    assert (tmp_path / "pkgone.dtsx").read_bytes() == b"<a/>"
    assert json.loads((tmp_path / "_packages_manifest.json").read_text())["store"] == "msdb"


def test_server_from_env(monkeypatch, tmp_path):
    _install(monkeypatch, [("", "EnvPkg", b"<e/>")])
    monkeypatch.setenv("MSSQL_SERVER_ADDRESS", "sql-from-env")
    rc = main(["extract-packages", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "envpkg.dtsx").exists()


def test_missing_server_returns_exit_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MSSQL_SERVER_ADDRESS", raising=False)
    rc = main(["extract-packages", "--out", str(tmp_path)])
    assert rc == 2
    assert "--server is required" in capsys.readouterr().err


def test_connection_failure_returns_exit_2(monkeypatch, tmp_path, capsys):
    import pyodbc

    def _boom(*a: Any, **k: Any):
        raise pyodbc.OperationalError("08001", "refused")

    monkeypatch.setattr("pyodbc.connect", _boom)
    rc = main(["extract-packages", "--server", "sql01", "--out", str(tmp_path)])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()

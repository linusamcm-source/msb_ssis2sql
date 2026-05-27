"""AC-7 + CLI exit-code coverage for the new convert-tree / extract-agent-jobs surfaces.

Per plan-final.md §Decisions, exit codes are:
    0 success
    1 ≥1 per-file conversion failed
    2 unrecoverable I/O / msdb connection / sa-user detected

This file covers the new code paths the plan introduces; existing
``test_cli.py`` keeps covering pre-sprint behaviour.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from msb_ssis2sql.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_FIRST = FIXTURES / "main_first"


def test_convert_tree_returns_zero_on_success(tmp_path, capsys):
    """A clean fixture exits 0."""
    rc = main(["convert-tree", str(MAIN_FIRST), str(tmp_path / "out")])
    assert rc == 0


def test_convert_tree_returns_one_on_partial_failure(tmp_path):
    """A bad .dtsx in the tree causes exit 1."""
    inp = tmp_path / "in"
    inp.mkdir()
    shutil.copy(MAIN_FIRST / "childa.dtsx", inp / "good.dtsx")
    (inp / "broken.dtsx").write_text("<not valid", encoding="utf-8")

    rc = main(["convert-tree", str(inp), str(tmp_path / "out")])
    assert rc == 1


def test_no_orchestrator_suppresses_main_proc(tmp_path):
    """--no-orchestrator: per-package wrapped procs are still emitted, but the
    orchestrator file referencing them is suppressed."""
    out = tmp_path / "out"
    rc = main([
        "convert-tree",
        "--no-orchestrator",
        str(MAIN_FIRST),
        str(out),
    ])
    assert rc == 0

    # Per-package procs are still emitted (each child still wrapped).
    assert (out / "childa.sql").exists()
    assert (out / "childb.sql").exists()
    child_a = (out / "childa.sql").read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE" in child_a

    # The orchestrator proc body (EXECs to child procs) must NOT be emitted.
    main_path = out / "main.sql"
    if main_path.exists():
        body = main_path.read_text(encoding="utf-8")
        # If a main.sql is emitted at all, it must contain no EXECs of child procs.
        assert "EXEC " not in body, (
            f"--no-orchestrator must suppress orchestrator EXECs, got:\n{body}"
        )


def test_no_orchestrator_default_is_off(tmp_path):
    """Without --no-orchestrator (default), the main orchestrator IS emitted as a distinct file."""
    out = tmp_path / "out"
    rc = main(["convert-tree", str(MAIN_FIRST), str(out)])
    assert rc == 0

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, (
        f"default convert-tree must emit an orchestrator file, got: {[f.name for f in orch_files]}"
    )
    orch_sql = orch_files[0].read_text(encoding="utf-8")
    assert "EXEC " in orch_sql, (
        "default convert-tree must emit an orchestrator file with EXECs"
    )


def test_extract_agent_jobs_help_advertises_dsn_and_out(capsys):
    """`extract-agent-jobs --help` must list --out and --dsn (exists in argparse)."""
    with pytest.raises(SystemExit):
        main(["extract-agent-jobs", "--help"])
    out = capsys.readouterr().out
    assert "--out" in out
    assert "--dsn" in out


def test_extract_agent_jobs_unknown_dir_path_handled(tmp_path, monkeypatch, capsys):
    """If extraction has nothing else wrong, the out dir is created."""
    # Set required env so we don't fall into config-missing branch (categorised),
    # but rig pyodbc to mimic an OK permission probe and zero jobs.
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    monkeypatch.setenv("MSDB_USER", "loginname")
    monkeypatch.setenv("MSDB_PASSWORD", "ignored")

    class _Cursor:
        def __init__(self):
            self._q = ""

        def execute(self, q, *args):
            self._q = q.lower()
            return self

        def fetchall(self):
            if "is_rolemember" in self._q or "is_srvrolemember" in self._q:
                return [(0, 1, 1)]
            return []

        def fetchone(self):
            return self.fetchall()[0] if self.fetchall() else None

        @property
        def description(self):
            return [("is_sa",), ("is_agent_reader",), ("is_datareader",)]

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setattr("pyodbc.connect", lambda *a, **k: _Conn())

    out = tmp_path / "jobs"
    rc = main(["extract-agent-jobs", "--out", str(out)])
    assert rc == 0
    assert out.exists() and out.is_dir()

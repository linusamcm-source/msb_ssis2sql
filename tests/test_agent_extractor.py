"""AC-2: extract-agent-jobs CLI + agent.extractor module.

Tests use a fake ``pyodbc`` connection — NO live SQL Server — to verify:
* role/permission probe rejects sa (exit 2 / 'sa-detected')
* role/permission probe rejects missing db_datareader (exit 2 / 'permission')
* role/permission probe rejects missing SQLAgentReaderRole
* Connection settings (password, dsn) NEVER appear in log lines or error output
* Happy-path extraction returns ``AgentJob`` objects whose YAML matches the
  Appendix A schema golden in ``tests/fixtures/golden_jobs/example_job.yaml``.

Will fail with ImportError until ``msb_ssis2sql/agent/`` ships.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# fake pyodbc cursor: returns canned rows for the queries the extractor runs
# --------------------------------------------------------------------------- #

class _FakeCursor:
    """Minimal pyodbc.Cursor stand-in: queues responses by query keyword."""

    def __init__(self, role_probe: dict[str, int], jobs: list[Any], steps: list[Any],
                 schedules: list[Any], jobschedules: list[Any]):
        self._role_probe = role_probe
        self._jobs = jobs
        self._steps = steps
        self._schedules = schedules
        self._jobschedules = jobschedules
        self._last_query = ""

    def execute(self, query: str, *params: Any) -> "_FakeCursor":
        self._last_query = query
        return self

    def fetchall(self) -> list[Any]:
        q = self._last_query.lower()
        if "is_rolemember" in q or "is_srvrolemember" in q:
            # Permission probe returns one row of named ints.
            return [tuple(self._role_probe.values())]
        if "sysjobs" in q and "sysjobsteps" not in q and "sysjobschedules" not in q:
            return list(self._jobs)
        if "sysjobsteps" in q:
            return list(self._steps)
        if "sysjobschedules" in q:
            return list(self._jobschedules)
        if "sysschedules" in q:
            return list(self._schedules)
        return []

    def fetchone(self) -> Any:
        rows = self.fetchall()
        return rows[0] if rows else None

    @property
    def description(self) -> list[Any]:
        q = self._last_query.lower()
        if "is_rolemember" in q or "is_srvrolemember" in q:
            return [("is_sa",), ("is_agent_reader",), ("is_datareader",)]
        return [("col",)]

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        pass

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _install_fake_pyodbc(monkeypatch, cursor: _FakeCursor) -> list[dict[str, Any]]:
    """Patch pyodbc.connect to return a fake; record every call."""
    import pyodbc  # noqa: F401 - tests run on machines where pyodbc is installed

    calls: list[dict[str, Any]] = []

    def _fake_connect(*args: Any, **kwargs: Any) -> _FakeConnection:
        calls.append({"args": args, "kwargs": kwargs})
        return _FakeConnection(cursor)

    monkeypatch.setattr("pyodbc.connect", _fake_connect)
    return calls


# --------------------------------------------------------------------------- #
# happy-path: one job, two steps, one daily schedule -> matches golden YAML
# --------------------------------------------------------------------------- #

def _golden_role_probe_ok() -> dict[str, int]:
    return {"is_sa": 0, "is_agent_reader": 1, "is_datareader": 1}


def _golden_rows():
    # Modelled on Appendix A.  Column order chosen so the extractor can map
    # by ordinal — the field-name list is part of the extractor's contract.
    jobs = [
        (
            "NightlyLoad",                              # name
            1,                                          # enabled
            "nightly load of fact and dimension tables",  # description
            "svc_agent_runner",                         # owner_sid_login_name
            2,                                          # notify_level_email
            "ops-team",                                 # notify_email_operator
        ),
    ]
    steps = [
        # (job_name, step_id, step_name, subsystem, command, database_name,
        #  on_success_action, on_success_step_id, on_fail_action, on_fail_step_id,
        #  retry_attempts, retry_interval)
        ("NightlyLoad", 1, "run-load", "TSQL", "EXEC dbo.usp_nightly_load",
         "SalesDW", 1, 0, 2, 0, 0, 0),
        ("NightlyLoad", 2, "verify", "TSQL", "EXEC dbo.usp_post_load_verify",
         "SalesDW", 1, 0, 2, 0, 0, 0),
    ]
    schedules = [
        # (schedule_name, enabled, freq_type, freq_interval, freq_subday_type,
        #  freq_subday_interval, freq_recurrence_factor,
        #  active_start_date, active_end_date, active_start_time, active_end_time)
        ("nightly-2am", 1, 4, 1, 1, 0, 0, 20260101, 99991231, 20000, 235959),
    ]
    jobschedules = [("NightlyLoad", "nightly-2am")]
    return jobs, steps, schedules, jobschedules


def test_extractor_happy_path_yaml_matches_golden(monkeypatch, tmp_path):
    """End-to-end with fake DB → emitted YAML byte-identical to the golden."""
    jobs, steps, schedules, jobsched = _golden_rows()
    cursor = _FakeCursor(_golden_role_probe_ok(), jobs, steps, schedules, jobsched)
    _install_fake_pyodbc(monkeypatch, cursor)

    from msb_ssis2sql.agent.extractor import extract_agent_jobs

    written = extract_agent_jobs(
        dsn="Driver={ODBC Driver 18 for SQL Server};Server=fake;",
        out_dir=tmp_path,
    )

    assert tmp_path.iterdir, list(tmp_path.iterdir())
    paths = sorted(p.name for p in tmp_path.iterdir())
    assert paths == ["nightlyload.yaml"], paths
    assert list(written) == [tmp_path / "nightlyload.yaml"]

    expected = (Path(__file__).parent / "fixtures" / "golden_jobs" / "example_job.yaml").read_text(
        encoding="utf-8"
    )
    actual = (tmp_path / "nightlyload.yaml").read_text(encoding="utf-8")
    assert actual == expected, f"YAML drift:\n--- expected ---\n{expected}\n--- got ---\n{actual}"


# --------------------------------------------------------------------------- #
# sa-detection: extract-agent-jobs CLI exits 2 with 'sa-detected'
# --------------------------------------------------------------------------- #

def test_cli_exits_two_when_sa_member(monkeypatch, tmp_path, capsys):
    cursor = _FakeCursor(
        role_probe={"is_sa": 1, "is_agent_reader": 1, "is_datareader": 1},
        jobs=[], steps=[], schedules=[], jobschedules=[],
    )
    _install_fake_pyodbc(monkeypatch, cursor)
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    monkeypatch.setenv("MSDB_USER", "loginname")
    monkeypatch.setenv("MSDB_PASSWORD", "redacted-secret-9847")

    from msb_ssis2sql.cli import main

    rc = main(["extract-agent-jobs", "--out", str(tmp_path)])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "sa-detected" in err or "sa detected" in err.lower()


def test_cli_exits_two_when_missing_db_datareader(monkeypatch, tmp_path, capsys):
    cursor = _FakeCursor(
        role_probe={"is_sa": 0, "is_agent_reader": 1, "is_datareader": 0},
        jobs=[], steps=[], schedules=[], jobschedules=[],
    )
    _install_fake_pyodbc(monkeypatch, cursor)
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    monkeypatch.setenv("MSDB_USER", "loginname")
    monkeypatch.setenv("MSDB_PASSWORD", "redacted-secret-9847")

    from msb_ssis2sql.cli import main

    rc = main(["extract-agent-jobs", "--out", str(tmp_path)])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "permission" in err.lower()


def test_cli_exits_two_when_missing_sqlagentreader(monkeypatch, tmp_path, capsys):
    cursor = _FakeCursor(
        role_probe={"is_sa": 0, "is_agent_reader": 0, "is_datareader": 1},
        jobs=[], steps=[], schedules=[], jobschedules=[],
    )
    _install_fake_pyodbc(monkeypatch, cursor)
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    monkeypatch.setenv("MSDB_USER", "loginname")
    monkeypatch.setenv("MSDB_PASSWORD", "redacted-secret-9847")

    from msb_ssis2sql.cli import main

    rc = main(["extract-agent-jobs", "--out", str(tmp_path)])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "permission" in err.lower()


# --------------------------------------------------------------------------- #
# password/DSN must never appear in logged output
# --------------------------------------------------------------------------- #

def test_password_and_dsn_never_appear_in_stderr(monkeypatch, tmp_path, capsys):
    """Decision: connection settings never appear in logs/error output."""
    secret_password = "supersecret-pw-1234"
    secret_dsn = "Driver={ODBC};Server=db-prod-internal;Database=msdb;"
    jobs, steps, schedules, jobsched = _golden_rows()
    cursor = _FakeCursor(_golden_role_probe_ok(), jobs, steps, schedules, jobsched)
    _install_fake_pyodbc(monkeypatch, cursor)
    monkeypatch.setenv("MSDB_DSN", secret_dsn)
    monkeypatch.setenv("MSDB_USER", "loginname-redacted")
    monkeypatch.setenv("MSDB_PASSWORD", secret_password)

    from msb_ssis2sql.cli import main

    rc = main(["extract-agent-jobs", "--out", str(tmp_path)])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert secret_password not in combined, (
        f"password leaked to stderr/stdout: {combined!r}"
    )
    assert "db-prod-internal" not in combined, (
        f"DSN host leaked to stderr/stdout: {combined!r}"
    )
    # And the run must still succeed.
    assert rc == 0, rc


# --------------------------------------------------------------------------- #
# Connection failure is categorised, not raw
# --------------------------------------------------------------------------- #

def test_cli_exits_two_on_connection_failure(monkeypatch, tmp_path, capsys):
    """pyodbc.connect raising must be categorised, not crash with a stack trace."""
    import pyodbc

    def _boom(*args: Any, **kwargs: Any):
        raise pyodbc.OperationalError("08001", "fake driver: connection refused")

    monkeypatch.setattr("pyodbc.connect", _boom)
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    monkeypatch.setenv("MSDB_USER", "loginname")
    monkeypatch.setenv("MSDB_PASSWORD", "irrelevant")

    from msb_ssis2sql.cli import main

    rc = main(["extract-agent-jobs", "--out", str(tmp_path)])
    assert rc == 2, rc
    err = capsys.readouterr().err
    assert "connection" in err.lower() or "error" in err.lower()


# --------------------------------------------------------------------------- #
# H-3: permission probe must execute in msdb context
# --------------------------------------------------------------------------- #

def test_permission_probe_contains_msdb_reference():
    """USE msdb is a separate execute call; SELECT probe is a second call."""
    from unittest.mock import MagicMock
    from msb_ssis2sql.agent.extractor import _PERMISSION_PROBE_USE, _PERMISSION_PROBE_SQL, _check_permissions

    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0, 1, 1)

    _check_permissions(cursor)

    execute_calls = [c[0][0] for c in cursor.execute.call_args_list]
    assert any("msdb" in q.lower() for q in execute_calls), (
        f"no USE msdb call found; calls were: {execute_calls!r}"
    )
    assert execute_calls[0] == _PERMISSION_PROBE_USE, (
        f"first execute must be USE msdb; got {execute_calls[0]!r}"
    )
    assert execute_calls[1] == _PERMISSION_PROBE_SQL, (
        f"second execute must be the SELECT probe; got {execute_calls[1]!r}"
    )
    assert len(execute_calls) == 2, (
        f"expected exactly 2 execute calls for permission probe; got {execute_calls!r}"
    )


# --------------------------------------------------------------------------- #
# AgentJob / AgentStep / AgentSchedule dataclass shape
# --------------------------------------------------------------------------- #

def test_agent_model_dataclasses_exist():
    from msb_ssis2sql.agent.model import AgentJob, AgentSchedule, AgentStep

    job = AgentJob(
        job_name="X", enabled=True, description="", owner_login_name="o",
        notify_level_email=0, notify_email_operator=None,
        schedules=[], steps=[],
    )
    assert job.job_name == "X"

    step = AgentStep(
        step_id=1, step_name="s", subsystem="TSQL", command="SELECT 1",
        database_name="msdb", on_success_action=1, on_success_step_id=0,
        on_fail_action=2, on_fail_step_id=0,
        retry_attempts=0, retry_interval=0,
    )
    assert step.step_id == 1

    sched = AgentSchedule(
        name="s", enabled=True, freq_type=4, freq_interval=1,
        freq_subday_type=1, freq_subday_interval=0,
        freq_recurrence_factor=0,
        active_start_date=20260101, active_end_date=99991231,
        active_start_time=20000, active_end_time=235959,
    )
    assert sched.name == "s"

"""Extract SQL Server Agent jobs from msdb into AgentJob dataclasses and YAML files.

Connection settings are read from MSDB_DSN, MSDB_USER, MSDB_PASSWORD env vars.
They must never appear in any logged or printed output.

Permission probe: rejects sysadmin (exit 2 / sa-detected); rejects missing
SQLAgentReaderRole or db_datareader (exit 2 / permission).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pyodbc

from .._naming import sanitise
from ..errors import AgentExtractError
from ..observability import logger
from .model import AgentJob, AgentSchedule, AgentStep
from .yaml_emitter import emit_job_yaml

_PERMISSION_PROBE_USE = "USE msdb"
_PERMISSION_PROBE_SQL = (
    "SELECT"
    " IS_SRVROLEMEMBER('sysadmin') AS is_sa,"
    " IS_ROLEMEMBER('SQLAgentReaderRole') AS is_agent_reader,"
    " IS_ROLEMEMBER('db_datareader') AS is_datareader"
)

_JOBS_SQL = """
SELECT
    j.name,
    j.enabled,
    j.description,
    SUSER_SNAME(j.owner_sid) AS owner_login_name,
    j.notify_level_email,
    o.name AS notify_email_operator
FROM msdb.dbo.sysjobs j
LEFT JOIN msdb.dbo.sysoperators o ON j.notify_email_operator_id = o.id
ORDER BY j.name
"""

_STEPS_SQL = """
SELECT
    j.name AS job_name,
    s.step_id,
    s.step_name,
    s.subsystem,
    s.command,
    s.database_name,
    s.on_success_action,
    s.on_success_step_id,
    s.on_fail_action,
    s.on_fail_step_id,
    s.retry_attempts,
    s.retry_interval
FROM msdb.dbo.sysjobsteps s
JOIN msdb.dbo.sysjobs j ON j.job_id = s.job_id
ORDER BY j.name, s.step_id
"""

_SCHEDULES_SQL = """
SELECT
    sc.name,
    sc.enabled,
    sc.freq_type,
    sc.freq_interval,
    sc.freq_subday_type,
    sc.freq_subday_interval,
    sc.freq_recurrence_factor,
    sc.active_start_date,
    sc.active_end_date,
    sc.active_start_time,
    sc.active_end_time
FROM msdb.dbo.sysschedules sc
ORDER BY sc.name
"""

_JOBSCHEDULES_SQL = """
SELECT j.name AS job_name, sc.name AS schedule_name
FROM msdb.dbo.sysjobschedules js
JOIN msdb.dbo.sysjobs j ON j.job_id = js.job_id
JOIN msdb.dbo.sysschedules sc ON sc.schedule_id = js.schedule_id
ORDER BY j.name, sc.name
"""


def _connect(dsn: str) -> Any:
    """Open a pyodbc connection. Never logs DSN or credentials."""
    user = os.environ.get("MSDB_USER", "")
    password = os.environ.get("MSDB_PASSWORD", "")
    kwargs: dict[str, Any] = {"timeout": 5}
    if user:
        kwargs["user"] = user
    if password:
        kwargs["password"] = password
    return pyodbc.connect(dsn, **kwargs)


def _check_permissions(cursor: Any) -> str | None:
    """Return an error category string or None if permissions are OK."""
    cursor.execute(_PERMISSION_PROBE_USE)
    cursor.fetchall()
    cursor.execute(_PERMISSION_PROBE_SQL)
    row = cursor.fetchone()
    if row is None:
        return "permission"
    is_sa = row[0]
    is_agent_reader = row[1]
    is_datareader = row[2]
    if is_sa:
        return "sa-detected"
    if not is_agent_reader or not is_datareader:
        return "permission"
    return None


def extract_jobs(
    dsn: str = "",
    out_dir: Path | str | None = None,
    job_filter: str | None = None,
) -> list[Path]:
    """Connect to msdb, extract agent jobs, write YAML files, return list of written paths.

    Raises ``AgentExtractError`` on permission / connection / query failures.
    Returns list of Path objects for written YAML files, sorted by job name.
    """
    out_dir = Path(out_dir) if out_dir else Path("jobs")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        conn = _connect(dsn)
    except Exception as exc:
        logger.error("agent-extractor: error: connection failed")
        raise AgentExtractError("connection failed") from exc

    try:
        cur = conn.cursor()
        perm_error = _check_permissions(cur)
        if perm_error:
            logger.error("agent-extractor: error: {}", perm_error)
            conn.close()
            raise AgentExtractError(perm_error)

        cur.execute(_JOBS_SQL)
        job_rows = cur.fetchall()

        cur.execute(_STEPS_SQL)
        step_rows = cur.fetchall()

        cur.execute(_JOBSCHEDULES_SQL)
        jobsched_rows = cur.fetchall()

        cur.execute(_SCHEDULES_SQL)
        sched_rows = cur.fetchall()
        cur.close()
        conn.close()

        # Build lookup maps.
        sched_by_name: dict[str, AgentSchedule] = {}
        for row in sched_rows:
            s = AgentSchedule(
                name=row[0],
                enabled=bool(row[1]),
                freq_type=row[2],
                freq_interval=row[3],
                freq_subday_type=row[4],
                freq_subday_interval=row[5],
                freq_recurrence_factor=row[6],
                active_start_date=row[7],
                active_end_date=row[8],
                active_start_time=row[9],
                active_end_time=row[10],
            )
            sched_by_name[s.name] = s

        # job_name -> list[schedule_name]
        job_sched_names: dict[str, list[str]] = {}
        for row in jobsched_rows:
            job_sched_names.setdefault(row[0], []).append(row[1])

        # job_name -> list[AgentStep]
        steps_by_job: dict[str, list[AgentStep]] = {}
        for row in step_rows:
            step = AgentStep(
                step_id=row[1],
                step_name=row[2],
                subsystem=row[3],
                command=row[4],
                database_name=row[5] or None,
                on_success_action=row[6],
                on_success_step_id=row[7],
                on_fail_action=row[8],
                on_fail_step_id=row[9],
                retry_attempts=row[10],
                retry_interval=row[11],
            )
            steps_by_job.setdefault(row[0], []).append(step)

        written: list[Path] = []
        for row in sorted(job_rows, key=lambda r: r[0]):
            job_name = row[0]
            if job_filter and job_filter.lower() not in job_name.lower():
                continue
            schedules = [
                sched_by_name[sn]
                for sn in job_sched_names.get(job_name, [])
                if sn in sched_by_name
            ]
            job = AgentJob(
                job_name=job_name,
                enabled=bool(row[1]),
                description=row[2] or "",
                owner_login_name=row[3] or "",
                notify_level_email=row[4],
                notify_email_operator=row[5] or None,
                schedules=schedules,
                steps=steps_by_job.get(job_name, []),
            )
            yaml_text = emit_job_yaml(job)
            file_name = f"{sanitise(job_name)}.yaml"
            out_path = out_dir / file_name
            out_path.write_text(yaml_text, encoding="utf-8")
            written.append(out_path)

    except AgentExtractError:
        raise
    except Exception as exc:
        category = "query"
        if isinstance(exc, pyodbc.InterfaceError):
            category = "auth"
        logger.error("agent-extractor: error: {}", category)
        raise AgentExtractError(category) from exc

    return written

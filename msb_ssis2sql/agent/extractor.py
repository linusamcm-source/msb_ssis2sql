"""Extract SQL Server Agent jobs from msdb into AgentJob dataclasses and YAML files.

Connection settings are read from MSDB_DSN, MSDB_USER, MSDB_PASSWORD env vars.
They must never appear in any logged or printed output.

Permission probe: rejects sysadmin (exit 2 / sa-detected); rejects missing
SQLAgentReaderRole or db_datareader (exit 2 / permission).
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import pyodbc

from .._naming import sanitise
from ..errors import AgentExtractError
from ..observability import logger
from .command_parser import Hit as ParseHit
from .command_parser import Unparseable, parse_ssis_command
from .manifest import Ambiguous
from .manifest import Hit as ResolveHit
from .manifest import Manifest, Miss, resolve
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


def maybe_rewrite_step(
    step: AgentStep,
    job_name: str,
    manifest: Manifest | None,
    warnings: list[tuple[str, int, str, str]],
) -> AgentStep:
    """Possibly rewrite ``step`` from SSIS subsystem to a TSQL EXEC call.

    Pure helper exposed for unit testing (D-11) — module-importable as
    ``msb_ssis2sql.agent.extractor.maybe_rewrite_step``. Behaviour summary:
      * Non-SSIS step → returned unchanged, no warnings appended.
      * SSIS step + no manifest → warn ``manifest_absent``, return verbatim.
      * SSIS step + manifest:
          - parse fails → warn ``unparseable``, return verbatim.
          - resolve Miss → warn ``unresolved``, return verbatim.
          - resolve Ambiguous → warn ``ambiguous_basename``, return verbatim.
          - resolve Hit → return new AgentStep with subsystem=TSQL,
            command=``EXEC <proc>;``, audit triple populated.

    ``warnings`` is a sink the caller passes in (D-11); we append
    ``(job_name, step_id, category, details)`` tuples to it. The helper
    NEVER writes to disk — that's the writer's job.
    """
    if step.subsystem != "SSIS":
        return step

    if manifest is None:
        warnings.append((job_name, step.step_id, "manifest_absent", "no manifest supplied"))
        return step

    parsed = parse_ssis_command(step.command)
    if isinstance(parsed, Unparseable):
        warnings.append(
            (job_name, step.step_id, "unparseable", parsed.reason)
        )
        return step
    # parsed must be a ParseHit at this point.
    assert isinstance(parsed, ParseHit)

    resolved = resolve(manifest, parsed.path)
    if isinstance(resolved, Miss):
        warnings.append(
            (job_name, step.step_id, "unresolved", parsed.path)
        )
        return step
    if isinstance(resolved, Ambiguous):
        candidate_paths = ", ".join(c.dtsx for c in resolved.candidates)
        warnings.append(
            (job_name, step.step_id, "ambiguous_basename",
             f"candidates=[{candidate_paths}]")
        )
        return step

    # Hit — build the rewritten step. dataclasses.replace preserves
    # every other field (database_name, retries, success/fail actions).
    assert isinstance(resolved, ResolveHit)
    return dataclasses.replace(
        step,
        subsystem="TSQL",
        command=f"EXEC {resolved.proc};",
        original_subsystem="SSIS",
        original_command=step.command,
        dtsx_source=resolved.dtsx_source,
    )


def write_agent_warnings_log(
    out_path: Path,
    warnings: list[tuple[str, int, str, str]],
    *,
    manifest_supplied: bool,
) -> None:
    """Write ``out_path`` with one line per warning, sorted by (job, step_id).

    When ``manifest_supplied`` is False, prepend the literal notice line
    ``manifest not supplied — all SSIS steps emitted verbatim`` per D-7.
    Empty warnings + supplied manifest produce a zero-byte file.
    """
    sorted_warnings = sorted(warnings, key=lambda w: (w[0], w[1]))
    # SEC-L1 — strip \r/\n from details so an attacker-supplied msdb command
    # cannot forge new log lines via embedded newlines.
    lines = [
        f"{job}:{step_id}: {category}: {details.replace(chr(10), ' ').replace(chr(13), ' ')}"
        for job, step_id, category, details in sorted_warnings
    ]
    if not manifest_supplied:
        lines.insert(0, "manifest not supplied — all SSIS steps emitted verbatim")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not lines:
        out_path.write_bytes(b"")
    else:
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def extract_agent_jobs(
    dsn: str = "",
    out_dir: Path | str | None = None,
    job_filter: str | None = None,
    manifest_path: Path | None = None,
) -> list[Path]:
    """Connect to msdb, extract agent jobs, write YAML files, return list of written paths.

    When ``manifest_path`` is given, SSIS-subsystem steps are rewritten to
    TSQL EXEC calls against the matching stored procedure (D-3 / T-4).
    Unresolved / unparseable / ambiguous steps are passed through verbatim
    and logged to ``<out_dir>/_agent_warnings.log`` (D-6 / T-7).

    Raises ``AgentExtractError`` on permission / connection / query failures.
    Raises ``ManifestError`` if ``manifest_path`` is given but the file is
    unreadable, invalid, or carries an unsupported version.
    Returns list of Path objects for written YAML files, sorted by job name.
    """
    out_dir = Path(out_dir) if out_dir else Path("jobs")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load the manifest first — invalid input must fail before we touch the DB.
    manifest: Manifest | None = None
    if manifest_path is not None:
        from .manifest import load_manifest  # local import to avoid cycle in CLI module
        manifest = load_manifest(manifest_path)

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

        # job_name -> list[AgentStep]. Rewriter sink: tuples of
        # (job_name, step_id, category, details) accumulated across all
        # steps, written once at the end (D-6 / D-7 / D-8).
        steps_by_job: dict[str, list[AgentStep]] = {}
        warning_sink: list[tuple[str, int, str, str]] = []
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
            step = maybe_rewrite_step(step, row[0], manifest, warning_sink)
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

        # T-7 — emit _agent_warnings.log when a manifest was supplied
        # (always, even if zero warnings → empty file) or when warnings
        # exist (with the position-0 notice when manifest was absent per
        # D-7). When no manifest AND no warnings, suppress the file so
        # the existing zero-SSIS happy path stays clean.
        if manifest is not None or warning_sink:
            write_agent_warnings_log(
                out_dir / "_agent_warnings.log",
                warning_sink,
                manifest_supplied=manifest is not None,
            )

    except AgentExtractError:
        raise
    except Exception as exc:
        category = "query"
        if isinstance(exc, pyodbc.InterfaceError):
            category = "auth"
        logger.error("agent-extractor: error: {}", category)
        raise AgentExtractError(category) from exc

    return written

"""AC-2 / AC-3: emitting YAML for identical inputs produces byte-identical output.

Will fail with ImportError until ``msb_ssis2sql.agent.yaml_emitter`` ships.
"""
from __future__ import annotations

from pathlib import Path


def _make_job():
    from msb_ssis2sql.agent.model import AgentJob, AgentSchedule, AgentStep

    return AgentJob(
        job_name="NightlyLoad",
        enabled=True,
        description="nightly load of fact and dimension tables",
        owner_login_name="svc_agent_runner",
        notify_level_email=2,
        notify_email_operator="ops-team",
        schedules=[AgentSchedule(
            name="nightly-2am",
            enabled=True,
            freq_type=4, freq_interval=1,
            freq_subday_type=1, freq_subday_interval=0,
            freq_recurrence_factor=0,
            active_start_date=20260101, active_end_date=99991231,
            active_start_time=20000, active_end_time=235959,
        )],
        steps=[
            AgentStep(
                step_id=1, step_name="run-load", subsystem="TSQL",
                command="EXEC dbo.usp_nightly_load",
                database_name="SalesDW",
                on_success_action=1, on_success_step_id=0,
                on_fail_action=2, on_fail_step_id=0,
                retry_attempts=0, retry_interval=0,
            ),
            AgentStep(
                step_id=2, step_name="verify", subsystem="TSQL",
                command="EXEC dbo.usp_post_load_verify",
                database_name="SalesDW",
                on_success_action=1, on_success_step_id=0,
                on_fail_action=2, on_fail_step_id=0,
                retry_attempts=0, retry_interval=0,
            ),
        ],
    )


def test_emit_job_yaml_is_deterministic():
    from msb_ssis2sql.agent.yaml_emitter import emit_job_yaml

    yaml1 = emit_job_yaml(_make_job())
    yaml2 = emit_job_yaml(_make_job())
    assert yaml1 == yaml2


def test_emit_job_yaml_matches_appendix_a_golden():
    from msb_ssis2sql.agent.yaml_emitter import emit_job_yaml

    golden = (Path(__file__).parent / "fixtures" / "golden_jobs" / "example_job.yaml").read_text(
        encoding="utf-8"
    )
    assert emit_job_yaml(_make_job()) == golden


def test_emit_keys_are_sorted_alphabetically():
    """sort_keys=True: top-level keys appear alphabetically."""
    from msb_ssis2sql.agent.yaml_emitter import emit_job_yaml

    text = emit_job_yaml(_make_job())
    top_level = [
        line.split(":", 1)[0] for line in text.splitlines()
        if line and line[0].isalpha() and ":" in line
    ]
    assert top_level == sorted(top_level), top_level

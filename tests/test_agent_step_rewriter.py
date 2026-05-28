"""Exhaustive coverage for ``maybe_rewrite_step`` (D-11 test seam).

Backs AC-5..AC-13 of plan-final-agent-step-procs.md. Each test builds an
in-memory ``AgentStep``, calls ``maybe_rewrite_step`` directly with a
fresh ``warnings`` list, and asserts both the returned step and the
warnings sink contents.

The warnings sink is a ``list[tuple[str, int, str, str]]`` —
``(job_name, step_id, category, details)`` — per D-11.
"""
from __future__ import annotations

from msb_ssis2sql.agent.model import AgentStep


def _ssis_step(step_id: int, command: str) -> AgentStep:
    """Build a minimal SSIS-subsystem AgentStep for the rewriter tests."""
    return AgentStep(
        step_id=step_id,
        step_name="run-ssis",
        subsystem="SSIS",
        command=command,
        database_name=None,
        on_success_action=1,
        on_success_step_id=0,
        on_fail_action=2,
        on_fail_step_id=0,
        retry_attempts=0,
        retry_interval=0,
    )


def _tsql_step(step_id: int, command: str) -> AgentStep:
    return AgentStep(
        step_id=step_id,
        step_name="run-tsql",
        subsystem="TSQL",
        command=command,
        database_name="SalesDW",
        on_success_action=1,
        on_success_step_id=0,
        on_fail_action=2,
        on_fail_step_id=0,
        retry_attempts=0,
        retry_interval=0,
    )


def _manifest_with(entries: list[tuple[str, str, str]]):
    """Build a Manifest in-memory from (dtsx, proc, out_sql) triples."""
    from msb_ssis2sql.agent.manifest import Manifest, ManifestEntry

    return Manifest(
        version=1,
        input_root="/srv/etl/src",
        entries=tuple(
            ManifestEntry(dtsx=d, proc=p, out_sql=o) for d, p, o in entries
        ),
    )


# ---------------------------------------------------------------- #
# AC-5: SSIS /FILE exact-match → SSIS step rewritten to TSQL EXEC
# ---------------------------------------------------------------- #

def test_ac5_ssis_file_exact_match_rewrites_to_tsql_exec() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/nightly_load.dtsx", "usp_fact_nightly_load", "fact/nightly_load.sql")]
    )
    cmd = 'DTExec /FILE "C:/etl/fact/nightly_load.dtsx" /CHECKPOINTING OFF'
    step = _ssis_step(1, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "NightlyLoad", manifest, warnings)

    assert out.subsystem == "TSQL"
    assert out.command == "EXEC usp_fact_nightly_load;"
    assert out.original_subsystem == "SSIS"
    assert out.original_command == cmd
    assert out.dtsx_source == "fact/nightly_load.dtsx"
    # Pass-through fields preserved (D-4).
    assert out.step_id == 1
    assert out.step_name == "run-ssis"
    assert out.on_fail_action == 2
    assert warnings == []


# ---------------------------------------------------------------- #
# AC-6: SSIS /ISSERVER basename match (case-insensitive)
# ---------------------------------------------------------------- #

def test_ac6_ssis_isserver_basename_match() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("etl/sales/NightlyLoad.dtsx", "usp_etl_sales_NightlyLoad", "etl/sales/NightlyLoad.sql")]
    )
    # SSISDB catalog path with totally different prefix; lowercase flag.
    cmd = 'dtexec /ISSERVER "\\SSISDB\\Sales\\Etl\\NightlyLoad.dtsx"'
    step = _ssis_step(2, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "Sales", manifest, warnings)

    assert out.subsystem == "TSQL"
    assert out.command == "EXEC usp_etl_sales_NightlyLoad;"
    assert out.original_subsystem == "SSIS"
    assert out.original_command == cmd
    assert out.dtsx_source == "etl/sales/NightlyLoad.dtsx"
    assert warnings == []


# ---------------------------------------------------------------- #
# AC-7: SSIS /SQ msdb-stored package without .dtsx suffix
# ---------------------------------------------------------------- #

def test_ac7_ssis_sq_appends_dtsx_then_resolves() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    # The manifest still has the .dtsx-suffixed dtsx field.
    manifest = _manifest_with(
        [("pkgs/nightlyload.dtsx", "usp_pkgs_nightlyload", "pkgs/nightlyload.sql")]
    )
    cmd = 'DTExec /SQ "\\Pkgs\\NightlyLoad"'
    step = _ssis_step(3, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "Sales", manifest, warnings)

    assert out.subsystem == "TSQL"
    assert out.command == "EXEC usp_pkgs_nightlyload;"
    assert out.original_subsystem == "SSIS"
    assert out.original_command == cmd
    # dtsx_source mirrors the manifest entry (POSIX, lowercase suffix).
    assert out.dtsx_source == "pkgs/nightlyload.dtsx"
    assert warnings == []


# ---------------------------------------------------------------- #
# AC-8: TSQL step is emitted UNCHANGED, no warnings
# ---------------------------------------------------------------- #

def test_ac8_tsql_step_passes_through_unchanged() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/nightly_load.dtsx", "usp_fact_nightly_load", "fact/nightly_load.sql")]
    )
    step = _tsql_step(1, "EXEC dbo.usp_something")
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "NightlyLoad", manifest, warnings)

    assert out is step or (
        out.subsystem == "TSQL"
        and out.command == "EXEC dbo.usp_something"
        and out.original_subsystem is None
        and out.original_command is None
        and out.dtsx_source is None
    )
    assert warnings == []


# ---------------------------------------------------------------- #
# AC-9: SSIS step with no recognised flag → verbatim + 'unparseable' warning
# ---------------------------------------------------------------- #

def test_ac9_ssis_no_recognised_flag_logs_unparseable_warning() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/nightly_load.dtsx", "usp_fact_nightly_load", "fact/nightly_load.sql")]
    )
    step = _ssis_step(7, "/UNKNOWN_FLAG x")
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "Job1", manifest, warnings)

    # Step is emitted verbatim with SSIS subsystem retained.
    assert out.subsystem == "SSIS"
    assert out.command == "/UNKNOWN_FLAG x"
    assert out.original_subsystem is None
    assert out.original_command is None
    assert out.dtsx_source is None
    # Exactly one warning, category 'unparseable'.
    assert len(warnings) == 1
    job_name, step_id, category, details = warnings[0]
    assert job_name == "Job1"
    assert step_id == 7
    assert category == "unparseable"
    assert details == "no /FILE, /ISSERVER, or /SQL flag found"


# ---------------------------------------------------------------- #
# AC-10: SSIS step parses cleanly but manifest has no match → unresolved
# ---------------------------------------------------------------- #

def test_ac10_ssis_unresolved_logs_warning() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/nightly_load.dtsx", "usp_fact_nightly_load", "fact/nightly_load.sql")]
    )
    cmd = 'DTExec /FILE "C:/etl/missing.dtsx"'
    step = _ssis_step(4, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "JobA", manifest, warnings)

    assert out.subsystem == "SSIS"
    assert out.command == cmd
    assert out.original_subsystem is None
    assert len(warnings) == 1
    job_name, step_id, category, details = warnings[0]
    assert job_name == "JobA"
    assert step_id == 4
    assert category == "unresolved"
    # Details should at least name what couldn't be resolved.
    assert "missing.dtsx" in details


# ---------------------------------------------------------------- #
# AC-11: Two manifest entries share basename → ambiguous_basename warning
# ---------------------------------------------------------------- #

def test_ac11_ssis_ambiguous_basename_logs_warning_with_candidates() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [
            ("fact/nightly_load.dtsx", "usp_fact_nightly_load", "fact/nightly_load.sql"),
            ("dim/nightly_load.dtsx", "usp_dim_nightly_load", "dim/nightly_load.sql"),
        ]
    )
    cmd = 'DTExec /FILE "C:/elsewhere/nightly_load.dtsx"'
    step = _ssis_step(5, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "JobB", manifest, warnings)

    assert out.subsystem == "SSIS"
    assert out.command == cmd
    assert out.original_subsystem is None
    assert len(warnings) == 1
    job_name, step_id, category, details = warnings[0]
    assert job_name == "JobB"
    assert step_id == 5
    assert category == "ambiguous_basename"
    # Both candidate dtsx paths must appear in the details so an operator
    # can grep and disambiguate.
    assert "fact/nightly_load.dtsx" in details
    assert "dim/nightly_load.dtsx" in details


# ---------------------------------------------------------------- #
# AC-12: when manifest is None, every SSIS step is logged under manifest_absent
# ---------------------------------------------------------------- #

def test_ac12_no_manifest_logs_manifest_absent_per_ssis_step() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    cmd = 'DTExec /FILE "C:/etl/fact/nightly_load.dtsx"'
    step = _ssis_step(1, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "Job1", None, warnings)

    assert out.subsystem == "SSIS"
    assert out.command == cmd
    assert out.original_subsystem is None
    assert len(warnings) == 1
    job_name, step_id, category, _details = warnings[0]
    assert job_name == "Job1"
    assert step_id == 1
    assert category == "manifest_absent"


def test_ac12_no_manifest_does_not_warn_for_tsql_steps() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    step = _tsql_step(2, "EXEC dbo.usp_something")
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "Job1", None, warnings)

    assert out.subsystem == "TSQL"
    assert out.command == "EXEC dbo.usp_something"
    assert warnings == []


# ---------------------------------------------------------------- #
# AC-13: env-var / configfile commands → verbatim + unparseable warning
# ---------------------------------------------------------------- #

def test_ac13_env_var_command_logs_unparseable_warning() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/foo.dtsx", "usp_fact_foo", "fact/foo.sql")]
    )
    cmd = 'DTExec /FILE "%SSIS_ROOT%/foo.dtsx"'
    step = _ssis_step(8, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "JobC", manifest, warnings)

    assert out.subsystem == "SSIS"
    assert out.command == cmd
    assert out.original_subsystem is None
    assert len(warnings) == 1
    _job, _id, category, details = warnings[0]
    assert category == "unparseable"
    assert details == "env var present"


def test_ac13_configfile_command_logs_unparseable_warning() -> None:
    from msb_ssis2sql.agent.extractor import maybe_rewrite_step

    manifest = _manifest_with(
        [("fact/foo.dtsx", "usp_fact_foo", "fact/foo.sql")]
    )
    cmd = 'DTExec /FILE "foo.dtsx" /CONFIGFILE "bar.dtsConfig"'
    step = _ssis_step(9, cmd)
    warnings: list[tuple[str, int, str, str]] = []

    out = maybe_rewrite_step(step, "JobD", manifest, warnings)

    assert out.subsystem == "SSIS"
    assert out.command == cmd
    assert out.original_subsystem is None
    assert len(warnings) == 1
    _job, _id, category, details = warnings[0]
    assert category == "unparseable"
    assert details == "config file present"


# ---------------------------------------------------------------- #
# Audit-field shape — Hit returns NEW AgentStep with three Optional[str] fields
# populated; non-hit returns step with audit fields None / unchanged.
# ---------------------------------------------------------------- #

def test_audit_fields_default_none_on_unrewritten_step() -> None:
    """An untouched ``AgentStep`` (constructor default) has all three audit
    fields equal to ``None``. AC-18 requires this so today's golden YAML
    remains byte-identical."""
    step = _tsql_step(1, "EXEC dbo.usp_x")
    assert step.original_subsystem is None
    assert step.original_command is None
    assert step.dtsx_source is None

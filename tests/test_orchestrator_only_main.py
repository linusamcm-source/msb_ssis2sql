"""Orchestrator-only ``main.dtsx`` collapse — AC-1, AC-2, AC-3, AC-9, AC-11, AC-12.

When ``main.dtsx`` is a pure control-flow orchestrator (zero Data Flow Tasks,
one or more ``ExecutePackageTask``s that resolve in-directory), ``convert_tree``
must emit a single proc named after ``main`` whose body is the EXEC sequence —
no separate ``_orchestrator.sql`` file, no empty-body main proc.

These tests target ``plan-final-orch-only-main.md`` Decisions D-1..D-8 and
will fail with the current implementation until T-1..T-4b land. They will
fail with assertion errors against the legacy dual-file output, not import
errors or fixture-not-found errors.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.batch import BatchResult, convert_tree

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_FIRST = FIXTURES / "main_first"
MAIN_FIRST_URL_ENCODED = FIXTURES / "main_first_url_encoded"
MAIN_FIRST_MALFORMED_MAIN = FIXTURES / "main_first_malformed_main"
MAIN_FIRST_ALL_DANGLING = FIXTURES / "main_first_all_dangling"


def test_no_orchestrator_file_when_main_is_orch_only(tmp_path):
    """AC-1: ``main_first/`` emits exactly 3 .sql files; no ``*_orchestrator.sql``."""
    out = tmp_path / "out"
    result = convert_tree(MAIN_FIRST, out)
    assert isinstance(result, BatchResult)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    sql_files = sorted(f.name for f in out.glob("*.sql"))
    assert sql_files == ["childa.sql", "childb.sql", "main.sql"], sql_files

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert orch_files == [], (
        f"collapse must suppress _orchestrator.sql, got {[f.name for f in orch_files]}"
    )

    # _batch_warnings.log always emitted by convert_tree.
    assert (out / "_batch_warnings.log").exists()


def test_main_sql_contains_exec_in_topological_order(tmp_path):
    """AC-2: ``main.sql`` wraps the EXEC sequence in ``usp_main`` in topo order."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)

    main_sql = (out / "main.sql").read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE usp_main" in main_sql, main_sql

    pos_a = main_sql.find("EXEC usp_childa;")
    pos_b = main_sql.find("EXEC usp_childb;")
    assert pos_a != -1, f"main.sql must EXEC usp_childa; got:\n{main_sql}"
    assert pos_b != -1, f"main.sql must EXEC usp_childb; got:\n{main_sql}"
    assert pos_a < pos_b, (
        f"precedence A->B requires usp_childa EXEC before usp_childb EXEC; got:\n{main_sql}"
    )


def test_main_header_does_not_claim_zero_data_flows(tmp_path):
    """AC-3 / D-4 / D-5: collapsed ``main.sql`` advertises the orchestration
    header line, and the no-DFT warning is suppressed in ``_batch_warnings.log``
    (the real surface where D-5's suppression takes effect — the warning text
    never appeared in ``main.sql`` content)."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)

    main_sql = (out / "main.sql").read_text(encoding="utf-8")
    # D-4: header is the orchestration variant, not the DFT variant.
    assert "Orchestration :" in main_sql, (
        f"D-4: collapsed main.sql header must include 'Orchestration :'; got:\n{main_sql}"
    )
    assert "Data flow tasks :" not in main_sql, (
        f"D-4: collapsed main.sql header must NOT advertise data flow tasks; got:\n{main_sql}"
    )

    # D-5: the no-DFT warning must NOT appear in the batch warnings log for an
    # orchestrator-only main (the warning is misleading there).
    warnings_log = (out / "_batch_warnings.log").read_text(encoding="utf-8")
    assert "package has no Data Flow Task" not in warnings_log, (
        f"D-5: orchestrator-only main must suppress the no-DFT warning in "
        f"_batch_warnings.log; got:\n{warnings_log}"
    )


def test_collapse_works_with_url_encoded_disk_names(tmp_path):
    """AC-9: ``main_first_url_encoded/`` collapses to a single ``main.sql``
    containing both EXECs of the URL-decoded child proc names."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST_URL_ENCODED, out)

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert orch_files == [], (
        f"collapse must suppress _orchestrator.sql for url_encoded fixture, got "
        f"{[f.name for f in orch_files]}"
    )

    main_sql = (out / "main.sql").read_text(encoding="utf-8")
    assert "EXEC usp_child_a;" in main_sql, (
        f"main.sql must EXEC usp_child_a; got:\n{main_sql}"
    )
    assert "EXEC usp_child_b;" in main_sql, (
        f"main.sql must EXEC usp_child_b; got:\n{main_sql}"
    )


def test_malformed_main_does_not_abort_directory(tmp_path):
    """AC-11: malformed ``main.dtsx`` records FileOutcome(ok=False), skips
    collapse, continues converting siblings. No ``main.sql`` is emitted."""
    out = tmp_path / "out"
    result = convert_tree(MAIN_FIRST_MALFORMED_MAIN, out)

    assert isinstance(result, BatchResult)
    assert result.failed == 1, (
        f"exactly one failed outcome expected (malformed main.dtsx); got "
        f"{[(o.source.name, o.ok, o.error) for o in result.outcomes]}"
    )

    main_outcomes = [o for o in result.outcomes if o.source.name == "main.dtsx"]
    assert len(main_outcomes) == 1, main_outcomes
    main_outcome = main_outcomes[0]
    assert main_outcome.ok is False
    assert main_outcome.error is not None
    err = main_outcome.error
    assert "parse" in err.lower() or err.startswith("main.dtsx parse failed"), (
        f"error string should signal a parse failure; got {err!r}"
    )

    # Sibling conversion continued.
    assert (out / "childa.sql").exists(), "childa.sql must still be emitted"

    # No main.sql for the failed main.dtsx.
    assert not (out / "main.sql").exists(), (
        "no main.sql should be emitted when main.dtsx parse fails"
    )


def test_all_dangling_epts_falls_back_to_legacy_dual_file(tmp_path):
    """AC-12 / D-1: when every EPT references a missing child (post-filter
    exec_lines empty), the legacy dual-file path runs — empty-body ``main.sql``
    + ``usp_<main>_orchestrator.sql`` + the "no Data Flow Task" warning logged
    to ``_batch_warnings.log`` + a "missing child" warning for each dangling EPT."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST_ALL_DANGLING, out)

    # Both files emitted.
    main_sql_path = out / "main.sql"
    assert main_sql_path.exists(), "legacy dual-file: main.sql must be emitted"
    main_sql = main_sql_path.read_text(encoding="utf-8")
    # Empty-body shape: main.sql contains no EXEC lines (collapse did not fire).
    main_exec_lines = [
        line for line in main_sql.splitlines() if line.strip().startswith("EXEC ")
    ]
    assert main_exec_lines == [], (
        f"legacy dual-file path: main.sql must be empty-body (no EXECs); got:\n{main_sql}"
    )

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, (
        f"legacy dual-file path must emit exactly one _orchestrator.sql, got "
        f"{[f.name for f in orch_files]}"
    )
    orch_sql = orch_files[0].read_text(encoding="utf-8")
    exec_lines = [
        line for line in orch_sql.splitlines() if line.strip().startswith("EXEC ")
    ]
    assert exec_lines == [], (
        f"all-dangling fixture: orchestrator must contain zero EXEC lines; got {exec_lines!r}"
    )

    # _batch_warnings.log records the no-DFT warning AND a missing-child warning
    # for each dangling EPT — both pinned by D-1 / D-5.
    warnings_log = (out / "_batch_warnings.log").read_text(encoding="utf-8")
    assert "no Data Flow Task" in warnings_log, (
        f"legacy dual-file path must surface the no-DFT warning; got:\n{warnings_log}"
    )
    assert "missing child" in warnings_log, (
        f"missing-child warnings expected in _batch_warnings.log; got:\n{warnings_log}"
    )
    assert "nonexistent_a.dtsx" in warnings_log, warnings_log
    assert "nonexistent_b.dtsx" in warnings_log, warnings_log

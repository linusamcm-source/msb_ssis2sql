"""AC-1 happy path: convert-tree wraps every .dtsx in a stored proc, emits main
first, synthesises the orchestrator proc for ``main.dtsx`` with EPTs.

These tests are written against the post-sprint surface described in
``plan-final.md``. They will fail with ``ImportError`` or
``AttributeError`` until the engineer wires up the new modules and the
new orchestrator emitter. See AC-1 in the plan for the contract.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.batch import BatchResult, FileOutcome, convert_tree

FIXTURE = Path(__file__).parent / "fixtures" / "main_first"
# Dual-mode main (1 DFT + 2 EPTs) — preserves the legacy dual-file output
# (main.sql carries the DFT body; *_orchestrator.sql carries the EXECs).
# Used by the retargeted orchestrator-file tests below per D-2 and T-5.
FIXTURE_DUAL_MODE = (
    Path(__file__).parent / "fixtures" / "main_first_main_with_dataflow_multi"
)


def test_main_dtsx_is_converted_first(tmp_path):
    """The outcome for ``main.dtsx`` appears before any child outcome."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert isinstance(result, BatchResult)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    names = [o.source.name for o in result.outcomes]
    assert "main.dtsx" in names, names
    assert names.index("main.dtsx") < names.index("childa.dtsx")
    assert names.index("main.dtsx") < names.index("childb.dtsx")


def test_every_emitted_sql_has_create_or_alter_procedure_header(tmp_path):
    """Every per-package .sql file in main_first/ wraps the body in a proc."""
    out = tmp_path / "out"
    convert_tree(FIXTURE, out)

    expected_sqls = ["main.sql", "childa.sql", "childb.sql"]
    for name in expected_sqls:
        text = (out / name).read_text(encoding="utf-8")
        assert text.startswith("/*") or "CREATE OR ALTER PROCEDURE" in text, name
        assert "CREATE OR ALTER PROCEDURE usp_" in text, (
            f"{name} should be wrapped in a stored procedure"
        )


def test_orchestrator_proc_emitted_when_main_has_execute_package_tasks(tmp_path):
    """Orchestrator file contains EXEC lines for each child, in precedence
    order A then B.

    Retargeted from ``main_first/`` to ``main_first_main_with_dataflow_multi/``
    per T-5: the original ``main_first/`` collapses under D-1 (no
    ``*_orchestrator.sql`` file), but the mixed DFT+EPT fixture preserves the
    legacy dual-file output per D-2.
    """
    out = tmp_path / "out"
    convert_tree(FIXTURE_DUAL_MODE, out)

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, f"expected one orchestrator file, got {[f.name for f in orch_files]}"
    orch_sql = orch_files[0].read_text(encoding="utf-8")

    assert "CREATE OR ALTER PROCEDURE usp_" in orch_sql

    exec_lines = [
        line for line in orch_sql.splitlines() if line.strip().startswith("EXEC ")
    ]
    assert len(exec_lines) >= 2, exec_lines

    text_pos_a = orch_sql.find("childa")
    text_pos_b = orch_sql.find("childb")
    assert text_pos_a != -1 and text_pos_b != -1, (
        "orchestrator should EXEC procs for ChildA and ChildB"
    )
    assert text_pos_a < text_pos_b, (
        f"precedence Success edge A->B must produce A EXEC before B EXEC; got {orch_sql!r}"
    )


def test_file_outcome_has_procedure_name_attribute(tmp_path):
    """Every successful FileOutcome carries the resolved proc-name."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)

    for outcome in result.outcomes:
        assert isinstance(outcome, FileOutcome)
        if outcome.ok:
            assert hasattr(outcome, "procedure_name")
            assert isinstance(outcome.procedure_name, str)
            assert outcome.procedure_name.startswith("usp_")


def test_emitted_exec_names_match_per_package_proc_names(tmp_path):
    """AC-8 trivial: orchestrator EXECs match the per-package proc-names produced.

    Retargeted from ``main_first/`` to ``main_first_main_with_dataflow_multi/``
    per T-5 for the same reason as
    ``test_orchestrator_proc_emitted_when_main_has_execute_package_tasks``.
    """
    out = tmp_path / "out"
    result = convert_tree(FIXTURE_DUAL_MODE, out)

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, f"expected one orchestrator file, got {[f.name for f in orch_files]}"
    orch_sql = orch_files[0].read_text(encoding="utf-8")

    per_pkg = {
        o.source.name: o.procedure_name
        for o in result.outcomes
        if o.ok and o.source.name != "main.dtsx"
    }
    assert len(per_pkg) >= 2, per_pkg
    for proc_name in per_pkg.values():
        assert proc_name in orch_sql, f"orchestrator missing EXEC for {proc_name}"

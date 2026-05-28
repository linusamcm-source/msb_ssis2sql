"""AC-1 main-with-zero-EPTs: emit ``usp_<...>_<MainPackageName>`` carrying
main's own data flow as proc body. No synthesised orchestrator.

Will fail with ImportError / AttributeError until the engineer wires up
the new emitter (plan-final.md §Decisions: "Main with zero ExecutePackageTasks").
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.batch import convert_tree

FIXTURE = Path(__file__).parent / "fixtures" / "main_first_no_children"


def test_no_synthesised_orchestrator_when_main_has_zero_epts(tmp_path):
    """Only one .sql is emitted: main's own wrapped proc."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    sqls = sorted(p.name for p in out.rglob("*.sql"))
    assert sqls == ["main.sql"], sqls


def test_main_proc_carries_main_data_flow_as_body(tmp_path):
    """Main with zero EPTs: proc body is main's data-flow, not EXECs."""
    out = tmp_path / "out"
    convert_tree(FIXTURE, out)
    main_sql = (out / "main.sql").read_text(encoding="utf-8")

    assert "CREATE OR ALTER PROCEDURE usp_" in main_sql
    assert "INSERT INTO" in main_sql, "main's data flow should be transpiled"
    assert "EXEC " not in main_sql or main_sql.count("EXEC ") == 0, (
        "main with zero EPTs must not contain EXEC stubs"
    )


def test_main_proc_name_matches_main_package_name(tmp_path):
    """Proc-name is ``usp_<reldir>_<MainObjectName>``; reldir here is the fixture dir."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)

    main_outcome = next(o for o in result.outcomes if o.ok and o.source.name == "main.dtsx")
    assert hasattr(main_outcome, "procedure_name")
    assert "main" in main_outcome.procedure_name.lower(), (
        main_outcome.procedure_name
    )

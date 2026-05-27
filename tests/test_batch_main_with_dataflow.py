"""C-1 regression: main.dtsx with BOTH a data flow AND ≥1 ExecutePackageTask.

Verifies that the per-package main.sql retains the data-flow content AND
the orchestrator file (distinct path) contains the EXEC statements.
"""
from __future__ import annotations

import re
from pathlib import Path

from msb_ssis2sql.batch import convert_tree

FIXTURE = Path(__file__).parent / "fixtures" / "main_first_main_with_dataflow"


def test_main_dataflow_body_survives_when_orchestrator_emitted(tmp_path):
    """main.sql keeps its data-flow SQL; orchestrator is a separate file with EXECs."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    main_sql = (out / "main.sql").read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE" in main_sql, "main.sql must be a wrapped proc"
    assert "dbo.DestMain" in main_sql or "SELECT" in main_sql, (
        "main.sql must contain data-flow content (not replaced by orchestrator)"
    )
    assert "EXEC usp_" not in main_sql, (
        "main.sql must not contain EXEC lines — those belong in the orchestrator"
    )

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, (
        f"expected exactly one orchestrator file, got {[f.name for f in orch_files]}"
    )
    orch_sql = orch_files[0].read_text(encoding="utf-8")
    assert "EXEC" in orch_sql, "orchestrator file must contain EXEC statements"
    assert "child" in orch_sql.lower(), "orchestrator must EXEC the child proc"

    # Both files must have CREATE OR ALTER PROCEDURE, but with DIFFERENT proc names.
    def _extract_proc_name(sql: str) -> str | None:
        m = re.search(r"CREATE OR ALTER PROCEDURE\s+(\S+)", sql)
        return m.group(1) if m else None

    main_proc = _extract_proc_name(main_sql)
    orch_proc = _extract_proc_name(orch_sql)
    assert main_proc is not None, "main.sql must declare a CREATE OR ALTER PROCEDURE"
    assert orch_proc is not None, "orchestrator must declare a CREATE OR ALTER PROCEDURE"
    assert main_proc != orch_proc, (
        f"main proc ({main_proc!r}) and orchestrator proc ({orch_proc!r}) must not share the same name"
    )
    assert orch_proc == f"{main_proc}_orchestrator", (
        f"orchestrator proc name must be {{main_proc}}_orchestrator; got {orch_proc!r}"
    )


def test_no_duplicate_proc_names_in_emitted_sql_files(tmp_path):
    """No two .sql files in the same output directory may declare the same proc name."""
    out = tmp_path / "out"
    convert_tree(FIXTURE, out)

    seen: dict[str, Path] = {}
    for sql_file in sorted(out.rglob("*.sql")):
        if sql_file.name.startswith("_"):
            continue
        text = sql_file.read_text(encoding="utf-8")
        m = re.search(r"CREATE OR ALTER PROCEDURE\s+(\S+)", text)
        if m:
            proc_name = m.group(1)
            assert proc_name not in seen, (
                f"proc name {proc_name!r} declared in both {seen[proc_name]} and {sql_file}"
            )
            seen[proc_name] = sql_file

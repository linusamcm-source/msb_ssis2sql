"""AC-1 missing-main fallback: synthesise ``usp_<SanitisedDir>_Main`` that
EXECs each child proc alphabetically; emit a warning into BatchResult.

Will fail with ImportError / AttributeError until the new batch.py emitter
ships per plan-final.md.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.batch import convert_tree

FIXTURE = Path(__file__).parent / "fixtures" / "main_first_no_main"


def test_synthesised_main_proc_emitted(tmp_path):
    """A directory without main.dtsx still gets a synthesised orchestrator file."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    sqls = sorted(p.name for p in out.iterdir() if p.suffix == ".sql")
    assert any(name.lower().endswith("_main.sql") for name in sqls), (
        f"expected a synthesised _Main.sql, got {sqls}"
    )


def test_synthesised_main_warns_into_batch_result(tmp_path):
    """The synthesis path must surface a warning so downstream tooling sees it."""
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)

    all_warnings = [w for o in result.outcomes for w in o.warnings]
    joined = "\n".join(all_warnings)
    assert "main" in joined.lower() and (
        "synthesised" in joined.lower()
        or "synthesized" in joined.lower()
        or "missing" in joined.lower()
        or "absent" in joined.lower()
    ), f"no synthesis warning surfaced: {all_warnings}"


def test_synthesised_main_execs_children_alphabetically(tmp_path):
    """The synthesised proc body EXECs child procs in sorted order."""
    out = tmp_path / "out"
    convert_tree(FIXTURE, out)

    main_sql_candidates = [p for p in out.iterdir() if p.name.lower().endswith("_main.sql")]
    assert main_sql_candidates, list(out.iterdir())
    body = main_sql_candidates[0].read_text(encoding="utf-8")

    pos_a = body.find("nomainchilda")
    pos_b = body.find("nomainchildb")
    assert pos_a != -1 and pos_b != -1, (
        "synthesised main proc should EXEC both child procs"
    )
    assert pos_a < pos_b, "EXECs in synthesised main must be alpha-sorted"

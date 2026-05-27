"""AC-8 + negative-path coverage for the batch + control-graph layers.

The central test is ``test_sanitiser_collision_orchestrator_exec_names_match_files``:
when three child packages sanitise to the same proc-name, the per-directory
collision-suffix rule MUST be self-consistent — the orchestrator's ``EXEC``
lines must reference the same suffixed proc-names that the per-package
``.sql`` files actually emit.

Surrounding tests cover dangling refs, missing children, ObjectName collisions,
nested orchestration (warned), and a sequence-container reference (warned +
skipped). The ``outside_dir`` case is covered by a sibling test.
"""
from __future__ import annotations

import re
from pathlib import Path

from msb_ssis2sql.batch import convert_tree

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# AC-8 CRITICAL: sanitiser within-run self-consistency
# --------------------------------------------------------------------------- #

def test_sanitiser_collision_orchestrator_exec_names_match_files(tmp_path):
    """Three children that all sanitise to ``foo_bar`` get _2 / _3 suffixes.

    Sort order is by original filename (case-sensitive ASCII):
      Foo Bar.dtsx (space, 0x20) < Foo.Bar.dtsx (dot, 0x2e) < Foo_Bar.dtsx (_, 0x5f)

    Suffix rule: first keeps un-suffixed, 2nd gets ``_2``, 3rd gets ``_3``.
    Per the proc-name namespacer, the resulting names use the rel-dir as a
    prefix segment: ``usp_main_first_sanitiser_collision_foo_bar`` etc.

    The orchestrator's EXEC lines and the actual .sql files emitted must
    address the SAME proc names.
    """
    src = FIXTURES / "main_first_sanitiser_collision"
    out = tmp_path / "out"
    result = convert_tree(src, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    orch_files = list(out.glob("*_orchestrator.sql"))
    assert len(orch_files) == 1, f"expected one orchestrator file, got {[f.name for f in orch_files]}"
    main_sql_text = orch_files[0].read_text(encoding="utf-8")

    # Pull every EXEC ... name from the orchestrator body.
    exec_names = set(re.findall(r"EXEC\s+(usp_[a-z0-9_]+)\b", main_sql_text, re.IGNORECASE))
    assert len(exec_names) == 3, f"orchestrator should EXEC three child procs, got {exec_names}"

    # Cross-check: each EXEC name must correspond to an emitted child outcome.
    emitted_procs = {
        o.procedure_name for o in result.outcomes
        if o.ok and o.source.name != "main.dtsx"
    }
    assert exec_names == emitted_procs, (
        f"orchestrator EXEC names {exec_names} drift from per-package procs {emitted_procs}"
    )

    # And every EXEC name must correspond to text appearing in the per-package
    # .sql header line (i.e. CREATE OR ALTER PROCEDURE <name>).
    for proc_name in exec_names:
        found = False
        for sql_path in out.glob("*.sql"):
            if sql_path.name == "main.sql":
                continue
            text = sql_path.read_text(encoding="utf-8")
            if f"CREATE OR ALTER PROCEDURE {proc_name}" in text:
                found = True
                break
        assert found, f"no emitted .sql has 'CREATE OR ALTER PROCEDURE {proc_name}'"


def test_sanitiser_collision_suffix_assigned_in_sorted_original_order(tmp_path):
    """Original-name sort order maps to _, _2, _3 in that exact order."""
    src = FIXTURES / "main_first_sanitiser_collision"
    out = tmp_path / "out"
    result = convert_tree(src, out)

    # Map: original filename stem -> proc_name.
    name_map = {
        o.source.stem: o.procedure_name
        for o in result.outcomes
        if o.ok and o.source.name != "main.dtsx"
    }
    # Sorted case-sensitive: "Foo Bar" < "Foo.Bar" < "Foo_Bar".
    sorted_originals = sorted(name_map.keys())
    assert sorted_originals == ["Foo Bar", "Foo.Bar", "Foo_Bar"], sorted_originals

    # First keeps un-suffixed; second gets _2; third gets _3.
    proc0 = name_map["Foo Bar"]
    proc1 = name_map["Foo.Bar"]
    proc2 = name_map["Foo_Bar"]
    assert not proc0.endswith("_2") and not proc0.endswith("_3"), proc0
    assert proc1.endswith("_2"), proc1
    assert proc2.endswith("_3"), proc2


# --------------------------------------------------------------------------- #
# negative paths — dangling, missing-child, collision, nested, container
# --------------------------------------------------------------------------- #

def test_dangling_precedence_ref_does_not_crash(tmp_path):
    """A precedence ref pointing at a non-existent EPT is warned, not fatal."""
    src = FIXTURES / "main_first_dangling"
    out = tmp_path / "out"
    result = convert_tree(src, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    all_warnings = " ".join(w for o in result.outcomes for w in o.warnings)
    assert "EPT_NOWHERE" in all_warnings or "dangling" in all_warnings.lower(), (
        f"expected a dangling-precedence warning, got: {all_warnings}"
    )


def test_missing_child_dtsx_warned_not_fatal(tmp_path):
    """An EPT referencing a missing .dtsx is warned, not fatal."""
    src = FIXTURES / "main_first_missing_child"
    out = tmp_path / "out"
    result = convert_tree(src, out)

    all_warnings = " ".join(w for o in result.outcomes for w in o.warnings)
    assert "does_not_exist" in all_warnings or "missing" in all_warnings.lower(), (
        f"expected a missing-child warning, got: {all_warnings}"
    )


def test_object_name_collision_in_two_files_is_handled(tmp_path):
    """Two .dtsx with the same ObjectName resolve to different proc-names by file basename."""
    src = FIXTURES / "main_first_collision"
    out = tmp_path / "out"
    convert_tree(src, out)
    # Both files must still emit; the namespacer key is the file stem, not ObjectName.
    sql_names = sorted(p.name for p in out.glob("*.sql"))
    assert "x1.sql" in sql_names and "x2.sql" in sql_names, sql_names


def test_nested_orchestration_warns(tmp_path):
    """A child that itself contains an ExecutePackageTask is warned + flagged."""
    src = FIXTURES / "main_first_nested"
    out = tmp_path / "out"
    result = convert_tree(src, out)
    all_warnings = " ".join(w for o in result.outcomes for w in o.warnings)
    assert "nested" in all_warnings.lower() or "grandchild" in all_warnings.lower(), (
        f"nested orchestration must warn, got: {all_warnings}"
    )


def test_container_precedence_target_warns_and_skips_edge(tmp_path):
    """A precedence edge pointing at a sequence-container is warned + skipped."""
    src = FIXTURES / "main_first_container"
    out = tmp_path / "out"
    result = convert_tree(src, out)
    all_warnings = " ".join(w for o in result.outcomes for w in o.warnings)
    assert "container" in all_warnings.lower() or "skip" in all_warnings.lower(), (
        f"container reference must warn, got: {all_warnings}"
    )


def test_outside_directory_child_is_rejected_with_warning(tmp_path):
    """An EPT referring to ``../sibling.dtsx`` is rejected, warned."""
    src = FIXTURES / "main_first_outside_dir"
    out = tmp_path / "out"
    result = convert_tree(src, out)
    all_warnings = " ".join(w for o in result.outcomes for w in o.warnings)
    assert "outside" in all_warnings.lower() or "sibling" in all_warnings.lower(), (
        f"outside-dir child must warn, got: {all_warnings}"
    )

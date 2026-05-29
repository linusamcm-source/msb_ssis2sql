"""AC-14 + D-7 + D-8 coverage for the ``_agent_warnings.log`` writer.

The plan declares (T-7) that ``extract_agent_jobs`` accumulates
``(job_name, step_id, category, details)`` tuples and writes them to
``<out_dir>/_agent_warnings.log``, sorted by ``(job_name, step_id)``,
prepending the ``manifest_absent`` notice only when no manifest was
supplied (D-7).

Tests here exercise a public helper ``write_agent_warnings_log``. The
function is expected to live on the extractor module — wire-up is the
engineer's job in T-7.

Module under test does not yet expose ``write_agent_warnings_log`` —
failures here will be ``ImportError`` / ``AttributeError`` until T-7
lands.
"""
from __future__ import annotations


def _sample_warnings() -> list[tuple[str, int, str, str]]:
    """Deliberately unsorted batch of (job, step_id, category, details)."""
    return [
        ("ZetaJob", 2, "unresolved", "missing.dtsx"),
        ("AlphaJob", 1, "unparseable", "no /FILE, /ISSERVER, or /SQL flag found"),
        ("AlphaJob", 3, "ambiguous_basename", "fact/x.dtsx, dim/x.dtsx"),
        ("AlphaJob", 2, "unparseable", "env var present"),
    ]


def test_log_lines_sorted_by_job_then_step_id(tmp_path) -> None:
    """D-8 determinism: lines are sorted by ``(job_name, step_id)``."""
    from msb_ssis2sql.agent.extractor import write_agent_warnings_log

    out = tmp_path / "_agent_warnings.log"
    write_agent_warnings_log(out, _sample_warnings(), manifest_supplied=True)
    text = out.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln]
    # Sorted lexically by job_name then numerically by step_id.
    expected_order = [
        "AlphaJob:1: unparseable: no /FILE, /ISSERVER, or /SQL flag found",
        "AlphaJob:2: unparseable: env var present",
        "AlphaJob:3: ambiguous_basename: fact/x.dtsx, dim/x.dtsx",
        "ZetaJob:2: unresolved: missing.dtsx",
    ]
    assert lines == expected_order, lines


def test_log_is_byte_identical_across_two_runs(tmp_path) -> None:
    """AC-14 byte-determinism: two writes against the same warnings produce
    byte-identical output."""
    from msb_ssis2sql.agent.extractor import write_agent_warnings_log

    out1 = tmp_path / "first" / "_agent_warnings.log"
    out2 = tmp_path / "second" / "_agent_warnings.log"
    out1.parent.mkdir(parents=True)
    out2.parent.mkdir(parents=True)
    write_agent_warnings_log(out1, _sample_warnings(), manifest_supplied=True)
    write_agent_warnings_log(out2, _sample_warnings(), manifest_supplied=True)
    assert out1.read_bytes() == out2.read_bytes()


def test_log_top_notice_line_when_manifest_absent(tmp_path) -> None:
    """D-7: when manifest is absent the literal notice line is at position 0,
    BEFORE all sorted warning lines."""
    from msb_ssis2sql.agent.extractor import write_agent_warnings_log

    out = tmp_path / "_agent_warnings.log"
    warnings = [
        ("AlphaJob", 1, "manifest_absent", "DTExec /FILE foo.dtsx"),
        ("AlphaJob", 2, "manifest_absent", "DTExec /FILE bar.dtsx"),
    ]
    write_agent_warnings_log(out, warnings, manifest_supplied=False)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "manifest not supplied — all SSIS steps emitted verbatim"
    # Remaining lines are the sorted warnings.
    assert lines[1:] == [
        "AlphaJob:1: manifest_absent: DTExec /FILE foo.dtsx",
        "AlphaJob:2: manifest_absent: DTExec /FILE bar.dtsx",
    ]


def test_log_no_notice_line_when_manifest_supplied(tmp_path) -> None:
    """The manifest-absent notice line is ONLY prepended when the manifest
    was missing. Supplied-manifest runs must NOT carry the notice."""
    from msb_ssis2sql.agent.extractor import write_agent_warnings_log

    out = tmp_path / "_agent_warnings.log"
    write_agent_warnings_log(out, _sample_warnings(), manifest_supplied=True)
    text = out.read_text(encoding="utf-8")
    assert "manifest not supplied" not in text


def test_log_is_empty_when_no_warnings_and_manifest_supplied(tmp_path) -> None:
    """T-7: empty warnings list → zero-byte file (matches batch_warnings convention)."""
    from msb_ssis2sql.agent.extractor import write_agent_warnings_log

    out = tmp_path / "_agent_warnings.log"
    write_agent_warnings_log(out, [], manifest_supplied=True)
    assert out.exists()
    assert out.read_bytes() == b""

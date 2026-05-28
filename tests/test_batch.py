"""Tests for msb_ssis2sql.batch — Story 1.

Every test maps to one Story 1 Acceptance Criterion; the mapping is noted inline.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# This import will raise ModuleNotFoundError until GREEN is implemented.
from msb_ssis2sql.batch import BatchResult, FileOutcome, convert_tree

# Path to the real .dtsx used as "valid package content" across tests.
_SALES_ETL = Path(__file__).parent.parent / "examples" / "sales_etl.dtsx"


# ---------------------------------------------------------------------------
# AC 1 — Mirroring: nested input tree produces mirrored .sql output.
# ---------------------------------------------------------------------------

def test_convert_tree_mirrors_nested_directory_structure(tmp_path):
    """a/b/pkg.dtsx -> output/a/b/pkg.sql, non-empty."""
    # Build input tree.
    nested = tmp_path / "input" / "a" / "b"
    nested.mkdir(parents=True)
    shutil.copy(_SALES_ETL, nested / "pkg.dtsx")

    output_root = tmp_path / "output"
    result = convert_tree(tmp_path / "input", output_root)

    expected = output_root / "a" / "b" / "pkg.sql"
    assert expected.exists(), f"{expected} was not created"
    assert expected.stat().st_size > 0, "output .sql file is empty"
    assert result.converted >= 1


# ---------------------------------------------------------------------------
# AC 2 — Skip dirs: bin/ and obj/ subtrees are ignored.
# ---------------------------------------------------------------------------

def test_convert_tree_skips_bin_subdir(tmp_path):
    """A .dtsx inside bin/ is not converted and absent from output."""
    bin_dir = tmp_path / "input" / "bin"
    bin_dir.mkdir(parents=True)
    shutil.copy(_SALES_ETL, bin_dir / "pkg.dtsx")

    output_root = tmp_path / "output"
    result = convert_tree(tmp_path / "input", output_root)

    assert result.converted == 0, "bin/ package should have been skipped"
    assert result.failed == 0, "skipped package must not appear as failed"
    assert not (output_root / "bin" / "pkg.sql").exists()


def test_convert_tree_skips_obj_subdir(tmp_path):
    """A .dtsx inside obj/ is not converted and absent from output."""
    obj_dir = tmp_path / "input" / "obj"
    obj_dir.mkdir(parents=True)
    shutil.copy(_SALES_ETL, obj_dir / "pkg.dtsx")

    output_root = tmp_path / "output"
    result = convert_tree(tmp_path / "input", output_root)

    assert result.converted == 0
    assert result.failed == 0
    assert not (output_root / "obj" / "pkg.sql").exists()


# ---------------------------------------------------------------------------
# AC 3 — Bad input: non-existent directory raises NotADirectoryError.
# ---------------------------------------------------------------------------

def test_convert_tree_raises_on_nonexistent_input(tmp_path):
    """Passing a path that does not exist raises NotADirectoryError."""
    with pytest.raises(NotADirectoryError):
        convert_tree(tmp_path / "does_not_exist", tmp_path / "output")


# ---------------------------------------------------------------------------
# AC 4 — Empty tree: no .dtsx files -> converted == 0 and failed == 0.
# ---------------------------------------------------------------------------

def test_convert_tree_empty_directory_returns_zero_counts(tmp_path):
    """An input directory with no .dtsx files returns BatchResult with both counts 0."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_dir = tmp_path / "output"

    result = convert_tree(input_dir, output_dir)

    assert isinstance(result, BatchResult)
    assert result.converted == 0
    assert result.failed == 0
    assert result.outcomes == []


# ---------------------------------------------------------------------------
# AC 5 — Failure isolation: malformed .dtsx is recorded as failed, sibling succeeds.
# ---------------------------------------------------------------------------

def test_convert_tree_isolates_failure_and_continues(tmp_path):
    """Bad XML is recorded as a failed FileOutcome; a valid sibling still converts."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    # Valid package.
    shutil.copy(_SALES_ETL, input_dir / "good.dtsx")
    # Malformed XML.
    (input_dir / "bad.dtsx").write_text("<not valid", encoding="utf-8")

    output_dir = tmp_path / "output"
    result = convert_tree(input_dir, output_dir)

    failed_outcomes = [o for o in result.outcomes if not o.ok]

    assert result.failed >= 1, "malformed package must be recorded as failed"
    assert result.converted >= 1, "valid sibling must still be converted"

    bad = next((o for o in failed_outcomes if o.source.name == "bad.dtsx"), None)
    assert bad is not None, "bad.dtsx not in failed outcomes"
    assert bad.ok is False
    assert bad.error is not None and len(bad.error) > 0


def test_failed_outcome_fields(tmp_path):
    """FileOutcome for a malformed package has ok=False and a non-empty error string."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "broken.dtsx").write_text("<not valid", encoding="utf-8")

    result = convert_tree(input_dir, tmp_path / "output")

    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert isinstance(outcome, FileOutcome)
    assert outcome.ok is False
    assert isinstance(outcome.error, str)
    assert len(outcome.error) > 0


# ---------------------------------------------------------------------------
# AC 6 — str | Path: both types accepted for input_root and output_root.
# ---------------------------------------------------------------------------

def test_convert_tree_accepts_str_arguments(tmp_path):
    """convert_tree must accept plain str for both roots."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(_SALES_ETL, input_dir / "pkg.dtsx")

    result = convert_tree(str(input_dir), str(tmp_path / "output"))

    assert result.converted >= 1


def test_convert_tree_accepts_path_arguments(tmp_path):
    """convert_tree must accept pathlib.Path for both roots."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(_SALES_ETL, input_dir / "pkg.dtsx")

    result = convert_tree(input_dir, tmp_path / "output")

    assert result.converted >= 1


# ---------------------------------------------------------------------------
# BatchResult property sanity checks.
# ---------------------------------------------------------------------------

def test_batch_result_converted_and_failed_are_properties(tmp_path):
    """BatchResult.converted and .failed are computed from outcomes list."""
    result = BatchResult()
    assert result.converted == 0
    assert result.failed == 0

    result.outcomes.append(FileOutcome(
        source=Path("a.dtsx"), destination=Path("a.sql"), ok=True
    ))
    result.outcomes.append(FileOutcome(
        source=Path("b.dtsx"), destination=Path("b.sql"), ok=False, error="boom"
    ))
    assert result.converted == 1
    assert result.failed == 1


# ---------------------------------------------------------------------------
# SEC-H1 — Symlink write-escape: a symlinked subdir inside output_root must not
# let a write land outside output_root.
# ---------------------------------------------------------------------------

def test_convert_tree_refuses_symlink_escape_in_output(tmp_path):
    """A symlink inside output_root pointing outside it must not be written through."""
    # Build a valid input .dtsx.
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(_SALES_ETL, input_dir / "pkg.dtsx")

    # Create output_root and plant a symlink that points outside it.
    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # output_root/escape -> ../outside  (symlink to a dir outside output_root)
    (output_root / "escape").symlink_to(outside, target_is_directory=True)

    # Place the .dtsx under input/escape/ so its mirrored dst is output/escape/pkg.sql,
    # which resolves to outside/pkg.sql — outside output_root.
    (input_dir / "escape").mkdir()
    shutil.copy(_SALES_ETL, input_dir / "escape" / "pkg.dtsx")

    result = convert_tree(input_dir, output_root)

    # No write must land outside output_root.
    assert not (outside / "pkg.sql").exists(), (
        "convert_tree wrote through a symlink to a path outside output_root"
    )
    # The escape attempt must be recorded as a failed FileOutcome — not silently dropped.
    escape_outcomes = [
        o for o in result.outcomes
        if o.source == input_dir / "escape" / "pkg.dtsx"
    ]
    assert len(escape_outcomes) == 1, "expected exactly one outcome for the symlink-escaped file"
    outcome = escape_outcomes[0]
    assert outcome.ok is False, "symlink-escape outcome must have ok=False"
    assert outcome.error and len(outcome.error) > 0, "symlink-escape outcome must have a non-empty error"


def test_convert_tree_mkdir_does_not_escape_via_deep_symlink(tmp_path):
    """mkdir must not create directories outside output_root through a deep symlink path.

    When output_root/esc is a symlink to an external directory and the input has
    input/esc/deeper/pkg.dtsx, dst.parent is output_root/esc/deeper/ which resolves
    to outside/deeper/. The confine check must fire BEFORE mkdir so that outside/deeper/
    is never created on disk.
    """
    input_dir = tmp_path / "input"
    (input_dir / "esc" / "deeper").mkdir(parents=True)
    shutil.copy(_SALES_ETL, input_dir / "esc" / "deeper" / "pkg.dtsx")

    output_root = tmp_path / "output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # output_root/esc -> ../outside  (symlink; dst.parent resolves to outside/deeper/)
    (output_root / "esc").symlink_to(outside, target_is_directory=True)

    result = convert_tree(input_dir, output_root)

    # No directory must have been created inside the symlink target.
    assert not (outside / "deeper").exists(), (
        "convert_tree created a directory outside output_root via symlink before the confine check"
    )
    # The file is a failed FileOutcome with a non-empty error.
    escape_outcomes = [
        o for o in result.outcomes
        if o.source == input_dir / "esc" / "deeper" / "pkg.dtsx"
    ]
    assert len(escape_outcomes) == 1
    assert escape_outcomes[0].ok is False
    assert escape_outcomes[0].error and len(escape_outcomes[0].error) > 0

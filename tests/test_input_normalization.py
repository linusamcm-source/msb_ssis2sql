"""Input normalization: convert-tree reads from a whitespace/%20-free staged copy."""
from __future__ import annotations

import shutil
from pathlib import Path

from msb_ssis2sql.batch import _stage_normalized_tree, convert_tree

EXAMPLE = Path(__file__).parent.parent / "examples" / "sales_etl.dtsx"


# --------------------------------------------------------------------------- #
# _stage_normalized_tree
# --------------------------------------------------------------------------- #

def test_stage_normalizes_spaces_and_percent20(tmp_path):
    src = tmp_path / "in"
    (src / "sub dir").mkdir(parents=True)
    (src / "My Package.dtsx").write_text("<a/>", encoding="utf-8")
    (src / "Child%20A.dtsx").write_text("<b/>", encoding="utf-8")
    (src / "sub dir" / "Nested File.dtsx").write_text("<c/>", encoding="utf-8")

    staged_root, mapping = _stage_normalized_tree(src)
    try:
        staged_names = sorted(p.relative_to(staged_root).as_posix() for p in staged_root.rglob("*.dtsx"))
        assert staged_names == [
            "Child_A.dtsx",
            "My_Package.dtsx",
            "sub_dir/Nested_File.dtsx",
        ]
        # original is untouched
        assert (src / "My Package.dtsx").exists()
        assert (src / "Child%20A.dtsx").exists()
        # map points originals at their staged copies, content preserved
        assert mapping[src / "My Package.dtsx"].read_text() == "<a/>"
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)


def test_stage_dedups_names_that_collide_after_normalization(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "A B.dtsx").write_text("<one/>", encoding="utf-8")
    (src / "A_B.dtsx").write_text("<two/>", encoding="utf-8")

    staged_root, mapping = _stage_normalized_tree(src)
    try:
        names = sorted(p.name for p in staged_root.rglob("*.dtsx"))
        assert names == ["A_B.dtsx", "A_B_2.dtsx"]  # distinct files, no clobber
        # the two distinct originals map to two distinct staged files
        assert mapping[src / "A B.dtsx"] != mapping[src / "A_B.dtsx"]
        assert {p.read_text() for p in mapping.values()} == {"<one/>", "<two/>"}
    finally:
        shutil.rmtree(staged_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# convert_tree end-to-end with a spaced input filename
# --------------------------------------------------------------------------- #

def test_convert_tree_reads_spaced_input_and_leaves_it_untouched(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    spaced = src / "My Sales ETL.dtsx"
    shutil.copy(EXAMPLE, spaced)

    out = tmp_path / "out"
    result = convert_tree(src, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    # Output filename is whitespace-free; original input is untouched.
    assert (out / "My_Sales_ETL.sql").exists()
    assert spaced.exists()

    # No temp staging path leaks into the SQL header; the original path is shown.
    sql = (out / "My_Sales_ETL.sql").read_text(encoding="utf-8")
    assert "My Sales ETL.dtsx" in sql            # original source path in header
    assert "msb_ssis2sql_input_" not in sql      # staging temp dir name must not leak

    # FileOutcome.source reports the original (spaced) file, not the staged copy.
    assert any(o.source == spaced for o in result.outcomes)


def test_no_staging_temp_dirs_left_behind(tmp_path):
    import tempfile

    src = tmp_path / "in"
    src.mkdir()
    shutil.copy(EXAMPLE, src / "pkg.dtsx")
    before = set(Path(tempfile.gettempdir()).glob("msb_ssis2sql_input_*"))
    convert_tree(src, tmp_path / "out")
    after = set(Path(tempfile.gettempdir()).glob("msb_ssis2sql_input_*"))
    assert before == after, "staging temp dir was not cleaned up"

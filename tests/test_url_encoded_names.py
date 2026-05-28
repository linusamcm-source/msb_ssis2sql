"""URL-encoded package names ('%20' etc) must match across XML refs and disk.

SSIS-export tools sometimes write '%20' into both filenames on disk and the
``<PackageName>`` / ``<PackagePath>`` text inside ExecutePackageTasks. The
orchestrator emitter matches EPT references against directory file stems;
if one side decodes percent-escapes and the other does not, every match
fails silently as a 'missing child' warning. The fixes:

* :func:`msb_ssis2sql.util.decode_package_name` is the single canonical
  decoder used at both read sites.
* :func:`msb_ssis2sql.parser._parse_execute_package_task` decodes
  ``PackageName`` / ``PackagePath`` / ``PackageNameFromProjectReference``.
* :func:`msb_ssis2sql.batch.convert_tree` decodes disk-file stems through
  ``_decoded_stem`` before keying ``proc_name_by_stem`` and computing
  ``dir_file_names``.
* :func:`msb_ssis2sql.parser.parse_file` falls back to URL-encoded /
  URL-decoded siblings if the literal path does not exist.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.batch import convert_tree
from msb_ssis2sql.parser import parse_file, parse_string
from msb_ssis2sql.util import decode_package_name

FIXTURE = Path(__file__).parent / "fixtures" / "main_first_url_encoded"


def test_decode_package_name_replaces_percent_twenty():
    assert decode_package_name("Child%20A.dtsx") == "Child A.dtsx"


def test_decode_package_name_passthrough_for_plain():
    assert decode_package_name("childa.dtsx") == "childa.dtsx"


def test_decode_package_name_handles_multiple_escapes():
    assert decode_package_name("My%20Big%20File.dtsx") == "My Big File.dtsx"


def test_parser_decodes_execute_package_task_package_name():
    """EPT ``<PackageName>Child%20A.dtsx</PackageName>`` becomes 'Child A.dtsx'."""
    xml = (FIXTURE / "main.dtsx").read_text(encoding="utf-8")
    pkg = parse_string(xml)
    names = [ept.package_name for ept in pkg.execute_package_tasks]
    assert "Child A.dtsx" in names
    assert "Child B.dtsx" in names
    # The encoded form must not survive.
    assert all("%20" not in n for n in names)


def test_parse_file_falls_back_to_encoded_sibling(tmp_path):
    """parse_file('Child A.dtsx') resolves a 'Child%20A.dtsx' on disk."""
    encoded = tmp_path / "Child%20A.dtsx"
    encoded.write_bytes((FIXTURE / "Child%20A.dtsx").read_bytes())
    # Caller asks for the decoded display name; file on disk is encoded.
    pkg = parse_file(tmp_path / "Child A.dtsx")
    assert pkg.name == "ChildA"


def test_convert_tree_matches_encoded_disk_files_against_decoded_ept_refs(tmp_path):
    """Orchestrator wires EPTs to children even when disk has '%20' names.

    Under the orch-only collapse (D-1), main.dtsx has zero DFTs and both EPTs
    resolve in-directory, so main.sql carries the EXECs directly — no separate
    ``*_orchestrator.sql`` file is emitted (AC-9).
    """
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert result.failed == 0, [o.error for o in result.outcomes if not o.ok]

    # No 'missing child' warning — every EPT ref must resolve to a real file.
    all_warnings = [w for o in result.outcomes for w in o.warnings]
    assert not any("missing child" in w for w in all_warnings), all_warnings

    # D-1 collapse: EXECs land in main.sql; no separate _orchestrator.sql.
    assert list(out.glob("*_orchestrator.sql")) == []
    main_sql = (out / "main.sql").read_text(encoding="utf-8")
    # Both child procs must appear; their names are derived from the
    # decoded stems 'Child A' / 'Child B'.
    assert "EXEC usp_child_a;" in main_sql, main_sql
    assert "EXEC usp_child_b;" in main_sql, main_sql


def test_convert_tree_emits_child_sql_files_using_decoded_proc_names(tmp_path):
    """Per-file .sql outputs are keyed off the decoded disk-stem."""
    out = tmp_path / "out"
    convert_tree(FIXTURE, out)
    # The output filename mirrors the disk path (encoded), but the proc
    # name inside is the decoded sanitised form.
    child_a_sql = (out / "Child%20A.sql").read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE usp_child_a" in child_a_sql, child_a_sql

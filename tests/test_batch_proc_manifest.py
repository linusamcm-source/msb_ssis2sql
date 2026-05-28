"""AC-1..AC-3 end-to-end coverage for the new ``_proc_manifest.json`` emit
in ``convert_tree`` (T-1).

Runs ``convert_tree`` against the existing ``tests/fixtures/main_first/``
and asserts the manifest contract:
* file exists at ``<output_root>/_proc_manifest.json``
* parses as JSON, ``version == 1``, ``input_root`` is the absolute
  resolved path, ``entries`` is a list (AC-1)
* every entry has exactly three string fields ``dtsx``, ``proc``,
  ``out_sql``; all paths use ``/`` separators (AC-3)
* two consecutive runs produce byte-identical manifests (AC-2)

Tests must fail RIGHT NOW because ``convert_tree`` does not yet write
``_proc_manifest.json``. Failure mode is ``FileNotFoundError`` /
assertion.
"""
from __future__ import annotations

import json
from pathlib import Path

from msb_ssis2sql.batch import convert_tree

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_FIRST = FIXTURES / "main_first"
MAIN_FIRST_URL_ENCODED = FIXTURES / "main_first_url_encoded"


def test_convert_tree_writes_proc_manifest(tmp_path) -> None:
    """AC-1: ``_proc_manifest.json`` exists; ``version == 1``; ``entries`` is a list."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)

    manifest_path = out / "_proc_manifest.json"
    assert manifest_path.exists(), (
        f"_proc_manifest.json should be written next to _batch_warnings.log; "
        f"out contents: {sorted(p.name for p in out.iterdir())}"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    # input_root is absolute and matches the resolved input path.
    assert data["input_root"] == str(MAIN_FIRST.resolve())
    assert isinstance(data["entries"], list)


def test_manifest_entries_are_sorted_and_have_three_string_fields(tmp_path) -> None:
    """AC-3: each entry has exactly three string fields; D-2 sorts by dtsx."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)
    data = json.loads((out / "_proc_manifest.json").read_text(encoding="utf-8"))
    entries = data["entries"]
    # Three real .dtsx files in main_first/ (main, childa, childb).
    assert len(entries) == 3
    # Sort key is dtsx, ascii case-sensitive.
    dtsx_values = [e["dtsx"] for e in entries]
    assert dtsx_values == sorted(dtsx_values), dtsx_values
    for entry in entries:
        assert set(entry.keys()) == {"dtsx", "proc", "out_sql"}, entry
        for k in ("dtsx", "proc", "out_sql"):
            assert isinstance(entry[k], str) and entry[k], (k, entry)


def test_manifest_paths_use_posix_separators(tmp_path) -> None:
    """AC-3 + D-13: every path uses ``/`` separators — never backslashes."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)
    data = json.loads((out / "_proc_manifest.json").read_text(encoding="utf-8"))
    for entry in data["entries"]:
        assert "\\" not in entry["dtsx"], entry
        assert "\\" not in entry["out_sql"], entry


def test_manifest_is_byte_identical_across_two_runs(tmp_path) -> None:
    """AC-2 determinism baseline — two runs over the same input produce
    byte-identical manifests."""
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    convert_tree(MAIN_FIRST, out1)
    convert_tree(MAIN_FIRST, out2)
    bytes1 = (out1 / "_proc_manifest.json").read_bytes()
    bytes2 = (out2 / "_proc_manifest.json").read_bytes()
    assert bytes1 == bytes2, (
        f"manifests should be byte-identical across runs:\n{bytes1!r}\n{bytes2!r}"
    )


def test_manifest_entries_use_relative_paths_under_input_root(tmp_path) -> None:
    """D-2: dtsx is a relpath from input_root; out_sql is a relpath from output_root."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)
    data = json.loads((out / "_proc_manifest.json").read_text(encoding="utf-8"))
    procs_in_order = [e["proc"] for e in data["entries"]]
    # main_first/ produces usp_childa, usp_childb, usp_main.
    assert set(procs_in_order) == {"usp_childa", "usp_childb", "usp_main"}, procs_in_order
    # Each dtsx and out_sql is a relative path (no leading slash, no drive).
    for entry in data["entries"]:
        assert not entry["dtsx"].startswith("/"), entry
        assert not entry["out_sql"].startswith("/"), entry
        assert ":" not in entry["dtsx"], entry  # no drive letter
        assert ":" not in entry["out_sql"], entry


def test_manifest_is_written_for_url_encoded_tree(tmp_path) -> None:
    """The url-encoded fixture is part of the determinism baseline; assert
    the manifest is also emitted there with valid entries."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST_URL_ENCODED, out)
    data = json.loads((out / "_proc_manifest.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["entries"], list)
    assert len(data["entries"]) >= 1
    for entry in data["entries"]:
        assert "\\" not in entry["dtsx"]
        assert "\\" not in entry["out_sql"]

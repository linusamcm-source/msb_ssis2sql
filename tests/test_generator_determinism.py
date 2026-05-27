"""AC-3 + AC-9: byte-identical reruns.

* AC-3: every emitted ``.sql`` AND the ``_batch_warnings.log`` (zero-byte or
  not) must be byte-identical across two consecutive convert-tree runs over
  the same input, for both the happy-path fixture and the cross-directory
  determinism_tree fixture.
* AC-9: ``main_first_cycle/`` produces a non-empty ``_batch_warnings.log``
  whose contents are byte-identical between consecutive runs.

Will fail until the generator drops its timestamp + warning-bullet header
section, and until batch.py writes ``_batch_warnings.log``.
"""
from __future__ import annotations

import filecmp
from pathlib import Path

import pytest

from msb_ssis2sql.batch import convert_tree

FIXTURES = Path(__file__).parent / "fixtures"
MAIN_FIRST = FIXTURES / "main_first"
DETERMINISM_TREE = FIXTURES / "determinism_tree"
CYCLE = FIXTURES / "main_first_cycle"


def _all_files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("fixture", [MAIN_FIRST, DETERMINISM_TREE], ids=["main_first", "determinism_tree"])
def test_convert_tree_is_byte_identical_across_reruns(tmp_path, fixture):
    """Two runs of convert-tree on the same fixture produce byte-identical
    output trees, including the zero/non-zero warnings log."""
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    convert_tree(fixture, out1)
    convert_tree(fixture, out2)

    files1 = _all_files(out1)
    files2 = _all_files(out2)

    assert set(files1.keys()) == set(files2.keys()), (
        f"emitted file set drifted:\nrun1={sorted(files1)}\nrun2={sorted(files2)}"
    )
    for rel, b1 in files1.items():
        assert b1 == files2[rel], (
            f"byte diff on {rel}:\n--- run1 ---\n{b1.decode('utf-8', 'replace')}\n"
            f"--- run2 ---\n{files2[rel].decode('utf-8', 'replace')}"
        )


@pytest.mark.parametrize("fixture", [MAIN_FIRST, DETERMINISM_TREE])
def test_convert_tree_emits_batch_warnings_log(tmp_path, fixture):
    """``_batch_warnings.log`` is always emitted - even when zero bytes."""
    out = tmp_path / "out"
    convert_tree(fixture, out)
    log = out / "_batch_warnings.log"
    assert log.exists(), f"_batch_warnings.log was not emitted for {fixture}"


def test_sql_does_not_contain_volatile_timestamp(tmp_path):
    """Generator header must NOT include 'Generated' timestamp line (R3 C-1)."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)
    for path in out.rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        assert " * Generated       :" not in text, (
            f"{path.name} still has the volatile generated-timestamp header line:\n{text[:300]}"
        )


def test_sql_header_has_no_warning_bullets(tmp_path):
    """Warning bullets must be stripped from the per-package header."""
    out = tmp_path / "out"
    convert_tree(MAIN_FIRST, out)
    for path in out.rglob("*.sql"):
        text = path.read_text(encoding="utf-8")
        # The 'Conversion warnings (N) - review before use' line lived in the
        # _header() block.  It must be gone.
        assert "Conversion warnings (" not in text, (
            f"{path.name} still emits warning bullets inside the header"
        )


def test_warnings_log_byte_identical_on_cycle(tmp_path):
    """AC-9: byte-identical _batch_warnings.log across two runs over a cycle fixture."""
    out1 = tmp_path / "r1"
    out2 = tmp_path / "r2"
    convert_tree(CYCLE, out1)
    convert_tree(CYCLE, out2)

    log1 = out1 / "_batch_warnings.log"
    log2 = out2 / "_batch_warnings.log"
    assert log1.exists() and log2.exists()
    assert log1.stat().st_size > 0, "cycle fixture must produce at least one warning"
    assert filecmp.cmp(log1, log2, shallow=False), (
        f"_batch_warnings.log differs across runs:\n"
        f"--- run1 ---\n{log1.read_text()}\n--- run2 ---\n{log2.read_text()}"
    )

    # No duplicate lines — each warning must appear exactly once.
    lines = log1.read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == sorted(set(lines)), (
        f"_batch_warnings.log contains duplicate lines: {[ln for ln in lines if lines.count(ln) > 1]}"
    )


def test_cycle_warning_uses_pinned_message_format(tmp_path):
    """Cycle audit line follows the pinned format in plan-final.md Decisions."""
    out = tmp_path / "out"
    convert_tree(CYCLE, out)
    log_text = (out / "_batch_warnings.log").read_text(encoding="utf-8")
    assert "main orchestrator: cycle detected on edge" in log_text, log_text
    assert "falling back to declaration order" in log_text, log_text


def test_warnings_log_sorted_stable(tmp_path):
    """Audit log lines must be sorted by source-file path then warning text."""
    out = tmp_path / "out"
    convert_tree(CYCLE, out)
    lines = (out / "_batch_warnings.log").read_text(encoding="utf-8").splitlines()
    assert lines == sorted(lines), f"log lines not in stable sort order:\n{lines}"

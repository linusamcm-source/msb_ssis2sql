"""End-to-end differential validation tests — Story 8.

This file lives at ``validation/test_validation.py`` (not under
``validation/tests/``) so it is collected by ``just validate``
(``pytest validation/ -m validation``) but NOT by ``just validate-unit``
(``pytest validation/tests``).

There are two parts:

PART A — differential validation tests (ACs 1 & 2)
    One test per ``(corpus-package, destination-table)`` pair, parametrised at
    collection time by reading every ``ledger.yaml`` in ``validation/corpus/``.
    Each test is marked ``@pytest.mark.validation`` (registered in
    ``pyproject.toml``).

    Per-test flow:
    1. ``fresh_db`` fixture — skips cleanly when the SQL Server is
       unreachable or ``MSSQL_*`` env vars are absent.
    2. ``provision`` + ``seed`` + ``truncate_destinations`` (Story 2).
    3. ``run(conn, pkg_dir)`` — transpile, execute, read back (Story 3).
    4. **Golden gate** — look for ``golden/<destination>.parquet``; if absent,
       ``pytest.skip`` with a message pointing at ``RUNBOOK.md``.
    5. **Integrity gate** — if ``golden/manifest.json`` is present, compare
       its ``seed_checksum`` against the live
       ``validation.provisioning.seed_checksum``; mismatch → FAIL with an
       explicit integrity message (not a data diff).
    6. ``compare(golden_df, actual_df, dest_ledger)`` (Story 4).
    7. Assert verdict is PASS or XFAIL; surface the readable diff on FAIL.

    Because no corpus golden fixtures have been captured yet (``golden/``
    contains only ``.gitkeep``), **every differential test skips** on first
    run.  That is AC2 — the run is green.

PART B — synthetic-golden self-test (ACs 3 & 4, server-free)
    Exercises the compare + integrity-gate plumbing directly using a
    ``tmp_path`` Parquet fixture:

    - Matching dataset → verdict PASS (AC3).
    - Injected row mismatch → verdict FAIL with a readable diff (AC3).
    - Manifest seed_checksum mismatch → integrity gate FAILs with an
      integrity message, not a data diff (AC4).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from validation.comparison import ComparisonResult, compare
from validation.ledger import ColumnPolicy, DestLedger, parse_ledger
from validation.provisioning import seed_checksum

if TYPE_CHECKING:
    import pyodbc

# Mark every test in this file as `validation` so `just validate`
# (`pytest validation/ -m validation`) collects both the parametrised
# differential tests (PART A) and the server-free synthetic self-tests (PART B).
pytestmark = pytest.mark.validation

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parent / "corpus"
_RUNBOOK_PATH: str = "validation/capture/RUNBOOK.md"

# ---------------------------------------------------------------------------
# Parametrisation — discover (package, destination) pairs at collection time
# ---------------------------------------------------------------------------


def _discover_pairs() -> list[tuple[str, str]]:
    """Return sorted list of (pkg_name, dst_name) for all corpus packages."""
    pairs: list[tuple[str, str]] = []
    if not _CORPUS_ROOT.is_dir():
        return pairs
    for pkg_dir in sorted(_CORPUS_ROOT.iterdir()):
        ledger_path = pkg_dir / "ledger.yaml"
        if not ledger_path.is_file():
            continue
        try:
            ledger = parse_ledger(ledger_path)
        except Exception:
            continue
        for dst_name in sorted(ledger):
            pairs.append((pkg_dir.name, dst_name))
    return pairs


_PAIRS: list[tuple[str, str]] = _discover_pairs()
_PAIR_IDS: list[str] = [f"{pkg}::{dst}" for pkg, dst in _PAIRS]


# ---------------------------------------------------------------------------
# PART A — differential validation (ACs 1 & 2)
# ---------------------------------------------------------------------------


def _check_seed_integrity(golden_dir: Path, pkg_dir: Path) -> str | None:
    """Check whether the golden manifest's seed_checksum matches the live corpus.

    Returns ``None`` when the checksums match or no manifest is present (gate
    passes).  Returns a human-readable failure message string when the
    checksums differ — the caller should pass it to ``pytest.fail``.
    """
    manifest_path = golden_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    golden_checksum: str = manifest.get("seed_checksum", "")
    live_checksum: str = seed_checksum(pkg_dir)
    if golden_checksum == live_checksum:
        return None
    return (
        f"Seed integrity gate FAILED — "
        f"the seed CSVs have changed since the golden fixture was captured.\n"
        f"  golden seed_checksum : {golden_checksum}\n"
        f"  live   seed_checksum : {live_checksum}\n"
        f"Re-run the capture harness ({_RUNBOOK_PATH}) to refresh the "
        f"golden fixture, or revert the seed CSV change."
    )


@pytest.mark.validation
@pytest.mark.parametrize("pkg_name,dst_name", _PAIRS, ids=_PAIR_IDS)
def test_validate(
    pkg_name: str,
    dst_name: str,
    fresh_db: "pyodbc.Connection",
) -> None:
    """Differential validation for one (package, destination) pair.

    Skips when the SQL Server is unreachable (via ``fresh_db``), when no
    golden ``.parquet`` fixture exists, or when the golden manifest
    ``seed_checksum`` does not match the live corpus seed (integrity gate).

    AC1: exactly one test collected per (package, destination) pair.
    AC2: with no golden fixtures, every test skips — the run is green.
    """
    from validation.provisioning import provision, seed, truncate_destinations
    from validation.sql_runner import run

    pkg_dir = _CORPUS_ROOT / pkg_name
    golden_dir = pkg_dir / "golden"
    parquet_path = golden_dir / f"{dst_name}.parquet"

    # --- Golden gate (checked FIRST — no DB work when fixture is absent) ---
    if not parquet_path.is_file():
        pytest.skip(
            f"[{pkg_name}::{dst_name}] No golden fixture at {parquet_path}. "
            f"Run the capture harness on Windows following {_RUNBOOK_PATH} to "
            f"generate golden/{dst_name}.parquet."
        )

    # --- Integrity gate ---
    integrity_msg = _check_seed_integrity(golden_dir, pkg_dir)
    if integrity_msg:
        pytest.fail(f"[{pkg_name}::{dst_name}] {integrity_msg}")

    # --- Provision, seed, run ---
    provision(fresh_db, pkg_dir)
    seed(fresh_db, pkg_dir)
    truncate_destinations(fresh_db, pkg_dir)
    run_result = run(fresh_db, pkg_dir)

    if run_result.error:
        pytest.fail(
            f"[{pkg_name}::{dst_name}] sql_runner.run() failed:\n{run_result.error}"
        )

    # --- Comparison ---
    golden_df = pd.read_parquet(parquet_path)
    actual_df = run_result.data.get(dst_name, pd.DataFrame())
    ledger = parse_ledger(pkg_dir / "ledger.yaml")
    dest_ledger = ledger[dst_name]

    result = compare(
        golden_df,
        actual_df,
        dest_ledger,
        package=pkg_name,
        destination=dst_name,
    )

    # Verdict PASS and XFAIL both represent expected outcomes.
    if result.verdict in ("PASS", "XFAIL"):
        return

    # FAIL or XPASS — surface the readable diff.
    diff_lines: list[str] = [
        f"[{pkg_name}::{dst_name}] Comparison verdict: {result.verdict}",
        f"  golden_rows={result.golden_rows}  actual_rows={result.actual_rows}",
    ]
    if result.schema_mismatch:
        diff_lines.append(f"  schema_mismatch: {result.schema_mismatch}")
    if result.missing_rows:
        diff_lines.append(f"  missing_rows ({len(result.missing_rows)}):")
        for row in result.missing_rows[:5]:
            diff_lines.append(f"    {row}")
    if result.extra_rows:
        diff_lines.append(f"  extra_rows ({len(result.extra_rows)}):")
        for row in result.extra_rows[:5]:
            diff_lines.append(f"    {row}")
    if result.cell_mismatches:
        diff_lines.append(f"  cell_mismatches ({len(result.cell_mismatches)}):")
        for cell in result.cell_mismatches[:5]:
            diff_lines.append(f"    {cell}")
    if result.applied_divergences:
        diff_lines.append(f"  applied_divergences: {result.applied_divergences}")

    pytest.fail("\n".join(diff_lines))


# ---------------------------------------------------------------------------
# PART B — synthetic-golden self-test (ACs 3 & 4, server-free)
# ---------------------------------------------------------------------------

# Shared ledger used across all synthetic tests — a simple exact-policy ledger
# with an 'id' key column and a 'value' exact column.
_SYNTH_DEST_LEDGER: DestLedger = DestLedger(
    comparison="multiset",
    order_key=[],
    columns={
        "id": ColumnPolicy(policy="exact"),
        "value": ColumnPolicy(policy="exact"),
    },
    known_divergences=[],
)

# Shared golden DataFrame used across synthetic tests.
_SYNTH_GOLDEN_DF: pd.DataFrame = pd.DataFrame(
    {"id": [1, 2, 3], "value": ["alpha", "beta", "gamma"]}
)


def _write_synth_golden(tmp_path: Path, df: pd.DataFrame, checksum: str) -> Path:
    """Write a synthetic golden Parquet + manifest.json; return the golden_dir."""
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    df.to_parquet(golden_dir / "dst_synth.parquet", index=False)
    manifest = {
        "package": "synth_pkg",
        "seed_checksum": checksum,
        "destinations": {"dst_synth": {"row_count": len(df), "column_types": {}}},
    }
    (golden_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return golden_dir


def test_synthetic_matching_dataset_passes() -> None:
    """compare() on identical golden/actual DataFrames returns verdict PASS.

    AC3 — verifies the PASS plumbing end-to-end with the shipped comparison
    engine.  SERVER-FREE.
    """
    result: ComparisonResult = compare(
        _SYNTH_GOLDEN_DF.copy(),
        _SYNTH_GOLDEN_DF.copy(),
        _SYNTH_DEST_LEDGER,
        package="synth_pkg",
        destination="dst_synth",
    )
    assert result.verdict == "PASS", (
        f"Expected PASS for identical datasets, got {result.verdict}.\n"
        f"  missing_rows={result.missing_rows}\n"
        f"  extra_rows={result.extra_rows}"
    )


def test_synthetic_row_mismatch_fails_with_readable_diff() -> None:
    """compare() on a mismatched dataset returns FAIL with a readable diff.

    AC3 — injects an extra row into the actual DataFrame; asserts verdict is
    FAIL and the result carries diff artefacts.  SERVER-FREE.
    """
    actual_with_extra = pd.concat(
        [_SYNTH_GOLDEN_DF.copy(), pd.DataFrame({"id": [99], "value": ["injected"]})],
        ignore_index=True,
    )
    result = compare(
        _SYNTH_GOLDEN_DF.copy(),
        actual_with_extra,
        _SYNTH_DEST_LEDGER,
        package="synth_pkg",
        destination="dst_synth",
    )
    assert result.verdict == "FAIL", (
        f"Expected FAIL for mismatched dataset, got {result.verdict}"
    )
    # The diff must name the injected row — not just "failed".
    assert result.extra_rows or result.missing_rows or result.cell_mismatches, (
        "FAIL verdict must carry at least one diff artefact "
        f"(extra_rows, missing_rows, or cell_mismatches). result: {result}"
    )


def test_synthetic_missing_row_fails_with_diff() -> None:
    """compare() on actual with a missing row returns FAIL with missing_rows populated.

    AC3 — complementary to the extra-row test.  SERVER-FREE.
    """
    actual_missing_row = _SYNTH_GOLDEN_DF.iloc[:2].copy()  # drop last row
    result = compare(
        _SYNTH_GOLDEN_DF.copy(),
        actual_missing_row,
        _SYNTH_DEST_LEDGER,
        package="synth_pkg",
        destination="dst_synth",
    )
    assert result.verdict == "FAIL", (
        f"Expected FAIL for actual missing a row, got {result.verdict}"
    )
    assert result.missing_rows, (
        "missing_rows must be populated when actual is missing a golden row"
    )


def test_synthetic_integrity_gate_mismatch_fails_not_data_diff(tmp_path: Path) -> None:
    """A mismatched seed_checksum causes _check_seed_integrity to return a message.

    AC4 — the integrity gate must fire on checksum mismatch.  The returned
    message must reference the integrity gate (contain "integrity") and must
    NOT contain data-diff wording ("extra_rows", "missing_rows").

    Writes a synthetic manifest with a bogus checksum; asserts
    ``_check_seed_integrity`` returns a non-None message with the right content.

    SERVER-FREE.
    """
    pkg_dir = _CORPUS_ROOT / "passthrough_basic"
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    _SYNTH_GOLDEN_DF.to_parquet(golden_dir / "dst_synth.parquet", index=False)

    bogus_checksum = "a" * 64  # SHA-256 length, obviously wrong value
    manifest = {
        "package": "synth_pkg",
        "seed_checksum": bogus_checksum,
        "destinations": {"dst_synth": {"row_count": 3, "column_types": {}}},
    }
    (golden_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    msg = _check_seed_integrity(golden_dir, pkg_dir)

    assert msg is not None, (
        "_check_seed_integrity must return a failure message when the stored "
        "checksum does not match the live corpus checksum"
    )
    assert "integrity" in msg.lower(), (
        f"Failure message must mention the integrity gate. Got: {msg!r}"
    )
    assert "extra_rows" not in msg and "missing_rows" not in msg, (
        f"Integrity failure must not contain data-diff wording. Got: {msg!r}"
    )


def test_synthetic_integrity_gate_match_allows_comparison(tmp_path: Path) -> None:
    """A matching seed_checksum causes _check_seed_integrity to return None.

    AC4 — when the checksum matches, the integrity gate passes (None returned)
    and the comparison can proceed.  SERVER-FREE.
    """
    pkg_dir = _CORPUS_ROOT / "passthrough_basic"
    live_checksum = seed_checksum(pkg_dir)

    golden_dir = _write_synth_golden(tmp_path, _SYNTH_GOLDEN_DF.copy(), live_checksum)

    msg = _check_seed_integrity(golden_dir, pkg_dir)

    assert msg is None, (
        f"_check_seed_integrity must return None when checksums match. Got: {msg!r}"
    )

    # With the gate passing, comparison on identical data must be PASS.
    result = compare(
        _SYNTH_GOLDEN_DF.copy(),
        _SYNTH_GOLDEN_DF.copy(),
        _SYNTH_DEST_LEDGER,
    )
    assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# Structural meta-test — verify test count at collection time
# ---------------------------------------------------------------------------


def test_pair_discovery_finds_expected_count() -> None:
    """Exactly 12 (package, destination) pairs are discovered from the 8-package corpus.

    Pinned count: conditional_split → 3 dsts, lookup_match → 2, union_multicast → 2,
    all others → 1 each.  If the corpus changes this test surfaces the drift.
    SERVER-FREE (reads ledger.yaml files only).
    """
    assert len(_PAIRS) == 12, (
        f"Expected 12 (pkg, dst) pairs from the corpus, got {len(_PAIRS)}:\n"
        + "\n".join(f"  {pkg}::{dst}" for pkg, dst in _PAIRS)
    )

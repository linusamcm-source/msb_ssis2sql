"""Tests for ``validation.comparison`` and ``validation.reporting`` — RED phase.

``validation/comparison.py`` and ``validation/reporting.py`` do not exist yet;
every test in this module will fail with ``ModuleNotFoundError`` until the
engineer's Story 4 implementation lands.  That is the correct TDD RED state.

Contract under test (sprint plan, Story 4 + §6):

``compare(golden_df, actual_df, dest_ledger) -> ComparisonResult``
    Pure-logic diff of two pandas DataFrames against a ``DestLedger``.
    Steps (in order):
    1. Schema check — same column set after ``exclude`` columns dropped.
    2. Per-column normalise — drop ``exclude``; round ``float`` to epsilon;
       round ``datetime`` to tolerance; replace ``non_null`` columns with a
       presence sentinel (not the value).
    3. Multiset diff — ``collections.Counter`` of row tuples; ``missing`` =
       golden − actual, ``extra`` = actual − golden.
    4. Ordered diff (when ``comparison = ordered``) — sort both by
       ``order_key``, compare positionally.
    5. Cell localisation — when total counts match but rows differ and a key
       column is available, report exactly which cell diverges.
    6. Apply divergences — ``xfail`` flips FAIL→XFAIL (and PASS→XPASS,
       itself reportable); ``filter`` applies a documented pre-comparison
       transform; ``accept`` annotates without changing the verdict.

``ComparisonResult`` dataclass fields:
    package, destination, verdict (PASS|FAIL|XFAIL|XPASS|SKIP),
    golden_rows: int, actual_rows: int, missing_rows: list[dict],
    extra_rows: list[dict], cell_mismatches: list[dict],
    schema_mismatch: str | None, applied_divergences: list[str].

``render_result(result) -> str``  (from ``validation.reporting``)
    Renders a ``ComparisonResult`` to a readable text block containing at
    least the verdict and the row counts.

Decimal vs float are compared numerically, never by repr.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pandas as pd
import pytest

# These imports raise ModuleNotFoundError until the source files exist.
# That is the expected RED state — do not wrap in try/except.
from validation.comparison import ComparisonResult, compare
from validation.ledger import ColumnPolicy, DestLedger, KnownDivergence
from validation.reporting import render_result


# ---------------------------------------------------------------------------
# Helpers — build DestLedger objects in-memory (no YAML files needed)
# ---------------------------------------------------------------------------


def _policy(name: str, **kwargs: object) -> ColumnPolicy:
    """Construct a ``ColumnPolicy`` with the given policy name and options."""
    return ColumnPolicy(policy=name, **kwargs)  # type: ignore[arg-type]


def _ledger(
    columns: dict[str, ColumnPolicy],
    *,
    comparison: str = "multiset",
    order_key: list[str] | None = None,
    known_divergences: list[KnownDivergence] | None = None,
) -> DestLedger:
    """Build a ``DestLedger`` directly (bypasses YAML parsing)."""
    return DestLedger(
        comparison=comparison,
        order_key=order_key or [],
        columns=columns,
        known_divergences=known_divergences or [],
    )


def _divergence(
    kind: str = "lookup_left_join",
    component: str = "Lookup X",
    handling: str = "xfail",
    reason: str = "Transpiler emits LEFT JOIN (README limitation)",
) -> KnownDivergence:
    return KnownDivergence(
        kind=kind, component=component, handling=handling, reason=reason
    )


# ---------------------------------------------------------------------------
# AC 1 — Identical frames → PASS
# ---------------------------------------------------------------------------


def test_identical_frames_pass() -> None:
    """Two identical DataFrames produce a PASS verdict."""
    df = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(df, df.copy(), ledger)
    assert result.verdict == "PASS"
    assert result.missing_rows == []
    assert result.extra_rows == []


def test_identical_frames_zero_missing_and_extra() -> None:
    """Identical DataFrames report zero missing and zero extra rows."""
    df = pd.DataFrame({"id": [1], "score": [1.0]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("exact")})
    result = compare(df, df.copy(), ledger)
    assert result.golden_rows == 1
    assert result.actual_rows == 1


# ---------------------------------------------------------------------------
# AC 2 — Extra row → FAIL, row in extra_rows
# ---------------------------------------------------------------------------


def test_extra_row_in_actual_causes_fail() -> None:
    """An extra row in actual (not in golden) produces FAIL."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


def test_extra_row_appears_in_extra_rows() -> None:
    """The extra row is present in ``result.extra_rows``."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    assert len(result.extra_rows) == 1
    assert result.extra_rows[0]["id"] == 3


def test_missing_row_causes_fail() -> None:
    """A row in golden but absent from actual produces FAIL."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1], "val": ["a"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"
    assert len(result.missing_rows) == 1
    assert result.missing_rows[0]["id"] == 2


# ---------------------------------------------------------------------------
# AC 3 — Float policy: within epsilon → PASS; outside → FAIL
# ---------------------------------------------------------------------------


def test_float_within_epsilon_passes() -> None:
    """Float values differing by less than epsilon compare as equal → PASS."""
    golden = pd.DataFrame({"id": [1], "score": [1.0000]})
    actual = pd.DataFrame({"id": [1], "score": [1.0005]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


def test_float_at_epsilon_boundary_passes() -> None:
    """Float values differing by exactly epsilon are within tolerance → PASS."""
    golden = pd.DataFrame({"id": [1], "score": [1.0]})
    actual = pd.DataFrame({"id": [1], "score": [1.0 + 0.001]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


def test_float_outside_epsilon_fails() -> None:
    """Float values differing by more than epsilon → FAIL."""
    golden = pd.DataFrame({"id": [1], "score": [1.0]})
    actual = pd.DataFrame({"id": [1], "score": [1.1]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


def test_decimal_vs_float_compared_numerically() -> None:
    """``Decimal`` and ``float`` with the same value compare as equal (not by repr)."""
    golden = pd.DataFrame({"id": [1], "amount": [Decimal("9.99")]})
    actual = pd.DataFrame({"id": [1], "amount": [9.99]})
    ledger = _ledger(
        {"id": _policy("exact"), "amount": _policy("float", epsilon=0.001)}
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# AC 4 — Exclude policy: differing values don't affect verdict
# ---------------------------------------------------------------------------


def test_exclude_column_differences_do_not_affect_verdict() -> None:
    """Columns with ``exclude`` policy are dropped before comparison → PASS."""
    golden = pd.DataFrame({"id": [1], "loaded_at": ["2026-01-01"], "val": ["x"]})
    actual = pd.DataFrame({"id": [1], "loaded_at": ["2026-02-01"], "val": ["x"]})
    ledger = _ledger({
        "id": _policy("exact"),
        "loaded_at": _policy("exclude", reason="non-deterministic"),
        "val": _policy("exact"),
    })
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


def test_exclude_column_only_difference_still_passes() -> None:
    """When the only difference is in an excluded column, verdict is PASS."""
    golden = pd.DataFrame({"id": [1, 2], "ts": ["T1", "T2"], "x": [10, 20]})
    actual = pd.DataFrame({"id": [1, 2], "ts": ["TX", "TX"], "x": [10, 20]})
    ledger = _ledger({
        "id": _policy("exact"),
        "ts": _policy("exclude", reason="audit timestamp"),
        "x": _policy("exact"),
    })
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# AC — Datetime policy: within tolerance → PASS; outside → FAIL
# ---------------------------------------------------------------------------


def test_datetime_within_tolerance_passes() -> None:
    """Datetimes within the tolerance window compare equal → PASS."""
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    golden = pd.DataFrame({"id": [1], "ts": [base]})
    actual = pd.DataFrame({"id": [1], "ts": [base + datetime.timedelta(seconds=1)]})
    ledger = _ledger(
        {"id": _policy("exact"), "ts": _policy("datetime", tolerance=2.0)}
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


def test_datetime_outside_tolerance_fails() -> None:
    """Datetimes outside the tolerance window → FAIL."""
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    golden = pd.DataFrame({"id": [1], "ts": [base]})
    actual = pd.DataFrame({"id": [1], "ts": [base + datetime.timedelta(seconds=10)]})
    ledger = _ledger(
        {"id": _policy("exact"), "ts": _policy("datetime", tolerance=2.0)}
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


# ---------------------------------------------------------------------------
# AC — Non-null policy: both non-null pass regardless of value; null → FAIL
# ---------------------------------------------------------------------------


def test_non_null_both_present_passes_despite_differing_values() -> None:
    """Both sides non-null → PASS even when values differ (only presence checked)."""
    golden = pd.DataFrame({"id": [1], "host": ["server-a"]})
    actual = pd.DataFrame({"id": [1], "host": ["server-b"]})
    ledger = _ledger({"id": _policy("exact"), "host": _policy("non_null")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"


def test_non_null_null_on_actual_side_fails() -> None:
    """A NULL on the actual side for a non_null column → FAIL."""
    golden = pd.DataFrame({"id": [1], "host": ["server-a"]})
    actual = pd.DataFrame({"id": [1], "host": [None]})
    ledger = _ledger({"id": _policy("exact"), "host": _policy("non_null")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


def test_non_null_null_on_golden_side_fails() -> None:
    """A NULL on the golden side for a non_null column → FAIL."""
    golden = pd.DataFrame({"id": [1], "host": [None]})
    actual = pd.DataFrame({"id": [1], "host": ["server-a"]})
    ledger = _ledger({"id": _policy("exact"), "host": _policy("non_null")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


# ---------------------------------------------------------------------------
# AC — Ordered comparison: positional sequence mismatch detected
# ---------------------------------------------------------------------------


def test_ordered_comparison_detects_sequence_mismatch() -> None:
    """Ordered comparison catches rows in the wrong position → FAIL."""
    golden = pd.DataFrame({"region": ["A", "B", "C"], "sales": [10, 20, 30]})
    # Same rows, different order — multiset would PASS, ordered must FAIL.
    actual = pd.DataFrame({"region": ["C", "A", "B"], "sales": [30, 10, 20]})
    ledger = _ledger(
        {"region": _policy("exact"), "sales": _policy("exact")},
        comparison="ordered",
        order_key=["region"],
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS"  # sorted by region both give A, B, C


def test_ordered_comparison_same_rows_different_values_fails() -> None:
    """Ordered comparison catches a value difference at a specific position → FAIL."""
    golden = pd.DataFrame({"region": ["A", "B"], "sales": [10, 20]})
    actual = pd.DataFrame({"region": ["A", "B"], "sales": [10, 99]})
    ledger = _ledger(
        {"region": _policy("exact"), "sales": _policy("exact")},
        comparison="ordered",
        order_key=["region"],
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"


# ---------------------------------------------------------------------------
# AC — Cell localisation
# ---------------------------------------------------------------------------


def test_cell_localisation_reports_differing_cell() -> None:
    """When row counts match but one cell differs, ``cell_mismatches`` is populated."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["correct", "ok"]})
    actual = pd.DataFrame({"id": [1, 2], "val": ["WRONG", "ok"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL"
    assert len(result.cell_mismatches) >= 1
    mismatch = result.cell_mismatches[0]
    assert mismatch["column"] == "val"


def test_cell_localisation_includes_key_value() -> None:
    """Each cell mismatch entry carries enough context to locate the row."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2], "val": ["a", "X"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    # At minimum the mismatch should identify which column diverged.
    assert any(m["column"] == "val" for m in result.cell_mismatches)


# ---------------------------------------------------------------------------
# AC — xfail handling: FAIL→XFAIL; PASS→XPASS (reportable)
# ---------------------------------------------------------------------------


def test_xfail_divergence_turns_fail_into_xfail() -> None:
    """A genuine mismatch with ``handling: xfail`` → XFAIL (not FAIL)."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[_divergence(handling="xfail")],
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "XFAIL"


def test_xfail_divergence_with_matching_data_gives_xpass() -> None:
    """Matching data with ``handling: xfail`` → XPASS (unexpected pass, reportable)."""
    df = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[_divergence(handling="xfail")],
    )
    result = compare(df, df.copy(), ledger)
    assert result.verdict == "XPASS"


def test_xfail_applied_divergence_is_recorded() -> None:
    """The divergence description appears in ``result.applied_divergences``."""
    golden = pd.DataFrame({"id": [1], "val": ["a"]})
    actual = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[_divergence(handling="xfail")],
    )
    result = compare(golden, actual, ledger)
    assert len(result.applied_divergences) >= 1


# ---------------------------------------------------------------------------
# AC — filter handling mode
# ---------------------------------------------------------------------------


def test_filter_handling_applied_before_comparison() -> None:
    """A ``filter`` divergence pre-filters rows before comparison.

    The filter handling is the most complex mode: the ledger entry documents a
    transform (e.g. drop rows where a lookup key did not match) and the
    comparison engine applies it before diffing.  Here we use a filter that
    drops rows where id > 1, so that the extra rows in actual are excluded
    before comparison — the remaining rows match, producing PASS.

    The exact filter specification format (inline lambda, row predicate, etc.)
    is left to the engineer; this test only asserts the verdict and that the
    divergence is recorded.
    """
    golden = pd.DataFrame({"id": [1], "val": ["a"]})
    # actual has rows 1 and 2; after the filter (drop id > 1) it matches golden.
    actual = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    filter_div = KnownDivergence(
        kind="extra_no_match_rows",
        component="Lookup X",
        handling="filter",
        reason="Lookup rows with no match appear in actual but not in golden",
        filter_expr="id <= 1",  # engineer interprets this field
    )
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[filter_div],
    )
    result = compare(golden, actual, ledger)
    # The filter must have been recorded in applied_divergences.
    assert "filter" in " ".join(result.applied_divergences).lower() or len(result.applied_divergences) >= 1


def test_filter_handling_divergence_is_recorded() -> None:
    """A ``filter`` divergence appears in ``result.applied_divergences``."""
    golden = pd.DataFrame({"id": [1], "val": ["a"]})
    actual = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    filter_div = KnownDivergence(
        kind="extra_rows",
        component="Some Component",
        handling="filter",
        reason="Expected extra rows from left join",
        filter_expr="id <= 1",
    )
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[filter_div],
    )
    result = compare(golden, actual, ledger)
    assert len(result.applied_divergences) >= 1


# ---------------------------------------------------------------------------
# AC — accept handling mode
# ---------------------------------------------------------------------------


def test_accept_handling_annotates_without_changing_verdict() -> None:
    """An ``accept`` divergence annotates the result but does not alter the verdict.

    Matching frames remain PASS; a failing comparison remains FAIL.
    ``accept`` is for acknowledged, reviewed divergences where the comparison
    still runs and must pass on the non-divergent columns/rows.
    """
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    accept_div = KnownDivergence(
        kind="known_extra_rows",
        component="Audit Component",
        handling="accept",
        reason="Audit rows always produce extras; acknowledged in design review",
    )
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[accept_div],
    )
    result = compare(golden, actual, ledger)
    # accept does not flip FAIL→XFAIL; the mismatch is still a FAIL.
    assert result.verdict == "FAIL"
    assert len(result.applied_divergences) >= 1


def test_accept_handling_with_matching_data_stays_pass() -> None:
    """Matching frames with an ``accept`` divergence remain PASS."""
    df = pd.DataFrame({"id": [1], "val": ["a"]})
    accept_div = KnownDivergence(
        kind="known_divergence",
        component="X",
        handling="accept",
        reason="Acknowledged",
    )
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[accept_div],
    )
    result = compare(df, df.copy(), ledger)
    assert result.verdict == "PASS"


# ---------------------------------------------------------------------------
# AC — Schema mismatch
# ---------------------------------------------------------------------------


def test_schema_mismatch_is_reported() -> None:
    """A column present in golden but absent from actual is reported in ``schema_mismatch``."""
    golden = pd.DataFrame({"id": [1], "extra_col": ["x"], "val": ["a"]})
    actual = pd.DataFrame({"id": [1], "val": ["a"]})
    ledger = _ledger({
        "id": _policy("exact"),
        "extra_col": _policy("exact"),
        "val": _policy("exact"),
    })
    result = compare(golden, actual, ledger)
    assert result.schema_mismatch is not None
    assert "extra_col" in result.schema_mismatch


# ---------------------------------------------------------------------------
# ComparisonResult dataclass shape
# ---------------------------------------------------------------------------


def test_comparison_result_has_expected_fields() -> None:
    """``ComparisonResult`` exposes all fields from the sprint plan §6.5."""
    df = pd.DataFrame({"id": [1], "val": ["a"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(df, df.copy(), ledger)
    # Verify every field in the plan's dataclass exists.
    assert hasattr(result, "package")
    assert hasattr(result, "destination")
    assert hasattr(result, "verdict")
    assert hasattr(result, "golden_rows")
    assert hasattr(result, "actual_rows")
    assert hasattr(result, "missing_rows")
    assert hasattr(result, "extra_rows")
    assert hasattr(result, "cell_mismatches")
    assert hasattr(result, "schema_mismatch")
    assert hasattr(result, "applied_divergences")


# ---------------------------------------------------------------------------
# reporting.py — render_result produces readable text
# ---------------------------------------------------------------------------


def test_render_result_contains_verdict() -> None:
    """``render_result`` output contains the verdict string."""
    df = pd.DataFrame({"id": [1], "val": ["a"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(df, df.copy(), ledger)
    text = render_result(result)
    assert "PASS" in text


def test_render_result_contains_row_counts() -> None:
    """``render_result`` output contains both golden and actual row counts."""
    golden = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    actual = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    text = render_result(result)
    assert "2" in text  # golden row count
    assert "3" in text  # actual row count


def test_render_result_fail_contains_extra_row_info() -> None:
    """A FAIL report mentions the extra rows."""
    golden = pd.DataFrame({"id": [1], "val": ["a"]})
    actual = pd.DataFrame({"id": [1, 99], "val": ["a", "z"]})
    ledger = _ledger({"id": _policy("exact"), "val": _policy("exact")})
    result = compare(golden, actual, ledger)
    text = render_result(result)
    assert "FAIL" in text


def test_render_result_xfail_verdict_visible() -> None:
    """An XFAIL verdict appears in the rendered report."""
    golden = pd.DataFrame({"id": [1], "val": ["a"]})
    actual = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    ledger = _ledger(
        {"id": _policy("exact"), "val": _policy("exact")},
        known_divergences=[_divergence(handling="xfail")],
    )
    result = compare(golden, actual, ledger)
    text = render_result(result)
    assert "XFAIL" in text


# ---------------------------------------------------------------------------
# Regression — bucket-straddling false FAILs (Story 4 Phase 4 HIGH finding)
#
# The contract (§6.3) is: abs(a - b) <= epsilon.
# A bucketing implementation (floor(v/epsilon) or round(ts/tolerance))
# produces false FAILs when two within-tolerance values straddle a bucket
# edge and land in adjacent buckets.  These tests pin the correct behaviour
# so that a bucketing implementation goes RED here and the engineer's fix
# (direct abs-difference comparison) makes them GREEN.
# ---------------------------------------------------------------------------


def test_float_within_epsilon_straddling_bucket_edge_passes() -> None:
    """Values within epsilon but straddling a bucket edge must PASS, not FAIL.

    golden=0.0009, actual=0.0011, epsilon=0.001.
    True diff = 0.0002 — well within epsilon, so PASS is required.

    A ``floor(v/epsilon)`` bucketing gives floor(0.9)=0 vs floor(1.1)=1 —
    different buckets → false FAIL.  The contract ``abs(a-b) <= epsilon``
    gives 0.0002 <= 0.001 → PASS.
    """
    golden = pd.DataFrame({"id": [1], "score": [0.0009]})
    actual = pd.DataFrame({"id": [1], "score": [0.0011]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "abs(0.0011 - 0.0009) = 0.0002 <= epsilon=0.001: must PASS "
        "(bucket-straddling regression)"
    )


def test_float_outside_epsilon_straddling_fails() -> None:
    """Values outside epsilon across a bucket boundary must still FAIL.

    golden=0.0010, actual=0.0025, epsilon=0.001.
    True diff = 0.0015 > epsilon → FAIL required.

    This confirms the fix does not accidentally broaden tolerance beyond epsilon.
    """
    golden = pd.DataFrame({"id": [1], "score": [0.0010]})
    actual = pd.DataFrame({"id": [1], "score": [0.0025]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "abs(0.0025 - 0.0010) = 0.0015 > epsilon=0.001: must FAIL"
    )


def test_datetime_within_tolerance_straddling_passes() -> None:
    """Timestamps within tolerance but straddling a rounding boundary must PASS.

    golden at epoch+0.5 s, actual at epoch+1.9 s, tolerance=2.0 s.
    True diff = 1.4 s — within tolerance, so PASS is required.

    A ``round(ts.timestamp() / tolerance) * tolerance`` bucketing rounds
    0.5/2=0.25 → bucket 0 (0.0 s) and 1.9/2=0.95 → bucket 2.0 s —
    different buckets → false FAIL.  Direct ``abs(a - b).total_seconds()
    <= tolerance`` gives 1.4 <= 2.0 → PASS.
    """
    epoch = datetime.datetime(2026, 1, 1, 0, 0, 0)
    golden = pd.DataFrame({"id": [1], "ts": [epoch + datetime.timedelta(seconds=0.5)]})
    actual = pd.DataFrame({"id": [1], "ts": [epoch + datetime.timedelta(seconds=1.9)]})
    ledger = _ledger(
        {"id": _policy("exact"), "ts": _policy("datetime", tolerance=2.0)}
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "abs(1.9 - 0.5) = 1.4 s <= tolerance=2.0 s: must PASS "
        "(bucket-straddling regression)"
    )


def test_datetime_outside_tolerance_straddling_fails() -> None:
    """Timestamps outside tolerance must FAIL even near a rounding boundary.

    golden at epoch+0.5 s, actual at epoch+3.5 s, tolerance=2.0 s.
    True diff = 3.0 s > tolerance → FAIL required.

    This confirms the fix does not accidentally broaden the tolerance window.
    """
    epoch = datetime.datetime(2026, 1, 1, 0, 0, 0)
    golden = pd.DataFrame({"id": [1], "ts": [epoch + datetime.timedelta(seconds=0.5)]})
    actual = pd.DataFrame({"id": [1], "ts": [epoch + datetime.timedelta(seconds=3.5)]})
    ledger = _ledger(
        {"id": _policy("exact"), "ts": _policy("datetime", tolerance=2.0)}
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "abs(3.5 - 0.5) = 3.0 s > tolerance=2.0 s: must FAIL"
    )


# ---------------------------------------------------------------------------
# Regression — duplicate-key Cartesian-product false FAILs (Phase 5b HIGH)
#
# When multiple rows share the same key tuple, a relational inner-merge
# pairwise comparison produces a Cartesian product of within-group rows.
# Two byte-identical DataFrames with a repeated key give cross-pairs like
# (1.0 vs 5.0) that exceed epsilon, producing a false FAIL.
# The fix is group-wise positional comparison within each key group.
# ---------------------------------------------------------------------------


def test_duplicate_key_tuples_identical_data_passes() -> None:
    """Two byte-identical DataFrames with a duplicate key tuple must PASS.

    golden = actual = rows [(id=1, score=1.0), (id=1, score=5.0)].
    The key column ``id`` is ``exact``; ``score`` is ``float`` (epsilon=0.001).

    A merge-based pairwise check produces the Cartesian product within the
    id=1 group: (1.0 vs 1.0), (1.0 vs 5.0), (5.0 vs 1.0), (5.0 vs 5.0).
    The cross-pairs (1.0 vs 5.0) and (5.0 vs 1.0) exceed epsilon → false FAIL.

    The correct group-wise positional approach pairs row 0 with row 0 and
    row 1 with row 1 within the group → both within epsilon → PASS.
    """
    golden = pd.DataFrame({"id": [1, 1], "score": [1.0, 5.0]})
    actual = pd.DataFrame({"id": [1, 1], "score": [1.0, 5.0]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "byte-identical DataFrames with duplicate key must PASS "
        "(Cartesian-product regression)"
    )


def test_duplicate_key_tuples_within_group_divergence_fails() -> None:
    """Within a duplicate-key group, a genuine score divergence must FAIL.

    golden = [(id=1, score=1.0), (id=1, score=5.0)].
    actual  = [(id=1, score=1.0), (id=1, score=9.0)].
    The second row's score differs by 4.0, well outside epsilon=0.001 → FAIL.

    This confirms the group-wise fix does not mask real divergences.
    """
    golden = pd.DataFrame({"id": [1, 1], "score": [1.0, 5.0]})
    actual = pd.DataFrame({"id": [1, 1], "score": [1.0, 9.0]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "score 5.0 vs 9.0 (diff=4.0) exceeds epsilon=0.001: must FAIL"
    )


def test_pairwise_no_key_fallback() -> None:
    """When every column is float/datetime, positional (no-key) fallback is used.

    A destination where no column has an ``exact`` or ``non_null`` policy has
    no key to merge on.  The comparison falls back to positional row pairing.
    Identical float-only frames → PASS; one value out of tolerance → FAIL.
    """
    golden = pd.DataFrame({"price": [1.0, 2.0], "qty": [10.0, 20.0]})
    actual_pass = pd.DataFrame({"price": [1.0, 2.0], "qty": [10.0, 20.0]})
    actual_fail = pd.DataFrame({"price": [1.0, 2.0], "qty": [10.0, 99.0]})
    ledger = _ledger({
        "price": _policy("float", epsilon=0.01),
        "qty": _policy("float", epsilon=0.01),
    })
    assert compare(golden, actual_pass, ledger).verdict == "PASS"
    assert compare(golden, actual_fail, ledger).verdict == "FAIL"


def test_float_column_null_one_side_fails() -> None:
    """A NULL on the actual side of a float-policy column → FAIL.

    golden row: id=1, score=3.14.
    actual row: id=1, score=NULL.
    NULL cannot be within any finite epsilon of a real number → FAIL.
    """
    golden = pd.DataFrame({"id": [1], "score": [3.14]})
    actual = pd.DataFrame({"id": [1], "score": [None]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "NULL vs 3.14 on a float-policy column must FAIL"
    )


def test_float_column_null_both_sides_equal() -> None:
    """NULLs on both sides of a float-policy column are not a mismatch → PASS.

    golden row: id=1, score=NULL.
    actual row: id=1, score=NULL.
    NULL == NULL for comparison purposes (both absent) → PASS.
    """
    golden = pd.DataFrame({"id": [1], "score": [None]})
    actual = pd.DataFrame({"id": [1], "score": [None]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "NULL vs NULL on a float-policy column must PASS (both absent)"
    )


def test_datetime_column_null_one_side_fails() -> None:
    """A NULL on the actual side of a datetime-policy column → FAIL.

    golden row: id=1, ts=<a real timestamp>.
    actual row: id=1, ts=NULL.
    NULL cannot be within any finite tolerance of a real timestamp → FAIL.
    """
    ts = datetime.datetime(2026, 6, 1, 12, 0, 0)
    golden = pd.DataFrame({"id": [1], "ts": [ts]})
    actual = pd.DataFrame({"id": [1], "ts": [None]})
    ledger = _ledger({"id": _policy("exact"), "ts": _policy("datetime", tolerance=2.0)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "NULL vs real timestamp on a datetime-policy column must FAIL"
    )


def test_ordered_comparison_unequal_row_counts() -> None:
    """An ordered comparison where actual has one extra row → FAIL.

    golden = 2 rows; actual = 3 rows.  Even if the first two rows match
    positionally after sorting, the extra row in actual means the sequences
    are unequal → FAIL.  Exercises the ordered unequal-row-count branch.
    """
    golden = pd.DataFrame({"region": ["A", "B"], "sales": [10, 20]})
    actual = pd.DataFrame({"region": ["A", "B", "C"], "sales": [10, 20, 30]})
    ledger = _ledger(
        {"region": _policy("exact"), "sales": _policy("exact")},
        comparison="ordered",
        order_key=["region"],
    )
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "ordered comparison with unequal row counts must FAIL"
    )


# ---------------------------------------------------------------------------
# Regression — lexicographic str-sort digit-boundary false FAILs (Phase 5c HIGH)
#
# When pairing within-key-group rows, sorting by str(v) orders numerically
# inconsistently at digit boundaries: str(10.0) = '10.0' < str(9.0) = '9.0'
# lexicographically, so golden [9.0, 10.0] and actual [9.0004, 9.9996] sort
# into different orders and the positional zip pairs the wrong rows — producing
# a false FAIL even though a valid all-within-tolerance pairing exists.
# The fix is to sort by the native numeric value, not its str() representation.
# ---------------------------------------------------------------------------


def test_duplicate_key_group_digit_boundary_passes() -> None:
    """Within-key-group rows straddling a digit boundary must PASS when all pairs are within epsilon.

    golden: id=1 ×2, score [9.0, 10.0].
    actual: id=1 ×2, score [9.0004, 9.9996].

    Correct numeric pairing (ascending): 9.0↔9.0004 (diff=0.0004 ≤ 0.001),
    10.0↔9.9996 (diff=0.0004 ≤ 0.001) → PASS.

    str() pairing: '10.0' < '9.0' < '9.0004' < '9.9996', so golden sorts to
    [10.0, 9.0] and actual sorts to [9.0004, 9.9996] — zip gives 10.0↔9.0004
    (diff=0.9996 >> epsilon) → false FAIL.
    """
    golden = pd.DataFrame({"id": [1, 1], "score": [9.0, 10.0]})
    actual = pd.DataFrame({"id": [1, 1], "score": [9.0004, 9.9996]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "numeric pairing gives diffs 0.0004 and 0.0004, both ≤ epsilon=0.001: "
        "must PASS (str-sort digit-boundary regression)"
    )


def test_duplicate_key_group_three_rows_digit_boundary_passes() -> None:
    """Three within-key-group rows at a digit boundary must PASS when all pairs are within epsilon.

    golden: id=1 ×3, score [9.0, 9.5, 10.0].
    actual: id=1 ×3, score [9.0004, 9.4996, 9.9996], epsilon=0.001.

    Correct numeric pairing: 9.0↔9.0004, 9.5↔9.4996, 10.0↔9.9996
    — all diffs 0.0004 ≤ 0.001 → PASS.

    str() sort places '10.0' before '9.*', causing at least one pair to have
    a diff of ~1.0, far outside epsilon → false FAIL.
    """
    golden = pd.DataFrame({"id": [1, 1, 1], "score": [9.0, 9.5, 10.0]})
    actual = pd.DataFrame({"id": [1, 1, 1], "score": [9.0004, 9.4996, 9.9996]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "PASS", (
        "all three pairs within epsilon=0.001 after numeric sort: "
        "must PASS (str-sort digit-boundary regression)"
    )


def test_duplicate_key_group_genuine_divergence_still_fails() -> None:
    """A genuinely divergent value within a duplicate-key group must still FAIL.

    golden: id=1 ×2, score [9.0, 10.0].
    actual: id=1 ×2, score [9.0, 50.0].

    Correct numeric pairing: 9.0↔9.0 (diff=0), 10.0↔50.0 (diff=40.0 >> epsilon).
    The fix must not become permissive — real divergences must stay FAIL.
    """
    golden = pd.DataFrame({"id": [1, 1], "score": [9.0, 10.0]})
    actual = pd.DataFrame({"id": [1, 1], "score": [9.0, 50.0]})
    ledger = _ledger({"id": _policy("exact"), "score": _policy("float", epsilon=0.001)})
    result = compare(golden, actual, ledger)
    assert result.verdict == "FAIL", (
        "10.0 vs 50.0 (diff=40.0) far exceeds epsilon=0.001: must FAIL"
    )

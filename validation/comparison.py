"""Comparison engine — pure-logic diff of two pandas DataFrames against a DestLedger.

Public API
----------
ComparisonResult
    Dataclass holding all comparison artefacts for one destination table.
compare(golden_df, actual_df, dest_ledger) -> ComparisonResult
    Produces a ComparisonResult following the six-step pipeline described in
    the sprint plan §6.4:
    1. Schema check.
    2. Partition columns by policy into "key" (exact / non_null) and
       "tolerance" (float / datetime) groups; drop "exclude" columns.
    3. Normalise key columns (non_null → sentinel) and build Counter tuples
       over the key-column values only.
    4. Multiset diff (Counter) or ordered diff on key columns.
    5. Pairwise tolerance check — for each row matched on key columns, compare
       float and datetime cells directly: abs(g-a) <= epsilon / tolerance.
       Mismatched tolerance cells are reported as cell_mismatches → FAIL.
    6. Cell localisation (positional for ordered; key-join for multiset).
    7. Divergence application (xfail / filter / accept).

The key insight: tolerance comparison is inherently pairwise. Bucketing each
value independently into floor(v/epsilon) intervals causes false FAILs when
two within-tolerance values straddle a bucket boundary.  The contract
(§6.3) is abs(a-b) <= tolerance — satisfied only by comparing matched pairs.
"""
from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import pandas as pd

from validation.ledger import ColumnPolicy, DestLedger, KnownDivergence

# Sentinel value used in place of actual non-null column values.
_NON_NULL_SENTINEL = "__NON_NULL__"

# Policies whose comparison is pairwise (cannot be canonicalised per-value).
_PAIRWISE_POLICIES: frozenset[str] = frozenset({"float", "datetime"})


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ComparisonResult:
    """All artefacts produced by a single comparison run.

    Attributes
    ----------
    package:
        SSIS package name (empty string when called without package context).
    destination:
        Destination table name (empty string when called without context).
    verdict:
        One of PASS, FAIL, XFAIL, XPASS, SKIP.
    golden_rows:
        Row count of the golden DataFrame (after filter divergences applied).
    actual_rows:
        Row count of the actual DataFrame (after filter divergences applied).
    missing_rows:
        Rows present in golden but absent from actual, each as a dict.
    extra_rows:
        Rows present in actual but absent from golden, each as a dict.
    cell_mismatches:
        Per-cell diff entries.  Each entry is a dict with at least ``column``.
    schema_mismatch:
        Non-None string when the column sets do not match (after exclude drop).
    applied_divergences:
        Human-readable description of each divergence that was applied.
    """

    package: str = ""
    destination: str = ""
    verdict: Literal["PASS", "FAIL", "XFAIL", "XPASS", "SKIP"] = "PASS"
    golden_rows: int = 0
    actual_rows: int = 0
    missing_rows: list[dict[str, Any]] = field(default_factory=list)
    extra_rows: list[dict[str, Any]] = field(default_factory=list)
    cell_mismatches: list[dict[str, Any]] = field(default_factory=list)
    schema_mismatch: str | None = None
    applied_divergences: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_na(value: Any) -> bool:
    """Return True if *value* is None or a pandas/float NA sentinel."""
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _to_float(value: Any) -> float:
    """Convert Decimal or any numeric value to float."""
    return float(value)


def _normalise_key_column(series: pd.Series, policy: str) -> pd.Series:
    """Normalise a key-column (exact or non_null) for Counter inclusion.

    - ``exact``: unchanged.
    - ``non_null``: non-null values become the sentinel string; nulls stay None.
    """
    if policy == "non_null":
        return series.map(
            lambda v: None if _is_na(v) else _NON_NULL_SENTINEL
        )
    # exact — identity
    return series


def _df_to_counter(df: pd.DataFrame) -> Counter[tuple[Any, ...]]:
    """Build a Counter of row tuples from *df*."""
    rows: list[tuple[Any, ...]] = [tuple(row) for row in df.itertuples(index=False)]
    return Counter(rows)


def _counter_diff_to_dicts(
    counter: Counter[tuple[Any, ...]],
    columns: list[str],
) -> list[dict[str, Any]]:
    """Expand a Counter of row tuples back to a list of dicts."""
    result: list[dict[str, Any]] = []
    for row_tuple, count in counter.items():
        for _ in range(count):
            result.append(dict(zip(columns, row_tuple)))
    return result


def _float_within(g_val: Any, a_val: Any, epsilon: float) -> bool:
    """Return True iff abs(float(g_val) - float(a_val)) <= epsilon."""
    if _is_na(g_val) and _is_na(a_val):
        return True
    if _is_na(g_val) or _is_na(a_val):
        return False
    return abs(_to_float(g_val) - _to_float(a_val)) <= epsilon


def _datetime_within(g_val: Any, a_val: Any, tolerance: float) -> bool:
    """Return True iff abs difference in seconds <= tolerance."""
    if _is_na(g_val) and _is_na(a_val):
        return True
    if _is_na(g_val) or _is_na(a_val):
        return False
    if isinstance(g_val, datetime.datetime) and isinstance(a_val, datetime.datetime):
        return abs((g_val - a_val).total_seconds()) <= tolerance
    # Fallback: numeric seconds
    return abs(float(g_val) - float(a_val)) <= tolerance


def _positional_cell_mismatches(
    golden: pd.DataFrame,
    actual: pd.DataFrame,
    col_policies: dict[str, ColumnPolicy],
) -> list[dict[str, Any]]:
    """Compare two same-shape DataFrames positionally, respecting policies.

    Both DataFrames must have the same columns and the same number of rows
    (with reset_index already applied).  Float/datetime columns are compared
    with their declared tolerance; all other columns use equality.
    """
    mismatches: list[dict[str, Any]] = []
    for col in golden.columns:
        pol = col_policies.get(col)
        g_col = golden[col].reset_index(drop=True)
        a_col = actual[col].reset_index(drop=True)
        for idx in range(len(g_col)):
            g_val = g_col.iloc[idx]
            a_val = a_col.iloc[idx]
            within: bool
            if pol is not None and pol.policy == "float":
                within = _float_within(g_val, a_val, pol.epsilon)
            elif pol is not None and pol.policy == "datetime":
                within = _datetime_within(g_val, a_val, pol.tolerance)
            else:
                g_na = _is_na(g_val)
                a_na = _is_na(a_val)
                within = (g_na and a_na) or (not g_na and not a_na and g_val == a_val)
            if not within:
                mismatches.append({
                    "column": col,
                    "row_index": idx,
                    "golden": g_val,
                    "actual": a_val,
                })
    return mismatches


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compare(
    golden_df: pd.DataFrame,
    actual_df: pd.DataFrame,
    dest_ledger: DestLedger,
    *,
    package: str = "",
    destination: str = "",
) -> ComparisonResult:
    """Compare *golden_df* against *actual_df* using *dest_ledger* policy.

    Returns a ``ComparisonResult`` with a verdict of PASS, FAIL, XFAIL, or
    XPASS.  Never raises; all diff artefacts are captured in the result.

    The pipeline separates columns into two groups:

    - **Key columns** (exact / non_null): canonicalised and used in the
      Counter / ordered diff to detect missing and extra rows.
    - **Tolerance columns** (float / datetime): not bucketed; compared
      pairwise (abs difference) after rows are matched on the key columns.

    This is the only correct implementation of ``abs(a-b) <= tolerance`` for
    multiset/ordered comparisons — bucketing individual values cannot satisfy
    the contract when two within-tolerance values straddle a bucket boundary.

    Parameters
    ----------
    golden_df:
        The reference (golden) DataFrame.
    actual_df:
        The DataFrame produced by the SSIS package under test.
    dest_ledger:
        Ledger for this destination table, including column policies and known
        divergences.
    package:
        Optional SSIS package name for the result label.
    destination:
        Optional destination table name for the result label.
    """
    result = ComparisonResult(package=package, destination=destination)

    # ---- Step 1: Apply filter divergences to both frames before anything else.
    filter_divs = [d for d in dest_ledger.known_divergences if d.handling == "filter"]
    for div in filter_divs:
        if div.filter_expr:
            try:
                golden_df = golden_df.query(div.filter_expr)
                actual_df = actual_df.query(div.filter_expr)
                result.applied_divergences.append(
                    f"filter({div.kind}): {div.reason}"
                )
            except Exception as exc:
                from msb_ssis2sql.observability import logger
                logger.warning(
                    "filter divergence {kind!r} NOT APPLIED — invalid filter_expr "
                    "{expr!r}: {exc}",
                    kind=div.kind,
                    expr=div.filter_expr,
                    exc=exc,
                )
                result.applied_divergences.append(
                    f"filter({div.kind}) NOT APPLIED (invalid expr): {div.reason}"
                )
        else:
            result.applied_divergences.append(
                f"filter({div.kind}): {div.reason}"
            )

    # ---- Step 2: Schema check (after exclude columns removed from consideration).
    exclude_cols: set[str] = {
        col for col, pol in dest_ledger.columns.items() if pol.policy == "exclude"
    }
    golden_cols: set[str] = set(golden_df.columns) - exclude_cols
    actual_cols: set[str] = set(actual_df.columns) - exclude_cols

    all_schema_missing = (golden_cols - actual_cols) | (actual_cols - golden_cols)
    if all_schema_missing:
        result.schema_mismatch = (
            f"column set mismatch — golden has {sorted(golden_cols)}, "
            f"actual has {sorted(actual_cols)}"
        )
        result.verdict = "FAIL"
        result.golden_rows = len(golden_df)
        result.actual_rows = len(actual_df)
        _apply_xfail_accept(result, dest_ledger.known_divergences)
        return result

    # ---- Step 3: Partition columns into key (Counter) vs tolerance (pairwise).
    work_cols: list[str] = sorted(golden_cols & actual_cols)

    key_cols: list[str] = []      # exact / non_null — drive the multiset diff
    tol_cols: list[str] = []      # float / datetime — pairwise after matching

    for col in work_cols:
        pol = dest_ledger.columns.get(col)
        if pol is None or pol.policy == "exact":
            key_cols.append(col)
        elif pol.policy == "non_null":
            key_cols.append(col)
        elif pol.policy in _PAIRWISE_POLICIES:
            tol_cols.append(col)
        # exclude already dropped above

    # Normalise key columns for the key-tuple comparison.
    golden_key = golden_df[key_cols].copy() if key_cols else golden_df[[]]
    actual_key = actual_df[key_cols].copy() if key_cols else actual_df[[]]

    for col in key_cols:
        pol = dest_ledger.columns.get(col)
        policy_name = pol.policy if pol else "exact"
        golden_key[col] = _normalise_key_column(golden_key[col], policy_name)
        actual_key[col] = _normalise_key_column(actual_key[col], policy_name)

    result.golden_rows = len(golden_df)
    result.actual_rows = len(actual_df)

    # ---- Step 4: Diff on key columns (multiset or ordered).
    if dest_ledger.comparison == "ordered":
        _compare_ordered(
            result, golden_df, actual_df, golden_key, actual_key,
            dest_ledger.order_key, tol_cols, dest_ledger.columns,
        )
    else:
        _compare_multiset(
            result, golden_df, actual_df, golden_key, actual_key,
            key_cols, tol_cols, dest_ledger.columns,
        )

    # ---- Step 5: Apply xfail / accept divergences.
    _apply_xfail_accept(result, dest_ledger.known_divergences)

    return result


# ---------------------------------------------------------------------------
# Diff implementations
# ---------------------------------------------------------------------------


def _compare_multiset(
    result: ComparisonResult,
    golden_full: pd.DataFrame,
    actual_full: pd.DataFrame,
    golden_key: pd.DataFrame,
    actual_key: pd.DataFrame,
    key_cols: list[str],
    tol_cols: list[str],
    col_policies: dict[str, ColumnPolicy],
) -> None:
    """Multiset diff on key columns; pairwise tolerance check on tol_cols.

    Strategy:
    1. Counter diff on key tuples → missing/extra key-rows.
    2. For rows whose key tuples match (intersection), check tolerance columns
       pairwise by joining on key columns and comparing abs differences.
    3. When all columns are key columns (no tol_cols) and row counts match but
       some rows differ, attempt cell localisation via key-join.
    """
    g_counter = _df_to_counter(golden_key)
    a_counter = _df_to_counter(actual_key)

    missing_counter = g_counter - a_counter
    extra_counter = a_counter - g_counter

    result.missing_rows = _counter_diff_to_dicts(missing_counter, key_cols) if key_cols else []
    result.extra_rows = _counter_diff_to_dicts(extra_counter, key_cols) if key_cols else []

    if result.missing_rows or result.extra_rows:
        result.verdict = "FAIL"

    # Pairwise tolerance check on matched rows.
    if tol_cols:
        _pairwise_tolerance_check(
            result, golden_full, actual_full, golden_key, actual_key,
            key_cols, tol_cols, col_policies,
        )

    # Cell localisation: when row counts match but rows differ, join on the
    # subset of key columns that uniquely identify a row (int-typed exact cols).
    if (
        result.missing_rows
        and result.extra_rows
        and result.golden_rows == result.actual_rows
        and not result.cell_mismatches
    ):
        # Use exact-policy integer columns as the join key for localisation.
        localisation_key = [
            c for c in key_cols
            if c in golden_full.columns
            and pd.api.types.is_integer_dtype(golden_full[c])
        ]
        _try_key_join_localisation(
            result, golden_full, actual_full, localisation_key
        )


def _compare_ordered(
    result: ComparisonResult,
    golden_full: pd.DataFrame,
    actual_full: pd.DataFrame,
    golden_key: pd.DataFrame,
    actual_key: pd.DataFrame,
    order_key: list[str],
    tol_cols: list[str],
    col_policies: dict[str, ColumnPolicy],
) -> None:
    """Sort both frames by order_key, then compare positionally."""
    sort_cols = [k for k in order_key if k in golden_full.columns]

    g_full_sorted = golden_full.sort_values(sort_cols).reset_index(drop=True)
    a_full_sorted = actual_full.sort_values(sort_cols).reset_index(drop=True)

    if len(g_full_sorted) != len(a_full_sorted):
        # Different row counts — use multiset key diff for artefacts.
        g_key_sorted = golden_key.sort_values(
            [c for c in sort_cols if c in golden_key.columns]
        ).reset_index(drop=True)
        a_key_sorted = actual_key.sort_values(
            [c for c in sort_cols if c in actual_key.columns]
        ).reset_index(drop=True)
        key_cols = list(golden_key.columns)
        g_counter = _df_to_counter(g_key_sorted)
        a_counter = _df_to_counter(a_key_sorted)
        result.missing_rows = _counter_diff_to_dicts(g_counter - a_counter, key_cols)
        result.extra_rows = _counter_diff_to_dicts(a_counter - g_counter, key_cols)
        if result.missing_rows or result.extra_rows:
            result.verdict = "FAIL"
        return

    # Same row count: positional compare with policy-aware tolerance.
    all_work_cols = list(dict.fromkeys(list(g_full_sorted.columns)))
    mismatches = _positional_cell_mismatches(
        g_full_sorted[all_work_cols],
        a_full_sorted[all_work_cols],
        col_policies,
    )
    # Filter out excluded columns from mismatches.
    exclude_cols = {
        col for col, pol in col_policies.items() if pol.policy == "exclude"
    }
    mismatches = [m for m in mismatches if m["column"] not in exclude_cols]
    if mismatches:
        result.cell_mismatches = mismatches
        result.verdict = "FAIL"


def _tol_distance(
    g_row: list[Any],
    a_row: list[Any],
    tol_cols: list[str],
    col_policies: dict[str, ColumnPolicy],
) -> float:
    """Return a scalar distance between two tolerance-column row vectors.

    For float columns: abs(g - a) / epsilon (normalised).
    For datetime columns: abs_seconds / tolerance (normalised).
    One-sided NULL vs a real value is treated as infinite distance.
    Both-NULL is distance 0.

    The returned value is the *maximum* normalised distance across all
    tolerance columns, making it suitable for greedy nearest-neighbour
    matching that minimises the worst-case per-column deviation.
    """
    max_dist: float = 0.0
    for col_idx, col in enumerate(tol_cols):
        pol = col_policies.get(col)
        if pol is None:
            continue
        g_val = g_row[col_idx]
        a_val = a_row[col_idx]
        g_na = _is_na(g_val)
        a_na = _is_na(a_val)
        if g_na and a_na:
            dist: float = 0.0
        elif g_na or a_na:
            dist = float("inf")
        elif pol.policy == "float":
            denom = pol.epsilon if pol.epsilon != 0 else 1.0
            dist = abs(_to_float(g_val) - _to_float(a_val)) / denom
        else:
            # datetime
            if isinstance(g_val, datetime.datetime) and isinstance(a_val, datetime.datetime):
                secs = abs((g_val - a_val).total_seconds())
            else:
                secs = abs(float(g_val) - float(a_val))
            denom = pol.tolerance if pol.tolerance != 0 else 1.0
            dist = secs / denom
        if dist > max_dist:
            max_dist = dist
    return max_dist


def _greedy_nn_match(
    g_rows: list[list[Any]],
    a_rows: list[list[Any]],
    tol_cols: list[str],
    col_policies: dict[str, ColumnPolicy],
) -> list[tuple[list[Any], list[Any]]]:
    """Greedily match each golden row to the nearest unmatched actual row.

    For each golden row in turn, scans all still-unmatched actual rows and
    picks the one with the smallest ``_tol_distance``.  O(N²) per group —
    correct for non-adversarial corpus data.  Greedy is not guaranteed
    globally optimal for pathologically-overlapping value clusters, but is
    sufficient for any realistic diff corpus.
    """
    unmatched = list(range(len(a_rows)))
    pairs: list[tuple[list[Any], list[Any]]] = []
    for g_row in g_rows:
        best_idx = min(
            unmatched,
            key=lambda ai: _tol_distance(g_row, a_rows[ai], tol_cols, col_policies),
        )
        pairs.append((g_row, a_rows[best_idx]))
        unmatched.remove(best_idx)
    return pairs


def _pairwise_tolerance_check(
    result: ComparisonResult,
    golden_full: pd.DataFrame,
    actual_full: pd.DataFrame,
    golden_key: pd.DataFrame,
    actual_key: pd.DataFrame,
    key_cols: list[str],
    tol_cols: list[str],
    col_policies: dict[str, ColumnPolicy],
) -> None:
    """Check tolerance columns pairwise on key-matched rows.

    For each unique key tuple present in both golden and actual with the same
    count (count mismatches are already caught by the Counter diff), rows in
    each group are matched using greedy nearest-neighbour: for each golden row
    in turn, the still-unmatched actual row with the smallest normalised
    distance (max over tolerance columns) is selected as its partner.

    Greedy NN avoids the Cartesian-product false FAILs of a relational merge
    and the lexicographic misordering of str()-sort, which breaks at digit
    boundaries (str('10.0') < str('9.0')).  The approach is O(N²) per group
    and is correct for non-adversarial corpus data; greedy is not provably
    optimal for pathologically-overlapping value clusters, but that is an
    acceptable limitation for a repo-authored validation corpus.

    When no key columns exist (all columns are tolerance columns), falls back
    to positional comparison over the whole frame.
    """
    if not key_cols:
        # No key — positional fallback over the whole frame.
        g_reset = golden_full[tol_cols].reset_index(drop=True)
        a_reset = actual_full[tol_cols].reset_index(drop=True)
        if len(g_reset) == len(a_reset):
            for col in tol_cols:
                pol = col_policies.get(col)
                for idx in range(len(g_reset)):
                    g_val = g_reset[col].iloc[idx]
                    a_val = a_reset[col].iloc[idx]
                    if pol and pol.policy == "float":
                        within = _float_within(g_val, a_val, pol.epsilon)
                    elif pol and pol.policy == "datetime":
                        within = _datetime_within(g_val, a_val, pol.tolerance)
                    else:
                        within = True
                    if not within:
                        result.cell_mismatches.append({
                            "column": col, "row_index": idx,
                            "golden": g_val, "actual": a_val,
                        })
                        result.verdict = "FAIL"
        return

    # Build a position-indexed view so we can slice rows by group membership.
    g_tagged = golden_full.reset_index(drop=True)
    a_tagged = actual_full.reset_index(drop=True)

    # Group each frame by its key tuple.
    g_key_tuples = [tuple(row) for row in golden_key.itertuples(index=False)]
    a_key_tuples = [tuple(row) for row in actual_key.itertuples(index=False)]

    from collections import defaultdict
    g_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    a_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)

    for pos, key_tuple in enumerate(g_key_tuples):
        g_groups[key_tuple].append(pos)
    for pos, key_tuple in enumerate(a_key_tuples):
        a_groups[key_tuple].append(pos)

    for key_tuple, g_positions in g_groups.items():
        a_positions = a_groups.get(key_tuple)
        if a_positions is None or len(g_positions) != len(a_positions):
            # Count mismatch already reported by Counter diff — skip.
            continue

        g_tol_rows = g_tagged.loc[g_positions, tol_cols].values.tolist()
        a_tol_rows = a_tagged.loc[a_positions, tol_cols].values.tolist()

        # Match each golden row to the nearest unmatched actual row.
        pairs = _greedy_nn_match(g_tol_rows, a_tol_rows, tol_cols, col_policies)

        for g_row, a_row in pairs:
            for col_idx, col in enumerate(tol_cols):
                pol = col_policies.get(col)
                if pol is None:
                    continue
                g_val = g_row[col_idx]
                a_val = a_row[col_idx]
                if pol.policy == "float":
                    within = _float_within(g_val, a_val, pol.epsilon)
                else:
                    within = _datetime_within(g_val, a_val, pol.tolerance)
                if not within:
                    result.cell_mismatches.append({
                        "column": col,
                        "golden": g_val,
                        "actual": a_val,
                    })
                    result.verdict = "FAIL"


def _try_key_join_localisation(
    result: ComparisonResult,
    golden: pd.DataFrame,
    actual: pd.DataFrame,
    key_cols: list[str],
) -> None:
    """Attempt cell-level localisation via declared key join when row counts match.

    Joins on *key_cols* (the declared exact-policy columns, or the DestLedger
    order_key).  If no key columns are available, skips localisation rather
    than guessing.
    """
    if not key_cols:
        # No declared key — skip rather than guess.
        return
    try:
        join_cols = [c for c in key_cols if c in golden.columns and c in actual.columns]
        if not join_cols:
            return
        merged = golden.merge(
            actual,
            on=join_cols,
            suffixes=("_golden", "_actual"),
            how="inner",
        )
        for col in golden.columns:
            if col in join_cols:
                continue
            g_col = f"{col}_golden"
            a_col = f"{col}_actual"
            if g_col in merged.columns and a_col in merged.columns:
                for _, row in merged.iterrows():
                    g_val = row[g_col]
                    a_val = row[a_col]
                    g_na = _is_na(g_val)
                    a_na = _is_na(a_val)
                    equal = (g_na and a_na) or (not g_na and not a_na and g_val == a_val)
                    if not equal:
                        result.cell_mismatches.append({
                            "column": col,
                            "golden": g_val,
                            "actual": a_val,
                        })
    except Exception as exc:
        from msb_ssis2sql.observability import logger
        logger.debug(
            "key-join localisation failed for columns {cols}: {exc}",
            cols=key_cols,
            exc=exc,
        )


def _apply_xfail_accept(
    result: ComparisonResult,
    divergences: list[KnownDivergence],
) -> None:
    """Mutate result.verdict for xfail/accept divergences."""
    for div in divergences:
        if div.handling == "xfail":
            result.applied_divergences.append(
                f"xfail({div.kind}): {div.reason}"
            )
            if result.verdict == "FAIL":
                result.verdict = "XFAIL"
            elif result.verdict == "PASS":
                result.verdict = "XPASS"
        elif div.handling == "accept":
            result.applied_divergences.append(
                f"accept({div.kind}): {div.reason}"
            )
            # accept does not change verdict

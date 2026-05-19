"""Reporting — render a ComparisonResult to a human-readable text block.

Public API
----------
render_result(result) -> str
    Formats a ComparisonResult into a text block that always contains the
    verdict and row counts, plus optional sections for missing/extra rows,
    cell mismatches, schema errors, and applied divergences.
"""
from __future__ import annotations

from validation.comparison import ComparisonResult

# Column width for the section header separator.
_WIDTH = 72


def _header(title: str) -> str:
    return f"--- {title} {'-' * max(0, _WIDTH - len(title) - 5)}"


def render_result(result: ComparisonResult) -> str:
    """Render *result* to a readable text block.

    The returned string always contains the verdict and the golden/actual row
    counts.  Additional sections are included when they contain data.

    Parameters
    ----------
    result:
        A ``ComparisonResult`` produced by ``compare()``.
    """
    lines: list[str] = []

    # Top-level summary line.
    label_parts: list[str] = []
    if result.package:
        label_parts.append(result.package)
    if result.destination:
        label_parts.append(result.destination)
    label = " / ".join(label_parts) if label_parts else "comparison"

    lines.append(_header(f"Comparison result: {label}"))
    lines.append(f"Verdict       : {result.verdict}")
    lines.append(f"Golden rows   : {result.golden_rows}")
    lines.append(f"Actual rows   : {result.actual_rows}")

    if result.schema_mismatch:
        lines.append("")
        lines.append(_header("Schema mismatch"))
        lines.append(result.schema_mismatch)

    if result.missing_rows:
        lines.append("")
        lines.append(_header(f"Missing rows ({len(result.missing_rows)})"))
        for row in result.missing_rows[:10]:
            lines.append(f"  {row}")
        if len(result.missing_rows) > 10:
            lines.append(f"  ... and {len(result.missing_rows) - 10} more")

    if result.extra_rows:
        lines.append("")
        lines.append(_header(f"Extra rows ({len(result.extra_rows)})"))
        for row in result.extra_rows[:10]:
            lines.append(f"  {row}")
        if len(result.extra_rows) > 10:
            lines.append(f"  ... and {len(result.extra_rows) - 10} more")

    if result.cell_mismatches:
        lines.append("")
        lines.append(_header(f"Cell mismatches ({len(result.cell_mismatches)})"))
        for m in result.cell_mismatches[:10]:
            col = m.get("column", "?")
            golden = m.get("golden", "?")
            actual = m.get("actual", "?")
            row_idx = m.get("row_index", "")
            idx_str = f" row {row_idx}" if row_idx != "" else ""
            lines.append(f"  {col}{idx_str}: golden={golden!r} actual={actual!r}")
        if len(result.cell_mismatches) > 10:
            lines.append(f"  ... and {len(result.cell_mismatches) - 10} more")

    if result.applied_divergences:
        lines.append("")
        lines.append(_header("Applied divergences"))
        for div in result.applied_divergences:
            lines.append(f"  {div}")

    lines.append(_header("end"))
    return "\n".join(lines)

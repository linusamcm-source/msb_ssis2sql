"""AC-6: ``examples/sales_etl.dtsx`` still converts.

* Emitted proc-name is ``usp_sales_etl`` (empty-reldir rule).
* The data-flow body (everything inside the proc wrapper, minus the deterministic
  header) is byte-identical to the pre-sprint baseline captured in
  ``tests/fixtures/sales_etl_baseline.sql``.

Will fail until convert-tree wraps in a stored proc and the generator's
header is reduced to three deterministic lines.
"""
from __future__ import annotations

import re
from pathlib import Path

from msb_ssis2sql.batch import convert_tree

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"
BASELINE = Path(__file__).parent / "fixtures" / "sales_etl_baseline.sql"


def _strip_header_block(sql: str) -> str:
    """Drop the leading ``/* ... */`` header comment block from a converted file."""
    text = sql.lstrip()
    if not text.startswith("/*"):
        return sql
    end = text.find("*/")
    if end == -1:
        return sql
    return text[end + 2 :].lstrip("\n")


def _unwrap_procedure(sql: str) -> str:
    """Return the inner body of a CREATE OR ALTER PROCEDURE wrapper.

    Strips:
      CREATE OR ALTER PROCEDURE <name>
      AS
      BEGIN
          SET NOCOUNT ON;

          <body>

      END;
      GO
    """
    m = re.search(
        r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+\w+\s*\nAS\s*\nBEGIN\s*\n\s*SET\s+NOCOUNT\s+ON;\s*\n\s*\n(.*)\nEND;",
        sql,
        re.DOTALL,
    )
    if not m:
        return sql
    inner = m.group(1)
    # The body was indented one level (4 spaces). Strip that uniformly.
    return "\n".join(
        (line[4:] if line.startswith("    ") else line) for line in inner.splitlines()
    ).rstrip() + "\n"


def test_sales_etl_emits_usp_sales_etl_procedure(tmp_path):
    """The proc-name follows the empty-reldir rule: usp_<SanitisedPackageName>.

    PackageName is ``CustomerSalesETL`` in ObjectName, BUT the plan locks the
    proc-name on the FILE name (sales_etl.dtsx), so:
        sanitise("sales_etl") -> "sales_etl"
        -> "usp_sales_etl"
    """
    out = tmp_path / "out"
    convert_tree(EXAMPLES, out)
    text = (out / "sales_etl.sql").read_text(encoding="utf-8")
    assert "CREATE OR ALTER PROCEDURE usp_sales_etl" in text, (
        f"expected exact proc-name 'usp_sales_etl' in header line; got:\n{text[:600]}"
    )


def test_sales_etl_body_byte_identical_to_baseline(tmp_path):
    """Stripping header + proc wrapper recovers the original data-flow body."""
    out = tmp_path / "out"
    convert_tree(EXAMPLES, out)
    sql = (out / "sales_etl.sql").read_text(encoding="utf-8")

    body = _unwrap_procedure(_strip_header_block(sql))
    baseline = BASELINE.read_text(encoding="utf-8")
    assert body == baseline, (
        f"data-flow body drift:\n--- baseline ---\n{baseline}\n--- got ---\n{body}"
    )

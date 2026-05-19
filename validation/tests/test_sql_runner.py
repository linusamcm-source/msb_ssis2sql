"""Tests for validation.sql_runner — the Converted-SQL runner.

RED phase: all tests import from ``validation.sql_runner`` which does not yet
exist.  Every test therefore fails with ``ImportError`` on collection.  That is
the correct TDD RED state before the engineer authors the module in GREEN.

What is tested (grouped by AC):

AC1 — Integration: passthrough_basic end-to-end
    ``test_run_passthrough_basic_returns_expected_columns``
        After provision + seed, run() yields a RunResult whose dst_items
        DataFrame contains exactly the expected columns.
    ``test_run_passthrough_basic_returns_expected_row_count``
        After provision + seed, dst_items has the same row count as the seed CSV.

AC2 — Server-free: SQL execution error surfaces as RunResult.error, no crash
    ``test_run_sql_error_populates_error_field``
        Inject a fake connection whose cursor.execute raises pyodbc.Error.
        run() returns RunResult(error=<non-empty str>), no exception propagates.
    ``test_run_sql_error_leaves_data_empty``
        Same injection — RunResult.data is an empty dict when error is set.
    ``test_go_split_produces_multiple_batches``
        Unit test that the internal GO-batch splitter (exposed as
        ``sql_runner.split_sql_batches``) splits correctly.
    ``test_warnings_from_convert_file_propagated``
        Monkeypatches convert_file to return a result with non-empty warnings;
        verifies RunResult.warnings matches.

AC3 — Integration: re-run idempotency via truncate_destinations
    ``test_rerun_after_truncate_yields_same_row_count``
        Run once, call truncate_destinations, run again; row count identical.

API contract pinned by these tests
-----------------------------------
RunResult — dataclass
    data: dict[str, pandas.DataFrame]
        Mapping of destination table name → read-back DataFrame.
        Empty dict when error is set.
    warnings: list[str]
        Warnings from ``ssis2sql.convert_file`` (may be empty).
    error: str
        Non-empty when the SQL execution step failed; empty string on success.

run(conn, package_dir, *, schema_types=None) -> RunResult
    Convert ``package_dir/package.dtsx`` via ``ssis2sql.convert_file``,
    split on GO, execute each batch via ``conn.cursor()``, read back every
    ``dst_*`` table into a DataFrame, return RunResult.
    On any SQL/pyodbc exception: return RunResult(data={}, warnings=...,
    error=<str(exc)>) — never raise.

read_destination(conn, table, schema_types) -> pandas.DataFrame
    SELECT * FROM <table> into a DataFrame.  ``schema_types`` is a mapping
    of col_name → SQL type token used for coercion (may be None/empty — no
    coercion performed in that case).

split_sql_batches(sql) -> list[str]
    Split *sql* on GO lines (same logic as provisioning._split_batches).
    Exposed at module level for unit testing.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from validation.sql_runner import RunResult, read_destination, run, split_sql_batches

if TYPE_CHECKING:
    import pyodbc

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parents[2] / "validation" / "corpus"
_PASSTHROUGH_DIR: Path = _CORPUS_ROOT / "passthrough_basic"

# Expected shape for passthrough_basic dst_items.
_EXPECTED_COLUMNS: frozenset[str] = frozenset({"id", "name", "amount", "active", "loaded_at"})
_EXPECTED_ROW_COUNT: int = 5  # matches seed/src_items.csv (5 data rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_pyodbc_error() -> type:
    """Return a minimal pyodbc.Error stand-in for the server-free error tests."""

    class _FakePyodbcError(Exception):
        pass

    return _FakePyodbcError


def _make_failing_conn(exc_type: type) -> MagicMock:
    """Build a fake connection whose cursor.execute always raises *exc_type*."""
    cursor = MagicMock()
    cursor.execute.side_effect = exc_type("Simulated SQL execution failure")
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# AC2 — Server-free unit tests (no live SQL Server required)
# ---------------------------------------------------------------------------


def test_go_split_produces_multiple_batches() -> None:
    """split_sql_batches correctly splits SQL on GO separators.

    Verifies the runner's internal batch-splitting logic handles:
    - Standalone GO on its own line.
    - Leading/trailing whitespace around GO.
    - Empty batches after a GO are dropped.
    - Content after the final GO (no trailing GO) is preserved.
    """
    sql = textwrap.dedent("""\
        CREATE TABLE dbo.foo (id INT);
        GO
        INSERT INTO dbo.foo VALUES (1);
        GO

        GO
        SELECT * FROM dbo.foo;
    """)
    batches = split_sql_batches(sql)
    assert len(batches) == 3, f"expected 3 non-empty batches, got {len(batches)}: {batches}"
    assert "CREATE TABLE" in batches[0]
    assert "INSERT" in batches[1]
    assert "SELECT" in batches[2]


def test_go_split_no_go_returns_whole_string() -> None:
    """split_sql_batches with no GO returns the whole SQL as one batch."""
    sql = "SELECT 1;\nSELECT 2;"
    batches = split_sql_batches(sql)
    assert batches == [sql]


def test_go_split_empty_string_returns_empty_list() -> None:
    """split_sql_batches on empty/whitespace-only SQL returns []."""
    assert split_sql_batches("") == []
    assert split_sql_batches("   \n  ") == []


def test_run_sql_error_populates_error_field() -> None:
    """A pyodbc-style execution error surfaces in RunResult.error, no crash.

    Uses a stub connection whose execute raises a fake exception.  The runner
    must catch all exceptions from cursor.execute and store them as RunResult.error.
    This test is SERVER-FREE — it does not touch the live remote SQL Server.
    """
    FakeError = _fake_pyodbc_error()
    fake_conn = _make_failing_conn(FakeError)

    with patch("validation.sql_runner.pyodbc.Error", FakeError):
        result = run(fake_conn, _PASSTHROUGH_DIR)

    assert isinstance(result, RunResult)
    assert result.error, "RunResult.error must be non-empty when SQL execution fails"
    assert "Simulated SQL execution failure" in result.error


def test_run_sql_error_leaves_data_empty() -> None:
    """RunResult.data is an empty dict when the SQL execution step fails.

    No partial-read data should leak into the result when execution errors.
    SERVER-FREE test — stub connection only.
    """
    FakeError = _fake_pyodbc_error()
    fake_conn = _make_failing_conn(FakeError)

    with patch("validation.sql_runner.pyodbc.Error", FakeError):
        result = run(fake_conn, _PASSTHROUGH_DIR)

    assert result.data == {}, (
        f"RunResult.data must be empty on error, got: {list(result.data.keys())}"
    )


def test_warnings_from_convert_file_propagated() -> None:
    """Warnings returned by convert_file are threaded through to RunResult.warnings.

    Uses monkeypatch to inject a synthetic ConversionResult with non-empty
    warnings.  SERVER-FREE — the stub connection accepts all execute calls
    (MagicMock default) and we skip actual result reading by asserting warnings
    only.
    """
    from ssis2sql.generator import ConversionResult

    synthetic_warnings = ["Component 'Foo' was skipped", "Unknown transform type: Bar"]
    fake_result = ConversionResult(
        sql="SELECT 1;",
        warnings=synthetic_warnings,
        package=MagicMock(),
    )

    fake_conn = MagicMock()
    # cursor.fetchall() returns empty list — simulate empty dst table
    fake_conn.cursor.return_value.fetchall.return_value = []
    fake_conn.cursor.return_value.description = []

    with patch("validation.sql_runner.convert_file", return_value=fake_result):
        result = run(fake_conn, _PASSTHROUGH_DIR)

    assert result.warnings == synthetic_warnings, (
        f"Expected warnings {synthetic_warnings!r}, got {result.warnings!r}"
    )


def test_run_result_is_dataclass_with_expected_fields() -> None:
    """RunResult has data, warnings, and error fields with correct defaults.

    SERVER-FREE — constructs RunResult directly without calling run().
    """
    import pandas as pd

    r = RunResult(data={}, warnings=[], error="")
    assert hasattr(r, "data")
    assert hasattr(r, "warnings")
    assert hasattr(r, "error")
    assert r.error == ""
    assert r.warnings == []
    assert r.data == {}

    # data can hold a DataFrame
    df = pd.DataFrame({"id": [1, 2]})
    r2 = RunResult(data={"dst_items": df}, warnings=["w"], error="")
    assert "dst_items" in r2.data
    assert len(r2.data["dst_items"]) == 2


# ---------------------------------------------------------------------------
# AC1 — Integration: passthrough_basic end-to-end (live server required)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fresh_db")
def test_run_passthrough_basic_returns_expected_columns(fresh_db: "pyodbc.Connection") -> None:
    """run() on passthrough_basic yields a RunResult with the correct columns.

    INTEGRATION — requires live SQL Server; skips via fresh_db fixture when
    ``MSSQL_*`` env vars are absent or the server is unreachable.

    Steps:
    1. provision() — create src_items and dst_items DDL.
    2. seed() — load seed/src_items.csv into src_items.
    3. run() — transpile package.dtsx, execute SQL, read dst_items.
    4. Assert column names match schema.
    """
    from validation.provisioning import provision, seed

    provision(fresh_db, _PASSTHROUGH_DIR)
    seed(fresh_db, _PASSTHROUGH_DIR)

    result = run(fresh_db, _PASSTHROUGH_DIR)

    assert not result.error, f"run() returned an error: {result.error}"
    assert "dst_items" in result.data, (
        f"'dst_items' not found in RunResult.data — got keys: {list(result.data.keys())}"
    )
    df = result.data["dst_items"]
    actual_cols = frozenset(df.columns)
    assert actual_cols == _EXPECTED_COLUMNS, (
        f"Column mismatch: expected {sorted(_EXPECTED_COLUMNS)}, got {sorted(actual_cols)}"
    )


@pytest.mark.usefixtures("fresh_db")
def test_run_passthrough_basic_returns_expected_row_count(fresh_db: "pyodbc.Connection") -> None:
    """run() on passthrough_basic writes the correct row count to dst_items.

    INTEGRATION — skips when server is unreachable (see fresh_db fixture).

    The seed CSV has 5 data rows; a passthrough package writes all 5 to dst_items.
    """
    from validation.provisioning import provision, seed

    provision(fresh_db, _PASSTHROUGH_DIR)
    seed(fresh_db, _PASSTHROUGH_DIR)

    result = run(fresh_db, _PASSTHROUGH_DIR)

    assert not result.error, f"run() returned an error: {result.error}"
    df = result.data["dst_items"]
    assert len(df) == _EXPECTED_ROW_COUNT, (
        f"Expected {_EXPECTED_ROW_COUNT} rows in dst_items, got {len(df)}"
    )


# ---------------------------------------------------------------------------
# AC3 — Integration: re-run idempotency via truncate_destinations
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fresh_db")
def test_rerun_after_truncate_yields_same_row_count(fresh_db: "pyodbc.Connection") -> None:
    """A second run after truncate_destinations produces identical results.

    INTEGRATION — skips when server is unreachable.

    Without truncation, INSERT-based packages accumulate rows across runs.
    truncate_destinations must clear dst_* tables so the second run starts
    clean and produces the same row count as the first run.
    """
    from validation.provisioning import provision, seed, truncate_destinations

    provision(fresh_db, _PASSTHROUGH_DIR)
    seed(fresh_db, _PASSTHROUGH_DIR)

    result_1 = run(fresh_db, _PASSTHROUGH_DIR)
    assert not result_1.error, f"first run() failed: {result_1.error}"
    count_1 = len(result_1.data["dst_items"])

    truncate_destinations(fresh_db, _PASSTHROUGH_DIR)

    result_2 = run(fresh_db, _PASSTHROUGH_DIR)
    assert not result_2.error, f"second run() failed: {result_2.error}"
    count_2 = len(result_2.data["dst_items"])

    assert count_1 == count_2, (
        f"Row count differs across runs: first={count_1}, second={count_2}. "
        "Destination table was not cleanly truncated between runs."
    )


# ---------------------------------------------------------------------------
# read_destination — server-free unit test
# ---------------------------------------------------------------------------


def test_read_destination_returns_dataframe() -> None:
    """read_destination returns a pandas DataFrame from a fake cursor.

    SERVER-FREE — stubs the connection so no live server is needed.
    Verifies that read_destination correctly maps cursor rows to a DataFrame
    using column names from cursor.description.
    """
    import pandas as pd

    cursor = MagicMock()
    cursor.description = [("id", None, None, None, None, None, None),
                          ("name", None, None, None, None, None, None)]
    cursor.fetchall.return_value = [(1, "Alpha"), (2, "Beta")]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    df = read_destination(conn, "dst_items", schema_types=None)

    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2
    assert df.loc[0, "id"] == 1
    assert df.loc[1, "name"] == "Beta"


def test_read_destination_coercion_nullable_dtype_no_raise() -> None:
    """schema_types coercion on a NULL-bearing numeric column does not raise.

    Nullable pandas dtypes (Int64, float64, boolean) handle None/NaN without
    error — a NULL-bearing column is NOT the trigger for the logger.warning
    coercion-failure path.  SERVER-FREE.
    """
    import pandas as pd

    cursor = MagicMock()
    cursor.description = [("amount", None, None, None, None, None, None)]
    # Row 1: numeric value; Row 2: None (SQL NULL).
    cursor.fetchall.return_value = [(99.5,), (None,)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    # Must not raise and must produce the correct nullable float64 dtype.
    df = read_destination(conn, "dst_items", schema_types={"amount": "NUMERIC"})

    assert df["amount"].dtype == "float64"
    assert pd.isna(df.loc[1, "amount"])


def test_read_destination_coercion_failure_preserved_in_dataframe() -> None:
    """A non-coercible column does not crash read_destination and is preserved.

    When a column contains data that cannot be cast to the declared pandas
    dtype, read_destination logs the failure via logger.warning and leaves the
    column dtype unchanged.  The column must still be present in the returned
    DataFrame — not dropped, not raised.  SERVER-FREE.
    """
    cursor = MagicMock()
    cursor.description = [("qty", None, None, None, None, None, None)]
    # "not_a_number" cannot be cast to Int64.
    cursor.fetchall.return_value = [("not_a_number",)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    # Must not raise despite the non-coercible value.
    df = read_destination(conn, "dst_items", schema_types={"qty": "INT"})

    # Column preserved in the DataFrame at its original (object) dtype.
    assert "qty" in df.columns
    assert df.loc[0, "qty"] == "not_a_number"
    # dtype is unchanged from pyodbc's inferred type — not Int64.
    assert str(df["qty"].dtype) != "Int64"


def test_run_readback_error_captured_in_result() -> None:
    """A pyodbc.Error during dst read-back is captured, not raised.

    The write batches succeed; the subsequent SELECT for read-back raises
    pyodbc.Error.  run() must catch it and return RunResult(error=<str>,
    data={}) — consistent with the "never raises" contract.
    SERVER-FREE.
    """
    from ssis2sql.generator import ConversionResult

    FakeError = _fake_pyodbc_error()

    # First cursor call (write loop): succeeds.
    # Second cursor call (read_destination SELECT): raises.
    write_cursor = MagicMock()
    read_cursor = MagicMock()
    read_cursor.execute.side_effect = FakeError("Read-back SELECT failed")

    call_count = {"n": 0}

    def _cursor_factory():
        call_count["n"] += 1
        # First call = write batch cursor; subsequent calls = read_destination.
        return write_cursor if call_count["n"] == 1 else read_cursor

    conn = MagicMock()
    conn.cursor.side_effect = _cursor_factory

    fake_conversion = ConversionResult(
        sql="SELECT 1;",
        warnings=[],
        package=MagicMock(),
    )

    with (
        patch("validation.sql_runner.convert_file", return_value=fake_conversion),
        patch("validation.sql_runner.pyodbc.Error", FakeError),
    ):
        result = run(conn, _PASSTHROUGH_DIR)

    assert result.error, "RunResult.error must be non-empty when read-back fails"
    assert "Read-back SELECT failed" in result.error
    assert result.data == {}, f"data must be empty on read-back error, got {result.data}"


@pytest.mark.usefixtures("fresh_db")
def test_run_invalid_sql_surfaces_structured_error(fresh_db: "pyodbc.Connection") -> None:
    """A deliberately broken package returns RunResult.error, never raises.

    monkeypatches convert_file to return genuinely invalid T-SQL.  run() is
    called against a live fresh_db; the real pyodbc.Error from SQL Server is
    captured into RunResult.error.

    INTEGRATION — skips cleanly when the server is unreachable.
    """
    from ssis2sql.generator import ConversionResult

    broken_sql = "SELECT * FROM nonexistent_garbage_table_xyz_story3;"
    fake_conversion = ConversionResult(
        sql=broken_sql,
        warnings=["deliberate broken SQL for AC2 test"],
        package=MagicMock(),
    )

    with patch("validation.sql_runner.convert_file", return_value=fake_conversion):
        result = run(fresh_db, _PASSTHROUGH_DIR)

    assert result.error, (
        "run() must populate RunResult.error for invalid SQL; got empty error"
    )
    assert result.data == {}, f"data must be empty on error, got {result.data}"
    assert result.warnings == ["deliberate broken SQL for AC2 test"]

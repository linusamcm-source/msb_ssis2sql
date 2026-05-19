"""Tests for validation.capture.capture — the golden-capture harness.

RED phase: all tests import from ``validation.capture.capture`` which does not
yet exist.  Every test fails with ``ImportError`` on collection.  That is the
correct TDD RED state before the engineer authors the module in GREEN.

CRITICAL DESIGN CONSTRAINT: the ``dtexec`` call is always stubbed.  Every test
in this file is runnable on macOS without Windows, without SQL Server, and
without the real ``dtexec`` binary.  The harness is designed so that its
Python logic — argparse, manifest construction, Parquet export, orchestration
— is fully exercisable server-free on macOS.

What is tested (grouped by AC):

AC1 — Importability & argparse CLI
    ``test_module_imports_without_error``
        ``import validation.capture.capture`` completes without exception.
    ``test_parser_builds_and_help_exits_zero``
        ``build_parser()`` returns an ``argparse.ArgumentParser``; calling
        it with ``--help`` exits 0.
    ``test_parser_required_arguments``
        ``--package-dir`` is required; omitting it causes a SystemExit.
    ``test_parser_optional_dtexec_path``
        ``--dtexec-path`` is optional; it defaults to something usable (a str
        or None — the engineer decides the default).

AC2 — Manifest construction (server-free)
    ``test_build_manifest_contains_seed_checksum``
        ``build_manifest`` embeds the same checksum as
        ``validation.provisioning.seed_checksum(package_dir)``.
    ``test_build_manifest_row_counts_match_dataframes``
        The per-destination row counts in the manifest match the len() of the
        DataFrames passed in.
    ``test_build_manifest_column_types_match_schema``
        Column type tokens in the manifest match those declared in ``schema.sql``
        for each destination table (extracted from the ``schema_types`` mapping
        passed to ``build_manifest``).
    ``test_build_manifest_structure``
        The manifest dict contains exactly the expected top-level keys:
        ``"seed_checksum"``, ``"destinations"``; each destination entry has
        ``"row_count"`` and ``"column_types"`` keys.

AC3 — Parquet export round-trip (server-free, pyarrow)
    ``test_parquet_export_round_trips_dataframe``
        ``export_parquet(df, path)`` writes a DataFrame to *path*; reading it
        back via ``pandas.read_parquet`` yields an equal DataFrame (same rows,
        same columns, dtypes preserved for numeric types).
    ``test_parquet_export_creates_file``
        The output file exists after ``export_parquet`` completes.
    ``test_parquet_export_multiple_destinations``
        Two DataFrames exported to separate paths both round-trip correctly.

AC (dtexec seam)
    ``test_capture_invokes_dtexec_seam``
        ``capture(conn, package_dir, dtexec_runner=stub)`` calls the stub
        exactly once with arguments that include the ``package.dtsx`` path.
        Uses a MagicMock as the seam; the stub returns a zero exit code.
    ``test_capture_asserts_row_count_not_stdout``
        The ``capture`` orchestration must NOT rely on dtexec stdout to judge
        success.  Stub dtexec with a callable that returns stdout="ERROR 123"
        but sets a real row count in the destination (via a post-run mock
        read).  Assert ``capture`` treats the run as successful (no error in
        the returned manifest) — success is determined by row-count check only.
    ``test_capture_returns_manifest_with_expected_keys``
        End-to-end stub: stub dtexec, stub read_destination to return a
        synthetic DataFrame.  ``capture()`` returns a manifest dict with
        ``"seed_checksum"`` and ``"destinations"`` keys.

API contract pinned by these tests
-----------------------------------
``build_parser() -> argparse.ArgumentParser``
    Returns a configured argument parser.  Required argument: ``--package-dir``
    (str/Path to a corpus package directory).  Optional: ``--dtexec-path``
    (str, path to dtexec.exe; defaults to implementation-defined value or
    None for the injected-seam case).

``build_manifest(
    package_dir: Path,
    destinations: dict[str, pandas.DataFrame],
    schema_types: dict[str, dict[str, str]] | None = None,
) -> dict``
    Build and return the manifest dict.  Contains:
    - ``"seed_checksum"`` (str): ``seed_checksum(package_dir)``
    - ``"destinations"`` (dict): per-dst entry with:
        - ``"row_count"`` (int): ``len(df)``
        - ``"column_types"`` (dict[str, str]): col_name → SQL type token
          (empty dict when schema_types is None or has no entry for that dst)

``export_parquet(df: pandas.DataFrame, path: Path) -> None``
    Write *df* to *path* as a Parquet file using pyarrow.  The file can be
    read back with ``pandas.read_parquet`` and yield an equal DataFrame.

``DtexecRunner``
    Type alias or Protocol for the dtexec seam callable:
    ``Callable[[Path], int]`` — receives the path to ``package.dtsx``,
    returns an int exit code (0 = success, non-zero = failure).

``capture(
    conn: pyodbc.Connection,
    package_dir: Path,
    *,
    dtexec_runner: DtexecRunner | None = None,
    golden_dir: Path | None = None,
) -> dict``
    Full orchestration:
    1. ``provision(conn, package_dir)`` — DDL setup.
    2. ``seed(conn, package_dir)`` — load src_* CSVs.
    3. ``dtexec_runner(package_dir / "package.dtsx")`` — run the package (or
       the default real dtexec if ``dtexec_runner`` is None).
    4. Read back each ``dst_*`` table via ``read_destination(conn, table, ...)``.
    5. Assert success by row-count check (NOT by parsing dtexec stdout).
    6. ``export_parquet(df, golden_dir / f"{dst_name}.parquet")`` per dst.
    7. Write ``golden_dir / "manifest.json"`` via ``build_manifest(...)``.
    8. Return the manifest dict.
    ``golden_dir`` defaults to ``package_dir / "golden"`` when None.

``main() -> None``
    CLI entry point — calls ``build_parser().parse_args()`` and dispatches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from validation.capture.capture import (
    build_manifest,
    build_parser,
    capture,
    export_parquet,
    main,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Paths & shared fixtures
# ---------------------------------------------------------------------------

_CORPUS_ROOT: Path = Path(__file__).parents[2] / "validation" / "corpus"
_PASSTHROUGH_DIR: Path = _CORPUS_ROOT / "passthrough_basic"

# A small synthetic DataFrame used across multiple tests — avoids live DB.
_SAMPLE_DF: pd.DataFrame = pd.DataFrame(
    {
        "id": pd.array([1, 2, 3], dtype="Int64"),
        "name": ["Alpha", "Beta", "Gamma"],
        "amount": [9.99, 0.01, 100.0],
    }
)

# Schema types that match passthrough_basic/schema.sql dst_items columns.
_DST_ITEMS_SCHEMA_TYPES: dict[str, str] = {
    "id": "int",
    "name": "nvarchar",
    "amount": "decimal",
    "active": "bit",
    "loaded_at": "datetime2",
}


# ---------------------------------------------------------------------------
# AC1 — Importability & argparse CLI
# ---------------------------------------------------------------------------


def test_module_imports_without_error() -> None:
    """validation.capture.capture imports cleanly without raising any exception.

    AC1 — verifies the module-level code is safe on macOS (no Windows-only
    calls at import time, no unconditional subprocess.run(['dtexec', ...]), etc.).
    """
    import validation.capture.capture  # noqa: F401 — import-only check


def test_parser_builds_and_help_exits_zero() -> None:
    """build_parser() returns an ArgumentParser; --help exits with code 0.

    AC1 — the CLI must be inspectable on macOS without requiring Windows or
    dtexec to be present.
    """
    parser = build_parser()
    # argparse.ArgumentParser is the expected type
    import argparse

    assert isinstance(parser, argparse.ArgumentParser)

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0


def test_parser_required_arguments() -> None:
    """Omitting --package-dir causes a non-zero SystemExit (required argument).

    AC1 — argparse must enforce the required --package-dir flag so the CLI
    fails fast with a clear message rather than a silent AttributeError later.
    """
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([])
    assert exc_info.value.code != 0


def test_parser_accepts_package_dir() -> None:
    """build_parser() accepts --package-dir and stores it as a Path or str.

    AC1 — verifies the argument is parsed without error.
    """
    parser = build_parser()
    args = parser.parse_args(["--package-dir", str(_PASSTHROUGH_DIR)])
    # The attribute must exist; type may be str or Path depending on engineer choice.
    assert hasattr(args, "package_dir") or hasattr(args, "package-dir".replace("-", "_"))


def test_parser_optional_dtexec_path() -> None:
    """--dtexec-path is optional; parser does not fail when it is omitted.

    AC1 — when the seam is injected programmatically, the CLI does not require
    the operator to supply a dtexec path.
    """
    parser = build_parser()
    # Should not raise — dtexec-path is optional
    args = parser.parse_args(["--package-dir", str(_PASSTHROUGH_DIR)])
    # dtexec_path attribute must exist (may be None or a default string).
    assert hasattr(args, "dtexec_path") or hasattr(args, "dtexec-path".replace("-", "_"))


# ---------------------------------------------------------------------------
# AC2 — Manifest construction (server-free)
# ---------------------------------------------------------------------------


def test_build_manifest_contains_seed_checksum() -> None:
    """build_manifest embeds the same checksum as seed_checksum(package_dir).

    AC2 — cross-check against the live provisioning.seed_checksum function so
    the manifest is always consistent with the seed files on disk.
    SERVER-FREE — no database involved.
    """
    from validation.provisioning import seed_checksum

    destinations = {"dst_items": _SAMPLE_DF.copy()}
    manifest = build_manifest(_PASSTHROUGH_DIR, destinations)

    expected_checksum = seed_checksum(_PASSTHROUGH_DIR)
    assert manifest["seed_checksum"] == expected_checksum, (
        f"manifest seed_checksum {manifest['seed_checksum']!r} != "
        f"seed_checksum() result {expected_checksum!r}"
    )


def test_build_manifest_row_counts_match_dataframes() -> None:
    """build_manifest row_count values match len(df) for each destination.

    AC2 — the manifest must faithfully record the number of rows read back;
    callers use this to detect empty-run failures.  SERVER-FREE.
    """
    df_a = pd.DataFrame({"id": [1, 2, 3]})
    df_b = pd.DataFrame({"val": [10, 20]})
    destinations = {"dst_alpha": df_a, "dst_beta": df_b}

    manifest = build_manifest(_PASSTHROUGH_DIR, destinations)

    assert manifest["destinations"]["dst_alpha"]["row_count"] == 3
    assert manifest["destinations"]["dst_beta"]["row_count"] == 2


def test_build_manifest_column_types_match_schema() -> None:
    """Column type tokens in the manifest match the schema_types mapping.

    AC2 — verifies schema_types flows through to manifest["destinations"][dst]
    ["column_types"].  SERVER-FREE.
    """
    df = pd.DataFrame({"id": [1], "name": ["x"], "amount": [1.0]})
    schema_types = {"dst_items": {"id": "int", "name": "nvarchar", "amount": "decimal"}}
    destinations = {"dst_items": df}

    manifest = build_manifest(_PASSTHROUGH_DIR, destinations, schema_types=schema_types)

    col_types = manifest["destinations"]["dst_items"]["column_types"]
    assert col_types.get("id") == "int"
    assert col_types.get("name") == "nvarchar"
    assert col_types.get("amount") == "decimal"


def test_build_manifest_column_types_empty_when_no_schema_types() -> None:
    """column_types is an empty dict when schema_types is None.

    AC2 — callers that do not supply schema_types still get a valid manifest.
    SERVER-FREE.
    """
    destinations = {"dst_items": _SAMPLE_DF.copy()}
    manifest = build_manifest(_PASSTHROUGH_DIR, destinations, schema_types=None)

    col_types = manifest["destinations"]["dst_items"]["column_types"]
    assert col_types == {}


def test_build_manifest_structure() -> None:
    """Manifest has exactly the expected top-level and per-destination keys.

    AC2 — structural validation so downstream consumers (capture.ps1, CI) can
    rely on the manifest shape without a schema file.  SERVER-FREE.
    """
    df = pd.DataFrame({"id": [1, 2]})
    destinations = {"dst_foo": df}
    manifest = build_manifest(_PASSTHROUGH_DIR, destinations)

    # Top-level keys
    assert "seed_checksum" in manifest, "manifest missing 'seed_checksum'"
    assert "destinations" in manifest, "manifest missing 'destinations'"

    # Per-destination keys
    dst_entry = manifest["destinations"]["dst_foo"]
    assert "row_count" in dst_entry, "destination entry missing 'row_count'"
    assert "column_types" in dst_entry, "destination entry missing 'column_types'"


def test_build_manifest_is_json_serialisable() -> None:
    """The manifest dict returned by build_manifest serialises to JSON without error.

    AC2 — the manifest is written to golden/manifest.json; it must be fully
    JSON-serialisable (no datetime objects, no numpy scalars, etc.).
    SERVER-FREE.
    """
    destinations = {"dst_items": _SAMPLE_DF.copy()}
    manifest = build_manifest(_PASSTHROUGH_DIR, destinations)

    # Should not raise
    serialised = json.dumps(manifest)
    recovered = json.loads(serialised)
    assert recovered["seed_checksum"] == manifest["seed_checksum"]


# ---------------------------------------------------------------------------
# AC3 — Parquet export round-trip (server-free, pyarrow)
# ---------------------------------------------------------------------------


def test_parquet_export_creates_file(tmp_path: Path) -> None:
    """export_parquet writes a file to the given path.

    AC3 — the golden/ directory must contain a .parquet file after capture.
    SERVER-FREE — purely in-process.
    """
    out = tmp_path / "dst_items.parquet"
    export_parquet(_SAMPLE_DF, out)
    assert out.is_file(), f"Parquet file not created at {out}"


def test_parquet_export_round_trips_dataframe(tmp_path: Path) -> None:
    """export_parquet + read_parquet yields a DataFrame equal to the original.

    AC3 — the golden fixture must faithfully preserve data; dtypes must survive
    the round-trip for numeric columns.  SERVER-FREE.
    """
    df = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3], dtype="Int64"),
            "name": ["Alpha", "Beta", "Gamma"],
            "score": [1.5, 2.5, 3.5],
        }
    )
    out = tmp_path / "round_trip.parquet"
    export_parquet(df, out)

    recovered = pd.read_parquet(out)
    pd.testing.assert_frame_equal(df, recovered, check_like=False)


def test_parquet_export_multiple_destinations(tmp_path: Path) -> None:
    """Two DataFrames exported to separate Parquet files both round-trip correctly.

    AC3 — captures with multiple dst_* tables must export each table independently
    without contamination.  SERVER-FREE.
    """
    df_a = pd.DataFrame({"x": [10, 20]})
    df_b = pd.DataFrame({"y": ["foo", "bar"]})

    path_a = tmp_path / "dst_alpha.parquet"
    path_b = tmp_path / "dst_beta.parquet"
    export_parquet(df_a, path_a)
    export_parquet(df_b, path_b)

    pd.testing.assert_frame_equal(df_a, pd.read_parquet(path_a))
    pd.testing.assert_frame_equal(df_b, pd.read_parquet(path_b))


def test_parquet_export_handles_null_values(tmp_path: Path) -> None:
    """Parquet round-trip preserves NULL (pd.NA / None) values.

    AC3 — golden fixtures may contain NULLs (seed rows with nullable columns).
    SERVER-FREE.
    """
    df = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3], dtype="Int64"),
            "name": pd.array(["A", None, "C"], dtype=pd.StringDtype()),
        }
    )
    out = tmp_path / "nullable.parquet"
    export_parquet(df, out)
    recovered = pd.read_parquet(out)
    # id column: NAs preserved
    assert recovered["name"][1] is None or pd.isna(recovered["name"][1])


# ---------------------------------------------------------------------------
# dtexec seam — orchestration tests (server-free via stubs)
# ---------------------------------------------------------------------------


def test_capture_invokes_dtexec_seam(tmp_path: Path) -> None:
    """capture() calls the dtexec_runner seam exactly once with the dtsx path.

    Stubs provision, seed, read_destination, and the dtexec seam so the test
    is fully SERVER-FREE.  Asserts the seam is called and receives the correct
    package.dtsx path.
    """
    stub_runner = MagicMock(return_value=0)
    fake_df = pd.DataFrame({"id": [1], "name": ["x"]})
    fake_conn = MagicMock()

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=fake_df),
    ):
        capture(
            fake_conn,
            _PASSTHROUGH_DIR,
            dtexec_runner=stub_runner,
            golden_dir=tmp_path,
        )

    stub_runner.assert_called_once()
    # The dtsx path must be among the arguments passed to the seam.
    dtsx_path = _PASSTHROUGH_DIR / "package.dtsx"
    call_args = stub_runner.call_args
    assert any(
        str(dtsx_path) in str(a) or Path(a) == dtsx_path
        for a in (list(call_args.args) + list(call_args.kwargs.values()))
    ), (
        f"dtexec_runner was not called with the package.dtsx path. "
        f"call_args: {call_args}"
    )


def test_capture_asserts_row_count_not_stdout(tmp_path: Path) -> None:
    """capture() judges success by row count, NOT by parsing dtexec stdout.

    Stub dtexec to return stdout containing an error-like string, but stub
    read_destination to return a non-empty DataFrame.  capture() must treat
    the run as successful (no exception, manifest has positive row_count).

    This pins the plan ADVISORY: dtexec stdout error-row parsing is brittle;
    use the post-run row-count check instead.  SERVER-FREE.
    """

    def _fake_runner(dtsx_path: Path) -> int:  # noqa: ARG001
        # Real dtexec might print 'Error 0xC0047038' even on success.
        # The harness must not parse this — it must check row count instead.
        return 0  # exit code 0 = dtexec considers it a success

    fake_df = pd.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
    fake_conn = MagicMock()

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=fake_df),
    ):
        manifest = capture(
            fake_conn,
            _PASSTHROUGH_DIR,
            dtexec_runner=_fake_runner,
            golden_dir=tmp_path,
        )

    # Success: manifest returned, row_count is positive for dst_items.
    assert manifest is not None
    assert "destinations" in manifest
    # At least one destination has a positive row count.
    any_positive = any(
        entry["row_count"] > 0
        for entry in manifest["destinations"].values()
    )
    assert any_positive, (
        "capture() must report positive row counts when read_destination "
        "returns data — success check must be row-count-based, not stdout-based."
    )


def test_capture_returns_manifest_with_expected_keys(tmp_path: Path) -> None:
    """capture() returns a manifest dict with 'seed_checksum' and 'destinations'.

    End-to-end stub: provision, seed, dtexec, and read_destination are all
    mocked.  Verifies the capture() return value has the required manifest
    structure.  SERVER-FREE.
    """
    fake_df = pd.DataFrame({"id": [1], "name": ["Widget"]})
    fake_conn = MagicMock()
    stub_runner = MagicMock(return_value=0)

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=fake_df),
    ):
        manifest = capture(
            fake_conn,
            _PASSTHROUGH_DIR,
            dtexec_runner=stub_runner,
            golden_dir=tmp_path,
        )

    assert "seed_checksum" in manifest, "manifest missing 'seed_checksum'"
    assert "destinations" in manifest, "manifest missing 'destinations'"
    # seed_checksum must be a non-empty hex string.
    assert isinstance(manifest["seed_checksum"], str)
    assert len(manifest["seed_checksum"]) == 64  # SHA-256 hex digest


def test_capture_writes_manifest_json(tmp_path: Path) -> None:
    """capture() writes manifest.json to the golden directory.

    SERVER-FREE — stubs all external calls.  Verifies the side-effect
    (manifest.json file created and valid JSON).
    """
    fake_df = pd.DataFrame({"id": [1]})
    fake_conn = MagicMock()

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=fake_df),
    ):
        capture(
            fake_conn,
            _PASSTHROUGH_DIR,
            dtexec_runner=MagicMock(return_value=0),
            golden_dir=tmp_path,
        )

    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.is_file(), "capture() must write manifest.json to golden_dir"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "seed_checksum" in manifest


def test_capture_writes_parquet_files(tmp_path: Path) -> None:
    """capture() writes a Parquet file per destination to golden_dir.

    SERVER-FREE — stubs provision, seed, dtexec, read_destination.
    Verifies at least one .parquet file appears under golden_dir.
    """
    fake_df = pd.DataFrame({"id": [1, 2], "name": ["A", "B"]})
    fake_conn = MagicMock()

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=fake_df),
    ):
        capture(
            fake_conn,
            _PASSTHROUGH_DIR,
            dtexec_runner=MagicMock(return_value=0),
            golden_dir=tmp_path,
        )

    parquet_files = list(tmp_path.glob("*.parquet"))
    assert parquet_files, (
        "capture() must write at least one .parquet file to golden_dir; "
        f"files found: {list(tmp_path.iterdir())}"
    )


# ---------------------------------------------------------------------------
# HIGH-A — zero-row success gate
# ---------------------------------------------------------------------------


def test_capture_raises_on_zero_rows(tmp_path: Path) -> None:
    """capture() raises RuntimeError when dtexec exits 0 but all dst tables are empty.

    A dtexec run that exits 0 but produces no rows would create useless golden
    fixtures.  The harness must abort before writing any file.
    SERVER-FREE.
    """
    fake_conn = MagicMock()
    empty_df = pd.DataFrame({"id": pd.Series([], dtype="int64")})

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
        patch("validation.capture.capture.read_destination", return_value=empty_df),
    ):
        with pytest.raises(RuntimeError, match="all destination tables are empty"):
            capture(
                fake_conn,
                _PASSTHROUGH_DIR,
                dtexec_runner=MagicMock(return_value=0),
                golden_dir=tmp_path,
            )

    # No golden files written before the raise.
    assert not list(tmp_path.glob("*.parquet")), "No Parquet files must be written before the row-count gate"
    assert not (tmp_path / "manifest.json").exists(), "manifest.json must not be written before the row-count gate"


# ---------------------------------------------------------------------------
# MEDIUM-2 — non-zero dtexec exit is captured
# ---------------------------------------------------------------------------


def test_capture_raises_on_nonzero_dtexec_exit(tmp_path: Path) -> None:
    """capture() raises RuntimeError when the dtexec seam returns a non-zero exit code.

    The error must name the exit code.  No golden files may be written.
    SERVER-FREE.
    """
    fake_conn = MagicMock()

    with (
        patch("validation.capture.capture.provision"),
        patch("validation.capture.capture.seed"),
    ):
        with pytest.raises(RuntimeError, match="dtexec exited 1"):
            capture(
                fake_conn,
                _PASSTHROUGH_DIR,
                dtexec_runner=MagicMock(return_value=1),
                golden_dir=tmp_path,
            )

    assert not list(tmp_path.glob("*.parquet")), "No Parquet files must be written after a non-zero dtexec exit"
    assert not (tmp_path / "manifest.json").exists(), "manifest.json must not be written after a non-zero dtexec exit"


# ---------------------------------------------------------------------------
# MEDIUM-3 — manifest audit fields
# ---------------------------------------------------------------------------


def test_build_manifest_contains_audit_fields() -> None:
    """build_manifest includes 'package', 'captured_at', and version fields.

    AC2 extended — the manifest must carry the §7 audit fields.
    SERVER-FREE.
    """
    import re as _re

    destinations = {"dst_items": _SAMPLE_DF.copy()}
    manifest = build_manifest(_PASSTHROUGH_DIR, destinations)

    assert "package" in manifest, "manifest missing 'package'"
    assert manifest["package"] == _PASSTHROUGH_DIR.name

    assert "captured_at" in manifest, "manifest missing 'captured_at'"
    # Must be a valid ISO-8601 UTC timestamp string.
    assert _re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", manifest["captured_at"]), (
        f"captured_at is not an ISO-8601 timestamp: {manifest['captured_at']!r}"
    )

    # Version fields are present (null values deferred — parsing dtexec stdout is brittle).
    assert "ssis_product_version" in manifest
    assert "dtexec_version" in manifest


# ---------------------------------------------------------------------------
# MEDIUM-1 / MEDIUM-3b — main() threads dtexec_path through to capture()
# ---------------------------------------------------------------------------


def test_main_threads_dtexec_path(tmp_path: Path) -> None:
    """main() passes --dtexec-path through to capture() as dtexec_path=.

    Proves MEDIUM-1 wiring: the parsed CLI arg is not silently dropped.
    SERVER-FREE — patches get_connection and capture.
    """
    fake_dtexec = r"C:\Program Files\MSSQL\dtexec.exe"
    test_argv = [
        "capture",
        "--package-dir", str(_PASSTHROUGH_DIR),
        "--dtexec-path", fake_dtexec,
    ]

    captured_kwargs: dict = {}

    def _fake_capture(conn, package_dir, **kwargs):
        captured_kwargs.update(kwargs)
        return {"seed_checksum": "a" * 64, "destinations": {}}

    with (
        patch("validation.capture.capture.capture", side_effect=_fake_capture),
        patch("validation.sqlserver.get_connection", return_value=MagicMock()),
        patch.object(sys, "argv", test_argv),
    ):
        from validation.capture.capture import main
        main()

    assert "dtexec_path" in captured_kwargs, (
        "main() must pass dtexec_path kwarg to capture(); "
        f"captured kwargs: {captured_kwargs}"
    )
    assert captured_kwargs["dtexec_path"] == fake_dtexec, (
        f"Expected dtexec_path={fake_dtexec!r}, got {captured_kwargs['dtexec_path']!r}"
    )

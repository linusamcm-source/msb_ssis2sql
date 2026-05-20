"""Golden-capture harness — Story 5.

A Windows operator runs this module (or the ``capture.ps1`` wrapper) after the
sprint merges.  The harness:

1. Provisions the remote SQL Server database from ``schema.sql``.
2. Seeds source tables from ``seed/*.csv``.
3. Executes the SSIS package via ``dtexec`` (Windows-only; injectable seam for
   macOS unit tests).
4. Reads back every ``dst_*`` destination table.
5. Asserts success via a post-run row-count check (not by parsing dtexec stdout,
   which is brittle): raises :exc:`RuntimeError` when all destination tables are
   empty (dtexec exited 0 but produced no rows).
6. Exports each destination table to ``golden/<dst>.parquet`` via pyarrow.
7. Writes ``golden/manifest.json`` with a seed checksum, row counts, column
   types, package name, and capture timestamp.

The ``dtexec`` call is factored behind a :data:`DtexecRunner` seam so the entire
harness is unit-testable on macOS without a real SSIS installation.

Manifest note: ``ssis_product_version`` and ``dtexec_version`` are set to
``null`` — capturing those values requires parsing dtexec stdout, which the plan
ADVISORY discourages as brittle.  They can be filled in manually after capture.

Typical operator invocation (Windows)::

    python -m validation.capture.capture --package-dir validation/corpus/passthrough_basic

Or via the PowerShell wrapper::

    .\\validation\\capture\\capture.ps1 -PackageDir validation\\corpus\\passthrough_basic
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyodbc  # noqa: F401 — runtime type annotation for conn parameter

from msb_ssis2sql.observability import logger
from validation.provisioning import provision, seed, seed_checksum

# read_destination imported as a name so tests can patch validation.capture.capture.read_destination
from validation.sql_runner import read_destination

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

#: Callable that receives the ``package.dtsx`` path and returns an int exit
#: code (0 = success).  Inject a stub in tests; the default is
#: :func:`_run_dtexec`.
DtexecRunner = Callable[[Path], int]

# ---------------------------------------------------------------------------
# Regex — same CREATE TABLE pattern as provisioning.py / sql_runner.py
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE: re.Pattern[str] = re.compile(
    r"CREATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dst_table_names(package_dir: Path) -> list[str]:
    """Return ``dst_*`` table names declared in ``package_dir/schema.sql``.

    Parses ``schema.sql`` with the shared ``CREATE TABLE`` regex.  Only names
    that begin with ``dst_`` are returned; ``src_*`` and ``ref_*`` are skipped.

    Parameters
    ----------
    package_dir:
        Corpus package directory containing ``schema.sql``.

    Returns
    -------
    list[str]
        Destination table names in declaration order.
    """
    schema_sql = (package_dir / "schema.sql").read_text(encoding="utf-8")
    return [
        name
        for name in _CREATE_TABLE_RE.findall(schema_sql)
        if name.startswith("dst_")
    ]


def _make_dtexec_runner(dtexec_path: str | None) -> DtexecRunner:
    """Return a :data:`DtexecRunner` that invokes the given ``dtexec`` executable.

    This is the real default runner — Windows-only and never called in tests
    (which inject a stub).  The body is intentionally minimal to keep the
    uncovered line count low.

    Parameters
    ----------
    dtexec_path:
        Path to ``dtexec.exe``.  When ``None``, the bare string ``"dtexec"``
        is used (relies on ``dtexec`` being on ``PATH``).

    Returns
    -------
    DtexecRunner
        A callable ``(dtsx_path: Path) -> int``.
    """
    exe = dtexec_path or "dtexec"

    def _runner(dtsx_path: Path) -> int:
        result = subprocess.run(
            [exe, "/FILE", str(dtsx_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "dtexec exited {} for {}\nstdout: {}",
                result.returncode,
                dtsx_path,
                result.stdout,
            )
        return result.returncode

    return _runner


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser.

    Required arguments
    ------------------
    ``--package-dir``:
        Path to the corpus package directory (e.g.
        ``validation/corpus/passthrough_basic``).

    Optional arguments
    ------------------
    ``--dtexec-path``:
        Path to the ``dtexec.exe`` binary.  Defaults to ``None``; when
        ``None`` the harness uses the system ``dtexec`` found on ``PATH``.

    Returns
    -------
    argparse.ArgumentParser
        Configured parser; call ``.parse_args()`` to obtain a namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Golden-capture harness: run an SSIS package via dtexec and export"
            " destination tables to golden/*.parquet + manifest.json."
        ),
    )
    parser.add_argument(
        "--package-dir",
        required=True,
        metavar="DIR",
        help="Path to the corpus package directory (contains package.dtsx, schema.sql, seed/).",
    )
    parser.add_argument(
        "--dtexec-path",
        default=None,
        metavar="PATH",
        help="Path to dtexec.exe. Defaults to 'dtexec' on PATH.",
    )
    return parser


def build_manifest(
    package_dir: Path,
    destinations: dict[str, pd.DataFrame],
    schema_types: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Build the golden-capture manifest dict.

    The manifest is fully JSON-serialisable (plain Python scalars only — no
    numpy scalars or pandas/numpy datetime objects).

    Parameters
    ----------
    package_dir:
        Corpus package directory; used to compute the seed checksum via
        :func:`validation.provisioning.seed_checksum` and to derive the
        package name (``package_dir.name``).
    destinations:
        Mapping of ``{dst_table_name: DataFrame}`` read back after the
        dtexec run.
    schema_types:
        Optional ``{dst_table_name: {col_name: sql_type_token}}`` mapping.
        When provided, ``column_types`` in each destination entry is populated
        from this mapping.  When ``None`` or the destination name is absent,
        ``column_types`` is an empty dict.

    Returns
    -------
    dict
        ``{"package": str, "captured_at": str, "ssis_product_version": None,
        "dtexec_version": None, "seed_checksum": str, "destinations":
        {name: {"row_count": int, "column_types": dict[str, str]}}}``
    """
    checksum = seed_checksum(package_dir)
    dest_entries: dict[str, dict] = {}
    for dst_name, df in destinations.items():
        col_types: dict[str, str] = {}
        if schema_types is not None:
            col_types = dict(schema_types.get(dst_name, {}))
        dest_entries[dst_name] = {
            "row_count": int(len(df)),
            "column_types": col_types,
        }
    return {
        "package": package_dir.name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        # Version fields deferred: capturing them requires parsing dtexec stdout,
        # which the plan ADVISORY discourages as brittle.  Fill in manually.
        "ssis_product_version": None,
        "dtexec_version": None,
        "seed_checksum": checksum,
        "destinations": dest_entries,
    }


def export_parquet(df: pd.DataFrame, path: Path) -> None:
    """Write *df* to *path* as a Parquet file using pyarrow.

    The file can be read back with ``pandas.read_parquet`` and yield a
    DataFrame equal to the original (same rows, same columns, dtypes preserved
    for numeric and nullable types).

    Parameters
    ----------
    df:
        DataFrame to export.
    path:
        Destination file path (parent directory must exist).
    """
    df.to_parquet(path, engine="pyarrow", index=False)


def capture(
    conn: pyodbc.Connection,
    package_dir: Path,
    *,
    dtexec_runner: DtexecRunner | None = None,
    dtexec_path: str | None = None,
    golden_dir: Path | None = None,
) -> dict:
    """Provision, seed, run dtexec, export golden fixtures, and return the manifest.

    Steps:

    1. :func:`provision` — apply ``schema.sql`` DDL.
    2. :func:`seed` — load ``seed/src_*.csv`` into source tables.
    3. Call *dtexec_runner* (or the default runner built from *dtexec_path*) with
       ``package_dir / "package.dtsx"``.
    4. Read back each ``dst_*`` table via :func:`read_destination`.
    5. Assert success by row-count check: raises :exc:`RuntimeError` when all
       destination tables are empty (dtexec exited 0 but wrote no rows).  No
       file is written before this check passes.
    6. Export each destination DataFrame to ``golden_dir/<dst>.parquet`` via
       :func:`export_parquet`.
    7. Write ``golden_dir/manifest.json`` (serialised :func:`build_manifest`
       output).
    8. Return the manifest dict.

    Parameters
    ----------
    conn:
        Active ``pyodbc.Connection`` to the target database.
    package_dir:
        Path to the corpus package directory.
    dtexec_runner:
        Injectable dtexec seam.  Receives the ``package.dtsx`` path, returns
        an int exit code.  When ``None``, a real runner is built from
        *dtexec_path*.
    dtexec_path:
        Path to ``dtexec.exe``.  Used only when *dtexec_runner* is ``None``.
        When both are ``None`` the bare ``"dtexec"`` string is used (relies on
        ``PATH``).
    golden_dir:
        Directory where Parquet files and ``manifest.json`` are written.
        Defaults to ``package_dir / "golden"`` when ``None``.

    Returns
    -------
    dict
        The manifest dict (same structure as :func:`build_manifest`'s return
        value).

    Raises
    ------
    RuntimeError
        When dtexec returns a non-zero exit code, or when dtexec exits 0 but
        all destination tables contain zero rows.
    """
    runner = dtexec_runner if dtexec_runner is not None else _make_dtexec_runner(dtexec_path)
    golden = golden_dir if golden_dir is not None else package_dir / "golden"
    golden.mkdir(parents=True, exist_ok=True)

    provision(conn, package_dir)
    seed(conn, package_dir)
    # No truncate_destinations: provision() above drops+recreates every table,
    # so dst_* tables already start empty.

    dtsx_path = package_dir / "package.dtsx"
    logger.info("capture: invoking dtexec for {}", dtsx_path)
    exit_code = runner(dtsx_path)
    if exit_code != 0:
        raise RuntimeError(
            f"dtexec exited {exit_code} for {dtsx_path} — capture aborted."
        )

    dst_names = _dst_table_names(package_dir)
    data: dict[str, pd.DataFrame] = {}
    for table in dst_names:
        df = read_destination(conn, table, schema_types=None)
        data[table] = df
        logger.info("capture: read {} rows from {}", len(df), table)

    # Success gate: row-count check (NOT stdout parsing).
    # A dtexec exit code of 0 with zero rows means the package ran but wrote
    # nothing — a silent failure that would produce useless golden fixtures.
    total_rows = sum(len(df) for df in data.values())
    if total_rows == 0:
        raise RuntimeError(
            f"capture: dtexec exited 0 but all destination tables are empty"
            f" for {package_dir} — capture aborted (no golden fixtures written)."
        )
    logger.info("capture: total rows across all destinations = {}", total_rows)

    for dst_name, df in data.items():
        parquet_path = golden / f"{dst_name}.parquet"
        export_parquet(df, parquet_path)
        logger.info("capture: wrote {}", parquet_path)

    manifest = build_manifest(package_dir, data)
    manifest_path = golden / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("capture: wrote {}", manifest_path)

    return manifest


def main() -> None:
    """CLI entry point — parse args and run the capture.

    Reads connection parameters from the environment / ``.env`` file via
    :func:`validation.sqlserver.get_connection` and invokes :func:`capture`.
    """
    parser = build_parser()
    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    dtexec_path: str | None = args.dtexec_path

    from validation.sqlserver import get_connection  # local import; server not needed on macOS

    conn = get_connection()
    capture(conn, package_dir, dtexec_path=dtexec_path)


if __name__ == "__main__":
    main()

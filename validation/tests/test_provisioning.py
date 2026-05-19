"""Tests for ``validation.provisioning`` — RED phase.

``validation/provisioning.py`` does not exist yet; every test in this module
will fail with ``ModuleNotFoundError`` until the engineer's Story 2
implementation lands.  That is the correct TDD RED state.

Contract under test (sprint plan, Story 2):

provision(conn, package_dir)
    Parse ``schema.sql``, split on ``GO`` batch separators (GO is a client
    directive that pyodbc rejects inside a batch), and execute each statement.
    Idempotent — a second call produces the same state without error.

seed(conn, package_dir)
    For each ``seed/src_*.csv``, ``TRUNCATE`` the target table then
    bulk-insert via parameterised ``executemany`` in column order per
    ``schema.sql``.  NULL convention:

    - **String columns** (nvarchar, varchar, …): empty CSV field → ``''``
      (empty string); the sentinel ``\\N`` → SQL NULL.  This lets a genuine
      empty string be stored while still representing NULL.
    - **Non-string columns** (int, decimal, datetime2, bit, …): empty CSV
      field → SQL NULL.

truncate_destinations(conn, package_dir)
    ``TRUNCATE`` (or ``DELETE FROM`` on FK constraint error) every ``dst_*``
    table found in ``schema.sql``.

seed_checksum(package_dir) -> str
    SHA-256 over the sorted, concatenated bytes of all ``seed/*.csv`` files.
    Stable across runs; changes when any seed CSV changes.

ENGINEER NOTE — TRUNCATE vs FK constraint
    TRUNCATE fails when the table is referenced by a FOREIGN KEY.  The tiny_pkg
    fixture is FK-free (kept simple deliberately), but real corpus packages may
    not be.  ``truncate_destinations`` should catch the constraint error
    (``pyodbc.DatabaseError`` / SQL Server error 4712) and fall back to
    ``DELETE FROM <table>`` so the corpus does not need redesign.

Test groups
-----------
GROUP A — Non-live (no SQL Server required; must always run and pass):

  * ``seed_checksum`` returns a non-empty hex string (SHA-256 is 64 chars).
  * ``seed_checksum`` is stable — two calls on the same directory return the
    same value.
  * ``seed_checksum`` changes when a seed CSV byte changes (write a temp dir,
    mutate a CSV, assert the digest differs).

GROUP B — Live (use the ``fresh_db`` fixture; skip cleanly if server unreachable):

  * After ``provision`` + ``seed``, ``src_widgets`` contains exactly the four
    seed rows with the correct column count.
  * The NULL vs empty-string distinction is preserved (three cases):
    - row 2 empty ``description`` (NVARCHAR, string) → ``''`` (empty string,
      NOT NULL);
    - row 3 ``description = \\N`` (NVARCHAR, string) → SQL NULL;
    - row 3 empty ``quantity`` (INT, non-string) → SQL NULL.
  * ``truncate_destinations`` leaves ``dst_widgets`` empty (row count = 0).
  * ``provision`` is idempotent — running it twice on the same connection
    raises no error and leaves the schema in a consistent state.

Fixture contract (already implemented in validation/conftest.py):
- ``fresh_db`` — function-scoped; yields a live ``pyodbc.Connection`` to a
  freshly created ``val_test_<uuid>`` database; skips if server unreachable.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

# This import raises ModuleNotFoundError until provisioning.py exists.
# That is the expected RED state — do not wrap in try/except.
from validation.provisioning import (
    provision,
    seed,
    seed_checksum,
    truncate_destinations,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TINY_PKG: Path = Path(__file__).parent / "fixtures" / "tiny_pkg"


# ---------------------------------------------------------------------------
# GROUP A — Non-live tests (no SQL Server required)
# ---------------------------------------------------------------------------


def test_seed_checksum_returns_hex_string() -> None:
    """``seed_checksum`` returns a non-empty hex string.

    A SHA-256 digest is exactly 64 hex characters; any shorter or non-hex
    result indicates the wrong algorithm or encoding.
    """
    digest = seed_checksum(_TINY_PKG)
    assert isinstance(digest, str)
    assert len(digest) == 64
    # Must be a valid hex string (no non-hex chars).
    int(digest, 16)


def test_seed_checksum_is_stable() -> None:
    """Two ``seed_checksum`` calls on the same directory return the same value."""
    assert seed_checksum(_TINY_PKG) == seed_checksum(_TINY_PKG)


def test_seed_checksum_changes_when_csv_bytes_change(tmp_path: Path) -> None:
    """Mutating a seed CSV changes the checksum.

    Copies the tiny_pkg fixture to a temp directory, records the digest,
    appends one byte to a seed CSV, and asserts the digest differs.
    """
    pkg_copy = tmp_path / "tiny_pkg"
    shutil.copytree(_TINY_PKG, pkg_copy)

    original = seed_checksum(pkg_copy)

    # Append a single byte to the seed CSV — any change must alter the digest.
    csv_file = pkg_copy / "seed" / "src_widgets.csv"
    csv_file.write_bytes(csv_file.read_bytes() + b"\n")

    mutated = seed_checksum(pkg_copy)
    assert original != mutated, "checksum must differ after a seed CSV byte changes"


def test_seed_checksum_includes_all_seed_files(tmp_path: Path) -> None:
    """Adding a second seed CSV changes the checksum.

    Verifies that ``seed_checksum`` folds all ``seed/*.csv`` files into the
    digest — not just the first one alphabetically.
    """
    pkg_copy = tmp_path / "tiny_pkg_extra"
    shutil.copytree(_TINY_PKG, pkg_copy)

    original = seed_checksum(pkg_copy)

    # Write a second seed file — this must shift the combined digest.
    extra = pkg_copy / "seed" / "src_extras.csv"
    extra.write_text("id\n99\n", encoding="utf-8")

    extended = seed_checksum(pkg_copy)
    assert original != extended, "checksum must change when a new seed file is added"


# ---------------------------------------------------------------------------
# GROUP B — Live tests (fresh isolated DB per test; skip if server unreachable)
# ---------------------------------------------------------------------------


def test_provision_creates_tables(fresh_db: object) -> None:
    """After ``provision``, ``src_widgets`` and ``dst_widgets`` exist in the DB.

    Verifies that the GO-split is working and both CREATE TABLE statements
    ran without error.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_type = 'BASE TABLE'"
        " ORDER BY table_name"
    )
    tables = {row[0] for row in cursor.fetchall()}
    assert "src_widgets" in tables
    assert "dst_widgets" in tables


def test_seed_loads_correct_row_count(fresh_db: object) -> None:
    """After ``provision`` + ``seed``, ``src_widgets`` has exactly 4 rows."""
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.src_widgets")
    assert cursor.fetchone()[0] == 4


def test_seed_empty_string_column_field_is_empty_string(fresh_db: object) -> None:
    """Row 2 has an empty ``description`` field (NVARCHAR) — must load as ``''``.

    CSV NULL convention for string columns: an empty field means the empty
    string ``''``, NOT SQL NULL.  Only the ``\\N`` sentinel means SQL NULL.
    Row 2 (id=2) has ``description`` as an empty CSV field.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT description FROM dbo.src_widgets WHERE id = 2")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "", (
        "empty CSV field for a string column must load as '' (empty string), not NULL"
    )


def test_seed_sentinel_in_string_column_is_null(fresh_db: object) -> None:
    r"""Row 3 uses the ``\N`` sentinel for ``description`` (NVARCHAR) — must be NULL.

    CSV NULL convention for string columns: the literal ``\N`` is the sentinel
    for SQL NULL, keeping it distinct from the empty string ``''``.
    Row 3 (id=3) has ``description = '\N'``.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT description FROM dbo.src_widgets WHERE id = 3")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] is None, r"the \N sentinel in a string column must load as SQL NULL"


def test_seed_empty_nonstring_column_field_is_null(fresh_db: object) -> None:
    """Row 3 has an empty ``quantity`` field (INT) — must load as SQL NULL.

    CSV NULL convention for non-string columns: an empty field means SQL NULL
    (there is no ambiguity because a non-string column cannot hold ``''``).
    Row 3 (id=3) has ``quantity`` as an empty CSV field.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT quantity FROM dbo.src_widgets WHERE id = 3")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] is None, "empty CSV field for a non-string nullable column must load as NULL"


def test_seed_preserves_nonempty_string(fresh_db: object) -> None:
    """Row 4 has a non-NULL, non-empty ``description`` — must load as a string.

    Row 4 (id=4) has ``description = 'Has spaces '`` (trailing space).  This
    must arrive as a non-NULL string value — neither NULL nor empty string.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT description FROM dbo.src_widgets WHERE id = 4")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] is not None, "non-empty description must not load as NULL"
    assert isinstance(row[0], str) and len(row[0]) > 0


def test_truncate_destinations_empties_dst_table(fresh_db: object) -> None:
    """``truncate_destinations`` leaves ``dst_widgets`` with zero rows.

    Seeds ``dst_widgets`` manually so there is something to truncate, then
    calls ``truncate_destinations`` and asserts the table is empty.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)

    # Insert a sentinel row into dst_widgets directly.
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO dbo.dst_widgets VALUES (99, N'Sentinel', NULL, NULL,"
        " 1.0000, '2026-01-01', 1)"
    )
    conn.commit()

    # Confirm the row landed.
    cursor.execute("SELECT COUNT(*) FROM dbo.dst_widgets")
    assert cursor.fetchone()[0] == 1

    truncate_destinations(conn, _TINY_PKG)

    cursor.execute("SELECT COUNT(*) FROM dbo.dst_widgets")
    assert cursor.fetchone()[0] == 0, "dst_widgets must be empty after truncate_destinations"


def test_truncate_destinations_does_not_touch_src_table(fresh_db: object) -> None:
    """``truncate_destinations`` must not truncate ``src_*`` tables."""
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    seed(conn, _TINY_PKG)

    truncate_destinations(conn, _TINY_PKG)

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM dbo.src_widgets")
    assert cursor.fetchone()[0] == 4, "truncate_destinations must not touch src_* tables"


def test_provision_is_idempotent(fresh_db: object) -> None:
    """Running ``provision`` twice raises no error and leaves the schema intact.

    The schema already exists after the first call; the second call must not
    raise (e.g. "table already exists").  The expected implementation drops
    existing tables before recreating them (since ``schema.sql`` uses bare
    ``CREATE TABLE``), making the second call a clean drop-and-recreate.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    provision(conn, _TINY_PKG)
    # A second call must not raise.
    provision(conn, _TINY_PKG)

    # Schema must still be consistent after the double provision.
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables"
        " WHERE table_name IN ('src_widgets', 'dst_widgets')"
    )
    assert cursor.fetchone()[0] == 2

"""Tests for ``validation.sqlserver`` — RED phase.

``validation/sqlserver.py`` does not exist yet; every test in this module
will fail with ``ModuleNotFoundError`` until the engineer's Story 1
implementation lands.  That is the correct TDD RED state.

Contract under test (sprint plan, Story 1):
- ``SqlServerUnavailable(Exception)``: raised by ``get_connection`` when the
  env is unconfigured or the server is unreachable.  The message contains
  ``"validation SQL Server not configured or unreachable"``.
  ``validation/sqlserver.py`` does NOT import pytest and does NOT call
  ``pytest.skip`` — it is a framework module used by non-pytest callers
  (``capture.py`` on Windows, ``test_validation.py``).  The skip lives in
  the test fixtures only.
- ``get_connection() -> pyodbc.Connection``: load config, build the ODBC
  connection string, connect.  Raises ``SqlServerUnavailable`` (not
  ``pytest.skip``) when the ``MSSQL_*`` vars are missing or the server is
  unreachable (``pyodbc.OperationalError`` / ``pyodbc.InterfaceError``).
- ``fresh_database(name, config) -> pyodbc.Connection``: drop and recreate a
  database named *name* in ``master`` (``autocommit=True``), then return a
  connection scoped to that database.  Uses ``ALTER DATABASE … SET
  SINGLE_USER WITH ROLLBACK IMMEDIATE`` before ``DROP`` if sessions are open.
  Function-scoped isolation — each call starts clean.
- No untrusted input is interpolated into DDL strings.

Test groups
-----------
GROUP A — Non-live (no real server needed; must run and pass in every CI
environment including one with no network access to SQL Server):

  * Unset MSSQL_* env vars → ``get_connection`` raises ``SqlServerUnavailable``.
  * ``pyodbc.connect`` raising ``pyodbc.OperationalError`` → raises
    ``SqlServerUnavailable``, not a bare pyodbc error.
  * ``pyodbc.connect`` raising ``pyodbc.InterfaceError`` (driver absent) →
    raises ``SqlServerUnavailable``.
  * The ``SqlServerUnavailable`` message contains the expected diagnostic text.
  * The connection string built internally contains all required ODBC parts:
    ``DRIVER=``, ``SERVER=<addr>,<port>``, ``UID=``, ``PWD=``,
    ``Encrypt=yes``, ``TrustServerCertificate=yes``.

GROUP B — Live (require a reachable SQL Server; guarded by
``sqlserver_connection`` fixture from ``validation/conftest.py`` which
catches ``SqlServerUnavailable`` and calls ``pytest.skip``):

  * A real connection executes ``SELECT 1`` and returns 1.
  * ``fresh_database("val_demo")`` yields a usable, empty database; a second
    call with the same name starts clean (idempotent drop-create).

Fixture contract (engineer must match these names in validation/conftest.py):
- ``sqlserver_connection`` — session-scoped; catches ``SqlServerUnavailable``
  and calls ``pytest.skip``; yields a live ``pyodbc.Connection`` otherwise.
- ``fresh_db`` — function-scoped; catches ``SqlServerUnavailable`` and calls
  ``pytest.skip``; yields a ``pyodbc.Connection`` scoped to a freshly created
  ``val_test_<uuid>`` database; cleaned up after the test.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# This import raises ModuleNotFoundError until sqlserver.py exists.
# That is the expected RED state — do not wrap in try/except.
from validation.sqlserver import (
    SqlServerUnavailable,
    drop_database,
    fresh_database,
    get_connection,
)

from validation.config import ValidationConfig, get_connection_string, load_config


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_MSSQL_VARS: dict[str, str] = {
    "MSSQL_SERVER_ADDRESS": "db.example.com",
    "MSSQL_SERVER_PORT": "1433",
    "MSSQL_SA_USERNAME": "sa",
    "MSSQL_SA_PASSWORD": "S3cr3t!",
}


def _set_mssql_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject all four MSSQL_* env vars."""
    for key, value in _MSSQL_VARS.items():
        monkeypatch.setenv(key, value)


def _unset_mssql_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all four MSSQL_* env vars."""
    for key in _MSSQL_VARS:
        monkeypatch.delenv(key, raising=False)


def _fake_config() -> ValidationConfig:
    """Return a ``ValidationConfig`` built from the test values above."""
    return ValidationConfig(
        server_address="db.example.com",
        server_port="1433",
        sa_username="sa",
        sa_password="S3cr3t!",
    )


# ---------------------------------------------------------------------------
# GROUP A — Non-live tests (no SQL Server required)
# ---------------------------------------------------------------------------


def test_get_connection_raises_when_mssql_vars_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset MSSQL_* env vars cause ``get_connection`` to raise ``SqlServerUnavailable``.

    The framework module must not call ``pytest.skip`` — it is reused by
    non-pytest callers (``capture.py`` on Windows).  The fixture layer is
    responsible for catching ``SqlServerUnavailable`` and skipping.
    """
    _unset_mssql_vars(monkeypatch)
    with pytest.raises(
        SqlServerUnavailable,
        match="validation SQL Server not configured or unreachable",
    ):
        get_connection()


def test_get_connection_raises_when_pyodbc_raises_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable server (``pyodbc.OperationalError``) raises ``SqlServerUnavailable``.

    Monkeypatches ``pyodbc.connect`` so no real network call is made.
    The framework module wraps the pyodbc error; callers decide whether to
    skip or propagate.
    """
    import pyodbc  # noqa: PLC0415

    _set_mssql_vars(monkeypatch)
    with patch("pyodbc.connect", side_effect=pyodbc.OperationalError("server down")):
        with pytest.raises(
            SqlServerUnavailable,
            match="validation SQL Server not configured or unreachable",
        ):
            get_connection()


def test_get_connection_raises_when_pyodbc_raises_interface_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver-not-found ``pyodbc.InterfaceError`` raises ``SqlServerUnavailable``.

    Covers environments where ODBC Driver 18 is not installed — the layer
    wraps the error so non-pytest callers (e.g. ``capture.py``) receive a
    typed exception they can handle rather than a raw pyodbc error.
    """
    import pyodbc  # noqa: PLC0415

    _set_mssql_vars(monkeypatch)
    with patch("pyodbc.connect", side_effect=pyodbc.InterfaceError("driver not found")):
        with pytest.raises(
            SqlServerUnavailable,
            match="validation SQL Server not configured or unreachable",
        ):
            get_connection()


def test_connection_string_parts_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ODBC driver clause appears in the connection string.

    Calls ``get_connection_string`` from ``validation.config`` directly (no
    network) to verify the string the ``sqlserver`` layer would pass to
    ``pyodbc.connect``.
    """
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in connstr


def test_connection_string_parts_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``SERVER=<addr>,<port>`` clause appears in the connection string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "SERVER=db.example.com,1433" in connstr


def test_connection_string_parts_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``UID=`` clause appears in the connection string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "UID=sa" in connstr


def test_connection_string_parts_pwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``PWD=`` clause appears in the connection string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "PWD=S3cr3t!" in connstr


def test_connection_string_parts_encrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``Encrypt=yes`` clause appears in the connection string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "Encrypt=yes" in connstr


def test_connection_string_parts_trust_server_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``TrustServerCertificate=yes`` clause appears in the connection string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "TrustServerCertificate=yes" in connstr


def test_get_connection_exception_message_mentions_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SqlServerUnavailable`` carries the expected diagnostic text.

    The fixture layer and non-pytest callers (e.g. ``capture.py``) both
    inspect this message to distinguish "server not configured" from other
    failure modes — so the exact substring must be present.
    """
    import pyodbc  # noqa: PLC0415

    _set_mssql_vars(monkeypatch)
    with patch(
        "pyodbc.connect",
        side_effect=pyodbc.OperationalError("connection timed out"),
    ):
        with pytest.raises(SqlServerUnavailable) as exc_info:
            get_connection()
    assert "validation SQL Server not configured or unreachable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# GROUP B — Live tests (skip automatically when server is unreachable)
#
# These tests depend on the ``sqlserver_connection`` and ``fresh_db`` fixtures
# declared in ``validation/conftest.py`` by the engineer.  Both fixtures catch
# ``SqlServerUnavailable`` and call ``pytest.skip`` so that a server-down run
# is GREEN (all skips), not RED (all errors).
# ---------------------------------------------------------------------------


def test_live_select_one(sqlserver_connection: object) -> None:
    """A real connection executes ``SELECT 1`` and returns the integer 1.

    Fixture: ``sqlserver_connection`` — session-scoped live pyodbc.Connection.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = sqlserver_connection  # type: ignore[assignment]
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == 1


def test_fresh_database_is_usable(fresh_db: object) -> None:
    """``fresh_database`` returns a connection to an empty, usable database.

    Fixture: ``fresh_db`` — function-scoped; yields a live pyodbc.Connection
    scoped to a freshly created ``val_test_<uuid>`` database.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    cursor = conn.cursor()
    # The database must be reachable — a simple SELECT succeeds.
    cursor.execute("SELECT DB_NAME()")
    row = cursor.fetchone()
    assert row is not None
    db_name: str = row[0]
    assert db_name.startswith("val_test_")


def test_fresh_database_is_empty(fresh_db: object) -> None:
    """The database produced by ``fresh_database`` contains no user tables.

    Validates that the drop-recreate cycle leaves a genuinely clean slate —
    not a database carrying over tables from a previous test run.
    """
    import pyodbc  # noqa: PLC0415

    conn: pyodbc.Connection = fresh_db  # type: ignore[assignment]
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
    )
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == 0, "fresh database must contain no user tables"


def test_fresh_database_second_call_starts_clean(
    sqlserver_connection: object,
) -> None:
    """A second ``fresh_database`` call with the same name produces a clean DB.

    Creates a table in the first instance, then drops and recreates the DB,
    and asserts the table is gone — proving the drop-create cycle ran.

    Fixture: ``sqlserver_connection`` — used directly here so the test
    controls the database name rather than relying on the ``fresh_db``
    fixture's auto-generated name.
    """
    import pyodbc  # noqa: PLC0415

    db_name = f"val_test_idempotent_{uuid.uuid4().hex[:8]}"
    config = load_config()

    try:
        # First call — create the DB, add a table.
        conn1: pyodbc.Connection = fresh_database(db_name, config)
        try:
            cursor1 = conn1.cursor()
            cursor1.execute("CREATE TABLE dbo.canary (id INT)")
            conn1.commit()
            cursor1.execute(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_name = 'canary'"
            )
            assert cursor1.fetchone()[0] == 1
        finally:
            conn1.close()

        # Second call — same name; DB is dropped and recreated; canary must be gone.
        conn2: pyodbc.Connection = fresh_database(db_name, config)
        try:
            cursor2 = conn2.cursor()
            cursor2.execute(
                "SELECT COUNT(*) FROM information_schema.tables"
                " WHERE table_name = 'canary'"
            )
            assert cursor2.fetchone()[0] == 0, (
                "second fresh_database call must drop the previous database"
            )
        finally:
            conn2.close()
    finally:
        # Always drop the test database — never leave an orphan on the server.
        drop_database(db_name, config)


# ---------------------------------------------------------------------------
# HIGH-B regression — fresh_db skips (not errors) when MSSQL_* is unset
# ---------------------------------------------------------------------------


def test_fresh_db_skips_when_mssql_vars_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``fresh_db`` fixture skips rather than errors when MSSQL_* is unset.

    HIGH-B regression: ``load_config()`` raises ``RuntimeError`` when the env
    vars are missing.  The ``fresh_db`` fixture must catch that and call
    ``pytest.skip`` — not let the ``RuntimeError`` escape as a test ERROR.

    This test simulates the fixture path directly: it unsets the MSSQL_* vars,
    then calls ``load_config()`` and asserts it raises ``RuntimeError`` (the
    condition that ``fresh_db`` now catches and converts to ``pytest.skip``).
    The actual skip behaviour of the fixture is verified by the manual
    ``mv .env .env.bak`` step in the delivery verification.
    """
    _unset_mssql_vars(monkeypatch)
    # load_config() must raise RuntimeError — that is the exact exception
    # fresh_db now catches and converts to pytest.skip.
    with pytest.raises(RuntimeError, match="validation SQL Server not configured"):
        load_config()


# ---------------------------------------------------------------------------
# M2 unit test — drop_database is idempotent (non-existent DB is a no-op)
# ---------------------------------------------------------------------------


def test_drop_database_idempotent_on_nonexistent(
    sqlserver_connection: object,
) -> None:
    """``drop_database`` on a non-existent database is a no-op (no exception).

    M2 contract: ``DROP DATABASE IF EXISTS`` means calling ``drop_database``
    when the named database does not exist must succeed silently.  Uses a
    ``val_test_`` name that is guaranteed not to exist.
    """
    config = load_config()
    nonexistent = f"val_test_nonexistent_{uuid.uuid4().hex[:8]}"
    # Must not raise.
    drop_database(nonexistent, config)

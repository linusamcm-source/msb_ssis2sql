"""SQL Server connection factory and per-test fresh-database helpers.

Public API
----------
SqlServerUnavailable
    Raised when the SQL Server is unreachable or the MSSQL_* env vars are
    unset.  Callers (fixtures, ``capture.py``) catch this and decide whether
    to skip, log, or abort — this module never calls ``pytest.skip``.
get_connection() -> pyodbc.Connection
    Load config, build the ODBC connection string, and return a live
    connection.  Raises ``SqlServerUnavailable`` on any configuration or
    connectivity failure.
drop_database(name, config) -> None
    Drop the named database if it exists, evicting open sessions first.
fresh_database(name, config) -> pyodbc.Connection
    Drop-and-recreate the named database (must match ``^val_[A-Za-z0-9_]+$``)
    on the remote SQL Server, then return a connection scoped to it.

System prerequisite: Microsoft ODBC Driver 18 for SQL Server.
  macOS: brew install msodbcsql18  (Microsoft tap) + unixodbc.
"""
from __future__ import annotations

import re

import pyodbc

from validation.config import ValidationConfig, get_connection_string, load_config

# Only database names matching this pattern are allowed in DDL — defence in
# depth against accidental interpolation of untrusted input.
_DB_NAME_RE: re.Pattern[str] = re.compile(r"^val_[A-Za-z0-9_]+$")


class SqlServerUnavailable(Exception):
    """The remote SQL Server is unreachable or not configured.

    Raised by ``get_connection``, ``drop_database``, and ``fresh_database``
    when:
    - the ``MSSQL_*`` environment variables are unset / empty, or
    - ``pyodbc.connect`` raises ``OperationalError`` (server down / timeout), or
    - ``pyodbc.connect`` raises ``InterfaceError`` (ODBC driver absent), or
    - ``pyodbc.connect`` raises ``pyodbc.Error`` (driver shared library absent).

    The message always contains
    ``"validation SQL Server not configured or unreachable"`` so that both
    fixture layers and non-pytest callers (e.g. ``capture.py``) can
    distinguish this condition from other exceptions.
    """


def _validate_db_name(name: str) -> None:
    """Raise ``ValueError`` if *name* does not match the allowlist pattern."""
    if not _DB_NAME_RE.match(name):
        raise ValueError(
            f"Database name {name!r} is not allowed — must match "
            r"^val_[A-Za-z0-9_]+$ to prevent DDL injection."
        )


def _master_conn(config: ValidationConfig) -> pyodbc.Connection:
    """Return a ``master``-scoped connection with ``autocommit=True``.

    DDL statements (``CREATE DATABASE``, ``DROP DATABASE``) cannot execute
    inside a transaction, so ``autocommit`` must be enabled.

    Raises
    ------
    SqlServerUnavailable
        If the server cannot be reached.
    """
    master_connstr = get_connection_string(config) + "DATABASE=master;"
    try:
        return pyodbc.connect(master_connstr, autocommit=True, timeout=10)
    except pyodbc.Error as exc:
        raise SqlServerUnavailable(
            "validation SQL Server not configured or unreachable — "
            f"could not connect to master: {exc}"
        ) from exc


def get_connection() -> pyodbc.Connection:
    """Return a live ``pyodbc.Connection`` to the remote SQL Server.

    Reads connection parameters from the environment (via ``load_config``),
    builds the ODBC connection string, and calls ``pyodbc.connect``.

    Raises
    ------
    SqlServerUnavailable
        If ``MSSQL_*`` env vars are missing/empty, or if ``pyodbc.connect``
        raises ``OperationalError``, ``InterfaceError``, or ``pyodbc.Error``
        (covers the "ODBC driver shared library not loaded" case).
    """
    try:
        config = load_config()
    except RuntimeError as exc:
        raise SqlServerUnavailable(
            "validation SQL Server not configured or unreachable — "
            f"missing MSSQL_* environment variables: {exc}"
        ) from exc

    connstr = get_connection_string(config)

    try:
        return pyodbc.connect(connstr, timeout=10)
    except pyodbc.Error as exc:
        # Catches OperationalError (server down/timeout), InterfaceError (driver
        # absent), and the pyodbc.Error base class emitted when the ODBC driver
        # shared library itself cannot be loaded ("Can't open lib …").
        raise SqlServerUnavailable(
            "validation SQL Server not configured or unreachable — "
            f"pyodbc.connect failed: {exc}"
        ) from exc


def drop_database(name: str, config: ValidationConfig) -> None:
    """Drop *name* if it exists, evicting open sessions first.

    Safe to call when the database does not exist — the ``DROP DATABASE IF
    EXISTS`` is a no-op in that case.

    The database name must match ``^val_[A-Za-z0-9_]+$`` to prevent DDL
    injection; a ``ValueError`` is raised otherwise.

    Parameters
    ----------
    name:
        Database name.  Must start with ``val_`` and contain only
        alphanumeric characters and underscores.
    config:
        A ``ValidationConfig`` from ``load_config()``.

    Raises
    ------
    ValueError
        If *name* does not match ``^val_[A-Za-z0-9_]+$``.
    SqlServerUnavailable
        If the server is unreachable.
    """
    _validate_db_name(name)
    quoted = f"[{name}]"

    conn = _master_conn(config)
    try:
        cursor = conn.cursor()
        # Evict any open sessions so the DROP cannot be blocked.
        try:
            cursor.execute(
                f"ALTER DATABASE {quoted} SET SINGLE_USER WITH ROLLBACK IMMEDIATE"
            )
        except pyodbc.ProgrammingError:
            # Database does not exist yet — nothing to evict.
            pass
        cursor.execute(f"DROP DATABASE IF EXISTS {quoted}")
    finally:
        conn.close()


def fresh_database(name: str, config: ValidationConfig) -> pyodbc.Connection:
    """Drop-and-recreate *name*, then return a connection scoped to it.

    Delegates the drop step to ``drop_database`` to keep the implementation
    DRY.  The connection to ``master`` uses ``autocommit=True`` because
    ``CREATE DATABASE`` cannot run inside a transaction.

    Parameters
    ----------
    name:
        Database name.  Must start with ``val_`` and contain only
        alphanumeric characters and underscores.
    config:
        A ``ValidationConfig`` from ``load_config()``.

    Returns
    -------
    pyodbc.Connection
        A fresh connection scoped to the newly created database.

    Raises
    ------
    ValueError
        If *name* does not match ``^val_[A-Za-z0-9_]+$``.
    SqlServerUnavailable
        If the server is unreachable.
    """
    # Validation and drop delegated to drop_database (single implementation).
    drop_database(name, config)

    # Create the fresh database.
    quoted = f"[{name}]"
    conn = _master_conn(config)
    try:
        conn.cursor().execute(f"CREATE DATABASE {quoted}")
    finally:
        conn.close()

    # Return a connection scoped to the new database.
    db_connstr = get_connection_string(config) + f"DATABASE={name};"
    try:
        return pyodbc.connect(db_connstr, timeout=10)
    except pyodbc.Error as exc:
        raise SqlServerUnavailable(
            "validation SQL Server not configured or unreachable — "
            f"could not connect to fresh database {name!r}: {exc}"
        ) from exc

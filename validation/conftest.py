"""Top-level pytest fixtures for the ``validation/`` package.

These fixtures are available to all test files under ``validation/`` —
both the unit tests in ``validation/tests/`` and the integration tests
at the ``validation/`` level (``test_validation.py``, ``test_static.py``).

Fixtures
--------
sqlserver_connection
    Session-scoped live ``pyodbc.Connection`` to the remote SQL Server.
    Skips (``pytest.skip``) the entire session's server-dependent tests when
    the server is unreachable or the ``MSSQL_*`` env vars are unset.
fresh_db
    Function-scoped ``pyodbc.Connection`` scoped to a freshly created
    ``val_test_<uuid>`` database.  The database is dropped on teardown so
    the remote server is not littered with test artefacts.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from ssis2sql.observability import logger
from validation.config import load_config
from validation.sqlserver import (
    SqlServerUnavailable,
    drop_database,
    fresh_database,
    get_connection,
)

if TYPE_CHECKING:
    import pyodbc


@pytest.fixture(scope="session")
def sqlserver_connection() -> Iterator[pyodbc.Connection]:
    """Yield a live ``pyodbc.Connection``; skip if the server is unreachable.

    Session-scoped — the connection is opened once and shared across all
    tests in the session that request it.  The caller must not close it; the
    fixture closes it on teardown.

    Skips when the server is unreachable (``SqlServerUnavailable``) or when
    the ``MSSQL_*`` env vars are not configured (``RuntimeError`` from
    ``load_config``).
    """
    try:
        conn = get_connection()
    except (SqlServerUnavailable, RuntimeError) as exc:
        pytest.skip(str(exc))

    yield conn
    conn.close()


@pytest.fixture(scope="function")
def fresh_db() -> Iterator[pyodbc.Connection]:
    """Yield a connection to a fresh ``val_test_<uuid>`` database.

    Function-scoped — each test receives an isolated, empty database.
    The database is dropped in teardown regardless of test outcome so the
    remote SQL Server is not littered with leftover databases.

    Skips (``pytest.skip``) if the server is unreachable (``SqlServerUnavailable``)
    or the ``MSSQL_*`` env vars are not configured (``RuntimeError`` from
    ``load_config``).
    """
    db_name = f"val_test_{uuid4().hex}"

    try:
        config = load_config()
        conn = fresh_database(db_name, config)
    except (SqlServerUnavailable, RuntimeError) as exc:
        pytest.skip(str(exc))

    yield conn

    # Teardown — close the scoped connection then drop the database.  Errors
    # here must not mask a test failure, but they are logged so a leaked
    # database leaves a visible trace in the output.
    try:
        conn.close()
    except Exception as exc:
        logger.warning("fresh_db teardown: could not close connection: {}", exc)

    try:
        config = load_config()
        drop_database(db_name, config)
    except Exception as exc:
        logger.warning(
            "fresh_db teardown: could not drop database {!r}: {}", db_name, exc
        )

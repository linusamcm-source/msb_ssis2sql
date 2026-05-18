"""ODBC connection configuration, corpus paths, and default tolerances.

Public API
----------
ValidationConfig
    Frozen dataclass holding all connection parameters and framework defaults.
load_config() -> ValidationConfig
    Reads ``MSSQL_*`` env vars (after loading ``.env`` via python-dotenv) and
    returns a populated ``ValidationConfig``.  Raises ``RuntimeError`` if any
    of the four required variables is unset or empty, with a message that
    contains ``"validation SQL Server not configured"``.
get_connection_string(config) -> str
    Builds the pyodbc-compatible ODBC connection string from a config.

This module intentionally does **not** import ``pyodbc``, so it can be
imported in any environment where the ODBC system driver is absent (e.g. the
unit-test runner).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import dotenv

# ---------------------------------------------------------------------------
# The four env-var names that must be present.
# ---------------------------------------------------------------------------

_REQUIRED_VARS: tuple[str, ...] = (
    "MSSQL_SERVER_ADDRESS",
    "MSSQL_SERVER_PORT",
    "MSSQL_SA_USERNAME",
    "MSSQL_SA_PASSWORD",
)

# Root of the validation corpus directory, relative to this file.
_CORPUS_ROOT: Path = Path(__file__).parent / "corpus"


@dataclass(frozen=True)
class ValidationConfig:
    """Immutable configuration for a validation framework run.

    All four connection parameters are required; they carry no insecure
    defaults so that a misconfigured environment fails loudly rather than
    silently connecting to the wrong server.

    Attributes
    ----------
    server_address:
        Hostname or IP of the remote SQL Server instance.
    server_port:
        TCP port as a string (default ``"1433"``).
    sa_username:
        SQL Server login name (typically ``"sa"`` for a dev instance).
    sa_password:
        SQL Server password.  Never hard-coded — must come from the environment.
    odbc_driver:
        ODBC driver name as it appears in the system DSN list.
    corpus_root:
        ``pathlib.Path`` to ``validation/corpus/``.
    float_epsilon:
        Absolute tolerance for ``float`` column comparisons.
    datetime_tolerance:
        Tolerance in seconds for ``datetime`` column comparisons.
    trust_server_certificate:
        Whether to pass ``TrustServerCertificate=yes`` in the connection
        string.  Required for dev instances with self-signed certs.
    """

    server_address: str
    server_port: str
    sa_username: str
    sa_password: str = field(repr=False)
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    corpus_root: Path = _CORPUS_ROOT
    float_epsilon: float = 1e-6
    datetime_tolerance: float = 1.0  # seconds
    trust_server_certificate: bool = True


def load_config() -> ValidationConfig:
    """Load connection configuration from environment variables.

    Calls ``dotenv.load_dotenv()`` so a gitignored ``.env`` at the repo root
    is picked up automatically.  The four ``MSSQL_*`` variables must be
    present and non-empty after that load; if any is missing the function
    raises ``RuntimeError`` with a message containing
    ``"validation SQL Server not configured"``.

    Returns
    -------
    ValidationConfig
        A fully populated, frozen config object.

    Raises
    ------
    RuntimeError
        When any of ``MSSQL_SERVER_ADDRESS``, ``MSSQL_SERVER_PORT``,
        ``MSSQL_SA_USERNAME``, or ``MSSQL_SA_PASSWORD`` is unset or empty.
    """
    dotenv.load_dotenv()

    missing = [var for var in _REQUIRED_VARS if not os.environ.get(var)]
    if missing:
        raise RuntimeError(
            f"validation SQL Server not configured — "
            f"missing or empty env var(s): {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in the values."
        )

    return ValidationConfig(
        server_address=os.environ["MSSQL_SERVER_ADDRESS"],
        server_port=os.environ["MSSQL_SERVER_PORT"],
        sa_username=os.environ["MSSQL_SA_USERNAME"],
        sa_password=os.environ["MSSQL_SA_PASSWORD"],
    )


def get_connection_string(config: ValidationConfig) -> str:
    """Build an ODBC connection string from *config*.

    The resulting string is suitable for passing directly to
    ``pyodbc.connect()``.  Encryption is always enabled; certificate trust
    follows ``config.trust_server_certificate`` (``yes`` / ``no``).

    Parameters
    ----------
    config:
        A ``ValidationConfig`` instance produced by ``load_config()``.

    Returns
    -------
    str
        A semicolon-delimited ODBC connection string.
    """
    trust = "yes" if config.trust_server_certificate else "no"
    return (
        f"DRIVER={{{config.odbc_driver}}};"
        f"SERVER={config.server_address},{config.server_port};"
        f"UID={config.sa_username};"
        f"PWD={config.sa_password};"
        f"Encrypt=yes;"
        f"TrustServerCertificate={trust};"
    )

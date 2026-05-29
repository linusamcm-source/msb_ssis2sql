"""ODBC connection configuration, corpus paths, and default tolerances.

Public API
----------
ValidationConfig
    Frozen dataclass holding all connection parameters and framework defaults.
load_config() -> ValidationConfig
    Reads ``MSSQL_*`` env vars (after loading ``.env`` via python-dotenv) and
    returns a populated ``ValidationConfig``.  Raises ``RuntimeError`` if any
    of the required variables is unset or empty, with a message that
    contains ``"validation SQL Server not configured"``.
get_connection_string(config) -> str
    Builds the pyodbc-compatible ODBC connection string from a config.

Authentication is Windows-only (Trusted_Connection=yes). SA username and
password env vars are no longer consulted. The connection is always
encrypted (Encrypt=yes) and server certificates are always trusted
(TrustServerCertificate=yes) so dev SQL Server instances with self-signed
certs work out of the box.

This module intentionally does **not** import ``pyodbc``, so it can be
imported in any environment where the ODBC system driver is absent (e.g. the
unit-test runner).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import dotenv

# ---------------------------------------------------------------------------
# Env-var names that must be present.
# ---------------------------------------------------------------------------

_REQUIRED_VARS: tuple[str, ...] = (
    "MSSQL_SERVER_ADDRESS",
    "MSSQL_SERVER_PORT",
)

# Root of the validation corpus directory, relative to this file.
_CORPUS_ROOT: Path = Path(__file__).parent / "corpus"


@dataclass(frozen=True)
class ValidationConfig:
    """Immutable configuration for a validation framework run.

    Connection parameters are required; they carry no insecure defaults so
    that a misconfigured environment fails loudly rather than silently
    connecting to the wrong server.

    Attributes
    ----------
    server_address:
        Hostname or IP of the remote SQL Server instance.
    server_port:
        TCP port as a string (default ``"1433"``).
    odbc_driver:
        ODBC driver name as it appears in the system DSN list.
    corpus_root:
        ``pathlib.Path`` to ``validation/corpus/``.
    float_epsilon:
        Absolute tolerance for ``float`` column comparisons.
    datetime_tolerance:
        Tolerance in seconds for ``datetime`` column comparisons.
    trust_server_certificate:
        Always ``True``. Retained for backward compatibility of the public
        API; the connection-string builder hard-codes
        ``TrustServerCertificate=yes`` regardless of this value.
    """

    server_address: str
    server_port: str
    odbc_driver: str = "ODBC Driver 18 for SQL Server"
    corpus_root: Path = _CORPUS_ROOT
    float_epsilon: float = 1e-6
    datetime_tolerance: float = 1.0  # seconds
    trust_server_certificate: bool = True


def load_config() -> ValidationConfig:
    """Load connection configuration from environment variables.

    Calls ``dotenv.load_dotenv()`` so a gitignored ``.env`` at the repo root
    is picked up automatically.  The two ``MSSQL_*`` variables
    (``MSSQL_SERVER_ADDRESS``, ``MSSQL_SERVER_PORT``) must be present and
    non-empty after that load; if either is missing the function raises
    ``RuntimeError`` with a message containing
    ``"validation SQL Server not configured"``.

    Returns
    -------
    ValidationConfig
        A fully populated, frozen config object.

    Raises
    ------
    RuntimeError
        When either ``MSSQL_SERVER_ADDRESS`` or ``MSSQL_SERVER_PORT`` is
        unset or empty.
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
    )


def get_connection_string(config: ValidationConfig) -> str:
    """Build an ODBC connection string from *config*.

    The resulting string is suitable for passing directly to
    ``pyodbc.connect()``. Authentication is Windows (``Trusted_Connection=yes``);
    encryption and server-certificate trust are always enabled.

    Parameters
    ----------
    config:
        A ``ValidationConfig`` instance produced by ``load_config()``.

    Returns
    -------
    str
        A semicolon-delimited ODBC connection string.
    """
    return (
        f"DRIVER={{{config.odbc_driver}}};"
        f"SERVER={config.server_address},{config.server_port};"
        f"Trusted_Connection=yes;"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
    )

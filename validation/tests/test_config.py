"""Tests for ``validation.config``.

Contract under test:
- A frozen dataclass holding ODBC connection config.
- Two connection parameters read from env vars: ``MSSQL_SERVER_ADDRESS``,
  ``MSSQL_SERVER_PORT``. Authentication is Windows-only (no SA username
  or password env vars are consulted).
- A fixed ODBC driver name (``ODBC Driver 18 for SQL Server``).
- A corpus root path, default float epsilon, default datetime tolerance,
  and a ``TrustServerCertificate`` flag (always ``True``; retained for
  backward-compatibility of the public dataclass surface).
- Obtaining the connection config when the ``MSSQL_*`` vars are unset
  raises a descriptive error containing ``"validation SQL Server not configured"``.
- A helper that builds the pyodbc connection string with
  ``Trusted_Connection=yes``, ``Encrypt=yes``, and
  ``TrustServerCertificate=yes`` clauses.
"""
from __future__ import annotations

import dataclasses

import pytest

# The import will raise ModuleNotFoundError until config.py exists.
# That is the expected RED state — do not add a try/except here.
from validation.config import get_connection_string, load_config


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_REQUIRED_VARS: dict[str, str] = {
    "MSSQL_SERVER_ADDRESS": "db.example.com",
    "MSSQL_SERVER_PORT": "1433",
}


def _set_mssql_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the MSSQL_* env vars via monkeypatch."""
    for key, value in _REQUIRED_VARS.items():
        monkeypatch.setenv(key, value)


def _unset_mssql_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the MSSQL_* env vars via monkeypatch."""
    for key in _REQUIRED_VARS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# 1. Config loads MSSQL_* vars from the environment
# ---------------------------------------------------------------------------


def test_load_config_reads_server_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.server_address`` reflects ``MSSQL_SERVER_ADDRESS``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert config.server_address == "db.example.com"


def test_load_config_reads_server_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.server_port`` reflects ``MSSQL_SERVER_PORT``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert config.server_port == "1433"


# ---------------------------------------------------------------------------
# 2. Connection-string helper produces expected parts
# ---------------------------------------------------------------------------


def test_connection_string_contains_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection string includes the ODBC Driver 18 driver clause."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in connstr


def test_connection_string_contains_server_address_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection string includes ``SERVER=<address>,<port>``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "SERVER=db.example.com,1433" in connstr


def test_connection_string_contains_trusted_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection string includes ``Trusted_Connection=yes`` (Windows auth)."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "Trusted_Connection=yes" in connstr


def test_connection_string_omits_uid_and_pwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows-auth connection string carries no UID/PWD clauses."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "UID=" not in connstr
    assert "PWD=" not in connstr


def test_connection_string_contains_encrypt_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection string includes ``Encrypt=yes``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "Encrypt=yes" in connstr


def test_connection_string_contains_trust_server_certificate_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection string includes ``TrustServerCertificate=yes``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    connstr = get_connection_string(config)
    assert "TrustServerCertificate=yes" in connstr


# ---------------------------------------------------------------------------
# 3. Missing MSSQL_* vars raise a clear error
# ---------------------------------------------------------------------------


def test_load_config_raises_when_server_address_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ``MSSQL_SERVER_ADDRESS`` raises an error with the expected message."""
    _set_mssql_vars(monkeypatch)
    monkeypatch.delenv("MSSQL_SERVER_ADDRESS")
    with pytest.raises(Exception, match="validation SQL Server not configured"):
        load_config()


def test_load_config_raises_when_all_mssql_vars_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All four vars unset raises an error with the expected message."""
    _unset_mssql_vars(monkeypatch)
    with pytest.raises(Exception, match="validation SQL Server not configured"):
        load_config()


# ---------------------------------------------------------------------------
# 4. Config exposes driver name, corpus root, epsilon, tolerance, trust-cert flag
# ---------------------------------------------------------------------------


def test_config_exposes_odbc_driver_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.odbc_driver`` equals the expected ODBC Driver 18 string."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert config.odbc_driver == "ODBC Driver 18 for SQL Server"


def test_config_exposes_corpus_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.corpus_root`` is a non-empty path string or Path object."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    # corpus_root must be truthy — a real path, not empty/None
    assert config.corpus_root


def test_config_exposes_float_epsilon(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.float_epsilon`` is a positive float."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert isinstance(config.float_epsilon, float)
    assert config.float_epsilon > 0


def test_config_exposes_datetime_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ValidationConfig.datetime_tolerance`` is a positive numeric value."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert config.datetime_tolerance > 0


def test_config_exposes_trust_server_certificate_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ValidationConfig.trust_server_certificate`` is a bool."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert isinstance(config.trust_server_certificate, bool)


# ---------------------------------------------------------------------------
# 5. The dataclass is frozen — attribute assignment raises FrozenInstanceError
# ---------------------------------------------------------------------------


def test_config_is_frozen_server_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assigning to ``server_address`` raises ``FrozenInstanceError``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.server_address = "other.host"  # type: ignore[misc]


def test_config_is_frozen_float_epsilon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assigning to ``float_epsilon`` raises ``FrozenInstanceError``."""
    _set_mssql_vars(monkeypatch)
    config = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.float_epsilon = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. Config has no SA credential surface (Windows auth)
# ---------------------------------------------------------------------------


def test_config_has_no_sa_credential_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ValidationConfig`` carries no ``sa_username`` / ``sa_password`` fields.

    Authentication is Windows-only — credentials are never stored on
    the config object, so they cannot leak through ``repr`` or pickling.
    """
    _set_mssql_vars(monkeypatch)
    config = load_config()
    assert not hasattr(config, "sa_username")
    assert not hasattr(config, "sa_password")

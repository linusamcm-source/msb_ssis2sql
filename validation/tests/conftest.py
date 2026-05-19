"""Pytest configuration for ``validation/tests/`` unit tests.

Strategy
--------
``dotenv.load_dotenv()`` is called inside ``validation.config.load_config()``.
If left unpatched during unit tests, it re-reads the developer's real ``.env``
file and silently repopulates any env var that a test deliberately removed via
``monkeypatch.delenv`` — causing "missing var" error-path tests to never raise.

Fix: neutralise ``load_dotenv`` globally for this directory, and instead
pre-load the real ``.env`` values into ``os.environ`` once at session start
(via ``_seed_env_from_dotenv``).  This means:

- Every test that calls ``monkeypatch.setenv`` / ``monkeypatch.delenv`` controls
  the env safely — monkeypatch restores after each test, and ``load_dotenv``
  cannot undo a ``delenv`` mid-test.
- Live tests (Group B in ``test_sqlserver.py``) find the MSSQL_* vars already
  in ``os.environ`` from the session-scoped seed and can reach the server.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import dotenv
import pytest

# ---------------------------------------------------------------------------
# Session-scoped seed: load .env into os.environ once, permanently.
# ---------------------------------------------------------------------------

_ENV_FILE: Path = Path(__file__).parents[2] / ".env"


@pytest.fixture(scope="session", autouse=True)
def _seed_env_from_dotenv() -> None:  # noqa: PT004
    """Load the repo-root ``.env`` into ``os.environ`` once per session.

    Uses ``override=False`` so CI-injected environment variables win over the
    developer ``.env``.  After this fixture runs, all MSSQL_* vars are present
    in ``os.environ`` for the whole session — ``load_dotenv`` no longer needs
    to re-read the file on each ``load_config()`` call, so it can be safely
    patched to a no-op for the rest of the session.
    """
    if _ENV_FILE.exists():
        dotenv.load_dotenv(dotenv_path=_ENV_FILE, override=False)


# ---------------------------------------------------------------------------
# Per-test patch: suppress load_dotenv so monkeypatch.delenv is respected.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PT004
    """Prevent ``dotenv.load_dotenv`` from re-reading ``.env`` during tests.

    Applied automatically to every test in this directory.  Because
    ``_seed_env_from_dotenv`` has already populated ``os.environ`` once at
    session start, suppressing ``load_dotenv`` here does not prevent live
    tests from finding their MSSQL_* vars — they are already in the
    environment.  It *does* prevent ``load_config()`` from undoing a
    ``monkeypatch.delenv`` mid-test.
    """
    with patch("dotenv.load_dotenv", return_value=False):
        yield

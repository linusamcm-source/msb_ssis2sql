"""Pytest configuration for ``validation/tests/`` unit tests.

This conftest neutralises ``dotenv.load_dotenv()`` for the entire unit-test
session.  The unit tests control the environment via ``monkeypatch.setenv``
and ``monkeypatch.delenv``; if ``load_dotenv`` were allowed to run it would
re-read the developer's real ``.env`` file and silently repopulate variables
that a test has deliberately removed — causing the "missing var" tests to
never raise.

Keeping ``load_dotenv`` as a no-op here does **not** affect the actual
runtime behaviour of ``validation.config`` when used with a real ``.env``
file; it only scopes the side-effect away from unit tests that own their own
environment through monkeypatch.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PT004
    """Prevent ``dotenv.load_dotenv`` from reading the real ``.env`` file.

    Applied automatically to every test in this directory.
    """
    with patch("dotenv.load_dotenv", return_value=False):
        yield

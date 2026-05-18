"""Root pytest configuration.

Living at the repository root, this file guarantees the root is on ``sys.path``
so ``import ssis2sql`` resolves without an editable install. It also exposes
fixtures shared across the test modules.
"""
from __future__ import annotations

import pathlib

import pytest

EXAMPLE_DTSX = pathlib.Path(__file__).parent / "examples" / "sales_etl.dtsx"


@pytest.fixture
def example_path() -> str:
    """Filesystem path to the bundled example package."""
    return str(EXAMPLE_DTSX)


@pytest.fixture
def example_package():
    """The bundled example package, parsed."""
    from ssis2sql.parser import parse_file

    return parse_file(str(EXAMPLE_DTSX))

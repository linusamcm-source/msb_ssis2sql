"""Component transpilers.

Importing this package has the side effect of registering every transpiler
(each module applies the :func:`~ssis2sql.transforms.registry.register`
decorator at import time). The generator only needs :func:`get_transpiler`.
"""
from __future__ import annotations

from .base import sanitise_identifier
from .context import BuildContext, Sink
from .registry import Transpiler, get_transpiler, register

# Import order is irrelevant - each module self-registers. Listed so the
# registry is fully populated the moment this package is imported.
from . import source as _source            # noqa: F401
from . import column_ops as _column_ops    # noqa: F401
from . import conditional_split as _split  # noqa: F401
from . import lookup as _lookup            # noqa: F401
from . import grouping as _grouping        # noqa: F401
from . import set_ops as _set_ops          # noqa: F401
from . import flow as _flow                # noqa: F401
from . import destination as _destination  # noqa: F401

__all__ = [
    "BuildContext",
    "Sink",
    "Transpiler",
    "get_transpiler",
    "register",
    "sanitise_identifier",
]

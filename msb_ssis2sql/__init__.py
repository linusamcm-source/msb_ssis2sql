"""msb_ssis2sql - convert SSIS data-flow transformations into consolidated T-SQL.

The framework has four stages:

    parser      .dtsx XML            -> msb_ssis2sql.model intermediate representation
    graph       components + paths   -> a directed acyclic data-flow graph
    transforms  one transpiler/kind  -> a relational fragment (a CTE) per component
    generator   topological assembly -> a single consolidated T-SQL statement per sink

Public entry points are re-exported here for convenience.
"""
from __future__ import annotations

from .errors import (
    Ssis2SqlError,
    ParseError,
    ExpressionError,
    GraphError,
)
from .generator import ConvertOptions, ConversionResult, convert_file, convert_package
from .observability import configure_logging, logged

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ConvertOptions",
    "ConversionResult",
    "convert_file",
    "convert_package",
    "configure_logging",
    "logged",
    "Ssis2SqlError",
    "ParseError",
    "ExpressionError",
    "GraphError",
]

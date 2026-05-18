"""Exception hierarchy for ssis2sql."""
from __future__ import annotations


class Ssis2SqlError(Exception):
    """Base class for every error raised by the framework."""


class ParseError(Ssis2SqlError):
    """A .dtsx document could not be parsed into the intermediate representation."""


class ExpressionError(Ssis2SqlError):
    """An SSIS expression could not be tokenised, parsed, or translated."""


class UnsupportedComponentError(Ssis2SqlError):
    """A pipeline component has no registered transpiler."""


class GraphError(Ssis2SqlError):
    """The data-flow graph is malformed - a cycle, a dangling path, or an orphan."""

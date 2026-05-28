"""Exception hierarchy for msb_ssis2sql."""
from __future__ import annotations


class Ssis2SqlError(Exception):
    """Base class for every error raised by the framework."""


class ParseError(Ssis2SqlError):
    """A .dtsx document could not be parsed into the intermediate representation."""


class ExpressionError(Ssis2SqlError):
    """An SSIS expression could not be tokenised, parsed, or translated."""


class GraphError(Ssis2SqlError):
    """The data-flow graph is malformed - a cycle, a dangling path, or an orphan."""


class AgentExtractError(Ssis2SqlError):
    """The SQL Server Agent job extraction failed: connection, permission, or query."""

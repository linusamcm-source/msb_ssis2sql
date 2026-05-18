"""Abstract syntax tree node types for the SSIS expression language."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Node:
    """Base class for every expression node."""


@dataclass
class Literal(Node):
    """A literal value. ``kind`` is one of: str, int, real, bool, null."""

    value: object
    kind: str


@dataclass
class ColumnRef(Node):
    """A reference to a pipeline column - ``[Name]`` or a bare identifier."""

    name: str


@dataclass
class VariableRef(Node):
    """A reference to a package variable - ``@[User::Threshold]``."""

    namespace: str
    name: str


@dataclass
class Unary(Node):
    """A unary operation: ``-`` ``+`` ``!`` ``~``."""

    op: str
    operand: Node


@dataclass
class Binary(Node):
    """A binary operation: arithmetic, comparison, logical, or bitwise."""

    op: str
    left: Node
    right: Node


@dataclass
class Conditional(Node):
    """The ternary ``cond ? when_true : when_false``."""

    cond: Node
    when_true: Node
    when_false: Node


@dataclass
class FunctionCall(Node):
    """A function call: ``UPPER([x])``, ``DATEADD("d", 1, GETDATE())``."""

    name: str
    args: list


@dataclass
class Cast(Node):
    """An SSIS cast: ``(DT_STR,50,1252)[x]``."""

    type_code: str
    type_args: list
    operand: Node


@dataclass
class TypedNull(Node):
    """A typed null literal: ``NULL(DT_WSTR, 50)``."""

    type_code: str
    type_args: list

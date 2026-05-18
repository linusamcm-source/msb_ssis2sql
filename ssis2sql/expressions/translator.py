"""Translate an SSIS expression AST into T-SQL.

The hard part is *context*. SSIS expressions are typed; T-SQL splits the world
into values and predicates and will not let a predicate sit where a value is
expected (or vice versa).

* ``translate``      -> the expression as a **value** (Derived Column output).
* ``translate_bool`` -> the expression as a **predicate** (Conditional Split,
  WHERE clause).

A comparison used as a value is wrapped ``CASE WHEN ... THEN 1 ELSE 0 END``.
A bare value used as a predicate is compared ``... = 1``. This mirrors how SSIS
coerces between its boolean and DT_BOOL/integer worlds.
"""
from __future__ import annotations

from . import ast
from ..errors import ExpressionError
from ..sqltypes import tsql_type

# SSIS datepart strings -> T-SQL datepart keywords.
_DATEPART = {
    "yy": "year", "yyyy": "year",
    "q": "quarter", "qq": "quarter",
    "m": "month", "mm": "month",
    "d": "day", "dd": "day",
    "y": "dayofyear", "dy": "dayofyear",
    "w": "weekday", "dw": "weekday",
    "wk": "week", "ww": "week",
    "h": "hour", "hh": "hour",
    "mi": "minute", "n": "minute",
    "s": "second", "ss": "second",
    "ms": "millisecond",
}

_COMPARISON = {"==": "=", "!=": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">="}
_ARITH = {"+", "-", "*", "/", "%"}
_BITWISE = {"&", "|", "^"}

# SSIS functions whose T-SQL spelling and argument order are identical.
_PASSTHROUGH = {
    "ABS", "CEILING", "FLOOR", "SIGN", "SQRT", "SQUARE", "EXP", "POWER", "ROUND",
    "UPPER", "LOWER", "LTRIM", "RTRIM", "REPLACE", "SUBSTRING", "REVERSE",
    "REPLICATE", "LEN", "YEAR", "MONTH", "DAY", "GETDATE", "GETUTCDATE",
    "LEFT", "RIGHT", "ISNUMERIC",
}


def default_column_resolver(name: str) -> str:
    """Render a column reference; overridden by joins that need table aliases."""
    return f"[{name}]"


def sql_string_literal(value: str) -> str:
    """Render a Python string as a T-SQL Unicode literal.

    Control characters cannot live inside a literal, so they are spliced out
    as ``NCHAR(n)`` concatenations - ``"a\\nb"`` -> ``(N'a' + NCHAR(10) + N'b')``.
    """
    parts: list[str] = []
    buf: list[str] = []
    for ch in value:
        if ord(ch) < 32:
            if buf:
                parts.append("N'" + "".join(buf).replace("'", "''") + "'")
                buf = []
            parts.append(f"NCHAR({ord(ch)})")
        else:
            buf.append(ch)
    if buf or not parts:
        parts.append("N'" + "".join(buf).replace("'", "''") + "'")
    return parts[0] if len(parts) == 1 else "(" + " + ".join(parts) + ")"


class Translator:
    """Walk an expression AST and emit T-SQL.

    ``column_resolver(name) -> sql`` and ``variable_resolver(ns, name) -> sql``
    let a caller (a join transpiler, say) control how identifiers are rendered.
    Unsupported constructs are appended to :attr:`warnings` rather than raised,
    so one odd expression does not abort a whole package.
    """

    def __init__(self, column_resolver=None, variable_resolver=None):
        self._resolve_column = column_resolver or default_column_resolver
        self._resolve_variable = variable_resolver
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def translate(self, node: ast.Node) -> str:
        """Emit ``node`` in value context."""
        return self._value(node)

    def translate_bool(self, node: ast.Node) -> str:
        """Emit ``node`` in predicate context."""
        return self._bool(node)

    # ------------------------------------------------------------------ #
    # context dispatch
    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_predicate(node: ast.Node) -> bool:
        """True when the node naturally yields a boolean."""
        if isinstance(node, ast.Binary):
            return node.op in _COMPARISON or node.op in ("&&", "||")
        if isinstance(node, ast.Unary):
            return node.op == "!"
        if isinstance(node, ast.Literal):
            return node.kind == "bool"
        if isinstance(node, ast.FunctionCall):
            return node.name.upper() == "ISNULL"
        return False

    def _value(self, node: ast.Node) -> str:
        """Emit any node as a scalar value."""
        if self._is_predicate(node):
            return f"CASE WHEN {self._bool(node)} THEN 1 ELSE 0 END"
        return self._raw_value(node)

    def _bool(self, node: ast.Node) -> str:
        """Emit any node as a boolean predicate."""
        if isinstance(node, ast.Binary):
            if node.op in _COMPARISON:
                return f"{self._value(node.left)} {_COMPARISON[node.op]} {self._value(node.right)}"
            if node.op == "&&":
                return f"({self._bool(node.left)} AND {self._bool(node.right)})"
            if node.op == "||":
                return f"({self._bool(node.left)} OR {self._bool(node.right)})"
        if isinstance(node, ast.Unary) and node.op == "!":
            return f"NOT ({self._bool(node.operand)})"
        if isinstance(node, ast.Literal) and node.kind == "bool":
            return "(1 = 1)" if node.value else "(1 = 0)"
        if isinstance(node, ast.FunctionCall) and node.name.upper() == "ISNULL":
            if len(node.args) != 1:
                raise ExpressionError("SSIS ISNULL() takes exactly one argument")
            return f"{self._value(node.args[0])} IS NULL"
        # A value in predicate position: SSIS coerces non-zero to true.
        return f"{self._raw_value(node)} <> 0"

    # ------------------------------------------------------------------ #
    # value emission
    # ------------------------------------------------------------------ #
    def _raw_value(self, node: ast.Node) -> str:
        if isinstance(node, ast.Literal):
            return self._literal(node)
        if isinstance(node, ast.ColumnRef):
            return self._resolve_column(node.name)
        if isinstance(node, ast.VariableRef):
            if self._resolve_variable:
                return self._resolve_variable(node.namespace, node.name)
            return f"@{node.name}"
        if isinstance(node, ast.TypedNull):
            return f"CAST(NULL AS {tsql_type(node.type_code, node.type_args)})"
        if isinstance(node, ast.Cast):
            return f"CAST({self._value(node.operand)} AS {tsql_type(node.type_code, node.type_args)})"
        if isinstance(node, ast.Unary):
            return self._unary(node)
        if isinstance(node, ast.Binary):
            return self._binary(node)
        if isinstance(node, ast.Conditional):
            return (
                f"CASE WHEN {self._bool(node.cond)} "
                f"THEN {self._value(node.when_true)} "
                f"ELSE {self._value(node.when_false)} END"
            )
        if isinstance(node, ast.FunctionCall):
            return self._function(node)
        raise ExpressionError(f"cannot translate node of type {type(node).__name__}")

    @staticmethod
    def _literal(node: ast.Literal) -> str:
        if node.kind == "str":
            return sql_string_literal(node.value)
        if node.kind == "bool":
            return "1" if node.value else "0"
        if node.kind == "null":
            return "NULL"
        return str(node.value)               # int / real - lexed text is fine

    def _unary(self, node: ast.Unary) -> str:
        if node.op in ("-", "+"):
            return f"({node.op}{self._value(node.operand)})"
        if node.op == "~":
            return f"(~ {self._value(node.operand)})"
        if node.op == "!":                   # value context: 1/0 result
            return f"CASE WHEN {self._bool(node)} THEN 1 ELSE 0 END"
        raise ExpressionError(f"unknown unary operator {node.op!r}")

    def _binary(self, node: ast.Binary) -> str:
        if node.op in _ARITH or node.op in _BITWISE:
            return f"({self._value(node.left)} {node.op} {self._value(node.right)})"
        # comparison / logical reaching here is value context -> 1/0 result
        return f"CASE WHEN {self._bool(node)} THEN 1 ELSE 0 END"

    # ------------------------------------------------------------------ #
    # function translation
    # ------------------------------------------------------------------ #
    def _function(self, node: ast.FunctionCall) -> str:
        name = node.name.upper()
        handler = getattr(self, f"_fn_{name.lower()}", None)
        if handler is not None:
            return handler(node.args)
        if name in _PASSTHROUGH:
            return f"{name}({self._args(node.args)})"
        self.warnings.append(
            f"SSIS function {node.name}() has no T-SQL mapping - emitted verbatim, review required"
        )
        return f"/* unmapped */ {node.name}({self._args(node.args)})"

    def _args(self, args: list) -> str:
        return ", ".join(self._value(a) for a in args)

    def _one_arg(self, args: list, name: str) -> str:
        """Emit a single-argument function call, rejecting any other arity."""
        if len(args) != 1:
            raise ExpressionError(f"SSIS {name}() takes exactly one argument")
        return self._value(args[0])

    def _datepart(self, node: ast.Node) -> str:
        """Resolve an SSIS datepart string argument to a T-SQL datepart keyword."""
        if isinstance(node, ast.Literal) and node.kind == "str":
            key = node.value.strip().lower()
            if key in _DATEPART:
                return _DATEPART[key]
            self.warnings.append(f"unknown datepart {node.value!r} - emitted verbatim")
            return key
        self.warnings.append("non-literal datepart argument - review the generated DATEPART/DATEADD")
        return self._value(node)

    # -- null handling -------------------------------------------------- #
    def _fn_isnull(self, args: list) -> str:
        # value context (predicate context is handled directly in _bool)
        if len(args) != 1:
            raise ExpressionError("SSIS ISNULL() takes exactly one argument")
        return f"CASE WHEN {self._value(args[0])} IS NULL THEN 1 ELSE 0 END"

    def _fn_replacenull(self, args: list) -> str:
        if len(args) != 2:
            raise ExpressionError("REPLACENULL() takes exactly two arguments")
        return f"COALESCE({self._value(args[0])}, {self._value(args[1])})"

    # -- string functions ---------------------------------------------- #
    def _fn_trim(self, args: list) -> str:
        return f"LTRIM(RTRIM({self._one_arg(args, 'TRIM')}))"

    def _fn_codepoint(self, args: list) -> str:
        return f"UNICODE({self._one_arg(args, 'CODEPOINT')})"

    def _fn_findstring(self, args: list) -> str:
        # SSIS: FINDSTRING(character_expression, searchstring, occurrence)
        if len(args) != 3:
            raise ExpressionError("FINDSTRING() takes exactly three arguments")
        haystack, needle, occurrence = args
        is_first = isinstance(occurrence, ast.Literal) and str(occurrence.value).strip() in ("1", "1.0")
        if not is_first:
            self.warnings.append(
                "FINDSTRING() occurrence > 1 has no direct T-SQL form - emitted as first-occurrence CHARINDEX"
            )
        return f"CHARINDEX({self._value(needle)}, {self._value(haystack)})"

    # -- numeric functions --------------------------------------------- #
    def _fn_ln(self, args: list) -> str:
        return f"LOG({self._one_arg(args, 'LN')})"          # SSIS LN -> natural log

    def _fn_log(self, args: list) -> str:
        return f"LOG10({self._one_arg(args, 'LOG')})"       # SSIS LOG -> base-10 log

    # -- date / time functions ----------------------------------------- #
    def _fn_dateadd(self, args: list) -> str:
        if len(args) != 3:
            raise ExpressionError("DATEADD() takes exactly three arguments")
        part, number, date = args
        return f"DATEADD({self._datepart(part)}, {self._value(number)}, {self._value(date)})"

    def _fn_datediff(self, args: list) -> str:
        if len(args) != 3:
            raise ExpressionError("DATEDIFF() takes exactly three arguments")
        part, start, end = args
        return f"DATEDIFF({self._datepart(part)}, {self._value(start)}, {self._value(end)})"

    def _fn_datepart(self, args: list) -> str:
        if len(args) != 2:
            raise ExpressionError("DATEPART() takes exactly two arguments")
        part, date = args
        return f"DATEPART({self._datepart(part)}, {self._value(date)})"

    def _fn_null(self, _args: list) -> str:
        # NULL() reached as a call rather than the NULL(DT_..) literal form.
        return "NULL"

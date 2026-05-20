"""Recursive-descent / Pratt parser for the SSIS expression language.

Precedence (loosest to tightest):

    ?:                ternary, right associative
    ||
    &&
    == !=
    < > <= >=
    | ^ &              bitwise
    + -
    * / %
    unary - + ! ~
    casts, primaries
"""
from __future__ import annotations

from . import ast
from .lexer import Token, tokenize
from ..errors import ExpressionError

# Binary operator binding powers.
_BP: dict[str, int] = {
    "||": 10,
    "&&": 20,
    "==": 30, "!=": 30,
    "<": 40, ">": 40, "<=": 40, ">=": 40,
    "|": 50, "^": 52, "&": 54,
    "+": 60, "-": 60,
    "*": 70, "/": 70, "%": 70,
}

_UNARY_OPS = {"-", "+", "!", "~"}


class Parser:
    """Turns a token list into an :mod:`msb_ssis2sql.expressions.ast` tree."""

    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    # ------------------------------------------------------------------ #
    # token cursor helpers
    # ------------------------------------------------------------------ #
    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def _peek(self, ahead: int = 1) -> Token:
        idx = min(self.i + ahead, len(self.toks) - 1)
        return self.toks[idx]

    def _advance(self) -> Token:
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def _expect(self, kind: str) -> Token:
        if self.cur.kind != kind:
            raise ExpressionError(
                f"expected {kind} but found {self.cur.kind} "
                f"({self.cur.value!r}) at position {self.cur.pos}"
            )
        return self._advance()

    # ------------------------------------------------------------------ #
    # grammar
    # ------------------------------------------------------------------ #
    def parse(self) -> ast.Node:
        """Parse a complete expression; raise if trailing tokens remain."""
        node = self._expression()
        if self.cur.kind != "EOF":
            raise ExpressionError(
                f"unexpected trailing token {self.cur.value!r} at position {self.cur.pos}"
            )
        return node

    def _expression(self) -> ast.Node:
        cond = self._binary(0)
        if self.cur.kind == "QUESTION":
            self._advance()
            when_true = self._expression()
            self._expect("COLON")
            when_false = self._expression()       # right-associative
            return ast.Conditional(cond, when_true, when_false)
        return cond

    def _binary(self, min_bp: int) -> ast.Node:
        left = self._unary()
        while self.cur.kind == "OP" and self.cur.value in _BP:
            op = self.cur.value
            bp = _BP[op]
            if bp < min_bp:
                break
            self._advance()
            right = self._binary(bp + 1)           # left-associative
            left = ast.Binary(op, left, right)
        return left

    def _unary(self) -> ast.Node:
        if self.cur.kind == "OP" and self.cur.value in _UNARY_OPS:
            op = self._advance().value
            return ast.Unary(op, self._unary())
        return self._primary()

    def _primary(self) -> ast.Node:
        tok = self.cur

        if tok.kind == "NUM":
            self._advance()
            kind = "real" if ("." in tok.value or "e" in tok.value.lower()) else "int"
            return ast.Literal(tok.value, kind)

        if tok.kind == "STR":
            self._advance()
            return ast.Literal(tok.value, "str")

        if tok.kind == "BOOL":
            self._advance()
            return ast.Literal(tok.value, "bool")

        if tok.kind == "NULL":
            self._advance()
            if self.cur.kind == "LPAREN":          # typed null: NULL(DT_WSTR, 50)
                self._advance()
                code, args = self._type_spec()
                self._expect("RPAREN")
                return ast.TypedNull(code, args)
            return ast.Literal(None, "null")

        if tok.kind == "COL":
            self._advance()
            return ast.ColumnRef(tok.value)

        if tok.kind == "VAR":
            self._advance()
            ns, name = tok.value
            return ast.VariableRef(ns, name)

        if tok.kind == "IDENT":
            name = tok.value
            self._advance()
            if self.cur.kind == "LPAREN":          # function call
                self._advance()
                args: list[ast.Node] = []
                if self.cur.kind != "RPAREN":
                    args.append(self._expression())
                    while self.cur.kind == "COMMA":
                        self._advance()
                        args.append(self._expression())
                self._expect("RPAREN")
                return ast.FunctionCall(name, args)
            return ast.ColumnRef(name)             # bare (unbracketed) column

        if tok.kind == "LPAREN":
            nxt = self._peek(1)
            if nxt.kind == "IDENT" and nxt.value.upper().startswith("DT_"):
                self._advance()                    # consume '('
                code, args = self._type_spec()
                self._expect("RPAREN")
                operand = self._unary()            # cast binds at unary precedence
                return ast.Cast(code, args, operand)
            self._advance()                        # consume '(' - grouping
            inner = self._expression()
            self._expect("RPAREN")
            return inner

        raise ExpressionError(
            f"unexpected token {tok.kind} ({tok.value!r}) at position {tok.pos}"
        )

    def _type_spec(self) -> tuple[str, list[int]]:
        """Parse ``DT_CODE`` followed by optional ``, N`` length/precision args."""
        code = self._expect("IDENT").value
        args: list[int] = []
        while self.cur.kind == "COMMA":
            self._advance()
            num = self._expect("NUM").value
            args.append(int(float(num)))
        return code, args


def parse_expression(text: str) -> ast.Node:
    """Convenience: lex then parse."""
    return Parser(tokenize(text)).parse()

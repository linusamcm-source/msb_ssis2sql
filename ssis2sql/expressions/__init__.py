"""The SSIS expression language: tokenise, parse, translate to T-SQL.

The SSIS expression grammar is its own small language (used by Derived Column,
Conditional Split, variable expressions, ...). It is *not* T-SQL: ``==`` not
``=``, ``&&`` not ``AND``, ``(DT_STR,n,cp)x`` casts, ``ISNULL(x)`` returning a
boolean rather than a coalesce. This package owns the translation.

Typical use::

    sql, warnings = translate_expression('[First] + " " + [Last]')
    sql, warnings = translate_condition('[Amount] > 1000 && !ISNULL([Region])')
"""
from __future__ import annotations

from collections.abc import Callable

from ..observability import logger
from .lexer import tokenize
from .parser import Parser, parse_expression
from .translator import Translator

__all__ = ["tokenize", "Parser", "Translator", "translate_expression", "translate_condition"]


def translate_expression(
    text: str,
    column_resolver: Callable[[str], str] | None = None,
    variable_resolver: Callable[[str, str], str] | None = None,
) -> tuple[str, list[str]]:
    """Translate an SSIS expression used in *value* position (Derived Column).

    Returns ``(sql_text, warnings)``. Unsupported constructs are reported in
    ``warnings``; raises :class:`~ssis2sql.errors.ExpressionError` when ``text``
    cannot be tokenised or parsed.
    """
    tr = Translator(column_resolver, variable_resolver)
    sql = tr.translate(parse_expression(text))
    logger.debug("expression {!r} -> {!r}", text, sql)
    return sql, tr.warnings


def translate_condition(
    text: str,
    column_resolver: Callable[[str], str] | None = None,
    variable_resolver: Callable[[str, str], str] | None = None,
) -> tuple[str, list[str]]:
    """Translate an SSIS expression used in *boolean* position (a WHERE clause).

    Returns ``(sql_predicate, warnings)``. Unsupported constructs are reported
    in ``warnings``; raises :class:`~ssis2sql.errors.ExpressionError` when
    ``text`` cannot be tokenised or parsed.
    """
    tr = Translator(column_resolver, variable_resolver)
    sql = tr.translate_bool(parse_expression(text))
    logger.debug("condition {!r} -> {!r}", text, sql)
    return sql, tr.warnings

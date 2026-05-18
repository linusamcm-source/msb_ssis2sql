"""Tests for the SSIS expression tokeniser (lexer.py).

These exercise ``tokenize`` directly rather than through the expressions
package facade, so the lexer's token shapes and error paths are pinned
independently of the parser and translator.
"""
from __future__ import annotations

import pytest

from ssis2sql.errors import ExpressionError
from ssis2sql.expressions.lexer import Token, tokenize


def kinds(text: str) -> list[str]:
    """The token kinds produced for ``text`` (including the trailing EOF)."""
    return [t.kind for t in tokenize(text)]


def first(text: str) -> Token:
    """The first token produced for ``text``."""
    return tokenize(text)[0]


# --------------------------------------------------------------------------- #
# EOF terminator
# --------------------------------------------------------------------------- #
def test_empty_input_is_just_eof():
    toks = tokenize("")
    assert len(toks) == 1
    assert toks[0].kind == "EOF"
    assert toks[0].value is None
    assert toks[0].pos == 0


def test_token_stream_always_ends_in_eof():
    toks = tokenize("1 + 2")
    assert toks[-1].kind == "EOF"
    assert toks[-1].pos == len("1 + 2")


def test_whitespace_is_skipped():
    assert kinds("  \t\r\n 1 ") == ["NUM", "EOF"]


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #
def test_integer_literal():
    tok = first("12345")
    assert tok.kind == "NUM"
    assert tok.value == "12345"
    assert tok.pos == 0


def test_real_literal_with_fraction():
    tok = first("3.14159")
    assert tok.kind == "NUM"
    assert tok.value == "3.14159"


def test_real_literal_with_exponent():
    tok = first("1e10")
    assert tok.kind == "NUM"
    assert tok.value == "1e10"


def test_real_literal_with_signed_exponent():
    tok = first("6.022E+23")
    assert tok.kind == "NUM"
    assert tok.value == "6.022E+23"


def test_real_literal_with_negative_exponent():
    assert first("2e-5").value == "2e-5"


def test_number_starting_with_a_dot():
    # A leading dot followed by a digit is still a number.
    assert first(".5").value == ".5"


def test_second_dot_terminates_a_number():
    # Only one dot is consumed; the rest tokenises separately.
    toks = tokenize("1.2.3")
    assert [(t.kind, t.value) for t in toks[:-1]] == [
        ("NUM", "1.2"),
        ("NUM", ".3"),
    ]


# --------------------------------------------------------------------------- #
# string literals
# --------------------------------------------------------------------------- #
def test_plain_string_literal():
    tok = first('"hello world"')
    assert tok.kind == "STR"
    assert tok.value == "hello world"
    assert tok.pos == 0


def test_empty_string_literal():
    assert first('""').value == ""


def test_string_backslash_escapes():
    # n t r and a backslash all decode via the escape table.
    assert first(r'"a\nb\tc\\d"').value == "a\nb\tc\\d"


def test_string_escape_of_quote_and_apostrophe():
    assert first(r'"say \"hi\""').value == 'say "hi"'


def test_string_unknown_escape_keeps_the_following_char():
    # An escape with no table entry yields the literal next character.
    assert first(r'"\q"').value == "q"


def test_string_hex_escape_decodes_to_a_character():
    # \x0041 is capital A; trailing 'X' keeps the escape inside bounds.
    assert first(r'"\x0041X"').value == "AX"


def test_malformed_hex_escape_raises_expression_error():
    with pytest.raises(ExpressionError):
        tokenize(r'"\xZZZZX"')


def test_malformed_hex_escape_error_reports_position():
    with pytest.raises(ExpressionError, match="hex escape"):
        tokenize(r'"\xGGGGX"')


# --------------------------------------------------------------------------- #
# column references
# --------------------------------------------------------------------------- #
def test_column_reference():
    tok = first("[Amount]")
    assert tok.kind == "COL"
    assert tok.value == "Amount"
    assert tok.pos == 0


def test_column_reference_with_spaces_in_name():
    assert first("[Sales Order Number]").value == "Sales Order Number"


# --------------------------------------------------------------------------- #
# variable references
# --------------------------------------------------------------------------- #
def test_variable_reference_with_namespace():
    tok = first("@[User::Threshold]")
    assert tok.kind == "VAR"
    assert tok.value == ("User", "Threshold")
    assert tok.pos == 0


def test_variable_reference_without_namespace_defaults_to_user():
    # No '::' separator -> namespace defaults to "User".
    assert first("@[BatchSize]").value == ("User", "BatchSize")


def test_variable_reference_strips_surrounding_whitespace():
    assert first("@[ System :: PackageName ]").value == ("System", "PackageName")


# --------------------------------------------------------------------------- #
# operators and punctuation
# --------------------------------------------------------------------------- #
def test_multi_char_operators():
    for op in ("==", "!=", "<=", ">=", "&&", "||"):
        tok = first(op)
        assert tok.kind == "OP"
        assert tok.value == op


def test_single_char_operators():
    for op in "<>+-*/%!~&|^":
        tok = first(op)
        assert tok.kind == "OP", op
        assert tok.value == op, op


def test_multi_char_operator_beats_single_char():
    # "==" must lex as one OP token, not two single-char tokens.
    assert [(t.kind, t.value) for t in tokenize("==")[:-1]] == [("OP", "==")]


def test_punctuation_tokens():
    toks = tokenize("(),?:")
    assert [t.kind for t in toks[:-1]] == [
        "LPAREN", "RPAREN", "COMMA", "QUESTION", "COLON",
    ]


def test_operator_positions_are_recorded():
    toks = tokenize("1+2")
    assert toks[1].kind == "OP"
    assert toks[1].pos == 1


# --------------------------------------------------------------------------- #
# keywords and identifiers
# --------------------------------------------------------------------------- #
def test_true_keyword():
    tok = first("TRUE")
    assert tok.kind == "BOOL"
    assert tok.value is True


def test_false_keyword():
    tok = first("FALSE")
    assert tok.kind == "BOOL"
    assert tok.value is False


def test_null_keyword():
    tok = first("NULL")
    assert tok.kind == "NULL"
    assert tok.value is None


def test_keywords_are_case_insensitive():
    assert first("true").value is True
    assert first("False").value is False
    assert first("nUlL").kind == "NULL"


def test_identifier_token():
    tok = first("UPPER")
    assert tok.kind == "IDENT"
    assert tok.value == "UPPER"


def test_identifier_with_underscore_and_digits():
    assert first("_col1").value == "_col1"
    assert first("DT_WSTR").value == "DT_WSTR"


# --------------------------------------------------------------------------- #
# error cases
# --------------------------------------------------------------------------- #
def test_unterminated_string_raises():
    with pytest.raises(ExpressionError, match="unterminated string"):
        tokenize('"no closing quote')


def test_unterminated_column_reference_raises():
    with pytest.raises(ExpressionError, match="unterminated column"):
        tokenize("[no closing bracket")


def test_invalid_variable_token_raises():
    # An '@' not followed by '[' is not a valid variable reference.
    with pytest.raises(ExpressionError, match="invalid variable"):
        tokenize("@name")


def test_unterminated_variable_reference_raises():
    with pytest.raises(ExpressionError, match="unterminated variable"):
        tokenize("@[User::Unfinished")


def test_unexpected_character_raises():
    with pytest.raises(ExpressionError, match="unexpected character"):
        tokenize("#")

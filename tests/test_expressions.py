"""Tests for the SSIS expression -> T-SQL translator."""
from __future__ import annotations

import pytest

from ssis2sql.errors import ExpressionError
from ssis2sql.expressions import translate_condition, translate_expression


def expr(text: str) -> str:
    sql, _ = translate_expression(text)
    return sql


def cond(text: str) -> str:
    sql, _ = translate_condition(text)
    return sql


# --------------------------------------------------------------------------- #
# literals and operators
# --------------------------------------------------------------------------- #
def test_string_literal_is_unicode_and_quote_escaped():
    assert expr('"it\'s fine"') == "N'it''s fine'"


def test_string_concatenation_is_left_associative():
    assert expr('[First] + " " + [Last]') == "(([First] + N' ') + [Last])"


def test_arithmetic_precedence():
    assert expr("1 + 2 * 3") == "(1 + (2 * 3))"


def test_unary_minus():
    assert expr("-[Amount]") == "(-[Amount])"


def test_control_character_is_spliced_out_of_literal():
    # \n is below the literal-safe range; it becomes an NCHAR() concatenation.
    assert expr(r'"a\nb"') == "(N'a' + NCHAR(10) + N'b')"


# --------------------------------------------------------------------------- #
# value vs predicate context
# --------------------------------------------------------------------------- #
def test_comparison_in_value_context_becomes_case():
    assert expr("[X] == 5") == "CASE WHEN [X] = 5 THEN 1 ELSE 0 END"


def test_comparison_in_condition_context_is_a_predicate():
    assert cond("[X] == 5") == "[X] = 5"


def test_not_equal_operator():
    assert cond("[X] != 5") == "[X] <> 5"


def test_logical_and_joins_predicates():
    assert cond("[A] > 1 && [B] < 9") == "([A] > 1 AND [B] < 9)"


def test_bare_value_in_condition_context_is_compared():
    assert cond("[Flag]") == "[Flag] <> 0"


def test_negation_wraps_a_predicate():
    assert cond("![Active]") == "NOT ([Active] <> 0)"


# --------------------------------------------------------------------------- #
# conditional / ternary
# --------------------------------------------------------------------------- #
def test_ternary_becomes_case():
    assert expr('[X] > 0 ? "pos" : "neg"') == (
        "CASE WHEN [X] > 0 THEN N'pos' ELSE N'neg' END"
    )


# --------------------------------------------------------------------------- #
# null handling
# --------------------------------------------------------------------------- #
def test_isnull_is_an_is_null_predicate_not_a_coalesce():
    assert cond("ISNULL([Email])") == "[Email] IS NULL"


def test_isnull_in_value_context():
    assert expr("ISNULL([Email])") == "CASE WHEN [Email] IS NULL THEN 1 ELSE 0 END"


def test_replacenull_is_a_coalesce():
    assert expr("REPLACENULL([Email], [Backup])") == "COALESCE([Email], [Backup])"


def test_typed_null():
    assert expr("NULL(DT_WSTR, 50)") == "CAST(NULL AS NVARCHAR(50))"


# --------------------------------------------------------------------------- #
# casts
# --------------------------------------------------------------------------- #
def test_string_cast_uses_length_argument():
    assert expr("(DT_STR,10,1252)[Phone]") == "CAST([Phone] AS VARCHAR(10))"


def test_integer_cast():
    assert expr("(DT_I4)[Amount]") == "CAST([Amount] AS INT)"


def test_numeric_cast_uses_precision_and_scale():
    assert expr("(DT_NUMERIC,18,2)[Amount]") == "CAST([Amount] AS NUMERIC(18,2))"


def test_cast_binds_tighter_than_arithmetic():
    assert expr("(DT_I4)[A] * [B]") == "(CAST([A] AS INT) * [B])"


# --------------------------------------------------------------------------- #
# functions
# --------------------------------------------------------------------------- #
def test_trim_expands_to_ltrim_rtrim():
    assert expr("TRIM([Name])") == "LTRIM(RTRIM([Name]))"


def test_passthrough_function_keeps_its_name():
    assert expr("UPPER([Name])") == "UPPER([Name])"


def test_dateadd_maps_the_datepart_keyword():
    assert expr('DATEADD("mi", 30, [Start])') == "DATEADD(minute, 30, [Start])"


def test_datepart_maps_the_datepart_keyword():
    assert expr('DATEPART("yyyy", [OrderDate])') == "DATEPART(year, [OrderDate])"


def test_unknown_function_warns_and_emits_verbatim():
    sql, warnings = translate_expression("WIDGETIZE([X])")
    assert "WIDGETIZE([X])" in sql
    assert any("WIDGETIZE" in w for w in warnings)


# --------------------------------------------------------------------------- #
# variables
# --------------------------------------------------------------------------- #
def test_variable_reference_resolves_to_a_sql_parameter():
    seen = []

    def resolver(ns, name):
        seen.append((ns, name))
        return f"@{name}"

    sql, _ = translate_expression("[Amount] > @[User::Threshold]", variable_resolver=resolver)
    assert sql == "CASE WHEN [Amount] > @Threshold THEN 1 ELSE 0 END"
    assert seen == [("User", "Threshold")]


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #
def test_unterminated_string_raises():
    with pytest.raises(ExpressionError):
        translate_expression('"unterminated')


def test_trailing_token_raises():
    with pytest.raises(ExpressionError):
        translate_expression("[A] [B]")


# --------------------------------------------------------------------------- #
# parser corners: bare columns, grouping parentheses, malformed input
# --------------------------------------------------------------------------- #
def test_bare_unbracketed_identifier_is_a_column_reference():
    assert expr("Amount") == "[Amount]"


def test_parentheses_group_a_subexpression():
    assert expr("([A] + [B]) * [C]") == "(([A] + [B]) * [C])"


def test_unexpected_token_raises():
    with pytest.raises(ExpressionError):
        translate_expression(")")


def test_missing_closing_paren_after_a_cast_raises():
    with pytest.raises(ExpressionError):
        translate_expression("(DT_I4 [A]")

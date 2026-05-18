"""Tests for the SSIS expression AST -> T-SQL translator (translator.py).

These import ``Translator`` directly and feed it ASTs from
``parse_expression``, exercising both the value and predicate contexts and
the deliberate split between recorded warnings and raised errors.
"""
from __future__ import annotations

import pytest

from ssis2sql.errors import ExpressionError
from ssis2sql.expressions import ast
from ssis2sql.expressions.parser import parse_expression
from ssis2sql.expressions.translator import Translator, default_column_resolver


def value(text: str) -> str:
    """Translate ``text`` in value context."""
    return Translator().translate(parse_expression(text))


def boolean(text: str) -> str:
    """Translate ``text`` in predicate context."""
    return Translator().translate_bool(parse_expression(text))


# --------------------------------------------------------------------------- #
# default_column_resolver
# --------------------------------------------------------------------------- #
def test_default_column_resolver_brackets_the_name():
    assert default_column_resolver("Amount") == "[Amount]"


def test_default_column_resolver_is_used_when_none_supplied():
    assert value("[Region]") == "[Region]"


def test_custom_column_resolver_overrides_rendering():
    tr = Translator(column_resolver=lambda name: f"src.{name}")
    assert tr.translate(parse_expression("[Amount]")) == "src.Amount"


# --------------------------------------------------------------------------- #
# literals
# --------------------------------------------------------------------------- #
def test_integer_literal():
    assert value("42") == "42"


def test_real_literal_keeps_lexed_text():
    assert value("3.14") == "3.14"


def test_string_literal_is_unicode_and_quote_escaped():
    assert value('"it\'s fine"') == "N'it''s fine'"


def test_null_literal():
    assert value("NULL") == "NULL"


def test_bool_literal_in_value_context_is_one_or_zero():
    assert value("TRUE") == "CASE WHEN (1 = 1) THEN 1 ELSE 0 END"


def test_bool_literal_in_bool_context_is_a_predicate():
    assert boolean("TRUE") == "(1 = 1)"
    assert boolean("FALSE") == "(1 = 0)"


# --------------------------------------------------------------------------- #
# comparisons: value context becomes CASE, bool context is a predicate
# --------------------------------------------------------------------------- #
def test_comparison_in_value_context_becomes_case():
    assert value("[X] == 5") == "CASE WHEN [X] = 5 THEN 1 ELSE 0 END"


def test_comparison_in_bool_context_is_a_predicate():
    assert boolean("[X] == 5") == "[X] = 5"


def test_not_equal_maps_to_angle_brackets():
    assert boolean("[X] != 5") == "[X] <> 5"


def test_relational_operators_pass_through():
    assert boolean("[A] >= 10") == "[A] >= 10"
    assert boolean("[A] <= 10") == "[A] <= 10"


def test_logical_and_joins_predicates():
    assert boolean("[A] > 1 && [B] < 9") == "([A] > 1 AND [B] < 9)"


def test_logical_or_joins_predicates():
    assert boolean("[A] > 1 || [B] < 9") == "([A] > 1 OR [B] < 9)"


def test_negation_wraps_a_predicate():
    assert boolean("![Active]") == "NOT ([Active] <> 0)"


def test_bare_value_in_bool_context_is_compared_to_zero():
    assert boolean("[Flag]") == "[Flag] <> 0"


def test_negation_in_value_context_becomes_case():
    assert value("![Active]") == "CASE WHEN NOT ([Active] <> 0) THEN 1 ELSE 0 END"


# --------------------------------------------------------------------------- #
# arithmetic, bitwise and unary operators
# --------------------------------------------------------------------------- #
def test_arithmetic_is_parenthesised():
    assert value("[A] + [B]") == "([A] + [B])"


def test_bitwise_and_is_parenthesised():
    assert value("[A] & [B]") == "([A] & [B])"


def test_unary_minus():
    assert value("-[Amount]") == "(-[Amount])"


def test_unary_bitwise_not():
    assert value("~[Mask]") == "(~ [Mask])"


# --------------------------------------------------------------------------- #
# conditional / ternary
# --------------------------------------------------------------------------- #
def test_ternary_becomes_case():
    assert value('[X] > 0 ? "pos" : "neg"') == (
        "CASE WHEN [X] > 0 THEN N'pos' ELSE N'neg' END"
    )


# --------------------------------------------------------------------------- #
# ISNULL: predicate in bool context, CASE in value context
# --------------------------------------------------------------------------- #
def test_isnull_in_bool_context_is_an_is_null_predicate():
    assert boolean("ISNULL([Email])") == "[Email] IS NULL"


def test_isnull_in_value_context_becomes_case():
    assert value("ISNULL([Email])") == "CASE WHEN [Email] IS NULL THEN 1 ELSE 0 END"


def test_isnull_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="ISNULL"):
        Translator().translate(parse_expression("ISNULL([A], [B])"))


def test_isnull_wrong_arity_raises_in_bool_context():
    with pytest.raises(ExpressionError, match="ISNULL"):
        Translator().translate_bool(parse_expression("ISNULL([A], [B])"))


# --------------------------------------------------------------------------- #
# casts and typed nulls
# --------------------------------------------------------------------------- #
def test_string_cast_uses_length_argument():
    assert value("(DT_STR,10,1252)[Phone]") == "CAST([Phone] AS VARCHAR(10))"


def test_integer_cast():
    assert value("(DT_I4)[Amount]") == "CAST([Amount] AS INT)"


def test_numeric_cast_uses_precision_and_scale():
    assert value("(DT_NUMERIC,18,2)[Amount]") == "CAST([Amount] AS NUMERIC(18,2))"


def test_typed_null_literal():
    assert value("NULL(DT_WSTR, 50)") == "CAST(NULL AS NVARCHAR(50))"


# --------------------------------------------------------------------------- #
# function passthrough vs unmapped warnings
# --------------------------------------------------------------------------- #
def test_passthrough_function_keeps_its_name_and_records_no_warning():
    tr = Translator()
    sql = tr.translate(parse_expression("UPPER([Name])"))
    assert sql == "UPPER([Name])"
    assert tr.warnings == []


def test_passthrough_function_uppercases_the_name():
    # The SSIS spelling may be lower-case; T-SQL output is the canonical name.
    assert value("upper([Name])") == "UPPER([Name])"


def test_trim_expands_to_ltrim_rtrim():
    assert value("TRIM([Name])") == "LTRIM(RTRIM([Name]))"


def test_dateadd_maps_the_datepart_keyword():
    assert value('DATEADD("mi", 30, [Start])') == "DATEADD(minute, 30, [Start])"


def test_unmapped_function_warns_and_emits_verbatim():
    tr = Translator()
    sql = tr.translate(parse_expression("WIDGETIZE([X])"))
    assert "WIDGETIZE([X])" in sql
    assert "/* unmapped */" in sql
    assert len(tr.warnings) == 1
    assert "WIDGETIZE" in tr.warnings[0]


def test_unknown_datepart_warns_but_still_emits():
    tr = Translator()
    sql = tr.translate(parse_expression('DATEPART("zz", [OrderDate])'))
    assert "DATEPART(zz, [OrderDate])" == sql
    assert any("datepart" in w for w in tr.warnings)


# --------------------------------------------------------------------------- #
# arity errors raise ExpressionError
# --------------------------------------------------------------------------- #
def test_trim_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="TRIM"):
        Translator().translate(parse_expression("TRIM([A], [B])"))


def test_replacenull_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="REPLACENULL"):
        Translator().translate(parse_expression("REPLACENULL([A])"))


def test_dateadd_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="DATEADD"):
        Translator().translate(parse_expression('DATEADD("d", 1)'))


def test_datediff_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="DATEDIFF"):
        Translator().translate(parse_expression('DATEDIFF("d", [Start])'))


def test_findstring_wrong_arity_raises():
    with pytest.raises(ExpressionError, match="FINDSTRING"):
        Translator().translate(parse_expression('FINDSTRING([S], "x")'))


# --------------------------------------------------------------------------- #
# structurally invalid input raises ExpressionError
# --------------------------------------------------------------------------- #
def test_unknown_node_type_raises():
    # A bare Node carries no translatable shape.
    with pytest.raises(ExpressionError, match="cannot translate node"):
        Translator().translate(ast.Node())


def test_unknown_unary_operator_raises():
    bad = ast.Unary("@", ast.Literal("1", "int"))
    with pytest.raises(ExpressionError, match="unknown unary operator"):
        Translator().translate(bad)


# --------------------------------------------------------------------------- #
# variable references
# --------------------------------------------------------------------------- #
def test_variable_ref_without_a_resolver_falls_back_to_at_name():
    # With no variable_resolver supplied, a VariableRef renders as @<name>.
    assert value("@[User::Threshold]") == "@Threshold"


def test_variable_ref_uses_the_supplied_resolver():
    tr = Translator(variable_resolver=lambda ns, name: f"@{ns}__{name}")
    assert tr.translate(parse_expression("@[User::Threshold]")) == "@User__Threshold"


# --------------------------------------------------------------------------- #
# function handlers the bundled example never exercises
# --------------------------------------------------------------------------- #
def test_codepoint_maps_to_unicode():
    assert value("CODEPOINT([Ch])") == "UNICODE([Ch])"


def test_ln_maps_to_natural_log():
    assert value("LN([X])") == "LOG([X])"


def test_log_maps_to_base_ten_log():
    assert value("LOG([X])") == "LOG10([X])"


def test_datediff_maps_the_datepart_keyword():
    assert value('DATEDIFF("d", [Start], [End])') == "DATEDIFF(day, [Start], [End])"


def test_findstring_first_occurrence_is_a_plain_charindex():
    tr = Translator()
    sql = tr.translate(parse_expression('FINDSTRING([S], "x", 1)'))
    assert sql == "CHARINDEX(N'x', [S])"
    assert tr.warnings == []


def test_findstring_later_occurrence_warns():
    tr = Translator()
    sql = tr.translate(parse_expression('FINDSTRING([S], "x", 2)'))
    assert sql == "CHARINDEX(N'x', [S])"
    assert any("occurrence" in w for w in tr.warnings)


def test_datepart_with_a_non_literal_argument_warns():
    tr = Translator()
    sql = tr.translate(parse_expression("DATEADD([PartCol], 1, [D])"))
    assert "DATEADD(" in sql
    assert any("non-literal datepart" in w for w in tr.warnings)


# --------------------------------------------------------------------------- #
# internal emission branches not reached through the value/predicate routing
#
# A bool literal, a bare comparison and ISNULL are always intercepted as
# predicates before _raw_value runs, so these handlers are exercised directly.
# --------------------------------------------------------------------------- #
def test_literal_helper_renders_a_bool_as_one_or_zero():
    assert Translator._literal(ast.Literal(True, "bool")) == "1"
    assert Translator._literal(ast.Literal(False, "bool")) == "0"


def test_unary_helper_renders_bang_in_value_context_as_case():
    node = ast.Unary("!", parse_expression("[Active]"))
    assert Translator()._unary(node) == "CASE WHEN NOT ([Active] <> 0) THEN 1 ELSE 0 END"


def test_binary_helper_renders_a_comparison_in_value_context_as_case():
    node = ast.Binary("==", parse_expression("[X]"), ast.Literal("5", "int"))
    assert Translator()._binary(node) == "CASE WHEN [X] = 5 THEN 1 ELSE 0 END"


def test_binary_helper_rejects_an_unknown_operator():
    node = ast.Binary("@@", parse_expression("[A]"), parse_expression("[B]"))
    with pytest.raises(ExpressionError, match="unknown binary operator"):
        Translator().translate(node)


def test_fn_isnull_value_handler_emits_a_case():
    # The value-context ISNULL handler (predicate context is handled in _bool).
    assert Translator()._fn_isnull([parse_expression("[Email]")]) == (
        "CASE WHEN [Email] IS NULL THEN 1 ELSE 0 END"
    )


def test_fn_null_handler_emits_a_bare_null():
    assert Translator()._fn_null([]) == "NULL"

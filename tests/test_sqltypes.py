"""Tests for SSIS data-type codes mapped onto T-SQL types."""
from __future__ import annotations

from ssis2sql.model import Column
from ssis2sql.sqltypes import sql_string_literal, tsql_type, tsql_type_from_column


# --------------------------------------------------------------------------- #
# tsql_type - short fixed-shape codes
# --------------------------------------------------------------------------- #
def test_short_integer_code():
    assert tsql_type("i4") == "INT"


def test_short_code_is_case_insensitive():
    assert tsql_type("I8") == "BIGINT"


def test_short_unicode_string_code_uses_default_length():
    assert tsql_type("wstr") == "NVARCHAR(4000)"


def test_short_code_with_dt_prefix_is_normalised():
    assert tsql_type("DT_BOOL") == "BIT"


# --------------------------------------------------------------------------- #
# tsql_type - DT_ cast codes carrying length / precision / scale args
# --------------------------------------------------------------------------- #
def test_dt_str_cast_uses_length_argument():
    assert tsql_type("DT_STR", [50, 1252]) == "VARCHAR(50)"


def test_dt_wstr_cast_uses_length_argument():
    assert tsql_type("DT_WSTR", [120]) == "NVARCHAR(120)"


def test_dt_numeric_cast_uses_precision_and_scale():
    assert tsql_type("DT_NUMERIC", [18, 2]) == "NUMERIC(18,2)"


def test_dt_numeric_defaults_when_args_missing():
    assert tsql_type("DT_NUMERIC") == "NUMERIC(18,0)"


def test_dt_decimal_cast_uses_scale_only():
    assert tsql_type("DT_DECIMAL", [4]) == "DECIMAL(38,4)"


def test_dt_str_defaults_to_8000_without_args():
    assert tsql_type("DT_STR") == "VARCHAR(8000)"


# --------------------------------------------------------------------------- #
# tsql_type - unknown codes fall back to the NVARCHAR(255) default
# --------------------------------------------------------------------------- #
def test_unknown_code_falls_back_to_default():
    assert tsql_type("widgettype") == "NVARCHAR(255)"


def test_empty_code_falls_back_to_default():
    assert tsql_type("") == "NVARCHAR(255)"


# --------------------------------------------------------------------------- #
# tsql_type_from_column - size lives on the Column, not the args list
# --------------------------------------------------------------------------- #
def test_column_unicode_string_uses_its_length():
    col = Column(name="Email", data_type="wstr", length=320)
    assert tsql_type_from_column(col) == "NVARCHAR(320)"


def test_column_string_without_length_uses_255_default():
    col = Column(name="Note", data_type="str")
    assert tsql_type_from_column(col) == "VARCHAR(255)"


def test_column_numeric_uses_precision_and_scale():
    col = Column(name="Amount", data_type="numeric", precision=18, scale=2)
    assert tsql_type_from_column(col) == "NUMERIC(18,2)"


def test_column_numeric_defaults_when_precision_scale_unset():
    col = Column(name="Amount", data_type="numeric")
    assert tsql_type_from_column(col) == "NUMERIC(18,0)"


def test_column_fixed_shape_code_ignores_size_fields():
    col = Column(name="Id", data_type="i4", length=99)
    assert tsql_type_from_column(col) == "INT"


def test_column_unknown_code_falls_back_to_default():
    col = Column(name="Mystery", data_type="zzz")
    assert tsql_type_from_column(col) == "NVARCHAR(255)"


# --------------------------------------------------------------------------- #
# sql_string_literal - Unicode literals, quote escaping, control characters
# --------------------------------------------------------------------------- #
def test_plain_string_is_a_unicode_literal():
    assert sql_string_literal("hello") == "N'hello'"


def test_empty_string_is_an_empty_unicode_literal():
    assert sql_string_literal("") == "N''"


def test_single_quotes_are_doubled():
    assert sql_string_literal("it's fine") == "N'it''s fine'"


def test_control_character_is_spliced_into_nchar_concatenation():
    assert sql_string_literal("a\nb") == "(N'a' + NCHAR(10) + N'b')"


def test_leading_control_character_concatenation():
    assert sql_string_literal("\ttab") == "(NCHAR(9) + N'tab')"


def test_trailing_control_character_concatenation():
    assert sql_string_literal("end\r") == "(N'end' + NCHAR(13))"


def test_quotes_around_control_character_are_still_escaped():
    assert sql_string_literal("a'\nb'c") == "(N'a''' + NCHAR(10) + N'b''c')"


# --------------------------------------------------------------------------- #
# binary and decimal codes
# --------------------------------------------------------------------------- #
def test_dt_bytes_cast_uses_length_argument():
    assert tsql_type("DT_BYTES", [16]) == "VARBINARY(16)"


def test_dt_bytes_cast_defaults_without_args():
    assert tsql_type("DT_BYTES") == "VARBINARY(8000)"


def test_column_bytes_uses_its_length():
    col = Column(name="Blob", data_type="bytes", length=64)
    assert tsql_type_from_column(col) == "VARBINARY(64)"


def test_column_decimal_uses_its_scale():
    col = Column(name="Rate", data_type="decimal", scale=4)
    assert tsql_type_from_column(col) == "DECIMAL(38,4)"

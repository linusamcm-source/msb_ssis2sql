"""Tests for the T-SQL dialect - identifier quoting."""
from __future__ import annotations

from ssis2sql.dialect import TSqlDialect


# --------------------------------------------------------------------------- #
# quote - single identifiers
# --------------------------------------------------------------------------- #
def test_dialect_name():
    assert TSqlDialect().name == "tsql"


def test_quote_brackets_a_plain_identifier():
    assert TSqlDialect().quote("Customers") == "[Customers]"


def test_quote_escapes_an_embedded_closing_bracket():
    assert TSqlDialect().quote("Weird]Name") == "[Weird]]Name]"


def test_quote_strips_existing_brackets_before_requoting():
    assert TSqlDialect().quote("[Customers]") == "[Customers]"


def test_quote_strips_existing_double_quotes_before_requoting():
    assert TSqlDialect().quote('"Customers"') == "[Customers]"


def test_quote_trims_surrounding_whitespace():
    assert TSqlDialect().quote("  Orders  ") == "[Orders]"


# --------------------------------------------------------------------------- #
# quote_qualified - multi-part names
# --------------------------------------------------------------------------- #
def test_quote_qualified_single_part():
    assert TSqlDialect().quote_qualified("Customers") == "[Customers]"


def test_quote_qualified_two_part_name():
    assert TSqlDialect().quote_qualified("dbo.Customers") == "[dbo].[Customers]"


def test_quote_qualified_three_part_name():
    assert (
        TSqlDialect().quote_qualified("Sales.dbo.Customers")
        == "[Sales].[dbo].[Customers]"
    )


def test_quote_qualified_keeps_dots_inside_bracketed_part():
    # The dot lives inside [...] so it is not a part separator.
    assert (
        TSqlDialect().quote_qualified("dbo.[Customer.Archive]")
        == "[dbo].[Customer.Archive]"
    )


def test_quote_qualified_keeps_dots_inside_quoted_part():
    assert (
        TSqlDialect().quote_qualified('dbo."Odd.Table"')
        == "[dbo].[Odd.Table]"
    )


def test_quote_qualified_requotes_already_bracketed_parts():
    assert (
        TSqlDialect().quote_qualified("[dbo].[Customers]")
        == "[dbo].[Customers]"
    )


def test_quote_qualified_empty_name_falls_back_to_quote():
    assert TSqlDialect().quote_qualified("") == "[]"

"""Tests for the dependency-free shared helpers."""
from __future__ import annotations

from ssis2sql.util import to_int


# --------------------------------------------------------------------------- #
# to_int - direct integer values
# --------------------------------------------------------------------------- #
def test_passes_through_an_int():
    assert to_int(42) == 42


def test_parses_a_plain_numeric_string():
    assert to_int("7") == 7


# --------------------------------------------------------------------------- #
# to_int - float-shaped SSIS property strings
# --------------------------------------------------------------------------- #
def test_parses_a_float_shaped_string_via_truncation():
    assert to_int("1.0") == 1


def test_parses_a_non_integral_float_string_by_truncating():
    assert to_int("3.9") == 3


def test_parses_an_actual_float_by_truncating():
    assert to_int(2.7) == 2


# --------------------------------------------------------------------------- #
# to_int - empty / None fall back to the default
# --------------------------------------------------------------------------- #
def test_none_returns_the_default():
    assert to_int(None) is None


def test_empty_string_returns_the_default():
    assert to_int("") is None


def test_none_returns_an_explicit_default():
    assert to_int(None, default=0) == 0


def test_empty_string_returns_an_explicit_default():
    assert to_int("", default=99) == 99


# --------------------------------------------------------------------------- #
# to_int - garbage falls back to the default
# --------------------------------------------------------------------------- #
def test_garbage_string_returns_the_default():
    assert to_int("not-a-number") is None


def test_garbage_string_returns_an_explicit_default():
    assert to_int("abc", default=-1) == -1

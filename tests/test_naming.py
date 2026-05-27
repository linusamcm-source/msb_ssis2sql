"""Direct unit tests for ``msb_ssis2sql._naming``.

The sanitiser + per-directory collision-suffix algorithm is locked in
plan-final.md Decisions:
  * lowercase; replace every non-``[a-z0-9]`` with ``_``;
  * collapse runs of ``_``; trim leading/trailing ``_``;
  * post-sanitisation collisions: sort inputs by ORIGINAL name (case-sensitive)
    and append ``_2``, ``_3``, ... to 2nd, 3rd, ... occurrences.

Will fail with ImportError until ``msb_ssis2sql/_naming.py`` ships.
"""
from __future__ import annotations


def test_sanitise_lowercases():
    from msb_ssis2sql._naming import sanitise
    assert sanitise("FooBar") == "foobar"


def test_sanitise_replaces_non_alnum_with_underscore():
    from msb_ssis2sql._naming import sanitise
    assert sanitise("Foo Bar") == "foo_bar"
    assert sanitise("Foo.Bar") == "foo_bar"
    assert sanitise("Foo-Bar+Baz") == "foo_bar_baz"


def test_sanitise_collapses_runs_of_underscore():
    from msb_ssis2sql._naming import sanitise
    assert sanitise("foo   bar") == "foo_bar"
    assert sanitise("foo___bar") == "foo_bar"


def test_sanitise_trims_leading_and_trailing_underscores():
    from msb_ssis2sql._naming import sanitise
    assert sanitise("__foo__") == "foo"
    assert sanitise(" .foo. ") == "foo"


def test_sanitise_keeps_alphanumeric():
    from msb_ssis2sql._naming import sanitise
    assert sanitise("abc123") == "abc123"


# --------------------------------------------------------------------------- #
# resolve_collisions: suffix _2, _3, ... per Decisions
# --------------------------------------------------------------------------- #

def test_resolve_collisions_no_collisions_returns_inputs_unchanged():
    from msb_ssis2sql._naming import resolve_collisions
    out = resolve_collisions(["Foo Bar", "Hello"])
    assert out == {"Foo Bar": "foo_bar", "Hello": "hello"}


def test_resolve_collisions_suffix_in_sorted_original_order():
    """Three colliding originals: first keeps un-suffixed, then _2, _3."""
    from msb_ssis2sql._naming import resolve_collisions

    out = resolve_collisions(["Foo Bar", "Foo.Bar", "Foo_Bar"])
    # Sort by original (case-sensitive): "Foo Bar" < "Foo.Bar" < "Foo_Bar"
    assert out == {
        "Foo Bar": "foo_bar",
        "Foo.Bar": "foo_bar_2",
        "Foo_Bar": "foo_bar_3",
    }


def test_resolve_collisions_deterministic_across_input_order():
    from msb_ssis2sql._naming import resolve_collisions

    a = resolve_collisions(["Foo_Bar", "Foo.Bar", "Foo Bar"])
    b = resolve_collisions(["Foo Bar", "Foo_Bar", "Foo.Bar"])
    assert a == b

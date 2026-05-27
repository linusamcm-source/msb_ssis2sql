"""Tests for ``validation.ledger`` — RED phase.

``validation/ledger.py`` does not exist yet; every test in this module will
fail with ``ModuleNotFoundError`` until the engineer's Story 4 implementation
lands.  That is the correct TDD RED state.

Contract under test (sprint plan, Story 4 + §6):

``parse_ledger(path) -> DestLedger``
    Parse a ``ledger.yaml`` file and return a typed ``DestLedger`` object.
    Validates on load:
    - ``ordered`` comparison mode requires a non-empty ``order_key`` list.
    - Every ``known_divergence`` entry requires a non-empty ``reason`` string.
    - An unknown column policy name raises ``LedgerError``.

``DestLedger``
    Typed container for one destination's ledger:
    - ``comparison``: ``"multiset"`` | ``"ordered"``
    - ``order_key``: ``list[str]`` (required when comparison = ordered)
    - ``columns``: ``dict[str, ColumnPolicy]``
    - ``known_divergences``: ``list[KnownDivergence]``

``ColumnPolicy``
    - ``policy``: ``"exact"`` | ``"float"`` | ``"datetime"`` | ``"non_null"`` | ``"exclude"``
    - ``epsilon``: ``float`` (for float policy)
    - ``tolerance``: ``float`` (for datetime policy, in seconds)
    - ``reason``: ``str`` (optional, for exclude / documentation)

``KnownDivergence``
    - ``kind``: ``str`` (e.g. ``"lookup_left_join"``)
    - ``component``: ``str``
    - ``handling``: ``"xfail"`` | ``"filter"`` | ``"accept"``
    - ``reason``: ``str`` (required, non-empty)

``LedgerError``
    Raised on validation failures (unknown policy, missing reason, ordered
    without order_key).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# This import raises ModuleNotFoundError until ledger.py exists.
# That is the expected RED state — do not wrap in try/except.
from validation.ledger import LedgerError, parse_ledger


# ---------------------------------------------------------------------------
# Helpers — write YAML strings to tmp_path and parse them
# ---------------------------------------------------------------------------


def _write_ledger(tmp_path: Path, content: str) -> Path:
    """Write *content* to ``tmp_path/ledger.yaml`` and return the path."""
    p = tmp_path / "ledger.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


_VALID_MULTISET_YAML = """\
    package: test_pkg
    destinations:
      dst_results:
        comparison: multiset
        columns:
          id:         { policy: exact }
          score:      { policy: float, epsilon: 0.001 }
          label:      { policy: exact }
          loaded_at:  { policy: exclude, reason: "non-deterministic timestamp" }
        known_divergences:
          - kind: lookup_left_join
            component: "Lookup Region"
            handling: xfail
            reason: "Transpiler emits LEFT JOIN; SSIS fails on no-match"
    """

_VALID_ORDERED_YAML = """\
    package: test_pkg
    destinations:
      dst_sorted:
        comparison: ordered
        order_key: [region, id]
        columns:
          region:     { policy: exact }
          id:         { policy: exact }
          amount:     { policy: float, epsilon: 0.01 }
          captured_at: { policy: datetime, tolerance: 2.0 }
          flag:       { policy: non_null }
        known_divergences: []
    """


# ---------------------------------------------------------------------------
# Parsing valid ledgers
# ---------------------------------------------------------------------------


def test_parse_ledger_multiset_comparison_mode(tmp_path: Path) -> None:
    """A multiset ledger parses with ``comparison = 'multiset'``."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_MULTISET_YAML))
    dest = ledger["dst_results"]
    assert dest.comparison == "multiset"


def test_parse_ledger_ordered_comparison_mode(tmp_path: Path) -> None:
    """An ordered ledger parses with ``comparison = 'ordered'``."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_ORDERED_YAML))
    dest = ledger["dst_sorted"]
    assert dest.comparison == "ordered"


def test_parse_ledger_ordered_has_order_key(tmp_path: Path) -> None:
    """An ordered ledger carries the ``order_key`` list."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_ORDERED_YAML))
    dest = ledger["dst_sorted"]
    assert dest.order_key == ["region", "id"]


def test_parse_ledger_column_exact_policy(tmp_path: Path) -> None:
    """A column with ``policy: exact`` parses to a ColumnPolicy with policy='exact'."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_MULTISET_YAML))
    col = ledger["dst_results"].columns["id"]
    assert col.policy == "exact"


def test_parse_ledger_column_float_policy_has_epsilon(tmp_path: Path) -> None:
    """A float-policy column carries the ``epsilon`` value."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_MULTISET_YAML))
    col = ledger["dst_results"].columns["score"]
    assert col.policy == "float"
    assert col.epsilon == pytest.approx(0.001)


def test_parse_ledger_column_datetime_policy_has_tolerance(tmp_path: Path) -> None:
    """A datetime-policy column carries the ``tolerance`` value (in seconds)."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_ORDERED_YAML))
    col = ledger["dst_sorted"].columns["captured_at"]
    assert col.policy == "datetime"
    assert col.tolerance == pytest.approx(2.0)


def test_parse_ledger_column_non_null_policy(tmp_path: Path) -> None:
    """A non_null-policy column parses correctly."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_ORDERED_YAML))
    col = ledger["dst_sorted"].columns["flag"]
    assert col.policy == "non_null"


def test_parse_ledger_column_exclude_policy(tmp_path: Path) -> None:
    """An exclude-policy column parses correctly and carries its reason."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_MULTISET_YAML))
    col = ledger["dst_results"].columns["loaded_at"]
    assert col.policy == "exclude"
    assert "non-deterministic" in col.reason


def test_parse_ledger_known_divergence_fields(tmp_path: Path) -> None:
    """A known_divergence entry parses all four fields."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_MULTISET_YAML))
    divs = ledger["dst_results"].known_divergences
    assert len(divs) == 1
    div = divs[0]
    assert div.kind == "lookup_left_join"
    assert div.component == "Lookup Region"
    assert div.handling == "xfail"
    assert "LEFT JOIN" in div.reason


def test_parse_ledger_empty_known_divergences(tmp_path: Path) -> None:
    """A destination with an empty divergences list parses to an empty list."""
    ledger = parse_ledger(_write_ledger(tmp_path, _VALID_ORDERED_YAML))
    assert ledger["dst_sorted"].known_divergences == []


# ---------------------------------------------------------------------------
# Validation — errors on malformed ledgers
# ---------------------------------------------------------------------------


def test_parse_ledger_rejects_ordered_without_order_key(tmp_path: Path) -> None:
    """``ordered`` comparison without ``order_key`` raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: ordered
            columns:
              id: { policy: exact }
            known_divergences: []
        """
    with pytest.raises(LedgerError, match="order_key"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))


def test_parse_ledger_rejects_ordered_with_empty_order_key(tmp_path: Path) -> None:
    """``ordered`` comparison with an empty ``order_key`` list raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: ordered
            order_key: []
            columns:
              id: { policy: exact }
            known_divergences: []
        """
    with pytest.raises(LedgerError, match="order_key"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))


def test_parse_ledger_rejects_divergence_without_reason(tmp_path: Path) -> None:
    """A ``known_divergence`` entry without a ``reason`` raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: multiset
            columns:
              id: { policy: exact }
            known_divergences:
              - kind: lookup_left_join
                component: "Lookup X"
                handling: xfail
        """
    with pytest.raises(LedgerError, match="reason"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))


def test_parse_ledger_rejects_divergence_with_empty_reason(tmp_path: Path) -> None:
    """A ``known_divergence`` with an empty-string ``reason`` raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: multiset
            columns:
              id: { policy: exact }
            known_divergences:
              - kind: lookup_left_join
                component: "Lookup X"
                handling: xfail
                reason: ""
        """
    with pytest.raises(LedgerError, match="reason"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))


def test_parse_ledger_rejects_unknown_policy(tmp_path: Path) -> None:
    """An unrecognised column policy name raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: multiset
            columns:
              id: { policy: fuzzy_match }
            known_divergences: []
        """
    with pytest.raises(LedgerError, match="policy"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))


def test_parse_ledger_rejects_unknown_handling_mode(tmp_path: Path) -> None:
    """An unrecognised ``handling`` value raises ``LedgerError``."""
    yaml_text = """\
        package: bad_pkg
        destinations:
          dst_bad:
            comparison: multiset
            columns:
              id: { policy: exact }
            known_divergences:
              - kind: some_kind
                component: "X"
                handling: ignore
                reason: "some reason"
        """
    with pytest.raises(LedgerError, match="handling"):
        parse_ledger(_write_ledger(tmp_path, yaml_text))

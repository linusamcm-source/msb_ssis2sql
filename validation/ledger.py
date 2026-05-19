"""Ledger YAML parsing and typed containers for the comparison engine.

A ledger file (``ledger.yaml``) lives at the corpus package level and
declares, per destination table:

- The comparison mode (``multiset`` or ``ordered``).
- Per-column comparison policy (exact / float / datetime / non_null / exclude).
- Known expected divergences (xfail / filter / accept), each with a mandatory
  ``reason`` traceable to a README limitation or filed issue.

Public API
----------
LedgerError
    Raised when a ledger file fails validation (unknown policy, missing reason,
    ``ordered`` without an ``order_key``).
ColumnPolicy
    Per-column comparison policy configuration.
KnownDivergence
    A single expected-divergence entry in the ledger.
DestLedger
    All ledger data for one destination table.
parse_ledger(path) -> dict[str, DestLedger]
    Parse and validate a ``ledger.yaml`` file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Allowed values — validated on parse
# ---------------------------------------------------------------------------

_VALID_POLICIES: frozenset[str] = frozenset(
    {"exact", "float", "datetime", "non_null", "exclude"}
)
_VALID_HANDLINGS: frozenset[str] = frozenset({"xfail", "filter", "accept"})


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Raised when a ledger file fails structural or semantic validation."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ColumnPolicy:
    """Per-column comparison policy.

    Attributes
    ----------
    policy:
        One of ``exact``, ``float``, ``datetime``, ``non_null``, ``exclude``.
    epsilon:
        Absolute tolerance for ``float`` comparisons.
    tolerance:
        Tolerance in seconds for ``datetime`` comparisons.
    reason:
        Human-readable note (typically required for ``exclude`` and optional
        elsewhere).
    """

    policy: str
    epsilon: float = 1e-6
    tolerance: float = 1.0
    reason: str = ""


@dataclass
class KnownDivergence:
    """A single expected-divergence entry.

    Attributes
    ----------
    kind:
        Machine-readable category (e.g. ``lookup_left_join``).
    component:
        The SSIS component name this divergence originates from.
    handling:
        One of ``xfail``, ``filter``, ``accept``.
    reason:
        Non-empty human-readable justification traceable to a README
        limitation or filed issue.  Required and validated on parse.
    filter_expr:
        A pandas ``DataFrame.query`` expression applied (to both golden and
        actual) when ``handling == "filter"``.  Empty string if not used.
    """

    kind: str
    component: str
    handling: str
    reason: str
    filter_expr: str = ""


@dataclass
class DestLedger:
    """Ledger data for one destination table.

    Attributes
    ----------
    comparison:
        ``"multiset"`` (default) or ``"ordered"``.
    order_key:
        Column names to sort by when ``comparison == "ordered"``.
    columns:
        Mapping of column name to ``ColumnPolicy``.
    known_divergences:
        List of ``KnownDivergence`` entries.
    """

    comparison: str
    order_key: list[str]
    columns: dict[str, ColumnPolicy]
    known_divergences: list[KnownDivergence] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_ledger(path: Path) -> dict[str, DestLedger]:
    """Parse and validate *path* (a ``ledger.yaml`` file).

    Returns a mapping of destination name to ``DestLedger``.

    Raises
    ------
    LedgerError
        If any of the following validation rules is violated:

        - A destination with ``comparison: ordered`` has no ``order_key`` or
          an empty one.
        - A ``known_divergence`` entry has no ``reason`` or an empty one.
        - A column policy value is not one of the recognised set.
        - A divergence ``handling`` value is not one of the recognised set.
    """
    raw: dict = yaml.safe_load(path.read_text(encoding="utf-8"))
    destinations_raw: dict = raw.get("destinations", {})
    result: dict[str, DestLedger] = {}

    for dest_name, dest_data in destinations_raw.items():
        comparison: str = dest_data.get("comparison", "multiset")
        order_key: list[str] = dest_data.get("order_key") or []

        if comparison == "ordered" and not order_key:
            raise LedgerError(
                f"Destination {dest_name!r}: comparison=ordered requires a "
                f"non-empty order_key list."
            )

        # Parse column policies.
        columns: dict[str, ColumnPolicy] = {}
        for col_name, col_data in (dest_data.get("columns") or {}).items():
            policy_name: str = col_data.get("policy", "exact")
            if policy_name not in _VALID_POLICIES:
                raise LedgerError(
                    f"Destination {dest_name!r}, column {col_name!r}: "
                    f"unknown policy {policy_name!r}. "
                    f"Valid values: {sorted(_VALID_POLICIES)}."
                )
            columns[col_name] = ColumnPolicy(
                policy=policy_name,
                epsilon=float(col_data.get("epsilon", 1e-6)),
                tolerance=float(col_data.get("tolerance", 1.0)),
                reason=col_data.get("reason", ""),
            )

        # Parse known divergences.
        known_divergences: list[KnownDivergence] = []
        for div_data in dest_data.get("known_divergences") or []:
            reason: str = div_data.get("reason") or ""
            if not reason:
                raise LedgerError(
                    f"Destination {dest_name!r}: every known_divergence "
                    f"must have a non-empty reason (kind={div_data.get('kind')!r})."
                )
            handling: str = div_data.get("handling", "")
            if handling not in _VALID_HANDLINGS:
                raise LedgerError(
                    f"Destination {dest_name!r}: unknown handling mode "
                    f"{handling!r}. Valid values: {sorted(_VALID_HANDLINGS)}."
                )
            known_divergences.append(
                KnownDivergence(
                    kind=div_data.get("kind", ""),
                    component=div_data.get("component", ""),
                    handling=handling,
                    reason=reason,
                    filter_expr=div_data.get("filter_expr", ""),
                )
            )

        result[dest_name] = DestLedger(
            comparison=comparison,
            order_key=order_key,
            columns=columns,
            known_divergences=known_divergences,
        )

    return result

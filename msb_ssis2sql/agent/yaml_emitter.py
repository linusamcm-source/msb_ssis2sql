"""Emit an AgentJob as a deterministic YAML string (Appendix A schema).

When ``AgentStep`` audit fields (``original_subsystem``,
``original_command``, ``dtsx_source``) are populated, they are rendered
at the TOP of the step block in declaration order, followed by the
remaining step fields in dataclass declaration order (T-6). When all
three audit fields are ``None``, they are filtered out and the step's
remaining keys are sorted alphabetically — preserving the byte-identity
of the no-audit-fields golden (AC-18).
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import yaml

from .model import AgentJob, AgentStep

# Audit-field names declared on AgentStep (T-4). Render order matches
# the declaration sequence — preserves the new golden's expected shape.
_AUDIT_FIELDS = ("original_subsystem", "original_command", "dtsx_source")


def _step_dict_for_yaml(step_dict: dict[str, Any]) -> dict[str, Any]:
    """Reshape a step's ``asdict`` representation for YAML emission.

    Two paths:
      * All three audit fields are ``None``: drop them entirely, then sort
        the surviving keys alphabetically (no-audit branch, AC-18).
      * At least one audit field is populated: keep audit fields first in
        declaration order, then the remaining keys in
        ``AgentStep.__dataclass_fields__`` declaration order.
    """
    audit_populated = any(step_dict.get(name) is not None for name in _AUDIT_FIELDS)

    if not audit_populated:
        # Filter is name-based, not value-based: only the audit keys are
        # dropped when None. Other keys are preserved regardless of value.
        return {
            key: value
            for key, value in sorted(step_dict.items())
            if key not in _AUDIT_FIELDS
        }

    # Audit fields first, then declaration order minus audit fields.
    ordered: dict[str, Any] = {}
    for name in _AUDIT_FIELDS:
        # Even None audit fields are kept in this branch — but per the
        # rewriter contract, hitting the audit branch means all three were
        # set by `maybe_rewrite_step`. Defensive: skip any that are None
        # so a half-populated step still emits cleanly.
        value = step_dict.get(name)
        if value is not None:
            ordered[name] = value

    for field_name in AgentStep.__dataclass_fields__:
        if field_name in _AUDIT_FIELDS:
            continue
        if field_name in step_dict:
            ordered[field_name] = step_dict[field_name]

    return ordered


def emit_job_yaml(job: AgentJob) -> str:
    """Render ``job`` as deterministic YAML.

    Job-level keys are alphabetised. Steps follow the per-step rule in
    :func:`_step_dict_for_yaml`. Schedules use the existing
    alphabetised-by-key behaviour (PyYAML ``sort_keys=True`` on each
    schedule dict).
    """
    raw = asdict(job)

    # Reshape each step. Job-level + schedule-level remain alphabetical.
    raw["steps"] = [_step_dict_for_yaml(step) for step in raw["steps"]]
    raw["schedules"] = [dict(sorted(s.items())) for s in raw["schedules"]]

    # Sort top-level keys for stable order; emit with sort_keys=False so
    # the manually-ordered step dicts keep their insertion order.
    sorted_top = dict(sorted(raw.items()))
    return yaml.safe_dump(sorted_top, sort_keys=False, default_flow_style=False)

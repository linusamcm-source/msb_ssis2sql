"""Sanitiser + per-directory collision-suffix algorithm.

Locked by plan-final.md §Decisions (C-1):
  * lowercase; replace every non-[a-z0-9] with '_'; collapse runs; trim.
  * Post-sanitisation collisions: sort inputs by ORIGINAL name (case-sensitive)
    and append _2, _3, ... to 2nd, 3rd, ... occurrences. First keeps un-suffixed.
"""
from __future__ import annotations

import pathlib
import re
from collections import defaultdict


def sanitise(name: str) -> str:
    """Return a lowercase, underscore-separated identifier from ``name``."""
    lowered = name.lower()
    replaced = re.sub(r"[^a-z0-9]+", "_", lowered)
    collapsed = re.sub(r"_+", "_", replaced)
    return collapsed.strip("_")


def resolve_collisions(names: list[str]) -> dict[str, str]:
    """Map each original name to its collision-free sanitised identifier.

    Within a group that sanitises to the same string, inputs are sorted by
    original name (case-sensitive ASCII). The first keeps the un-suffixed
    sanitised name; subsequent ones get _2, _3, etc.

    Returns a dict preserving input order: {original_name: resolved_name}.
    """
    sanitised = {n: sanitise(n) for n in names}

    groups: dict[str, list[str]] = defaultdict(list)
    for n in names:
        groups[sanitised[n]].append(n)

    suffix_map: dict[str, str] = {}
    for base, group_names in groups.items():
        sorted_group = sorted(group_names)
        for i, orig in enumerate(sorted_group):
            if i == 0:
                suffix_map[orig] = base
            else:
                suffix_map[orig] = f"{base}_{i + 1}"

    return {n: suffix_map[n] for n in names}


def resolve_procedure_name(rel_dir: pathlib.Path, file_stem: str) -> str:
    """Return ``usp_<SanitisedRelDir>_<SanitisedStem>`` or ``usp_<SanitisedStem>``
    when the sanitised rel-dir is empty (top-level files)."""
    stem = sanitise(file_stem)
    rel_str = str(rel_dir)
    if rel_str in ("", "."):
        return f"usp_{stem}"
    dir_part = sanitise(rel_str.replace("/", "_").replace("\\", "_"))
    if not dir_part:
        return f"usp_{stem}"
    return f"usp_{dir_part}_{stem}"

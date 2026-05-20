"""Small dependency-free helpers shared across layers."""
from __future__ import annotations


def to_int(value: object, default: int | None = None) -> int | None:
    """Best-effort int coercion for SSIS property strings ('1', '1.0', '')."""
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

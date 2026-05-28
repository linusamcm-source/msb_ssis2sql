"""Small dependency-free helpers shared across layers."""
from __future__ import annotations


def to_int(value: object, default: int | None = None) -> int | None:
    """Best-effort int coercion for SSIS property strings ('1', '1.0', '')."""
    if value in (None, ""):
        return default
    try:
        return int(value)  # type: ignore[call-overload]  # intentional: best-effort coercion from unknown type, TypeError caught below
    except (TypeError, ValueError):
        try:
            return int(float(value))  # type: ignore[arg-type]  # intentional: best-effort coercion from unknown type, TypeError caught below
        except (TypeError, ValueError):
            return default

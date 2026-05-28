"""Small dependency-free helpers shared across layers."""
from __future__ import annotations

from urllib.parse import unquote


def decode_package_name(name: str) -> str:
    """URL-decode SSIS package name/path text.

    SSIS-export tools sometimes write '%20' (and other percent-escapes) into
    filenames and ExecutePackageTask references. The on-disk file and the
    in-XML reference may not agree on whether spaces are encoded, which
    breaks the orchestrator's name-to-file matching. Decoding both sides
    gives a single canonical form.
    """
    return unquote(name)


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

"""Dataclass for a single extracted SSIS package."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedPackage:
    """One SSIS package recovered from a SQL Server store.

    Attributes
    ----------
    folder:
        Source folder name as stored in the server (original, un-sanitised).
        May be the empty string for packages at the msdb root.
    project:
        SSIS catalog project name (catalog store only); ``None`` for the
        legacy msdb store, which has no project tier.
    name:
        Package name as stored in the server. For the catalog this may carry a
        trailing ``.dtsx`` extension; the writer strips it before sanitising.
    payload:
        The raw ``.dtsx`` bytes, written to disk verbatim.
    store:
        ``"msdb"`` or ``"ssisdb"`` — which store the package came from.
    """

    folder: str
    project: str | None
    name: str
    payload: bytes
    store: str

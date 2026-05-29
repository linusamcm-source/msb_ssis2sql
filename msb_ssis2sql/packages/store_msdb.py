"""Legacy msdb package store: ``msdb.dbo.sysssispackages``.

Each row's ``packagedata`` column holds the package XML (the ``.dtsx`` bytes).
We select it as ``VARBINARY(MAX)`` and write the bytes verbatim, preserving the
original encoding declaration and any byte-order mark.
"""
from __future__ import annotations

from typing import Any

from .model import ExtractedPackage

# Probe: NULL when the modern (2008+) store table is absent, so we can fall
# back to the SQL Server 2005-era sysdtspackages90 tables.
PROBE_MODERN_SQL = "SELECT OBJECT_ID('msdb.dbo.sysssispackages')"

PACKAGES_SQL = (
    "SELECT f.foldername, p.name, CAST(p.packagedata AS VARBINARY(MAX)) "
    "FROM msdb.dbo.sysssispackages p "
    "JOIN msdb.dbo.sysssispackagefolders f ON p.folderid = f.folderid "
    "ORDER BY f.foldername, p.name"
)

PACKAGES_SQL_90 = (
    "SELECT f.foldername, p.name, CAST(p.packagedata AS VARBINARY(MAX)) "
    "FROM msdb.dbo.sysdtspackages90 p "
    "JOIN msdb.dbo.sysdtspackagefolders90 f ON p.folderid = f.folderid "
    "ORDER BY f.foldername, p.name"
)


def _coerce_payload(value: Any) -> bytes:
    """Return the package bytes regardless of how the driver typed the column."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    # An XML/nvarchar column comes back as str; encode it as UTF-8.
    return str(value).encode("utf-8")


def fetch_packages(cursor: Any) -> list[ExtractedPackage]:
    """Query the msdb package store and return one :class:`ExtractedPackage` per row.

    Falls back to the ``sysdtspackages90`` tables when the modern store table
    is not present on the instance.
    """
    cursor.execute(PROBE_MODERN_SQL)
    probe = cursor.fetchone()
    use_modern = probe is not None and probe[0] is not None

    cursor.execute(PACKAGES_SQL if use_modern else PACKAGES_SQL_90)
    rows = cursor.fetchall()

    return [
        ExtractedPackage(
            folder=row[0] or "",
            project=None,
            name=row[1],
            payload=_coerce_payload(row[2]),
            store="msdb",
        )
        for row in rows
    ]

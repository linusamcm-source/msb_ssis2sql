"""Resolve an SSIS ``componentClassID`` to a normalised :class:`ComponentKind`.

Modern .dtsx files (SQL Server 2012+) use friendly identifiers such as
``Microsoft.DerivedColumn``. Older files and some exports use fully-qualified
assembly strings. Both are handled: an exact match first, then a substring
heuristic over the dotted name.
"""
from __future__ import annotations

from .model import ComponentKind

# Exact friendly-name lookup (lower-cased, assembly suffix stripped).
_FRIENDLY: dict[str, ComponentKind] = {
    "microsoft.oledbsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.adonetsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.odbcsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.excelsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.xmlsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.rawsource": ComponentKind.OLEDB_SOURCE,
    "microsoft.flatfilesource": ComponentKind.FLATFILE_SOURCE,
    "microsoft.oledbdestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.adonetdestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.sqlserverdestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.odbcdestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.exceldestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.rawdestination": ComponentKind.OLEDB_DESTINATION,
    "microsoft.flatfiledestination": ComponentKind.FLATFILE_DESTINATION,
    "microsoft.derivedcolumn": ComponentKind.DERIVED_COLUMN,
    "microsoft.dataconvert": ComponentKind.DATA_CONVERSION,
    "microsoft.copycolumn": ComponentKind.COPY_COLUMN,
    "microsoft.conditionalsplit": ComponentKind.CONDITIONAL_SPLIT,
    "microsoft.lookup": ComponentKind.LOOKUP,
    "microsoft.aggregate": ComponentKind.AGGREGATE,
    "microsoft.sort": ComponentKind.SORT,
    "microsoft.unionall": ComponentKind.UNION_ALL,
    "microsoft.merge": ComponentKind.MERGE,
    "microsoft.mergejoin": ComponentKind.MERGE_JOIN,
    "microsoft.multicast": ComponentKind.MULTICAST,
    "microsoft.rowcount": ComponentKind.ROW_COUNT,
    "microsoft.charactermap": ComponentKind.CHARACTER_MAP,
    "microsoft.audit": ComponentKind.AUDIT,
    "microsoft.oledbcommand": ComponentKind.OLEDB_COMMAND,
    "microsoft.pivot": ComponentKind.PIVOT,
    "microsoft.unpivot": ComponentKind.UNPIVOT,
    "microsoft.managedcomponenthost": ComponentKind.SCRIPT,
    "microsoft.scriptcomponenthost": ComponentKind.SCRIPT,
    "microsoft.sqlserverscd": ComponentKind.SCD,
}

# Ordered substring heuristics - longer / more specific needles first so that
# "mergejoin" wins over "merge" and "unpivot" wins over "pivot".
_HEURISTICS: list[tuple[str, ComponentKind]] = [
    ("mergejoin", ComponentKind.MERGE_JOIN),
    ("unionall", ComponentKind.UNION_ALL),
    ("conditionalsplit", ComponentKind.CONDITIONAL_SPLIT),
    ("derivedcolumn", ComponentKind.DERIVED_COLUMN),
    ("dataconvert", ComponentKind.DATA_CONVERSION),
    ("dataconversion", ComponentKind.DATA_CONVERSION),
    ("copycolumn", ComponentKind.COPY_COLUMN),
    ("charactermap", ComponentKind.CHARACTER_MAP),
    ("rowcount", ComponentKind.ROW_COUNT),
    ("oledbcommand", ComponentKind.OLEDB_COMMAND),
    ("unpivot", ComponentKind.UNPIVOT),
    ("pivot", ComponentKind.PIVOT),
    ("lookup", ComponentKind.LOOKUP),
    ("aggregate", ComponentKind.AGGREGATE),
    ("multicast", ComponentKind.MULTICAST),
    ("audit", ComponentKind.AUDIT),
    ("scd", ComponentKind.SCD),
    ("script", ComponentKind.SCRIPT),
    ("sort", ComponentKind.SORT),
    ("merge", ComponentKind.MERGE),
    ("flatfiledestination", ComponentKind.FLATFILE_DESTINATION),
    ("flatfilesource", ComponentKind.FLATFILE_SOURCE),
    ("destination", ComponentKind.OLEDB_DESTINATION),
    ("source", ComponentKind.OLEDB_SOURCE),
]

# Legacy SQL Server 2005/2008 pipeline components are identified by a GUID
# rather than a friendly name. These five are verified from the bundled
# example packages; further GUIDs can be added as they are encountered.
_GUIDS: dict[str, ComponentKind] = {
    "2c0a8be5-1edc-4353-a0ef-b778599c65a0": ComponentKind.OLEDB_SOURCE,
    "b551fca8-23bd-4719-896f-d8f352a5283c": ComponentKind.OLEDB_SOURCE,        # Excel Source
    "e2568105-9550-4f71-a638-b7fe42e66922": ComponentKind.OLEDB_DESTINATION,
    "4963caed-cb38-4146-96f0-5910342ff3b9": ComponentKind.OLEDB_DESTINATION,   # Excel Destination
    "9cf90bf0-5bcc-4c63-b91d-1f322dc12c26": ComponentKind.DERIVED_COLUMN,
}

# Components that have no row-set semantics in plain SQL; the transpiler emits a
# transparent pass-through and a warning rather than failing the whole package.
PASS_THROUGH_KINDS = frozenset(
    {
        ComponentKind.MULTICAST,
        ComponentKind.ROW_COUNT,
        ComponentKind.AUDIT,
        ComponentKind.CHARACTER_MAP,
    }
)


def resolve(class_id: str) -> ComponentKind:
    """Map a raw ``componentClassID`` string onto a :class:`ComponentKind`."""
    if not class_id:
        return ComponentKind.UNKNOWN
    key = class_id.strip().lower().strip("{}")
    base = key.split(",", 1)[0].strip()          # drop ", Microsoft.SqlServer..., Version=..."
    if base in _FRIENDLY:
        return _FRIENDLY[base]
    if base in _GUIDS:                            # legacy GUID componentClassID
        return _GUIDS[base]
    for needle, kind in _HEURISTICS:
        if needle in base:
            return kind
    return ComponentKind.UNKNOWN

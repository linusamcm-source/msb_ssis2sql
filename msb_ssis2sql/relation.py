"""The relational unit that flows between transpilers.

Every component output is modelled as a :class:`Relation` - a named result set
that becomes one common table expression (CTE) in the consolidated output.
A downstream transpiler never re-parses an upstream component; it only reads
the upstream :class:`Relation`'s column list and references its CTE name.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RelColumn:
    """One column exposed by a :class:`Relation`.

    ``expr`` is the SQL text that produces the column *inside the SELECT that
    defines this relation*. Once the relation is a CTE, downstream code refers
    to the column by ``name`` only.
    """

    name: str
    expr: str
    data_type: str = ""          # SSIS short code, best-effort
    lineage_id: str = ""


@dataclass
class Relation:
    """A named result set: the SQL realisation of one component output.

    The CTE body that defines the relation is held by the build context
    (``ctx.ctes[name]``); a relation needs its name, its exposed columns, and
    any row order a Sort imposed on it.
    """

    name: str                                  # CTE name
    columns: list[RelColumn] = field(default_factory=list)
    # ORDER BY clause set by a Sort; read by a destination it feeds directly.
    order_by: str = ""

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def find(self, name: str) -> RelColumn | None:
        """Case-insensitive lookup by exposed column name."""
        low = name.lower()
        for col in self.columns:
            if col.name.lower() == low:
                return col
        return None

    def find_by_lineage(self, lineage_id: str) -> RelColumn | None:
        """Lookup by the upstream lineage id this column was produced under."""
        if not lineage_id:
            return None
        for col in self.columns:
            if col.lineage_id and col.lineage_id == lineage_id:
                return col
        return None

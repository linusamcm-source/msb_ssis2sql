"""SQL dialect concerns - identifier quoting.

Only T-SQL is implemented. The class is kept behind a small interface so a
future dialect (PostgreSQL, Snowflake) can be slotted in without touching the
transpilers, which only ever call :meth:`quote` and :meth:`quote_qualified`.
"""
from __future__ import annotations


class TSqlDialect:
    """Microsoft T-SQL (SQL Server 2016+)."""

    name = "tsql"

    def quote(self, identifier: str) -> str:
        """Bracket-quote a single identifier, escaping embedded ``]``."""
        clean = identifier.strip().strip('[]"')
        return "[" + clean.replace("]", "]]") + "]"

    def quote_qualified(self, name: str) -> str:
        """Quote a possibly multi-part name such as ``Sales.dbo.Customers``."""
        parts = self._split_qualified(name)
        if not parts:
            return self.quote(name)
        return ".".join(self.quote(p) for p in parts)

    @staticmethod
    def _split_qualified(name: str) -> list[str]:
        """Split on ``.`` while respecting ``[...]`` / ``"..."`` quoting."""
        name = (name or "").strip()
        parts: list[str] = []
        buf: list[str] = []
        in_bracket = False
        in_quote = False
        for ch in name:
            if ch == "[" and not in_quote:
                in_bracket = True
            elif ch == "]" and not in_quote:
                in_bracket = False
            elif ch == '"':
                in_quote = not in_quote
            elif ch == "." and not in_bracket and not in_quote:
                parts.append("".join(buf))
                buf = []
                continue
            buf.append(ch)
        if buf:
            parts.append("".join(buf))
        return [p.strip() for p in parts if p.strip().strip('[]"')]

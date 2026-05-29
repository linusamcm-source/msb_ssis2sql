"""SSIS data-type codes mapped onto T-SQL types.

Two surfaces feed in:

* the ``dataType`` attribute on pipeline columns - short codes like ``i4``,
  ``wstr``, ``numeric`` (with ``length`` / ``precision`` / ``scale`` siblings);
* cast type codes in SSIS expressions - ``DT_STR``, ``DT_WSTR``, ``DT_NUMERIC``
  (with the length / precision / scale supplied as cast arguments).

Both reduce to the same base table; only where the size lives differs.
"""
from __future__ import annotations

from .model import Column

# Fixed-shape codes - no length / precision / scale needed.
_BASE: dict[str, str] = {
    "i1": "SMALLINT",          # signed 1-byte has no exact T-SQL peer
    "i2": "SMALLINT",
    "i4": "INT",
    "i8": "BIGINT",
    "ui1": "TINYINT",
    "ui2": "INT",
    "ui4": "BIGINT",
    "ui8": "DECIMAL(20,0)",
    "r4": "REAL",
    "r8": "FLOAT",
    "bool": "BIT",
    "guid": "UNIQUEIDENTIFIER",
    "cy": "MONEY",
    "date": "DATETIME",
    "dbdate": "DATE",
    "dbtime": "TIME(0)",
    "dbtime2": "TIME(7)",
    "dbtimestamp": "DATETIME",
    "dbtimestamp2": "DATETIME2(7)",
    "dbtimestampoffset": "DATETIMEOFFSET(7)",
    "filetime": "DATETIME2(7)",
    "image": "VARBINARY(MAX)",
    "text": "VARCHAR(MAX)",
    "ntext": "NVARCHAR(MAX)",
}

_DEFAULT = "NVARCHAR(255)"


def _normalise_type_code(code: str) -> str:
    code = (code or "").strip().lower()
    if code.startswith("dt_"):
        code = code[3:]
    return code


def tsql_type(code: str, args: list[int] | None = None) -> str:
    """Translate an expression cast code, e.g. ``tsql_type("DT_STR", [50, 1252])``."""
    args = args or []
    c = _normalise_type_code(code)
    if c == "str":
        return f"VARCHAR({args[0] if args else 8000})"
    if c == "wstr":
        return f"NVARCHAR({args[0] if args else 4000})"
    if c == "bytes":
        return f"VARBINARY({args[0] if args else 8000})"
    if c == "numeric":
        precision = args[0] if len(args) > 0 else 18
        scale = args[1] if len(args) > 1 else 0
        return f"NUMERIC({precision},{scale})"
    if c == "decimal":
        scale = args[0] if args else 0
        return f"DECIMAL(38,{scale})"
    return _BASE.get(c, _DEFAULT)


def tsql_type_from_column(col: Column) -> str:
    """Translate a pipeline :class:`~msb_ssis2sql.model.Column` to a T-SQL type."""
    c = _normalise_type_code(col.data_type)
    if c == "str":
        return f"VARCHAR({col.length or 255})"
    if c == "wstr":
        return f"NVARCHAR({col.length or 255})"
    if c == "bytes":
        return f"VARBINARY({col.length or 255})"
    if c == "numeric":
        return f"NUMERIC({col.precision or 18},{col.scale or 0})"
    if c == "decimal":
        return f"DECIMAL(38,{col.scale or 0})"
    return _BASE.get(c, _DEFAULT)


# .NET TypeCode -> T-SQL, for project/package parameter ``DataType`` codes.
_PARAM_TYPECODE: dict[str, str] = {
    "3": "BIT",                # Boolean
    "4": "NCHAR(1)",           # Char
    "5": "SMALLINT",           # SByte
    "6": "TINYINT",            # Byte
    "7": "SMALLINT",           # Int16
    "8": "INT",                # UInt16
    "9": "INT",                # Int32
    "10": "BIGINT",            # UInt32
    "11": "BIGINT",            # Int64
    "12": "DECIMAL(20,0)",     # UInt64
    "13": "REAL",              # Single
    "14": "FLOAT",             # Double
    "15": "DECIMAL(38,6)",     # Decimal
    "16": "DATETIME2(7)",      # DateTime
    "18": "NVARCHAR(4000)",    # String
}

_PARAM_DEFAULT = "NVARCHAR(4000)"

# Character/date families take a quoted literal; everything else is emitted bare.
_QUOTED_TYPE_PREFIXES = ("NVARCHAR", "VARCHAR", "NCHAR", "CHAR", "DATE", "TIME", "UNIQUEIDENTIFIER")


def param_type_to_tsql(code: str) -> str:
    """Map a parameter ``DataType`` code to a T-SQL type.

    Project parameters store a .NET ``TypeCode`` integer; package parameters use
    the same numeric space in practice. Non-numeric codes fall back to the
    pipeline-column / expression-cast mapping. Unknown codes default to
    ``NVARCHAR(4000)``.
    """
    code = (code or "").strip()
    if code in _PARAM_TYPECODE:
        return _PARAM_TYPECODE[code]
    if code.isdigit():
        return _PARAM_DEFAULT
    return tsql_type(code)


def param_literal(type_sql: str, value: str, sensitive: bool) -> str:
    """Render a parameter's default as a T-SQL literal for its declared type.

    Sensitive parameters (value withheld under an ``Encrypt*`` protection level)
    and empty non-character defaults render as ``NULL``; character/date types are
    quoted; numeric/bit types are emitted bare.
    """
    if sensitive:
        return "NULL"
    upper = type_sql.upper()
    if upper.startswith(_QUOTED_TYPE_PREFIXES):
        return sql_string_literal(value)
    return value.strip() if value.strip() else "NULL"


def sql_string_literal(value: str) -> str:
    """Render a Python string as a T-SQL Unicode literal.

    Control characters cannot live inside a literal, so they are spliced out
    as ``NCHAR(n)`` concatenations - ``"a\\nb"`` -> ``(N'a' + NCHAR(10) + N'b')``.
    """
    parts: list[str] = []
    buf: list[str] = []
    for ch in value:
        if ord(ch) < 32:
            if buf:
                parts.append("N'" + "".join(buf).replace("'", "''") + "'")
                buf = []
            parts.append(f"NCHAR({ord(ch)})")
        else:
            buf.append(ch)
    if buf or not parts:
        parts.append("N'" + "".join(buf).replace("'", "''") + "'")
    return parts[0] if len(parts) == 1 else "(" + " + ".join(parts) + ")"

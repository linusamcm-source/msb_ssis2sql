"""Tokeniser for the SSIS expression language."""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import ExpressionError

# Token kinds:
#   NUM STR BOOL NULL COL VAR IDENT OP LPAREN RPAREN COMMA QUESTION COLON EOF


@dataclass
class Token:
    kind: str
    value: str | bool | None | tuple[str, str]
    pos: int


_MULTI_OPS = ("==", "!=", "<=", ">=", "&&", "||")
_SINGLE_OPS = set("<>+-*/%!~&|^")

# SSIS string-literal escape sequences.
_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v",
    "a": "\a", "b": "\b", "0": "\0", '"': '"', "'": "'", "\\": "\\",
}


def tokenize(text: str) -> list[Token]:
    """Lex an SSIS expression into a token list terminated by an ``EOF`` token."""
    toks: list[Token] = []
    i, n = 0, len(text)

    while i < n:
        c = text[i]

        if c in " \t\r\n":
            i += 1
            continue

        # --- string literal: "..." with backslash escapes ----------------
        if c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    if nxt == "x" and j + 5 < n:
                        try:
                            buf.append(chr(int(text[j + 2:j + 6], 16)))
                        except ValueError:
                            raise ExpressionError(
                                f"invalid \\x hex escape at position {j}"
                            ) from None
                        j += 6
                        continue
                    buf.append(_ESCAPES.get(nxt, nxt))
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            if j >= n:
                raise ExpressionError(f"unterminated string literal at position {i}")
            toks.append(Token("STR", "".join(buf), i))
            i = j + 1
            continue

        # --- column reference: [Column Name] -----------------------------
        if c == "[":
            j = text.find("]", i + 1)
            if j < 0:
                raise ExpressionError(f"unterminated column reference at position {i}")
            toks.append(Token("COL", text[i + 1:j], i))
            i = j + 1
            continue

        # --- variable reference: @[Namespace::Name] ----------------------
        if c == "@":
            if i + 1 < n and text[i + 1] == "[":
                j = text.find("]", i + 2)
                if j < 0:
                    raise ExpressionError(f"unterminated variable reference at position {i}")
                inner = text[i + 2:j]
                if "::" in inner:
                    ns, name = inner.split("::", 1)
                else:
                    ns, name = "User", inner
                toks.append(Token("VAR", (ns.strip(), name.strip()), i))
                i = j + 1
                continue
            raise ExpressionError(f"invalid variable token at position {i}")

        # --- number ------------------------------------------------------
        if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            j = i
            seen_dot = seen_exp = False
            while j < n:
                ch = text[j]
                if ch.isdigit():
                    j += 1
                elif ch == "." and not seen_dot and not seen_exp:
                    seen_dot = True
                    j += 1
                elif ch in "eE" and not seen_exp and j + 1 < n:
                    seen_exp = True
                    j += 1
                    if j < n and text[j] in "+-":
                        j += 1
                else:
                    break
            toks.append(Token("NUM", text[i:j], i))
            i = j
            continue

        # --- identifier / keyword ---------------------------------------
        if c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            up = word.upper()
            if up == "TRUE":
                toks.append(Token("BOOL", True, i))
            elif up == "FALSE":
                toks.append(Token("BOOL", False, i))
            elif up == "NULL":
                toks.append(Token("NULL", None, i))
            else:
                toks.append(Token("IDENT", word, i))
            i = j
            continue

        # --- operators and punctuation ----------------------------------
        if text[i:i + 2] in _MULTI_OPS:
            toks.append(Token("OP", text[i:i + 2], i))
            i += 2
            continue
        if c == "(":
            toks.append(Token("LPAREN", c, i))
        elif c == ")":
            toks.append(Token("RPAREN", c, i))
        elif c == ",":
            toks.append(Token("COMMA", c, i))
        elif c == "?":
            toks.append(Token("QUESTION", c, i))
        elif c == ":":
            toks.append(Token("COLON", c, i))
        elif c in _SINGLE_OPS:
            toks.append(Token("OP", c, i))
        else:
            raise ExpressionError(f"unexpected character {c!r} at position {i}")
        i += 1

    toks.append(Token("EOF", None, n))
    return toks

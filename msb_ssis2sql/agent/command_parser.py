"""Parse ``msdb.dbo.sysjobsteps.command`` text into a dtsx path or Unparseable.

Pure-string transform — no I/O. Used by the agent-step rewriter (T-4) to
extract the .dtsx reference from a DTExec command line so the resolver
(``msb_ssis2sql.agent.manifest.resolve``) can map it to a stored
procedure.

D-9 / D-10 / D-12 / D-13 of plan-final-agent-step-procs.md:
  * D-9 — three regex patterns tried in order: /F (long /FILE), /ISSERVER,
    /SQ (long /SQL). First match wins.
  * D-10 — commands containing ``%`` (env var) or ``/CONFIGFILE`` are
    rejected up front; rewriting would be unsound without the env.
  * D-12 — ``.dtsx`` (lowercase) is appended to the captured path's
    basename when the basename has no ``.dtsx``/``.DTSX`` suffix. Existing
    case is preserved — audit fields keep operator-original casing.
  * D-13 — captured path is POSIX-normalised (``\\`` → ``/``) so it
    compares apples to apples with manifest entries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..util import _posix

# D-9 regex set. Compiled once at module load. The third pattern's
# trailing ``\s+`` is load-bearing — prevents matching /SQUASH, /SQLDBG,
# /SQLOUTPUT etc. that share the /SQ prefix but are not the /SQ flag.
# CODE-H2 — anchor at start-of-string OR whitespace so commands that begin
# directly with /FILE (no leading dtexec) still match. Real msdb rows for
# SSIS subsystem steps often omit the dtexec prefix.
PATTERN_FILE = re.compile(
    r'(?:^|\s)/F(?:ILE)?\s+(?P<quoted>"[^"]+"|\S+)', re.IGNORECASE
)
PATTERN_ISSERVER = re.compile(
    r'(?:^|\s)/ISSERVER\s+(?P<quoted>"[^"]+"|\S+)', re.IGNORECASE
)
# trailing \s+ is load-bearing — prevents matching /SQUASH, /SQLDBG, /SQLOUTPUT etc.
PATTERN_SQL = re.compile(
    r'(?:^|\s)/SQ(?:L)?\s+(?P<quoted>"[^"]+"|\S+)', re.IGNORECASE
)

_PATTERNS = (PATTERN_FILE, PATTERN_ISSERVER, PATTERN_SQL)


# Tagged union ParseResult --------------------------------------------- #


@dataclass(frozen=True)
class Hit:
    """A recognised flag was found and the path captured (already normalised)."""

    path: str


@dataclass(frozen=True)
class Unparseable:
    """The command was rejected — reason explains why."""

    reason: str


ParseResult = Hit | Unparseable


# Public entry point --------------------------------------------------- #


def parse_ssis_command(command: str) -> ParseResult:
    """Parse ``command`` into ``Hit(path)`` or ``Unparseable(reason)``.

    Guards (D-10) fire BEFORE any regex match — an env-var or configfile
    command is never rewritten, even if a /FILE flag is present.

    The captured path is POSIX-normalised (D-13) and has a ``.dtsx``
    suffix appended if the basename lacks one (D-12, case-insensitive).
    """
    if "%" in command:
        return Unparseable(reason="env var present")
    # /CONFIGFILE detection — case-insensitive bare word boundary.
    if re.search(r"(?i)/CONFIGFILE\b", command):
        return Unparseable(reason="config file present")

    for pattern in _PATTERNS:
        match = pattern.search(command)
        if match:
            captured = match.group("quoted")
            # Strip surrounding double-quotes if present.
            if captured.startswith('"') and captured.endswith('"'):
                captured = captured[1:-1]
            return Hit(path=_normalise_dtsx_path(captured))

    return Unparseable(reason="no /FILE, /ISSERVER, or /SQL flag found")


def _normalise_dtsx_path(raw: str) -> str:
    """POSIX-normalise (D-13) and append lowercase ``.dtsx`` if missing (D-12)."""
    posix = _posix(raw)
    # Determine basename; if it has no .dtsx suffix (case-insensitive), append.
    # Use rsplit so we don't have to import pathlib just for the basename.
    basename = posix.rsplit("/", 1)[-1] if "/" in posix else posix
    if not basename.lower().endswith(".dtsx"):
        return posix + ".dtsx"
    return posix

"""Table-driven coverage for ``msb_ssis2sql.agent.command_parser.parse_ssis_command``.

Backs D-9 / D-10 / D-12 / D-13 of plan-final-agent-step-procs.md and
AC-5..AC-7, AC-9, AC-13, AC-17. The fixture file
``tests/fixtures/agent_step_commands.json`` enumerates 12 command-line
shapes that the parser must classify; each case asserts the returned
``ParseResult`` discriminator plus either the normalised path (for ``Hit``)
or the reason string (for ``Unparseable``).

Module under test does not yet exist — failures here will be
``ImportError`` until ``msb_ssis2sql/agent/command_parser.py`` ships.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_CASES_PATH = Path(__file__).parent / "fixtures" / "agent_step_commands.json"


def _load_cases() -> list[dict[str, str]]:
    data = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


_CASES = _load_cases()


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_parse_ssis_command_table(case: dict[str, str]) -> None:
    """Every row in the fixture matches its declared expected outcome."""
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command(case["command"])
    cls_name = type(result).__name__

    if case["expected_outcome"] == "Hit":
        assert cls_name == "Hit", (
            f"case {case['id']}: expected Hit, got {cls_name}: {result!r}"
        )
        # All Hit variants expose .path
        assert result.path == case["expected_path"], (
            f"case {case['id']}: expected path {case['expected_path']!r}, "
            f"got {result.path!r}"
        )
    else:
        assert cls_name == "Unparseable", (
            f"case {case['id']}: expected Unparseable, got {cls_name}: {result!r}"
        )
        assert result.reason == case["expected_reason"], (
            f"case {case['id']}: expected reason {case['expected_reason']!r}, "
            f"got {result.reason!r}"
        )


def test_parse_result_is_a_tagged_union() -> None:
    """``ParseResult`` should be exposed as a discriminated type — either a
    Union/sum type with ``Hit`` and ``Unparseable`` variants. Test asserts
    both variants are importable from the parser module.
    """
    from msb_ssis2sql.agent.command_parser import Hit, Unparseable

    hit = Hit(path="foo.dtsx")
    assert hit.path == "foo.dtsx"

    bad = Unparseable(reason="env var present")
    assert bad.reason == "env var present"


def test_parse_preserves_dtsx_uppercase_suffix() -> None:
    """D-12: existing ``.DTSX`` suffix is preserved (case unchanged) so the
    audit-field ``dtsx_source`` keeps the operator's original casing.
    Lowercase ``.dtsx`` is only appended when no suffix is present.
    """
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command('DTExec /FILE "\\foo\\bar.DTSX"')
    assert type(result).__name__ == "Hit"
    assert result.path == "/foo/bar.DTSX"


def test_parse_appends_lowercase_dtsx_when_missing() -> None:
    """D-12: append ``.dtsx`` lowercase when the basename has no .dtsx suffix."""
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command('DTExec /SQ "\\Pkgs\\NightlyLoad"')
    assert type(result).__name__ == "Hit"
    assert result.path.endswith(".dtsx")
    # Body before the suffix is untouched.
    assert result.path == "/Pkgs/NightlyLoad.dtsx"


def test_parse_posix_normalises_unc_backslashes() -> None:
    """D-13: backslash-separated paths are POSIX-normalised before return.

    Cross-OS contract — manifest entries are POSIX (T-1); resolver compares
    apples to apples.
    """
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command(
        'DTExec /FILE "C:\\etl\\fact\\nightly_load.dtsx"'
    )
    assert type(result).__name__ == "Hit"
    assert "\\" not in result.path
    assert result.path == "C:/etl/fact/nightly_load.dtsx"


def test_parse_squash_does_not_match_sq_prefix() -> None:
    """L-2 negative — the load-bearing trailing ``\\s+`` in the /SQ regex
    must NOT match ``/SQUASH``."""
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command("DTExec /SQUASH x")
    assert type(result).__name__ == "Unparseable"
    assert result.reason == "no /FILE, /ISSERVER, or /SQL flag found"


def test_parse_sqldbg_falls_through_to_inner_file_flag() -> None:
    """``/SQLDBG`` is a real dtexec diagnostic flag and must NOT match the
    /SQ prefix (the trailing ``\\s+`` separates them). Any inner ``/F`` /
    ``/FILE`` flag still wins.
    """
    from msb_ssis2sql.agent.command_parser import parse_ssis_command

    result = parse_ssis_command('dtexec /SQLDBG /F "foo.dtsx"')
    assert type(result).__name__ == "Hit"
    assert result.path == "foo.dtsx"

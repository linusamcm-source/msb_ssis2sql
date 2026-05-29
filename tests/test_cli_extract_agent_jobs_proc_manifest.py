"""AC-4: ``extract-agent-jobs --proc-manifest <path>`` exit-code coverage.

Per plan-final-agent-step-procs.md AC-4:
* Missing flag → run as today (D-7) — no error.
* Invalid JSON → exit non-zero with ``manifest invalid: <reason>``.
* Unknown ``version`` → exit non-zero with
  ``manifest version unsupported: <n>``.

The ``--proc-manifest`` flag does not yet exist on the
``extract-agent-jobs`` subparser. Tests will fail at argparse parse-time
(``SystemExit``) until T-5 wires the flag, then will fail on the message
contract until the handler routes ``ManifestError`` through.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "agent_manifest"


def _run_main_capturing_rc(argv: list[str]) -> int:
    """Run ``main`` and return its exit code.

    argparse raises ``SystemExit`` for unrecognised flags; the handler
    returns an int once T-5 wires the flag. Both paths are ``rc != 0``.
    """
    try:
        rc = main(argv)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 2
    return rc


def test_proc_manifest_invalid_json_exits_with_manifest_invalid_prefix(
    tmp_path, monkeypatch, capsys
) -> None:
    """Invalid JSON manifest → non-zero exit, stderr begins with
    ``manifest invalid:`` (AC-4)."""
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")
    # No pyodbc patch needed — the manifest load happens before any DB call.

    rc = _run_main_capturing_rc([
        "extract-agent-jobs",
        "--out", str(tmp_path / "jobs"),
        "--proc-manifest", str(FIXTURES / "invalid_json.json"),
    ])
    err = capsys.readouterr().err
    assert rc != 0, (rc, err)
    assert "manifest invalid:" in err, err


def test_proc_manifest_wrong_version_exits_with_version_unsupported_prefix(
    tmp_path, monkeypatch, capsys
) -> None:
    """Unknown ``version`` → non-zero exit, stderr begins with
    ``manifest version unsupported:`` (AC-4)."""
    monkeypatch.setenv("MSDB_DSN", "Driver={ODBC};Server=fake;")

    rc = _run_main_capturing_rc([
        "extract-agent-jobs",
        "--out", str(tmp_path / "jobs"),
        "--proc-manifest", str(FIXTURES / "wrong_version.json"),
    ])
    err = capsys.readouterr().err
    assert rc != 0, (rc, err)
    assert "manifest version unsupported:" in err, err
    assert "99" in err, err


def test_proc_manifest_flag_is_advertised_in_help(capsys) -> None:
    """T-5: ``--proc-manifest`` shows up in ``--help`` output for the
    ``extract-agent-jobs`` subcommand."""
    import pytest

    with pytest.raises(SystemExit):
        main(["extract-agent-jobs", "--help"])
    out = capsys.readouterr().out
    assert "--proc-manifest" in out

"""Tests for msb_ssis2sql.web — the textual-serve wrapper.

The Server.serve() call binds a socket and blocks, so it is monkeypatched out.
The tests cover argparse defaults, custom --host/--port routing, and the
ImportError fallback when textual-serve is not installed.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from msb_ssis2sql import web


def test_build_parser_defaults() -> None:
    args = web.build_parser().parse_args([])
    assert args.host == "localhost"
    assert args.port == 8000


def test_build_parser_custom_host_port() -> None:
    args = web.build_parser().parse_args(["--host", "0.0.0.0", "--port", "9000"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_main_passes_args_to_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() must hand the parsed host/port to textual_serve.Server and call serve()."""
    captured: dict[str, object] = {}

    class _FakeServer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            captured["serve_called"] = False

        def serve(self) -> None:
            captured["serve_called"] = True

    fake_module = SimpleNamespace(Server=_FakeServer)
    monkeypatch.setitem(sys.modules, "textual_serve.server", fake_module)
    # textual_serve.server is imported lazily inside main(), so the stub above is enough.

    rc = web.main(["--host", "127.0.0.1", "--port", "8123"])

    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8123
    assert captured["title"] == "msb_ssis2sql"
    assert "msb_ssis2sql.tui" in str(captured["command"])
    assert captured["serve_called"] is True


def test_main_reports_missing_textual_serve(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Missing textual-serve should produce exit code 2 and a helpful message."""
    import builtins

    real_import = builtins.__import__

    def _raise_for_textual_serve(name: str, *args: object, **kwargs: object) -> object:
        if name == "textual_serve.server":
            raise ImportError("No module named 'textual_serve'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raise_for_textual_serve)

    with pytest.raises(SystemExit) as excinfo:
        web.main([])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "textual-serve" in err
    assert "uv sync" in err

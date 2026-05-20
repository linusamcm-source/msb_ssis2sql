"""Serve the ssis2sql Textual TUI as a browser web app via textual-serve.

``textual-serve`` runs the existing TUI inside a subprocess and bridges it to an
xterm.js terminal over WebSocket; nothing about the TUI itself changes.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssis2sql-web",
        description="Serve the ssis2sql Textual TUI in a web browser.",
    )
    parser.add_argument(
        "--host", default="localhost",
        help="bind host (default: localhost)",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="bind port (default: 8000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from textual_serve.server import Server
    except ImportError as exc:
        print(
            "ssis2sql-web: textual-serve is not installed. "
            "Run 'just install' (or 'uv sync').",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    # Re-launch the TUI with the same interpreter so .venv stays in scope.
    Server(
        command=f"{sys.executable} -m ssis2sql.tui",
        host=args.host,
        port=args.port,
        title="ssis2sql",
    ).serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

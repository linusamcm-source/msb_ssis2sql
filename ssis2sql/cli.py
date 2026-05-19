"""Command-line interface: ``ssis2sql convert``, ``ssis2sql inspect``, and ``ssis2sql convert-tree``."""
from __future__ import annotations

import argparse
import pathlib
import sys

from .batch import convert_tree
from .errors import Ssis2SqlError
from .generator import ConvertOptions, convert_package
from .observability import configure_logging
from .parser import parse_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssis2sql",
        description="Convert SSIS (.dtsx) data-flow transformations into consolidated T-SQL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="-v: info-level logging; -vv: trace every call",
    )

    convert = sub.add_parser(
        "convert", parents=[common], help="convert a .dtsx package to T-SQL"
    )
    convert.add_argument("dtsx", help="path to the .dtsx file")
    convert.add_argument("-o", "--output", help="write the SQL here (default: stdout)")
    convert.add_argument(
        "--procedure",
        metavar="NAME",
        help="wrap the output in CREATE OR ALTER PROCEDURE NAME",
    )
    convert.add_argument("--no-header", action="store_true", help="omit the header comment block")
    convert.add_argument("--quiet", action="store_true", help="do not print warnings to stderr")

    inspect = sub.add_parser(
        "inspect", parents=[common], help="print the parsed component graph and exit"
    )
    inspect.add_argument("dtsx", help="path to the .dtsx file")

    tree = sub.add_parser(
        "convert-tree",
        parents=[common],
        help="Recursively convert a directory of .dtsx files into mirrored .sql files.",
    )
    tree.add_argument("input", help="Parent directory scanned recursively for .dtsx files.")
    tree.add_argument("output", help="Directory the mirrored .sql tree is written into.")
    tree.add_argument("--procedure", metavar="NAME", help="Wrap each script in a stored procedure.")
    tree.add_argument("--no-header", action="store_true", help="Omit the generated header.")

    return parser


def _cmd_convert(args) -> int:
    options = ConvertOptions(
        wrap_in_procedure=bool(args.procedure),
        procedure_name=args.procedure or "usp_Migrated_Package",
        include_header=not args.no_header,
    )
    result = convert_package(parse_file(pathlib.Path(args.dtsx)), options)

    if args.output:
        output_path = pathlib.Path(args.output)
        output_path.write_text(result.sql, encoding="utf-8")
        print(f"ssis2sql: wrote {output_path}", file=sys.stderr)
    else:
        sys.stdout.write(result.sql)

    if result.warnings and not args.quiet:
        print(f"\nssis2sql: {len(result.warnings)} warning(s):", file=sys.stderr)
        for warning in result.warnings:
            print(f"  ! {warning}", file=sys.stderr)
    return 0


def _cmd_inspect(args) -> int:
    package = parse_file(pathlib.Path(args.dtsx))
    print(f"Package: {package.name}")
    print(f"  source file        : {package.source_path}")
    print(f"  connection managers: {len(package.connection_managers)}")
    for cm in package.connection_managers:
        print(f"    - {cm.name}  [{cm.creation_name or 'unknown'}]")
    print(f"  variables          : {len(package.variables)}")
    for var in package.variables:
        print(f"    - {var.qualified} = {var.value!r}")
    if package.exec_sql_tasks:
        print(f"  execute SQL tasks  : {len(package.exec_sql_tasks)}")
    print(f"  data flow tasks    : {len(package.data_flows)}")
    for data_flow in package.data_flows:
        print(f"\n  Data Flow: {data_flow.name}")
        for component in data_flow.components:
            in_cols = sum(len(p.columns) for p in component.inputs)
            out_cols = sum(len(p.columns) for p in component.outputs)
            print(
                f"    [{component.kind.value:<18}] {component.name}"
                f"   (input cols: {in_cols}, output cols: {out_cols})"
            )
        for path in data_flow.paths:
            print(f"    path: {path.name or path.ref_id}")
    return 0


def _cmd_convert_tree(args) -> int:
    options = ConvertOptions(
        wrap_in_procedure=bool(args.procedure),
        procedure_name=args.procedure or "usp_Migrated_Package",
        include_header=not args.no_header,
    )
    result = convert_tree(pathlib.Path(args.input), pathlib.Path(args.output), options)
    for outcome in result.outcomes:
        if outcome.ok:
            print(f"converted {outcome.source} -> {outcome.destination}")
        else:
            print(f"failed    {outcome.source}: {outcome.error}", file=sys.stderr)
    print(f"converted {result.converted}, failed {result.failed}")
    return 1 if result.failed > 0 else 0


def _log_level(verbosity: int) -> str:
    """Map ``-v`` occurrences to a loguru level: 0 quiet, 1 info, 2+ trace."""
    return {0: "WARNING", 1: "INFO"}.get(verbosity, "DEBUG")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(level=_log_level(getattr(args, "verbose", 0)))
    try:
        if args.command == "convert":
            return _cmd_convert(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        if args.command == "convert-tree":
            return _cmd_convert_tree(args)
    except Ssis2SqlError as exc:
        print(f"ssis2sql: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # parse_file() already converts input-file failures to ParseError; this
        # catches a failed --output write, the one raw OSError that still escapes.
        print(f"ssis2sql: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Recursively convert a directory tree of .dtsx packages into mirrored .sql files."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ._naming import resolve_collisions, resolve_procedure_name, sanitise
from .control_graph import ControlFlowGraph
from .errors import GraphError
from .generator import ConversionResult, ConvertOptions, convert_file
from .observability import logged, logger
from .parser import parse_file

# Visual Studio build / intermediate dirs — not source packages.
_SKIP_DIRS = frozenset({"bin", "obj"})


@dataclass
class FileOutcome:
    """The result of converting a single .dtsx file."""

    source: Path
    destination: Path
    ok: bool
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    procedure_name: str = ""


@dataclass
class BatchResult:
    """Aggregate result of a convert_tree run: a list of per-file outcomes."""

    outcomes: list[FileOutcome] = field(default_factory=list)

    @property
    def converted(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


@logged
def convert_tree(
    input_root: str | Path,
    output_root: str | Path,
    options: ConvertOptions | None = None,
    no_orchestrator: bool = False,
) -> BatchResult:
    """Convert every .dtsx under ``input_root`` into ``output_root``.

    Each ``.dtsx`` is wrapped in a stored procedure. If a directory contains
    ``main.dtsx`` with ExecutePackageTasks, an orchestrator proc is also emitted
    in topological order. Directories without ``main.dtsx`` get a synthesised
    orchestrator that EXECs children alphabetically.
    """
    input_root = Path(input_root)
    output_root = Path(output_root).resolve()
    if not input_root.is_dir():
        raise NotADirectoryError(f"input is not a directory: {input_root}")

    base_options = options or ConvertOptions()
    result = BatchResult()

    # Collect all .dtsx files, skipping bin/obj dirs.
    all_dtsx: list[Path] = []
    for src in sorted(input_root.rglob("*.dtsx")):
        rel = src.relative_to(input_root)
        if _SKIP_DIRS.intersection(rel.parts):
            continue
        all_dtsx.append(src)

    # Group by parent directory.
    by_dir: dict[Path, list[Path]] = defaultdict(list)
    for src in all_dtsx:
        by_dir[src.parent].append(src)

    # All stems in each directory for collision resolution.
    batch_warnings: list[tuple[str, str]] = []  # (source_path_str, warning_text)

    for dir_path, dir_files in sorted(by_dir.items()):
        rel_dir = dir_path.relative_to(input_root)

        # Resolve proc-name collisions per directory.
        stems = [f.stem for f in dir_files]
        collision_map = resolve_collisions(stems)  # {stem: resolved_sanitised}

        # Identify main.dtsx (case-insensitive).
        main_file: Path | None = None
        for f in dir_files:
            if f.name.lower() == "main.dtsx":
                main_file = f
                break

        # Order: main first, then siblings sorted.
        ordered = []
        if main_file is not None:
            ordered.append(main_file)
        for f in sorted(dir_files):
            if f != main_file:
                ordered.append(f)

        # Convert each file with wrap_in_procedure=True.
        dir_outcomes: list[FileOutcome] = []
        proc_name_by_stem: dict[str, str] = {}
        cached_main_pkg = None  # M-8: avoid double-parsing main.dtsx

        for src in ordered:
            rel = src.relative_to(input_root)
            dst = output_root / rel.with_suffix(".sql")
            resolved = dst.resolve()
            if not resolved.is_relative_to(output_root):
                error_msg = f"output path escapes output root: {resolved}"
                logger.warning("skipping {} — {}", rel, error_msg)
                result.outcomes.append(FileOutcome(src, dst, ok=False, error=error_msg))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)

            # Build the sanitised proc-name, honouring the collision suffix.
            sanitised_stem = collision_map.get(src.stem, sanitise(src.stem))
            rel_dir_str = str(rel_dir)
            if rel_dir_str in ("", "."):
                proc_name = f"usp_{sanitised_stem}"
            else:
                dir_part = sanitise(rel_dir_str.replace("/", "_").replace("\\", "_"))
                proc_name = f"usp_{dir_part}_{sanitised_stem}" if dir_part else f"usp_{sanitised_stem}"
            proc_name_by_stem[src.stem] = proc_name

            wrap_opts = ConvertOptions(
                wrap_in_procedure=True,
                procedure_name=proc_name,
                include_header=base_options.include_header,
            )
            try:
                conversion: ConversionResult = convert_file(src, wrap_opts)
                dst.write_text(conversion.sql, encoding="utf-8")
                outcome = FileOutcome(
                    src, dst, ok=True,
                    warnings=list(conversion.warnings),
                    procedure_name=proc_name,
                )
                if src == main_file and conversion.package is not None:
                    cached_main_pkg = conversion.package
                dir_outcomes.append(outcome)
                result.outcomes.append(outcome)
                logger.info("converted {} -> {}", rel, dst)
            except Exception as exc:  # noqa: BLE001
                outcome = FileOutcome(src, dst, ok=False, error=str(exc))
                dir_outcomes.append(outcome)
                result.outcomes.append(outcome)
                logger.warning("failed to convert {}: {}", rel, exc)

        # Emit orchestrator (unless --no-orchestrator).
        if not no_orchestrator:
            _emit_orchestrator(
                dir_path=dir_path,
                rel_dir=rel_dir,
                dir_files=dir_files,
                main_file=main_file,
                proc_name_by_stem=proc_name_by_stem,
                output_root=output_root,
                dir_outcomes=dir_outcomes,
                result=result,
                base_options=base_options,
                cached_main_pkg=cached_main_pkg,
            )

        # Collect all warnings for batch log.
        for outcome in dir_outcomes:
            for w in outcome.warnings:
                batch_warnings.append((str(outcome.source), w))

    # Write _batch_warnings.log — always emitted; zero warnings = zero-byte file.
    log_path = output_root / "_batch_warnings.log"
    output_root.mkdir(parents=True, exist_ok=True)
    sorted_warnings = sorted(batch_warnings)
    log_path.write_text(
        "\n".join(f"{src}: {w}" for src, w in sorted_warnings) + ("\n" if sorted_warnings else ""),
        encoding="utf-8",
    )

    return result


def _emit_orchestrator(
    *,
    dir_path: Path,
    rel_dir: Path,
    dir_files: list[Path],
    main_file: Path | None,
    proc_name_by_stem: dict[str, str],
    output_root: Path,
    dir_outcomes: list[FileOutcome],
    result: BatchResult,
    base_options: ConvertOptions,
    cached_main_pkg=None,
) -> list[tuple[str, str]]:
    """Emit the orchestrator SQL for a directory. Returns (src_path, warning) pairs."""
    warnings_out: list[tuple[str, str]] = []
    rel_dir_str = str(rel_dir)

    if main_file is not None:
        # Use cached parse result from the conversion pass to avoid double-parsing (M-8).
        if cached_main_pkg is not None:
            main_pkg = cached_main_pkg
        else:
            try:
                main_pkg = parse_file(main_file)
            except Exception as exc:  # noqa: BLE001
                warnings_out.append((str(main_file), f"main.dtsx parse failed: {exc!r}; orchestrator skipped"))
                return warnings_out

        epts = main_pkg.execute_package_tasks
        if not epts:
            # Zero EPTs: main's own data flow is its proc body. No orchestrator.
            return warnings_out

        # Check for nested EPTs in children (warn but don't block).
        for src in dir_files:
            if src == main_file:
                continue
            try:
                child_pkg = parse_file(src)
                if child_pkg.execute_package_tasks:
                    w = f"nested orchestration: {src.name} itself contains ExecutePackageTasks"
                    warnings_out.append((str(main_file), w))
                    _add_warning_to_dir_outcomes(dir_outcomes, w)
            except Exception:  # noqa: BLE001
                pass

        # Get topological order of EPTs.
        try:
            graph = ControlFlowGraph(main_pkg)
            ordered_epts = graph.topological_order()
            for w in graph.warnings:
                warnings_out.append((str(main_file), w))
                _add_warning_to_dir_outcomes(dir_outcomes, w)
        except GraphError as exc:
            # Pinned format from plan-final.md Decisions.
            edge_msg = str(exc)
            # Extract from/to from the GraphError message.
            m = re.search(r"'([^']+)' -> '([^']+)'", edge_msg)
            if m:
                from_r, to_r = m.group(1), m.group(2)
                w = (
                    f"main orchestrator: cycle detected on edge {from_r} -> {to_r}; "
                    f"falling back to declaration order"
                )
            else:
                w = "main orchestrator: cycle detected on edge ? -> ?; falling back to declaration order"
            warnings_out.append((str(main_file), w))
            _add_warning_to_dir_outcomes(dir_outcomes, w)
            ordered_epts = epts  # declaration order fallback

        # Warn about dangling refs (EPT package names not in dir_files).
        dir_file_names = {f.name.lower() for f in dir_files}
        for ept in ordered_epts:
            pkg_name = ept.package_name
            if not pkg_name:
                continue
            # Outside-dir check.
            if ".." in pkg_name or pkg_name.startswith("/"):
                w = f"outside-dir child reference rejected: {pkg_name!r}"
                warnings_out.append((str(main_file), w))
                _add_warning_to_dir_outcomes(dir_outcomes, w)
                continue
            if pkg_name.lower() not in dir_file_names:
                w = f"missing child: {pkg_name!r} referenced by EPT but not found in directory"
                warnings_out.append((str(main_file), w))
                _add_warning_to_dir_outcomes(dir_outcomes, w)

        # Build EXEC order: EPTs that reference files present in the directory.
        exec_lines: list[str] = []
        for ept in ordered_epts:
            pkg_name = ept.package_name
            if not pkg_name:
                continue
            if ".." in pkg_name or pkg_name.startswith("/"):
                continue
            # Derive stem from package_name.
            stem = Path(pkg_name).stem
            if stem in proc_name_by_stem:
                exec_lines.append(f"    EXEC {proc_name_by_stem[stem]};")

        main_proc_name = proc_name_by_stem.get(main_file.stem, resolve_procedure_name(rel_dir, main_file.stem))
        orch_proc_name = f"{main_proc_name}_orchestrator"
        orch_sql = _render_orchestrator_proc(orch_proc_name, exec_lines)

        dst = output_root / rel_dir / f"{orch_proc_name}.sql"
        if dst.resolve().is_relative_to(output_root):
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(orch_sql, encoding="utf-8")

    else:
        # Synthesize orchestrator: no main.dtsx present.
        rel_dir_sanitised = sanitise(rel_dir_str.replace("/", "_").replace("\\", "_")) if rel_dir_str not in ("", ".") else ""
        if rel_dir_sanitised:
            synth_proc_name = f"usp_{rel_dir_sanitised}_main"
        else:
            synth_proc_name = "usp_main"

        w = f"no main.dtsx found in {rel_dir_str!r}; synthesised orchestrator {synth_proc_name!r}"
        warnings_out.append((str(rel_dir), w))
        _add_warning_to_dir_outcomes(dir_outcomes, w)

        # EXECs sorted alphabetically by proc-name.
        child_procs = sorted(
            proc_name_by_stem[f.stem]
            for f in dir_files
            if f.stem in proc_name_by_stem
        )
        exec_lines = [f"    EXEC {p};" for p in child_procs]
        orch_sql = _render_orchestrator_proc(synth_proc_name, exec_lines)

        dst = output_root / rel_dir / f"{synth_proc_name}.sql"
        if not dst.resolve().is_relative_to(output_root):
            return warnings_out
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(orch_sql, encoding="utf-8")

    return warnings_out


def _render_orchestrator_proc(proc_name: str, exec_lines: list[str]) -> str:
    body = "\n".join(exec_lines) if exec_lines else "    -- no child procedures"
    return (
        f"CREATE OR ALTER PROCEDURE {proc_name}\n"
        f"AS\n"
        f"BEGIN\n"
        f"    SET NOCOUNT ON;\n\n"
        f"{body}\n"
        f"END;\n"
        f"GO\n"
    )


def _add_warning_to_dir_outcomes(dir_outcomes: list[FileOutcome], warning: str) -> None:
    """Append warning to the first successful outcome in the directory (main or first)."""
    for o in dir_outcomes:
        if o.ok:
            o.warnings.append(warning)
            return
    # If no successful outcome, append to any outcome.
    if dir_outcomes:
        dir_outcomes[0].warnings.append(warning)



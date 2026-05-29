"""Recursively convert a directory tree of .dtsx packages into mirrored .sql files."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ._naming import resolve_collisions, resolve_procedure_name, sanitise
from .control_graph import ControlFlowGraph
from .errors import GraphError
from .generator import ConvertOptions, convert_file, convert_package
from .model import Package
from .observability import logged, logger
from .parser import parse_file
from .project import load_project
from .util import _posix, decode_package_name


def _decoded_stem(path: Path) -> str:
    """Disk-file stem with %xx percent-escapes decoded — matches EPT refs."""
    return decode_package_name(path.stem)


def _clean_sql_stems(dir_files: list[Path]) -> dict[str, str]:
    """Map each decoded stem -> a unique, whitespace-free output ``.sql`` stem.

    Whitespace runs in a ``.dtsx`` name (literal spaces or decoded ``%20``)
    collapse to single underscores so the emitted ``.sql`` files have clean
    names. Case is preserved (unlike proc-name sanitising). Collisions are
    de-duplicated case-insensitively with ``_2`` / ``_3`` suffixes in
    decoded-stem sort order, matching the proc-name collision algorithm.
    """
    result: dict[str, str] = {}
    seen: dict[str, int] = {}
    for src in sorted(dir_files, key=_decoded_stem):
        decoded = _decoded_stem(src)
        base = re.sub(r"\s+", "_", decoded) or "package"
        key = base.lower()
        count = seen.get(key, 0)
        seen[key] = count + 1
        result[decoded] = base if count == 0 else f"{base}_{count + 1}"
    return result

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
        rel_dir_str = str(rel_dir)

        # An expanded SSIS project (a directory with @Project.manifest) supplies
        # project parameters + shared connection managers to every package in it.
        project = load_project(dir_path)
        if project is not None:
            logger.info(
                "directory {} is an expanded project {!r}: protection={!r}, "
                "{} param(s), {} project connection(s)",
                rel_dir_str, project.name, project.protection_level,
                len(project.parameters), len(project.connection_managers),
            )
            if project.is_password_encrypted:
                batch_warnings.append((
                    str(dir_path),
                    f"project {project.name!r} protection level "
                    f"{project.protection_level!r}: parameter values and sensitive "
                    f"connection-string parts are encrypted and not exported",
                ))

        # Resolve proc-name collisions per directory. Decode stems first so
        # '%20'-encoded disk names share a collision namespace with EPT refs.
        stems = [_decoded_stem(f) for f in dir_files]
        collision_map = resolve_collisions(stems)  # {decoded_stem: resolved_sanitised}

        # Identify main.dtsx (case-insensitive).
        main_file: Path | None = None
        for f in dir_files:
            if f.name.lower() == "main.dtsx":
                main_file = f
                break

        # Pre-compute proc names for every file in the directory. The formula
        # is deterministic so we can build the full map before any conversion
        # runs; this lets the collapse decision (T-2) and the main-first
        # emission order both run cleanly.
        def _resolve_proc_name(src: Path) -> str:
            canonical_stem = _decoded_stem(src)
            sanitised_stem = collision_map.get(canonical_stem, sanitise(canonical_stem))
            if rel_dir_str in ("", "."):
                return f"usp_{sanitised_stem}"
            dir_part = sanitise(rel_dir_str.replace("/", "_").replace("\\", "_"))
            return f"usp_{dir_part}_{sanitised_stem}" if dir_part else f"usp_{sanitised_stem}"

        proc_name_by_stem: dict[str, str] = {
            _decoded_stem(f): _resolve_proc_name(f) for f in dir_files
        }

        # Whitespace-free, collision-safe output .sql filenames (per directory).
        sql_name_by_stem = _clean_sql_stems(dir_files)

        def _sql_dest(src: Path) -> Path:
            rel_parent = src.relative_to(input_root).parent
            return output_root / rel_parent / f"{sql_name_by_stem[_decoded_stem(src)]}.sql"

        # T-4: eagerly parse main.dtsx so the per-file loop and the collapse
        # decision share one parse. On parse failure, record the outcome now
        # (preserving main-first outcome ordering), skip main in the loop, but
        # continue converting siblings.
        cached_main_pkg: Package | None = None
        main_parse_error: str | None = None
        if main_file is not None:
            try:
                cached_main_pkg = parse_file(main_file)
            except Exception as exc:  # noqa: BLE001
                main_parse_error = f"main.dtsx parse failed: {exc!r}"
                main_dst = _sql_dest(main_file)
                result.outcomes.append(
                    FileOutcome(main_file, main_dst, ok=False, error=main_parse_error)
                )
                logger.warning("main.dtsx parse failed for {}: {!r}", main_file, exc)

        # Cache the helper output once per directory so every downstream path
        # (collapse trial, legacy `_emit_orchestrator`) reuses it. The helper's
        # nested-EPT scan parses every sibling — without this cache it runs up
        # to 3x per directory (trial + legacy emit + …).
        precomputed_exec_lines: tuple[list[str], list[tuple[str, str]]] | None = None
        if main_file is not None and cached_main_pkg is not None:
            precomputed_exec_lines = _build_ordered_exec_lines(
                cached_main_pkg, dir_files, main_file, proc_name_by_stem,
            )

        # T-2: determine collapse. Requires cached_main_pkg AND the full
        # proc_name_by_stem (pre-computed above). D-8 forces collapse=False
        # when --no-orchestrator is set.
        collapse = False
        collapse_exec_lines: list[str] = []
        collapse_warnings: list[tuple[str, str]] = []
        if (
            main_file is not None
            and cached_main_pkg is not None
            and not no_orchestrator
            and not cached_main_pkg.data_flows
            and cached_main_pkg.execute_package_tasks
            and precomputed_exec_lines is not None
        ):
            trial_lines, trial_warnings = precomputed_exec_lines
            if trial_lines:
                collapse = True
                collapse_exec_lines = trial_lines
                collapse_warnings = trial_warnings

        # Per-file conversion. Main first (so outcome ordering preserves the
        # main-is-first invariant), then siblings sorted. Main is routed
        # through convert_package(cached_main_pkg, ...) so the eager parse is
        # reused (T-4b); siblings continue to use convert_file.
        dir_outcomes: list[FileOutcome] = []

        ordered: list[Path] = []
        if main_file is not None and cached_main_pkg is not None:
            ordered.append(main_file)
        for f in sorted(dir_files):
            if f != main_file:
                ordered.append(f)

        for src in ordered:
            rel = src.relative_to(input_root)
            dst = _sql_dest(src)
            resolved = dst.resolve()
            if not resolved.is_relative_to(output_root):
                error_msg = f"output path escapes output root: {resolved}"
                logger.warning("skipping {} — {}", rel, error_msg)
                result.outcomes.append(FileOutcome(src, dst, ok=False, error=error_msg))
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)

            proc_name = proc_name_by_stem[_decoded_stem(src)]

            is_main = src == main_file
            wrap_opts = ConvertOptions(
                wrap_in_procedure=True,
                procedure_name=proc_name,
                include_header=base_options.include_header,
                orchestration_body=collapse_exec_lines if (is_main and collapse) else None,
            )
            try:
                if is_main:
                    # T-4b: reuse the eager parse instead of re-parsing.
                    assert cached_main_pkg is not None
                    conversion = convert_package(cached_main_pkg, wrap_opts, project=project)
                else:
                    conversion = convert_file(src, wrap_opts, project=project)
                dst.write_text(conversion.sql, encoding="utf-8")
                outcome = FileOutcome(
                    src, dst, ok=True,
                    warnings=list(conversion.warnings),
                    procedure_name=proc_name,
                )
                dir_outcomes.append(outcome)
                result.outcomes.append(outcome)
                logger.info("converted {} -> {}", rel, dst)
            except Exception as exc:  # noqa: BLE001
                outcome = FileOutcome(src, dst, ok=False, error=str(exc))
                dir_outcomes.append(outcome)
                result.outcomes.append(outcome)
                logger.warning("failed to convert {}: {}", rel, exc)

        # Attach collapse-path warnings (dangling, outside-dir, nested, cycle)
        # to dir_outcomes so they reach _batch_warnings.log via the same
        # mechanism the legacy path uses.
        if collapse and collapse_warnings:
            for _src_path, warning in collapse_warnings:
                _add_warning_to_dir_outcomes(dir_outcomes, warning)

        # Emit orchestrator:
        # - Skip entirely under --no-orchestrator (D-8).
        # - Skip the main-bearing branch when collapse fired (main.sql already
        #   carries the EXECs).
        # - Skip the main-bearing branch when main parse failed (no main_pkg
        #   to drive the orchestrator); siblings stay converted.
        # - Always run for directories without main.dtsx (synthesised path).
        if not no_orchestrator:
            if main_file is None:
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
                    precomputed_exec_lines=precomputed_exec_lines,
                )
            elif cached_main_pkg is not None and not collapse:
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
                    precomputed_exec_lines=precomputed_exec_lines,
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

    # Write _proc_manifest.json — shared interchange with extract-agent-jobs (T-1).
    # Always emitted, even when zero packages converted (entries: []).
    manifest = {
        "version": 1,
        "input_root": str(input_root.resolve()),
        "entries": sorted(
            [
                {
                    "dtsx": _posix(outcome.source.relative_to(input_root)),
                    "proc": outcome.procedure_name,
                    "out_sql": _posix(outcome.destination.relative_to(output_root)),
                }
                for outcome in result.outcomes
                if outcome.ok and outcome.procedure_name
            ],
            key=lambda e: e["dtsx"],
        ),
    }
    (output_root / "_proc_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return result


def _build_ordered_exec_lines(
    main_pkg: Package,
    dir_files: list[Path],
    main_file: Path,
    proc_name_by_stem: dict[str, str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Build the topologically ordered EXEC body for the orchestrator path.

    Returns ``(exec_lines, warnings)`` where ``exec_lines`` are raw, unindented
    SQL statements (``"EXEC usp_childa;"``) and ``warnings`` are
    ``(source_path, warning_text)`` pairs matching the existing
    ``_emit_orchestrator`` warning shape.

    Owns every caller-side step that lives in the orchestrator emission path:
        * ``ControlFlowGraph`` construction
        * ``topological_order()`` + ``GraphError`` -> declaration-order fallback
          with pinned warning ``"main orchestrator: cycle detected on edge
          {from} -> {to}; falling back to declaration order"``
        * Dangling-ref scan -> ``"missing child: {pkg_name!r} referenced by EPT
          but not found in directory"``
        * Outside-dir filter -> ``"outside-dir child reference rejected:
          {pkg_name!r}"``
        * Nested-EPT child scan -> ``"nested orchestration: {src.name} itself
          contains ExecutePackageTasks"``
        * EXEC line formatting

    Both the collapse path (T-2) and the legacy ``_emit_orchestrator`` call it
    so the warnings and ordering are identical across paths.
    """
    warnings: list[tuple[str, str]] = []
    main_src = str(main_file)
    epts = main_pkg.execute_package_tasks

    # Nested-EPT child scan: warn but don't block.
    for src in dir_files:
        if src == main_file:
            continue
        try:
            child_pkg = parse_file(src)
        except Exception:  # noqa: BLE001 - child parse failure surfaces via convert pass
            continue
        if child_pkg.execute_package_tasks:
            warnings.append(
                (main_src, f"nested orchestration: {src.name} itself contains ExecutePackageTasks")
            )

    # Topological order with declaration-order fallback on cycle.
    try:
        graph = ControlFlowGraph(main_pkg)
        ordered_epts = graph.topological_order()
        for w in graph.warnings:
            warnings.append((main_src, w))
    except GraphError as exc:
        edge_msg = str(exc)
        m = re.search(r"'([^']+)' -> '([^']+)'", edge_msg)
        if m:
            from_r, to_r = m.group(1), m.group(2)
            cycle_warning = (
                f"main orchestrator: cycle detected on edge {from_r} -> {to_r}; "
                f"falling back to declaration order"
            )
        else:
            cycle_warning = (
                "main orchestrator: cycle detected on edge ? -> ?; "
                "falling back to declaration order"
            )
        warnings.append((main_src, cycle_warning))
        ordered_epts = epts  # declaration order fallback

    # Dangling / outside-dir scan. Compare against decoded disk names so
    # '%20'-encoded files match decoded EPT refs.
    dir_file_names = {decode_package_name(f.name).lower() for f in dir_files}
    for ept in ordered_epts:
        pkg_name = ept.package_name
        if not pkg_name:
            continue
        if ".." in pkg_name or pkg_name.startswith("/"):
            warnings.append(
                (main_src, f"outside-dir child reference rejected: {pkg_name!r}")
            )
            continue
        if pkg_name.lower() not in dir_file_names:
            warnings.append(
                (main_src, f"missing child: {pkg_name!r} referenced by EPT but not found in directory")
            )

    # EXEC line formatting (raw, unindented).
    exec_lines: list[str] = []
    for ept in ordered_epts:
        pkg_name = ept.package_name
        if not pkg_name:
            continue
        if ".." in pkg_name or pkg_name.startswith("/"):
            continue
        stem = Path(pkg_name).stem
        if stem in proc_name_by_stem:
            exec_lines.append(f"EXEC {proc_name_by_stem[stem]};")

    return exec_lines, warnings


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
    cached_main_pkg: Package | None = None,
    precomputed_exec_lines: tuple[list[str], list[tuple[str, str]]] | None = None,
) -> list[tuple[str, str]]:
    """Emit the orchestrator SQL for a directory. Returns (src_path, warning) pairs.

    ``precomputed_exec_lines``: when supplied, skip the internal call to
    ``_build_ordered_exec_lines`` and reuse the caller's cached tuple. The
    caller computes this once per directory so the helper's nested-EPT
    sibling parse loop runs at most once per directory.
    """
    warnings_out: list[tuple[str, str]] = []
    rel_dir_str = str(rel_dir)

    if main_file is not None:
        # Caller is expected to have eagerly parsed main.dtsx (T-4). Defensive
        # fallback parse retained for safety.
        if cached_main_pkg is not None:
            main_pkg = cached_main_pkg
        else:
            try:
                main_pkg = parse_file(main_file)
            except Exception as exc:  # noqa: BLE001
                warnings_out.append(
                    (str(main_file), f"main.dtsx parse failed: {exc!r}; orchestrator skipped")
                )
                return warnings_out

        if not main_pkg.execute_package_tasks:
            # Zero EPTs: main's own data flow is its proc body. No orchestrator.
            return warnings_out

        if precomputed_exec_lines is not None:
            exec_lines, helper_warnings = precomputed_exec_lines
        else:
            exec_lines, helper_warnings = _build_ordered_exec_lines(
                main_pkg, dir_files, main_file, proc_name_by_stem,
            )
        for src_path, warning in helper_warnings:
            warnings_out.append((src_path, warning))
            _add_warning_to_dir_outcomes(dir_outcomes, warning)

        # Legacy dual-file path indents EXEC lines by 4 spaces inside the proc.
        indented_exec_lines = [f"    {line}" for line in exec_lines]
        main_proc_name = proc_name_by_stem.get(
            _decoded_stem(main_file),
            resolve_procedure_name(rel_dir, _decoded_stem(main_file)),
        )
        orch_proc_name = f"{main_proc_name}_orchestrator"
        orch_sql = _render_orchestrator_proc(orch_proc_name, indented_exec_lines)

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
            proc_name_by_stem[_decoded_stem(f)]
            for f in dir_files
            if _decoded_stem(f) in proc_name_by_stem
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



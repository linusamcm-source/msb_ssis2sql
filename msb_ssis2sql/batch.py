"""Recursively convert a directory tree of .dtsx packages into mirrored .sql files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .generator import ConversionResult, ConvertOptions, convert_file
from .observability import logged, logger

# Visual Studio build / intermediate dirs — not source packages.
# convert-samples filters bin/ only; batch.py additionally skips obj/ for the same reason.
_SKIP_DIRS = frozenset({"bin", "obj"})


@dataclass
class FileOutcome:
    """The result of converting a single .dtsx file."""

    source: Path
    destination: Path
    ok: bool
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


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
) -> BatchResult:
    """Convert every .dtsx under ``input_root`` into ``output_root``, mirroring the tree.

    Each ``<input_root>/<rel>/<name>.dtsx`` becomes ``<output_root>/<rel>/<name>.sql``.
    A failure on one package is recorded and does not stop the run.
    """
    input_root = Path(input_root)
    output_root = Path(output_root).resolve()
    if not input_root.is_dir():
        raise NotADirectoryError(f"input is not a directory: {input_root}")

    result = BatchResult()
    for src in sorted(input_root.rglob("*.dtsx")):
        rel = src.relative_to(input_root)
        if _SKIP_DIRS.intersection(rel.parts):
            continue
        dst = output_root / rel.with_suffix(".sql")
        # Guard against symlink write-escape: check BEFORE mkdir so no directory is
        # created outside output_root. A symlinked subdir in output_root can cause
        # dst.resolve() to land outside the tree.
        resolved = dst.resolve()
        if not resolved.is_relative_to(output_root):
            error_msg = f"output path escapes output root: {resolved}"
            logger.warning("skipping {} — {}", rel, error_msg)
            result.outcomes.append(FileOutcome(src, dst, ok=False, error=error_msg))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            conversion: ConversionResult = convert_file(src, options)
            dst.write_text(conversion.sql, encoding="utf-8")
            result.outcomes.append(
                FileOutcome(src, dst, ok=True, warnings=list(conversion.warnings))
            )
            logger.info("converted {} -> {}", rel, dst)
        except Exception as exc:  # noqa: BLE001 - one bad package must not abort the run
            result.outcomes.append(FileOutcome(src, dst, ok=False, error=str(exc)))
            logger.warning("failed to convert {}: {}", rel, exc)
    return result

"""Manifest reader for the agent-step → converted-proc rewriting interchange.

Loads ``_proc_manifest.json`` (emitted by ``convert_tree`` per T-1) and
resolves a parsed SSIS command path to either a stored-procedure name
(Hit), a clean miss (Miss), or a basename collision (Ambiguous) per the
D-3 three-pass matcher.

Pure-data module with no side effects beyond a single ``read_text``.
Frozen dataclasses keep the loaded manifest hashable and unmodifiable.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass

from ..errors import Ssis2SqlError

# SEC-H1 — proc names must mirror `_naming.sanitise` output (lowercase,
# usp_ prefix, alnum+underscore only). Anchored end-to-end via fullmatch.
_PROC_RE = re.compile(r"usp_[a-z0-9_]+$")
# Path components allowed in dtsx/out_sql relpaths. Conservative ASCII set
# matching what convert_tree emits (POSIX separators only).
_PATH_RE = re.compile(r"^[a-zA-Z0-9_\-./]+$")

# SEC-M2 — bound the manifest file size to defeat OOM on hostile input.
MAX_MANIFEST_BYTES = 16 * 1024 * 1024  # 16 MiB


class ManifestError(Ssis2SqlError):
    """The ``_proc_manifest.json`` could not be loaded or did not validate.

    Message is prefixed with ``invalid: <reason>`` for structural problems
    and ``unsupported version: <n>`` for forward-incompatible schemas.
    """


@dataclass(frozen=True)
class ManifestEntry:
    """One row of the manifest — POSIX dtsx relpath, proc name, out_sql relpath."""

    dtsx: str
    proc: str
    out_sql: str


@dataclass(frozen=True)
class Manifest:
    """Loaded manifest: version, input_root absolute path, and frozen entries."""

    version: int
    input_root: str
    entries: tuple[ManifestEntry, ...]


# Tagged union ResolveResult variants ---------------------------------- #


@dataclass(frozen=True)
class Hit:
    """The parsed command path resolved to exactly one manifest entry."""

    proc: str
    dtsx_source: str


@dataclass(frozen=True)
class Miss:
    """The parsed command path did not match any manifest entry."""


@dataclass(frozen=True)
class Ambiguous:
    """Multiple manifest entries share the basename — the resolver cannot
    safely pick one. ``candidates`` is the list of colliding entries.
    """

    candidates: tuple[ManifestEntry, ...]


ResolveResult = Hit | Miss | Ambiguous


# Loader --------------------------------------------------------------- #


def load_manifest(path: pathlib.Path) -> Manifest:
    """Parse ``path`` into a ``Manifest``.

    Raises:
        ManifestError: on unreadable file, invalid JSON, schema violations,
            or unsupported ``version``.
    """
    # SEC-M2 — reject UNC paths and oversized manifests BEFORE read_text.
    path_str = str(path)
    if path_str.startswith(("\\\\", "//")):
        raise ManifestError("invalid: UNC paths not supported")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ManifestError(f"invalid: {exc}") from exc
    if size > MAX_MANIFEST_BYTES:
        raise ManifestError(
            f"invalid: file too large ({size} bytes; max {MAX_MANIFEST_BYTES})"
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"invalid: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("invalid: top-level value is not an object")

    # CODE-M2 — split missing-version from unsupported-version. A missing
    # version key is a structural defect ("invalid:"), not a forward-incompat.
    if "version" not in data:
        raise ManifestError("invalid: version missing")
    version = data["version"]
    if version != 1:
        raise ManifestError(f"unsupported version: {version}")

    input_root = data.get("input_root")
    if not isinstance(input_root, str):
        raise ManifestError("invalid: input_root missing or not a string")

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise ManifestError("invalid: entries missing or not a list")

    entries: list[ManifestEntry] = []
    for idx, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ManifestError(f"invalid: entries[{idx}] is not an object")
        for key in ("dtsx", "proc", "out_sql"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise ManifestError(
                    f"invalid: entries[{idx}].{key} missing or not a non-empty string"
                )
        # SEC-H1 — validate proc against the sanitise-output regex and reject
        # any dtsx/out_sql path that could carry traversal or absolute form.
        proc_value = entry["proc"]
        if not _PROC_RE.fullmatch(proc_value):
            raise ManifestError(
                f"invalid: entry[{idx}].proc does not match usp_[a-z0-9_]+"
            )
        for path_key in ("dtsx", "out_sql"):
            path_value = entry[path_key]
            if not _PATH_RE.fullmatch(path_value):
                raise ManifestError(
                    f"invalid: entry[{idx}].{path_key} contains disallowed characters"
                )
            if ".." in path_value:
                raise ManifestError(
                    f"invalid: entry[{idx}].{path_key} path-traversal"
                )
            if path_value.startswith(("/", "\\")):
                raise ManifestError(
                    f"invalid: entry[{idx}].{path_key} must not be absolute"
                )
        entries.append(
            ManifestEntry(
                dtsx=entry["dtsx"], proc=entry["proc"], out_sql=entry["out_sql"]
            )
        )

    return Manifest(version=1, input_root=input_root, entries=tuple(entries))


# Resolver — D-3 three-pass matcher ------------------------------------ #


def resolve(manifest: Manifest, parsed_path: str) -> ResolveResult:
    """Resolve ``parsed_path`` (already POSIX-normalised) against the manifest.

    Algorithm (D-3):
      1. Exact suffix — ``parsed_path.endswith(entry.dtsx)``: first hit wins.
      2. Basename match (case-insensitive) — if exactly one entry has the
         same ``Path(...).name`` as the parsed path's basename, hit.
      3. Multiple basename hits → Ambiguous(candidates).
      4. Otherwise → Miss.
    """
    # Pass 1: exact suffix match — anchored at a path boundary so
    # ``concat.dtsx`` does not falsely hit ``cat.dtsx`` (CODE-H1).
    for entry in manifest.entries:
        if parsed_path == entry.dtsx or parsed_path.endswith("/" + entry.dtsx):
            return Hit(proc=entry.proc, dtsx_source=entry.dtsx)

    # Pass 2/3: basename comparison, case-insensitive.
    target_basename = pathlib.PurePosixPath(parsed_path).name.lower()
    basename_matches = [
        e for e in manifest.entries
        if pathlib.PurePosixPath(e.dtsx).name.lower() == target_basename
    ]
    if len(basename_matches) == 1:
        match = basename_matches[0]
        return Hit(proc=match.proc, dtsx_source=match.dtsx)
    if len(basename_matches) > 1:
        return Ambiguous(candidates=tuple(basename_matches))

    return Miss()

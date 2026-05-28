"""Unit tests for ``msb_ssis2sql.agent.manifest`` (T-2 module under test).

Backs D-2, D-3, AC-1, AC-3, AC-4, AC-17 of plan-final-agent-step-procs.md.

Module under test does not yet exist — failures here will be
``ImportError`` until ``msb_ssis2sql/agent/manifest.py`` ships.
"""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "agent_manifest"


# ---------------------------------------------------------------- #
# load_manifest — schema validation / version check
# ---------------------------------------------------------------- #

def test_load_valid_manifest_parses_three_entries() -> None:
    """AC-3 — version=1, three string-field entries, sorted by dtsx."""
    from msb_ssis2sql.agent.manifest import load_manifest

    manifest = load_manifest(FIXTURES / "valid.json")
    assert manifest.version == 1
    assert manifest.input_root == "/srv/etl/src"
    assert len(manifest.entries) == 3
    # Sorted by dtsx ascending (case-sensitive).
    dtsx_in_order = [e.dtsx for e in manifest.entries]
    assert dtsx_in_order == sorted(dtsx_in_order), dtsx_in_order
    # Each entry exposes exactly the three string fields per D-2.
    for entry in manifest.entries:
        assert isinstance(entry.dtsx, str) and entry.dtsx
        assert isinstance(entry.proc, str) and entry.proc.startswith("usp_")
        assert isinstance(entry.out_sql, str) and entry.out_sql


def test_load_invalid_json_raises_manifest_error_invalid() -> None:
    """AC-4: invalid JSON surfaces a categorised ``ManifestError`` whose
    message begins with ``invalid: ``."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    with pytest.raises(ManifestError) as excinfo:
        load_manifest(FIXTURES / "invalid_json.json")
    assert str(excinfo.value).startswith("invalid:"), str(excinfo.value)


def test_load_unsupported_version_raises_manifest_error_version() -> None:
    """AC-4: unknown ``version`` surfaces a ``ManifestError`` whose message
    begins with ``unsupported version: ``."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    with pytest.raises(ManifestError) as excinfo:
        load_manifest(FIXTURES / "wrong_version.json")
    msg = str(excinfo.value)
    assert msg.startswith("unsupported version:"), msg
    assert "99" in msg, msg


# ---------------------------------------------------------------- #
# resolve — three-pass matcher per D-3
# ---------------------------------------------------------------- #

def _three_entry_manifest():
    """Returns a Manifest with three POSIX entries for the resolver tests."""
    from msb_ssis2sql.agent.manifest import load_manifest

    return load_manifest(FIXTURES / "valid.json")


def test_resolve_exact_suffix_match_returns_hit() -> None:
    """D-3 pass 1: ``endswith(dtsx_relpath)`` returns Hit."""
    from msb_ssis2sql.agent.manifest import resolve

    manifest = _three_entry_manifest()
    # Parsed path includes leading drive/root + the relative path under the
    # manifest's input_root. ``endswith('fact/nightly_load.dtsx')`` should hit.
    result = resolve(manifest, "C:/etl/src/fact/nightly_load.dtsx")
    assert type(result).__name__ == "Hit"
    assert result.proc == "usp_fact_nightly_load"
    assert result.dtsx_source == "fact/nightly_load.dtsx"


def test_resolve_basename_unique_match_returns_hit() -> None:
    """D-3 pass 2: basename-only match returns Hit when exactly one entry has
    that basename (case-insensitive)."""
    from msb_ssis2sql.agent.manifest import resolve

    manifest = _three_entry_manifest()
    # ``finance_daily.dtsx`` only appears once in the manifest under
    # ``marts/finance_daily.dtsx``. Some unrelated path that just shares the
    # basename should still resolve.
    result = resolve(manifest, "/some/other/dir/finance_daily.dtsx")
    assert type(result).__name__ == "Hit"
    assert result.proc == "usp_marts_finance_daily"
    assert result.dtsx_source == "marts/finance_daily.dtsx"


def test_resolve_basename_unique_match_is_case_insensitive() -> None:
    """D-3: basename comparison is case-insensitive (AC-6)."""
    from msb_ssis2sql.agent.manifest import resolve

    manifest = _three_entry_manifest()
    # Mixed case basename — manifest has ``finance_daily.dtsx``.
    result = resolve(manifest, "/random/Finance_Daily.DTSX")
    assert type(result).__name__ == "Hit"
    assert result.proc == "usp_marts_finance_daily"


def test_resolve_basename_ambiguous_returns_ambiguous() -> None:
    """D-3 pass 2 (collision): two entries share a basename → Ambiguous.

    Build a manifest in-memory with two ``nightly_load.dtsx`` entries in
    different directories — the resolver must NOT silently pick one.
    """
    from msb_ssis2sql.agent.manifest import Manifest, ManifestEntry, resolve

    manifest = Manifest(
        version=1,
        input_root="/srv/etl/src",
        entries=(
            ManifestEntry(
                dtsx="fact/nightly_load.dtsx",
                proc="usp_fact_nightly_load",
                out_sql="fact/nightly_load.sql",
            ),
            ManifestEntry(
                dtsx="dim/nightly_load.dtsx",
                proc="usp_dim_nightly_load",
                out_sql="dim/nightly_load.sql",
            ),
        ),
    )
    result = resolve(manifest, "/elsewhere/nightly_load.dtsx")
    assert type(result).__name__ == "Ambiguous"
    candidate_dtsx = {c.dtsx for c in result.candidates}
    assert candidate_dtsx == {"fact/nightly_load.dtsx", "dim/nightly_load.dtsx"}


def test_resolve_miss_returns_miss() -> None:
    """D-3 pass 3: no exact-suffix or basename match → Miss."""
    from msb_ssis2sql.agent.manifest import resolve

    manifest = _three_entry_manifest()
    result = resolve(manifest, "/srv/etl/src/never/heard_of.dtsx")
    assert type(result).__name__ == "Miss"


# ---------------------------------------------------------------- #
# SEC-H1: proc/path validation
# ---------------------------------------------------------------- #

def test_load_rejects_proc_with_sql_injection() -> None:
    """SEC-H1: a hand-crafted manifest with a SQL-injection ``proc`` value
    is rejected at load time with ``invalid:`` prefix."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    with pytest.raises(ManifestError) as excinfo:
        load_manifest(FIXTURES / "injected_proc.json")
    msg = str(excinfo.value)
    assert msg.startswith("invalid:"), msg
    assert "proc" in msg, msg


def test_load_rejects_dtsx_path_traversal() -> None:
    """SEC-H1: ``..`` in a dtsx relpath is rejected at load time with
    ``invalid:`` prefix."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    with pytest.raises(ManifestError) as excinfo:
        load_manifest(FIXTURES / "traversal_dtsx.json")
    msg = str(excinfo.value)
    assert msg.startswith("invalid:"), msg
    assert "dtsx" in msg, msg


# ---------------------------------------------------------------- #
# SEC-M2: oversized manifest is rejected before read_text
# ---------------------------------------------------------------- #

def test_load_rejects_oversized_manifest(tmp_path) -> None:
    """SEC-M2: a manifest file larger than MAX_MANIFEST_BYTES is rejected
    with ``invalid:`` prefix; no OOM."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    huge = tmp_path / "huge_manifest.json"
    huge.write_bytes(b"x" * (17 * 1024 * 1024))  # 17 MiB > 16 MiB cap
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(huge)
    msg = str(excinfo.value)
    assert msg.startswith("invalid:"), msg
    assert "too large" in msg, msg


# ---------------------------------------------------------------- #
# CODE-M2: missing version key surfaced as 'invalid:', not 'unsupported'
# ---------------------------------------------------------------- #

def test_load_missing_version_raises_invalid_not_unsupported() -> None:
    """CODE-M2: a manifest without a ``version`` key is a structural defect —
    error must start with ``invalid:``, not ``unsupported version:``."""
    from msb_ssis2sql.agent.manifest import ManifestError, load_manifest

    with pytest.raises(ManifestError) as excinfo:
        load_manifest(FIXTURES / "missing_version.json")
    msg = str(excinfo.value)
    assert msg.startswith("invalid:"), msg
    assert "version" in msg, msg


# ---------------------------------------------------------------- #
# CODE-H1: Pass-1 endswith must anchor to a path boundary
# ---------------------------------------------------------------- #

def test_resolve_rejects_suffix_substring_collision() -> None:
    """CODE-H1: ``concat.dtsx`` must NOT hit manifest entry ``cat.dtsx`` —
    the Pass-1 endswith comparison is anchored on ``/``."""
    from msb_ssis2sql.agent.manifest import Manifest, ManifestEntry, Miss, resolve

    manifest = Manifest(
        version=1,
        input_root="/srv/etl/src",
        entries=(
            ManifestEntry(
                dtsx="cat.dtsx", proc="usp_cat", out_sql="cat.sql"
            ),
        ),
    )
    result = resolve(manifest, "concat.dtsx")
    assert isinstance(result, Miss)


def test_resolve_normalises_windows_paths_to_posix() -> None:
    """AC-17 cross-OS: a parsed (POSIX-normalised) Windows path with a
    drive-letter prefix that the manifest's input_root does NOT cover, but
    whose trailing suffix matches a manifest entry written with ``/``
    separators, still resolves to Hit via D-3 pass 1.

    Real-world: convert_tree ran on a Linux build host (`/srv/etl/src/...`)
    while the SQL Server Agent job runs on a Windows host that names the
    same .dtsx as ``C:\\etl\\src\\fact\\nightly_load.dtsx``. After parsing,
    the resolver sees ``C:/etl/src/fact/nightly_load.dtsx`` — the manifest
    entry ``fact/nightly_load.dtsx`` is still an exact suffix match.
    """
    from msb_ssis2sql.agent.manifest import resolve

    manifest = _three_entry_manifest()
    # POSIX form a parser would emit from `C:\etl\src\fact\nightly_load.dtsx`.
    # Drive letter prefix differs from manifest.input_root (/srv/etl/src) —
    # the resolver still hits via endswith() on the dtsx relpath.
    result = resolve(manifest, "C:/etl/src/fact/nightly_load.dtsx")
    assert type(result).__name__ == "Hit"
    assert result.proc == "usp_fact_nightly_load"
    assert result.dtsx_source == "fact/nightly_load.dtsx"
    # Crucially: NO backslashes appear in the returned dtsx_source.
    assert "\\" not in result.dtsx_source

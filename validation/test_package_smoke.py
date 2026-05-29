"""Live SQL Server smoke test for the SSIS package extractor.

Marked ``@pytest.mark.package_smoke`` and excluded from the default run
(``pyproject.toml`` sets ``testpaths = ["tests"]``); invoke explicitly:

    just extract-packages-smoke        # uv run pytest validation/ -m package_smoke

It exercises the *real* ``extract_packages`` path — Windows Integrated auth via
``Trusted_Connection=yes`` — so it is meaningful only on a host that can reach a
SQL Server with that identity (e.g. an Azure DevOps self-hosted domain agent).
Anywhere else it **skips** rather than fails:

  * ``MSSQL_SERVER_ADDRESS`` unset                -> skip (not configured)
  * server unreachable / not Windows-auth-capable -> skip (PackageExtractError)

Optional environment overrides:
  * ``MSSQL_SERVER_PORT``      (default ``1433``)
  * ``PACKAGE_SMOKE_STORE``    (``auto`` | ``msdb`` | ``ssisdb``; default ``auto``)
  * ``PACKAGE_SMOKE_DATABASE`` (connection scope; default ``master``)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.package_smoke


def _require_server() -> str:
    server = os.environ.get("MSSQL_SERVER_ADDRESS")
    if not server:
        pytest.skip("MSSQL_SERVER_ADDRESS not set — package smoke needs a live SQL Server")
    return server


def test_extract_packages_against_live_server(tmp_path: Path) -> None:
    """Extract from a live instance and validate the on-disk artefacts."""
    server = _require_server()
    from msb_ssis2sql.errors import PackageExtractError
    from msb_ssis2sql.packages.extractor import MANIFEST_VERSION, extract_packages

    try:
        written = extract_packages(
            server=server,
            port=os.environ.get("MSSQL_SERVER_PORT", "1433"),
            database=os.environ.get("PACKAGE_SMOKE_DATABASE", ""),
            store=os.environ.get("PACKAGE_SMOKE_STORE", "auto"),
            out_dir=tmp_path,
            clean=True,
        )
    except PackageExtractError as exc:
        pytest.skip(f"SQL Server unreachable or not Windows-auth-capable: {exc}")

    # The manifest is always written, even when the instance has zero packages.
    manifest_path = tmp_path / "_packages_manifest.json"
    assert manifest_path.exists(), "extractor did not write _packages_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["store"] in ("msdb", "ssisdb")
    assert len(manifest["packages"]) == len(written)

    # Every written artefact is a non-empty .dtsx living under the output dir,
    # and every manifest path resolves to one of them.
    for path in written:
        assert path.suffix == ".dtsx"
        assert path.is_file() and path.stat().st_size > 0
        assert tmp_path in path.parents

    written_rel = {p.relative_to(tmp_path).as_posix() for p in written}
    assert {e["path"] for e in manifest["packages"]} == written_rel

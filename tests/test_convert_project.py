"""Phase 4: convert_project + convert-tree auto-detection of expanded projects."""
from __future__ import annotations

import shutil
from pathlib import Path

from msb_ssis2sql import convert_project
from msb_ssis2sql.batch import convert_tree

FIXTURE = Path(__file__).parent / "fixtures" / "expanded_project"


# --------------------------------------------------------------------------- #
# convert_project
# --------------------------------------------------------------------------- #

def test_convert_project_resolves_project_params_per_package():
    results = convert_project(FIXTURE)
    assert set(results) == {"LoadSales", "LoadDims"}

    sales = results["LoadSales"].sql
    assert "DECLARE @StagingServer NVARCHAR(4000) = N'sql-stg-01';" in sales
    assert "$Project::StagingServer" in sales

    dims = results["LoadDims"].sql
    assert "DECLARE @BatchSize INT = 5000;" in dims


def test_convert_project_without_manifest_still_converts(tmp_path):
    # Copy only a package (no @Project.manifest) -> project context is None,
    # the param is unresolved (NULL) rather than valued.
    shutil.copy(FIXTURE / "LoadSales.dtsx", tmp_path / "LoadSales.dtsx")
    results = convert_project(tmp_path)
    assert set(results) == {"LoadSales"}
    sql = results["LoadSales"].sql
    assert "DECLARE @StagingServer NVARCHAR(4000) = NULL;" in sql
    assert "(unresolved)" in sql


# --------------------------------------------------------------------------- #
# convert-tree auto-detection
# --------------------------------------------------------------------------- #

def test_convert_tree_threads_project_context(tmp_path):
    out = tmp_path / "out"
    result = convert_tree(FIXTURE, out)
    assert result.failed == 0, result.outcomes

    sales_sql = (out / "LoadSales.sql").read_text(encoding="utf-8")
    # convert-tree wraps in a proc; the project param is resolved to its value.
    assert "DECLARE @StagingServer NVARCHAR(4000) = N'sql-stg-01';" in sales_sql

    dims_sql = (out / "LoadDims.sql").read_text(encoding="utf-8")
    assert "DECLARE @BatchSize INT = 5000;" in dims_sql


def test_password_encrypted_project_warns(tmp_path):
    from msb_ssis2sql.generator import convert_package
    from msb_ssis2sql.model import Package, Project

    project = Project(name="Locked", protection_level="EncryptAllWithPassword")
    result = convert_package(Package(name="p"), project=project)
    assert any("encrypted" in w and "EncryptAllWithPassword" in w for w in result.warnings)


def test_convert_tree_without_manifest_leaves_param_unresolved(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIXTURE / "LoadSales.dtsx", src / "LoadSales.dtsx")
    out = tmp_path / "out"
    convert_tree(src, out)
    sql = (out / "LoadSales.sql").read_text(encoding="utf-8")
    assert "DECLARE @StagingServer NVARCHAR(4000) = NULL;" in sql

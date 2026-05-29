"""Phase 1: parsing an expanded SSIS project + package parameters.

Covers msb_ssis2sql.project (Project.params, *.conmgr, @Project.manifest,
load_project) and the new <DTS:PackageParameters> parsing in msb_ssis2sql.parser.
"""
from __future__ import annotations

from pathlib import Path

from msb_ssis2sql.parser import parse_string
from msb_ssis2sql.project import (
    load_project,
    parse_conmgr_file,
    parse_project_manifest,
    parse_project_params,
)

FIXTURE = Path(__file__).parent / "fixtures" / "expanded_project"


# --------------------------------------------------------------------------- #
# Project.params
# --------------------------------------------------------------------------- #

def test_parse_project_params_values_types_and_sensitivity():
    params = parse_project_params(FIXTURE / "Project.params")
    by_name = {p.name: p for p in params}
    assert set(by_name) == {"StagingServer", "BatchSize", "ApiKey"}

    staging = by_name["StagingServer"]
    assert staging.namespace == "Project"
    assert staging.value == "sql-stg-01"
    assert staging.data_type == "18"
    assert staging.sensitive is False
    assert staging.qualified == "$Project::StagingServer"

    assert by_name["BatchSize"].value == "5000"
    assert by_name["BatchSize"].data_type == "9"

    assert by_name["ApiKey"].sensitive is True
    assert by_name["ApiKey"].value == ""


# --------------------------------------------------------------------------- #
# *.conmgr
# --------------------------------------------------------------------------- #

def test_parse_conmgr_file():
    cm = parse_conmgr_file(FIXTURE / "Staging.conmgr")
    assert cm.scope == "project"
    assert cm.name == "StagingDB"
    assert cm.creation_name == "OLEDB"
    assert "Initial Catalog=Staging" in cm.connection_string


# --------------------------------------------------------------------------- #
# @Project.manifest
# --------------------------------------------------------------------------- #

def test_parse_project_manifest():
    name, protection, packages = parse_project_manifest(FIXTURE / "@Project.manifest")
    assert name == "SalesProject"
    assert protection == "EncryptSensitiveWithUserKey"
    assert packages == ["LoadSales.dtsx", "LoadDims.dtsx"]


# --------------------------------------------------------------------------- #
# load_project
# --------------------------------------------------------------------------- #

def test_load_project_aggregates_everything():
    project = load_project(FIXTURE)
    assert project is not None
    assert project.name == "SalesProject"
    assert project.protection_level == "EncryptSensitiveWithUserKey"
    assert project.is_password_encrypted is False
    assert len(project.parameters) == 3
    assert [cm.name for cm in project.connection_managers] == ["StagingDB"]
    assert project.package_names == ["LoadSales.dtsx", "LoadDims.dtsx"]
    assert project.source_dir == str(FIXTURE)


def test_load_project_returns_none_without_manifest(tmp_path):
    (tmp_path / "Project.params").write_text("<SSIS:Parameters/>", encoding="utf-8")
    assert load_project(tmp_path) is None


def test_password_encrypted_flag():
    name, _, _ = parse_project_manifest(FIXTURE / "@Project.manifest")
    assert name  # sanity
    from msb_ssis2sql.model import Project

    assert Project(protection_level="EncryptAllWithPassword").is_password_encrypted
    assert Project(protection_level="EncryptSensitiveWithPassword").is_password_encrypted
    assert not Project(protection_level="EncryptSensitiveWithUserKey").is_password_encrypted


# --------------------------------------------------------------------------- #
# package parameters in a .dtsx
# --------------------------------------------------------------------------- #

_PKG_WITH_PARAMS = """<?xml version="1.0"?>
<DTS:Executable xmlns:DTS="www.microsoft.com/SqlServer/Dts"
    DTS:ObjectName="PkgWithParams" DTS:ExecutableType="Package">
  <DTS:PackageParameters>
    <DTS:PackageParameter DTS:ObjectName="RunDate" DTS:DataType="7"
        DTS:Required="True" DTS:Sensitive="False">
      <DTS:Property DTS:Name="ParameterValue">2026-01-01</DTS:Property>
    </DTS:PackageParameter>
    <DTS:PackageParameter DTS:ObjectName="Password" DTS:DataType="18"
        DTS:Sensitive="True">
      <DTS:Property DTS:Name="ParameterValue"></DTS:Property>
    </DTS:PackageParameter>
  </DTS:PackageParameters>
</DTS:Executable>
"""


def test_parse_package_parameters():
    package = parse_string(_PKG_WITH_PARAMS)
    by_name = {p.name: p for p in package.parameters}
    assert set(by_name) == {"RunDate", "Password"}

    run_date = by_name["RunDate"]
    assert run_date.namespace == "Package"
    assert run_date.value == "2026-01-01"
    assert run_date.required is True
    assert run_date.sensitive is False
    assert run_date.qualified == "$Package::RunDate"

    assert by_name["Password"].sensitive is True


def test_package_without_parameters_has_empty_list(example_package):
    assert example_package.parameters == []

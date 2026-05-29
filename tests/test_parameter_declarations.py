"""Phase 2: typed, real-valued parameter DECLAREs + sensitivity handling."""
from __future__ import annotations

from msb_ssis2sql.generator import _declarations, _parameter_warnings
from msb_ssis2sql.model import Package, Parameter, Project, Variable
from msb_ssis2sql.sqltypes import param_literal, param_type_to_tsql


# --------------------------------------------------------------------------- #
# type + literal mapping
# --------------------------------------------------------------------------- #

def test_param_type_to_tsql():
    assert param_type_to_tsql("18") == "NVARCHAR(4000)"   # String
    assert param_type_to_tsql("9") == "INT"               # Int32
    assert param_type_to_tsql("3") == "BIT"               # Boolean
    assert param_type_to_tsql("16") == "DATETIME2(7)"     # DateTime
    assert param_type_to_tsql("999") == "NVARCHAR(4000)"  # unknown numeric -> default
    assert param_type_to_tsql("i4") == "INT"              # SSIS short code fallback


def test_param_literal():
    assert param_literal("NVARCHAR(4000)", "abc", False) == "N'abc'"
    assert param_literal("INT", "5000", False) == "5000"
    assert param_literal("INT", "", False) == "NULL"          # empty numeric
    assert param_literal("NVARCHAR(4000)", "x", True) == "NULL"  # sensitive
    assert param_literal("DATETIME2(7)", "2026-01-01", False) == "N'2026-01-01'"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _project() -> Project:
    return Project(
        name="P",
        protection_level="EncryptSensitiveWithUserKey",
        parameters=[
            Parameter("Project", "StagingServer", "18", "sql-stg-01"),
            Parameter("Project", "BatchSize", "9", "5000"),
            Parameter("Project", "ApiKey", "18", "", sensitive=True),
        ],
    )


# --------------------------------------------------------------------------- #
# DECLARE generation
# --------------------------------------------------------------------------- #

def test_project_params_declared_typed_with_real_values():
    pkg = Package(name="pkg")
    refs = {("$Project", "StagingServer"), ("$Project", "BatchSize")}
    sql = _declarations(pkg, refs, _project())
    assert "DECLARE @StagingServer NVARCHAR(4000) = N'sql-stg-01';  -- SSIS parameter $Project::StagingServer" in sql
    assert "DECLARE @BatchSize INT = 5000;  -- SSIS parameter $Project::BatchSize" in sql


def test_sensitive_param_declared_null_with_note_and_warning():
    pkg = Package(name="pkg")
    refs = {("$Project", "ApiKey")}
    sql = _declarations(pkg, refs, _project())
    assert "DECLARE @ApiKey NVARCHAR(4000) = NULL;  -- SSIS parameter $Project::ApiKey (sensitive - value withheld)" in sql

    warnings = _parameter_warnings(pkg, _project(), refs)
    assert any("sensitive" in w for w in warnings)


def test_unresolved_param_is_null_and_warned():
    pkg = Package(name="pkg")
    refs = {("$Project", "Ghost")}
    sql = _declarations(pkg, refs, _project())
    assert "DECLARE @Ghost NVARCHAR(4000) = NULL;  -- SSIS parameter $Project::Ghost (unresolved)" in sql
    warnings = _parameter_warnings(pkg, _project(), refs)
    assert any("not defined" in w for w in warnings)


def test_package_param_overrides_project_param_of_same_name():
    pkg = Package(name="pkg", parameters=[Parameter("Package", "BatchSize", "9", "100")])
    # Reference both namespaces; package wins for $Package, project for $Project.
    refs = {("$Package", "BatchSize"), ("$Project", "BatchSize")}
    sql = _declarations(pkg, refs, _project())
    assert "DECLARE @BatchSize INT = 100;  -- SSIS parameter $Package::BatchSize" in sql
    assert "DECLARE @BatchSize INT = 5000;  -- SSIS parameter $Project::BatchSize" in sql


def test_variable_branch_is_unchanged():
    pkg = Package(name="pkg", variables=[Variable("User", "MinThreshold", "1000")])
    sql = _declarations(pkg, {("User", "MinThreshold")}, None)
    assert "DECLARE @MinThreshold NVARCHAR(4000) = N'1000';  -- SSIS variable User::MinThreshold" in sql
    # No project, no params referenced -> no parameter warnings.
    assert _parameter_warnings(pkg, None, {("User", "MinThreshold")}) == []


def test_no_references_yields_empty_block():
    assert _declarations(Package(name="pkg"), set(), None) == ""

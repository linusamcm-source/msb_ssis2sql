"""ExecutePackageTask references by *path* (with spaces / backslashes) resolve.

SSIS packages reference children either by PackageName ('Child A.dtsx') or by a
PackagePath ('Sub Folder\\Child A.dtsx', possibly extension-less). The orchestrator
matcher must resolve both, including paths that contain blank spaces.
"""
from __future__ import annotations

from msb_ssis2sql.batch import _build_ordered_exec_lines, _ept_reference
from msb_ssis2sql.model import ExecutePackageTask, Package


# --------------------------------------------------------------------------- #
# _ept_reference: (raw, basename) extraction
# --------------------------------------------------------------------------- #

def test_reference_prefers_package_name():
    ept = ExecutePackageTask(package_name="Child A.dtsx", package_path="ignored")
    assert _ept_reference(ept) == ("Child A.dtsx", "Child A.dtsx")


def test_reference_falls_back_to_packagepath_with_spaces_and_backslashes():
    ept = ExecutePackageTask(package_name="", package_path="Sub Folder\\Child A.dtsx")
    assert _ept_reference(ept) == ("Sub Folder\\Child A.dtsx", "Child A.dtsx")


def test_reference_handles_extensionless_store_path():
    ept = ExecutePackageTask(package_name="", package_path="\\package\\Child A")
    raw, basename = _ept_reference(ept)
    assert basename == "Child A"


# --------------------------------------------------------------------------- #
# orchestrator matching via basename
# --------------------------------------------------------------------------- #

def _main_with_ept(package_path: str) -> Package:
    main = Package(name="main")
    main.execute_package_tasks = [
        ExecutePackageTask(ref_id="e1", name="Run Child", package_name="", package_path=package_path)
    ]
    return main


def test_packagepath_reference_with_spaces_resolves_to_proc(tmp_path):
    main = _main_with_ept("Sub Folder\\Child A.dtsx")
    dir_files = [tmp_path / "main.dtsx", tmp_path / "Child A.dtsx"]  # need not exist
    proc_name_by_stem = {"main": "usp_main", "Child A": "usp_child_a"}

    exec_lines, warnings = _build_ordered_exec_lines(
        main, dir_files, dir_files[0], proc_name_by_stem
    )
    assert exec_lines == ["EXEC usp_child_a;"]
    assert not any("missing child" in w for _, w in warnings)


def test_extensionless_packagepath_resolves(tmp_path):
    main = _main_with_ept("\\package\\Child A")
    dir_files = [tmp_path / "main.dtsx", tmp_path / "Child A.dtsx"]
    proc_name_by_stem = {"main": "usp_main", "Child A": "usp_child_a"}

    exec_lines, warnings = _build_ordered_exec_lines(
        main, dir_files, dir_files[0], proc_name_by_stem
    )
    assert exec_lines == ["EXEC usp_child_a;"]
    assert not any("missing child" in w for _, w in warnings)


def test_genuinely_missing_child_still_warns(tmp_path):
    main = _main_with_ept("Sub Folder\\No Such Child.dtsx")
    dir_files = [tmp_path / "main.dtsx", tmp_path / "Child A.dtsx"]
    proc_name_by_stem = {"main": "usp_main", "Child A": "usp_child_a"}

    exec_lines, warnings = _build_ordered_exec_lines(
        main, dir_files, dir_files[0], proc_name_by_stem
    )
    assert exec_lines == []
    assert any("missing child" in w and "No Such Child" in w for _, w in warnings)


def test_directory_traversal_reference_is_rejected(tmp_path):
    main = _main_with_ept("..\\..\\evil.dtsx")
    dir_files = [tmp_path / "main.dtsx"]
    _, warnings = _build_ordered_exec_lines(main, dir_files, dir_files[0], {"main": "usp_main"})
    assert any("outside-dir" in w for _, w in warnings)

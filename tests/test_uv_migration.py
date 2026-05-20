"""RED-phase tests for the uv migration sprint.

These tests assert the post-migration structural state of the repository.
They are grep / config / filesystem assertions, not runtime unit tests, and
they enforce the verification checklist from ``docs/sprint-uv-migration.md``
programmatically.

Pre-migration the suite MUST be largely failing — that proves the gate
detects the work has not been done. Post-migration the suite turns green.
"""
from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
JUSTFILE = REPO_ROOT / "justfile"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"
UV_LOCK = REPO_ROOT / "uv.lock"
WEB_PY = REPO_ROOT / "ssis2sql" / "web.py"
RUNBOOK = REPO_ROOT / "validation" / "capture" / "RUNBOOK.md"
CAPTURE_PS1 = REPO_ROOT / "validation" / "capture" / "capture.ps1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "validation.yml"
README = REPO_ROOT / "README.md"


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# 1. pyproject.toml — dependency groups
# ---------------------------------------------------------------------------
def test_pyproject_uses_dependency_groups() -> None:
    """[dependency-groups] exists with dev/web/validation and bumped floors."""
    data = _load_pyproject()
    groups = data.get("dependency-groups")
    assert groups is not None, (
        "pyproject.toml is missing the [dependency-groups] table"
    )
    for key in ("dev", "web", "validation"):
        assert key in groups, f"[dependency-groups] missing key: {key!r}"

    validation = groups["validation"]
    assert isinstance(validation, list)
    joined = "\n".join(validation)
    assert "pyodbc>=5.3" in joined, (
        f"validation group must pin pyodbc>=5.3; got: {validation}"
    )
    assert "pandas>=3.0" in joined, (
        f"validation group must pin pandas>=3.0; got: {validation}"
    )
    assert "pyarrow>=24.0" in joined, (
        f"validation group must pin pyarrow>=24.0; got: {validation}"
    )


# ---------------------------------------------------------------------------
# 2. pyproject.toml — no [project.optional-dependencies]
# ---------------------------------------------------------------------------
def test_pyproject_drops_optional_dependencies() -> None:
    data = _load_pyproject()
    project = data.get("project", {})
    assert "optional-dependencies" not in project, (
        "pyproject.toml still contains [project.optional-dependencies]; "
        "migrate it to PEP 735 [dependency-groups]"
    )


# ---------------------------------------------------------------------------
# 3. pyproject.toml — [tool.uv] default-groups, no package=true
# ---------------------------------------------------------------------------
def test_pyproject_default_groups() -> None:
    data = _load_pyproject()
    tool_uv = data.get("tool", {}).get("uv")
    assert tool_uv is not None, "pyproject.toml is missing [tool.uv]"
    assert tool_uv.get("default-groups") == ["dev", "web", "validation"], (
        f"[tool.uv].default-groups must equal "
        f"['dev','web','validation']; got: {tool_uv.get('default-groups')!r}"
    )
    # Round-1 adversarial finding: `package = true` must not be present.
    assert "package" not in tool_uv, (
        "[tool.uv] must not contain `package = true` (redundant when "
        "[build-system] is defined; flagged in adversarial round 1)"
    )


# ---------------------------------------------------------------------------
# 4. .python-version pinned to 3.14
# ---------------------------------------------------------------------------
def test_dotpython_version_pinned() -> None:
    assert PYTHON_VERSION_FILE.is_file(), (
        f"{PYTHON_VERSION_FILE} does not exist; "
        "Phase 1 requires `.python-version` pinned to 3.14"
    )
    contents = PYTHON_VERSION_FILE.read_text().strip()
    assert contents == "3.14", (
        f".python-version must contain exactly '3.14'; got: {contents!r}"
    )


# ---------------------------------------------------------------------------
# 5. uv.lock committed
# ---------------------------------------------------------------------------
def test_uv_lock_committed() -> None:
    assert UV_LOCK.is_file(), (
        f"{UV_LOCK} does not exist; `uv sync` must be run and the lockfile "
        "committed"
    )
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "uv.lock"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"uv.lock is not tracked by git. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# 6. justfile contains no .venv/bin/python, no python3 -m venv, no pip install -e
# ---------------------------------------------------------------------------
def test_justfile_no_dotvenv_or_pip() -> None:
    text = JUSTFILE.read_text()
    pattern = re.compile(r"\.venv/bin/python|python3? -m venv|pip install -e")
    matches = pattern.findall(text)
    assert matches == [], (
        f"justfile still contains legacy venv/pip strings: {matches!r}"
    )


# ---------------------------------------------------------------------------
# 7. justfile install recipe body is `uv sync`
# ---------------------------------------------------------------------------
def test_justfile_install_uses_uv_sync() -> None:
    text = JUSTFILE.read_text()
    # Match an `install:` recipe header followed by a body line. A justfile
    # recipe body is indented (tab or spaces). We capture only the first body
    # line because that's where the plan puts `uv sync`.
    match = re.search(
        r"^install:[^\n]*\n[ \t]+(.+?)(?:\n[^\s]|\n[ \t]*#|\n$|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "justfile has no `install:` recipe"
    body = match.group(1).strip()
    assert body == "uv sync", (
        f"`install:` recipe body must be exactly `uv sync`; got: {body!r}"
    )


# ---------------------------------------------------------------------------
# 8. justfile has lint + typecheck recipes invoking uv run ruff / uv run mypy
# ---------------------------------------------------------------------------
def test_justfile_has_lint_and_typecheck() -> None:
    text = JUSTFILE.read_text()

    lint_match = re.search(
        r"^lint:[^\n]*\n[ \t]+([^\n]+)", text, re.MULTILINE
    )
    assert lint_match is not None, "justfile has no `lint:` recipe"
    lint_body = lint_match.group(1).strip()
    assert "uv run ruff check" in lint_body, (
        f"`lint:` recipe must invoke `uv run ruff check`; got: {lint_body!r}"
    )

    typecheck_match = re.search(
        r"^typecheck:[^\n]*\n[ \t]+([^\n]+)", text, re.MULTILINE
    )
    assert typecheck_match is not None, "justfile has no `typecheck:` recipe"
    typecheck_body = typecheck_match.group(1).strip()
    assert "uv run mypy" in typecheck_body, (
        f"`typecheck:` recipe must invoke `uv run mypy`; "
        f"got: {typecheck_body!r}"
    )


# ---------------------------------------------------------------------------
# 9. justfile install-web / install-validation recipes removed
# ---------------------------------------------------------------------------
def test_justfile_install_web_recipe_removed() -> None:
    text = JUSTFILE.read_text()
    # Recipe headers always start at column 0 in the form `name:` (possibly
    # with parameters before the colon). Match at line starts to avoid false
    # positives from comments.
    assert not re.search(r"^install-web:", text, re.MULTILINE), (
        "`install-web:` recipe must be removed from justfile"
    )
    assert not re.search(r"^install-validation:", text, re.MULTILINE), (
        "`install-validation:` recipe must be removed from justfile"
    )


# ---------------------------------------------------------------------------
# 10. ssis2sql/web.py error message migrates to `just install`
# ---------------------------------------------------------------------------
def test_web_py_error_message_uses_just_install() -> None:
    text = WEB_PY.read_text()
    assert "just install" in text, (
        "ssis2sql/web.py must mention `just install` in the import-error hint"
    )
    assert "install-web" not in text, (
        "ssis2sql/web.py must not reference the removed `install-web` recipe"
    )


# ---------------------------------------------------------------------------
# 11. RUNBOOK has no old venv paths or pip install -e references
# ---------------------------------------------------------------------------
def test_runbook_no_old_venv_paths() -> None:
    text = RUNBOOK.read_text()
    pattern = re.compile(r"\.venv\\Scripts\\|pip install -e")
    matches = pattern.findall(text)
    assert matches == [], (
        f"validation/capture/RUNBOOK.md still contains legacy install strings: "
        f"{matches!r}"
    )


# ---------------------------------------------------------------------------
# 12. capture.ps1 uses `uv run python`, no .venv\Scripts\
# ---------------------------------------------------------------------------
def test_capture_ps1_uses_uv_run() -> None:
    text = CAPTURE_PS1.read_text()
    assert "uv run python" in text, (
        "validation/capture/capture.ps1 must invoke `uv run python`"
    )
    assert r".venv\Scripts" not in text, (
        r"validation/capture/capture.ps1 must not reference .venv\Scripts\\"
    )


# ---------------------------------------------------------------------------
# 13. CI workflow uses setup-uv + uv sync --locked, no install-validation
# ---------------------------------------------------------------------------
def test_workflow_uses_setup_uv() -> None:
    text = WORKFLOW.read_text()
    assert "astral-sh/setup-uv" in text, (
        ".github/workflows/validation.yml must use astral-sh/setup-uv"
    )
    assert "uv sync --locked" in text, (
        ".github/workflows/validation.yml must call `uv sync --locked`"
    )
    assert "just install-validation" not in text, (
        ".github/workflows/validation.yml must not call `just install-validation`"
    )


# ---------------------------------------------------------------------------
# 14. README ## Install section migrated to uv sync, no pip install -e
# ---------------------------------------------------------------------------
def test_readme_install_section_uses_uv() -> None:
    text = README.read_text()
    # Slice from the `## Install` heading to the next `## ` heading so we
    # only assert on the install section itself.
    install_match = re.search(
        r"^## Install\b(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL
    )
    assert install_match is not None, "README.md has no `## Install` section"
    install_section = install_match.group(1)
    assert "uv sync" in install_section, (
        "README.md `## Install` section must mention `uv sync`"
    )
    assert "pip install -e" not in install_section, (
        "README.md `## Install` section must not mention `pip install -e`"
    )


# ---------------------------------------------------------------------------
# 15. Phase 4 step 11 — live-source grep gate (legacy venv paths)
# ---------------------------------------------------------------------------
def test_grep_gate_live_source() -> None:
    """git grep for legacy venv path strings must return zero matches."""
    result = subprocess.run(
        [
            "git", "grep", "-nE",
            r"\.venv/bin|\.venv\\Scripts|python3? -m venv",
            "--",
            ":!docs/sprint-*.md",
            ":!docs/epic-*.md",
            ":!docs/plan-tui-*.md",
            ":!.repomix-output.xml",
            ":!.repomix-textual.xml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # git grep returns 1 when zero matches; stdout must be empty.
    assert result.returncode == 1, (
        f"grep gate found matches (returncode={result.returncode}); "
        f"stdout=\n{result.stdout}"
    )
    assert result.stdout == "", (
        f"grep gate found legacy venv path matches:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# 16. Phase 4 step 12 — install-string grep gate
# ---------------------------------------------------------------------------
def test_grep_gate_install_strings() -> None:
    """git grep for legacy install strings must return zero matches."""
    result = subprocess.run(
        [
            "git", "grep", "-n",
            r"install-web\|install-validation\|pip install -e",
            "--",
            ":!docs/sprint-*.md",
            ":!docs/epic-*.md",
            ":!docs/plan-tui-*.md",
            ":!.repomix-output.xml",
            ":!.repomix-textual.xml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (
        f"grep gate found matches (returncode={result.returncode}); "
        f"stdout=\n{result.stdout}"
    )
    assert result.stdout == "", (
        f"grep gate found legacy install-string matches:\n{result.stdout}"
    )

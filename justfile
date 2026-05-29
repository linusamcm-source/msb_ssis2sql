# Default recipe: run Claude in max-effort auto mode.
opus:
    @claude --dangerously-skip-permissions --effort 'max' --enable-auto-mode "/caveman"

# Sync the project venv with every dependency group (single unified install).
install:
    uv sync

# Refresh the lockfile (use after editing pyproject.toml dependencies).
lock:
    uv lock

# Run the test suite.
test:
    uv run pytest

# Run the test suite with a line-coverage report.
cov:
    uv run pytest --cov=msb_ssis2sql --cov-report=term-missing

# Static lint via ruff (PEP 8 + pyflakes).
lint:
    uv run ruff check .

# Type-check the package with mypy.
typecheck:
    uv run mypy msb_ssis2sql validation

# Convert a .dtsx file to T-SQL and write to OUTFILE.
# Usage: just migrate-file path/to/pkg.dtsx path/to/output.sql
migrate-file FILE OUTFILE:
    uv run python -m msb_ssis2sql convert '{{FILE}}' -o '{{OUTFILE}}'

# Print the parsed component graph. Usage: just inspect path/to/pkg.dtsx
inspect FILE:
    uv run python -m msb_ssis2sql inspect '{{FILE}}'

# Convert the bundled example package and print the consolidated SQL.
demo:
    uv run python -m msb_ssis2sql convert examples/sales_etl.dtsx

# Recursively convert every .dtsx under INPUT into OUTPUT, mirroring the input tree.
# Usage: just migrate-directory path/to/input path/to/output
migrate-directory INPUT OUTPUT:
    uv run python -m msb_ssis2sql convert-tree '{{INPUT}}' '{{OUTPUT}}'

# Convert every .dtsx under examples/samples into generated_scripts/*.sql.
# Build copies under bin/ are skipped. Warnings are embedded in each .sql header.
convert-samples:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p generated_scripts
    count=0
    while IFS= read -r -d '' src; do
        out="generated_scripts/$(basename "${src%.dtsx}").sql"
        echo "converting ${src#examples/samples/} -> ${out}"
        uv run python -m msb_ssis2sql convert "$src" -o "$out" -vv
        count=$((count + 1))
    done < <(find examples/samples -name '*.dtsx' -not -path '*/bin/*' -print0 | sort -z)
    echo "done: ${count} package(s) converted into generated_scripts/"

# Extract every SSIS package from a SQL Server instance into OUT as .dtsx files,
# using Windows Integrated auth (the current process identity). Auto-detects the
# SSISDB catalog, falling back to the legacy msdb store.
# Usage: just extract-packages sql-host path/to/output
extract-packages SERVER OUT:
    uv run python -m msb_ssis2sql extract-packages --server '{{SERVER}}' --out '{{OUT}}'

# Launch the Textual control-panel UI for msb_ssis2sql.
tui:
    uv run python -m msb_ssis2sql.tui

# Serve the Textual TUI in a browser via textual-serve (default localhost:8000).
web HOST="localhost" PORT="8000":
    uv run python -m msb_ssis2sql.web --host '{{HOST}}' --port '{{PORT}}'

# Run the full differential validation suite (needs SQL Server; skips until golden exists).
validate:
    uv run pytest validation/ -m validation

# Run the framework's own unit tests (no SQL Server required).
validate-unit:
    uv run pytest validation/tests

# Run framework unit tests with a coverage report for the validation package.
validate-cov:
    uv run pytest validation/tests --cov=validation --cov-report=term-missing --cov-report=json

# Run the static structural checks (no SQL Server required).
validate-static:
    uv run pytest validation/test_static.py

# Spin up a containerised SQL Server with a seeded msdb and run the agent
# extractor against it. Manual pre-merge smoke; not part of `just test`.
extract-agent-jobs-smoke:
    uv run pytest validation/ -m agent_smoke

# Remove the virtual environment, lockfile-tracked caches, build artefacts.
clean:
    rm -rf .venv .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
    find . -name __pycache__ -type d -prune -exec rm -rf {} +

# Default recipe: run Claude in max-effort auto mode.
opus:
    claude --dangerously-skip-permissions --effort 'max' --enable-auto-mode 

# Create the virtual environment and install ssis2sql with dev dependencies.
install:
    python3 -m venv .venv
    .venv/bin/pip install -e ".[dev]"

# Run the test suite.
test:
    .venv/bin/python -m pytest

# Run the test suite with a line-coverage report.
cov:
    .venv/bin/python -m pytest --cov=ssis2sql --cov-report=term-missing

# Convert a .dtsx file to T-SQL on stdout. Usage: just convert path/to/pkg.dtsx
convert FILE:
    .venv/bin/python -m ssis2sql convert {{FILE}}

# Print the parsed component graph. Usage: just inspect path/to/pkg.dtsx
inspect FILE:
    .venv/bin/python -m ssis2sql inspect {{FILE}}

# Convert the bundled example package and print the consolidated SQL.
demo:
    .venv/bin/python -m ssis2sql convert examples/sales_etl.dtsx

# Convert every .dtsx under examples/samples into generated_scripts/*.sql.
# Build copies under bin/ are skipped. Warnings are embedded in each .sql header.
convert-samples:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -x .venv/bin/python ]; then echo "run 'just install' first" >&2; exit 1; fi
    mkdir -p generated_scripts
    count=0
    while IFS= read -r -d '' src; do
        out="generated_scripts/$(basename "${src%.dtsx}").sql"
        echo "converting ${src#examples/samples/} -> ${out}"
        .venv/bin/python -m ssis2sql convert "$src" -o "$out" -vv 
        count=$((count + 1))
    done < <(find examples/samples -name '*.dtsx' -not -path '*/bin/*' -print0 | sort -z)
    echo "done: ${count} package(s) converted into generated_scripts/"

# Remove the virtual environment, build artefacts, and caches.
clean:
    rm -rf .venv .pytest_cache build dist *.egg-info
    find . -name __pycache__ -type d -prune -exec rm -rf {} +

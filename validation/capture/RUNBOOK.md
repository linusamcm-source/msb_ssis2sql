# Golden-Capture Runbook

Run this on a **Windows host** after the sprint merges to populate `golden/*.parquet` and `manifest.json` for each corpus package.  The resulting files are committed to the repository so the differential validation layer can run in CI.

---

## Prerequisites

### 1. Windows with SSIS / dtexec

Install SQL Server Developer Edition (free) or the standalone SSIS runtime.  After installation, `dtexec.exe` must be on `PATH`:

```powershell
dtexec /?   # must print usage without error
```

If `dtexec` is not on PATH, pass its location via `--dtexec-path` (see Step 6).

### 2. Microsoft ODBC Driver 18 for SQL Server

Download from https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server and install.  Verify:

```powershell
Get-OdbcDriver -Name "*SQL Server*"   # must list "ODBC Driver 18 for SQL Server"
```

### 3. Python 3.11+ (3.14 recommended; pinned via .python-version)

From the repo root:

```powershell
uv sync
```

Verify:

```powershell
uv run python -c "import validation.capture.capture; print('OK')"
```

### 4. Remote SQL Server access

The same SQL Server instance used by CI.  Network connectivity and credentials are required.

---

## Environment / .env setup

Copy `.env.example` to `.env` in the repo root and fill in the four connection parameters:

```
MSSQL_SERVER_ADDRESS=your-sql-server-host-or-ip
MSSQL_SERVER_PORT=1433
MSSQL_SA_USERNAME=sa
MSSQL_SA_PASSWORD=YourStrong!Passw0rd
```

These are the exact variable names `validation/config.py` reads (`_REQUIRED_VARS`).  The ODBC driver is resolved automatically by the framework; do not add `MSSQL_DATABASE` or `MSSQL_DRIVER`.  The harness reads `.env` automatically via `python-dotenv`.  Never commit `.env`.

---

## Capturing golden fixtures for a single package

Run from the repo root on the Windows capture host:

### Step 1 — Verify the package transpiles

```powershell
uv run python -c "
from ssis2sql import convert_file, ConvertOptions
from pathlib import Path
r = convert_file(Path('validation/corpus/passthrough_basic/package.dtsx'), ConvertOptions())
print('SQL length:', len(r.sql)); print('Warnings:', r.warnings)
"
```

### Step 2 — Run the capture

```powershell
.\validation\capture\capture.ps1 -PackageDir validation\corpus\passthrough_basic
```

Or invoke Python directly:

```powershell
uv run python -m validation.capture.capture --package-dir validation\corpus\passthrough_basic
```

The harness will:

1. Connect to the remote SQL Server and create/reset the package database.
2. Apply `schema.sql` DDL.
3. Seed source tables from `seed/*.csv`.
4. Run `dtexec /FILE package.dtsx` (exit code != 0 aborts with an error).
5. Read back each `dst_*` table.
6. Write `validation/corpus/passthrough_basic/golden/<table>.parquet` for each destination.
7. Write `validation/corpus/passthrough_basic/golden/manifest.json`.

### Step 3 — Verify outputs

```powershell
# Parquet files and manifest must exist
ls validation\corpus\passthrough_basic\golden\

# Spot-check the manifest
Get-Content validation\corpus\passthrough_basic\golden\manifest.json

# Spot-check a Parquet file (requires pandas/pyarrow)
uv run python -c "
import pandas as pd
df = pd.read_parquet('validation/corpus/passthrough_basic/golden/dst_items.parquet')
print(df)
"
```

### Step 4 — Commit the golden fixtures

```powershell
git add validation/corpus/passthrough_basic/golden/
git commit -m "feat: golden fixtures for passthrough_basic"
```

---

## Capturing all packages at once

```powershell
.\validation\capture\capture.ps1 -AllPackages
```

Failed packages are reported at the end; exit code is non-zero if any fail.

After all captures complete:

```powershell
git add validation/corpus/
git commit -m "feat: golden fixtures for all corpus packages"
```

---

## Specifying a non-PATH dtexec

```powershell
.\validation\capture\capture.ps1 `
    -PackageDir validation\corpus\passthrough_basic `
    -DtexecPath "C:\Program Files\Microsoft SQL Server\160\DTS\Binn\dtexec.exe"
```

---

## Output locations

| File | Description |
|------|-------------|
| `validation/corpus/<pkg>/golden/<dst>.parquet` | Golden fixture for one destination table |
| `validation/corpus/<pkg>/golden/manifest.json` | Seed checksum, row counts, column types |

The `.gitkeep` placeholder in each `golden/` directory ensures the directory exists before capture.  The actual `.parquet` files and `manifest.json` are committed after capture.

---

## Integrity gate

The validation CI re-computes the seed checksum from the live `seed/*.csv` files and asserts it matches `manifest.json["seed_checksum"]`.  If seed CSVs change after capture, re-run the capture and re-commit the golden files.

Error message on mismatch: `"golden fixture is stale — seed_checksum does not match; re-run validation/capture/RUNBOOK.md"`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `dtexec` not found | Not on PATH | Install SSIS or pass `--dtexec-path` |
| `pyodbc.Error: [28000]` login failure | Wrong credentials in `.env` | Check `MSSQL_SA_USERNAME` / `MSSQL_SA_PASSWORD` |
| `pyodbc.Error: [08001]` connection failure | Server unreachable | Check network / `MSSQL_SERVER_ADDRESS` |
| Exit code non-zero from dtexec | Package execution error | Check `dtexec` stdout; verify ODBC connection strings in the package |
| `manifest.json` seed_checksum mismatch in CI | Seed CSVs changed after capture | Re-run capture and re-commit golden files |
| `ODBC Driver 18 for SQL Server` not found | Driver not installed | Install from Microsoft download link above |

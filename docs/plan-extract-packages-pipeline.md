# Plan — extract SSIS packages from SQL Server in an Azure DevOps pipeline

> **Status: implemented (rev 2).** Shipped as `msb_ssis2sql extract-packages`
> (`msb_ssis2sql/packages/`), the `azure-pipelines.yaml` at the repo root, a
> `just extract-packages` recipe, and unit tests in
> `tests/test_packages_extractor.py` / `tests/test_cli_extract_packages.py`.
> Rev-2 adjustments folded in during implementation:
> - **Raw-bytes extraction** — msdb packages are read as `VARBINARY(MAX)` and
>   written verbatim (preserves the original encoding/BOM), rather than casting
>   to `XML` and losing the declaration.
> - **Manifest + warnings log** — a deterministic `_packages_manifest.json` and,
>   when packages are skipped, a `_packages_warnings.log`, mirroring the agent
>   extractor's `_proc_manifest.json` / `_agent_warnings.log`.
> - **Per-package error isolation** — a corrupt `.ispac` or a missing `.dtsx`
>   member is logged and skipped, never aborting the whole run.
> - **`--clean`** for idempotent re-runs, and an optional **`convertToSql`**
>   pipeline parameter that chains extraction into `convert-tree`.
> - The connection scope defaults to `master`; all queries are three-part
>   qualified, so store detection works regardless of `--database`.

## Goal

From an Azure DevOps pipeline, connect to a **parameterised** SQL Server
database using **Windows Authentication**, enumerate the SSIS packages stored
in that instance, and write each one to the local pipeline VM as a `.dtsx`
file (the format `convert-tree` already consumes). All operator-facing inputs
(server, port, store type, database, name filter, output directory) are
declared as `parameters:` at the top of `azure-pipelines.yaml`.

## Decisions (locked with the requester)

| # | Decision | Choice |
|---|----------|--------|
| D-1 | Package store | Support **both** the legacy `msdb` store and the **SSISDB catalog**, selected by a `--store {auto,msdb,ssisdb}` flag (`auto` probes for `SSISDB`). |
| D-2 | Authentication | **Windows Integrated** auth via a **self-hosted domain agent**. The agent service runs as a domain account (or gMSA) with read rights on the instance; the connection uses `Trusted_Connection=yes` — no password is ever stored, passed, or logged. |
| D-3 | Implementation | New CLI subcommand **`msb_ssis2sql extract-packages`**, a self-contained module mirroring `msb_ssis2sql/agent/`. The pipeline simply invokes it. |

## Hard constraints & call-outs

- **Windows Integrated auth cannot run on Microsoft-hosted agents** — they
  can't join the domain. This plan therefore targets a **Windows self-hosted
  agent** whose service identity is the domain account that authenticates to
  SQL Server. The pipeline `pool:` must point at that self-hosted pool.
- **Prerequisite on the agent VM:** Microsoft **ODBC Driver 18 for SQL
  Server** (the driver `validation/config.py` already standardises on) plus a
  Python toolchain (`uv`). Both are installed once when the agent is built, not
  per-run.
- The SQL identity needs only **read** access: `db_datareader` on `msdb` (for
  the legacy store) and/or the `SSISDB` reader rights + `EXECUTE` on
  `catalog.get_project` (for the catalog store).

## Architecture

New package `msb_ssis2sql/packages/`, structured like `agent/`:

```
msb_ssis2sql/packages/
  __init__.py        "SSIS package extraction package."
  extractor.py       connect + dispatch on store type + write files
  store_msdb.py      query sysssispackages -> .dtsx XML per row
  store_ssisdb.py    enumerate catalog + get_project -> unzip .ispac -> .dtsx
  model.py           ExtractedPackage dataclass (folder, project, name, kind, payload)
```

- `errors.py` gains `class PackageExtractError(Ssis2SqlError)` (mirrors
  `AgentExtractError`).
- Reuse `_naming.sanitise` for every path segment so package/folder/project
  names cannot escape the output directory (path-traversal defence).
- Connection built in `extractor.py` (self-contained, like `agent/_connect`),
  **not** through `validation/config.py` — that module is SQL-auth only and
  lives in the validation layer.

### Connection string (Windows auth)

```
DRIVER={ODBC Driver 18 for SQL Server};
SERVER=<server>,<port>;
DATABASE=<database>;
Trusted_Connection=yes;
Encrypt=yes;
TrustServerCertificate=yes;
```

No `UID`/`PWD`. `Trusted_Connection=yes` makes pyodbc authenticate as the
process identity — i.e. the self-hosted agent's domain service account.
`TrustServerCertificate` is a parameter (`--trust-cert`, default on) so a
properly-PKI'd instance can turn it off.

### Store: `msdb` (legacy package store)

Each row's `packagedata` column **is** the `.dtsx` XML.

```sql
SELECT  f.foldername,
        p.name,
        CAST(p.packagedata AS VARBINARY(MAX)) AS dtsx_bytes
FROM    msdb.dbo.sysssispackages       p
JOIN    msdb.dbo.sysssispackagefolders f ON p.folderid = f.folderid
ORDER BY f.foldername, p.name;
```

(Read as raw bytes and written verbatim — casting to `XML` would drop the
package's `<?xml ?>` declaration / BOM.)

- Write to `<out>/<sanitised-folder>/<sanitised-name>.dtsx`.
- Fallback for pre-2012 instances: probe for `sysdtspackages90` /
  `sysdtspackagefolders90` and use them if `sysssispackages` is absent.

### Store: `ssisdb` (SSIS catalog)

Catalog stores **projects** as `.ispac` (a zip of `.dtsx` + `.params` +
`.conmgr`). Enumerate, fetch the project binary, unzip the `.dtsx` members.

```sql
-- enumerate
SELECT f.name AS folder, prj.name AS project, pkg.name AS package
FROM   SSISDB.catalog.folders  f
JOIN   SSISDB.catalog.projects prj ON prj.folder_id  = f.folder_id
JOIN   SSISDB.catalog.packages pkg ON pkg.project_id = prj.project_id
ORDER BY f.name, prj.name, pkg.name;

-- per distinct (folder, project): returns the .ispac as varbinary
EXEC SSISDB.catalog.get_project @folder_name = ?, @project_name = ?;
```

- Parameterise the `EXEC` with `?` placeholders — never string-format folder
  or project names into SQL.
- The returned binary is a zip; use Python `zipfile` to extract each `*.dtsx`
  member to `<out>/<folder>/<project>/<package>.dtsx`. Optionally also keep the
  raw `.ispac` with `--keep-ispac`.

### Store: `auto`

`SELECT DB_ID('SSISDB')` — non-null ⇒ catalog, else fall back to `msdb`.

### CLI surface

```
msb_ssis2sql extract-packages
  --server      HOST            (required; or MSSQL_SERVER_ADDRESS)
  --port        PORT            (default 1433)
  --database    NAME            (default: derived from --store)
  --store       {auto,msdb,ssisdb}   (default auto)
  --filter      PATTERN         (case-insensitive substring on package name)
  --out         DIR             (default ./packages)
  --driver      NAME            (default "ODBC Driver 18 for SQL Server")
  --trust-cert / --no-trust-cert
  --keep-ispac                  (catalog only)
  -v / -vv                      (existing verbosity convention)
```

Wire into `cli.py` exactly like `extract-agent-jobs`: a `sub.add_parser`
block, a `_cmd_extract_packages` dispatcher, and a clause in `main()`. Print
`wrote <path>` per file and return non-zero on connection/permission failure
(catch `PackageExtractError`).

A `justfile` recipe for local runs:

```make
# Extract SSIS packages from a SQL Server instance (Windows auth) into DIR.
extract-packages SERVER OUT:
    uv run python -m msb_ssis2sql extract-packages --server '{{SERVER}}' --out '{{OUT}}'
```

## `azure-pipelines.yaml` (top-of-file parameterisation)

```yaml
parameters:
  - name: sqlServer
    type: string
    default: 'sql-prod-01.corp.example.com'
  - name: sqlPort
    type: string
    default: '1433'
  - name: packageStore
    type: string
    default: 'auto'
    values: [auto, msdb, ssisdb]
  - name: database          # blank => derived from store (msdb / SSISDB)
    type: string
    default: ''
  - name: packageFilter     # blank => all packages
    type: string
    default: ''
  - name: outputDir
    type: string
    default: '$(Pipeline.Workspace)/ssis-packages'

variables:
  - name: pythonVersion
    value: '3.14'

# Windows self-hosted pool: the agent service runs as the domain account
# that authenticates to SQL Server (Decision D-2). Microsoft-hosted agents
# cannot do Windows Integrated auth against a domain SQL Server.
pool:
  name: 'self-hosted-windows-domain'

steps:
  - checkout: self

  - script: |
      uv sync --locked --no-group validation
    displayName: 'Install msb_ssis2sql (uv)'

  - script: >
      uv run python -m msb_ssis2sql extract-packages
      --server "${{ parameters.sqlServer }}"
      --port "${{ parameters.sqlPort }}"
      --store "${{ parameters.packageStore }}"
      --database "${{ parameters.database }}"
      --filter "${{ parameters.packageFilter }}"
      --out "${{ parameters.outputDir }}"
      -v
    displayName: 'Extract SSIS packages (Windows auth)'

  - publish: '${{ parameters.outputDir }}'
    artifact: ssis-packages
    displayName: 'Publish extracted packages'
```

Notes:
- `${{ parameters.* }}` are **compile-time** substitutions — ideal for values
  chosen when the pipeline is queued. No secrets are needed because Windows
  auth carries no password.
- `--database ""` is handled by the CLI (blank ⇒ derive from `--store`).
- The `publish` step is optional; drop it if the packages only need to exist on
  the VM for a later step in the same job.

## Security

- **No credentials in YAML, logs, or artifacts.** `Trusted_Connection=yes`
  uses the agent identity; the extractor never reads or prints a password
  (same discipline as `agent/extractor.py`).
- **Parameterised SQL** for the catalog `get_project` EXEC; identifiers are
  never string-interpolated.
- **Path-traversal defence:** every folder/project/package name is run through
  `_naming.sanitise` before being joined to `--out`.
- **Least privilege:** read-only DB role; document the exact grants in the CLI
  help and a short `docs/` note.

## Testing

- **Unit (no SQL Server):** parse a captured `sysssispackages` row fixture into
  `.dtsx`; unzip a tiny fixture `.ispac` into its `.dtsx` members; verify
  sanitisation/output-path mapping; verify `auto` store selection logic with a
  mocked cursor. Lives in `tests/` (and/or `validation/tests/`), mirroring the
  agent extractor's unit coverage.
- **Smoke (live, manual):** a `just extract-packages-smoke` recipe / pytest
  marker like the existing `agent_smoke`, run against a containerised or lab
  SQL Server — not part of CI.
- CI (`validation.yml`) stays SQL-Server-free; the new unit tests run there via
  `just validate-unit` / `just test`.

## Rollout steps

1. Add `PackageExtractError` to `errors.py`.
2. Build `msb_ssis2sql/packages/` (model, msdb store, ssisdb store, extractor).
3. Wire `extract-packages` into `cli.py`; add the `justfile` recipe.
4. Add unit tests + fixtures.
5. Author `azure-pipelines.yaml` with the parameter block above.
6. Document agent prerequisites (ODBC Driver 18, uv, domain service account &
   SQL grants) in `README.md` and/or a `docs/` runbook.
7. Update `README.md` Usage table with the new subcommand.

## Open questions / assumptions

- Assumes a **Windows** self-hosted agent. A Linux agent can do Windows auth
  only via Kerberos/`gss` configuration — out of scope unless required.
- Assumes packages should land as **`.dtsx`** (uniform across both stores) to
  feed `convert-tree`. `--keep-ispac` is offered for the catalog case if the
  raw project artifact is also wanted.
- Output layout mirrors the source hierarchy
  (`<folder>/<project>/<package>.dtsx` for catalog, `<folder>/<name>.dtsx` for
  msdb); confirm this matches the downstream `convert-tree` expectations.

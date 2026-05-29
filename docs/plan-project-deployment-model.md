# Plan — project-deployment-model awareness (`@Project.manifest`, `Project.params`, `*.conmgr`)

> **Status: implemented** (phases 1–6). Companion to `docs/plan-extract-packages-pipeline.md`.
> That work extracts each catalog package's `.dtsx` and discards the rest of the
> `.ispac`. This plan parses the *project-scoped* files an `.ispac` carries and
> threads them through conversion, so packages that reference project parameters
> and project (shared) connection managers convert faithfully instead of losing
> that information.

## Goal

Teach `msb_ssis2sql` to consume an **expanded SSIS project** — the directory you
get by unzipping an `.ispac` — and use every project-scoped artefact during
conversion:

- **`Project.params`** — project parameters (`$Project::Name`), their data types
  and default values.
- **`*.conmgr`** — project-level (shared) connection managers referenced by
  packages but not defined inside their `.dtsx`.
- **`@Project.manifest`** — project metadata: protection level, version, the
  ordered package list, and the project↔params/conmgr wiring.
- **Package parameters** (`<DTS:PackageParameters>` inside each `.dtsx`,
  `$Package::Name`) — currently unparsed even for standalone packages.

## Background — what an expanded `.ispac` contains

An `.ispac` is a zip. Unzipped, a typical project directory holds:

```
MyProject/
  @Project.manifest        SSIS:Project — protection level, version, references
  Project.params           SSIS:Parameters — project parameters
  Connection1.conmgr        DTS:ConnectionManager — a shared connection
  Connection2.conmgr
  Package1.dtsx            packages (already handled today)
  Package2.dtsx
```

### `Project.params`

```xml
<SSIS:Parameters xmlns:SSIS="www.microsoft.com/SqlServer/SSIS">
  <SSIS:Parameter SSIS:Name="StagingServer">
    <SSIS:Properties>
      <SSIS:Property SSIS:Name="ID">{guid}</SSIS:Property>
      <SSIS:Property SSIS:Name="DataType">18</SSIS:Property>   <!-- TypeCode -->
      <SSIS:Property SSIS:Name="Value">sql-stg-01</SSIS:Property>
      <SSIS:Property SSIS:Name="Sensitive">0</SSIS:Property>
      <SSIS:Property SSIS:Name="Required">0</SSIS:Property>
    </SSIS:Properties>
  </SSIS:Parameter>
</SSIS:Parameters>
```

`DataType` is a .NET `TypeCode` (e.g. 18=String, 9=Int32, 11=Boolean, 7=Double,
15=Decimal, 16=DateTime). Sensitive parameters carry no usable value when the
project is `EncryptSensitive*` — flag, don't guess.

### `*.conmgr`

Same shape as the in-package `<DTS:ConnectionManager>` the parser already reads
(`parser.py:_parse_connection_manager`): an `ObjectName`, `CreationName`
(`OLEDB`, `ADO.NET`, `FLATFILE`, …), and an `ObjectData/ConnectionManager` with a
`ConnectionString`. The file root *is* a `<DTS:ConnectionManager>` element.

### `@Project.manifest`

```xml
<SSIS:Project SSIS:ProtectionLevel="EncryptSensitiveWithUserKey">
  <SSIS:Properties>… VersionMajor/Minor/Build, Name …</SSIS:Properties>
  <SSIS:DeploymentInfo>
    <SSIS:ProjectConnectionParameters>…</SSIS:ProjectConnectionParameters>
    <SSIS:PackageInfo>… one entry per package …</SSIS:PackageInfo>
  </SSIS:DeploymentInfo>
</SSIS:Project>
```

The decisive field for us is **`ProtectionLevel`**: `EncryptAllWithPassword` /
`EncryptSensitiveWithPassword` mean we cannot read parameter values or connection
strings without the password — those must surface as warnings, not silent blanks.

## Current behaviour & the precise gap

Tracing the existing code:

- **Connection managers** are parsed only from the package XML
  (`parser.py:172`). They are barely *used*: `source.py:_connection_name` maps a
  component's `<connection>` to a CM name for the flat-file staging-table hint;
  table names otherwise come from the component's `OpenRowset`/`TableName`
  property (`base.py:table_name`), and the connection string is **not** woven
  into the emitted SQL at all.
- **Variables** are parsed (`User::`, `System::`) but **project and package
  parameters are not**. The expression lexer happily tokenises
  `@[$Project::X]` / `@[$Package::X]` (`lexer.py:84`), and the resolver records
  `(namespace, name)` (`context.py:make_variable_resolver`), but
  `generator.py:_declarations` looks the reference up in `package.variables`
  only — so a `$Project::` reference becomes
  `DECLARE @X NVARCHAR(4000) = '';` with an empty value and a generic type.

So for a project-deployment package the conversion is **lossy in two concrete
ways**:

1. Every project/package parameter referenced by a Derived Column / Conditional
   Split / property expression emits as an empty, untyped `DECLARE`.
2. A source/destination whose connection (and therefore database/schema) is a
   *project* connection manager has no way to resolve it — only the bare table
   name survives.

## Design overview

Introduce a **`Project` aggregate** that holds project parameters and project
connection managers, parsed once from the expanded directory, and **thread it
into conversion** as optional context. Resolution becomes scoped:

```
parameter/variable lookup order:  User/System vars  →  $Package::  →  $Project::
connection-manager lookup order:  package CM (by id/name)  →  project CM
```

When no project context is supplied, behaviour is **exactly as today** — this is
purely additive, so every existing test stays green.

## Work breakdown

### 1. IR additions (`model.py`)

- `class Parameter` — `namespace` (`"Project"` | `"Package"`), `name`,
  `data_type` (.NET TypeCode int as string), `value`, `sensitive: bool`,
  `required: bool`. A `qualified` property mirroring `Variable`.
- Extend `ConnectionManager` with `scope: str = "package"` (`"package"` |
  `"project"`) so diagnostics can distinguish them; it already has a
  `properties` dict.
- `class Project` — `name`, `protection_level: str`, `parameters:
  list[Parameter]`, `connection_managers: list[ConnectionManager]`,
  `package_names: list[str]`, `source_dir: str`.
- `Package` gains `parameters: list[Parameter]` (package params from the `.dtsx`)
  and an optional back-reference `project: Project | None = None` (or pass the
  project alongside — see §5 for the threading choice).

### 2. Parsers

Reuse the namespace-agnostic helpers already in `parser.py` (`_local`, `_prop`,
`_child`, `_children`).

- `parse_project_manifest(path) -> ProjectManifestInfo` — protection level,
  version, package list. New module `msb_ssis2sql/project.py` (keeps `parser.py`
  focused on a single `.dtsx`).
- `parse_project_params(path) -> list[Parameter]` — read `Project.params`.
- `parse_conmgr_file(path) -> ConnectionManager` — wrap the existing
  `_parse_connection_manager`; the file root is a `<DTS:ConnectionManager>`, so
  factor that function out of `parser.py` for reuse (or expose it).
- `parse_package_parameters(root) -> list[Parameter]` — read
  `<DTS:PackageParameters>/<DTS:PackageParameter>` inside a `.dtsx`; wire into
  `parse_root` so standalone packages benefit too.
- `load_project(dir) -> Project` — orchestrates the three file parsers; presence
  of `@Project.manifest` is what marks a directory as an expanded project.

### 3. Parameter typing + DECLARE generation (`generator.py`)

- New `sqltypes` helper: `param_type_to_tsql(type_code) -> str` mapping .NET
  TypeCodes to T-SQL (`18→NVARCHAR(4000)`, `9→INT`, `11→BIT`, `7→FLOAT`,
  `15→DECIMAL(38,...)`, `16→DATETIME2`, …), defaulting to `NVARCHAR(4000)`.
- Rework `_declarations` to resolve a referenced `(namespace, name)` across the
  merged scope (User var → package param → project param), emit a **typed**
  `DECLARE` with the **real default value**, and:
  - mark sensitive params: `DECLARE @X … = NULL; -- SENSITIVE: value not
    exported (encrypted)` + a conversion warning.
  - comment the source scope (`$Project::` vs `User::`) for traceability.

### 4. Connection-manager resolution (`transforms/`)

- Add `BuildContext.resolve_connection_manager(component) -> ConnectionManager |
  None`: search `package.connection_managers` by ref_id/id, then the project's
  connection managers, then by name. `source.py:_connection_name` and the
  flat-file staging path use it.
- **Optional, behind `ConvertOptions.qualify_from_connection` (default off):**
  parse `Initial Catalog` / `Data Source` out of a resolved OLE DB connection
  string and qualify emitted tables as `[db].[schema].[table]`. Off by default
  because it changes existing output; documented as opt-in.

### 5. Threading the project into conversion

- `BuildContext` gains `project: Project | None`. The variable resolver and CM
  resolver consult it when present.
- Public API:
  - `convert_project(project_dir, options) -> dict[str, ConversionResult]` —
    load the project once, convert every package with the shared context.
  - `convert_file` / `convert_package` gain an optional `project: Project | None`
    parameter (keyword-only, default `None`) — fully backward compatible.

### 6. `convert-tree` integration (`batch.py`)

- Before converting a directory's `.dtsx` files, check for `@Project.manifest`
  in that directory; if present, `load_project` once and pass the `Project` to
  every package conversion in that directory.
- Record project context in `_batch_warnings.log` (protection level, # project
  params, # project connection managers) for auditability.

### 7. Extraction side (`packages/store_ssisdb.py`, `extractor.py`)

The extractor must *write* the project files so conversion can read them.

- New `--expanded` mode (or make it the default for the catalog store): instead
  of writing only `*.dtsx`, write the **full expanded project tree** per project:
  `<out>/<folder>/<project>/{@Project.manifest, Project.params, *.conmgr,
  *.dtsx}`. Reuse the already-unzipped `zipfile` members — `ispac_to_dtsx`
  generalises to `ispac_members(blob) -> dict[str, bytes]` filtered by the
  caller.
- Keep the manifest (`_packages_manifest.json`) but add a `project_files` list
  per project so downstream tooling knows the params/conmgr/manifest paths.
- This makes extraction → `convert-tree` a faithful, lossless round-trip for
  project-deployment projects.

### 8. CLI

- `convert-tree` auto-detects expanded projects (no new flag needed — driven by
  `@Project.manifest` presence).
- Optional `convert-project DIR OUT` subcommand for converting a single expanded
  project explicitly.
- `extract-packages --expanded` (catalog store) to emit the full project tree.

### 9. Protection level / encryption

- `EncryptAllWithPassword` → the `.dtsx` bodies themselves are encrypted; parsing
  fails. Detect via the manifest and emit one clear warning per package
  ("package encrypted with password — cannot convert; re-export with a lower
  protection level").
- `EncryptSensitiveWithPassword` / `*WithUserKey` → non-sensitive content is
  readable; sensitive parameter values and sensitive parts of connection strings
  (passwords) are not. Convert what we can; flag the rest.

## Tests & fixtures

- New fixture `tests/fixtures/expanded_project/` — a hand-built expanded project:
  `@Project.manifest`, `Project.params` (a String + an Int + a Sensitive param),
  two `*.conmgr` (OLEDB + FLATFILE), and two `.dtsx` that reference
  `$Project::` params and a project connection manager.
- Unit tests:
  - `parse_project_params` / `parse_conmgr_file` / `parse_project_manifest`
    shapes and TypeCode mapping.
  - `parse_package_parameters` from a `.dtsx`.
  - resolver precedence (User → Package → Project) and CM fallback.
  - typed, real-valued DECLAREs; sensitive → `NULL` + warning.
  - `EncryptAllWithPassword` manifest → per-package warning, no crash.
  - `convert_project` end-to-end on the fixture; golden SQL.
  - `convert-tree` auto-detects the fixture project and threads context.
  - regression: converting a plain `.dtsx` with no project is byte-identical to
    today (guards the additive contract).
- Extraction: extend `tests/test_packages_extractor.py` with an `.ispac` fixture
  containing project files; assert `--expanded` writes them and the manifest
  lists them.

## Edge cases & limitations

- **Parameterised connection strings / table names** — a connection string or
  `OpenRowset` can itself be set by a property expression referencing a
  parameter. Phase 1 resolves the *parameter value*; fully evaluating property
  expressions into the emitted SQL is a documented follow-up.
- **Environment references / server-side overrides** — SSISDB environments can
  override project parameters at runtime; those live in the catalog, not the
  `.ispac`. Out of scope (note it).
- **`Data Source`/`Initial Catalog` qualification** stays opt-in to avoid
  churning existing golden output.

## Rollout phases

1. **IR + parsers + `load_project`** (no behaviour change; pure additions).
2. **Typed, real-valued parameter DECLAREs** (project + package params) — the
   highest-value, lowest-risk win.
3. **Connection-manager resolution** (project fallback) + flat-file staging
   names; opt-in connection-string qualification.
4. **`convert_project` + `convert-tree` auto-detection.**
5. **Extraction `--expanded`** so the catalog round-trip is lossless.
6. **Protection-level handling + docs/README.**

## Open questions

- Should opt-in `qualify_from_connection` ever become the default (changes
  existing output), or stay a flag indefinitely?
- For `convert-tree`, should an expanded project collapse into a single
  project-scoped output file/proc set, or keep the current per-package files
  plus a project preamble of shared DECLAREs?
- Do we need to support `.ispac`/`.dtproj` directly (read the zip without
  extracting), or is the expanded directory the only supported input?

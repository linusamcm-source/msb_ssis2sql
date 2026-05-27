# SSIS to Azure Data Factory Conversion — Development Plan

## Context

Migration project converting legacy SSIS packages into a modern Azure Data Factory (ADF) orchestration layer, using a Python-based conversion pipeline and infrastructure-as-code for SQL Agent job equivalents. This document captures the architectural decisions reached during discussion and is structured to support both development planning and a draw.io architecture diagram.

---

## Current State

A working Python repository already exists that:

- Consumes SSIS package files (`.dtsx`)
- Parses each package and emits per-file SQL output
- Handles two distinct logical components found inside SSIS packages:
  - **Data flow logic** — the ETL transformations
  - **SQL Agent job definitions** — scheduling and orchestration metadata

The output of stage one is therefore a corpus of raw SQL files representing the data flow logic, plus extracted SQL Agent job metadata.

---

## Target Architecture

Two separately managed artefact streams flow out of the conversion pipeline:

### Stream 1 — Data Flow Logic → Stored Procedures (ADF-callable)

- Each SSIS data flow becomes a stored procedure (or a small set of them) callable from an ADF `Stored Procedure` activity.
- Stored procs are version-controlled SQL files deployed via the CI/CD pipeline.
- ADF pipelines orchestrate the call sequence, parameterisation, and error handling.

### Stream 2 — SQL Agent Jobs → Infrastructure-as-Code Job Definitions

- SQL Agent jobs stay **decoupled** from the stored procs — they were already separate entities in the SSIS world, so the separation of concerns is preserved.
- Job definitions are codified as IaC artefacts (see "IaC Options" below).
- Where ADF needs to trigger a job, it does so via a stored proc activity calling `msdb.dbo.sp_start_job`, keeping the coupling loose.

---

## Infrastructure-as-Code Options Considered

| Option | Notes | Verdict for this project |
|---|---|---|
| **Bicep** | Microsoft-native, clean declarative syntax over ARM. First-class Azure support. | Strong candidate — native Azure, version-control friendly. |
| **ARM templates** | Lower-level than Bicep, more verbose JSON. | Avoid unless Bicep can't express something. |
| **Terraform** | Cloud-agnostic, mature ecosystem. Useful if multi-cloud is ever on the table. | Strong candidate — portability bonus. |
| **CloudFormation** | AWS-only. | Not applicable. |
| **Python SDK (azure-mgmt-*)** | Imperative provisioning from Python. Flexible, embeds logic. Loses some of the version-control clarity of declarative IaC. | **Preferred** — already writing Python conversion code, keeps the language footprint small. Combine with version-controlled YAML config files for job definitions. |

### Chosen Approach

**Python SDK + YAML config files**

- Python drives the provisioning logic (calls Azure SDK to create / update / delete jobs).
- Job definitions themselves live in version-controlled YAML — declarative, reviewable, environment-templatable.
- Environment variables and per-environment overrides are declared in parameter / environment-specific YAML files (dev, test, prod).

---

## Orchestration — Azure DevOps CI/CD

Instead of relying on SQL Agent for scheduling, the intention is to use Azure DevOps pipelines to kick off jobs:

- DevOps pipelines provide version control, audit trails, branch-based deployment, and integration with the broader CI/CD flow.
- Pipelines can trigger Python entry points that invoke the converted job logic.
- Scheduled triggers (cron-style) and on-demand triggers both supported in DevOps.
- ADF handles intra-pipeline data orchestration; DevOps handles cross-pipeline scheduling and deployment.

**Outstanding dependency:** confirmation from the host organisation on what DevOps infrastructure is available and what guardrails apply.

---

## End-to-End Pipeline (for diagram source)

```
SSIS Package (.dtsx)
        │
        ▼
[Python Converter Repo]
        │
        ├──► SQL files (data flow logic)
        │           │
        │           ▼
        │   [Stored Procedures]
        │           │
        │           ▼
        │   [Azure Data Factory Pipelines]
        │           │
        │           ▼
        │     [Target Database]
        │
        └──► SQL Agent job metadata
                    │
                    ▼
            [YAML job definitions] (version controlled)
                    │
                    ▼
            [Python SDK provisioner] (azure-mgmt-*)
                    │
                    ▼
            [Azure SQL Agent / equivalent job runner]
                    │
                    ▼
            [Triggered by Azure DevOps CI/CD pipelines]
```

---

## Components for draw.io Diagram

Suggested nodes and edges to drop into a draw.io diagram:

### Nodes

1. **SSIS Package (.dtsx)** — source artefact
2. **Python Converter** — existing repo
3. **SQL Output: Data Flow** — generated SQL files
4. **SQL Output: Agent Job Metadata** — extracted job definitions
5. **Stored Procedures** — wrapped data flow logic
6. **YAML Job Config Files** — declarative job definitions, version controlled
7. **Python Provisioner (Azure SDK)** — imperative provisioning layer
8. **Azure Data Factory** — pipeline orchestrator
9. **Azure SQL Database** — target database
10. **Azure DevOps Pipelines** — CI/CD and scheduling
11. **Git Repository** — source of truth for all code, SQL, YAML

### Edges

- SSIS Package → Python Converter
- Python Converter → SQL Output: Data Flow
- Python Converter → SQL Output: Agent Job Metadata
- SQL Output: Data Flow → Stored Procedures
- SQL Output: Agent Job Metadata → YAML Job Config Files
- YAML Job Config Files → Python Provisioner
- Python Provisioner → Azure SQL Agent / Job Runner
- Stored Procedures → Azure Data Factory (called via Stored Procedure activity)
- Azure Data Factory → Azure SQL Database
- Azure DevOps Pipelines → Python Provisioner (trigger)
- Azure DevOps Pipelines → Azure Data Factory (trigger / deploy)
- Git Repository → Azure DevOps Pipelines (source)
- ADF → Job Runner (optional, via `sp_start_job`)

### Suggested Swimlanes / Groupings

- **Source** — SSIS Package, Git Repository
- **Conversion Layer** — Python Converter, SQL outputs, YAML configs
- **Provisioning Layer** — Python Provisioner
- **Runtime Layer** — Stored Procedures, ADF, SQL Agent / Job Runner, Azure SQL Database
- **CI/CD Layer** — Azure DevOps Pipelines

---

## Development Plan — Next Steps

1. **Confirm host org infrastructure** — Azure DevOps availability, subscription / resource group guardrails, identity / RBAC model.
2. **Define the YAML schema** for SQL Agent job definitions (name, schedule, steps, notifications, retry policy, environment overrides).
3. **Extend the Python converter** to emit YAML job definitions alongside the existing SQL output.
4. **Build the Python provisioner** using the Azure SDK (`azure-mgmt-sql`, `azure-mgmt-resource`, etc.) to read YAML and create / update jobs idempotently.
5. **Template the stored procedure deployment** — wrap each generated SQL file as a stored procedure, version controlled, deployed via DevOps.
6. **Author ADF pipelines** that call the stored procs via the Stored Procedure activity, with parameterisation for environment.
7. **Wire up DevOps pipelines** — build / test / deploy stages for SQL, YAML, Python provisioner, and ADF definitions.
8. **Environment parameter files** — separate dev / test / prod YAML overrides.
9. **End-to-end test** with a representative SSIS package.

---

## Open Questions / Decisions Deferred

- Whether Bicep / Terraform should also be in the mix alongside the Python SDK approach (e.g. for non-job infrastructure like the ADF instance itself, the SQL database, networking).
- Scheduling model: pure DevOps pipeline triggers vs. retaining a thin SQL Agent layer triggered by `sp_start_job`.
- How to handle SSIS-specific constructs that don't translate cleanly to T-SQL (e.g. Script Tasks, custom components) — currently assumed to be flagged and handled manually.
- Logging / observability layer — Application Insights, Log Analytics, or custom.

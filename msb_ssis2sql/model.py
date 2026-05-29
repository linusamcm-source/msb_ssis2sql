"""Intermediate representation (IR) of an SSIS package and its data flows.

The IR is deliberately plain: dataclasses with no behaviour beyond a couple of
case-insensitive lookups. The parser populates it; everything downstream reads
from it. Keeping it dumb means a new front-end (a different SSIS version, a
hand-built fixture, a unit test) only has to produce these objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ComponentKind(Enum):
    """Normalised pipeline-component category, resolved from ``componentClassID``."""

    OLEDB_SOURCE = "oledb_source"
    FLATFILE_SOURCE = "flatfile_source"
    OLEDB_DESTINATION = "oledb_destination"
    FLATFILE_DESTINATION = "flatfile_destination"
    DERIVED_COLUMN = "derived_column"
    DATA_CONVERSION = "data_conversion"
    COPY_COLUMN = "copy_column"
    CONDITIONAL_SPLIT = "conditional_split"
    LOOKUP = "lookup"
    AGGREGATE = "aggregate"
    SORT = "sort"
    UNION_ALL = "union_all"
    MERGE = "merge"
    MERGE_JOIN = "merge_join"
    MULTICAST = "multicast"
    ROW_COUNT = "row_count"
    CHARACTER_MAP = "character_map"
    AUDIT = "audit"
    OLEDB_COMMAND = "oledb_command"
    PIVOT = "pivot"
    UNPIVOT = "unpivot"
    SCRIPT = "script"
    SCD = "scd"
    UNKNOWN = "unknown"


@dataclass
class Column:
    """A pipeline column - an output column, or an input column reference.

    Output columns carry ``ref_id`` / ``lineage_id`` that downstream input
    columns point at through ``upstream_lineage_id``.
    """

    ref_id: str = ""
    name: str = ""
    data_type: str = ""              # SSIS short code: i4, wstr, numeric, ...
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    code_page: int | None = None
    lineage_id: str = ""             # often equal to ref_id in modern .dtsx
    usage_type: str = ""             # input columns only: readOnly / readWrite
    upstream_lineage_id: str = ""    # input columns only: id of the source output column
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Port:
    """An ``<input>`` or ``<output>`` of a component."""

    ref_id: str = ""
    name: str = ""
    is_error: bool = False
    synchronous_input_id: str = ""   # outputs: empty/"0" => asynchronous output
    exclusion_group: int = 0         # conditional-split style routing groups
    columns: list[Column] = field(default_factory=list)
    external_columns: list[Column] = field(default_factory=list)  # destination table metadata
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def is_async(self) -> bool:
        """True when the output reshapes the buffer (Aggregate, Sort, Union, ...).

        An asynchronous output lists every output column explicitly; a
        synchronous output lists only *new* columns and passes the rest through.
        """
        sid = (self.synchronous_input_id or "").strip()
        return sid in ("", "0")


@dataclass
class Connection:
    """A component's reference to a connection manager."""

    ref_id: str = ""
    connection_manager_id: str = ""
    connection_manager_ref_id: str = ""
    name: str = ""


@dataclass
class Component:
    """A single pipeline component (source, transform, or destination)."""

    ref_id: str = ""
    name: str = ""
    class_id: str = ""
    kind: ComponentKind = ComponentKind.UNKNOWN
    description: str = ""
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)

    def property(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive custom-property lookup."""
        low = name.lower()
        for key, value in self.properties.items():
            if key.lower() == low:
                return value
        return default

    def non_error_outputs(self) -> list[Port]:
        return [o for o in self.outputs if not o.is_error]

    def error_outputs(self) -> list[Port]:
        return [o for o in self.outputs if o.is_error]


@dataclass
class Path:
    """A ``<path>`` linking one component output to one component input."""

    ref_id: str = ""
    name: str = ""
    start_id: str = ""               # producing output's ref_id
    end_id: str = ""                 # consuming input's ref_id


@dataclass
class DataFlow:
    """A Data Flow Task: a bag of components wired together by paths."""

    name: str = ""
    ref_id: str = ""
    components: list[Component] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)


@dataclass
class ConnectionManager:
    """A connection manager, scoped to a package or to the project."""

    ref_id: str = ""
    name: str = ""
    creation_name: str = ""          # OLEDB, FLATFILE, ADO.NET, ...
    connection_string: str = ""
    scope: str = "package"           # "package" | "project"
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class Variable:
    """A package or project variable referenceable from SSIS expressions."""

    namespace: str = "User"
    name: str = ""
    value: str = ""
    data_type: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.namespace}::{self.name}"


@dataclass
class Parameter:
    """A project or package parameter (``$Project::Name`` / ``$Package::Name``).

    Distinct from :class:`Variable`: parameters are the project-deployment
    model's typed, defaulted inputs, defined in ``Project.params`` or a
    package's ``<DTS:PackageParameters>`` block. ``data_type`` is the raw type
    code as stored (a .NET ``TypeCode`` for project params); ``sensitive`` marks
    a value withheld under an ``Encrypt*`` protection level.
    """

    namespace: str = "Project"       # "Project" | "Package"
    name: str = ""
    data_type: str = ""
    value: str = ""
    sensitive: bool = False
    required: bool = False

    @property
    def qualified(self) -> str:
        return f"${self.namespace}::{self.name}"


@dataclass
class Project:
    """An expanded SSIS project (the unzipped contents of an ``.ispac``).

    Holds the project-scoped artefacts a package references but does not itself
    contain: project parameters, shared (project) connection managers, the
    protection level, and the project's package list.
    """

    name: str = ""
    protection_level: str = ""
    parameters: list[Parameter] = field(default_factory=list)
    connection_managers: list[ConnectionManager] = field(default_factory=list)
    package_names: list[str] = field(default_factory=list)
    source_dir: str = ""

    @property
    def is_password_encrypted(self) -> bool:
        """True when values/connection strings are unreadable without a password."""
        return self.protection_level in (
            "EncryptAllWithPassword",
            "EncryptSensitiveWithPassword",
        )


@dataclass
class Executable:
    """A control-flow executable (data flow, exec package task, sequence container, etc.)."""

    ref_id: str = ""
    name: str = ""
    kind: str = "other"  # data_flow | exec_sql | exec_package | sequence_container | other


@dataclass
class ExecutePackageTask:
    """An ExecutePackageTask in the control flow."""

    ref_id: str = ""
    name: str = ""
    package_name: str = ""
    package_path: str = ""
    precedence_predecessors: list[str] = field(default_factory=list)


@dataclass
class PrecedenceConstraint:
    """A DTS:PrecedenceConstraint between two executables."""

    from_ref: str = ""
    to_ref: str = ""
    value: str = "Success"   # Success | Failure | Completion
    eval_op: str = "Constraint"


@dataclass
class Package:
    """The whole parsed package."""

    name: str = ""
    source_path: str = ""
    data_flows: list[DataFlow] = field(default_factory=list)
    connection_managers: list[ConnectionManager] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)   # package parameters
    exec_sql_tasks: list[str] = field(default_factory=list)    # raw SQL from control flow
    executables: list[Executable] = field(default_factory=list)
    execute_package_tasks: list[ExecutePackageTask] = field(default_factory=list)
    precedence_constraints: list[PrecedenceConstraint] = field(default_factory=list)
    project: Project | None = None                              # project context, if any

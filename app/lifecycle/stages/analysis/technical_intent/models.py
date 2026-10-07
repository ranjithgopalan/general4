"""Data models for technical intent analysis.

All structures needed for pattern detection, impact tracing, blind spot detection,
and change list generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TechnicalPattern(Enum):
    """Detected technical patterns in requirements."""

    FIELD_ADDITION = "Add field to screen/table"
    FIELD_REMOVAL = "Remove field from screen/table"
    FIELD_RENAME = "Rename field in screen/table"
    FIELD_TYPE_CHANGE = "Change field type/size"

    SCREEN_CREATION = "Create new screen"
    SCREEN_MODIFICATION = "Modify existing screen"
    SCREEN_DELETION = "Remove screen"

    PROCESS_ADDITION = "Add new process/workflow"
    PROCESS_MODIFICATION = "Modify existing process"
    INTEGRATION_ADDITION = "Add integration/ESB route"
    INTEGRATION_MODIFICATION = "Update integration"

    API_ENDPOINT_ADD = "Add new API endpoint"
    API_ENDPOINT_MODIFY = "Modify API endpoint"

    DATABASE_SCHEMA_CHANGE = "Schema change (DDL)"
    DATABASE_MIGRATION = "Data migration"

    UNKNOWN = "Unknown/unrecognized pattern"


@dataclass
class FieldSpec:
    """Field specification for field additions."""
    name: str
    type: str | None = None      # VARCHAR, INT, BOOLEAN, etc.
    size: int | None = None      # 20, 255, etc.
    nullable: bool = True
    default_value: str | None = None


@dataclass
class TechnicalExtraction:
    """Result of pattern extraction."""

    pattern: TechnicalPattern
    target_screen: str | None = None   # "Basic Information", or screen ID
    target_table: str | None = None    # "ASACDP_QUOTE"
    fields: list[FieldSpec] = field(default_factory=list)
    flows_affected: list[str] = field(default_factory=list)  # ["Renewal", "New Business"]

    # For other patterns
    integration_type: str | None = None  # "ESB", "API", "ETL"
    source_system: str | None = None
    target_system: str | None = None
    data_to_pipe: list[str] = field(default_factory=list)

    confidence: float = 0.0
    explanation: str = ""


@dataclass
class AffectedComponent:
    """A component (node) affected by the change."""

    id: str
    label: str = ""
    kind: str = ""  # Screen, Workflow, Process, Component, API, Table, Integration, System
    source_locus: str | None = None
    confidence: float = 0.9


@dataclass
class ImpactResult:
    """Complete technical impact analysis."""

    pattern: TechnicalPattern
    extraction: TechnicalExtraction

    # Raw requirement text — carried through so downstream steps (blind-spot keyword checks) can use it.
    requirement_text: str = ""

    # Affected components by kind
    affected_screens: list[AffectedComponent] = field(default_factory=list)
    affected_workflows: list[AffectedComponent] = field(default_factory=list)
    affected_processes: list[AffectedComponent] = field(default_factory=list)
    affected_code: list[AffectedComponent] = field(default_factory=list)  # CMP, API
    affected_databases: list[AffectedComponent] = field(default_factory=list)
    affected_integrations: list[AffectedComponent] = field(default_factory=list)
    affected_downstream_systems: list[AffectedComponent] = field(default_factory=list)

    # Files to change
    files_to_change: list[FileChange] = field(default_factory=list)

    # Blind spots / risks
    blind_spots: list[BlindSpot] = field(default_factory=list)

    # Change list
    change_list: list[ChangeTask] = field(default_factory=list)

    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    risk_reasons: list[str] = field(default_factory=list)

    def all_affected_components(self) -> list[AffectedComponent]:
        """Get all affected components in order."""
        return (
            self.affected_screens
            + self.affected_workflows
            + self.affected_processes
            + self.affected_code
            + self.affected_databases
            + self.affected_integrations
            + self.affected_downstream_systems
        )


@dataclass
class FileChange:
    """A file that needs to be changed."""

    priority: str = "P0"  # P0, P1, P2
    type: str = "UNKNOWN"  # UI, BACKEND, DATABASE, INTEGRATION, CONFIGURATION
    file_path: str = ""
    action: str = ""  # What to do
    owner: str = ""  # Team responsible
    depends_on: list[str] = field(default_factory=list)  # Other file changes this depends on


@dataclass
class BlindSpot:
    """A potential gap or risk in the impact analysis."""

    severity: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    type: str = ""  # MISSING_SCHEMA, MISSING_INTEGRATION, DOWNSTREAM_INCOMPATIBILITY, etc.
    description: str = ""
    action: str = ""  # What to do about it
    impact: str = ""  # What could go wrong


@dataclass
class ChangeTask:
    """A specific change task for the change list."""

    priority: str = "P0"  # P0, P1, P2
    task_description: str = ""
    change_type: str = ""  # DATABASE, UI, BACKEND, INTEGRATION, VERIFICATION
    files_affected: list[str] = field(default_factory=list)
    owner: str = ""
    depends_on: list[str] = field(default_factory=list)
    estimated_complexity: str = "MEDIUM"  # LOW, MEDIUM, HIGH

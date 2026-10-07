"""Traceability data models (Pydantic schemas)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class KBCard(BaseModel):
    """KB card reference in traceability chain."""
    kb_id: Optional[str] = None  # Optional: validator generates if missing
    kind: str
    label: str
    source_locus: Optional[str] = None  # Optional: may not have source evidence
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)

    @model_validator(mode='before')
    @classmethod
    def handle_legacy_format(cls, data: Any) -> Any:
        """Handle legacy ImpactCitation format with 'id' instead of 'kb_id'."""
        if isinstance(data, dict):
            # Convert 'id' to 'kb_id' if present
            if 'id' in data and 'kb_id' not in data:
                data['kb_id'] = data.pop('id')
            # Generate kb_id from kind + label if missing
            if not data.get('kb_id'):
                kind = data.get('kind', 'UNKNOWN')
                label = data.get('label', '')
                # Generate a simple kb_id from kind and label
                data['kb_id'] = f"{kind}-{label[:20].upper().replace(' ', '-')}"
            # Provide default confidence if missing
            if 'confidence' not in data:
                data['confidence'] = 0.9
        return data


class SystemAffected(BaseModel):
    """System affected by change."""
    system_id: Optional[str] = None  # Can be None if using legacy 'id' field
    system_name: Optional[str] = None  # Can be None if not provided
    components_changed: List[str] = []
    reason: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def handle_legacy_id_format(cls, data: Any) -> Any:
        """Handle legacy format where 'id' is used instead of 'system_id'."""
        if isinstance(data, dict):
            # If 'id' exists but 'system_id' doesn't, use 'id' as system_id
            if 'id' in data and 'system_id' not in data:
                data['system_id'] = data.pop('id')
            # If we don't have system_name but have system_id, use system_id as name
            if 'system_name' not in data and 'system_id' in data:
                data['system_name'] = data['system_id']
        return data


# ============================================================================
# Phase 2: Systems, Scope, and Metrics Models
# ============================================================================

class WorkspaceSystem(BaseModel):
    """Extracted system impact for workspace."""
    id: Optional[int] = None
    system_id: str
    system_name: str
    system_type: Optional[str] = None  # backend | frontend | database | integration
    impact_level: str = "medium"  # low | medium | high | critical
    components_changed: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None
    source_artifact_id: Optional[str] = None
    source_locus: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    extraction_method: str = "regex"  # regex | llm | manual
    created_at: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def convert_datetime_to_string(cls, data: Any) -> Any:
        """Convert datetime objects to ISO format strings."""
        if isinstance(data, dict):
            if isinstance(data.get('created_at'), datetime):
                data['created_at'] = data['created_at'].isoformat()
        return data


class WorkspaceScopeItem(BaseModel):
    """Scope categorization item (IN/OUT/DEFERRED)."""
    id: Optional[int] = None
    item_id: str
    scope_category: str  # IN_SCOPE | OUT_OF_SCOPE | DEFERRED
    item_type: Optional[str] = None  # feature | bug | enhancement | infrastructure
    item_description: str
    rationale: Optional[str] = None
    related_kb_card_id: Optional[str] = None
    related_artifact_id: Optional[str] = None
    priority: Optional[str] = None  # High | Medium | Low (for DEFERRED)
    created_at: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def convert_datetime_to_string(cls, data: Any) -> Any:
        """Convert datetime objects to ISO format strings."""
        if isinstance(data, dict):
            if isinstance(data.get('created_at'), datetime):
                data['created_at'] = data['created_at'].isoformat()
        return data


class WorkspaceMetrics(BaseModel):
    """Coverage and traceability metrics for workspace."""
    workspace_id: str
    total_artifacts: int = 0
    total_relationships: int = 0
    coverage_pct: float = 0.0
    orphan_count: int = 0
    artifacts_by_stage: Optional[Dict[str, int]] = None  # e.g., {"ANALYSIS": 1, "FSD": 1}
    relationships_by_type: Optional[Dict[str, int]] = None  # e.g., {"derives_from": 2}
    systems_affected: int = 0
    scope_in_scope: int = 0
    scope_out_of_scope: int = 0
    scope_deferred: int = 0
    calculated_at: Optional[str] = None

    @model_validator(mode='before')
    @classmethod
    def convert_datetime_to_string(cls, data: Any) -> Any:
        """Convert datetime objects to ISO format strings."""
        if isinstance(data, dict):
            if isinstance(data.get('calculated_at'), datetime):
                data['calculated_at'] = data['calculated_at'].isoformat()
        return data


class WorkspaceSystemsResponse(BaseModel):
    """Response for systems queries."""
    workspace_id: str
    count: int
    systems: List[WorkspaceSystem]


class WorkspaceScopeResponse(BaseModel):
    """Response for scope queries."""
    workspace_id: str
    count: int
    in_scope: List[WorkspaceScopeItem]
    out_of_scope: List[WorkspaceScopeItem]
    deferred: List[WorkspaceScopeItem]

    @property
    def total_items(self) -> int:
        return len(self.in_scope) + len(self.out_of_scope) + len(self.deferred)


class ArtifactData(BaseModel):
    """Single stage artifact in chain."""
    artifact_id: str
    stage: str
    status: str  # PENDING, ACCEPTED, BLOCKED, READY_FOR_QA, etc.
    created_by: str
    created_at: Optional[str] = None  # Optional: allows loading legacy data without created_at
    what_changed: List[str] = []
    kb_sources: List[KBCard] = []
    approved_by: Optional[Dict[str, Any]] = None  # {name, date}
    systems_affected: List[SystemAffected] = []
    quality_metrics: Optional[Dict[str, Any]] = None  # {tests, coverage, etc.}


class ImpactMap(BaseModel):
    """Cumulative impact across all stages."""
    kb_cards_affected: List[KBCard] = []
    systems_affected: List[SystemAffected] = []
    database_changes: List[Dict[str, Any]] = []


class Manifest(BaseModel):
    """Complete traceability manifest for workspace."""
    id: str
    workspace_id: str
    requirement: str
    classification: Optional[str] = None  # NEW, ENHANCEMENT, EXISTING
    kb_version: str = "db"

    # Artifact chain (builds up as stages complete)
    artifact_chain: Dict[str, ArtifactData] = Field(default_factory=dict)

    # Cumulative impact
    impact_map: ImpactMap = Field(default_factory=ImpactMap)

    # Metadata
    created_at: str
    updated_at: str

    class Config:
        json_schema_extra = {
            "example": {
                "id": "manifest-ws-123",
                "workspace_id": "ws-123",
                "requirement": "Add receipt delivery tracking",
                "classification": "ENHANCEMENT",
                "kb_version": "db",
                "artifact_chain": {
                    "stage_1_analysis": {
                        "artifact_id": "analysis-ws-123",
                        "stage": "analysis",
                        "status": "ACCEPTED",
                        "created_by": "kb-change-classifier",
                        "created_at": "2026-08-13T10:35:00Z",
                        "what_changed": [
                            "Matched 6 KB cards",
                            "Identified 4 systems"
                        ],
                        "kb_sources": [
                            {
                                "kb_id": "BR-JAUTO-001",
                                "kind": "BusinessRule",
                                "label": "Track delivery status",
                                "source_locus": "input/Auto/PEGA/Rules.xml §4.5 char:2340-2450",
                                "confidence": 0.95
                            }
                        ]
                    }
                },
                "impact_map": {
                    "kb_cards_affected": [],
                    "systems_affected": [],
                    "database_changes": []
                },
                "created_at": "2026-08-13T10:35:00Z",
                "updated_at": "2026-08-13T10:35:00Z"
            }
        }


class ArtifactLink(BaseModel):
    """Link between two artifacts in chain (auto-generated ID from DB)."""
    workspace_id: str
    source_artifact_id: str
    target_artifact_id: str
    link_type: str  # DERIVES_FROM, CREATES, IMPLEMENTS, GROUNDS
    reason: Optional[str] = None
    source_locus_snapshot: Optional[str] = None  # Immutable at creation time
    created_at: str


class TracingResult(BaseModel):
    """Complete tracing result for a workspace."""
    workspace_id: str
    timeline: List[Dict[str, Any]]  # Rendered timeline
    artifact_chain: Dict[str, ArtifactData]  # All artifacts
    kb_grounding: List[KBCard]  # KB cards at start
    systems_affected: List[SystemAffected]  # Cumulative systems
    scope_analysis: Dict[str, Any]  # Analysis of scope (files, tables, etc.)

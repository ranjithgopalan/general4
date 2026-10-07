"""Typed DevDocument artifact — single source for /dev page + AIG .docx export.

5 sections: implementation_plan · code_stubs · integration_wiring · development_notes · open_items.
Every generated item carries a KB card ID, ownership (AUTO/HYBRID/DEV-TODO), and gap_status.
Code generation (actual .ts/.java files) is a separate step via the Supervisor→Planner→Executor→Reviewer
LangGraph codegen pipeline (ChatBedrockConverse; replaces former Claude CLI subprocess).

Cross-repo:
  S3-plugins  — section ownership matrix (AUTO/HYBRID/DEV-TODO), gap_status enum, dev_gaps.yaml
  Connected-layer — gap_status values (direct/derived/partial/gap/out_of_scope/deferred)
  Genlite     — section_basis provenance dict (per-section KB-explicit/KB-inferred/stub counts)
  IMAD/LMOD   — code stub shapes (angular_builder + springboot_builder patterns)

P1 additions: FRDelta, story_text, gherkin_ac, definition_of_done in ImplTask (from StoriesDocument).
P2 additions: fr_deltas, business_rules_summary in ImplTask (from FSDDocument — best-effort).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.lifecycle.stages.analysis.schema import ImpactCitation  # noqa: F401 — re-exported
from app.lifecycle.stages.fsd.schema import Stub  # noqa: F401 — re-exported for open_items

# ── KB kind groups the Developer persona reads (personas.json §developer) ───────
DEV_KINDS = frozenset({"System", "Integration", "Component", "ApiOp", "Screen", "FunctionalReq"})

# ── Tech stack IDs (detected from source_locus) ─────────────────────────────────
Stack = Literal[
    "angular",          # TypeScript / Angular — UI-NewBusiness, UI-Endorsement repos
    "java_springboot",  # Java / Spring Boot — Services-NewBusiness, csvimport-services repos
    "ibm_esb",          # IBM ESB / ESQL — ESB repos (WSDL, ESQL, msgflow)
    "adobe_xdp",        # Adobe LiveCycle XDP — AdobeForms repo (form definition, not Angular)
    "sql",              # SQL / Oracle — ASACDP database repo
    "java_legacy",      # Legacy Java/JSP — WebOnline-Adapter / FREIA adapter
    "pega",             # PEGA — runtime exports only (no source stub, reference doc only)
    "unknown",          # No source_locus or unrecognised path
]

# ── Ownership (S3-plugins FSD-generator section ownership matrix) ────────────────
Ownership = Literal["AUTO", "HYBRID", "DEV-TODO"]

# ── Change-spec lane (docs/24 §D — the four first-class developer lanes) ─────────
Lane = Literal["frontend", "backend", "integration", "db"]

# ── Gap status (Connected-layer L2 phase4_l2.py pattern) ────────────────────────
GapStatus = Literal[
    "direct",           # KB card → code implementation 1:1 (confidence ≥ 0.7 token overlap)
    "derived",          # inferred from adjacent cards (0.4 – 0.69)
    "partial",          # KB card partially covers the story
    "gap",              # no KB card / card confidence < 0.4 → write to dev_gaps.yaml
    "out_of_scope",     # explicitly not in this workspace's stories
    "deferred",         # acknowledged but pushed to v2
    "compliance_gap",   # compliance requirement, cannot defer — escalate
]


class FRDelta(BaseModel):
    """As-is → to-be delta for one Functional Requirement (P2 — from FSDDocument).

    Gives the developer the exact behavior change to implement: what the current
    code does (as_is) and what it must do after the change (to_be).
    """

    fr_id: str
    title: str = ""
    as_is: str | None = Field(
        default=None, description="Current behavior verbatim from FSD (KB-grounded)"
    )
    to_be: str | None = Field(
        default=None, description="Required behavior after change (LLM-reasoned or BA-TODO stub)"
    )
    source_locus: str | None = None
    source_type: str = "fsd_derived"


class ImplTask(BaseModel):
    """One implementation task per story from SRD traceability.

    Enriched with:
    - P1: story_text (As-a/I-want/So-that) + gherkin_ac + definition_of_done from StoriesDocument
    - P2: fr_deltas (as-is→to-be per FR) + business_rules_summary from FSDDocument
    """

    story_id: str
    title: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    components_to_modify: list[str] = Field(
        default_factory=list, description="CMP-*/SYS-* IDs"
    )
    apis_to_implement: list[str] = Field(
        default_factory=list, description="API-* IDs"
    )
    integrations_to_wire: list[str] = Field(
        default_factory=list, description="INT-* IDs"
    )
    screens_to_update: list[str] = Field(
        default_factory=list, description="SCR-* IDs"
    )
    ownership: Ownership = "DEV-TODO"
    gap_status: GapStatus = "gap"
    match_confidence: float = 0.0
    status: str = Field(
        default="TODO", description="TODO | IN_PROGRESS | DONE — developer-set"
    )
    source_type: str = "srd_derived"

    # ── P1: Story context (from StoriesDocument) ─────────────────────────────
    story_text: str = Field(
        default="",
        description=(
            "Full user story prose: 'As a {role}, I want {need}, so that {outcome}'. "
            "Populated from StoriesDocument.stories[story_id]. "
            "Empty string when StoriesDocument is not available."
        ),
    )
    gherkin_ac: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Gherkin acceptance criteria: {'given': '...', 'when': '...', 'then': '...'}. "
            "Populated from StoriesDocument.stories[story_id]. "
            "Empty dict when not available — developer cannot verify completeness."
        ),
    )
    definition_of_done: list[str] = Field(
        default_factory=list,
        description=(
            "Auto-generated checklist derived from gherkin_ac + kb_ids. "
            "Each item is a verifiable completion criterion. "
            "Empty when gherkin_ac is empty."
        ),
    )
    story_priority: str = Field(
        default="Medium",
        description="Priority from StoriesDocument (High | Medium | Low)",
    )
    story_points: int = Field(
        default=0,
        description="Story points from StoriesDocument (New=8 / Enhancement=5 / Derived=3)",
    )

    # ── P2: FSD delta (from FSDDocument — best-effort) ───────────────────────
    fr_deltas: list[FRDelta] = Field(
        default_factory=list,
        description=(
            "As-is → to-be delta per Functional Requirement linked to this story. "
            "Populated from FSDDocument when available. "
            "Gives the developer the exact behavior change to implement."
        ),
    )
    business_rules_summary: list[str] = Field(
        default_factory=list,
        description=(
            "Business rule text summaries (from FSD BR-* cards) that govern this story. "
            "Surfaced here so the developer knows what logic to implement "
            "without breaching the dev persona's BR-* kind restriction. "
            "Format: '[BR-xxx] rule_text (≤200 chars)'."
        ),
    )


class CodeStub(BaseModel):
    """A generated code stub for one KB card (template-based, no LLM)."""

    story_id: str
    component_id: str = Field(description="CMP-* or API-* KB card ID")
    language: str = Field(
        default="typescript",
        description="typescript | java | esql | sql | reference",
    )
    stub_type: str = Field(
        default="component",
        description="component | service | controller | esb_reference | xdp_reference | sql_migration | pega_reference",
    )
    stack: Stack = "unknown"
    filename: str = ""
    code: str = ""
    ownership: Ownership = "DEV-TODO"
    source_type: str = "kb_derived"
    source_locus: str | None = None


class ChangePlanItem(BaseModel):
    """One row of the high-level Change Plan — file + what to change (no code; the code lives in the
    per-file stub / .md export). Gives a scannable 'which files, what change' overview."""

    file: str = Field(description="real source path (existing) or suggested path (new)")
    layer: str = Field(default="", description="Frontend | Backend | Data | Integration | Forms | Other")
    action: str = Field(default="", description="Modify existing file | Create new file")
    change: str = Field(default="", description="concise, plain-language what-to-change instruction")
    component_id: str = ""


class WiringTask(BaseModel):
    """One integration to wire — one per INT-* from SRD integration_design."""

    integration_id: str
    label: str = ""
    protocol: str | None = Field(
        default=None,
        description="REST | SOAP | ESB | DB | Event | gRPC — None means ARCH-TODO inherited",
    )
    from_component: str | None = None
    to_component: str | None = None
    wiring_note: str = ""
    ownership: Ownership = "DEV-TODO"
    status: str = "TODO"


class ChangeSpec(BaseModel):
    """docs/24 §D — a first-class developer change spec on one of four lanes.

    Makes "lane" a real section (not just a presentational ``stack`` label): each spec ties KB IDs to
    the concrete files to touch and the as-is→to-be behaviour, grounded in the SRD. Screens and DB
    entities become change specs here (the code-stub builder only covers CMP-*/API-*)."""

    lane: Lane
    id: str = Field(description="stable spec id, e.g. 'fe-SCR-JAUTO-012'")
    title: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(
        default_factory=list, description="real file(s) to modify (from source_locus) — [] = new file DEV-TODO"
    )
    as_is: str = ""
    to_be: str = ""
    ownership: Ownership = "DEV-TODO"
    gap_status: GapStatus = "gap"
    source_locus: str | None = None


class DevNote(BaseModel):
    """A human-written development note (append-only, writeAccess: workspace.development-notes)."""

    note_id: str
    story_id: str | None = None
    text: str
    author: str = "developer"
    created_at: str | None = None


class DevGap(BaseModel):
    """A gap recorded when a KB card is missing or confidence is too low.

    S3-plugins rule: write to dev_gaps.yaml — never emit invented content in code stubs.
    """

    story_id: str | None = None
    component_id: str = ""
    description: str
    gap_status: GapStatus = "gap"
    sme_required: bool = True
    source: str = "conductor"


class CodegenFile(BaseModel):
    """One file written by the Claude Agent SDK codegen step."""

    file_path: str
    language: str = ""
    story_id: str | None = None
    component_id: str | None = None
    written_at: str | None = None


class DevDocument(BaseModel):
    """The full Developer stage artifact — single source for the /dev page and AIG .docx export."""

    workspace_id: str
    srd_ref: str | None = Field(
        default=None, description="artifact_id of the accepted SRDDocument"
    )
    stories_ref: str | None = None
    kb_version: str
    persona: str
    template_id: str = "aig-dev"
    template_version: str = "v1"
    requirement: str = ""
    change_class: str = ""
    grounding_score: float = 1.0
    stub_count: int = 0

    # ── 5 sections ────────────────────────────────────────────────────────────
    change_plan: list[ChangePlanItem] = Field(
        default_factory=list,
        description="High-level 'files to change + what to change' summary (no code; derived from code_stubs)",
    )
    implementation_plan: list[ImplTask] = Field(
        default_factory=list, description="One ImplTask per story from SRD traceability"
    )
    code_stubs: list[CodeStub] = Field(
        default_factory=list, description="Template-based stubs per CMP-*/API-* card"
    )
    integration_wiring: list[WiringTask] = Field(
        default_factory=list, description="One WiringTask per INT-* from SRD integration_design"
    )
    change_specs: list[ChangeSpec] = Field(
        default_factory=list,
        description="docs/24 §D — first-class change specs on the frontend/backend/integration/db lanes",
    )
    development_notes: list[DevNote] = Field(
        default_factory=list, description="Human-written notes (append-only via /dev/notes)"
    )
    open_items: list[Stub] = Field(
        default_factory=list, description="DEV-TODO coverage gaps (from check_dev_coverage)"
    )

    # ── Codegen output (written by Claude Agent SDK step, not by generate_stream) ──
    codegen_files: list[CodegenFile] = Field(
        default_factory=list, description="Files written by the Claude CLI codegen agent"
    )
    codegen_output_dir: str | None = Field(
        default=None, description="Local path where codegen files were written"
    )

    # ── Dev gaps (S3 GAP HANDLING rule) ──────────────────────────────────────
    dev_gaps: list[DevGap] = Field(
        default_factory=list, description="Missing KB evidence — never emitted as invented content"
    )

    # ── Provenance (Genlite section_basis pattern) ────────────────────────────
    section_basis: dict[str, str] = Field(default_factory=dict)
    abstained: bool = False
    generated_at: str | None = None


class DriftNote(BaseModel):
    """docs/27 §7 — a KB-drift signal: a live file diverged from the KB's recorded ``as_is``.

    Filed as an OPEN review item — never an automatic KB edit. The moat: a source-verified change is
    required, adjudicated by an SME → kb-refresh (CLAUDE.md §5 feedback loop)."""

    file: str
    expected: str = Field(default="", description="the KB's as-is for this file/spec")
    actual: str = Field(default="", description="what the developer found in the live repo")
    kb_ids: list[str] = Field(default_factory=list)


class PrSyncRequest(BaseModel):
    """docs/27 §6.1 (P2) — the local Developer plugin syncs its dev outcome back to central by workspace_id.

    Recorded as a ``dev-pr`` artifact (provenance); when ``advance`` is set and the workspace is in
    DEVELOPMENT, the guarded transition to QA_TESTING fires. ``pr_url``/``branch`` are OPTIONAL — a local
    edit run with the git/PR steps disabled still advances the flow (no fake PR). Optional ``kb_drift`` notes
    are filed as OPEN review items (SME-adjudicated; never an automatic KB edit). The state model is
    unchanged — this is the plugin acting as another actor on the DB (docs/27 §4)."""

    pr_url: str | None = Field(default=None, description="URL of the raised PR (omit for a no-PR local run)")
    branch: str | None = Field(default=None, description="branch the PR was raised from, e.g. aidlc/<workspace_id>-<slug>")
    commit: str | None = Field(default=None, description="head commit SHA")
    repo: str | None = Field(default=None, description="repo the PR targets (name/remote)")
    files_changed: list[str] = Field(default_factory=list)
    summary: str = ""
    advance: bool = Field(default=True, description="advance DEVELOPMENT → QA_TESTING on sync")
    kb_drift: list[DriftNote] = Field(
        default_factory=list, description="live-file-vs-KB divergences → OPEN review items (moat: SME adjudicates)"
    )


class PrSyncResult(BaseModel):
    """Result of a dev sync — the recorded artifact id + the (possibly advanced) workspace state."""

    workspace_id: str
    pr_url: str | None = None
    artifact_id: str | None = None
    state: str
    advanced: bool = False
    drift_recorded: int = 0

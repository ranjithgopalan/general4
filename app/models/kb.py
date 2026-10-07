"""kb.query DTOs — a grounded answer + its citations (docs/04 §6, docs/11)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KbCitation(BaseModel):
    """One grounded card reference (id + kind + the drill locus)."""

    id: str
    kind: str
    label: str
    source_locus: str | None = None


class KbAnswer(BaseModel):
    """The kb.query result — cite-or-abstain: ``abstained=True`` means no grounded cards were found."""

    question: str
    persona: str
    answer: str = ""
    citations: list[KbCitation] = Field(default_factory=list)
    confidence: float = 0.0
    abstained: bool = False
    source: str = "fixture"
    kb_version: str | None = None
    disputed_ids: list[str] = Field(default_factory=list)  # Gap 5: DISPUTED cards in this answer


class GroundingReport(BaseModel):
    """The FE grounding spine's verdict over a generated artifact (docs/04 §7). BLOCK = HIGH severity."""

    verdict: str  # PASS | BLOCK
    cited_ids: list[str] = Field(default_factory=list)
    resolved: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)  # fabricated ids not in the retrieved citations
    coverage: float = 1.0


class AnswerGrounding(BaseModel):
    """Serve-time grounding-gate verdict over a synthesized chatbot answer (docs/20 §10).

    ``PASS`` = grounded; ``ABSTAIN`` = every cited card failed re_anchor (no grounded claim left);
    ``BLOCK`` = a fabricated citation (or an ungrounded number in strict mode).
    """

    verdict: str  # PASS | ABSTAIN | BLOCK
    grounded: bool = False
    cited_ids: list[str] = Field(default_factory=list)
    resolved_ids: list[str] = Field(default_factory=list)
    unresolved_ids: list[str] = Field(default_factory=list)  # fabricated ids -> BLOCK
    unverified_ids: list[str] = Field(default_factory=list)  # cited but not re_anchor-grounded -> dropped
    ungrounded_numbers: list[str] = Field(default_factory=list)  # numbers absent from cited sources
    coverage: float = 1.0


class NameCount(BaseModel):
    """A named bucket with a file/item count (tech-stack entry, code repo)."""

    name: str
    files: int


class FamilyGroup(BaseModel):
    """A display group of KB families (Business / Domain / Screens / Systems / People) + its kinds."""

    group: str
    count: int
    items: list[dict] = Field(default_factory=list)  # [{"kind": "BR", "count": 87}, ...]


class KbOverview(BaseModel):
    """Active-KB Overview (docs/20 §4a, docs/09 kb_versions.coverage_stats) — the RE dashboard payload."""

    kb_version: str
    status: str
    build_date: str | None = None
    signed_off_by: str | None = None
    signed_off_at: str | None = None
    card_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    chunk_count: int = 0
    gates_passed: int = 0
    gates_total: int = 0
    fabricated: int = 0
    review_open: int = 0
    running_jobs: int = 0
    families: dict[str, int] = Field(default_factory=dict)
    family_groups: list[FamilyGroup] = Field(default_factory=list)
    by_product: dict[str, int] = Field(default_factory=dict)
    tech_stack: list[NameCount] = Field(default_factory=list)
    repos: list[NameCount] = Field(default_factory=list)
    source: str = "computed"  # computed | coverage_stats | none


class VersionDiff(BaseModel):
    """Deterministic diff of two KB versions (docs/20 §9) — what changed between builds."""

    from_version: str
    to_version: str
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    unchanged: int = 0


class ClassificationResult(BaseModel):
    """ANALYSIS classification of a requirement vs the KB (docs/04 §4/§8d)."""

    change_class: str  # New | Enhancement | Existing | Derived
    matched_ids: list[str] = Field(default_factory=list)
    affected_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""


class GenerateRequest(BaseModel):
    """Body for ``fe.generate`` (docs/11 §2.3). ``persona`` optional (service-to-service overrides JWT).

    ``requirement_text`` optional: if provided, overrides the workspace requirement for this generation.
    """

    artifact_type: str  # analysis | stories (v1); + fsd | brd | srd | test-plan (v2)
    persona: str | None = None
    requirement_text: str | None = None  # Optional override for requirement


class TraceRow(BaseModel):
    """One AC-3 traceability row (docs/11 §2.3): what a generated item is grounded in."""

    story_id: str | None = None
    kb_ids: list[str] = Field(default_factory=list)
    source_loci: list[str] = Field(default_factory=list)


class FeGenerateResult(BaseModel):
    """The ``fe.generate`` contract result (docs/11 §2.3) — the single FE write/generate path."""

    workspace_id: str
    artifact_type: str
    artifact: str  # generated artifact content (JSON / Markdown / CSV / HTML)
    trace: list[TraceRow] = Field(default_factory=list)
    grounding_score: float = 0.0

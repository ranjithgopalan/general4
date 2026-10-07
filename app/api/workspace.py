"""``/ws`` — FE workspace lifecycle API (docs/19 §6). Framework only (no LLM).

Thin controllers over ``WorkspaceService`` + ``TraceabilityService``. Auth / entitlement are enforced
by the middleware chain; service exceptions (``ResourceNotFoundError`` -> 404, ``ValidationError`` ->
422) render via the ``app.main`` exception handlers, so routes stay declarative.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile, status

from app.core.dependencies import (
    get_current_persona,
    get_intake_service,
    get_persona_registry,
    get_persona_specialist,
    get_traceability_service,
    get_workspace_service,
)
from app.utils.file_parser import merge_parsed_files, parse_file, clean_requirement_text
from app.lifecycle.stages.intake.handler import IntakeService
from app.lifecycle.stages.intake.schema import IntakeArtifact
from app.models.kb import ClassificationResult
from app.models.workspace import (
    AttachArtifactRequest,
    CreateWorkspaceRequest,
    TraceGraph,
    TransitionRequest,
    Workspace,
    WorkspaceArtifact,
    WorkspaceState,
    WorkspaceType,
)
from app.services.graph_provider import GraphProvider, get_graph_provider
from app.services.personas import PersonaRegistry
from app.services.specialists import PersonaSpecialist
from app.services.traceability_service import TraceabilityService
from app.services.workspace_service import WorkspaceService

router = APIRouter(prefix="/ws", tags=["workspace"])

SvcDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
TraceDep = Annotated[TraceabilityService, Depends(get_traceability_service)]
IntakeDep = Annotated[IntakeService, Depends(get_intake_service)]
SpecialistDep = Annotated[PersonaSpecialist, Depends(get_persona_specialist)]
GraphDep = Annotated[GraphProvider, Depends(get_graph_provider)]
RegDep = Annotated[PersonaRegistry, Depends(get_persona_registry)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


async def _impact_ids(graph: GraphProvider, matched: list[str], *, cap: int = 8) -> list[str]:
    """Affected set = the matched cards + their downstream graph neighbours (bounded, best-effort)."""
    affected: set[str] = set(matched)
    for node_id in matched[:cap]:
        try:
            sub = await graph.impact(node_id, direction="downstream")
        except Exception:  # noqa: BLE001 — impact expansion is best-effort enrichment
            continue
        affected.update(n.id for n in sub.nodes)
    return sorted(affected)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a workspace (JSON mode)")
async def create_workspace(body: CreateWorkspaceRequest, svc: SvcDep, intake: IntakeDep) -> Workspace:
    """Create a workspace with requirement text via JSON body.

    For file uploads, use POST /ws/with-files instead.
    """
    ws = await svc.create(
        type=body.type.value,
        title=body.title,
        requirement_text=body.requirement_text,
        route=body.route.value if body.route else None,
        gear_id=body.gear_id,
        pinned_kb_version=body.pinned_kb_version,
    )
    # Persist the S0·INTAKE snapshot (requirement + metadata) to S3 + DB artifact.
    # Best-effort — IntakeService never raises; a failed S3 write does not fail workspace creation.
    await intake.save(ws)
    return ws


@router.post("/with-files", status_code=status.HTTP_201_CREATED, summary="Create a workspace (with files)")
async def create_workspace_with_files(
    type: Annotated[str, Form()],
    title: Annotated[str, Form()],
    svc: SvcDep,
    intake: IntakeDep,
    requirement_text: Annotated[str | None, Form()] = None,
    files: Annotated[list[UploadFile] | None, Form()] = None,
    gear_id: Annotated[str, Form()] = "japan",
    pinned_kb_version: Annotated[str | None, Form()] = None,
) -> Workspace:
    """Create a workspace with optional file attachments (.md, .txt, .docx, .pdf).

    If files are provided:
    1. Parse .md/.txt files as plain text
    2. Parse .docx/.pdf files to extract text
    3. Merge parsed content into requirement_text (or use as fallback if requirement_text is empty)

    Args:
        type: WorkspaceType (NewFeature, Enhancement, TechModernization, Upgrade, DefectResolution)
        title: Workspace title
        requirement_text: Optional requirement text (takes precedence over files)
        files: Optional list of supporting files to parse
        gear_id: Knowledge base domain (default: japan)
        pinned_kb_version: Optional specific KB version to pin to

    Returns:
        Created Workspace

    Raises:
        ValidationError: If type is invalid or no requirement provided (text or files)
    """
    # Parse files if provided
    parsed = []
    if files:
        for f in files:
            if f.size > 0:
                content = await f.read()
                try:
                    parsed_file = parse_file(content, f.filename)
                    parsed.append(parsed_file)
                except ValueError as e:
                    # Log warning but don't fail — unsupported file types are skipped
                    from app.utils.logging import log
                    log.warning(f"[workspace] skipped file {f.filename}: {e}")

    # Build final requirement text: use provided text, or merge parsed files, or error
    final_requirement = requirement_text
    if not final_requirement and parsed:
        final_requirement = merge_parsed_files(parsed)
    elif not final_requirement and not parsed:
        raise ValueError("Requirement text or at least one valid file is required")

    # Clean up the requirement text
    if final_requirement:
        final_requirement = clean_requirement_text(final_requirement)

    # Validate and convert type
    try:
        workspace_type = WorkspaceType(type)
    except ValueError as e:
        raise ValueError(f"Invalid workspace type: {type}. Valid types: {', '.join([t.value for t in WorkspaceType])}") from e

    # Create workspace
    ws = await svc.create(
        type=workspace_type.value,
        title=title,
        requirement_text=final_requirement,
        route=None,  # route defaults from type in service
        gear_id=gear_id,
        pinned_kb_version=pinned_kb_version,
    )
    # Persist the S0·INTAKE snapshot
    await intake.save(ws)
    return ws


@router.get("/{workspace_id}/intake", summary="Get the S0·INTAKE artifact (requirement snapshot)")
async def get_intake(workspace_id: str, intake: IntakeDep) -> IntakeArtifact | None:
    return await intake.get(workspace_id)


@router.get("", summary="List workspaces")
async def list_workspaces(
    svc: SvcDep,
    gear_id: Annotated[str | None, Query()] = None,
    state: Annotated[WorkspaceState | None, Query()] = None,
    type: Annotated[WorkspaceType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Workspace]:
    return await svc.list(
        gear_id=gear_id,
        state=state.value if state else None,
        type=type.value if type else None,
        limit=limit,
        offset=offset,
    )


@router.get("/{workspace_id}", summary="Get a workspace")
async def get_workspace(workspace_id: str, svc: SvcDep) -> Workspace:
    return await svc.get(workspace_id)


@router.post("/{workspace_id}/advance", summary="Advance one lifecycle stage (happy path)")
async def advance_workspace(workspace_id: str, svc: SvcDep) -> Workspace:
    return await svc.advance(workspace_id)


@router.post("/{workspace_id}/transition", summary="Transition to a specific state (guarded)")
async def transition_workspace(workspace_id: str, body: TransitionRequest, svc: SvcDep) -> Workspace:
    return await svc.transition(workspace_id, body.to_state.value)


@router.post("/{workspace_id}/analyze", summary="Run the ANALYSIS agent (grounded impact analysis)")
async def analyze_workspace(
    workspace_id: str, svc: SvcDep, specialist: SpecialistDep, graph: GraphDep, reg: RegDep, persona: PersonaDep
) -> ClassificationResult:
    """Runs the persona agent for the ANALYSIS stage: advances INTAKE→ANALYSIS, grounds a persisted
    analysis artifact (+ GROUNDS links) via kb.query, and returns the change classification. The
    affected set expands the matched cards with their downstream graph neighbours."""
    reg.require_capability(persona, "workspace.analysis")  # capability guard (BA owns analysis)
    ws = await svc.get(workspace_id)
    if ws.state is WorkspaceState.INTAKE:  # progress the lifecycle (idempotent: only from INTAKE)
        await svc.advance(workspace_id, actor=f"persona:{persona}")
    result = await specialist.run_stage(workspace_id, persona=persona, stage="ANALYSIS", artifact_kind="analysis")
    matched: list[str] = result["matched_ids"]
    if result["abstained"] or not matched:
        return ClassificationResult(
            change_class="New",
            confidence=result["confidence"],
            rationale="no matching KB concept found — net-new",
        )
    affected = await _impact_ids(graph, matched)
    return ClassificationResult(
        change_class="Enhancement",
        matched_ids=matched,
        affected_ids=affected,
        confidence=result["confidence"],
        rationale=f"matches {len(matched)} KB card(s); impact touches {len(affected)}",
    )


@router.post("/{workspace_id}/artifacts", status_code=status.HTTP_201_CREATED, summary="Attach an artifact")
async def attach_artifact(workspace_id: str, body: AttachArtifactRequest, svc: SvcDep) -> WorkspaceArtifact:
    row = await svc.attach_artifact(
        workspace_id,
        kind=body.kind,
        content=body.content,
        s3_uri=body.s3_uri,
        template_id=body.template_id,
        template_version=body.template_version,
        grounding_score=body.grounding_score,
    )
    return WorkspaceArtifact.model_validate(row)


@router.get("/{workspace_id}/trace", summary="Traceability graph for the workspace")
async def workspace_trace(workspace_id: str, svc: SvcDep, trace: TraceDep):
    """Return traceability result (timeline + artifact chain + KB grounding + systems affected)."""
    await svc.get(workspace_id)  # 404 if the workspace does not exist
    result = await trace.get_tracing_result(workspace_id)
    if not result:
        return {
            "workspace_id": workspace_id,
            "timeline": [],
            "artifact_chain": {},
            "kb_grounding": [],
            "systems_affected": [],
            "scope_analysis": {
                "stages_completed": 0,
                "kb_cards_matched": 0,
                "systems_affected": 0,
                "database_changes": 0
            }
        }
    return result


@router.post("/{workspace_id}/close", summary="Close the workspace (enqueues kb-refresh from PENDING_SYNC)")
async def close_workspace(workspace_id: str, svc: SvcDep) -> Workspace:
    return await svc.close(workspace_id)

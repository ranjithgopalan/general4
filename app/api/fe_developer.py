"""``/ws/{id}/dev`` — dedicated Developer stage routes.

Unlike other stages (FSD/BRD/Stories/SRD), the Developer stage does NOT route through
fe.generate. Developer persona has ``capabilities: ["kb.query", "kb.read"]`` — no
``fe.generate.*`` capability — so it gets its own dedicated routes here.

6 routes:
  GET    /ws/{id}/dev                     — read persisted DevDocument
  POST   /ws/{id}/dev/generate/stream     — SSE: generate DevDocument (LangGraph + Bedrock)
  POST   /ws/{id}/dev/accept              — accept DevDocument → advance to QA_TESTING
  GET    /ws/{id}/dev/export.docx         — export as official AIG .docx
  GET    /ws/{id}/dev/export.md           — export as 9-section enterprise Markdown handover document
  POST   /ws/{id}/dev/codegen/stream      — SSE: ReAct codegen (reads source, writes changed files)

All paths ending in ``/stream`` are excluded from GZip by ``_SSESafeGZipMiddleware``.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_persona, get_developer_service
from app.lifecycle.stages.developer.handler import DeveloperService
from app.lifecycle.stages.developer.schema import DevDocument, PrSyncRequest, PrSyncResult
from app.models.workspace import Workspace
from app.utils.exceptions import ResourceNotFoundError
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["developer"])

SvcDep = Annotated[DeveloperService, Depends(get_developer_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _sse(event: str, data: dict) -> str:
    """SSE frame — mirrors fe_generate.py._sse() convention."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── Read ───────────────────────────────────────────────────────────────────────

@router.get("/{workspace_id}/dev", summary="Get the latest Dev artifact (DevDocument)")
async def get_dev(workspace_id: str, svc: SvcDep) -> DevDocument:
    doc = await svc.get(workspace_id)
    if doc is None:
        raise ResourceNotFoundError(
            f"no Dev document for {workspace_id}", {"workspace_id": workspace_id}
        )
    return doc


# ── Generate (SSE) — LangGraph + Bedrock ─────────────────────────────────────

@router.post(
    "/{workspace_id}/dev/generate/stream",
    summary="Generate DevDocument (SSE) — LangGraph + Bedrock → code stubs + wiring + notes",
)
async def generate_dev_stream(
    workspace_id: str,
    svc: SvcDep,
    persona: PersonaDep,
) -> StreamingResponse:
    """Stream DevDocument generation.

    Events: ``status`` (node progress) → ``result`` (DevDocument) → ``done``.
    Prerequisite: SRD stage must be accepted (SRDDocument must exist).
    Path ends in ``/stream`` so GZip is excluded automatically by _SSESafeGZipMiddleware.
    """

    async def stream():
        try:
            async for event, payload in svc.generate_stream(workspace_id, persona=persona):
                if event == "result":
                    # DevDocument is a Pydantic model — serialize to dict for SSE
                    if hasattr(payload, "model_dump"):
                        payload = payload.model_dump()
                yield _sse(event, payload)
            yield _sse("done", {})
        except Exception as exc:  # noqa: BLE001 — surface as error event, don't 500 mid-stream
            log.error(
                f"[fe-developer] stream error | ws={workspace_id} persona={persona} "
                f"| {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Accept → QA_TESTING ────────────────────────────────────────────────────────

@router.post(
    "/{workspace_id}/dev/accept",
    summary="Accept the Dev document → advance to QA_TESTING",
)
async def accept_dev(workspace_id: str, svc: SvcDep, persona: PersonaDep) -> Workspace:
    return await svc.accept(workspace_id, persona=persona)


# ── PR sync (local Developer plugin → central) ───────────────────────────────────

@router.post(
    "/{workspace_id}/dev/pr",
    summary="Sync a PR raised by the local Developer plugin → record provenance + advance DEVELOPMENT→QA_TESTING",
)
async def sync_dev_pr(
    workspace_id: str, body: PrSyncRequest, svc: SvcDep, persona: PersonaDep
) -> PrSyncResult:
    """docs/27 §6.1 (P2). Records the PR as a ``dev-pr`` artifact and, when ``advance`` is set and the
    workspace is in DEVELOPMENT, fires the guarded transition to QA_TESTING. The DB stays the source of
    truth (docs/27 §4) — the plugin is just another actor writing it by ``workspace_id``.
    """
    return await svc.record_pr(workspace_id, persona=persona, pr=body)


# ── Export .docx ───────────────────────────────────────────────────────────────

@router.get(
    "/{workspace_id}/dev/export.docx",
    summary="Export the Dev document as an official AIG .docx",
)
async def export_dev_docx(workspace_id: str, svc: SvcDep) -> Response:
    data = await svc.export_docx(workspace_id)
    return Response(
        content=data,
        media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="dev-{workspace_id}.docx"'},
    )


# ── Export Markdown ────────────────────────────────────────────────────────────

@router.get(
    "/{workspace_id}/dev/export.md",
    summary="Export the Dev document as a 9-section enterprise Markdown handover document",
)
async def export_dev_markdown(workspace_id: str, svc: SvcDep) -> Response:
    """Download the DevDocument as a Markdown developer handover document.

    The document is self-contained and can be handed directly to any developer or
    Claude Code: "here is the original code and here is the dev document — go do the changes."

    9 sections:
      1. Change Summary
      2. Story-by-Story Implementation Workbook (user story + Gherkin AC + as-is→to-be FR delta
         + affected components with source loci + code stubs + Definition of Done)
      3. Architecture Context
      4. Full Code Stubs
      5. Integration Wiring Checklist
      6. REGO Policy Stubs
      7. Test Expectations
      8. Gap Log
      9. Handover Checklist (developer + QA + governance sign-off)
    """
    from app.lifecycle.render.markdown import render_dev_markdown

    doc = await svc.get(workspace_id)
    if doc is None:
        raise ResourceNotFoundError(
            f"no Dev document for {workspace_id} — generate first",
            {"workspace_id": workspace_id},
        )
    content = render_dev_markdown(doc)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="dev-handover-{workspace_id}.md"'
        },
    )


# ── Codegen (SSE) — LangGraph ReAct agent (reads source, writes output files) ──

@router.post(
    "/{workspace_id}/dev/codegen/stream",
    summary="Run LangGraph ReAct codegen (SSE) — reads original source, writes changed files to outputs/codegen/{id}/",
)
async def codegen_stream(
    workspace_id: str,
    svc: SvcDep,
    persona: PersonaDep,
) -> StreamingResponse:
    """Stream LangGraph ReAct code generation.

    The ReAct agent (ChatBedrockConverse + bind_tools) reads original source files via
    read_source_file, implements the changes guided by the DevDocument (stories + FR deltas
    + Gherkin AC + code stubs), and writes output files via write_output_file.

    Prereq: DevDocument must exist (call /dev/generate/stream first).

    Events: ``status`` (progress) → ``result`` ({written_files, output_dir}) → ``done``.
    On error: ``error`` event (non-fatal — the route never returns a 5xx mid-stream).

    Codebase root and output directory are read from config.json:
      CODEBASE_ROOT       — Japan Auto source code root (default: input/Auto)
      CODEGEN_OUTPUT_ROOT — output directory root (default: outputs/codegen)
    """
    async def stream():
        try:
            async for event, payload in svc.codegen_stream(workspace_id):
                yield _sse(event, payload)
            yield _sse("done", {})
        except Exception as exc:  # noqa: BLE001
            log.error(
                f"[fe-developer] codegen stream error | ws={workspace_id} "
                f"| {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(stream(), media_type="text/event-stream")

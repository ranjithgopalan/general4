"""``POST /ws/{id}/generate`` — fe.generate, the single FE artifact-generation path (docs/11 §2.3).

Dispatches by ``artifact_type`` to the owning stage handler (``analysis`` today; stories/fsd/brd/…
register in ``FeGenerateService``). Returns the uniform fe.generate contract (artifact + AC-3 trace +
grounding_score). ``persona`` in the body overrides the JWT-resolved persona (service-to-service / MCP).
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_current_persona, get_fe_generate_service
from app.lifecycle.generate import FeGenerateService
from app.models.kb import FeGenerateResult, GenerateRequest
from app.utils.exceptions import BaseAppException
from app.utils.logging import log
from app.utils.request_context import get_correlation_id

router = APIRouter(prefix="/ws", tags=["fe-generate"])

GenDep = Annotated[FeGenerateService, Depends(get_fe_generate_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{workspace_id}/generate", summary="fe.generate — generate a grounded SDLC artifact (docs/11 §2.3)")
async def fe_generate(workspace_id: str, body: GenerateRequest, svc: GenDep, persona: PersonaDep) -> FeGenerateResult:
    """Generate + persist a grounded artifact for a workspace stage (analysis today)."""
    return await svc.generate(workspace_id, artifact_type=body.artifact_type, persona=body.persona or persona)


@router.post(
    "/{workspace_id}/generate/stream",
    summary="fe.generate (SSE) — node-progress events, then the generated artifact",
)
async def fe_generate_stream(
    workspace_id: str, body: GenerateRequest, svc: GenDep, persona: PersonaDep
) -> StreamingResponse:
    """Stream document generation: ``status`` node-progress → ``artifact`` (fe.generate result) → ``done``.

    Validates capability/type up front (real 403/422 before the stream opens), then streams node-progress
    like the treasury pattern — never raw LLM tokens. Path ends in ``/stream`` so GZip is skipped.
    """
    persona_id = body.persona or persona
    svc.check(artifact_type=body.artifact_type, persona=persona_id)  # 403/422 before streaming

    async def stream():
        try:
            async for event, payload in svc.generate_stream(
                workspace_id,
                artifact_type=body.artifact_type,
                persona=persona_id,
                requirement_text=body.requirement_text,
            ):
                yield _sse(event, payload)
            yield _sse("done", {})
        except Exception as exc:  # noqa: BLE001 — surface as a stream error event, don't 500 mid-stream
            log.error(
                f"[fe-generate] stream error | ws={workspace_id} type={body.artifact_type} "
                f"persona={persona_id} | {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            error_code = getattr(exc, "error_code", "INTERNAL_SERVER_ERROR")
            if isinstance(error_code, object) and hasattr(error_code, "value"):
                error_code = error_code.value
            yield _sse("error", {
                "error_code": str(error_code),
                "message": str(exc),
                "detail": str(exc),
                "request_id": get_correlation_id() or "",
            })

    return StreamingResponse(stream(), media_type="text/event-stream")

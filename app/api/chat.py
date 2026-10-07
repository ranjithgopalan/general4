"""``/fe`` — the chatbot surface (docs/20 §6, §15): grounded Q&A + conversational SSE.

Both routes resolve the caller's persona via the 3-point enforcement (``get_current_persona``;
dev-bypass → ``DEV_DEFAULT_PERSONA``). ``/fe/query`` is buffered (also what the tools-repo MCP facade
proxies to); ``/fe/chat`` streams node-progress events then the **grounding-gated** answer — never raw
LLM tokens (docs/20 §15), because the gate can drop claims after synthesis.
``/fe/query/stream`` is the UI-facing SSE endpoint: streams answer tokens then emits the full
grounded ``KbAnswer`` as the ``done`` event (matches the AIDLC chatbot SSE contract).
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.dependencies import get_chat_service, get_current_persona, get_current_user
from app.models.chat import ChatRequest
from app.models.kb import KbAnswer
from app.services.chat_service import ChatService
from app.utils.logging import log

router = APIRouter(prefix="/fe", tags=["chat"])

ChatDep = Annotated[ChatService, Depends(get_chat_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]
UserDep = Annotated[str, Depends(get_current_user)]


@router.get("/me", summary="Show the identity the backend resolves for the current request")
async def fe_me(persona: PersonaDep, user: UserDep) -> dict:
    """Returns the resolved persona and user_id — use this to verify identity before chatting."""
    return {"user_id": user, "persona": persona}


@router.post("/query", summary="Grounded Q&A (cite-or-abstain)")
async def fe_query(body: ChatRequest, persona: PersonaDep, user: UserDep, chat: ChatDep) -> KbAnswer:
    return await chat.answer(
        question=body.question,
        persona=body.persona or persona,
        session_id=body.session_id,
        user_id=user,
        category=body.category,
        top_k=body.top_k,
        hybrid=body.hybrid,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/query/stream", summary="Grounded Q&A with SSE token streaming (AIDLC chatbot contract)")
async def fe_query_stream(
    body: ChatRequest, persona: PersonaDep, user: UserDep, chat: ChatDep
) -> StreamingResponse:
    """Stream the grounded answer as SSE with REAL word-by-word tokens + live agent stages.

    Event protocol (matches aidlc-platform.service.ts chatQueryStream):
      event: status data: {"name": "...", "detail": "..."}  — live pipeline stage (what's happening)
      event: token  data: {"token": "<delta>"}              — real synthesis delta (word-by-word)
      event: abstain data: {"message": "..."}               — grounded abstain (no verified answer)
      event: done   data: KbAnswer                          — full grounded answer + citations
      event: error  data: {"message": "..."}                — on failure

    Tokens are the drafted answer streamed live; the grounding gate runs on the accumulated text
    before ``done``, so a post-stream abstain cleanly supersedes the draft (moat preserved).
    """
    persona_id = body.persona or persona

    async def _stream():
        try:
            final = None
            async for kind, payload in chat.answer_stream(
                question=body.question,
                persona=persona_id,
                session_id=body.session_id,
                user_id=user,
                category=body.category,
                top_k=body.top_k,
                hybrid=body.hybrid,
            ):
                if kind == "stage":
                    yield _sse("status", payload)
                elif kind == "token":
                    yield _sse("token", {"token": payload})
                elif kind == "answer":
                    final = payload
            if final is None:
                yield _sse("error", {"message": "No answer produced."})
                return
            if final.abstained:
                yield _sse("abstain", {"message": final.answer or "Not found in the knowledge base."})
            yield _sse("done", final.model_dump())
        except Exception as exc:
            log.error(f"[fe_query_stream] error: {exc}", exc_info=True)
            yield _sse("error", {"message": "The knowledge base service encountered an error. Please try again."})

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", summary="Conversational grounded chat (SSE: node-progress + gated answer)")
async def fe_chat(body: ChatRequest, persona: PersonaDep, user: UserDep, chat: ChatDep) -> StreamingResponse:
    persona_id = body.persona or persona

    async def stream():
        yield _sse("status", {"stage": "retrieving"})
        answer = await chat.answer(
            question=body.question,
            persona=persona_id,
            session_id=body.session_id,
            user_id=user,
            category=body.category,
            top_k=body.top_k,
            hybrid=body.hybrid,
        )
        yield _sse("status", {"stage": "grounded"})
        # Gap 10: emit a distinct "abstain" event so the UI can render a caveat rather than
        # interpreting a blank answer payload as an error condition.
        if answer.abstained:
            yield _sse("abstain", {
                "message": answer.answer or "Not found in the knowledge base.",
                "disputed_ids": answer.disputed_ids,
            })
        else:
            yield _sse("answer", answer.model_dump())
        yield _sse("done", {"abstained": answer.abstained})

    return StreamingResponse(stream(), media_type="text/event-stream")

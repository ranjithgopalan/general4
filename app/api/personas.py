"""``/personas`` — the standalone Personas surface (docs/04 §6, docs/19 §1).

The second entry point that shares the persona config with the conductor: persona-scoped, grounded
Q&A (the per-persona chatbot). Retrieval is `kb.query`, filtered by the persona's ``includeKinds`` —
cite-or-abstain holds. Auth/entitlement are enforced by the middleware chain; an unknown persona
surfaces as a 404 via the ``app.main`` handlers. No LLM in this phase (deterministic kb.query stub).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_persona, get_kb_query_service, get_persona_registry
from app.models.kb import KbAnswer
from app.models.persona import PersonaQueryRequest, PersonaSummary
from app.services.kb_query import KbQueryService
from app.services.personas import PersonaRegistry

router = APIRouter(prefix="/personas", tags=["personas"])

RegDep = Annotated[PersonaRegistry, Depends(get_persona_registry)]
KbDep = Annotated[KbQueryService, Depends(get_kb_query_service)]


@router.get("", summary="List the personas (non-sensitive view)")
async def list_personas(reg: RegDep) -> list[PersonaSummary]:
    return [
        PersonaSummary(id=p.id, display_name=p.display_name, include_kinds=p.include_kinds, capabilities=p.capabilities)
        for p in reg.all()
    ]


@router.get("/me", summary="The caller's resolved persona (Okta group -> persona)")
async def current_persona(persona_id: Annotated[str, Depends(get_current_persona)], reg: RegDep) -> PersonaSummary:
    p = reg.get(persona_id)
    return PersonaSummary(
        id=p.id, display_name=p.display_name, include_kinds=p.include_kinds, capabilities=p.capabilities
    )


@router.post("/{persona_id}/query", summary="Persona-scoped grounded Q&A (cite-or-abstain)")
async def persona_query(persona_id: str, body: PersonaQueryRequest, kb: KbDep) -> KbAnswer:
    # kb.query raises ResourceNotFoundError (404) for an unknown persona and enforces includeKinds.
    return await kb.query(question=body.question, persona=persona_id, top_k=body.top_k, category=body.category)

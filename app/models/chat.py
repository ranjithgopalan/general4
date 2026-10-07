"""Chatbot request/response DTOs (docs/20 §15). The answer DTO is the shared ``KbAnswer``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """A chat/query request. ``persona`` is a dev override; in prod it resolves from the JWT."""

    question: str = Field(min_length=1)
    persona: str | None = None
    session_id: str | None = None
    category: str | None = None
    top_k: int | None = None
    hybrid: bool | None = None


class GuardrailVerdict(BaseModel):
    """Deterministic input-guardrail decision (docs/20 §13)."""

    allowed: bool
    category: str = ""
    reason: str = ""


class IntakeResult(BaseModel):
    """Dual-axis intake classification (docs/20 §5/§5a/§7): form → route, domain → retrieval scope."""

    route: str  # DIRECT | ANSWER
    intent: str  # greeting | meta | out_of_scope | card_direct | answerable | needs_clarify
    domains: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    direct_message: str | None = None
    needs_clarify: bool = False  # docs/20 §119 CLARIFY path (wired when StateGraph is live)
    classifier_source: str = "regex"  # "regex" | "llm" — which tier produced this result

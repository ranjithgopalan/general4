"""Persona value model — the manifest schema (CLAUDE.md §6, docs/04 §6).

Mirrors ``app/config/personas.json`` (camelCase keys) but exposes snake_case attributes via aliases.
This is the SSOT for a persona's retrieval scope (``include_kinds``) and its ``capabilities`` /
``write_access`` — read by both the conductor specialists and the standalone persona surface.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Persona(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(alias="displayName")
    okta_group: str = Field(alias="oktaGroup")
    include_kinds: list[str] = Field(alias="includeKinds")
    max_tokens: int = Field(default=2000, alias="maxTokens")
    write_access: list[str] = Field(default_factory=list, alias="writeAccess")
    capabilities: list[str] = Field(default_factory=list)


class PersonaSummary(BaseModel):
    """Non-sensitive persona view for the standalone Personas surface (docs/04 §6)."""

    id: str
    display_name: str
    include_kinds: list[str]
    capabilities: list[str]


class PersonaQueryRequest(BaseModel):
    """A persona-scoped grounded Q&A request (standalone persona chat)."""

    question: str
    top_k: int = 8
    category: str | None = None

"""Persona registry + 3-point-enforcement guards (CLAUDE.md §6, docs/04 §6, §9).

Loads the persona manifest (config SSOT: ``app/config/personas.json``) and answers the two runtime
guards used around every KB read and FE write:

  * ``filter_kinds`` — the **includeKinds retrieval filter** (kb.query returns only allowed kinds);
  * ``require_capability`` — the **capability guard** on ``fe.generate`` / workspace writes.

The third enforcement point — the **Okta group** check — is the middleware layer (it maps a JWT group
to a persona); this registry supplies the ``okta_group`` config it needs. Read-only; no LLM.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config.settings import get_settings
from app.models.persona import Persona
from app.utils.exceptions import AuthorizationError, ResourceNotFoundError
from app.utils.logging import log

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "personas.json"


def _load(path: Path) -> dict[str, Persona]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {p.id: p for p in (Persona.model_validate(entry) for entry in data.get("personas", []))}


class PersonaRegistry:
    """In-memory persona config + enforcement guards (read-only)."""

    def __init__(self, personas: dict[str, Persona]) -> None:
        self._personas = personas

    def all(self) -> list[Persona]:
        return list(self._personas.values())

    def get(self, persona_id: str) -> Persona:
        persona = self._personas.get(persona_id)
        if persona is None:
            raise ResourceNotFoundError(f"unknown persona: {persona_id}", {"persona": persona_id})
        return persona

    def include_kinds(self, persona_id: str) -> list[str]:
        return list(self.get(persona_id).include_kinds)

    def filter_kinds(self, persona_id: str, requested: list[str] | None = None) -> list[str]:
        """Scope requested kinds to the persona's ``includeKinds`` (empty request = all allowed)."""
        allowed = self.get(persona_id).include_kinds
        if not requested:
            return list(allowed)
        allowed_set = set(allowed)
        return [k for k in requested if k in allowed_set]

    def has_capability(self, persona_id: str, capability: str) -> bool:
        return capability in self.get(persona_id).capabilities

    def require_capability(self, persona_id: str, capability: str) -> None:
        """Raise ``AuthorizationError`` (403) if the persona lacks ``capability``."""
        if not self.has_capability(persona_id, capability):
            raise AuthorizationError(
                f"persona '{persona_id}' lacks capability '{capability}'",
                {"persona": persona_id, "capability": capability},
            )

    def resolve_persona(self, groups: list[str]) -> Persona | None:
        """Enforcement point #1: the first persona whose ``okta_group`` is in the user's groups
        (case-insensitive). Returns None when no group maps to a persona."""
        wanted = {g.lower() for g in groups}
        return next((p for p in self._personas.values() if p.okta_group.lower() in wanted), None)


@lru_cache(maxsize=1)
def get_persona_registry() -> PersonaRegistry:
    """DI provider: the process-wide persona registry (config path override via ``PERSONAS_PATH``)."""
    settings = get_settings()
    path = Path(settings.PERSONAS_PATH) if settings.PERSONAS_PATH else _DEFAULT_PATH
    try:
        return PersonaRegistry(_load(path))
    except Exception as exc:  # noqa: BLE001 — surface a clear boot-time failure
        log.error(f"[personas] failed to load persona manifest from {path}: {exc}")
        raise

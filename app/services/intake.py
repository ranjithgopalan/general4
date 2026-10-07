"""
Dual-axis intake classifier (docs/20 §5, §5a, §7) — deterministic routing with LLM fallback.

Two tiers in sequence:

    Tier 1 — Deterministic regex (zero latency, no cost)
        High-confidence signals — pure greeting, meta, or messages containing a valid KB entity
        ID — are returned immediately without calling the LLM.

    Tier 2 — LLM structured output (Bedrock, task="classify")
        Fires only for the generic ``answerable`` fallthrough, where regex cannot distinguish
        mixed-intent messages (greeting + question), vague inputs, or out-of-scope requests.
        Returns a validated IntakeResult; any parse/validation failure falls back to Tier 1.

The LLM is invoked via the shared ModelRouter so telemetry and the fake-LLM test double work
automatically. The model is resolved via the ``classify`` task key in MODEL_ROUTING_JSON.

Deterministic-before-LLM is the invariant (docs/20 §2): entity ID extraction, domain keyword
tagging, and the clean-signal branches (greeting/meta/card_direct) never touch the LLM.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.config.settings import Settings, get_settings
from app.models.chat import IntakeResult
from app.utils.logging import log

# ── Regex patterns (Tier 1) ────────────────────────────────────────────────────────────────────

_ID_RE = re.compile(
    r"\b(?:ENT|REL|PROC|WF|SEQ|STM|SCR|BR|FR|SYS|INT|ROLE|TERM|DOM|ALIAS|EXT|API|CMP|SRV)-[A-Za-z0-9][A-Za-z0-9-]*"
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|bye|goodbye)\b", re.I)
_META = re.compile(
    r"(what can you do|what do you know|what.?s in (this |the )?(kb|knowledge base)|who are you|^\s*help\b|your capabilities)",
    re.I,
)
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

# ── Validation sets (Tier 2) ───────────────────────────────────────────────────────────────────

_VALID_ROUTES = frozenset({"DIRECT", "ANSWER"})
_VALID_INTENTS = frozenset({"greeting", "meta", "out_of_scope", "answerable", "needs_clarify"})
_VALID_DOMAINS = frozenset(
    {"code", "architecture", "systems", "process", "workflow", "rules", "screens", "data", "support"}
)

# ── Domain keyword hints (shared by both tiers) ────────────────────────────────────────────────

_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "code": ("code", "class", "method", "function", "java", "typescript", "angular", ".java", ".ts", "component"),
    "architecture": ("architecture", "architect", "design", "diagram", "topology"),
    "systems": ("system", "integration", "esb", "wod", "pega", "freia", "interface", "endpoint"),
    "process": ("process", "new business", "endorsement", "renewal", "cancellation"),
    "workflow": ("workflow", "flow", "sequence", "state", "lifecycle"),
    "rules": ("rule", "eligibility", "premium", "discount", "validation", "coverage"),
    "screens": ("screen", "ui", "form", "page", "field"),
    "data": ("entity", "glossary", "term", "schema", "attribute"),
    "support": ("defect", "bug", "incident", "ticket", "support", "error"),
}

# ── Canned messages ─────────────────────────────────────────────────────────────────────────────

_META_MSG = (
    "I answer grounded questions about the {gear} knowledge base — business rules, processes, "
    "workflows, systems and integrations, screens, code components and glossary — always with "
    "citations, and I say when something isn't covered. Ask me anything about it."
)
_OUT_OF_SCOPE_MSG = (
    "That question is outside the scope of the {gear} knowledge base. "
    "Ask me about business rules, processes, workflows, systems, screens, or code components."
)


class IntakeClassifier:
    """Deterministic form × domain classifier with LLM fallback for ambiguous inputs."""

    def __init__(self, settings: Settings | None = None, router: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._router = router  # injected in tests; lazily resolved from get_model_router() at runtime

    # ── Public API ──────────────────────────────────────────────────────────────────────────────

    def classify(self, question: str) -> IntakeResult:
        """Tier 1: synchronous regex classifier. Returns immediately with no LLM call."""
        q = question or ""
        ids = list(dict.fromkeys(_ID_RE.findall(q)))
        domains = self._domains(q)
        if _GREETING.search(q) and len(q.split()) <= 4 and not ids:
            return IntakeResult(
                route="DIRECT",
                intent="greeting",
                domains=domains,
                entity_ids=ids,
                direct_message=(
                    f"Hello! Ask me anything about the {self._settings.GEAR_ID} knowledge base "
                    "and I'll answer with citations."
                ),
            )
        if _META.search(q):
            return IntakeResult(
                route="DIRECT",
                intent="meta",
                domains=domains,
                entity_ids=ids,
                direct_message=_META_MSG.format(gear=self._settings.GEAR_ID),
            )
        return IntakeResult(
            route="ANSWER",
            intent="card_direct" if ids else "answerable",
            domains=domains,
            entity_ids=ids,
        )

    async def classify_async(self, question: str) -> IntakeResult:
        """Tier 1 → Tier 2 cascade.

        High-confidence regex signals (greeting, meta, card_direct) skip the LLM entirely.
        The generic ``answerable`` case escalates to the LLM, which can detect mixed-intent
        messages, out-of-scope questions, and inputs that need clarification.
        Any LLM failure falls back to the regex result transparently.
        """
        regex_result = self.classify(question)

        # High-confidence signals — regex is authoritative, no LLM needed
        if regex_result.intent in ("greeting", "meta", "card_direct"):
            return regex_result

        # LLM tier is disabled when INTAKE_CLASSIFY_PROMPT is not configured
        if not self._settings.INTAKE_CLASSIFY_PROMPT:
            return regex_result

        try:
            return await self._llm_classify(question, regex_result)
        except Exception as exc:
            log.warning(f"[intake] LLM classifier unavailable ({exc!r}); using regex result")
            return regex_result

    # ── Tier 2: LLM classification ──────────────────────────────────────────────────────────────

    def _get_router(self) -> Any:
        if self._router is None:
            from app.services.model_router import get_model_router

            self._router = get_model_router()
        return self._router

    async def _llm_classify(self, question: str, regex_fallback: IntakeResult) -> IntakeResult:
        llm = self._get_router().get_llm("classify")
        system = self._settings.INTAKE_CLASSIFY_PROMPT.replace("{gear}", self._settings.GEAR_ID)
        response = await llm.ainvoke([("system", system), ("human", question)])
        content = (getattr(response, "content", "") or "").strip()

        parsed = _parse_json(content)
        if parsed is None:
            log.warning(f"[intake] LLM returned unparseable content; using regex result")
            return regex_fallback

        route = parsed.get("route", "")
        intent = parsed.get("intent", "")
        needs_clarify = bool(parsed.get("needs_clarify", False))
        llm_domains = [d for d in (parsed.get("domains") or []) if d in _VALID_DOMAINS]

        if route not in _VALID_ROUTES or intent not in _VALID_INTENTS:
            log.warning(f"[intake] LLM returned invalid route/intent ({route!r}, {intent!r}); using regex result")
            return regex_fallback

        # Merge domains: union of regex (keyword-matched) and LLM (semantically inferred)
        merged_domains = list(dict.fromkeys(regex_fallback.domains + llm_domains))

        return IntakeResult(
            route=route,
            intent=intent,
            domains=merged_domains,
            entity_ids=regex_fallback.entity_ids,  # regex is authoritative for entity IDs
            direct_message=_direct_message(route, intent, self._settings.GEAR_ID),
            needs_clarify=needs_clarify,
            classifier_source="llm",
        )

    # ── Helpers ─────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _domains(question: str) -> list[str]:
        ql = question.lower()
        return [domain for domain, kws in _DOMAIN_HINTS.items() if any(k in ql for k in kws)]


# ── Module-level helpers ────────────────────────────────────────────────────────────────────────

def _parse_json(content: str) -> dict | None:
    """Try direct parse, then extract the first JSON block if the model added surrounding text."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_BLOCK.search(content)
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _direct_message(route: str, intent: str, gear: str) -> str | None:
    """Canned direct response for DIRECT-routed LLM results."""
    if route != "DIRECT":
        return None
    if intent == "greeting":
        return f"Hello! Ask me anything about the {gear} knowledge base and I'll answer with citations."
    if intent == "meta":
        return _META_MSG.format(gear=gear)
    # out_of_scope
    return _OUT_OF_SCOPE_MSG.format(gear=gear)


@lru_cache
def get_intake_classifier() -> IntakeClassifier:
    """Process-wide singleton IntakeClassifier (cached)."""
    return IntakeClassifier()

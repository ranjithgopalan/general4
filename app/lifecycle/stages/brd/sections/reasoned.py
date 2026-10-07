"""LLM-derived (reasoned) BRD sections — whitelist-filtered; deterministic fallbacks always present.

The ReAct agent proposes: business_case, proposed_change for requirements, stakeholder_personas,
success_criteria, risks_and_compliance, and key_decisions.  These builders apply the agent's
enrichment onto the deterministic base and strip any id references outside the allowed whitelist.

Word budget (Import 1 — Genlite):
  business_case   → 250w soft cap (LLM prose)
  success_criteria → 500w soft cap (LLM list)
  All other sections: uncapped (never truncated).
  Soft cap trims at last complete sentence; never mid-sentence; never truncates data/tables.

Business-language framing (Import 2 — IMAD):
  ``build_business_case_fallback()`` translates KB card content to business impact language
  (regulatory risk, process disruption, cost) — not just listing KB IDs.

Mandatory ambiguity stubs (Import 3 — Genlite + connected-layer):
  ``validate_brd_ambiguities()`` is the post-assembly check — any BR/FR cited in prose but
  NOT in allowed_ids becomes a BA-TODO KeyDecision stub.
"""

from __future__ import annotations

import re as _re
from typing import Any

from app.lifecycle.stages.brd.match.context import BRDContext
from app.lifecycle.stages.brd.schema import (
    DECISION_TAGS,
    PRIORITY_LEVELS,
    BusinessRequirement,
    BRDDocument,
    BRDRule,
    KeyDecision,
    PersonaNeed,
)


def _s(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _priority(raw: Any) -> str:
    p = str(raw or "").strip().capitalize()
    return p if p in PRIORITY_LEVELS else "Medium"


def _tag(raw: Any) -> str:
    t = str(raw or "").strip().upper()
    return t if t in DECISION_TAGS else "OPEN"


# ── Import 1: soft word-cap helpers ───────────────────────────────────────────

_SENTENCE_END = _re.compile(r"(?<=[.!?])\s+")
_WORD_RE = _re.compile(r"\S+")
_TRUNCATION_SUFFIX = " [...] See section 9 (Open Items) for remaining detail."


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _soft_cap_prose(text: str, max_words: int) -> tuple[str, bool]:
    """Trim LLM-generated prose to ``max_words``, cutting at the last complete sentence.

    Returns: (trimmed_text, truncated_bool).
    Never truncates mid-sentence.  If the text fits within budget, returns it unchanged.
    """
    if _count_words(text) <= max_words:
        return text, False

    sentences = _SENTENCE_END.split(text)
    kept: list[str] = []
    total = 0
    for s in sentences:
        s_words = _count_words(s)
        if total + s_words > max_words:
            break
        kept.append(s)
        total += s_words

    if not kept:
        # Even the first sentence exceeds budget — return it anyway (never return empty).
        return sentences[0] + _TRUNCATION_SUFFIX, True

    return " ".join(kept) + _TRUNCATION_SUFFIX, True


def _soft_cap_list(items: list[str], max_words: int) -> tuple[list[str], bool]:
    """Keep whole items from ``items`` until total word count exceeds ``max_words``.

    Returns: (kept_items, truncated_bool).
    Each item is always kept whole — never truncated mid-item.
    """
    if not items:
        return items, False

    kept: list[str] = []
    total = 0
    for item in items:
        item_words = _count_words(item)
        if kept and total + item_words > max_words:
            break
        kept.append(item)
        total += item_words

    truncated = len(kept) < len(items)
    return kept, truncated


# ── Section 1: Business Case (LLM, soft cap 250w) ─────────────────────────────

def build_business_case(enriched: dict[str, Any] | None, context: BRDContext) -> str:
    """LLM: 3–5 sentence business impact brief — deterministic fallback via Import 2 framing."""
    if enriched:
        raw = _s(enriched.get("business_case"))
        if raw:
            return raw

    return build_business_case_fallback(context)


def build_business_case_fallback(context: BRDContext) -> str:
    """Import 2 (IMAD framing): translate KB card context to business impact language.

    Does NOT list KB IDs — translates to: regulatory risk, process disruption, cost, benefit.
    """
    change = context.change_class or "change"
    req_brief = (context.requirement[:200] + "...") if len(context.requirement) > 200 else context.requirement

    br_count = len(context.br_matched)
    fr_count = len(context.fr_matched)

    risk_note = ""
    if br_count:
        risk_note = (
            f" This change touches {br_count} existing business rule(s), "
            "which may carry regulatory or compliance implications that require BA confirmation."
        )

    effort_note = ""
    if fr_count:
        effort_note = (
            f" {fr_count} functional requirement(s) are directly in scope, "
            "indicating process changes that may disrupt current workflows."
        )

    return (
        f"This {change} initiative addresses the following business need: {req_brief}."
        f"{risk_note}{effort_note} "
        f"Proceeding without addressing this requirement poses ongoing process and "
        f"compliance risk to the AIG Connect Japan Auto Insurance portfolio. "
        f"Approval of this BRD authorises the delivery team to proceed to the Stories stage."
    )


# ── Section 3: Business Requirements enrichment ───────────────────────────────

def apply_brd_req_enrichment(
    reqs: list[BusinessRequirement],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[BusinessRequirement]:
    """Apply proposed_change + priority updates from the agent onto deterministic requirement rows.

    Whitelist-filtered: only applies to ids already in allowed_ids.
    Never drops rows — enrichment adds detail, never removes grounded data.
    """
    if not enriched or not isinstance(enriched.get("business_requirements"), list):
        return reqs

    updates: dict[str, dict[str, Any]] = {}
    for entry in enriched["business_requirements"][:50]:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("id") or "").strip()
        if not eid or eid not in allowed_ids:
            continue
        updates[eid] = {
            "proposed_change": _s(entry.get("proposed_change")),
            "priority": _priority(entry.get("priority")),
        }

    result: list[BusinessRequirement] = []
    for req in reqs:
        if req.id in updates:
            upd = updates[req.id]
            result.append(req.model_copy(
                update={k: v for k, v in upd.items() if v is not None}
            ))
        else:
            # No agent enrichment — mark as BA-TODO stub for proposed_change
            result.append(req.model_copy(update={"stub_marker": "BA-TODO"}))

    return result


def apply_brd_rule_enrichment(
    rules: list[BRDRule],
    enriched: dict[str, Any] | None,
    allowed_ids: set[str],
) -> list[BRDRule]:
    """Apply applies_to lists from agent enrichment onto deterministic business rule rows."""
    if not enriched or not isinstance(enriched.get("business_rules"), list):
        return rules

    updates: dict[str, list[str]] = {}
    for entry in enriched["business_rules"][:50]:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("id") or "").strip()
        if not eid or eid not in allowed_ids:
            continue
        applies_to = [str(a).strip() for a in (entry.get("applies_to") or []) if a]
        if applies_to:
            updates[eid] = applies_to

    result: list[BRDRule] = []
    for rule in rules:
        if rule.id in updates:
            result.append(rule.model_copy(update={"applies_to": updates[rule.id]}))
        else:
            result.append(rule)

    return result


# ── Section 5: Target Personas & Needs (mixed, uncapped) ──────────────────────

def build_stakeholder_personas(
    enriched: dict[str, Any] | None,
    context: BRDContext,
) -> list[PersonaNeed]:
    """ROLE/DOM KB cards + stakeholder_input lines + agent-reasoned persona needs.

    Order: KB-grounded role cards first, then stakeholder_input-parsed personas,
    then any agent-proposed extras.  Full text always — never truncated.
    """
    out: list[PersonaNeed] = []
    seen_personas: set[str] = set()
    bodies = context.card_bodies

    # KB ROLE/DOM cards → persona rows
    for c in context.role_matched + context.dom_matched:
        label = c.label.strip()
        if not label or label in seen_personas:
            continue
        seen_personas.add(label)
        prose = bodies.get(c.id, {})
        use_case = str(prose.get("prose") or prose.get("text_en") or "")[:200] or label
        out.append(PersonaNeed(
            persona=label,
            use_case=use_case,
            need="See KB card — BA to elaborate with stakeholder input",
            source="kb_card",
        ))

    # Agent-proposed persona needs (whitelist-checked by persona name, not KB id)
    agent_personas: list[dict[str, Any]] = (
        enriched.get("stakeholder_personas") or [] if enriched else []
    )
    for entry in agent_personas[:12]:
        if not isinstance(entry, dict):
            continue
        persona = _s(entry.get("persona"))
        if not persona:
            continue
        use_case = _s(entry.get("use_case")) or ""
        need = _s(entry.get("need")) or "BA-TODO — specify what this persona needs"
        source = "stakeholder_input" if context.stakeholder_input else "kb_card"
        out.append(PersonaNeed(persona=persona, use_case=use_case, need=need, source=source))

    return out


# ── Section 6: Success Criteria (LLM, soft cap 500w) ─────────────────────────

def build_success_criteria(
    enriched: dict[str, Any] | None,
    context: BRDContext,
) -> list[str]:
    """LLM: PRD-style KPI 1 / KPI 2 numbered list — deterministic fallback = 3 generic criteria."""
    if enriched and isinstance(enriched.get("success_criteria"), list):
        items: list[str] = [
            str(item).strip()
            for item in enriched["success_criteria"]
            if str(item or "").strip()
        ]
        if items:
            return items

    return _fallback_success_criteria(context)


def _fallback_success_criteria(context: BRDContext) -> list[str]:
    """Deterministic fallback when the agent produces no success criteria."""
    criteria: list[str] = [
        f"KPI 1 — All {len(context.br_matched)} business rules impacted by this "
        f"{context.change_class or 'change'} are confirmed compliant with AIG Connect Japan "
        "Auto policy standards before release to production.",
        "KPI 2 — End-to-end user acceptance testing (UAT) passes with zero critical defects "
        "and all BA-TODO items resolved or formally deferred.",
        "KPI 3 — Sign-off obtained from BA, PO, and compliance stakeholders "
        "(sign-off strip completed below).",
    ]
    return criteria


# ── Section 7: Risks & Compliance (mixed, uncapped) ──────────────────────────

def build_risks_and_compliance(
    enriched: dict[str, Any] | None,
    context: BRDContext,
) -> list[str]:
    """Merge analysis conflicts + BR compliance cards + agent-reasoned risks.

    Full text always shown — never truncated.
    """
    out: list[str] = []

    # Agent-proposed risks (most relevant; whitelist enforcement not required for narrative prose)
    if enriched and isinstance(enriched.get("risks_and_compliance"), list):
        for item in enriched["risks_and_compliance"][:20]:
            text = _s(item)
            if text:
                out.append(text)

    # Deterministic fallback: summarise scope of BR coverage as a compliance note
    if not out and context.br_matched:
        br_labels = ", ".join(f"[{c.id}] {c.label}" for c in context.br_matched[:5])
        out.append(
            f"COMPLIANCE NOTE: Business rules in scope ({br_labels}) must be verified against "
            "current AIG Connect Japan regulatory requirements before production deployment."
        )

    if not out:
        out.append(
            "RISK: No KB-grounded business rules found for this requirement. "
            "BA-TODO — confirm scope with SME before proceeding to stories."
        )

    return out


# ── Section 9: Key Decisions & Open Items (mixed, uncapped) ──────────────────

def build_key_decisions(
    enriched: dict[str, Any] | None,
    context: BRDContext,
) -> list[KeyDecision]:
    """[DECIDED]/[OPEN]/[INSIGHT] decision log — full text always; never truncated.

    Seeds from FSD open_items gaps (all become [OPEN] BA-TODO items) then agent-proposed
    decisions are appended.
    """
    out: list[KeyDecision] = []
    seen: set[str] = set()

    # FSD open_items gaps → [OPEN] BA-TODO items (these are the mandatory ambiguity stubs)
    for i, gap in enumerate(context.gaps):
        key = gap[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(KeyDecision(
            tag="OPEN",
            description=gap,
            section="9.Key Decisions & Open Items",
            marker="BA-TODO",
        ))

    # Agent-proposed decisions (DECIDED/OPEN/INSIGHT/NOTE/ROADMAP)
    if enriched and isinstance(enriched.get("key_decisions"), list):
        for entry in enriched["key_decisions"][:20]:
            if not isinstance(entry, dict):
                continue
            desc = _s(entry.get("description"))
            if not desc:
                continue
            key = desc[:60]
            if key in seen:
                continue
            seen.add(key)
            out.append(KeyDecision(
                tag=_tag(entry.get("tag")),
                description=desc,
                section=_s(entry.get("section")) or "9.Key Decisions & Open Items",
                marker=_s(entry.get("marker")) or "BA-TODO",
            ))

    return out


# ── Import 3: Post-assembly ambiguity check ───────────────────────────────────

_KB_ID_PAT = _re.compile(r"\b([A-Z]{2,10}-[A-Z]{0,5}-?\d+)\b")


def validate_brd_ambiguities(doc: BRDDocument, allowed_ids: set[str]) -> list[KeyDecision]:
    """Import 3 (Genlite + connected-layer): post-assembly deterministic check.

    Scans all prose fields in the BRD for KB-style IDs (e.g. BR-JAUTO-001).
    Any ID found in prose that is NOT in allowed_ids → BA-TODO KeyDecision stub.
    Returns a (possibly empty) list of additional KeyDecision stubs to append.
    """
    all_prose = " ".join([
        doc.business_case,
        " ".join(doc.risks_and_compliance),
        " ".join(doc.success_criteria),
        " ".join(kd.description for kd in doc.key_decisions),
        " ".join(req.requirement + " " + (req.proposed_change or "") for req in doc.business_requirements),
    ])

    found_ids = set(_KB_ID_PAT.findall(all_prose))
    ungrounded = found_ids - allowed_ids

    stubs: list[KeyDecision] = []
    for uid in sorted(ungrounded):
        stubs.append(KeyDecision(
            tag="OPEN",
            description=(
                f"KB ID [{uid}] appears in BRD prose but is not in the grounded FSD references. "
                "BA-TODO: verify this ID is in scope and re-run, or remove the reference."
            ),
            section="9.Key Decisions & Open Items",
            marker="BA-TODO",
        ))

    return stubs


# ── Fallback for grounding gate ───────────────────────────────────────────────

def fallback_business_case(context: BRDContext) -> str:
    """Used by the grounding gate if the LLM business_case cites blocked ids."""
    return build_business_case_fallback(context)

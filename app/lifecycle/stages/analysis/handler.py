"""ANALYSIS stage handler — thin orchestrator (docs/22).

Pipeline: **deterministic match** (retrieve → walk affected + downstream + coverage → detect conflicts →
assemble ContextPackage) → **OPEN ReAct reasoning** (grounded, tool-using) → **assemble** the typed
ImpactAnalysis from modular section builders → **grounding gate** (whitelist + re_anchor; BLOCK →
deterministic fallback) → **persist** + GROUNDS. Accept advances to FSD (Existing → CLOSE). Export
renders the SAME artifact to official AIG .docx.

Infrastructure shared with FSD and future stages lives in app/lifecycle/common/handler_base.py.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.lifecycle.common.handler_base import StageHandlerBase, _now_iso
from app.lifecycle.render.docx import render_impact_business_docx, render_impact_docx
from app.lifecycle.stages.analysis.agent.react import run_react
from app.lifecycle.traceability import TraceabilityService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.justification_builder import RelationshipJustification
from app.lifecycle.stages.analysis.match import conflicts as conflict_detect
from app.lifecycle.stages.analysis.match.context import ContextPackage
from app.lifecycle.stages.analysis.match.retrieve import retrieve
from app.lifecycle.stages.analysis.match.walk import (
    WalkResult,
    change_is_light,
    compute_affected,
    compute_downstream,
    policy_for_change,
    primary_cap,
)
from app.lifecycle.stages.analysis.match.walk_tables import compute_downstream_with_tables
from app.lifecycle.stages.analysis.technical_intent import TechnicalIntentAnalyzer
from app.lifecycle.stages.analysis.schema import AffectedNode, ImpactAnalysis, ImpactCitation
from app.lifecycle.stages.analysis.sections.assemble import assemble_impact_analysis
from app.lifecycle.stages.analysis.sections.business import BusinessImpactView, build_business_view
from app.lifecycle.stages.analysis.sections.deterministic import card_excerpt
from app.lifecycle.stages.analysis.sections.reasoned import fallback_narrative
from app.lifecycle.stages.analysis.validation import get_validation_warning_banner, validate_and_correct
from app.lifecycle.templates.registry import get_template
from app.models.workspace import Workspace, WorkspaceState
from app.utils.exceptions import ResourceNotFoundError
from app.utils.logging import log

# Kinds preferred when rebuilding the primary set from the agent's own retrieval (screens/entities
# are the usual change targets; systems/integrations are context).
_PRIMARY_KIND_ORDER = ("Screen", "Entity", "BusinessRule", "FunctionalReq", "Process", "Workflow")

# Fix 3: Include architectural/code kinds when they rank high enough
_ARCHITECTURAL_KINDS = frozenset({"Component", "CMP", "ApiOp", "API", "System", "SYS", "Integration", "INT"})


def _extract_table_names(requirement: str) -> set[str]:
    """Extract explicitly mentioned table names from requirement text.

    Patterns:
    - UPPERCASE_WITH_UNDERSCORES (e.g., WEB_RECEIPTS, CUSTOMER_DATA)
    - 'table_name' or "table_name" (quoted names)
    - table/schema references
    """
    tables = set()

    # Pattern 1: UPPERCASE_WITH_UNDERSCORES (likely table names)
    for match in re.finditer(r'\b([A-Z][A-Z0-9_]{2,})\b', requirement):
        word = match.group(1)
        # Exclude common non-table keywords (but KEEP RECEIPTS for WEB_RECEIPTS detection)
        exclude = {"THE", "FROM", "TABLE", "ADD", "UPDATE", "DELETE", "INSERT", "SELECT",
                  "CHANGE", "MODIFY", "COLUMN", "SCHEMA", "DATABASE", "FIELD", "FIELDS",
                  "AND", "FOR", "WITH", "DELIVERY", "STATUS", "METHOD", "SCREEN", "API",
                  "DATA", "TRACK", "EMAIL", "RECEIPT"}  # Removed "RECEIPTS"
        if word not in exclude:
            tables.add(word)

    # Pattern 2: "table_name" or 'table_name' references
    for match in re.finditer(r"['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", requirement):
        tables.add(match.group(1).upper())

    # Pattern 3: Explicit "table XYZ" or "schema XYZ" mentions
    for match in re.finditer(r"\b(?:table|schema)\s+([A-Za-z_][A-Za-z0-9_]*)\b", requirement, re.IGNORECASE):
        tables.add(match.group(1).upper())

    log.info(f"[table-filter] Extracted table names from requirement: {tables}")
    return tables


def _filter_matched_tables(matched: list[ImpactCitation], requirement: str) -> tuple[list[ImpactCitation], list[ImpactCitation]]:
    """Filter matched tables: keep exact matches, expand if exact name not found.

    Logic:
    1. Extract table names explicitly mentioned in requirement (e.g., "WEB_RECEIPTS")
    2. Find tables that EXACTLY match the mentioned name
    3. If exact match FOUND: keep only that table (narrow scope)
    4. If exact match NOT FOUND: expand to ALL related tables (fallback for partial matches)

    Args:
        matched: Retrieved matched citations (may include related tables)
        requirement: Original requirement text

    Returns:
        (filtered_matched, implicit_tables) where:
        - filtered_matched: Exact matches + non-table items, OR all related tables if no exact match
        - implicit_tables: Always empty if expanding (because we included everything)
    """
    mentioned_tables = _extract_table_names(requirement)

    if not mentioned_tables:
        # No tables found in requirement text, return all matched (all are implicit but needed)
        log.info("[table-filter] No explicit table names found in requirement → keeping all matched")
        return matched, []

    entity_items = [c for c in matched if c.kind in ("Entity", "ENT")]
    non_entity_items = [c for c in matched if c.kind not in ("Entity", "ENT")]

    exact_matches = []
    related_tables = []

    for entity in entity_items:
        entity_label = entity.label.upper()

        # Check for EXACT match (mentioned table name is exactly in the label)
        is_exact = False
        for table_name in mentioned_tables:
            # Exact match: table name appears as whole word in entity label
            if re.search(rf'\b{re.escape(table_name)}\b', entity_label):
                is_exact = True
                exact_matches.append(entity)
                break

        if not is_exact:
            # Check if this is a related table (shares prefix with mentioned tables)
            is_related = False
            for table_name in mentioned_tables:
                # Related: table starts with the mentioned table name or vice versa
                if entity_label.startswith(table_name) or table_name in entity_label:
                    is_related = True
                    related_tables.append(entity)
                    break

    # Decision logic:
    if exact_matches:
        # EXACT MATCH FOUND: Use only exact matches + narrow scope
        log.info(
            f"[table-filter] Exact table match found: {len(exact_matches)} table(s) → "
            f"NARROW scope (excluding {len(related_tables)} related tables)"
        )
        return non_entity_items + exact_matches, related_tables
    elif related_tables:
        # NO EXACT MATCH: Expand to include ALL related tables (fallback)
        log.info(
            f"[table-filter] Exact table match NOT found → EXPAND to {len(related_tables)} related table(s) "
            f"for better coverage"
        )
        return non_entity_items + related_tables, []
    else:
        # No tables at all
        log.info("[table-filter] No tables found matching requirement table names")
        return non_entity_items, []




def _seed_query(title: str | None, requirement: str) -> str:
    """A concise, salient seed query (mirrors how the agent queries) — the title, else the first
    sentence of the requirement. A short query retrieves far more reliably than a verbose paragraph."""
    if title and title.strip():
        return title.strip()
    text = (requirement or "").strip()
    for sep in ("。", ". ", ".\n", "\n"):
        if sep in text:
            return text.split(sep, 1)[0].strip() or text
    return text


def _clip_detail(s: str, n: int = 90) -> str:
    """One-line, length-bounded detail for a streamed sub-step."""
    t = " ".join((s or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _kind_breakdown(cards: list[ImpactCitation], cap: int = 6) -> str:
    """'N found — 5 Screen · 3 Component · …' — substance for the 'reviewed candidates' sub-step."""
    counts: dict[str, int] = {}
    for c in cards:
        counts[c.kind or "item"] = counts.get(c.kind or "item", 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
    inv = " · ".join(f"{n} {k}" for k, n in top)
    return f"{len(cards)} found — {inv}" if inv else f"{len(cards)} found"


# P3 precision (docs/29): when a requirement names a screen/entity, anchor the PRIMARY set on it so a
# single-screen change doesn't fan out to tangential cards (print forms via "reporting", etc.).
_NAMED_SCREEN_RX = re.compile(r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s+(?:[Ss]creen|[Pp]age|[Tt]ab|[Mm]odal)\b")
_CJK_RX = re.compile(r"[぀-ヿ一-鿿]{2,}")


def _promote_components(ranked: list[ImpactCitation], matched: list[ImpactCitation], cap: int) -> list[ImpactCitation]:
    """Fix 3: Promote architectural/code components to the matched set when retrieval found them.

    If the ranked list has components (CMP, API, SYS, INT), include them even if they ranked below
    the original cap. This ensures impact analysis includes the implementing systems and code,
    not just the UI screens and business rules.

    Strategy: if matched has only screens/entities/rules (no components), look for components
    in the ranked list and add up to cap items total.
    """
    from app.config.graph_layers import family_of

    matched_kinds = {family_of(c.id, c.kind) for c in matched}
    has_components = bool(matched_kinds & _ARCHITECTURAL_KINDS)

    if has_components:
        return matched  # Already has components, keep as-is

    # No components in matched → search ranked for them and include
    components = [c for c in ranked if family_of(c.id, c.kind) in _ARCHITECTURAL_KINDS]
    if not components:
        return matched  # No components available in ranked

    # Combine: keep all matched + add components up to cap
    # Prioritize: first all matched (highest ranked), then top components
    # Use ID-based deduplication (ImpactCitation is not hashable)
    seen_ids: set[str] = {c.id for c in matched}
    combined = matched.copy()
    for c in components[:3]:
        if c.id not in seen_ids:
            combined.append(c)
            seen_ids.add(c.id)
    return combined[:cap]


def _named_targets(requirement: str) -> list[str]:
    """Screen/entity names the requirement explicitly calls out — English 'X screen/page/tab' phrases +
    CJK terms (e.g. 基本情報). Lower-cased, deduped; used to anchor the primary set."""
    req = requirement or ""
    names = [m.group(1).strip() for m in _NAMED_SCREEN_RX.finditer(req)]
    names += _CJK_RX.findall(req)
    out: list[str] = []
    for n in (x.strip().lower() for x in names):
        if n and len(n) >= 3 and n not in out:
            out.append(n)
    return out


# Flow context — a requirement usually names the flow the screen belongs to. When it does, anchor on the
# card for THAT flow (New Business), not the same-named Renewal/Endorsement variant of the screen.
# Structural stop-words only (English filler + UI nouns) — NOT domain/LOB terms. Used to score how well a
# candidate card's label overlaps the requirement's own wording, so disambiguation stays domain-neutral.
_STOPWORDS = frozenset({
    "the", "a", "an", "to", "of", "for", "and", "or", "in", "on", "with", "this", "that", "is", "are",
    "be", "it", "its", "add", "new", "optional", "screen", "page", "tab", "modal", "field", "flow", "section",
})


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase tokens (drop structural stop-words + short tokens). Domain-neutral."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOPWORDS}


def _anchored_primary(
    ranked: list[ImpactCitation], requirement: str, cap: int
) -> tuple[list[ImpactCitation], list[str], bool]:
    """Pick the PRIMARY change targets. If the requirement names a screen/entity, the primary set is JUST
    the anchored card(s) — NOT padded with tangential cards. When several cards share the named label
    (e.g. the same screen exists in multiple flows), keep the one(s) whose label best overlaps the
    requirement's OWN wording — a generic, domain/LOB-neutral tie-break (no hardcoded flow tokens).
    Falls back to the proportional top-cap when nothing is named. Returns (matched, labels, is_anchored)."""
    targets = _named_targets(requirement)
    if not targets:
        return ranked[:cap], [], False
    # A card anchors if a named target's significant tokens are ALL present in its label — order- and
    # format-independent (KB labels reorder/annotate, e.g. requirement "AU Renewal Reception Registration
    # Screen" vs label "Reception Registration Screen — Renewal (受付登録情報)"). A raw substring test misses
    # these and over-falls back to the whole top-cap family. Legacy substring kept as a fallback for
    # short/CJK targets that tokenize to nothing. Domain-neutral (token overlap, no hardcoded terms).
    target_tok_sets = [ts for ts in (_tokens(t) for t in targets) if ts]

    def _anchors(c: ImpactCitation) -> bool:
        lab = _tokens(c.label)
        if any(ts <= lab for ts in target_tok_sets):
            return True
        return any(t in (c.label or "").lower() for t in targets)

    anchored = [c for c in ranked if _anchors(c)]
    if not anchored:
        return ranked[:cap], [], False
    req_tok = _tokens(requirement)

    def _fit(c: ImpactCitation) -> tuple[int, int]:
        ct = _tokens(c.label)
        return (len(ct & req_tok), -len(ct - req_tok))  # most requirement-overlap, then fewest foreign words

    best = max((_fit(c) for c in anchored), default=(0, 0))
    matched = ([c for c in anchored if _fit(c) == best] or anchored)[:cap]
    return matched, [c.label or c.id for c in matched], True


class ImpactAnalysisService(StageHandlerBase):
    """Config-driven grounded Impact Analysis (deterministic match → open ReAct → grounding gate).

    Inherits from StageHandlerBase:
      - __init__  (8 shared params: workspace, kb_query, graph, personas, trace, spine, model, store)
      - _s3_key() → ``<workspace>/impact-analysis/analysis.json``
      - get()     → S3-first → DB-fallback
      - export_docx() → get() + ResourceNotFoundError + _render_docx()
      - _ground_field() → verify → BLOCK → fallback → re-verify → (text, score)
    """

    _CAPABILITY = "workspace.analysis"
    _KIND = "analysis"
    _STAGE_FOLDER = "impact-analysis"  # S3 sub-folder per workspace (docs/09 §3.6 s3_uri)
    _TEMPLATE_NAME = "analysis"

    def __init__(self, **kwargs):
        """Initialize ImpactAnalysisService with technical intent analyzer."""
        super().__init__(**kwargs)
        self._intent_analyzer = TechnicalIntentAnalyzer(graph=self._graph)
        # Use self._trace from parent class (StageHandlerBase sets this from 'trace' kwarg)
        self._traceability = self._trace
        # ─── Bedrock Concurrency Limiter (Fix: sequential LLM calls) ───
        # Limit concurrent Bedrock requests to 2 to avoid throttling
        # This prevents the connection pool exhaustion that caused 48-56 second latencies
        self._bedrock_semaphore = asyncio.Semaphore(2)

    # ── Bedrock concurrency control ───────────────────────────────────────────────────

    async def _retrieve_with_rate_limit(self, kb, requirement: str, persona: str):
        """Wrapper to limit concurrent Bedrock requests via semaphore.

        Prevents connection pool exhaustion by enforcing max 2 concurrent LLM calls.
        This fixes the 48-56 second latency issue caused by asyncio.gather firing all
        retrieval queries at Bedrock simultaneously, exceeding its per-account limits.
        """
        async with self._bedrock_semaphore:
            result = await retrieve(kb, requirement=requirement, persona=persona)
            # Small delay after each call to allow Bedrock to drain its queue
            await asyncio.sleep(0.1)
            return result

    # ── abstract implementations ───────────────────────────────────────────────────

    def _parse(self, workspace_id: str, text: str, *, source: str) -> ImpactAnalysis | None:
        try:
            return ImpactAnalysis.model_validate_json(text)
        except Exception as exc:  # noqa: BLE001 — legacy/non-JSON content → treat as absent
            log.warning(f"[impact] {source} analysis for {workspace_id} is not parseable: {exc}")
            return None

    def _render_docx(self, artifact: ImpactAnalysis) -> bytes:
        return render_impact_docx(artifact, self._template)

    # ── public API ─────────────────────────────────────────────────────────────────

    async def analyze(self, workspace_id: str, *, persona: str) -> ImpactAnalysis:
        """Generate + persist the impact analysis (buffered) — delegates to the streaming pipeline."""
        result: ImpactAnalysis | None = None
        async for event, payload in self.analyze_stream(workspace_id, persona=persona):
            if event == "result":
                result = payload
        assert result is not None  # analyze_stream always yields a 'result' (or raises)
        return result

    async def analyze_stream(
        self,
        workspace_id: str,
        *,
        persona: str,
        requirement_override: str | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """Async generator: yields ``('status', {stage, detail})`` node-progress, then ``('result', ImpactAnalysis)``.

        Streams **node-progress** (like the treasury/chat pattern), never raw LLM tokens — the grounding
        gate can drop claims after synthesis, so only the gated, assembled artifact is emitted at the end.

        Args:
            requirement_override: If provided, use this requirement instead of workspace.requirement_text
        """
        self._personas.require_capability(persona, self._CAPABILITY)
        ws = await self._workspace.get(workspace_id)
        if ws.state is WorkspaceState.INTAKE:
            await self._workspace.advance(workspace_id, actor=f"persona:{persona}")
            ws = await self._workspace.get(workspace_id)
        # Use override if provided, else use workspace requirement
        requirement = requirement_override or ws.requirement_text or f"analysis for workspace {workspace_id}"

        # ─ VALIDATION: detect and correct common parsing/concatenation errors ──────────────────────
        validation_result = validate_and_correct(requirement)
        requirement = validation_result.text
        warning_banner = get_validation_warning_banner(validation_result)
        if warning_banner:
            log.warning(f"[analysis] {workspace_id}: {warning_banner}")
            yield ("status", {"stage": "validating", "detail": warning_banner.split("\n")[0]})

        yield ("status", {"stage": "retrieving", "detail": "searching the knowledge base"})
        context: ContextPackage | None = None
        async for kind, payload in self._match_stream(workspace_id, ws, persona, requirement):
            if kind == "context":
                context = payload
            else:
                yield (kind, payload)  # forward the Knowledge-Retriever + Impact-Mapper sub-steps
        yield (
            "status",
            {
                "stage": "impact",
                "detail": f"{len(context.affected)} impacted · {len(context.downstream)} downstream "
                f"· {len(context.conflicts)} conflict(s)",
            },
        )

        yield ("status", {"stage": "reasoning", "detail": "running the grounded ANALYSIS agent"})
        enriched: dict[str, Any] | None = None
        whitelist: set[str] = set(context.allowed_ids)
        async for kind, payload in run_react(
            model=self._model, kb=self._kb, graph=self._graph, persona=persona, context=context
        ):
            if kind == "step":
                yield ("step", payload)  # live agent tool-call → forwarded to the SSE client
            elif kind == "result":
                enriched, whitelist = payload
        # RESILIENCE: if the deterministic seed whiffed but the agent grounded itself via its tools,
        # rebuild the primary set from what the agent actually retrieved (don't discard its work).
        context = await self._augment_from_agent(context, whitelist)

        yield ("status", {"stage": "grounding", "detail": "verifying citations (cite-or-abstain)"})

        # ─── Extract PRD impact sections (Aug 18, 2026) ───
        # what_is_modified: direct targets (matched items)
        # where_changes_needed: downstream impact (affected items not in matched)
        what_is_modified = await self._extract_what_is_modified(context.matched)
        where_changes_needed = await self._extract_where_changes_needed(context.matched, context.affected)

        # DEBUG: Log what we extracted
        log.info(f"[analysis] PRD sections: what_is_modified={len(what_is_modified)} items, where_changes_needed={len(where_changes_needed)} items")

        # ─── Collect blind spots from impact analysis ───
        blind_spots_to_include = getattr(context, 'blind_spots', [])
        log.info(f"[analysis] Assembling artifact with {len(blind_spots_to_include)} blind spot(s)")

        analysis = assemble_impact_analysis(
            workspace_id=workspace_id,
            kb_version=context.kb_version,
            persona=persona,
            template=self._template,
            requirement=requirement,
            context=context,
            enriched=enriched,
            allowed_ids=whitelist,
            generated_at=_now_iso(),
            blind_spots=blind_spots_to_include,
            what_is_modified=what_is_modified,
            where_changes_needed=where_changes_needed,
        )
        analysis = self._ground(analysis, context, whitelist)

        yield ("status", {"stage": "persisting", "detail": "saving artifact + GROUNDS links"})
        try:
            await self._persist(workspace_id, persona, analysis, context)
        except Exception as exc:  # noqa: BLE001 — persist is best-effort; always yield the result
            log.warning(f"[impact] persist failed for {workspace_id} (result still returned): {exc}")
        yield ("result", analysis)

    async def accept(self, workspace_id: str, *, persona: str) -> Workspace:
        """Accept the analysis → advance to FSD (or CLOSE for an Existing-class change)."""
        self._personas.require_capability(persona, self._CAPABILITY)
        analysis = await self.get(workspace_id)
        if analysis is not None and analysis.classification.change_class == "Existing":
            return await self._workspace.transition(
                workspace_id, WorkspaceState.CLOSED.value, actor=f"persona:{persona}"
            )
        return await self._workspace.advance(workspace_id, actor=f"persona:{persona}")

    async def reject(self, workspace_id: str, *, persona: str) -> Workspace:
        """Reject the analysis — the workspace stays at ANALYSIS for revision."""
        self._personas.require_capability(persona, self._CAPABILITY)
        return await self._workspace.get(workspace_id)

    async def get_business_view(self, workspace_id: str) -> BusinessImpactView:
        """Plain-language business view of the persisted analysis (the UI JSON). 404 if none yet."""
        analysis = await self.get(workspace_id)
        if analysis is None:
            raise ResourceNotFoundError(
                f"no {self._KIND} artifact for workspace {workspace_id}", {"workspace_id": workspace_id}
            )
        return build_business_view(analysis)

    async def export_business_docx(self, workspace_id: str) -> bytes:
        """Render the persisted analysis to the plain-language BUSINESS .docx — the UI download.

        The technical .docx is written to S3 at persist time (audit / technical access); the front end
        only ever receives this business view (no KB ids, loci, or IT jargon).
        """
        view = await self.get_business_view(workspace_id)
        return render_impact_business_docx(view, get_template("analysis-business"))

    # ── pipeline steps ─────────────────────────────────────────────────────────────

    async def _add_named_target_candidates(
        self, requirement: str, ranked: list[ImpactCitation], seen: set[str]
    ) -> None:
        """Recall boost (docs/29): the semantic retriever can rank a same-named SIBLING (e.g. the
        Renewal "Basic Information Tab") above the requirement's actual flow variant (the New Business
        "Basic Information Page"), so the flow-correct card never becomes a candidate and anchoring is
        forced onto the wrong screen. Guarantee every same-named variant is a candidate via the graph's
        label index; the flow-aware tie-break in ``_anchored_primary`` then picks the right flow.
        Appends in place. Analysis-only (``graph.seeds``, NOT the shared ``kb.query``)."""
        for tgt in _named_targets(requirement):
            try:
                sr = await self._graph.seeds(tgt)
            except Exception:  # noqa: BLE001 — recall boost is best-effort; never fail the match
                continue
            for h in sr.seeds:
                if h.id not in seen and tgt in (h.label or "").lower():
                    seen.add(h.id)
                    ranked.append(ImpactCitation(id=h.id, kind=h.kind, label=h.label, source_locus=None))

    async def _match_stream(
        self, workspace_id: str, ws: Workspace, persona: str, requirement: str
    ) -> AsyncIterator[tuple[str, Any]]:
        """Streaming deterministic match — yields nested sub-steps for the Knowledge Retriever + Impact
        Mapper agents (so the long retrieval isn't one opaque spinner), then a final
        ``('context', ContextPackage)``. Same deterministic result as the buffered path."""
        # ── Knowledge Retriever Agent ──
        seed_query = _seed_query(getattr(ws, "title", None), requirement)
        named_targets = _named_targets(requirement)
        named_targets_query = " ".join(named_targets)
        yield ("step", {"tool": "retrieve", "label": "Understanding the request", "detail": _clip_detail(seed_query)})

        # Technical Intent Analysis (Phases 1-4): Haiku LLM intent analysis + graph impact tracing
        impact = None
        try:
            log.info(f"[analysis] === TECHNICAL INTENT ANALYSIS ===")
            impact = await self._intent_analyzer.analyze(requirement)
            if impact:
                log.info(f"[analysis] Intent analysis complete: pattern={impact.pattern.name}, confidence={impact.extraction.confidence:.0%}")
                log.info(f"[analysis] Affected components: {len(impact.all_affected_components())}, blind spots: {len(impact.blind_spots)}, risk: {impact.risk_level}")
            else:
                log.warning(f"[analysis] No recognized technical pattern detected, using standard retrieval only")
        except Exception as e:
            log.warning(f"[analysis] Technical intent analysis failed (proceeding without): {e}")

        # Semantic preprocessing disabled (Aug 13, 2026)
        semantic_keywords_query = ""

        yield ("step", {"tool": "retrieve", "label": "Searching the knowledge base", "detail": "knowledge graph + keyword + vector · focused + broad in parallel"})

        # === DEBUG: QUERY BUILDING ===
        log.info("[analysis] === STEP 4: BUILD RETRIEVAL QUERIES ===")

        # Build 3 focused retrieval queries (removed Query 4 as it was redundant with Query 1)
        # Query 1: Technical intent extracted from pattern
        # Query 2: Full requirement text (broad context)
        # Query 3: Named targets (specific entities mentioned in requirement)
        if impact:
            intent_parts = []
            if impact.extraction.target_screen:
                intent_parts.append(impact.extraction.target_screen)
            if impact.extraction.target_table:
                intent_parts.append(impact.extraction.target_table)
            intent_parts.extend(impact.extraction.flows_affected)
            # [FIX #1] Add backend systems to intent query (source_system, target_system)
            # This ensures Q1 retrieves not just UI screens/tables but also backend systems
            if impact.extraction.source_system:
                intent_parts.append(impact.extraction.source_system)
            if impact.extraction.target_system:
                intent_parts.append(impact.extraction.target_system)
            query_1 = " ".join(p for p in intent_parts if p and p.strip())
            log.debug(f"[analysis] Intent parts: {intent_parts}")

            # ORIGINAL ORDER: Q1 (technical intent) → Q2 (full requirement) → Q3 (named targets)
            # Both Q1 & Q2 are always run (different result types: architecture vs semantics)
            # Only Q3 can be skipped based on delta
            queries = [query_1, requirement, named_targets_query]
            if semantic_keywords_query:
                queries.append(semantic_keywords_query)
            log.info(f"[analysis] Query 1 (technical intent): {query_1[:80]}")
            log.info(f"[analysis]   Pattern: {impact.pattern.name}, confidence: {impact.extraction.confidence:.0%}")
        else:
            # Fallback: use seed if intent analysis failed
            queries = [seed_query, requirement, named_targets_query]
            if semantic_keywords_query:
                queries.append(semantic_keywords_query)
            log.info(f"[analysis] Query 1 (seed - fallback): {seed_query[:80]}")

        log.info(f"[analysis] Query 2 (full requirement): {requirement[:80]}...")
        log.info(f"[analysis] Query 3 (named targets): {named_targets_query or '(none)'}")
        if semantic_keywords_query:
            log.info(f"[analysis] Query 4 (semantic preprocessing): {semantic_keywords_query[:80]}...")
        else:
            log.info("[analysis] Query 4 (semantic preprocessing): unavailable")

        queries = list(dict.fromkeys(q for q in queries if q))
        log.info(f"[analysis] Total queries: {len(queries)}")
        log.info(f"[analysis] Running retrieve() sequentially with smart stopping (delta-based)...")

        # OPTIMIZATION: Smart query stopping based on delta
        # Run Q1 → Run Q2 → check delta → if delta < 3, skip Q3
        # This avoids wasting 50s on redundant queries
        results = []
        seen_ids: set[str] = set()
        delta_threshold = 3  # Stop if Q adds fewer than 3 new results

        for i, q in enumerate(queries, 1):
            log.debug(f"[analysis]   Query {i}/{len(queries)}: {q[:60]}...")
            result = await self._retrieve_with_rate_limit(self._kb, requirement=q, persona=persona)
            results.append(result)

            # Count new unique IDs from this query
            new_ids = {c.id for c in result.citations} - seen_ids
            new_count = len(new_ids)
            log.debug(f"[analysis]   Query {i} returned {len(result.citations)} citations ({new_count} new)")

            # Update seen IDs for delta check
            seen_ids.update(new_ids)

            # Smart stopping: if last query added few new results, skip remaining queries
            if i >= 2 and new_count < delta_threshold:
                skipped = len(queries) - i
                log.info(f"[analysis] Delta stopping: Q{i} added only {new_count} new results (threshold={delta_threshold}) → skipping {skipped} remaining queries")
                break

        log.info(f"[analysis] Executed {len(results)}/{len(queries)} queries")

        log.info(f"[analysis] Retrieval complete: merged {len(results)} result sets")
        seen: set[str] = set()
        cites: list[Any] = []
        answer_source: str | None = None
        for ans in results:
            answer_source = answer_source or ans.source
            for c in ans.citations:
                if c.id not in seen:
                    seen.add(c.id)
                    cites.append(c)

        # === DEBUG: RETRIEVAL RESULTS ===
        log.info(f"[analysis] === STEP 5: RETRIEVAL RESULTS ===")
        log.info(f"[analysis] Total unique citations: {len(cites)}")
        log.info(f"[analysis] KB version: {answer_source}")

        # Count by kind
        kind_counts = {}
        for c in cites:
            kind = c.kind or "Unknown"
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        log.info(f"[analysis] Citations by kind: {kind_counts}")

        ranked = [ImpactCitation(id=c.id, kind=c.kind, label=c.label, source_locus=c.source_locus) for c in cites]
        log.info(f"[analysis] Ranked candidates prepared: {len(ranked)} items")
        # Named-target recall (docs/29): guarantee every same-named variant of a named target screen
        # is a candidate (so flow-aware anchoring can pick New Business over Renewal). See helper.
        await self._add_named_target_candidates(requirement, ranked, seen)
        yield ("step", {"tool": "retrieve", "label": "Reviewed candidate cards", "detail": _kind_breakdown(ranked)})
        # PRECISION (docs/29): if the requirement names a screen/entity, the PRIMARY set is JUST that
        # anchored card (filtered to the named flow — NB vs Renewal vs Endorsement), NOT padded with
        # tangential cards. Analysis-only — never touches the shared kb.query.
        light = change_is_light(requirement)

        # DISABLE_LIGHT_WALK flag: bypass light detection for testing full impact analysis
        from app.config.settings import get_settings
        disable_light = get_settings().ANALYSIS_DISABLE_LIGHT_WALK
        if disable_light:
            light = False
            log.info(f"[analysis] ANALYSIS_DISABLE_LIGHT_WALK=True → forcing full impact walk")

        matched, anchored, is_anchored = _anchored_primary(ranked, requirement, primary_cap(requirement))

        # ─── Add LLM-extracted target_table and target_system to matched ───
        # The LLM identified specific tables and APIs to target, so prioritize them
        if impact and impact.extraction:
            target_ids = []

            # Find target table by ID or label match
            if impact.extraction.target_table:
                target_table_name = impact.extraction.target_table.upper()
                for candidate in ranked:
                    if candidate.kind in ("Entity", "ENT"):
                        if target_table_name in (candidate.label or "").upper():
                            if candidate.id not in {m.id for m in matched}:
                                target_ids.append(candidate)
                                log.info(f"[analysis] Adding LLM target_table to matched: {candidate.label} ({candidate.id})")

            # Find target system/API by label match
            if impact.extraction.target_system:
                target_sys_name = impact.extraction.target_system.lower()
                for candidate in ranked:
                    if candidate.kind in ("Component", "CMP", "ApiOp", "API", "Integration", "INT"):
                        if target_sys_name in (candidate.label or "").lower():
                            if candidate.id not in {m.id for m in matched}:
                                target_ids.append(candidate)
                                log.info(f"[analysis] Adding LLM target_system to matched: {candidate.label} ({candidate.id})")

            # Add to matched if any targets found
            if target_ids:
                matched.extend(target_ids)
                log.info(f"[analysis] Added {len(target_ids)} LLM-extracted targets to matched")

            # FALLBACK: If target_system was not found in ranked, do a secondary targeted kb.query
            if impact.extraction.target_system and not any(
                impact.extraction.target_system.lower() in (c.label or "").lower()
                for c in matched
                if c.kind in ("Component", "CMP", "ApiOp", "API")
            ):
                log.info(f"[analysis] target_system '{impact.extraction.target_system}' not found in ranked results, running targeted kb.query...")
                try:
                    fallback_result = await self._retrieve_with_rate_limit(
                        self._kb,
                        requirement=impact.extraction.target_system,
                        persona=persona
                    )
                    # Search for matching components in fallback result
                    for c in fallback_result.citations:
                        if c.kind in ("Component", "CMP", "ApiOp", "API"):
                            impact_cit = ImpactCitation(id=c.id, kind=c.kind, label=c.label, source_locus=c.source_locus)
                            if impact_cit.id not in {m.id for m in matched}:
                                matched.append(impact_cit)
                                log.info(f"[analysis] Added fallback component from secondary kb.query: {c.label} ({c.id})")
                                break  # Take the first matching component
                except Exception as e:
                    log.warning(f"[analysis] Fallback kb.query for target_system failed: {e}")

        # === TABLE SCOPE FILTERING (Fix: Filter implicit/related tables) ===
        # If requirement mentions specific table(s), filter matched to only those tables
        # (related tables will be found through downstream graph walk if needed)
        matched, implicit_tables = _filter_matched_tables(matched, requirement)

        # Mark implicit tables with metadata for UI grouping/collapsing
        for table in implicit_tables:
            # Store metadata that this is a related/implicit table
            if not hasattr(table, '_metadata'):
                table._metadata = {}
            table._metadata['is_related_table'] = True
            table._metadata['group'] = 'related-tables'

        log.info(f"[analysis] After table filter: {len(matched)} matched, {len(implicit_tables)} implicit tables (marked for UI grouping)")

        # DEBUG: log what ranked contains (especially CMP/API kinds)
        kind_counts = {}
        for c in ranked[:24]:  # look at top 24
            kind = c.kind or "unknown"
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
        log.info(f"[analysis] ranked list (top 24 by score): kinds={kind_counts} | cmp/api count={(sum(1 for c in ranked[:24] if c.kind in ['Component', 'CMP', 'ApiOp', 'API']))}")

        # Fix 3: Promote components to the matched set if not already present
        cap = primary_cap(requirement)
        matched = _promote_components(ranked, matched, cap)

        # ─── OPTIMIZATION: Narrow matched to PRIMARY TARGETS ONLY ───
        # Keep only explicitly mentioned items + LLM-extracted targets
        # This reduces ReAct context from 30+ cards to ~3, improving speed 3x
        # AFFECTED list stays at 22+ for comprehensive impact display
        primary_matched = []

        log.info(f"[analysis] Narrowing matched ({len(matched)} items) to primary targets...")
        log.info(f"[analysis]   target_screen: {impact.extraction.target_screen if impact else 'N/A'}")
        log.info(f"[analysis]   target_table: {impact.extraction.target_table if impact else 'N/A'}")
        log.info(f"[analysis]   target_system: {impact.extraction.target_system if impact else 'N/A'}")

        # FALLBACK: If impact.extraction is None (LLM failed), extract tables from requirement text
        target_table_fallback = None
        if impact and impact.extraction and impact.extraction.target_table:
            target_table_fallback = impact.extraction.target_table
        else:
            # Extract UPPERCASE_WITH_UNDERSCORES patterns from requirement (e.g., WEB_RECEIPTS)
            import re
            table_patterns = re.findall(r'\b([A-Z][A-Z0-9_]+)\b', requirement)
            # Filter to likely table names (containing underscore or all caps)
            table_names = {t for t in table_patterns if '_' in t and len(t) > 3}
            if table_names:
                target_table_fallback = list(table_names)[0]
                log.info(f"[analysis] Fallback: extracted table name from requirement: {target_table_fallback}")

        # Also extract component name from requirement (fallback for LLM failure)
        component_name_fallback = None
        if 'email' in requirement.lower() and 'modal' in requirement.lower():
            # Likely looking for EmailEditorModalComponent
            component_name_fallback = 'emaileditormodal'
            log.info(f"[analysis] Fallback: detected email/modal pattern in requirement → looking for EmailEditorModalComponent")

        if impact:
            # Priority 1: Screens (anchored or mentioned in requirement)
            for m in matched:
                if m.kind in ("Screen", "SCR"):
                    primary_matched.append(m)
                    log.info(f"[analysis] PRIMARY (screen): {m.id} [{m.label}]")

            # Priority 2: Table (LLM target_table or fallback)
            if impact.extraction and impact.extraction.target_table:
                target_table_upper = impact.extraction.target_table.upper()
                for m in matched:
                    if m.kind in ("Entity", "ENT"):
                        m_label_upper = (m.label or "").upper()
                        # Match: "WEB_RECEIPTS" in "WEB_RECEIPTS" or similar
                        if target_table_upper in m_label_upper or m_label_upper in target_table_upper:
                            if m.id not in {pm.id for pm in primary_matched}:
                                primary_matched.append(m)
                                log.info(f"[analysis] PRIMARY (table): {m.id} [{m.label}] (matched '{target_table_upper}' in '{m_label_upper}')")
                        else:
                            log.debug(f"[analysis]   SKIP table: {m.id} [{m_label_upper}] (no match for '{target_table_upper}')")

            # Priority 3: API/Component (LLM target_system or fallback)
            component_target = None
            if impact.extraction and impact.extraction.target_system:
                component_target = impact.extraction.target_system.lower()
            elif component_name_fallback:
                component_target = component_name_fallback
                log.info(f"[analysis] Using fallback component target: {component_target}")

            if component_target:
                for m in matched:
                    if m.kind in ("Component", "CMP", "ApiOp", "API"):
                        m_label_lower = (m.label or "").lower()
                        # Match: "emaileditormodal" in "emaileditormodalcomponent"
                        if component_target in m_label_lower or m_label_lower in component_target:
                            if m.id not in {pm.id for pm in primary_matched}:
                                primary_matched.append(m)
                                log.info(f"[analysis] PRIMARY (api/component): {m.id} [{m.label}] (matched '{component_target}' in '{m_label_lower}')")
                        else:
                            log.debug(f"[analysis]   SKIP component: {m.id} [{m_label_lower}] (no match for '{component_target}'")

        # Use narrowed matched list for ReAct (3-5 items instead of 30+)
        # AFFECTED list remains comprehensive for display
        if primary_matched:
            log.info(f"[analysis] Narrowed matched: {len(matched)} → {len(primary_matched)} primary targets (for faster ReAct)")
            matched = primary_matched
        else:
            log.warning(f"[analysis] No primary targets extracted; using full matched list ({len(matched)} items)")

        if is_anchored:
            yield ("step", {"tool": "retrieve", "label": "Anchored on the named target", "detail": ", ".join(anchored[:3])})
        matched_ids = [c.id for c in matched]
        yield ("step", {"tool": "retrieve", "label": "Selected the change target(s)", "detail": f"{len(matched)} primary target(s) for detailed analysis"})
        kb_version = ws.pinned_kb_version or answer_source or "fixture"

        # ── Impact Mapper Agent ──
        yield ("status", {"stage": "impact", "detail": "mapping the change across the system"})
        policy = policy_for_change(ws.route, requirement)  # tight walk for light changes, else route policy

        # DEBUG: Log matched cards before graph walk
        matched_details = [f"{m.id} ({m.kind})" for m in matched]
        log.info(
            f"[analysis] Starting impact walk: matched={matched_details} | "
            f"route={ws.route} | light={light} | policy=(depth={policy.max_depth}, "
            f"source_cap={policy.source_cap}, fanout_cap={policy.fanout_cap})"
        )

        # ─── Narrow matched list: Remove "related" tables marked for UI grouping only ───
        # Only walk from PRIMARY targets (those directly mentioned in requirement)
        # Related/implicit tables are for DISPLAY only, not for graph traversal
        primary_matched_ids = []
        for m_id, m_obj in zip(matched_ids, matched):
            # Skip items marked as related/implicit (for UI grouping only, not walkable)
            if hasattr(m_obj, '_metadata') and m_obj._metadata.get('is_related_table'):
                log.info(f"[analysis] Skipping related table from walk: {m_id} ({m_obj.label})")
                continue
            primary_matched_ids.append(m_id)

        if len(primary_matched_ids) < len(matched_ids):
            log.info(f"[analysis] Walk scope narrowed: {len(matched_ids)} matched → {len(primary_matched_ids)} primary targets")

        yield ("step", {"tool": "impact", "label": "Mapping affected areas"})
        affected, coverage = await compute_affected(self._graph, primary_matched_ids, policy)

        # ─── Add BR/FR that govern/validate the primary matched screens/tables ───
        # These rules are business-critical but may not be text-similar to the requirement
        # So find them via graph edges (GOVERNED_BY, VALIDATES) from matched nodes
        br_fr_additions = []
        matched_ids_set = {m.id for m in matched}

        for matched_id in primary_matched_ids:
            # Find rules that govern this item (GOVERNED_BY edges point FROM rule TO screen/table)
            # So we look for REVERSE edges (rules pointing in)
            try:
                for edge_type in ["GOVERNED_BY", "VALIDATES"]:
                    edges = await self._graph.get_edges_reverse(matched_id, edge_type=edge_type)
                    for edge in edges:
                        source_id = edge.get("source")
                        if not source_id:
                            continue
                        source_node = await self._graph.get_node(source_id)
                        if not source_node:
                            continue
                        # Check if it's a BR or FR
                        kind = source_node.get("kind", "")
                        if kind in ("BusinessRule", "BR", "FunctionalReq", "FR") and source_id not in matched_ids_set:
                            br_fr_additions.append(
                                ImpactCitation(
                                    id=source_id,
                                    kind=kind,
                                    label=source_node.get("label", source_id),
                                    source_locus=source_node.get("source_locus"),
                                )
                            )
                            matched_ids_set.add(source_id)
            except Exception as e:
                log.warning(f"[analysis] Failed to find governing rules for {matched_id}: {e}")
                continue

        if br_fr_additions:
            log.info(f"[analysis] Found {len(br_fr_additions)} BR/FR governing the primary targets → adding to AFFECTED only (not matched)")
            # IMPORTANT: BR/FR should NOT be in matched (which is for direct changes shown in Section 5)
            # They should ONLY be in affected (for impact display in Sections 2-3, 6-7)
            affected.extend([
                AffectedNode(
                    id=r.id,
                    label=r.label,
                    kind=r.kind,
                    via=[],
                    summary=None,
                    source_locus=r.source_locus or "",
                )
                for r in br_fr_additions
            ])

        # ─── P1/P2 Semantic Search Results (Aug 17, 2026) ───
        # P1/P2 components from technical intent analysis → add to AFFECTED only
        # Matched is kept narrow (3-4 primary targets) for Section 5 "What Is Being Modified"
        # P1/P2 results appear in Sections 2-3, 6-7 (impact display) via affected list
        if impact:
            seen_ids = {n.id for n in affected}
            p1p2_added = 0
            for comp in impact.all_affected_components():
                if comp.id not in seen_ids:
                    # Add to affected list (for impact section display)
                    affected.append(
                        AffectedNode(
                            id=comp.id,
                            label=comp.label,
                            kind=comp.kind,
                            via=[],  # impact tracer doesn't track edges, set empty
                            summary=None,
                            source_locus=comp.source_locus or "",
                        )
                    )
                    seen_ids.add(comp.id)
                    p1p2_added += 1
            log.info(f"[analysis] P1/P2 results: added {p1p2_added} components to affected (for impact display)")

        # Semantic walk disabled (Aug 13, 2026) — using deterministic impact analysis only
        semantic_walk_result: WalkResult | None = None
        # An optional / additive / non-rating change breaks nothing downstream → skip the reverse walk.
        if light:
            downstream = []
        else:
            yield ("step", {"tool": "impact", "label": "Tracing downstream dependents"})
            downstream = await compute_downstream_with_tables(self._graph, matched_ids)
            # Filter out information records (TERM kind) from downstream effects
            downstream = [d for d in downstream if d.kind not in ("TERM", "Term")]
        # attach a short grounded card-body excerpt so sections can describe WHAT each node is + HOW touched.
        await self._hydrate_summaries(matched, affected, downstream)
        yield ("step", {"tool": "impact", "label": "Checking for conflicts with in-flight work"})
        detected = await conflict_detect.evidence_contradictions(self._graph, matched_ids)
        this_ids = set(matched_ids) | {n.id for n in affected}
        detected += await conflict_detect.sibling_overlap(
            lambda: self._workspace.list(limit=100),
            self.get,
            this_workspace_id=workspace_id,
            this_ids=this_ids,
            this_requirement=requirement,
        )
        # Extract blind spots from impact analyzer
        blind_spots_from_impact = []
        if impact and impact.blind_spots:
            blind_spots_from_impact = impact.blind_spots
            log.info(f"[analysis] Passing {len(blind_spots_from_impact)} blind spot(s) from impact analyzer to context")

        yield ("context", ContextPackage(
            requirement=requirement,
            kb_version=kb_version,
            matched=matched,
            affected=affected,
            downstream=downstream,
            coverage=coverage,
            conflicts=detected,
            semantic_walk_result=semantic_walk_result,
            intent_confidence=(impact.extraction.confidence if impact else None),
            blind_spots=blind_spots_from_impact,  # NEW: Pass blind spots to context
        ))

    async def _hydrate_summaries(
        self,
        matched: list[ImpactCitation],
        affected: list[AffectedNode],
        downstream: list[AffectedNode],
    ) -> None:
        """Attach a short grounded excerpt (``summary``) to every impact-set node from its card body.

        Best-effort: ``read_many`` returns ``{}`` on any error, so a fetch miss just leaves summaries
        as ``None`` (sections fall back to label + edge note). Never fails the stage.
        """
        ids = {n.id for n in (*matched, *affected, *downstream)}
        if not ids:
            return
        bodies = await self._kb.read_many(list(ids))
        if not bodies:
            return
        for node in (*matched, *affected, *downstream):
            body = bodies.get(node.id) or {}
            node.summary = card_excerpt(body.get("prose") or body.get("text_en") or body.get("summary"))

    async def _augment_from_agent(self, context: ContextPackage, whitelist: set[str]) -> ContextPackage:
        """If the seed returned nothing but the agent grounded itself via its tools, rebuild the
        primary set from what it retrieved (capped + proportional walk), so its work isn't discarded."""
        if context.matched:  # seed worked — keep the deterministic (already-precise) context
            return context
        ids = sorted(i for i in whitelist if i)  # B2: deterministic base order (whitelist is a set)
        bodies = await self._kb.read_many(ids) if ids else {}
        resolved = [i for i in ids if i in bodies]
        if not resolved:
            return context

        def _rank(cid: str) -> int:
            kind = (bodies.get(cid) or {}).get("kind", "")
            return _PRIMARY_KIND_ORDER.index(kind) if kind in _PRIMARY_KIND_ORDER else len(_PRIMARY_KIND_ORDER)

        # B2: (rank, id) tiebreak — same-kind ids no longer depend on set-iteration order (PYTHONHASHSEED)
        chosen = sorted(resolved, key=lambda c: (_rank(c), c))[: primary_cap(context.requirement)]
        matched = [
            ImpactCitation(
                id=i, kind=(bodies[i] or {}).get("kind", ""),
                label=(bodies[i] or {}).get("label", i), source_locus=(bodies[i] or {}).get("source_locus", ""),
            )
            for i in chosen
        ]
        matched_ids = [c.id for c in matched]
        policy = policy_for_change(None, context.requirement)
        affected, coverage = await compute_affected(self._graph, matched_ids, policy)
        downstream = await compute_downstream_with_tables(self._graph, matched_ids)
        await self._hydrate_summaries(matched, affected, downstream)
        log.info(f"[impact] seed empty → rebuilt {len(matched)} primary card(s) from agent grounding")
        return ContextPackage(
            requirement=context.requirement, kb_version=context.kb_version, matched=matched,
            affected=affected, downstream=downstream, coverage=coverage, conflicts=context.conflicts,
            intent_confidence=context.intent_confidence,
        )

    async def _extract_what_is_modified(self, matched: list[ImpactCitation]) -> list[dict]:
        """Extract direct change targets for PRD 'What is being modified' section.

        Returns list of ModifiedTarget dicts with as_is/to_be for each matched item.
        Also includes components/APIs that depend on matched items.
        """
        what_is_modified = []

        try:
            # Get KB card prose for each matched item
            matched_ids = [c.id for c in matched]
            bodies = await self._kb.read_many(matched_ids) if matched_ids else {}

            for citation in matched:
                card = bodies.get(citation.id) or {}
                prose = card.get("prose") or card.get("text_en") or ""

                what_is_modified.append({
                    "id": citation.id,
                    "kind": citation.kind,
                    "label": citation.label,
                    "as_is": prose[:300] if prose else "No description in KB",
                    "to_be": "Directly touched by requirement - to be defined during design",
                    "change_type": "MODIFIED",
                    "reason": f"Named/directly affected in requirement",
                    "source_locus": citation.source_locus or ""
                })

            # Also add components/APIs that depend on matched items
            for matched_id in matched_ids:
                try:
                    edges = await self._graph.get_edges_from(matched_id)
                    for edge in edges:
                        target = await self._graph.get_node(edge.get("to_id"))
                        if target and target.get("kind") in ["Component", "CMP", "ApiOp", "API"]:
                            # Check if already in what_is_modified
                            if not any(t["id"] == target.get("id") for t in what_is_modified):
                                what_is_modified.append({
                                    "id": target.get("id"),
                                    "kind": target.get("kind"),
                                    "label": target.get("label"),
                                    "as_is": "Current implementation",
                                    "to_be": f"Must integrate with {matched_id} changes",
                                    "change_type": "MODIFIED",
                                    "reason": f"Required by {matched_id}",
                                    "source_locus": target.get("source_locus", "")
                                })
                except Exception as e:
                    log.warning(f"[analysis] Error extracting components from {matched_id}: {e}")

            log.info(f"[analysis] Extracted {len(what_is_modified)} items for 'what is modified'")
        except Exception as e:
            log.warning(f"[analysis] Error extracting what_is_modified: {e}")

        return what_is_modified

    async def _extract_where_changes_needed(
        self, matched: list[ImpactCitation], affected: list[AffectedNode]
    ) -> list[AffectedNode]:
        """Extract downstream impact for PRD 'Where changes are needed' section.

        Returns affected nodes that are NOT in matched, filtered by:
        1. Kind: Only business-critical and user-facing kinds
        2. Hops: Only direct and near impacts (1-2 hops)

        This reduces noise (internal modules, implementation details) for PRD readability.
        """
        try:
            matched_ids = {c.id for c in matched}

            # Filter 1: Important kinds only (exclude implementation details)
            IMPORTANT_KINDS = {
                "Screen", "SCR",
                "Workflow", "WF",
                "System", "SYS",
                "Process", "PROC",
                "Integration", "INT",
                "Entity", "ENT",
                "BusinessRule", "BR",
                "FunctionalReq", "FR",
            }

            # Filter 2: Only direct + near impacts (1-2 hops)
            MAX_HOPS = 2

            where_changes = [
                node for node in affected
                if node.id not in matched_ids
                and node.kind in IMPORTANT_KINDS  # Filter 1: By Kind
                and node.hops <= MAX_HOPS  # Filter 2: By Distance
            ]

            # Log filtering results
            unfiltered_count = len([n for n in affected if n.id not in matched_ids])
            log.info(
                f"[analysis] Extracted 'where changes needed': {unfiltered_count} unfiltered → "
                f"{len(where_changes)} after kind+hops filtering (max_hops={MAX_HOPS})"
            )

            return where_changes
        except Exception as e:
            log.warning(f"[analysis] Error extracting where_changes_needed: {e}")
            return []

    def _ground(self, analysis: ImpactAnalysis, context: ContextPackage, whitelist: set[str]) -> ImpactAnalysis:
        """Grounding gate on the narrative — any cited id outside the whitelist → deterministic fallback."""
        text, score = self._ground_field(
            analysis.narrative,
            whitelist,
            lambda: fallback_narrative(context.matched, context.affected),
        )
        analysis.narrative = text
        analysis.grounding_score = score
        return analysis

    async def _persist(
        self, workspace_id: str, persona: str, analysis: ImpactAnalysis, context: ContextPackage
    ) -> None:
        """S3 write + DB mirror + GROUNDS links — ALL operations are best-effort (never raises).

        S3 is the single artifact object per workspace/stage — a re-run OVERWRITES the same key
        (docs/09 §3.6 s3_uri). The DB row mirrors it (content + s3_uri) as an audit trail + fallback.
        Errors are logged at WARNING level; the caller always receives the result regardless.
        """
        content = analysis.model_dump_json()

        # 1. S3 write (overwrite on re-run — primary SSOT)
        s3_uri: str | None = None
        try:
            s3_uri = await self._store.put_json(self._s3_key(workspace_id), analysis.model_dump())
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[impact] S3 write failed for {workspace_id}: {exc}")

        # 1b. Technical .docx → S3 ONLY (audit / technical access). The UI serves the business view
        #     (export_business_docx); the technical document is never surfaced to the front end.
        try:
            tech_docx = render_impact_docx(analysis, self._template)
            await self._store.put_bytes(
                self._store.key(workspace_id, self._STAGE_FOLDER, "analysis.technical.docx"),
                tech_docx,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; never fails the stage
            log.warning(f"[impact] technical .docx S3 upload failed for {workspace_id}: {exc}")

        # 2. DB mirror (audit trail + fallback for GET when S3 misses)
        # On re-run: clean up previous ANALYSIS artifacts + their relationships for this workspace
        try:
            await self._trace.cleanup_stage_artifacts(workspace_id, kind="analysis")
        except Exception as exc:  # noqa: BLE001
            log.debug(f"[impact] Cleanup of previous analysis artifacts: {exc}")  # best-effort

        artifact_id: str | None = None
        try:
            artifact = await self._workspace.attach_artifact(
                workspace_id,
                kind="analysis",
                content=content,
                s3_uri=s3_uri,
                grounding_score=analysis.grounding_score,
                template_id=analysis.template_id,
                template_version=analysis.template_version,
                actor=f"persona:{persona}",
            )
            artifact_id = artifact.get("artifact_id")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[impact] DB artifact write failed for {workspace_id}: {exc}")

        # 2b. Auto-link to previous stage (if any)
        if artifact_id:
            try:
                # Build justification using deterministic extraction
                systems_data = [
                    {
                        "system_id": s.id,
                        "system_name": s.label,
                        "impact_level": "high" if s.hops == 1 else "low"
                    }
                    for s in analysis.systems_affected
                ]
                matched_data = [
                    {
                        "id": c.id,
                        "kind": c.kind,
                        "label": c.label
                    }
                    for c in context.matched
                ]
                scope_items = {
                    "in_scope": [{"label": item.label} for item in analysis.scope.new] if analysis.scope.new else [],
                    "out_of_scope": [],
                    "deferred": []
                }

                justification = RelationshipJustification.build_analysis_from_requirement(
                    requirement_text=context.requirement,
                    classification_change_class=analysis.classification.change_class,
                    systems_affected=systems_data,
                    matched_kb_cards=matched_data,
                    scope_items=scope_items,
                    coverage_pct=analysis.coverage.coverage_pct,
                )

                await self._trace.auto_link_to_previous(
                    workspace_id=workspace_id,
                    current_artifact_id=artifact_id,
                    current_kind="analysis",
                    justification=justification,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(f"[impact] Auto-linking to previous stage failed for {workspace_id}: {exc}")

        # 3. GROUNDS traceability links (best-effort; skipped if artifact_id unavailable)
        if artifact_id and context.matched:
            for cite in context.matched:
                try:
                    await self._trace.add_grounds(
                        workspace_id=workspace_id,
                        from_artifact_id=artifact_id,
                        persona=persona,
                        artifact_kind="analysis",
                        to_kb_card_id=cite.id,
                        kb_version=analysis.kb_version,
                        source_locus=cite.source_locus,
                        stage="ANALYSIS",
                        applied_because="RETRIEVAL_MATCH",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"[impact] GROUNDS link failed for {workspace_id} → {cite.id}: {exc}")

        # 3b. GROUNDS links for graph-walk affected nodes (complete the audit trail)
        #     These are not in matched (no direct retrieval hit) but are in the impact set
        #     via typed graph edges. applied_because="GRAPH_WALK" distinguishes them from
        #     direct retrieval hits so auditors can see the full traceability path.
        if artifact_id and context.affected:
            for node in context.affected:
                try:
                    await self._trace.add_grounds(
                        workspace_id=workspace_id,
                        from_artifact_id=artifact_id,
                        persona=persona,
                        artifact_kind="analysis",
                        to_kb_card_id=node.id,
                        kb_version=analysis.kb_version,
                        source_locus=node.source_locus,
                        stage="ANALYSIS",
                        applied_because="GRAPH_WALK",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(f"[impact] GROUNDS (graph-walk) link failed for {workspace_id} → {node.id}: {exc}")

        # 4. Save systems, scope, metrics to fe_workspace_analysis table for UI traceability tab
        try:
            dao = WorkspaceAnalysisDAO()

            # Extract systems from analysis
            systems = [
                {
                    "system_id": node.id,
                    "system_name": node.label or node.id,
                    "impact_level": "high" if node.kind == "System" else "medium",
                    "components_changed": [],
                    "confidence": getattr(node, "confidence", 0.85),
                }
                for node in analysis.systems_affected
            ]

            # Extract scope from ScopeDiff: new + enhancement = IN_SCOPE, existing = OUT_OF_SCOPE
            in_scope_items = []
            for i, item in enumerate(analysis.scope.new or []):
                in_scope_items.append({
                    "item_id": item.id or f"scope-new-{i}",
                    "item_description": item.label,
                    "scope_category": "IN_SCOPE"
                })
            for i, item in enumerate(analysis.scope.enhancement or []):
                in_scope_items.append({
                    "item_id": item.id or f"scope-enh-{i}",
                    "item_description": item.label,
                    "scope_category": "IN_SCOPE"
                })

            scope = {
                "in_scope": in_scope_items,
                "out_of_scope": [
                    {
                        "item_id": item.id or f"scope-existing-{i}",
                        "item_description": item.label,
                        "scope_category": "OUT_OF_SCOPE"
                    }
                    for i, item in enumerate(analysis.scope.existing or [])
                ],
                "deferred": [],  # ScopeDiff doesn't have deferred items; use empty list
            }

            # Extract metrics
            metrics = {
                "total_artifacts": len(analysis.affected),
                "total_relationships": len([t for t in analysis.touch_points if t]),
                "coverage_pct": analysis.coverage.coverage_pct if analysis.coverage else 0.0,
                "orphan_count": len(analysis.gaps),
                "systems_affected": len(analysis.systems_affected),
            }

            # Save to database
            await dao.update_systems(workspace_id, systems)
            await dao.update_scope(workspace_id, scope)
            await dao.update_metrics(workspace_id, metrics)

            # 4b. Save PRD impact sections (Aug 18, 2026)
            # what_is_modified: direct targets
            # where_changes_needed: downstream impact
            # DEBUG: Check if analysis has the fields
            log.info(f"[impact] DEBUG: analysis.what_is_modified type={type(analysis.what_is_modified)}, len={len(analysis.what_is_modified) if analysis.what_is_modified else 0}")
            log.info(f"[impact] DEBUG: analysis.where_changes_needed type={type(analysis.where_changes_needed)}, len={len(analysis.where_changes_needed) if analysis.where_changes_needed else 0}")

            what_is_modified_data = [
                {
                    "id": item["id"] if isinstance(item, dict) else item.id,
                    "kind": item["kind"] if isinstance(item, dict) else item.kind,
                    "label": item["label"] if isinstance(item, dict) else item.label,
                    "as_is": item.get("as_is", "") if isinstance(item, dict) else getattr(item, "as_is", ""),
                    "to_be": item.get("to_be", "") if isinstance(item, dict) else getattr(item, "to_be", ""),
                    "change_type": item.get("change_type", "MODIFIED") if isinstance(item, dict) else getattr(item, "change_type", "MODIFIED"),
                    "reason": item.get("reason", "") if isinstance(item, dict) else getattr(item, "reason", ""),
                    "source_locus": item.get("source_locus", "") if isinstance(item, dict) else getattr(item, "source_locus", ""),
                }
                for item in (analysis.what_is_modified or [])
            ]
            where_changes_needed_data = [
                {
                    "id": node.id if hasattr(node, 'id') else node["id"],
                    "kind": node.kind if hasattr(node, 'kind') else node["kind"],
                    "label": node.label if hasattr(node, 'label') else node["label"],
                    "via": node.via if hasattr(node, 'via') else node.get("via", []),
                    "source_locus": (node.source_locus or "") if hasattr(node, 'source_locus') else node.get("source_locus", ""),
                }
                for node in (analysis.where_changes_needed or [])
            ]

            await dao.update_analysis_sections(workspace_id, what_is_modified_data, where_changes_needed_data)

            log.info(
                f"[impact] Saved analysis data for {workspace_id}: {len(systems)} systems, {len(scope['in_scope'])} in-scope items, "
                f"{len(what_is_modified_data)} direct targets, {len(where_changes_needed_data)} downstream impacts"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[impact] Failed to save analysis data to traceability table for {workspace_id}: {exc}")

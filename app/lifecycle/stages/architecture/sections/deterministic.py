"""Deterministic Architecture section builders and async graph-diagram builders.

All sync builders are pure functions — no LLM, no network. Cyclomatic ≤ 21 per function.
Async builders (build_asIs_diagram, build_sequence_diagrams) are called from the handler
BEFORE assemble_srd(); their string results are passed as params to keep assemble_srd sync.

Cross-repo patterns:
  IMAD  — deterministic Mermaid DSL from GraphProvider.neighbors() walk (graph-first)
  XPF   — TraceLink: build_story_refs populates diagram_sections per story
  S3    — coverage gate: check_arch_coverage (every SYS must have ≥1 INT, every INT needs protocol)
  Genlite — section_basis provenance tags via _basis() helper in assemble.py
"""

from __future__ import annotations

import re

from app.config.settings import settings
from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff
from app.lifecycle.stages.architecture.match.context import ArchitectureContext
from app.lifecycle.stages.architecture.schema import (
    ApiSpec,
    DataEntity,
    IntegrationPoint,
    SchemaChange,
    SequenceDiagram,
    SRDStoryRef,
    SystemComponent,
)
from app.lifecycle.stages.fsd.schema import FunctionalRequirement, Stub

# ---------------------------------------------------------------------------
# Async graph-walk diagram builders (called from handler, not from assemble)
# ---------------------------------------------------------------------------

async def _build_topology(
    graph: object,
    seed_ids: list[str],
    *,
    in_scope: set[str] | None = None,
    seed_cap: int = 30,
    edge_cap: int = 60,
) -> str:
    """Shared graph-walk → scoped, connected Mermaid flowchart (IMAD pattern).

    Walks the depth-1 neighbourhood of each seed along the structural (``_E2E_EDGE_LABELS``) edges,
    then applies three guards so the diagram stays relevant and connected:
      1. **kind** — a neighbour is kept only if it is an architecture kind (``_E2E_NODE_KINDS``);
      2. **scope** — a neighbour is kept only if it is in ``in_scope`` (seeds are always in scope),
         so a shared hub (PEGA/WOD/ESB) can't drag its whole neighbourhood in;
      3. **connectivity** — a node is rendered only if it takes part in at least one kept edge,
         which drops the disconnected/orphan nodes the raw walk pulls in.
    Never raises — returns "" (hides the card) when nothing connected is in scope.
    """
    seed_ids = list(seed_ids)
    if not graph or not seed_ids:
        return ""  # nothing to seed → empty string hides the card (no blank SVG under a header)

    seed_set = set(seed_ids)
    scope = seed_set | set(in_scope or set())

    labels: dict[str, str] = {}
    kinds: dict[str, str] = {}
    raw_edges: list[tuple[str, str, str]] = []
    for sid in seed_ids[:seed_cap]:  # cap seeds — keeps the walk and the rendered diagram bounded
        try:
            sub = await graph.neighbors(sid, direction="both", depth=1)
        except Exception:  # noqa: BLE001 — never fail the SRD on a graph error
            continue
        for n in sub.nodes:
            labels[n.id] = n.label or n.id
            kinds[n.id] = n.kind or ""
        for e in sub.edges:
            if e.label in _E2E_EDGE_LABELS:
                raw_edges.append((e.source, e.target, e.label))

    def _eligible(nid: str) -> bool:
        # Seeds are already architecture-scoped; a neighbour must be an architecture kind AND in scope.
        return nid in seed_set or (kinds.get(nid, "") in _E2E_NODE_KINDS and nid in scope)

    kept_edges: list[tuple[str, str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for src, tgt, lbl in raw_edges:
        if src == tgt or (src, tgt) in seen_pairs:
            continue
        if not _eligible(src) or not _eligible(tgt):
            continue
        seen_pairs.add((src, tgt))
        kept_edges.append((src, tgt, lbl))
        if len(kept_edges) >= edge_cap:
            break

    if not kept_edges:
        return ""  # nothing connected in scope → hide the card rather than render floating nodes

    # Deterministic node order: first appearance across kept edges (stable under PYTHONHASHSEED=0).
    ordered: list[str] = []
    seen_n: set[str] = set()
    for src, tgt, _lbl in kept_edges:
        for nid in (src, tgt):
            if nid not in seen_n:
                seen_n.add(nid)
                ordered.append(nid)

    def _safe(nid: str) -> str:
        return nid.replace("-", "_")

    lines = ["flowchart LR"]
    for nid in ordered:
        lines.append(f'  {_safe(nid)}["{labels.get(nid, nid).replace(chr(34), chr(39))}"]')
    for src, tgt, lbl in kept_edges:
        lines.append(f'  {_safe(src)} -->|"{lbl.replace(chr(34), chr(39))}"| {_safe(tgt)}')
    return "\n".join(lines)


async def build_asIs_diagram(
    graph: object, sys_ids: list[str], *, in_scope: set[str] | None = None
) -> str:
    """Build AS-IS Mermaid flowchart DSL via GraphProvider.neighbors() walk (IMAD pattern).

    Args:
        graph: GraphProvider instance (self._graph in handler).
        sys_ids: SYS-*/CMP-*/SCR-*/ENT-*/INT-*/API-* KB IDs to seed the walk.
        in_scope: the change's grounded id set (upstream citations + C1 walk) — neighbours outside
            it are dropped so the AS-IS view stays scoped to the change instead of the whole KB.

    Returns Mermaid DSL string (never raises — returns "" to hide the card when empty).
    """
    return await _build_topology(graph, sys_ids, in_scope=in_scope, seed_cap=8, edge_cap=20)


# Node families that act as sequence-diagram participants (systems, components, integrations, roles).
_SEQ_PARTICIPANT_FAMILIES = ("SYS", "CMP", "INT", "API", "ROLE", "EXT")


def _seq_clean(text: str, limit: int = 60) -> str:
    """Mermaid-message-safe label: drop the ``:`` / newlines that break sequence syntax; truncate."""
    text = " ".join((text or "").replace(":", " -").split())
    return (text[: limit - 1] + "…") if len(text) > limit else text


async def _sequence_from_workflow(
    graph: object, wf_id: str, wf_label: str, default_parts: list[str]
) -> tuple[list[str], str]:
    """Walk a workflow/sequence node's 1-hop neighborhood → a GROUNDED sequenceDiagram.

    Participants = the system/component/integration/role nodes involved; messages = the typed edges
    among them (source ->> target : edge-label). Falls back to a participant list + a Note when the
    graph has no usable edges. Uses aliases (P0, P1 …) so labels with spaces/punctuation stay valid."""
    label_by_id: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    try:
        sub = await graph.neighbors(wf_id, direction="both", depth=2)  # C3 — deepen to 2-hop flows
        for n in sub.nodes:
            label_by_id[n.id] = n.label or n.id
        for e in sub.edges:
            edges.append((e.source, e.target, e.label))
    except Exception:  # noqa: BLE001 — never fail the SRD on a graph error
        pass

    part_labels: list[str] = []
    for nid, lbl in label_by_id.items():
        if nid.split("-", 1)[0] in _SEQ_PARTICIPANT_FAMILIES and lbl not in part_labels:
            part_labels.append(lbl)
    participants = part_labels[:6] or default_parts
    alias = {p: f"P{i}" for i, p in enumerate(participants)}

    lines = [f'  participant {alias[p]} as "{_seq_clean(p, 40)}"' for p in participants]
    msg_count = 0
    seen: set[tuple[str, str]] = set()
    for src, tgt, lbl in edges:
        s_lbl, t_lbl = label_by_id.get(src), label_by_id.get(tgt)
        if s_lbl in alias and t_lbl in alias and s_lbl != t_lbl and (s_lbl, t_lbl) not in seen:
            seen.add((s_lbl, t_lbl))
            lines.append(f"  {alias[s_lbl]}->>{alias[t_lbl]}: {_seq_clean(lbl)}")
            msg_count += 1
            if msg_count >= 12:
                break
    if msg_count == 0:  # grounded participants but no edges among them → labelled Note
        lines.append(f"  Note over {alias[participants[0]]}: {_seq_clean(wf_label)}")
    return participants, "sequenceDiagram\n" + "\n".join(lines)


async def build_sequence_diagrams(
    graph: object,
    ctx: ArchitectureContext,
) -> list[dict]:
    """Build Mermaid sequenceDiagrams GROUNDED in the KB's workflows/sequences (WF-*/SEQ-*).

    Design-first order: Architecture precedes Stories, so sequences are seeded from the workflows the
    change touches (``ctx.wf_matched``) — walking the graph for each to render real participant→message
    flows — not from Stories (which don't exist yet). Capped to keep the SRD manageable.
    """
    # Participants: prefer the systems the change actually matched; else the per-LOB fallback
    # from config (never a hardcoded LOB system list); else a neutral placeholder.
    default_parts: list[str] = (
        [c.label or c.id for c in ctx.sys_matched[:5]]
        or settings.LOB_SYSTEM_PARTICIPANTS
        or ["System"]
    )
    # C3 — raised cap; ctx.wf_matched now includes C1 graph-retrieved Workflow/Sequence nodes,
    # so this seeds from the change's workflows even in the design-first (thin-upstream) order.
    seeds = ctx.wf_matched[:8]
    results: list[dict] = []
    for wf in seeds:
        participants, dsl = await _sequence_from_workflow(graph, wf.id, wf.label or wf.id, default_parts)
        results.append({
            "story_id": wf.id,  # kept as the section key (schema field name is generic)
            "title": f"Sequence: {wf.label or wf.id}",
            "mermaid_dsl": dsl,
            "participants": participants,
        })
    return results


# ---------------------------------------------------------------------------
# Sync section builders — pure KB data, no LLM
# ---------------------------------------------------------------------------

def build_scope_definition(ctx: ArchitectureContext) -> ScopeDiff:
    """Pass-through: FSD scope (ScopeDiff) as the SRD scope section.

    If FSD not available, returns empty ScopeDiff (stub — ARCH-TODO).
    """
    if ctx.fsd_doc and ctx.fsd_doc.scope:
        return ctx.fsd_doc.scope
    return ScopeDiff()


def build_functional_requirements(ctx: ArchitectureContext) -> list[FunctionalRequirement]:
    """Pass-through: FSD functional requirements as-is into the SRD.

    Architect uses these to confirm architectural coverage per FR.
    """
    if ctx.fsd_doc and ctx.fsd_doc.functional_requirements:
        return list(ctx.fsd_doc.functional_requirements)
    # Stub one FR per fr_matched KB card (no FSD available)
    return [
        FunctionalRequirement(
            id=c.id,
            title=c.label or c.id,
            source_locus=c.source_locus,
            source_type="kb_explicit",
        )
        for c in ctx.fr_matched
    ]


def build_component_design(ctx: ArchitectureContext) -> list[SystemComponent]:
    """Build SystemComponent list from SYS-* and CMP-* KB cards in context."""
    components: list[SystemComponent] = []
    seen: set[str] = set()

    for c in ctx.sys_matched + ctx.cmp_matched:
        if c.id in seen:
            continue
        seen.add(c.id)
        # Cross-reference: which INTs connect to this component?
        interfaces = [
            i.id for i in (ctx.int_matched + ctx.api_matched)
            if _component_uses_integration(c, i)
        ]
        components.append(SystemComponent(
            id=c.id,
            label=c.label or c.id,
            kind=c.kind if c.kind else "System",
            responsibility="[ARCH-TODO: Architect to define]",
            interfaces=interfaces,
            source_locus=c.source_locus,
            source_type="kb_explicit" if c.kind else "stub",
        ))
    return components


def _component_uses_integration(comp: ImpactCitation, intg: ImpactCitation) -> bool:
    """Heuristic: integration label contains system label fragment."""
    if not comp.label or not intg.label:
        return False
    return comp.label.lower()[:6] in intg.label.lower()


def build_integration_design(ctx: ArchitectureContext) -> list[IntegrationPoint]:
    """Build IntegrationPoint list from INT-* and API-* KB cards in context."""
    integrations: list[IntegrationPoint] = []
    seen: set[str] = set()

    for c in ctx.int_matched + ctx.api_matched:
        if c.id in seen:
            continue
        seen.add(c.id)
        integrations.append(IntegrationPoint(
            id=c.id,
            label=c.label or c.id,
            kind=c.kind if c.kind else "Integration",
            source_locus=c.source_locus,
        ))
    return integrations


def build_story_refs(ctx: ArchitectureContext) -> list[SRDStoryRef]:
    """Build traceability rows: one SRDStoryRef per story (XPF TraceLink pattern)."""
    if not ctx.stories:
        return []

    refs: list[SRDStoryRef] = []
    for row in ctx.stories.traceability or []:
        # Determine which SRD sections this story maps to
        sections: list[str] = []
        kb_kinds = {
            c.kind for c in ctx.matched
            if c.id in set(row.kb_ids or [])
        }
        if kb_kinds & {"System", "Component"}:
            sections.append("component_design")
        if kb_kinds & {"Integration", "ApiOp"}:
            sections.append("integration_design")
        if row.story_id:
            sections.append("sequence_diagrams")

        refs.append(SRDStoryRef(
            story_id=row.story_id,
            kb_ids=list(row.kb_ids or []),
            diagram_sections=sections,
            link_type=row.link_type if row.link_type else "IMPLEMENTS",
        ))
    return refs


def build_references(ctx: ArchitectureContext) -> list[ImpactCitation]:
    """Union of all KB citations from all prior artifacts (deduped)."""
    return list(ctx.matched)


# ---------------------------------------------------------------------------
# S3 coverage gate — every SYS must have ≥1 INT; every INT must have protocol
# ---------------------------------------------------------------------------

def check_arch_coverage(
    components: list[SystemComponent],
    integrations: list[IntegrationPoint],
) -> list[Stub]:
    """S3 coverage gate: flag architectural gaps as ARCH-TODO open items.

    Rule 1: every SYS-* component must connect to ≥1 integration.
    Rule 2: every INT-* integration must have a protocol defined.
    """
    stubs: list[Stub] = []
    int_ids = {i.id for i in integrations}

    for comp in components:
        if comp.kind in ("System", "Component"):
            linked = set(comp.interfaces) & int_ids
            if not linked:
                stubs.append(Stub(
                    id=f"arch-cov-{comp.id}",
                    section="open_items",
                    description=(
                        f"The component '{comp.label}' has no integration defined yet — "
                        "the Architect should confirm how it connects to other systems."
                    ),
                    marker="BA-TODO",
                    priority="High",
                ))

    for intg in integrations:
        if not intg.protocol:
            stubs.append(Stub(
                id=f"arch-proto-{intg.id}",
                section="open_items",
                description=(
                    f"The integration '{intg.label}' has no protocol defined yet — "
                    "the Architect should confirm how the two systems communicate."
                ),
                marker="BA-TODO",
                priority="Medium",
            ))

    return stubs


# ---------------------------------------------------------------------------
# C2 — full end-to-end architecture diagram (async graph walk, layered bands)
# ---------------------------------------------------------------------------

# Structural edges that carry end-to-end topology (mirror match/retrieve.ARCH_EDGE_LABELS).
_E2E_EDGE_LABELS = frozenset({"DEPENDS_ON", "CALLS", "FEEDS_INTO", "PRODUCES", "IMPLEMENTS", "SCREEN_OF"})

# Node kinds that belong on an architecture diagram (mirror match/retrieve.ARCH_KINDS + Screen,
# since SCREEN_OF is an end-to-end edge and screens seed the walk). A depth-1 neighbour whose kind
# is outside this set (Role/Term/BusinessRule/Process/…) is never drawn — it is not architecture.
_E2E_NODE_KINDS = frozenset(
    {"System", "Integration", "Component", "ApiOp", "Entity", "Workflow", "Sequence", "Screen"}
)


async def build_endToEnd_diagram(graph: object, ctx: ArchitectureContext) -> str:
    """C2 — full end-to-end Mermaid flowchart across the retrieved SYS/CMP/INT/API/ENT/SCR nodes.

    Seeds from every architecture node in context and walks the structural edges, scoped to the
    change's grounded id set (``ctx.allowed_ids``) so a shared hub can't drag its whole neighbourhood
    in, and pruned to connected nodes only. Never raises — returns "" when the graph is empty."""
    # Include the screens the change touches (from matched SCR cards) so a screen/field change
    # anchors the end-to-end walk instead of coming back empty.
    scr_matched = [c for c in ctx.matched if (c.kind or "").lower() in ("screen", "scr") or c.id.startswith("SCR-")]
    seed_ids = [c.id for c in (ctx.sys_matched + ctx.cmp_matched + ctx.int_matched
                               + ctx.api_matched + ctx.ent_matched + scr_matched)]
    return await _build_topology(graph, seed_ids, in_scope=set(ctx.allowed_ids or set()),
                                 seed_cap=30, edge_cap=60)


# ---------------------------------------------------------------------------
# C4 — API specs (from API-* cards; method/path/payload parsed from card bodies)
# ---------------------------------------------------------------------------

_HTTP_VERB_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\b")
_PATH_RE = re.compile(r"(/[A-Za-z0-9_{}\-./]+)")


def _card_text(bodies: dict, cid: str) -> str:
    b = bodies.get(cid) or {}
    return " ".join(str(b.get(k, "")) for k in ("label", "prose", "text_en"))


def _filter_relevant_apis(ctx: ArchitectureContext, api_candidates: list[ImpactCitation]) -> list[ImpactCitation]:
    """[FIX #2] Filter API-* cards to only those relevant to the requirement.

    Includes APIs that are:
    1. Cited in FSD functional_requirements or integration_design, OR
    2. Referenced in component labels/descriptions (components that use these APIs), OR
    3. In scope of impact analysis matched set

    Avoids including all APIs from graph walk that may be tangential.
    """
    if not api_candidates:
        return []

    cited_api_ids: set[str] = set()

    # Get APIs cited in FSD
    if ctx.fsd_doc and hasattr(ctx.fsd_doc, 'functional_requirements'):
        for fr in (ctx.fsd_doc.functional_requirements or []):
            # FunctionalRequirement id may be API-* itself
            if fr.id.startswith('API-'):
                cited_api_ids.add(fr.id)
            # Also check if the FunctionalRequirement references APIs in its text/title
            for field in (getattr(fr, 'title', '') or '', getattr(fr, 'as_is', '') or '', getattr(fr, 'to_be', '') or ''):
                for candidate in api_candidates:
                    if candidate.id.lower() in field.lower() or candidate.label and candidate.label.lower() in field.lower():
                        cited_api_ids.add(candidate.id)

    # Get APIs from FSD integration_design
    if ctx.fsd_doc and hasattr(ctx.fsd_doc, 'integration_design'):
        for integ in (ctx.fsd_doc.integration_design or []):
            if integ.id.startswith('API-'):
                cited_api_ids.add(integ.id)

    # Get APIs referenced in component descriptions
    for comp in ctx.cmp_matched:
        comp_text = _card_text(ctx.card_bodies, comp.id) or (comp.label or '')
        for api_cand in api_candidates:
            # Check if API is mentioned in component
            if api_cand.id in comp_text or (api_cand.label and api_cand.label.lower() in comp_text.lower()):
                cited_api_ids.add(api_cand.id)

    # Get APIs from impact analysis matched set (direct matches)
    for item in getattr(ctx.impact, 'matched', []) or []:
        if item.id.startswith('API-'):
            cited_api_ids.add(item.id)

    # Return candidates that are in cited_api_ids, or if no APIs are cited, return all (fallback)
    filtered = [c for c in api_candidates if c.id in cited_api_ids]

    # Fallback: if no APIs were found by filtering, return all candidates (don't suppress all APIs)
    # This ensures SRD still has architecture info even if citations are sparse
    return filtered if filtered else api_candidates


def build_api_specs(ctx: ArchitectureContext) -> list[ApiSpec]:
    """C4 — one ApiSpec per relevant API-* card, method/path grounded in the card body where present.

    [FIX #2] Filters API cards to only those relevant to requirement (cited in FSD, used by components,
    or matched in impact analysis). Falls back to all APIs if none are found (ensures coverage).
    """
    # [FIX #2] Filter to relevant APIs only
    relevant_apis = _filter_relevant_apis(ctx, ctx.api_matched)

    specs: list[ApiSpec] = []
    seen: set[str] = set()
    for c in relevant_apis:
        if c.id in seen:
            continue
        seen.add(c.id)
        text = _card_text(ctx.card_bodies, c.id) or (c.label or "")
        verb = _HTTP_VERB_RE.search(text)
        path = _PATH_RE.search(text)
        grounded = bool(verb or path)
        specs.append(ApiSpec(
            id=c.id,
            label=c.label or c.id,
            http_method=verb.group(1) if verb else "",
            path=path.group(1) if path else "",
            source_locus=c.source_locus,
            source_type="kb_explicit" if grounded else "stub",
        ))
    return specs


# ---------------------------------------------------------------------------
# C5 — data model + ER diagram (from ENT-* DB-table cards)
# ---------------------------------------------------------------------------

_KEYCOLS_RE = re.compile(r"^\s*key_columns?\s*[:=]\s*(.+)$", re.I | re.M)
_RELS_RE = re.compile(r"^\s*relationships?\s*[:=]\s*(.+)$", re.I | re.M)
_ERD_RE = re.compile(r"(erDiagram[\s\S]+)", re.I)


def _split_list(raw: str) -> list[str]:
    return [t.strip(" []`") for t in re.split(r"[,;]", raw) if t.strip(" []`")]


def build_data_model(ctx: ArchitectureContext) -> list[DataEntity]:
    """C5 — DataEntity per ENT-* DB-table card; key_columns/relationships from the card body."""
    entities: list[DataEntity] = []
    seen: set[str] = set()
    for c in ctx.ent_matched:
        if c.id in seen:
            continue
        seen.add(c.id)
        body = ctx.card_bodies.get(c.id) or {}
        prose = " ".join(str(body.get(k, "")) for k in ("prose", "text_en"))
        kc = _KEYCOLS_RE.search(prose)
        rel = _RELS_RE.search(prose)
        entities.append(DataEntity(
            id=c.id,
            label=c.label or c.id,
            key_columns=_split_list(kc.group(1)) if kc else [],
            relationships=_split_list(rel.group(1)) if rel else [],
            source_locus=c.source_locus or body.get("source_locus"),
            source_type="kb_explicit" if (kc or rel) else "stub",
        ))
    return entities


def build_erd_diagram(ctx: ArchitectureContext, data_model: list[DataEntity]) -> str:
    """C5 — prefer a corpus erDiagram present in a cited card body; else synthesize from key_columns."""
    for cid, body in ctx.card_bodies.items():  # noqa: B007 — cid kept for readability
        blob = " ".join(str(body.get(k, "")) for k in ("prose", "text_en"))
        m = _ERD_RE.search(blob)
        if m:
            return m.group(1).strip()
    if not data_model:
        return "erDiagram\n  %% No DB-table entities (ENT-*) in context"

    def _ident(raw: str) -> str:
        return re.sub(r"\W+", "_", raw or "").strip("_")

    lines = ["erDiagram"]
    for e in data_model:
        safe = _ident(e.label or e.id) or e.id.replace("-", "_")
        cols = [f"    string {_ident(c)}" for c in e.key_columns[:12] if _ident(c)]
        body = "\n".join(cols)
        lines.append(f"  {safe} {{\n{body}\n  }}" if body else f"  {safe} {{\n  }}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# C6 — schema-change specs (scope deltas vs existing DB-table entities)
# ---------------------------------------------------------------------------

def build_schema_changes(ctx: ArchitectureContext, data_model: list[DataEntity]) -> list[SchemaChange]:
    """C6 — deterministic seed: diff FSD scope (new/enhancement) against existing ENT-* tables.

    Emits one SchemaChange per scope item that names/implies a table: matched table → ALTER, no
    match → NEW. The LLM later refines column/rationale within the grounding whitelist; here we only
    seed cited, table-anchored deltas (never fabricate a column)."""
    scope = ctx.impact.scope_definition if (ctx.impact and getattr(ctx.impact, "scope_definition", None)) else None
    if scope is None:
        return []
    table_labels = {(e.label or "").lower(): e for e in data_model}
    changes: list[SchemaChange] = []
    for item in (list(getattr(scope, "new", []) or []) + list(getattr(scope, "enhancement", []) or [])):
        label = (getattr(item, "label", "") or "").strip()
        if not label:
            continue
        hit = next((e for lbl, e in table_labels.items() if lbl and lbl in label.lower()), None)
        changes.append(SchemaChange(
            table=hit.label if hit else label,
            op="ALTER" if hit else "NEW",
            rationale=f"Scope item: {label}",
            kb_id=hit.id if hit else getattr(item, "id", None),
            source_type="kb_grounded" if hit else "llm_reasoned",
        ))
    return changes

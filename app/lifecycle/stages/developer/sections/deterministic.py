"""Deterministic Developer section builders — no LLM, no network. Cyclomatic ≤ 21 per function.

build_impl_tasks   — one ImplTask per story (SRD traceability → component/api/int/screen buckets)
                     P1: enriched with story_text, gherkin_ac, definition_of_done from DevContext.story_details
                     P2: enriched with fr_deltas (as-is→to-be), business_rules_summary from DevContext.fr_map/br_map
build_code_stubs   — one+ CodeStub per CMP-*/API-* card (stack-detected, template-based)
build_wiring_tasks — one WiringTask per INT-* from SRD integration_design
check_dev_coverage — S3 coverage gate: every story ≥1 task; every gapless INT logged

Cross-repo:
  S3-plugins FSD-generator — section ownership matrix (AUTO/HYBRID/DEV-TODO)
  Connected-layer L2       — token-overlap → gap_status (≥0.7 direct, else derived/gap)
  S3-plugins conductor     — GAP HANDLING: write to dev_gaps.yaml, not into artifacts
  IMAD angular_builder     — Angular component + service stub templates
  IMAD springboot_builder  — Spring Boot @Service + @RestController stub templates
"""

from __future__ import annotations

import re

from app.lifecycle.stages.architecture.schema import IntegrationPoint, SystemComponent
from app.lifecycle.stages.developer.match.context import DevContext
from app.lifecycle.stages.developer.schema import (
    ChangePlanItem,
    CodeStub,
    DevGap,
    FRDelta,
    GapStatus,
    ImplTask,
    Ownership,
    Stack,
    WiringTask,
)
from app.lifecycle.stages.fsd.schema import Stub


# ── Stack detection ────────────────────────────────────────────────────────────

def _detect_stack(card: dict) -> Stack:
    """Detect tech stack from KB card source_locus or label heuristic.

    Maps corpus paths to stack identifiers:
      UI-NewBusiness / UI-Endorsement → angular
      Services-NewBusiness / csvimport → java_springboot
      CodeBase/ESB/                   → ibm_esb
      AdobeForms/                     → adobe_xdp
      Database/                       → sql
      WebOnline-Adapter / freia       → java_legacy
      label/locus contains 'pega'     → pega

    Fallback heuristics (when locus is missing):
      label contains 'angular'/'component' → angular
      label contains 'service'/'springboot' or kind=='ApiOp' → java_springboot
      kind=='database' or label contains 'table' → sql
    """
    locus = (card.get("source_locus") or "").lower().replace("\\", "/")
    label = (card.get("label") or "").lower()
    kind = (card.get("kind") or "").lower()

    # Pattern 1: source_locus matching (existing)
    if "ui-newbusiness" in locus or "ui-endorsement" in locus:
        return "angular"
    if "services-newbusiness" in locus or "csvimport-services" in locus:
        return "java_springboot"
    if "codebase/esb" in locus or "esb" in locus:
        return "ibm_esb"
    if "adobeforms" in locus or locus.endswith(".xdp"):
        return "adobe_xdp"
    if "database" in locus or locus.endswith(".sql"):
        return "sql"
    if "webonline-adapter" in locus or "freia" in locus:
        return "java_legacy"
    if "pega" in label or "pega blob" in locus:
        return "pega"

    # Pattern 2: label/kind heuristic (fallback when locus missing)
    if "angular" in label or "component" in kind:
        return "angular"
    if "service" in label or "springboot" in label or kind == "apiop":
        return "java_springboot"
    if "api" in kind or "endpoint" in label or "rest" in label:
        return "java_springboot"  # Default REST tier
    if "database" in kind or "table" in label:
        return "sql"

    return "unknown"


def _detect_ownership(card: dict, stack: Stack) -> Ownership:
    """S3-plugins ownership matrix: card confidence + stack → AUTO/HYBRID/DEV-TODO."""
    conf = float(card.get("confidence") or 0.5)
    source_type = card.get("source_type") or "stub"

    # Stacks that can never be AUTO-generated (no source code or low-code)
    if stack in ("ibm_esb", "adobe_xdp", "sql", "pega"):
        return "DEV-TODO"
    if source_type == "test-fixture":
        return "DEV-TODO"
    if conf >= 0.8:
        return "AUTO"
    if conf >= 0.6:
        return "HYBRID"
    return "DEV-TODO"


# ── Token-overlap gap_status (Connected-layer L2 pattern) ─────────────────────

def _token_overlap(a: str, b: str) -> float:
    """Jaccard token overlap between two strings (L2 phase4_l2.py pattern)."""
    tok_a = set(re.sub(r"[^a-z0-9 ]", " ", a.lower()).split())
    tok_b = set(re.sub(r"[^a-z0-9 ]", " ", b.lower()).split())
    if not tok_a or not tok_b:
        return 0.0
    return len(tok_a & tok_b) / max(len(tok_a), len(tok_b))


def _gap_status_from(story_title: str, card_label: str, card_conf: float) -> tuple[GapStatus, float]:
    score = _token_overlap(story_title, card_label)
    if score >= 0.7:
        return "direct", score
    if score >= 0.4 and card_conf >= 0.6:
        return "derived", score
    if card_conf < 0.4:
        return "gap", score
    return "derived", score


# ── P1 helpers: story enrichment ──────────────────────────────────────────────

def _build_story_text(row: object) -> str:
    """Compose the user story prose from StoriesDocument StoryRow.

    Returns empty string when the row carries stub/placeholder values so that
    the markdown renderer can show a clear 'run Stories stage first' notice
    instead of rendering confusing generated-stub prose.
    """
    as_a = (getattr(row, "as_a", "") or "").strip()
    i_want = (getattr(row, "i_want", "") or "").strip()
    so_that = (getattr(row, "so_that", "") or "").strip()
    # Suppress stubs — i_want is the most reliable field to check
    if not as_a and not i_want:
        return ""
    if _is_stub(i_want) or _is_stub(so_that):
        return ""
    parts = []
    if as_a:
        parts.append(f"As a {as_a}")
    if i_want:
        parts.append(f"I want {i_want}")
    if so_that:
        parts.append(f"so that {so_that}")
    return ", ".join(parts) + "."


def _build_gherkin_ac(row: object) -> dict[str, str]:
    """Extract Gherkin AC dict from StoriesDocument StoryRow.

    Emits each non-stub clause independently. If all clauses are stubs/empty,
    returns empty dict to signal 'run Stories stage first'. But partial AC
    (e.g., given+when present, then missing) is returned as-is — partial
    guidance is better than nothing for the developer.
    """
    given = (getattr(row, "ac_given", "") or "").strip()
    when = (getattr(row, "ac_when", "") or "").strip()
    then = (getattr(row, "ac_then", "") or "").strip()

    result: dict[str, str] = {}

    # Emit each clause independently (don't suppress all if one is stub)
    if given and not _is_stub(given):
        result["given"] = given
    if when and not _is_stub(when):
        result["when"] = when
    if then and not _is_stub(then):
        result["then"] = then

    # Return dict as-is: empty if all are stubs, partial if some are present
    return result


_STUB_PATTERNS = frozenset({"[precondition", "[action", "[expected outcome", "[business value"})


def _is_stub(text: str) -> bool:
    """Return True when text is a placeholder stub (not real story content)."""
    lc = text.lower().strip()
    return any(p in lc for p in _STUB_PATTERNS) or lc.startswith("implement ")


def _build_definition_of_done(
    gherkin_ac: dict[str, str],
    kb_ids: list[str],
    wiring_ids: list[str],
) -> list[str]:
    """Generate a concrete Definition of Done checklist from Gherkin AC + KB ids.

    Rules (in order):
    1. One DoD item per Gherkin clause (Given/When/Then) → verifiable test condition.
    2. One generic item per component or API id (unit test expectation).
    3. One item per integration id (wiring + integration test).
    4. Final item: QA acceptance (always appended when Gherkin AC is available).

    NOTE: items do NOT include a leading '[ ]' — the markdown renderer adds '- [ ]'.
    """
    dod: list[str] = []
    given = gherkin_ac.get("given", "")
    when = gherkin_ac.get("when", "")
    then = gherkin_ac.get("then", "")
    # Only add Gherkin-derived DoD items if the content is not a stub placeholder
    if given and not _is_stub(given):
        dod.append(f"Precondition verified: {given}")
    if when and not _is_stub(when):
        dod.append(f"Action tested: {when}")
    if then and not _is_stub(then):
        dod.append(f"Observable outcome passes: {then}")
    for kid in kb_ids[:3]:
        if kid.startswith("CMP-") or kid.startswith("API-"):
            dod.append(f"Unit tests pass for {kid}")
    for kid in wiring_ids[:2]:
        dod.append(f"Integration wiring tested for {kid}")
    if dod:  # Only add QA sign-off when there is substantive content
        dod.append("QA sign-off: acceptance criteria verified in test environment")
    return dod


# ── P2 helpers: FSD delta + BR summaries ─────────────────────────────────────

def _build_fr_deltas(
    kb_ids: list[str],
    fr_map: dict[str, object],
) -> list[FRDelta]:
    """Build FRDelta list from FR-* kb_ids using the FSD fr_map.

    Only emits FRDelta for IDs that exist in fr_map (FSD-grounded).
    Never fabricates as_is/to_be — stubs with None if FSD doesn't have it.
    """
    deltas: list[FRDelta] = []
    for kid in kb_ids:
        if not kid.startswith("FR-"):
            continue
        fr = fr_map.get(kid)
        if fr is None:
            continue
        deltas.append(FRDelta(
            fr_id=kid,
            title=getattr(fr, "title", "") or "",
            as_is=getattr(fr, "as_is", None),
            to_be=getattr(fr, "to_be", None),
            source_locus=getattr(fr, "source_locus", None),
            source_type="fsd_derived",
        ))
    return deltas


def _build_br_summaries(
    kb_ids: list[str],
    br_map: dict[str, object],
    screens: list[str],
) -> list[str]:
    """Build business rules summary strings from BR-* ids + screen governess.

    Developer persona cannot see raw BR-* cards (persona restriction). We surface
    the rule_text from the FSD (which already extracted them) as plain summaries —
    no persona bypass, just FSD-derived content carried forward.

    Format: '[BR-xxx] rule_text[:200] (applies_to: SCR-xxx, SCR-yyy)'
    Capped at 200 chars per rule to keep the DevDocument scannable.
    """
    summaries: list[str] = []
    # Also scan br_map for any BR that applies_to a screen in this story
    all_br_ids = list(kb_ids)
    for br_id, br in br_map.items():
        applies_to = getattr(br, "applies_to", []) or []
        if any(scr in applies_to for scr in screens) and br_id not in all_br_ids:
            all_br_ids.append(br_id)
    for kid in all_br_ids:
        if not kid.startswith("BR-"):
            continue
        br = br_map.get(kid)
        if br is None:
            continue
        rule_text = (getattr(br, "rule_text", "") or "").strip()
        applies_to = getattr(br, "applies_to", []) or []
        cap = rule_text[:200] + ("…" if len(rule_text) > 200 else "")
        applies_str = f" (applies_to: {', '.join(applies_to[:3])})" if applies_to else ""
        summaries.append(f"[{kid}] {cap}{applies_str}")
    return summaries


# ── impl_tasks builder ─────────────────────────────────────────────────────────

def build_impl_tasks(ctx: DevContext) -> tuple[list[ImplTask], list[DevGap]]:
    """Build one ImplTask per story from SRD story_refs.

    Returns (tasks, gaps). Gaps are recorded when no KB card reaches the story.
    S3 GAP HANDLING rule: gaps go into dev_gaps.yaml — never emitted as invented tasks.

    P1 enrichment: story_text, gherkin_ac, definition_of_done, priority, points from ctx.story_details.
    P2 enrichment: fr_deltas (as-is→to-be per FR), business_rules_summary from ctx.fr_map/br_map.
    """
    tasks: list[ImplTask] = []
    gaps: list[DevGap] = []

    # Build a lookup: kb_id → card body
    card_lookup = ctx.card_bodies

    # Use story_refs from SRD (SRDStoryRef: story_id, kb_ids, title)
    for ref in ctx.story_refs:
        story_id = ref.story_id
        story_title = ref.title or story_id
        kb_ids = [kid for kid in (ref.kb_ids or []) if kid in ctx.allowed_ids]

        # ── P1: extract story enrichment ──────────────────────────────────────
        story_row = ctx.story_details.get(story_id)
        story_text = _build_story_text(story_row) if story_row else ""
        gherkin_ac = _build_gherkin_ac(story_row) if story_row else {}
        story_priority = (getattr(story_row, "priority", "Medium") or "Medium") if story_row else "Medium"
        story_points = int(getattr(story_row, "points", 0) or 0) if story_row else 0
        # Use richer title from StoryRow if available
        if story_row and getattr(story_row, "title", ""):
            story_title = story_row.title

        if not kb_ids:
            # No KB cards visible to developer for this story → record gap
            gaps.append(DevGap(
                story_id=story_id,
                description=(
                    f"Story {story_id} has no developer-visible KB cards "
                    "(CMP/API/INT/SCR/FR/SYS). Architect review required."
                ),
                gap_status="gap",
                sme_required=True,
                source="build_impl_tasks",
            ))
            # Still emit the task so the developer knows the story exists
            dod = _build_definition_of_done(gherkin_ac, [], [])
            tasks.append(ImplTask(
                story_id=story_id,
                title=story_title,
                kb_ids=[],
                ownership="DEV-TODO",
                gap_status="gap",
                match_confidence=0.0,
                story_text=story_text,
                gherkin_ac=gherkin_ac,
                definition_of_done=dod,
                story_priority=story_priority,
                story_points=story_points,
            ))
            continue

        # Bucket KB IDs by kind
        components = [kid for kid in kb_ids if kid.startswith("CMP-")]
        apis = [kid for kid in kb_ids if kid.startswith("API-")]
        integrations = [kid for kid in kb_ids if kid.startswith("INT-")]
        screens = [kid for kid in kb_ids if kid.startswith("SCR-")]

        # Compute gap_status from token overlap of best-matching card label
        best_conf = 0.0
        best_gap: GapStatus = "gap"
        for kid in kb_ids:
            card = card_lookup.get(kid, {})
            label = card.get("label") or kid
            conf = float(card.get("confidence") or 0.5)
            gs, score = _gap_status_from(story_title, label, conf)
            if score > best_conf:
                best_conf = score
                best_gap = gs

        # Ownership from best-matching card
        best_card = card_lookup.get(kb_ids[0], {})
        for kid in kb_ids:
            if card_lookup.get(kid, {}).get("confidence", 0) > best_card.get("confidence", 0):
                best_card = card_lookup.get(kid, {})
        stack = _detect_stack(best_card)
        ownership = _detect_ownership(best_card, stack)

        # ── P1: definition of done ─────────────────────────────────────────────
        dod = _build_definition_of_done(gherkin_ac, kb_ids, integrations)

        # ── P2: FR delta + BR summaries from FSD ──────────────────────────────
        fr_deltas = _build_fr_deltas(kb_ids, ctx.fr_map)
        br_summaries = _build_br_summaries(kb_ids, ctx.br_map, screens)

        tasks.append(ImplTask(
            story_id=story_id,
            title=story_title,
            kb_ids=kb_ids,
            components_to_modify=components,
            apis_to_implement=apis,
            integrations_to_wire=integrations,
            screens_to_update=screens,
            ownership=ownership,
            gap_status=best_gap,
            match_confidence=round(best_conf, 3),
            story_text=story_text,
            gherkin_ac=gherkin_ac,
            definition_of_done=dod,
            story_priority=story_priority,
            story_points=story_points,
            fr_deltas=fr_deltas,
            business_rules_summary=br_summaries,
        ))

    return tasks, gaps


# ── change-plan builder (high-level "files + what to change" summary; no code) ───

# Change-neutral, LOB-neutral phrasing — describes WHERE/what-kind of change, never assumes a
# specific field/domain (works for any change class or LOB).
_LAYER_BY_STACK: dict[str, str] = {
    "angular": "Frontend (UI)",
    "java_springboot": "Backend (service)",
    "java_legacy": "Backend (legacy)",
    "sql": "Data",
    "ibm_esb": "Integration (ESB)",
    "adobe_xdp": "Forms",
    "pega": "PEGA",
    "unknown": "Other",
}
_CHANGE_BY_LAYER: dict[str, str] = {
    "Frontend (UI)": "Update this screen/component to implement the change (form controls + template binding).",
    "Backend (service)": "Update this service/endpoint to implement the change, persist it, and carry it downstream.",
    "Backend (legacy)": "Update this adapter/service to handle the change.",
    "Data": "Add or adjust the column/attribute for the change.",
    "Integration (ESB)": "Route/map the changed data through this integration.",
    "Forms": "Add or adjust the field in this form definition.",
    "PEGA": "Update the PEGA rule/flow for the change.",
    "Other": "Apply the required change in this file.",
}


def build_change_plan(code_stubs: list[CodeStub]) -> list[ChangePlanItem]:
    """Derive the high-level Change Plan (files + what to change, NO code) from the code stubs.

    Deduped by file. ``action`` = Modify existing (real file:line) vs Create new; ``change`` is a
    concise, change-/LOB-neutral instruction keyed off the layer. The actual code stays in the
    per-file stub and the .md export."""
    seen: set[str] = set()
    plan: list[ChangePlanItem] = []
    for s in code_stubs:
        real = bool(re.match(r"[^:]+:\d", (s.source_locus or "").strip()))
        file = (
            (s.source_locus or "").split(" ", 1)[0].split(":", 1)[0] if real
            else (s.filename or s.component_id)
        )
        if not file or file in seen:
            continue
        seen.add(file)
        layer = _LAYER_BY_STACK.get(s.stack, "Other")
        plan.append(ChangePlanItem(
            file=file,
            layer=layer,
            action="Modify existing file" if real else "Create new file",
            change=_CHANGE_BY_LAYER.get(layer, _CHANGE_BY_LAYER["Other"]),
            component_id=s.component_id,
        ))
    return plan


# ── code_stubs builder ─────────────────────────────────────────────────────────

def build_code_stubs(ctx: DevContext) -> list[CodeStub]:
    """Build one+ CodeStub per CMP-*/API-* card (template-based, no LLM).

    Dispatches to stack-specific stub generator. PEGA, XDP, ESB → reference docs only.
    """
    stubs: list[CodeStub] = []
    seen: set[str] = set()

    # Story → KB ids mapping for stub.story_id assignment
    kid_to_story: dict[str, str] = {}
    for ref in ctx.story_refs:
        for kid in (ref.kb_ids or []):
            if kid not in kid_to_story:
                kid_to_story[kid] = ref.story_id

    all_cards = list(ctx.cmp_cards) + list(ctx.api_cards)
    for comp in all_cards:
        if comp.id in seen or comp.id not in ctx.allowed_ids:
            continue
        seen.add(comp.id)
        card_body = ctx.card_bodies.get(comp.id) or {}
        # Fall back to the component's own fields when the KB body is absent (e.g. code cards
        # injected after card_bodies was fetched) — otherwise _detect_stack sees {} → "unknown".
        if not card_body.get("source_locus") and getattr(comp, "source_locus", None):
            card_body = {
                **card_body,
                "source_locus": comp.source_locus,
                "label": comp.label,
                "kind": comp.kind,
                "source_type": getattr(comp, "source_type", ""),
                "responsibility": getattr(comp, "responsibility", ""),
            }
        stack = _detect_stack(card_body)
        ownership = _detect_ownership(card_body, stack)
        story_id = kid_to_story.get(comp.id, "")

        new_stubs = _make_stubs(comp, card_body, stack, ownership, story_id)
        stubs.extend(new_stubs)

    return stubs


def _make_stubs(
    comp: SystemComponent,
    card: dict,
    stack: Stack,
    ownership: Ownership,
    story_id: str,
) -> list[CodeStub]:
    """Dispatch to stack-specific stub generator."""
    if stack == "angular":
        return _angular_stubs(comp, card, ownership, story_id)
    if stack == "java_springboot":
        return _java_spring_stubs(comp, card, ownership, story_id)
    if stack == "java_legacy":
        return _java_legacy_stub(comp, card, ownership, story_id)
    if stack == "ibm_esb":
        return [_esb_reference(comp, card, story_id)]
    if stack == "adobe_xdp":
        return [_xdp_reference(comp, card, story_id)]
    if stack == "sql":
        return [_sql_migration_stub(comp, card, story_id)]
    if stack == "pega":
        return [_pega_reference(comp, card, story_id)]
    return [_unknown_stub(comp, card, ownership, story_id)]


def _to_pascal(s: str) -> str:
    return "".join(w.capitalize() for w in re.sub(r"[^a-zA-Z0-9]", " ", s).split())


def _to_kebab(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "-", s.lower()).strip("-")


def _source_snippet(locus: str, radius: int = 10) -> str | None:
    """Read the REAL source at ``file:line`` (± radius lines) so the stub shows the actual code to
    change, not just the class name. Resolves the corpus-relative path against the repo root; returns
    ``None`` when the file can't be found/read (never raises). Grounded — it's the verbatim source."""
    from pathlib import Path

    m = re.match(r"([^:]+):(\d+)", (locus or "").strip())
    if not m:
        return None
    rel, line = m.group(1).strip(), int(m.group(2))
    here = Path(__file__).resolve()
    # candidate roots: japan repo root (parents[6]), agents repo (parents[5]), CWD and its parent.
    roots = [here.parents[6], here.parents[5], Path.cwd(), Path.cwd().parent]
    for base in roots:
        fp = base / rel
        if fp.is_file():
            try:
                lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:  # noqa: BLE001 — snippet is best-effort
                return None
            lo, hi = max(0, line - 1 - radius), min(len(lines), line - 1 + radius + 1)
            out = [
                f"{i + 1:>5} {'>>>' if i == line - 1 else '   '} {lines[i]}"
                for i in range(lo, hi)
            ]
            return "\n".join(out)
    return None


def _where_to_change(
    comp: SystemComponent, card: dict, prefix: str, suggested_path: str
) -> str:
    """Developer-facing 'where to make the change' header for a code stub.

    - HAS a real ``source_locus`` (file:line): point at the exact file and mark the
      edit site with ``>>> CHANGE HERE``, so the developer edits existing source in
      place instead of guessing.
    - NO ``source_locus`` (proposed / net-new component, e.g. PROP-CMP-*): give an
      honest 'new file to create' message with a sensible suggested location plus the
      component's responsibility as a guiding comment.

    Domain-neutral: the suggested path is derived from the component label/kind only —
    it carries no hardcoded LOB / repo tokens.
    """
    locus = (comp.source_locus or "").strip()
    responsibility = (
        getattr(comp, "responsibility", "") or card.get("responsibility") or ""
    ).strip()
    if locus:
        file_path = locus.split(" ", 1)[0]  # drop any '§section char:start-end' suffix
        detail = f" — {responsibility}" if responsibility else ""
        header = (
            f"{prefix} File to edit: {file_path}\n"
            f"{prefix} >>> CHANGE HERE: apply this story's change for {comp.id} here{detail}"
        )
        snippet = _source_snippet(file_path)  # the ACTUAL current code at that line (not just a name)
        if snippet:
            header += (
                f"\n{prefix}\n{prefix} ---- current code (the >>> line is your edit site) ----\n{snippet}"
            )
        return header
    resp = responsibility or f"implement {comp.label or comp.id} per the story requirement"
    return (
        f"{prefix} New file to create — suggested location: {suggested_path}\n"
        f"{prefix} Responsibility: {resp}"
    )


def _has_real_locus(comp: SystemComponent) -> bool:
    """True when the component points at an existing source file (``path:line``)."""
    return bool(re.match(r"[^:]+:\d", (comp.source_locus or "").strip()))


def _edit_in_place_stub(
    comp: SystemComponent, card: dict, ownership: Ownership, story_id: str, language: str, stack: Stack
) -> CodeStub:
    """A stub for an EXISTING file: the real code snippet + a 'change here' marker only — NO
    fabricated skeleton (that boilerplate is for net-new files, and only adds noise to a real edit)."""
    file_path = (comp.source_locus or "").split(" ", 1)[0].split(":", 1)[0]
    fname = file_path.rsplit("/", 1)[-1] or comp.id
    return CodeStub(
        story_id=story_id,
        component_id=comp.id,
        language=language,
        stub_type="edit",
        stack=stack,
        filename=fname,
        code=_where_to_change(comp, card, "//", ""),
        ownership=ownership,
        source_type=card.get("source_type", "kb_explicit"),
        source_locus=comp.source_locus or None,
    )


def _angular_stubs(
    comp: SystemComponent, card: dict, ownership: Ownership, story_id: str
) -> list[CodeStub]:
    """Angular component + service stubs (IMAD angular_builder pattern)."""
    if _has_real_locus(comp):  # existing file → show the real code, not a fabricated skeleton
        return [_edit_in_place_stub(comp, card, ownership, story_id, "typescript", "angular")]
    label = comp.label or comp.id
    pascal = _to_pascal(label)
    kebab = _to_kebab(label)
    locus = comp.source_locus or ""
    ownership_note = "DEV-TODO: fill" if ownership == "DEV-TODO" else "HYBRID: review"
    comp_marker = _where_to_change(comp, card, "//", f"src/app/{kebab}/{kebab}.component.ts")
    svc_marker = _where_to_change(comp, card, "//", f"src/app/{kebab}/{pascal}Service.ts")

    component_code = f"""// Generated from KB card {comp.id}
{comp_marker}
// ownership: {ownership} — {ownership_note} @Input()/@Output() bindings per SRD

import {{ Component, OnInit }} from '@angular/core';

@Component({{
  selector: 'app-{kebab}',
  templateUrl: './{kebab}.component.html',
  styleUrls: ['./{kebab}.component.scss']
}})
export class {pascal}Component implements OnInit {{
  // DEV-TODO: Add @Input() / @Output() per SRD integration_design
  // DEV-TODO: Inject services per SRD component_design.interfaces

  ngOnInit(): void {{
    // DEV-TODO: Initialize — load data, set up subscriptions
  }}
}}
"""

    service_code = f"""// Generated from KB card {comp.id} (service counterpart)
{svc_marker}
// DEV-TODO: Implement methods. See API-* cards for endpoint contracts.

import {{ Injectable }} from '@angular/core';
import {{ HttpClient }} from '@angular/common/http';
import {{ Observable }} from 'rxjs';

@Injectable({{ providedIn: 'root' }})
export class {pascal}Service {{
  private readonly apiBase = '/api/v1';

  constructor(private http: HttpClient) {{}}

  // DEV-TODO: Add methods matching API-* cards in SRD integration_design
}}
"""

    return [
        CodeStub(
            story_id=story_id,
            component_id=comp.id,
            language="typescript",
            stub_type="component",
            stack="angular",
            filename=f"{kebab}.component.ts",
            code=component_code,
            ownership=ownership,
            source_type=card.get("source_type", "kb_derived"),
            source_locus=locus or None,
        ),
        CodeStub(
            story_id=story_id,
            component_id=comp.id,
            language="typescript",
            stub_type="service",
            stack="angular",
            filename=f"{pascal}Service.ts",
            code=service_code,
            ownership=ownership,
            source_type=card.get("source_type", "kb_derived"),
            source_locus=locus or None,
        ),
    ]


def _java_spring_stubs(
    comp: SystemComponent, card: dict, ownership: Ownership, story_id: str
) -> list[CodeStub]:
    """Spring Boot @Service + @RestController stubs (IMAD springboot_builder pattern)."""
    if _has_real_locus(comp):  # existing file → real code, not a fabricated skeleton
        return [_edit_in_place_stub(comp, card, ownership, story_id, "java", "java_springboot")]
    label = comp.label or comp.id
    pascal = _to_pascal(label)
    locus = comp.source_locus or ""
    pkg = "com.aig.connect.auto.service"
    pkg_path = pkg.replace(".", "/")

    is_api = comp.kind == "ApiOp"
    if is_api:
        filename = f"{pascal}Controller.java"
        marker = _where_to_change(comp, card, "//", f"src/main/java/{pkg_path}/{filename}")
        code = f"""// Generated from KB card {comp.id}
{marker}
// ownership: {ownership}
// DEV-TODO: fill request/response body types from API-* card fields

package {pkg};

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/v1")
public class {pascal}Controller {{

    // DEV-TODO: Inject {pascal}Service
    // DEV-TODO: Add @Valid + error handling per SRD NFRs

    @PostMapping("/{_to_kebab(label)}")
    public ResponseEntity<Object> handle(
            @RequestBody Object request) {{
        // DEV-TODO: delegate to {pascal}Service
        throw new UnsupportedOperationException("DEV-TODO: not yet implemented");
    }}
}}
"""
        stub_type = "controller"
    else:
        filename = f"{pascal}Service.java"
        marker = _where_to_change(comp, card, "//", f"src/main/java/{pkg_path}/{filename}")
        code = f"""// Generated from KB card {comp.id}
{marker}
// ownership: {ownership}

package {pkg};

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@Service
public class {pascal}Service {{

    private static final Logger log = LoggerFactory.getLogger({pascal}Service.class);

    // DEV-TODO: Inject repositories per SRD integration_design
    // DEV-TODO: Inject downstream ESB / WOD clients per INT-* cards

    @Transactional
    public void process() {{
        // DEV-TODO: implement per story requirement
        throw new UnsupportedOperationException("DEV-TODO: not yet implemented");
    }}
}}
"""
        stub_type = "service"

    return [CodeStub(
        story_id=story_id,
        component_id=comp.id,
        language="java",
        stub_type=stub_type,
        stack="java_springboot",
        filename=filename,
        code=code,
        ownership=ownership,
        source_type=card.get("source_type", "kb_derived"),
        source_locus=locus or None,
    )]


def _java_legacy_stub(
    comp: SystemComponent, card: dict, ownership: Ownership, story_id: str
) -> list[CodeStub]:
    """Legacy Java/JSP stub (WOD/FREIA adapter — no Spring annotations)."""
    if _has_real_locus(comp):  # existing file → real code, not a fabricated skeleton
        return [_edit_in_place_stub(comp, card, ownership, story_id, "java", "java_legacy")]
    label = comp.label or comp.id
    pascal = _to_pascal(label)
    locus = comp.source_locus or ""
    marker = _where_to_change(comp, card, "//", f"src/main/java/{pascal}.java")
    code = f"""// Generated from KB card {comp.id}
{marker}
// Stack: Legacy Java/JSP (WOD/FREIA adapter — WebOnline-Adapter)
// ownership: HYBRID — verify against existing source before modifying

public class {pascal} {{
    // DEV-TODO: Locate existing class in WebOnline-Adapter before creating new
    // DEV-TODO: Follow existing package structure and naming conventions
    // DEV-TODO: Coordinate with WOD team for ESB integration changes
}}
"""
    return [CodeStub(
        story_id=story_id, component_id=comp.id, language="java",
        stub_type="component", stack="java_legacy", filename=f"{pascal}.java",
        code=code, ownership="HYBRID",
        source_type=card.get("source_type", "kb_derived"), source_locus=locus or None,
    )]


def _esb_reference(comp: SystemComponent, card: dict, story_id: str) -> CodeStub:
    label = comp.label or comp.id
    locus = comp.source_locus or ""
    code = f"""// Integration Reference: {comp.id}
// Source: {locus}
// Stack: IBM ESB / ESQL (IBM Integration Bus / App Connect)
// ownership: DEV-TODO — ESB flows are configured in IBM ACE Toolkit, not in source code

// DEV-TODO:
//   1. Open IBM ACE Toolkit and locate the message flow for {label}
//   2. Confirm WSDL endpoint (source: {comp.id})
//   3. Map request fields from SRD integration_design.from_component
//   4. Add error handling for SOAP faults
// CAUTION: ESB repo name may be DISPUTED — see enrichment/contradictions.md
"""
    return CodeStub(
        story_id=story_id, component_id=comp.id, language="esql",
        stub_type="esb_reference", stack="ibm_esb", filename=f"{comp.id}-esb-reference.txt",
        code=code, ownership="DEV-TODO",
        source_type=card.get("source_type", "kb_derived"), source_locus=locus or None,
    )


def _xdp_reference(comp: SystemComponent, card: dict, story_id: str) -> CodeStub:
    label = comp.label or comp.id
    locus = comp.source_locus or ""
    code = f"""// XDP Form Reference: {comp.id}
// Source: {locus}
// Stack: Adobe LiveCycle XDP (rendered via AEM — not Angular source)
// ownership: DEV-TODO

// DEV-TODO:
//   1. Cross-reference XDP field names vs Angular reactive form model
//   2. Mirror XDP validation rules in Angular form validators
//   3. JA-language form labels need SME translation before Angular implementation
//      See: _review-queue.md tag needs-ja-sme
//   4. Confirm AEM rendering pipeline with AEM team
"""
    return CodeStub(
        story_id=story_id, component_id=comp.id, language="reference",
        stub_type="xdp_reference", stack="adobe_xdp", filename=f"{comp.id}-xdp-reference.txt",
        code=code, ownership="DEV-TODO",
        source_type=card.get("source_type", "kb_derived"), source_locus=locus or None,
    )


def _sql_migration_stub(comp: SystemComponent, card: dict, story_id: str) -> CodeStub:
    label = comp.label or comp.id
    locus = comp.source_locus or ""
    code = f"""-- Migration stub: {comp.id}
-- Source: {locus}
-- Stack: SQL / Oracle (ASACDP database)
-- ownership: DEV-TODO — DBA review required before execution
-- SECURITY: Submit via Flyway migration process; never execute directly

-- DEV-TODO: Migration for {label}
-- DEV-TODO: Check existing Flyway migrations for version sequence
-- DEV-TODO: DBA must review and approve before merge

-- V{{next_version}}__{{description}}.sql
-- ALTER TABLE {{table_name}}
--   ADD COLUMN {{column_name}} {{type}} DEFAULT {{default}} NOT NULL;
--   -- DEV-TODO: confirm column name from {comp.id} source_locus
"""
    return CodeStub(
        story_id=story_id, component_id=comp.id, language="sql",
        stub_type="sql_migration", stack="sql", filename=f"{comp.id}-migration.sql",
        code=code, ownership="DEV-TODO",
        source_type=card.get("source_type", "kb_derived"), source_locus=locus or None,
    )


def _pega_reference(comp: SystemComponent, card: dict, story_id: str) -> CodeStub:
    label = comp.label or comp.id
    locus = comp.source_locus or ""
    code = f"""// PEGA Reference: {comp.id}
// Source: {locus} (runtime export — not source code)
// Stack: PEGA low-code platform
// ownership: DEV-TODO — changes made in PEGA Studio, not in source files

// DEV-TODO:
//   1. Open PEGA Studio → Application → Cases
//   2. Locate case type for {label}
//   3. Apply changes per SRD component_design.responsibility
//   4. Export updated PEGA blob to Architecture/ folder after change
// Note: PEGA Blob XML may be DISPUTED — see enrichment/contradictions.md
"""
    return CodeStub(
        story_id=story_id, component_id=comp.id, language="reference",
        stub_type="pega_reference", stack="pega", filename=f"{comp.id}-pega-reference.txt",
        code=code, ownership="DEV-TODO",
        source_type=card.get("source_type", "kb_derived"), source_locus=locus or None,
    )


def _unknown_stub(
    comp: SystemComponent, card: dict, ownership: Ownership, story_id: str
) -> CodeStub:
    if _has_real_locus(comp):  # existing file → real code, not a "confirm the stack" scaffold
        ext = (comp.source_locus or "").split(":", 1)[0].rsplit(".", 1)[-1].lower()
        lang = {"ts": "typescript", "java": "java", "sql": "sql", "xml": "xml", "py": "python"}.get(ext, "text")
        return _edit_in_place_stub(comp, card, ownership, story_id, lang, "unknown")
    locus = comp.source_locus or ""
    kebab = _to_kebab(comp.label or comp.id) or comp.id.lower()
    marker = _where_to_change(comp, card, "//", f"src/{kebab}/{comp.id}")
    code = f"""// Generated from KB card {comp.id}
{marker}
// ownership: DEV-TODO
//
// Tech stack not auto-detected from the KB card. Confirm the target stack, then
// replace this scaffold with a stack-appropriate implementation for the change above.
"""
    return CodeStub(
        story_id=story_id, component_id=comp.id, language="reference",
        stub_type="component", stack="unknown", filename=f"{comp.id}-stub.txt",
        code=code, ownership="DEV-TODO",
        source_type=card.get("source_type", "stub"), source_locus=locus or None,
    )


# ── wiring_tasks builder ───────────────────────────────────────────────────────

def build_wiring_tasks(ctx: DevContext) -> list[WiringTask]:
    """Build one WiringTask per INT-* from SRD integration_design."""
    tasks: list[WiringTask] = []
    seen: set[str] = set()
    for intg in ctx.int_cards:
        if intg.id in seen or intg.id not in ctx.allowed_ids:
            continue
        seen.add(intg.id)
        has_protocol = bool(intg.protocol)
        tasks.append(WiringTask(
            integration_id=intg.id,
            label=intg.label or intg.id,
            protocol=intg.protocol,
            from_component=intg.from_component or None,
            to_component=intg.to_component or None,
            wiring_note=(
                "" if has_protocol
                else "ARCH-TODO protocol — confirm with Architect before wiring"
            ),
            ownership="HYBRID" if has_protocol else "DEV-TODO",
        ))
    return tasks


# ── S3 coverage gate ───────────────────────────────────────────────────────────

def check_dev_coverage(
    tasks: list[ImplTask],
    stubs: list[CodeStub],
    wiring: list[WiringTask],
    ctx: DevContext,
) -> list[Stub]:
    """S3 coverage gate — flag missing coverage as DEV-TODO open items."""
    open_items: list[Stub] = []

    # Every story must have ≥1 ImplTask with kb_ids
    for task in tasks:
        if not task.kb_ids:
            open_items.append(Stub(
                id=f"dev-cov-{task.story_id}",
                section="implementation_plan",
                description=(
                    f"DEV-TODO: Story {task.story_id} has no developer-visible KB cards. "
                    "Architect must add CMP-*/API-*/INT-*/SCR-* cards to SRD."
                ),
                marker="DEV-TODO",
                priority="High",
            ))

    # Every WiringTask without protocol → flag
    for wt in wiring:
        if not wt.protocol:
            open_items.append(Stub(
                id=f"dev-wire-{wt.integration_id}",
                section="integration_wiring",
                description=(
                    f"DEV-TODO: Integration {wt.integration_id} ({wt.label}) "
                    "has no protocol. Architect must confirm REST/SOAP/ESB/DB/Event."
                ),
                marker="DEV-TODO",
                priority="Medium",
            ))

    # Every INT-* in SRD not covered by a WiringTask → flag
    wired_ids = {wt.integration_id for wt in wiring}
    for intg in ctx.int_cards:
        if intg.id not in wired_ids and intg.id in ctx.allowed_ids:
            open_items.append(Stub(
                id=f"dev-missing-wire-{intg.id}",
                section="integration_wiring",
                description=(
                    f"DEV-TODO: Integration {intg.id} ({intg.label or ''}) "
                    "is in SRD but has no wiring task. Verify it is in scope."
                ),
                marker="DEV-TODO",
                priority="Low",
            ))

    return open_items

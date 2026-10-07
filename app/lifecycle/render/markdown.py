"""Markdown renderer for DevDocument — enterprise developer handover document.

Produces a 9-section Markdown document that can be handed to any developer or
Claude Code with: "here is the original code and here is the dev assistance document —
go and do the changes."

9 sections:
  1. Change Summary           — requirement + change class + KB KB version + story count
  2. Story-by-Story Workbook  — per story: user story + Gherkin AC + as-is→to-be FR delta
                                + affected components with source loci + code stubs + DoD
  3. Architecture Context     — system components + integrations + sequence diagrams (from SRD)
  4. Full Code Stubs          — all generated stubs in one place (copy-paste ready)
  5. Integration Wiring       — INT-* tasks with protocol + endpoint + from/to component
  6. REGO Policy Stubs        — authorisation policy stubs (one per persona + capability)
  7. Test Expectations        — test cases derived from Gherkin AC + DoD
  8. Gap Log                  — all DEV-TODO items, compliance gaps, SME-required gaps
  9. Handover Checklist       — dev / QA / ops sign-off checklist

Cyclomatic ≤ 21: each section is a separate function.
No LLM calls — purely deterministic rendering from the DevDocument model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.lifecycle.stages.developer.schema import (
    DevDocument,
    DevGap,
    ImplTask,
    WiringTask,
)

# ── Section helpers ─────────────────────────────────────────────────────────────

def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n"


def _extract_file_path(source_locus: str) -> str:
    """Extract the file path from a source_locus string.

    source_locus format: 'input/Auto/CodeBase/UI-NewBusiness/src/app/... §Section char:1234-5678'
    Returns everything before the first ' §' or '§'.
    """
    if not source_locus:
        return ""
    # Strip section + char-span markers
    for sep in (" §", "§", " #", " @"):
        idx = source_locus.find(sep)
        if idx != -1:
            return source_locus[:idx].strip()
    return source_locus.strip()


def _extract_repo_name(file_path: str) -> str:
    """Extract the repository/folder name from a corpus file path.

    Examples:
      input/Auto/CodeBase/UI-NewBusiness/src/app/... → UI-NewBusiness
      input/Auto/CodeBase/Services-NewBusiness/src/... → Services-NewBusiness
      input/Auto/AdobeForms/AU/... → AdobeForms
    Strategy: find 'CodeBase' first (highest priority — it's the repo container);
    if absent, take the folder directly under 'Auto/'.
    """
    if not file_path:
        return "_unknown_"
    parts = file_path.replace("\\", "/").split("/")
    # Priority 1: 'CodeBase' → the very next segment is the repo folder
    for i, part in enumerate(parts):
        if part.lower() in ("codebase", "codecode"):
            if i + 1 < len(parts):
                return parts[i + 1]
    # Priority 2: 'Auto' → next segment is a top-level corpus folder (AdobeForms, Architecture…)
    for i, part in enumerate(parts):
        if part == "Auto" and i + 1 < len(parts):
            return parts[i + 1]
    # Fallback: second-to-last path segment
    return parts[-2] if len(parts) >= 2 else file_path


def _badge(text: str, kind: str = "gap") -> str:
    """Inline marker: `[GAP]` `[DEV-TODO]` etc."""
    return f"`[{text.upper()}]`"


def _code_block(code: str, lang: str = "") -> str:
    fence = "```"
    return f"{fence}{lang}\n{code}\n{fence}\n"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    col = len(headers)
    sep = " | ".join(["---"] * col)
    head = " | ".join(headers)
    lines = [f"| {head} |", f"| {sep} |"]
    for row in rows:
        padded = [str(c) for c in row] + [""] * (col - len(row))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines) + "\n"


def _divider() -> str:
    return "\n---\n\n"


# ── Section 1 — Change Summary ─────────────────────────────────────────────────

def _section_change_summary(doc: DevDocument) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        _h(2, "1. Change Summary"),
        f"| Field | Value |",
        f"| --- | --- |",
        f"| Workspace | `{doc.workspace_id}` |",
        f"| Generated | {ts} |",
        f"| KB Version | `{doc.kb_version}` |",
        f"| Change Class | **{doc.change_class}** |",
        f"| Grounding Score | {doc.grounding_score:.2f} |",
        f"| Template | `{doc.template_id}` `{doc.template_version}` |",
        f"| Stories | {len(doc.implementation_plan)} |",
        f"| Code Stubs | {len(doc.code_stubs)} |",
        f"| Open Gaps | {len(doc.dev_gaps)} |",
        "",
        "**Requirement:**",
        "",
        f"> {doc.requirement or '_(not set)_'}",
        "",
    ]
    if doc.abstained:
        lines.insert(2, "> ⚠️ **ABSTAINED** — insufficient KB evidence for full grounding. "
                        "DEV-TODO stubs mark all ungrounded sections.\n")
    return "\n".join(lines)


# ── Section 2 — Story-by-Story Implementation Workbook ────────────────────────

def _format_story_block(task: ImplTask, doc: DevDocument) -> str:
    """Render one ImplTask as a markdown story block.

    Each block is self-contained: story narrative + Gherkin + FR delta + BR +
    code stubs + DoD.  Claude Code / developer reads this block and implements the
    story without referring to any other section.
    """
    lines: list[str] = []

    # Pre-compute story stubs early so we can use them for the Files-to-Touch table
    story_stubs = [s for s in doc.code_stubs if s.story_id == task.story_id and s.filename]

    # Header — story ID, title, priority, points, ownership, gap status
    priority = task.story_priority or "Medium"
    pts = f"{task.story_points}pts" if task.story_points else "—pts"
    ownership_icon = {"AUTO": "✅ AUTO", "HYBRID": "⚡ HYBRID"}.get(task.ownership, "🔴 DEV-TODO")
    gap_icon = {"direct": "🟢", "derived": "🟡", "partial": "🟡"}.get(task.gap_status, "🔴")
    lines.append(_h(3, f"{task.story_id} — {task.title or task.story_id}"))
    lines.append(
        f"`{priority}` `{pts}` `{doc.change_class or 'Enhancement'}` "
        f"{gap_icon} `{task.gap_status}` {ownership_icon}\n"
    )

    # ── Files to Touch ─────────────────────────────────────────────────────────
    # The primary action table: WHERE to make changes. Each row = one file in the corpus.
    # Source paths come from KB card source_loci extracted during the Architecture stage.
    if story_stubs:
        lines.append("**📁 Files to Touch:**\n")
        seen_files: set[str] = set()
        rows: list[list[str]] = []
        for stub in story_stubs:
            file_path = _extract_file_path(stub.source_locus or "")
            repo_name = _extract_repo_name(file_path) if file_path else "_unknown_"
            key = file_path or stub.filename or ""
            if key in seen_files:
                continue
            seen_files.add(key)
            action = "MODIFY" if file_path else "ADD"
            rows.append([
                f"`{repo_name}`",
                f"`{file_path or stub.filename}`",
                f"`{stub.stack}`",
                action,
                f"`{stub.ownership}`",
            ])
        if rows:
            lines.append(_table(
                ["Repo", "File Path (corpus)", "Stack", "Action", "Ownership"],
                rows,
            ))
        lines.append(
            "_Tip: open the corpus path above in `input/Auto/` to locate the existing source. "
            "For `ibm_esb` / `pega` / `adobe_xdp` — use the IDE (IBM ACE Toolkit / PEGA Studio / AEM) "
            "rather than editing source files directly._\n"
        )
    elif task.kb_ids:
        # KB cards exist but no code stubs — likely Architecture stage not run yet
        lines.append(
            f"> ⚠️ **No file paths available** — run the Architecture stage to generate code stubs "
            f"with concrete source loci. KB cards in scope: "
            + ", ".join(f"`{k}`" for k in task.kb_ids[:5])
            + ".\n"
        )
    else:
        lines.append(
            f"> 🔴 **DEV-TODO: No KB cards in scope for this story.** "
            "Architect must add CMP-*/API-*/INT-* cards to the SRD. "
            "Manually locate the relevant source files in `input/Auto/CodeBase/` before implementing.\n"
        )

    # User story — only render when non-stub content is available
    if task.story_text:
        lines.append("**User Story:**\n")
        lines.append(f"> {task.story_text}\n")
    else:
        lines.append(
            "> ⚠️ **Stories stage not yet run** — user story will appear here after "
            "running the Stories stage.\n"
            "> Developer: refer to the workspace requirement in Section 1 and confirm "
            "the story narrative with the BA before implementing.\n"
        )

    # Gherkin AC — only render when non-stub content is available
    if task.gherkin_ac:
        ac = task.gherkin_ac
        lines.append("**Acceptance Criteria (Gherkin):**\n")
        lines.append(_code_block(
            f"Given {ac.get('given', '[BA-TODO: specify precondition]')}\n"
            f"When  {ac.get('when', '[BA-TODO: specify trigger action]')}\n"
            f"Then  {ac.get('then', '[BA-TODO: specify observable outcome]')}",
            "gherkin"
        ))
    else:
        lines.append(
            f"**Acceptance Criteria:** ⚠️ _Run Stories stage first — "
            f"Gherkin Given/When/Then will appear here._\n"
        )

    # FR Deltas
    if task.fr_deltas:
        lines.append("**Functional Requirement Deltas (as-is → to-be):**\n")
        rows = []
        for delta in task.fr_deltas:
            as_is = delta.as_is or "_not documented_"
            to_be = delta.to_be or f"{_badge('DEV-TODO')}: determine from BA"
            rows.append([f"`{delta.fr_id}`", delta.title, as_is, to_be])
        lines.append(_table(["FR ID", "Title", "Current Behaviour (as-is)", "Required Change (to-be)"], rows))

    # Business rules
    if task.business_rules_summary:
        lines.append("**Applicable Business Rules:**\n")
        for rule in task.business_rules_summary:
            lines.append(f"- {rule}")
        lines.append("")

    # KB card references (secondary — file paths above are the primary action target)
    all_ids = task.kb_ids
    if all_ids:
        chips = " ".join(f"`{kid}`" for kid in all_ids)
        lines.append(f"_KB cards: {chips}_\n")
    if task.integrations_to_wire:
        lines.append(f"**Integrations to wire (see Section 5):** {', '.join(f'`{i}`' for i in task.integrations_to_wire)}\n")

    # Code stubs for this story (story_stubs already computed at top of function)
    if story_stubs:
        lines.append("**Code Stubs:**\n")
        for stub in story_stubs:
            lines.append(f"##### `{stub.filename}`")
            meta = f"KB: `{stub.component_id}` | Stack: `{stub.stack}` | Ownership: `{stub.ownership}`"
            if stub.source_locus:
                meta += f"\nSource: `{stub.source_locus}`"
            lines.append(meta + "\n")
            lines.append(_code_block(stub.code, stub.language))

    # Definition of Done
    if task.definition_of_done:
        lines.append("**Definition of Done:**\n")
        for item in task.definition_of_done:
            lines.append(f"- [ ] {item}")
        lines.append("")
    else:
        lines.append(f"**Definition of Done:** {_badge('DEV-TODO')} — derive from Gherkin AC\n")

    return "\n".join(lines)


def _section_story_workbook(doc: DevDocument) -> str:
    # Check whether any story has real (non-stub) narrative content
    has_real_stories = any(t.story_text for t in doc.implementation_plan)
    has_real_gherkin = any(t.gherkin_ac for t in doc.implementation_plan)
    has_code_stubs   = any(t.story_id for t in doc.code_stubs) if doc.code_stubs else False

    pipeline_note = (
        "> **Pipeline note:** Each story block below requires the following stages to have "
        "run before this Developer stage:\n"
        "> - **Stories stage** → populates user story (As a / I want / So that) + Gherkin AC + DoD\n"
        "> - **FSD stage** → populates FR delta (as-is → to-be) + business rules summary\n"
        "> - **Architecture stage** → populates code stubs (CMP-*/API-* cards)\n"
    )
    if not has_real_stories:
        pipeline_note += (
            ">\n> ⚠️ **Stories stage output not found** — user story and Gherkin fields "
            "are showing as stubs. Run the Stories stage, then re-generate this document.\n"
        )
    if not has_code_stubs:
        pipeline_note += (
            ">\n> ⚠️ **No code stubs generated** — the Architecture stage SRD may have "
            "no CMP-*/API-* cards, or all KB cards fall outside developer-visible kinds "
            "(CMP/API/INT/SCR/FR/SYS). Architect review required.\n"
        )

    lines = [
        _h(2, "2. Story-by-Story Implementation Workbook"),
        "_Self-contained per-story developer handover. Each block contains: "
        "user story · Gherkin AC · FR delta (as-is→to-be) · business rules · "
        "affected components + source loci · code stubs · Definition of Done._\n",
        "_**Handover instruction for Claude Code / developer:** "
        "Read Section 1 (Change Summary) for the full requirement, then implement "
        "each story block below in order. Every field maps directly to a change you must make._\n",
        pipeline_note,
    ]

    if not doc.implementation_plan:
        lines.append(
            f"> {_badge('GAP')}: No implementation tasks found — "
            "SRD story_refs may be empty. Re-run the Architecture stage.\n"
        )
        return "\n".join(lines)

    for task in doc.implementation_plan:
        lines.append(_format_story_block(task, doc))
        lines.append(_divider())

    return "\n".join(lines)


# ── Section 3 — Architecture Context ──────────────────────────────────────────

def _section_architecture_context(doc: DevDocument) -> str:
    srd = doc.implementation_plan[0].dict() if doc.implementation_plan else {}
    lines = [
        _h(2, "3. Architecture Context"),
        "_Extracted from the accepted SRD. Reference only — do not modify without Architect sign-off._\n",
    ]

    # Pull from DevDocument — integration_wiring carries INT-* data
    if doc.integration_wiring:
        lines.append(_h(3, "Integration Points"))
        rows = []
        for task in doc.integration_wiring:
            rows.append([
                f"`{task.integration_id}`",
                task.label,
                task.protocol or "_ARCH-TODO_",
                task.from_component or "_TBD_",
                task.to_component or "_TBD_",
            ])
        lines.append(_table(
            ["INT ID", "Label", "Protocol", "From", "To"],
            rows
        ))

    if doc.code_stubs:
        lines.append(_h(3, "Components in Scope"))
        component_ids = sorted({s.component_id for s in doc.code_stubs if s.component_id})
        for cid in component_ids:
            stub = next((s for s in doc.code_stubs if s.component_id == cid), None)
            if stub:
                locus = f"`{stub.source_locus}`" if stub.source_locus else "_no source locus_"
                lines.append(f"- **`{cid}`** — {stub.stub_type} ({stub.stack}) at {locus}")
        lines.append("")

    return "\n".join(lines)


# ── Section 4 — Full Code Stubs ────────────────────────────────────────────────

def _section_full_code_stubs(doc: DevDocument) -> str:
    lines = [
        _h(2, "4. Full Code Stubs"),
        "_All generated stubs in one place — copy-paste ready for the developer. "
        "DEV-TODO sections must be implemented by the developer before QA handover._\n",
    ]

    if not doc.code_stubs:
        lines.append(f"> {_badge('DEV-TODO')}: No code stubs generated — check SRD component_design and KB card confidence.\n")
        return "\n".join(lines)

    by_story: dict[str, list[Any]] = {}
    for stub in doc.code_stubs:
        by_story.setdefault(stub.story_id or "_no-story_", []).append(stub)

    for story_id, stubs in by_story.items():
        lines.append(_h(3, f"Story: {story_id}"))
        for stub in stubs:
            lines.append(f"#### `{stub.filename or stub.component_id}`")
            meta_parts = [
                f"KB: `{stub.component_id}`",
                f"Stack: `{stub.stack}`",
                f"Lang: `{stub.language}`",
                f"Ownership: `{stub.ownership}`",
            ]
            if stub.source_locus:
                meta_parts.append(f"Source: `{stub.source_locus}`")
            lines.append(" | ".join(meta_parts) + "\n")
            lines.append(_code_block(stub.code, stub.language))

    return "\n".join(lines)


# ── Section 5 — Integration Wiring Checklist ──────────────────────────────────

def _section_integration_wiring(doc: DevDocument) -> str:
    lines = [
        _h(2, "5. Integration Wiring Checklist"),
        "_One row per INT-* from the SRD. Protocol, endpoint, and from/to component "
        "must be confirmed before QA handover._\n",
    ]

    if not doc.integration_wiring:
        lines.append("> No integration tasks — either no INT-* cards in SRD, or all integrations are out-of-scope.\n")
        return "\n".join(lines)

    rows = []
    for w in doc.integration_wiring:
        protocol = w.protocol or f"{_badge('ARCH-TODO')}"
        from_comp = w.from_component or "_TBD_"
        to_comp = w.to_component or "_TBD_"
        note = w.wiring_note or "_—_"
        status = f"`{w.status}`"
        rows.append([f"`{w.integration_id}`", w.label, protocol, from_comp, to_comp, note, status])

    lines.append(_table(
        ["INT ID", "Label", "Protocol", "From", "To", "Wiring Note", "Status"],
        rows
    ))

    lines.append("\n**Wiring verification steps:**\n")
    lines.append("- [ ] Confirm all ARCH-TODO protocols with Architect before implementation")
    lines.append("- [ ] Smoke-test each integration point in the dev environment")
    lines.append("- [ ] ESB integrations: validate via IBM ACE Toolkit (no source file changes)")
    lines.append("- [ ] Update this checklist with actual endpoint URLs before QA handover\n")

    return "\n".join(lines)


# ── Section 6 — REGO Policy Stubs ─────────────────────────────────────────────

def _section_rego_stubs(doc: DevDocument) -> str:
    lines = [
        _h(2, "6. REGO Policy Stubs"),
        "_Authorisation policy stubs — one per persona + capability combination affected "
        "by this change. Apply via Open Policy Agent (OPA) in the serving layer._\n",
    ]

    if not doc.implementation_plan:
        lines.append("> No implementation tasks — REGO stubs skipped.\n")
        return "\n".join(lines)

    # Build one stub per unique screen touched
    stubs_written = set()
    for task in doc.implementation_plan:
        for scr in task.screens_to_update:
            if scr in stubs_written:
                continue
            stubs_written.add(scr)
            lines.append(f"#### Screen: `{scr}`\n")
            rego = (
                f'package japan_auto.authz\n\n'
                f'# DEV-TODO: replace with real persona + capability rules\n'
                f'allow {{\n'
                f'  input.persona in {{"ba", "developer", "qa"}}\n'
                f'  input.screen == "{scr}"\n'
                f'  input.action in {{"read", "write"}}\n'
                f'}}\n'
            )
            lines.append(_code_block(rego, "rego"))
            lines.append(f"> {_badge('DEV-TODO')}: confirm allowed personas from `personas.json` with Architect.\n")

    if not stubs_written:
        lines.append("> No screens in scope — REGO stubs not required for this change.\n")

    return "\n".join(lines)


# ── Section 7 — Test Expectations ─────────────────────────────────────────────

def _section_test_expectations(doc: DevDocument) -> str:
    lines = [
        _h(2, "7. Test Expectations"),
        "_Auto-generated test expectations from Gherkin AC + Definition of Done. "
        "QA engineer: use these as the starting point for the formal test plan._\n",
    ]

    if not doc.implementation_plan:
        lines.append("> No implementation tasks — test expectations not generated.\n")
        return "\n".join(lines)

    for task in doc.implementation_plan:
        if not task.gherkin_ac and not task.definition_of_done:
            continue

        lines.append(_h(3, f"{task.story_id} — {task.title or task.story_id}"))

        if task.gherkin_ac:
            ac = task.gherkin_ac
            lines.append("**Happy-path test case (from Gherkin AC):**\n")
            lines.append(_code_block(
                f"Test: {task.story_id} — happy path\n"
                f"  Arrange: {ac.get('given', '[DEV-TODO]')}\n"
                f"  Act:     {ac.get('when', '[DEV-TODO]')}\n"
                f"  Assert:  {ac.get('then', '[DEV-TODO]')}",
                "plaintext"
            ))

        if task.definition_of_done:
            lines.append("**Completion checks:**\n")
            for item in task.definition_of_done:
                lines.append(f"- [ ] {item}")
            lines.append("")

        # Negative case stub
        if task.gherkin_ac:
            lines.append("**Negative test case (DEV-TODO — QA to specify):**\n")
            lines.append(_code_block(
                f"Test: {task.story_id} — negative / boundary\n"
                f"  Arrange: [DEV-TODO: invalid state or missing input]\n"
                f"  Act:     {task.gherkin_ac.get('when', '[DEV-TODO]')}\n"
                f"  Assert:  [DEV-TODO: error / rejection behaviour]",
                "plaintext"
            ))

    return "\n".join(lines)


# ── Section 8 — Gap Log ────────────────────────────────────────────────────────

def _section_gap_log(doc: DevDocument) -> str:
    lines = [
        _h(2, "8. Gap Log"),
        "_All DEV-TODO items, compliance gaps, and SME-required gaps recorded during generation. "
        "Each gap must be resolved or escalated before QA handover._\n",
    ]

    # Gaps from dev_gaps list
    all_gaps: list[DevGap] = list(doc.dev_gaps)

    # Also collect open_items stubs
    open_stubs = [s for s in (doc.open_items or []) if s.description]

    if not all_gaps and not open_stubs:
        lines.append("> ✅ No gaps recorded. Review DEV-TODO comments in code stubs manually.\n")
        return "\n".join(lines)

    if all_gaps:
        lines.append(_h(3, "Developer Gaps"))
        rows = []
        for gap in all_gaps:
            sme = "⚠️ Yes" if gap.sme_required else "No"
            story = gap.story_id or "_—_"
            rows.append([
                story,
                f"`{gap.component_id}`" if gap.component_id else "_—_",
                gap.description,
                f"`{gap.gap_status}`",
                sme,
                f"`{gap.source}`",
            ])
        lines.append(_table(
            ["Story", "Component", "Description", "Status", "SME Required", "Source"],
            rows
        ))

    if open_stubs:
        lines.append(_h(3, "Open Items (from SRD)"))
        rows = []
        for stub in open_stubs:
            rows.append([stub.description, getattr(stub, "stub_marker", "DEV-TODO")])
        lines.append(_table(["Description", "Marker"], rows))

    lines.append("\n**Gap resolution process:**\n")
    lines.append("1. `compliance_gap` → escalate to Compliance team immediately (cannot defer)")
    lines.append("2. `gap` + `sme_required=true` → raise with BA/Architect in next standup")
    lines.append("3. `partial` → implement best-effort; flag in code comments; QA to test boundary")
    lines.append("4. `deferred` → create backlog ticket with workspace_id reference\n")

    return "\n".join(lines)


# ── Section 9 — Handover Checklist ────────────────────────────────────────────

def _section_handover_checklist(doc: DevDocument) -> str:
    lines = [
        _h(2, "9. Handover Checklist"),
        "_Developer signs off on each item before handing over to QA._\n",
        _h(3, "Development"),
        "- [ ] All user stories implemented (no `TODO` left in Section 2)",
        "- [ ] All code stubs implemented (no `DEV-TODO` in Section 4)",
        "- [ ] All integration wirings confirmed (Section 5 — no ARCH-TODO protocols)",
        "- [ ] REGO policy stubs reviewed and updated with correct personas (Section 6)",
        "- [ ] Every code change has inline KB card ID comment: `// [CMP-xxx]` or `// [FR-xxx]`",
        "- [ ] SQL migrations written and DBA review requested (if Section 8 has DB gaps)",
        "- [ ] Flyway migration version number confirmed (no duplicate versions)",
        "",
        _h(3, "Quality"),
        "- [ ] Unit tests pass (≥ 80% coverage on changed files)",
        "- [ ] Happy-path test cases from Section 7 executed and passing",
        "- [ ] Negative test cases from Section 7 executed and passing",
        "- [ ] No Sonar issues / critical vulnerabilities introduced",
        "- [ ] OWASP checks pass (parameterised SQL, no hardcoded secrets)",
        "- [ ] Cyclomatic complexity ≤ 21 on all new functions",
        "",
        _h(3, "Governance"),
        "- [ ] Gap log (Section 8) — all `compliance_gap` items escalated",
        "- [ ] Gap log — all `sme_required=true` items have SME response recorded",
        "- [ ] KB IDs used in this document are all valid (no dangling refs)",
        "- [ ] Workspace advanced to QA_TESTING (`POST /ws/{id}/dev/accept`)",
        "",
        _h(3, "Sign-off"),
        f"| Role | Name | Date |",
        f"| --- | --- | --- |",
        f"| Developer | _________________________ | __________ |",
        f"| Architect | _________________________ | __________ |",
        f"| BA | _________________________ | __________ |",
        "",
    ]
    return "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────

def render_dev_markdown(doc: DevDocument) -> str:
    """Render a DevDocument as a 9-section enterprise developer handover document.

    Pure deterministic render — no LLM calls, no network.
    Returns a UTF-8 markdown string suitable for:
      - Direct download via GET /ws/{id}/dev/export.md
      - Passing to Claude Code: "here is the dev document — implement the changes"
      - Attaching to a JIRA / Rally story for developer reference

    Sections:
      1. Change Summary
      2. Story-by-Story Implementation Workbook
      3. Architecture Context
      4. Full Code Stubs
      5. Integration Wiring Checklist
      6. REGO Policy Stubs
      7. Test Expectations
      8. Gap Log
      9. Handover Checklist
    """
    sections = [
        _h(1, f"Developer Handover Document — {doc.workspace_id}"),
        f"_Generated by AIG Central Platform · Japan Auto · `{doc.kb_version}`_\n",
        "---\n",
        _section_change_summary(doc),
        _divider(),
        _section_story_workbook(doc),
        _divider(),
        _section_architecture_context(doc),
        _divider(),
        _section_full_code_stubs(doc),
        _divider(),
        _section_integration_wiring(doc),
        _divider(),
        _section_rego_stubs(doc),
        _divider(),
        _section_test_expectations(doc),
        _divider(),
        _section_gap_log(doc),
        _divider(),
        _section_handover_checklist(doc),
    ]

    return "\n".join(sections)

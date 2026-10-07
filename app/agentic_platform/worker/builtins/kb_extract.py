"""WP2: In-process KB extract builtin — no LLM, no plugin.

Implements the G1 Knowledge Base Build stage as a LangGraph StateGraph. It has
two input modes, chosen from what the worktree holds:

  cards mode    inputs/corpus/re-cards/*.json is present. Every module JSON (RED
                JSON or cards JSON) is normalised and merged, screenshots are
                bound from the screens catalogue(s), and the KB is written with
                its page index. This is how the global library is built, and
                how a project that still uploads its own module JSON is built.

  library mode  only inputs/corpus/asp-source/* is present. The uploaded ASP
                file names are matched against the approved library build
                (inputs/library/ref.json), the cards of the matched pages are
                selected, and the project KB is written from that subset.

Pipeline: load_inputs → validate → group_by_page → bind_screens → link_edges
          → write_kb → gate → repair(max 2) → report
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass
class KbExtractOutcome:
    cards: int = 0
    edges: int = 0
    scr_with_screenshot: int = 0
    issues: list[str] = field(default_factory=list)
    gate_passed: bool = False
    gate_findings: list[str] = field(default_factory=list)
    tokens: int = 0  # always 0 — no LLM used
    mode: str = "cards"                # "cards" | "library"
    warnings: list[str] = field(default_factory=list)
    unmatched_files: list[str] = field(default_factory=list)
    library_version: int | None = None


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------

class KbState(TypedDict, total=False):
    worktree: str              # path to the stage worktree
    app_id: str
    mode: str                  # "cards" | "library"
    cards_data: dict           # merged (cards mode) or selected (library mode) cards doc
    cards_list: list[dict]     # flat card list from cards_schema.iter_all_cards
    catalog: dict              # merged screens catalogue (cards mode; may be empty)
    screen_bindings: dict      # scr_id → [{local_path, ...}]
    write_result: Any          # KbWriteResult
    check_result: Any          # CheckResult
    repair_attempts: int
    error: str
    warnings: list[str]
    page_index: dict           # written in cards mode
    resolution: dict           # library mode: {matched, unmatched, ignored}
    library: dict              # library mode: {artifact_id, version, checksum}
    pruned_edges: list[dict]
    pruned_refs: list[dict]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _join(items: list[str], limit: int = 5) -> str:
    text = "; ".join(items[:limit])
    if len(items) > limit:
        text += f" … ({len(items) - limit} more)"
    return text


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

def _load_inputs(state: KbState) -> KbState:
    worktree = Path(state["worktree"])
    corpus = worktree / "inputs" / "corpus"
    cards_dir = corpus / "re-cards"
    asp_dir = corpus / "asp-source"

    json_files = sorted(cards_dir.glob("*.json")) if cards_dir.is_dir() else []
    asp_files = sorted(p.name for p in asp_dir.iterdir() if p.is_file()) if asp_dir.is_dir() else []

    # A project's own module JSON wins over the library.
    if json_files:
        return _load_cards_mode(state, corpus, json_files)
    if asp_files:
        return _load_library_mode(state, worktree, asp_files)
    return {**state, "error": (
        "kb_input_missing: no module JSON under inputs/corpus/re-cards/ and no ASP files "
        "under inputs/corpus/asp-source/")}


def _load_cards_mode(state: KbState, corpus: Path, json_files: list[Path]) -> KbState:
    from app.agentic_platform.fe_core.kb.cards_schema import merge_modules, normalise  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.screens import merge_catalogs  # noqa: PLC0415

    docs: list[dict] = []
    problems: list[str] = []
    warnings: list[str] = []
    for path in json_files:
        try:
            data = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path.name}: not valid JSON ({exc})")
            continue
        doc, violations, notes = normalise(data)
        if violations:
            problems.extend(f"{path.name}: {v}" for v in violations)
            continue
        docs.append(doc)
        warnings.extend(n for n in notes if n not in warnings)
    if problems:
        return {**state, "error": f"cards validation failed: {_join(problems)}"}

    merged, violations, notes = merge_modules(docs)
    if violations:
        return {**state, "error": f"cards validation failed: {_join(violations)}"}
    warnings.extend(n for n in notes if n not in warnings)

    # Screens catalogue(s) are optional; every version is merged, newest wins per page.
    catalogs: list[dict] = []
    screens_dir = corpus / "screens"
    if screens_dir.is_dir():
        for path in screens_dir.glob("catalog-v*.json"):
            try:
                catalogs.append(_read_json(path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not parse screens catalogue %s: %s", path.name, exc)
    catalog = merge_catalogs(catalogs)

    logger.info("kb_extract: cards mode, %d module file(s), %d pages, screens versions %s",
                len(json_files), len(merged.get("pages", [])), catalog.get("versions", []))
    return {**state, "mode": "cards", "cards_data": merged, "catalog": catalog,
            "warnings": warnings, "error": ""}


def _load_library_mode(state: KbState, worktree: Path, asp_files: list[str]) -> KbState:
    from app.agentic_platform.fe_core.kb.library import (  # noqa: PLC0415
        bindings_from_index, load_library, locate_library_kb, resolve_files, select_subset,
    )

    ref_path = worktree / "inputs" / "library" / "ref.json"
    if not ref_path.is_file():
        return {**state, "error": (
            "kb_input_missing: no_library: ASP files were uploaded but no approved build of "
            "the global library is available. Build the library and approve it first.")}
    try:
        ref = _read_json(ref_path)
    except Exception as exc:  # noqa: BLE001
        return {**state, "error": f"kb_input_missing: no_library: unreadable library reference ({exc})"}
    kb = locate_library_kb(ref.get("path"))
    if kb is None:
        return {**state, "error": (
            f"kb_input_missing: no_library: the approved library build at "
            f"'{ref.get('path')}' could not be read")}

    page_index, merged = load_library(kb)
    resolution = resolve_files(asp_files, page_index)
    if not resolution["matched"]:
        return {**state, "error": (
            f"kb_input_missing: no_match: none of the {len(asp_files)} uploaded file(s) has cards "
            f"in the library (library version {ref.get('version')})")}

    norms = [m["page_norm"] for m in resolution["matched"]]
    subset, pruned_edges, pruned_refs = select_subset(merged, norms)
    bindings = bindings_from_index(page_index, norms, kb)

    logger.info("kb_extract: library mode v%s, %d matched, %d unmatched, %d ignored",
                ref.get("version"), len(resolution["matched"]),
                len(resolution["unmatched"]), len(resolution["ignored"]))
    return {
        **state, "mode": "library", "cards_data": subset, "catalog": {},
        "screen_bindings": bindings, "resolution": resolution,
        "library": {k: ref.get(k) for k in ("artifact_id", "version", "checksum")},
        "pruned_edges": pruned_edges, "pruned_refs": pruned_refs,
        "warnings": [f"'{name}' has no cards in the library" for name in resolution["unmatched"]],
        "error": "",
    }


def _validate(state: KbState) -> KbState:
    if state.get("error"):
        return state
    from app.agentic_platform.fe_core.kb.cards_schema import validate  # noqa: PLC0415
    violations = validate(state["cards_data"])
    if violations:
        return {**state, "error": f"cards validation failed: {_join(violations)}"}
    return state


def _group_by_page(state: KbState) -> KbState:
    if state.get("error"):
        return state
    from app.agentic_platform.fe_core.kb.cards_schema import iter_all_cards  # noqa: PLC0415
    cards_list = iter_all_cards(state["cards_data"])
    logger.info("kb_extract: %d cards expanded", len(cards_list))
    return {**state, "cards_list": cards_list}


def _resolve_image(screens_root: Path, binding: dict) -> str:
    """Where a catalogue image is in this worktree.

    The catalogue records the path the API host extracted to; the worker reads
    the copy under inputs/corpus/screens/v<N>/ instead.
    """
    name = str(binding.get("file") or Path(str(binding.get("local_path") or "")).name)
    if name:
        version = binding.get("version")
        if version:
            candidate = screens_root / f"v{version}" / name
            if candidate.is_file():
                return str(candidate)
        for candidate in screens_root.glob(f"v*/{name}"):
            if candidate.is_file():
                return str(candidate)
    local = str(binding.get("local_path") or "")
    return local if local and Path(local).is_file() else ""


def _bind_screens(state: KbState) -> KbState:
    if state.get("error"):
        return state
    if state.get("mode") == "library":
        # Pictures were bound once, when the library was built.
        return state
    from app.agentic_platform.fe_core.kb.screens import lookup_by_page, bind  # noqa: PLC0415

    catalog = state.get("catalog", {})
    cards_list = state.get("cards_list", [])
    screens_root = Path(state["worktree"]) / "inputs" / "corpus" / "screens"
    screen_bindings: dict[str, list[dict]] = {}

    # Group SCR cards by page
    scr_by_page: dict[str, list[dict]] = {}
    for card in cards_list:
        if card.get("kind") == "SCR":
            page = card.get("page", "")
            scr_by_page.setdefault(page, []).append(card)

    for page, scr_cards in scr_by_page.items():
        entry = lookup_by_page(catalog, page)
        bindings = bind(scr_cards, entry)
        for b in bindings:
            scr_id = b.get("scr_id", "")
            if scr_id:
                b["local_path"] = _resolve_image(screens_root, b)
                screen_bindings.setdefault(scr_id, []).append(b)

    logger.info("kb_extract: %d SCR cards bound to screenshots", len(screen_bindings))
    return {**state, "screen_bindings": screen_bindings}


def _link_edges(state: KbState) -> KbState:
    """Edge list is already in cards_data.edges — nothing to derive."""
    return state


def _write_kb(state: KbState) -> KbState:
    if state.get("error"):
        return state
    from app.agentic_platform.fe_core.kb.writer import write_kb  # noqa: PLC0415

    worktree = Path(state["worktree"])
    kb_dir = worktree / "kb"
    cards_list = state.get("cards_list", [])
    cards_data = state.get("cards_data", {})
    edges = cards_data.get("edges", [])
    screen_bindings = state.get("screen_bindings", {})
    catalog = state.get("catalog", {})
    mode = state.get("mode", "cards")

    provenance: dict[str, Any] = {
        "mode": mode,
        "app": cards_data.get("app", ""),
        "module": cards_data.get("module", ""),
        "module_key": cards_data.get("module_key", ""),
        "modules": cards_data.get("modules", []),
        "generated_at": cards_data.get("generated_at", ""),
        "source": cards_data.get("source", {}),
        "catalog_version": catalog.get("version", None),
        "catalog_versions": catalog.get("versions", []),
    }
    if mode == "library":
        resolution = state.get("resolution", {})
        provenance.update({
            "library": state.get("library", {}),
            "selected_pages": [m["page"] for m in resolution.get("matched", [])],
            "unmatched_files": resolution.get("unmatched", []),
            "ignored_files": resolution.get("ignored", []),
            "pruned_edges": state.get("pruned_edges", []),
            "pruned_refs": state.get("pruned_refs", []),
        })

    result = write_kb(
        kb_dir=kb_dir,
        cards=cards_list,
        edges=edges,
        screen_bindings=screen_bindings,
        provenance=provenance,
    )

    page_index = state.get("page_index", {})
    if mode == "cards":
        # The page index is what a project's ASP files are later matched against.
        from app.agentic_platform.fe_core.kb.library import build_page_index, write_library_files  # noqa: PLC0415
        page_index = build_page_index(cards_data, result.screenshots, catalog)
        write_library_files(kb_dir, cards_data, page_index)

    logger.info(
        "kb_extract: wrote %d cards, %d edges, %d evidence lines, %d SCR w/screenshot",
        result.cards_written, result.edges_written, result.evidence_lines, result.scr_with_screenshot,
    )
    return {**state, "write_result": result, "page_index": page_index}


def _gate(state: KbState) -> KbState:
    if state.get("error"):
        return state
    from app.agentic_platform.fe_core.kb.check import check  # noqa: PLC0415

    worktree = Path(state["worktree"])
    kb_dir = worktree / "kb"
    result = check(kb_dir)
    logger.info("kb_extract gate: %s (%d findings)", "PASS" if result.passed else "FAIL",
                len(result.findings))
    return {**state, "check_result": result}


def _should_repair(state: KbState) -> str:
    check_result = state.get("check_result")
    repair_attempts = state.get("repair_attempts", 0)
    if check_result and not check_result.passed and repair_attempts < 2:
        return "repair"
    return "report"


def _repair(state: KbState) -> KbState:
    """Best-effort repair: re-run write_kb after logging findings."""
    attempts = state.get("repair_attempts", 0) + 1
    findings = (state.get("check_result") or object()).__dict__.get("findings", [])
    logger.warning("kb_extract repair attempt %d: %d findings", attempts, len(findings))
    state = {**state, "repair_attempts": attempts}
    state = _write_kb(state)
    state = _gate(state)
    return state


def _report_sections(state: KbState) -> dict[str, list[str]]:
    """Bullet lists for the build report: what a reviewer has to look at."""
    write_result = state.get("write_result")
    page_of = {c.get("id", ""): c.get("page", "") for c in state.get("cards_list", [])}
    sections: dict[str, list[str]] = {}

    if state.get("mode") == "library":
        library = state.get("library", {})
        resolution = state.get("resolution", {})
        sections["Built from the global library"] = [
            f"library version {library.get('version')} (artefact {library.get('artifact_id')})"]
        sections["Selected pages"] = [m["page"] for m in resolution.get("matched", [])]
        sections["Unmatched ASP files (no cards in the library)"] = list(resolution.get("unmatched", []))
        sections["Ignored files (not pages in the library)"] = list(resolution.get("ignored", []))
        sections["Dropped links (other end is on a page that was not uploaded)"] = [
            f"{e.get('from')} {e.get('label')} {e.get('to')}" for e in state.get("pruned_edges", [])]
        sections["Dropped references"] = [
            f"{r.get('card')}.{r.get('field')}: {r.get('ref')}" for r in state.get("pruned_refs", [])]
    else:
        catalog = state.get("catalog", {})
        page_index = state.get("page_index", {})
        sections["Input warnings"] = list(state.get("warnings", []))
        sections["Pictures on pages without a screen card"] = list(
            page_index.get("pictures_without_screen_card", []))
        sections["Pictures for pages that have no cards"] = list(
            page_index.get("catalog_pages_without_cards", []))
        sections["Screens document notes"] = list(catalog.get("report", []))

    if write_result is not None:
        sections["Screen cards without a picture"] = [
            f"{scr_id} ({page_of.get(scr_id, '')})" for scr_id in write_result.scr_without_screenshot]
    return sections


def _report(state: KbState) -> KbState:
    """Final node: write kb/_build-report.md. No state change."""
    write_result = state.get("write_result")
    if state.get("error") or write_result is None:
        return state
    from app.agentic_platform.fe_core.kb.writer import write_build_report  # noqa: PLC0415

    write_build_report(
        Path(state["worktree"]) / "kb", write_result,
        check_result=state.get("check_result"),
        catalog=state.get("catalog"),
        cards_data=state.get("cards_data"),
        sections=_report_sections(state),
    )
    return state


# ---------------------------------------------------------------------------
# Build and run the graph
# ---------------------------------------------------------------------------

def _build_graph():
    from langgraph.graph import StateGraph, END  # noqa: PLC0415

    g = StateGraph(KbState)
    g.add_node("load_inputs", _load_inputs)
    g.add_node("validate", _validate)
    g.add_node("group_by_page", _group_by_page)
    g.add_node("bind_screens", _bind_screens)
    g.add_node("link_edges", _link_edges)
    g.add_node("write_kb", _write_kb)
    g.add_node("gate", _gate)
    g.add_node("repair", _repair)
    g.add_node("report", _report)

    g.set_entry_point("load_inputs")
    g.add_edge("load_inputs", "validate")
    g.add_edge("validate", "group_by_page")
    g.add_edge("group_by_page", "bind_screens")
    g.add_edge("bind_screens", "link_edges")
    g.add_edge("link_edges", "write_kb")
    g.add_edge("write_kb", "gate")
    g.add_conditional_edges("gate", _should_repair, {"repair": "repair", "report": "report"})
    g.add_edge("repair", "report")
    g.add_edge("report", END)

    return g.compile()


def run_kb_extract(worktree: str, app_id: str) -> KbExtractOutcome:
    """Entry point called by stage_executor._run_kb_extract().

    Raises RuntimeError when there is nothing to build from (kb_input_missing:
    no module JSON, no approved library, or no uploaded ASP file matches).
    Returns KbExtractOutcome; gate_passed=False means the run should be FAILED.
    """
    graph = _build_graph()
    initial: KbState = {
        "worktree": worktree,
        "app_id": app_id,
        "mode": "cards",
        "cards_data": {},
        "cards_list": [],
        "catalog": {},
        "screen_bindings": {},
        "repair_attempts": 0,
        "error": "",
        "warnings": [],
    }

    final: KbState = graph.invoke(initial)

    error = final.get("error", "")
    if error and error.startswith("kb_input_missing"):
        raise RuntimeError(error)

    write_result = final.get("write_result")
    check_result = final.get("check_result")
    resolution = final.get("resolution", {})

    return KbExtractOutcome(
        cards=write_result.cards_written if write_result else 0,
        edges=write_result.edges_written if write_result else 0,
        scr_with_screenshot=write_result.scr_with_screenshot if write_result else 0,
        issues=check_result.findings if check_result else ([error] if error else []),
        gate_passed=check_result.passed if check_result else False,
        gate_findings=check_result.findings if check_result else ([error] if error else []),
        tokens=0,
        mode=final.get("mode", "cards"),
        warnings=list(final.get("warnings", [])),
        unmatched_files=list(resolution.get("unmatched", [])),
        library_version=(final.get("library") or {}).get("version"),
    )

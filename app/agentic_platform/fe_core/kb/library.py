"""The global card library and how a project draws from it.

The library is one shared knowledge base, held as a reserved project
(LIBRARY_APP_ID) that runs the one-stage LIBRARY_PIPELINE. Its inputs are
uploaded once: the module JSON(s) and the screens Word document. Its build
writes two extra files next to the usual KB tree:

    page-index.json            ASP file -> card ids, shared entities, screenshots
    _source/cards-merged.json  every module, merged and validated (cards model)

A project uploads only ASP files. Its G1 run matches the file names against the
page index, selects the cards of the matched pages plus the shared entities
they use (select_subset), and writes its own KB from that subset.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agentic_platform.fe_core.kb.cards_schema import KIND_ORDER, normalise_page

LIBRARY_APP_ID = "_library"
LIBRARY_PIPELINE = "kb-library"
LIBRARY_STAGE = "kb"

PAGE_INDEX_FILE = "page-index.json"
MERGED_FILE = "_source/cards-merged.json"

ASP_SOURCE_KIND = "asp-source"
ASP_EXTENSIONS = (".asp", ".asa", ".inc")

# Card fields that hold ids of other cards.
REF_FIELDS = ("source_br", "implemented_by", "db_objects_used")


def is_library(application_id: str | None) -> bool:
    return (application_id or "").strip() == LIBRARY_APP_ID


# ---------------------------------------------------------------------------
# Page index (written by the library build)
# ---------------------------------------------------------------------------

def build_page_index(
    merged: dict[str, Any],
    screenshots: dict[str, list[dict]] | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map every ASP page of the merged doc to its cards and pictures.

    `screenshots` is KbWriteResult.screenshots: SCR id -> [{"file", "sha256"}],
    with `file` relative to the KB root.
    """
    screenshots = screenshots or {}
    pages: dict[str, dict] = {}
    for page in merged.get("pages", []):
        name = page.get("page", "")
        norm = normalise_page(name)
        if not norm:
            continue
        card_ids: list[str] = []
        shared_refs: list[str] = []
        scr_ids: list[str] = []
        counts: dict[str, int] = {}
        for kind in KIND_ORDER:
            for card in page.get("cards", {}).get(kind, []):
                cid = card.get("id")
                if cid:
                    card_ids.append(cid)
                    counts[kind] = counts.get(kind, 0) + 1
                    if kind == "SCR":
                        scr_ids.append(cid)
                elif card.get("ref") and card["ref"] not in shared_refs:
                    shared_refs.append(card["ref"])
        if shared_refs:
            counts["ENT"] = counts.get("ENT", 0) + len(shared_refs)
        pages[norm] = {
            "page": name,
            "source_path": page.get("source_path", ""),
            "module_key": page.get("module_key") or merged.get("module_key", ""),
            "layer": page.get("layer", ""),
            "card_ids": card_ids,
            "shared_refs": shared_refs,
            "scr_ids": scr_ids,
            "screenshots": [{"scr_id": scr, **shot} for scr in scr_ids for shot in screenshots.get(scr, [])],
            "counts": counts,
        }

    pictures_without_screen_card: list[str] = []
    catalog_pages_without_cards: list[str] = []
    for sc in (catalog or {}).get("screens", []):
        if not sc.get("images"):
            continue
        norm = normalise_page(sc.get("page_norm") or sc.get("page", ""))
        if norm not in pages:
            catalog_pages_without_cards.append(norm)
        elif not pages[norm]["scr_ids"]:
            pictures_without_screen_card.append(norm)

    modules = merged.get("modules") or [{
        "module_key": merged.get("module_key", ""), "module": merged.get("module", ""),
        "app": merged.get("app", ""),
    }]
    return {
        "schema": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "modules": modules,
        "catalog_versions": (catalog or {}).get("versions") or (
            [catalog["version"]] if (catalog or {}).get("version") else []),
        "pages": pages,
        "pages_without_screenshot": [n for n, p in pages.items() if p["scr_ids"] and not p["screenshots"]],
        "pictures_without_screen_card": pictures_without_screen_card,
        "catalog_pages_without_cards": catalog_pages_without_cards,
        "unbound_pictures": len((catalog or {}).get("unbound", [])),
    }


def write_library_files(kb_dir: Path, merged: dict[str, Any], page_index: dict[str, Any]) -> None:
    """Write page-index.json and _source/cards-merged.json into the KB tree."""
    kb_dir = Path(kb_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / PAGE_INDEX_FILE).write_text(json.dumps(page_index, indent=2), encoding="utf-8")
    merged_path = kb_dir / MERGED_FILE
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    merged_path.write_text(json.dumps(merged, indent=1), encoding="utf-8")


def locate_library_kb(path: Path | str | None) -> Path | None:
    """The KB root under `path`: the folder that holds page-index.json.

    A materialised `kb` artefact is the KB tree itself; a worktree still on the
    host has it under kb/.
    """
    if not path:
        return None
    root = Path(path)
    for candidate in (root, root / "kb"):
        if (candidate / PAGE_INDEX_FILE).is_file() and (candidate / MERGED_FILE).is_file():
            return candidate
    return None


def load_library(kb_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (page_index, merged_doc) from a library KB root."""
    kb_dir = Path(kb_dir)
    page_index = json.loads((kb_dir / PAGE_INDEX_FILE).read_text(encoding="utf-8"))
    merged = json.loads((kb_dir / MERGED_FILE).read_text(encoding="utf-8"))
    return page_index, merged


# ---------------------------------------------------------------------------
# Matching a project's ASP files
# ---------------------------------------------------------------------------

def resolve_files(filenames: list[str], page_index: dict[str, Any]) -> dict[str, list]:
    """Sort uploaded file names into matched / unmatched / ignored.

    matched    the library has cards for this page (case and folder ignored)
    unmatched  an .asp page the library has no cards for (a warning)
    ignored    an include or other file that is not a page in the library
    """
    pages = page_index.get("pages", {})
    matched: list[dict] = []
    unmatched: list[str] = []
    ignored: list[str] = []
    seen: set[str] = set()
    for name in filenames:
        norm = normalise_page(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        entry = pages.get(norm)
        display = str(name).replace("\\", "/").rsplit("/", 1)[-1]
        if entry is not None:
            matched.append({
                "file": display,
                "page": entry.get("page", display),
                "page_norm": norm,
                "module_key": entry.get("module_key", ""),
                "cards": len(entry.get("card_ids", [])),
                "shared_entities": len(entry.get("shared_refs", [])),
                "scr_ids": list(entry.get("scr_ids", [])),
                "has_screenshot": bool(entry.get("screenshots")),
            })
        elif norm.endswith(".asp"):
            unmatched.append(display)
        else:
            ignored.append(display)
    return {"matched": matched, "unmatched": unmatched, "ignored": ignored}


def bindings_from_index(
    page_index: dict[str, Any], page_norms: list[str], kb_dir: Path | str,
) -> dict[str, list[dict]]:
    """Screen bindings for the selected pages, pointing at the library's pictures."""
    kb_dir = Path(kb_dir)
    out: dict[str, list[dict]] = {}
    for norm in page_norms:
        entry = page_index.get("pages", {}).get(normalise_page(norm)) or {}
        for shot in entry.get("screenshots", []):
            scr_id = shot.get("scr_id", "")
            path = kb_dir / str(shot.get("file", ""))
            if not scr_id:
                continue
            bound = out.setdefault(scr_id, [])
            bound.append({
                "scr_id": scr_id,
                "local_path": str(path) if path.is_file() else "",
                "file": path.name, "s3_key": "", "sha256": shot.get("sha256", ""),
                "order": len(bound) + 1, "match": "library",
            })
    return out


# ---------------------------------------------------------------------------
# Selecting a project's cards
# ---------------------------------------------------------------------------

def select_subset(
    merged: dict[str, Any], page_norms: list[str],
) -> tuple[dict[str, Any], list[dict], list[dict]]:
    """Cut the merged library doc down to the given pages.

    Kept: the cards of the selected pages, and every shared card they point at
    (a page-level ref, a reference field, or an edge). A reference or edge to a
    card on a page that was not selected is dropped and returned, never followed:
    pages are not added on the project's behalf. The result is a valid cards doc.

    Returns (subset_doc, pruned_edges, pruned_refs).
    """
    wanted = {normalise_page(p) for p in page_norms}
    pages = [copy.deepcopy(p) for p in merged.get("pages", [])
             if normalise_page(p.get("page", "")) in wanted]

    shared_all: dict[str, tuple[str, dict]] = {}
    for kind in KIND_ORDER:
        for card in merged.get("shared_cards", {}).get(kind, []):
            if card.get("id"):
                shared_all[card["id"]] = (kind, card)

    kept_ids: set[str] = set()
    shared_kept: list[str] = []

    def _keep_shared(cid: str) -> None:
        if cid in shared_all and cid not in shared_kept:
            shared_kept.append(cid)

    for page in pages:
        for kind in KIND_ORDER:
            for card in page.get("cards", {}).get(kind, []):
                if card.get("id"):
                    kept_ids.add(card["id"])
                elif card.get("ref"):
                    _keep_shared(card["ref"])

    pruned_refs: list[dict] = []
    for page in pages:
        for kind in KIND_ORDER:
            for card in page.get("cards", {}).get(kind, []):
                if not card.get("id"):
                    continue
                for field_ in REF_FIELDS:
                    refs = card.get(field_)
                    if not isinstance(refs, list):
                        continue
                    kept: list[str] = []
                    for ref in refs:
                        if ref in kept_ids:
                            kept.append(ref)
                        elif ref in shared_all:
                            _keep_shared(ref)
                            kept.append(ref)
                        else:
                            pruned_refs.append({"card": card["id"], "field": field_, "ref": ref})
                    card[field_] = kept

    all_edges = merged.get("edges", [])
    for edge in all_edges:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in kept_ids and dst in shared_all:
            _keep_shared(dst)
        if dst in kept_ids and src in shared_all:
            _keep_shared(src)

    present = kept_ids | set(shared_kept)
    edges: list[dict] = []
    pruned_edges: list[dict] = []
    for edge in all_edges:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in present and dst in present:
            edges.append(edge)
        elif src in kept_ids or dst in kept_ids:
            pruned_edges.append(edge)

    shared_cards: dict[str, list[dict]] = {}
    for kind in KIND_ORDER:
        cards = [copy.deepcopy(shared_all[cid][1]) for cid in shared_kept if shared_all[cid][0] == kind]
        if cards:
            shared_cards[kind] = cards

    for section, page in enumerate(pages, start=1):
        page["section"] = section

    counts: dict[str, int] = {}
    for page in pages:
        for kind in KIND_ORDER:
            n = sum(1 for c in page.get("cards", {}).get(kind, []) if c.get("id"))
            if n:
                counts[kind] = counts.get(kind, 0) + n
    for kind, cards in shared_cards.items():
        counts[kind] = counts.get(kind, 0) + len(cards)

    subset: dict[str, Any] = {
        key: merged[key]
        for key in ("app", "module", "module_key", "modules", "edge_labels", "source",
                    "source_format", "generated_at")
        if key in merged
    }
    subset.update({
        "pages": pages,
        "shared_cards": shared_cards,
        "edges": edges,
        "issues": [i for i in merged.get("issues", [])
                   if normalise_page(i.get("page", "")) in wanted or i.get("id") in present],
        "summary": {"pages": len(pages), "cards": counts,
                    "cards_total": sum(counts.values()), "edges": len(edges)},
    })
    return subset, pruned_edges, pruned_refs


# ---------------------------------------------------------------------------
# The approved library build
# ---------------------------------------------------------------------------

def library_workspace_id() -> str:
    from app.agentic_platform.fe_core.workspaces.models import global_workspace_id  # noqa: PLC0415
    return global_workspace_id(LIBRARY_APP_ID)


def approved_library_artifact(store) -> Any | None:
    """Latest APPROVED `kb` artefact of the library, or None. Projects read only this."""
    artifacts = store.approved_artifacts(
        LIBRARY_PIPELINE, [LIBRARY_STAGE], workspace_ids=[library_workspace_id()])
    return max(artifacts, key=lambda a: a.version) if artifacts else None


def materialise_library(store, settings) -> dict[str, Any] | None:
    """Bring the approved library KB to local disk and describe it.

    Returns {"path", "artifact_id", "version", "checksum"} or None when no build
    has been approved (or its files cannot be read). The copy is cached per
    artefact id, which is immutable.
    """
    artifact = approved_library_artifact(store)
    if artifact is None:
        return None
    dest = Path(settings.fe_workspace_root) / "_cache" / "library-kb" / str(artifact.id)
    kb = locate_library_kb(dest)
    if kb is None:
        from app.agentic_platform.fe_core.artifacts.reader import materialise  # noqa: PLC0415
        root = materialise(artifact, dest=dest, settings=settings)
        kb = locate_library_kb(root)
    if kb is None:
        return None
    return {
        "path": str(kb),
        "artifact_id": artifact.id,
        "version": artifact.version,
        "checksum": getattr(artifact, "checksum", "") or "",
    }

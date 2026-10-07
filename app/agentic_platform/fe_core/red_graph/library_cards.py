"""The Global Library's cards for an ASP page (BR / FR / SCR / CMP / API / ENT / WF).

Where the cards come from, in order of preference:

1. the **database**: the card build published to ``fe_kb_cards`` / ``fe_kb_nodes`` / ``fe_kb_edges``
   (``_library-re-v<N>``; the ACTIVE one, else the newest). This is what every deployment has, so
   the client reads its approved cards from the same tables the graph comes from;
2. the **approved** library build files (page-index.json + cards-merged.json), materialised from the
   artifact store (S3 at the client, local folder here);
3. the **latest** built library on local disk, approved or not;
4. the **uploaded module JSONs** themselves (corpus/re-cards), merged on the fly with the same
   deterministic functions the build uses. No agent run is needed for this, so the cards of a page
   can be shown as soon as its module JSON has been uploaded.

A page's cards are listed per kind with id, label and summary; ENT pointers to shared entity cards
are resolved. The page's library module (module_key / module name), logical sub-module
(``business_domain.module`` from the RED JSON) and domain come along, so the UI can show them next
to the folder-level module of the dependency graph.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from app.agentic_platform.fe_core.kb import library
from app.agentic_platform.fe_core.kb.cards_schema import KIND_ORDER, merge_modules, normalise, normalise_page

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[Any, dict]] = {}


def _corpus_docs(settings) -> list[tuple[Path, dict]]:
    """(file, normalised cards doc) for every readable module JSON the library has uploaded."""
    corpus_dir = settings.corpus_root_for(library.LIBRARY_APP_ID) / "re-cards"
    out: list[tuple[Path, dict]] = []
    if not corpus_dir.is_dir():
        return out
    for path in sorted(corpus_dir.glob("*.json")):
        try:
            doc, violations, _notes = normalise(json.loads(path.read_text(encoding="utf-8-sig")))
        except Exception:  # noqa: BLE001 - an unreadable upload is reported by the build, not here
            continue
        if not violations:
            out.append((path, doc))
    return out


CARDS_PREFIX = f"{library.LIBRARY_APP_ID}-re-v"


def _db_page_data(dao) -> dict[str, Any] | None:
    """The card build in the KB tables, shaped like a merged cards doc + page index.

    ``fe_kb_cards`` rows carry the card kind, label, text and ``source_locus`` = the page the card was
    extracted from (pg_sink). Shared entity cards have no page; a page's shared entities are the
    ENT cards its own cards point at through ``fe_kb_edges``.
    """
    from app.agentic_platform.fe_core.red_graph.kb_graph import CARDS_VERSION_RE  # noqa: PLC0415

    kb_version = dao.active_version(CARDS_PREFIX) or dao.latest_version(CARDS_PREFIX, CARDS_VERSION_RE)
    if not kb_version:
        return None
    sig = f"db:{kb_version}"
    if sig in _cache:
        return _cache[sig][1]
    cards = dao._rows(f"SELECT id, kind, label, category, source_locus, text_en, metadata FROM {dao._t('fe_kb_cards')} "
                      f"WHERE kb_version = %s ORDER BY id", (kb_version,))
    edges = dao._rows(f"SELECT from_id, to_id FROM {dao._t('fe_kb_edges')} WHERE kb_version = %s", (kb_version,))
    by_id = {c["id"]: c for c in cards}
    pages: dict[str, dict] = {}
    for c in cards:
        kind = str(c.get("kind") or "")
        page = str(c.get("source_locus") or "")
        norm = normalise_page(page) if page.lower().endswith((".asp", ".asa", ".inc")) else ""
        if not norm:
            continue
        p = pages.setdefault(norm, {"page": page, "cards": {}, "business_domain": {}, "module_key": ""})
        meta = c.get("metadata") or {}
        p["cards"].setdefault(kind, []).append({"id": c["id"], "label": c.get("label") or c["id"], "summary": (c.get("text_en") or "")[:400],
                                                "confidence": meta.get("confidence"), "business_domain": meta.get("business_domain")})
        if not p["business_domain"].get("domain") and (meta.get("business_domain") or c.get("category")):
            p["business_domain"] = {"domain": meta.get("business_domain") or c.get("category"), "module": meta.get("module")}
        if not p["module_key"] and meta.get("module_key"):
            p["module_key"] = meta["module_key"]
    # shared entities: ENT cards without a page, attached to the pages whose cards reference them
    page_of_card = {cd["id"]: norm for norm, p in pages.items() for kind in p["cards"].values() for cd in kind}
    shared_cards: dict[str, list[dict]] = {}
    shared_refs: dict[str, list[str]] = {}
    for c in cards:
        if str(c.get("kind") or "") == "ENT" and c["id"] not in page_of_card:
            shared_cards.setdefault("ENT", []).append({"id": c["id"], "label": c.get("label") or c["id"], "summary": (c.get("text_en") or "")[:400],
                                                       "confidence": (c.get("metadata") or {}).get("confidence")})
    shared_ids = {s["id"] for s in shared_cards.get("ENT", [])}
    for e in edges:
        src, dst = e["from_id"], e["to_id"]
        if dst in shared_ids and src in page_of_card:
            shared_refs.setdefault(page_of_card[src], [])
            if dst not in shared_refs[page_of_card[src]]:
                shared_refs[page_of_card[src]].append(dst)
    merged = {"pages": list(pages.values()), "shared_cards": shared_cards, "modules": [], "module_key": ""}
    page_index = {"pages": {norm: {"page": p["page"], "module_key": p["module_key"], "card_ids": [cd["id"] for k in p["cards"].values() for cd in k],
                                   "shared_refs": shared_refs.get(norm, []), "scr_ids": [cd["id"] for cd in p["cards"].get("SCR", [])], "screenshots": []}
                            for norm, p in pages.items()}}
    data = {"page_index": page_index, "merged": merged, "source": "db", "version": kb_version}
    _cache[sig] = (None, data)
    return data


def library_page_data(settings, store, dao=None) -> dict[str, Any] | None:
    """{"page_index", "merged", "source", "version"} for the best available library data, or None."""
    # 1. the KB tables (what every deployment has)
    if dao is not None:
        try:
            data = _db_page_data(dao)
            if data is not None:
                return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("library_page_data: KB tables unavailable for cards: %s", exc)
    # 2. approved build
    try:
        ref = library.materialise_library(store, settings) if store is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("library_page_data: approved build unavailable: %s", exc)
        ref = None
    if ref is not None:
        key = f"approved:{ref['artifact_id']}"
        if key not in _cache:
            page_index, merged = library.load_library(ref["path"])
            _cache[key] = (None, {"page_index": page_index, "merged": merged, "source": "approved", "version": ref["version"]})
        return _cache[key][1]
    # 3. latest build on disk
    kb = library.locate_library_kb(settings.kb_root_for(library.LIBRARY_APP_ID))
    if kb is not None:
        sig = str(os.stat(kb / library.PAGE_INDEX_FILE).st_mtime_ns)
        key = f"latest:{kb}"
        if key not in _cache or _cache[key][0] != sig:
            page_index, merged = library.load_library(kb)
            _cache[key] = (sig, {"page_index": page_index, "merged": merged, "source": "latest", "version": None})
        return _cache[key][1]
    # 4. uploaded module JSONs, merged on the fly
    docs = _corpus_docs(settings)
    if not docs:
        return None
    sig = "|".join(f"{p.name}:{os.stat(p).st_mtime_ns}" for p, _ in docs)
    key = "corpus"
    if key not in _cache or _cache[key][0] != sig:
        merged, clashes, _notes = merge_modules([d for _, d in docs])
        if clashes:
            logger.warning("library_page_data: module JSONs clash (%d); cards shown from the merge anyway", len(clashes))
        _cache[key] = (sig, {"page_index": library.build_page_index(merged), "merged": merged, "source": "corpus", "version": None})
    return _cache[key][1]


def _card_dto(card: dict, kind: str) -> dict[str, Any]:
    return {"id": card.get("id", ""), "kind": kind, "label": str(card.get("label") or card.get("rule_name") or card.get("id") or ""),
            "summary": str(card.get("summary") or card.get("description") or "")[:400], "confidence": card.get("confidence")}


def cards_for_page(settings, store, page_name: str, dao=None) -> dict[str, Any] | None:
    """The library's cards for ``page_name`` (matched by file name, ignoring case and folder)."""
    data = library_page_data(settings, store, dao)
    if data is None:
        return None
    norm = normalise_page(page_name)
    merged, page_index = data["merged"], data["page_index"]
    entry = page_index.get("pages", {}).get(norm)
    page = next((p for p in merged.get("pages", []) if normalise_page(p.get("page", "")) == norm), None)
    base = {"source": data["source"], "version": data["version"], "found": page is not None or entry is not None, "page": page_name,
            "cards": {}, "counts": {}, "shared_entities": []}
    if page is None and entry is None:
        return base

    shared: dict[str, tuple[str, dict]] = {}
    for kind in KIND_ORDER:
        for card in merged.get("shared_cards", {}).get(kind, []):
            if card.get("id"):
                shared[card["id"]] = (kind, card)

    cards: dict[str, list[dict]] = {}
    shared_entities: list[dict] = []
    for kind in KIND_ORDER:
        for card in (page or {}).get("cards", {}).get(kind, []):
            if card.get("id"):
                cards.setdefault(kind, []).append(_card_dto(card, kind))
            elif card.get("ref") and card["ref"] in shared:
                skind, scard = shared[card["ref"]]
                shared_entities.append(_card_dto(scard, skind))
    for ref in (entry or {}).get("shared_refs", []):
        if ref in shared and all(s["id"] != ref for s in shared_entities):
            skind, scard = shared[ref]
            shared_entities.append(_card_dto(scard, skind))

    module_key = (entry or {}).get("module_key") or (page or {}).get("module_key") or merged.get("module_key") or ""
    module_name = next((m.get("module") for m in merged.get("modules", []) if m.get("module_key") == module_key), None) or merged.get("module")
    domain = (page or {}).get("business_domain") if isinstance((page or {}).get("business_domain"), dict) else {}
    base.update({
        "module_key": module_key, "module": module_name, "submodule": (domain or {}).get("module") or None,
        "domain": (domain or {}).get("domain") or next((c.get("business_domain") for k in cards.values() for c in k if c.get("business_domain")), None),
        "layer": (page or {}).get("layer") or (entry or {}).get("layer"),
        "cards": cards, "counts": {k: len(v) for k, v in cards.items()}, "shared_entities": shared_entities,
        "screenshots": len((entry or {}).get("screenshots", [])),
    })
    return base


def cards_summary(settings, store, page_name: str, dao=None) -> dict[str, Any] | None:
    """Counts only, for lists of many pages."""
    full = cards_for_page(settings, store, page_name, dao)
    if full is None:
        return None
    return {k: full.get(k) for k in ("source", "found", "module_key", "module", "submodule", "domain", "counts")} | {
        "shared_entities": len(full.get("shared_entities", []))}

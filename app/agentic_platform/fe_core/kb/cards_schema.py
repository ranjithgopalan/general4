"""Pydantic models and validation for the cards JSON uploaded as document_kind=re-cards.

Schema matches §1.1 of IMPLEMENTATION-PLAN-card-pipeline-v1.md.
The validate() function is called at upload time (422 on any violation) and
reused by the WP2 kb_extract gate.
"""

from __future__ import annotations

import re
from typing import Any

CARD_ID_PATTERN = re.compile(
    r"^(BR|FR|SCR|CMP|SRV|API|ENT|WF)-[A-Z0-9]{2,6}-\d{3,4}$"
)

KIND_PREFIXES = {"BR", "FR", "SCR", "CMP", "API", "ENT", "WF"}

# Fixed iteration order, so two builds of the same input write identical trees.
KIND_ORDER = ("BR", "FR", "SCR", "CMP", "API", "ENT", "WF")

# Id prefixes that belong to an existing kind. The RED JSON numbers components
# as SRV-<APP>-NNN; those ids are kept verbatim and the cards are components.
PREFIX_KIND = {"SRV": "CMP"}

KIND_DIR = {
    "BR": "BR",
    "FR": "FR",
    "SCR": "SCR",
    "CMP": "CMP",
    "API": "API",
    "ENT": "ENT",
    "WF": "WF",
}

KIND_LABEL = {
    "BR": "BusinessRule",
    "FR": "FunctionalReq",
    "SCR": "Screen",
    "CMP": "Component",
    "API": "ApiOp",
    "ENT": "Entity",
    "WF": "Workflow",
}


def _prefix(card_id: str) -> str:
    return card_id.split("-")[0] if card_id else ""


def kind_of(card_id: str) -> str:
    """Kind a card id belongs to: its prefix, or the kind an alias prefix maps to."""
    prefix = _prefix(card_id)
    return PREFIX_KIND.get(prefix, prefix)


def normalise_page(name: str) -> str:
    """Join key for an ASP page: file name only, lower-case."""
    return str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _collect_all_ids(data: dict) -> dict[str, str]:
    """Return {card_id: location} for every card defined in data."""
    ids: dict[str, str] = {}

    def _add(card_id: str, loc: str) -> None:
        ids[card_id] = loc

    for page in data.get("pages", []):
        page_name = page.get("page", "?")
        cards_block = page.get("cards", {})
        for kind in KIND_ORDER:
            for card in cards_block.get(kind, []):
                cid = card.get("id", "")
                if cid:
                    _add(cid, f"pages[{page_name}].cards.{kind}")

    shared = data.get("shared_cards", {})
    for kind in KIND_ORDER:
        for card in shared.get(kind, []):
            cid = card.get("id", "")
            if cid:
                _add(cid, f"shared_cards.{kind}")

    return ids


def _iter_defined_ids(data: dict):
    """Yield (card_id, location) for every card definition, repeats included."""
    for page in data.get("pages", []):
        page_name = page.get("page", "?")
        for kind in KIND_ORDER:
            for card in page.get("cards", {}).get(kind, []):
                if card.get("id"):
                    yield card["id"], f"pages[{page_name}].cards.{kind}"
    for kind in KIND_ORDER:
        for card in data.get("shared_cards", {}).get(kind, []):
            if card.get("id"):
                yield card["id"], f"shared_cards.{kind}"


def validate(data: dict) -> list[str]:
    """Return a list of violation strings (empty = valid).

    Checks:
    - Required top-level keys present
    - Every card id matches the id pattern and has the right prefix for its kind
    - Ids are unique across pages and shared_cards
    - Every card has label, summary, page, ≥1 evidence (ENT refs from pages are skipped)
    - Every id in edges.from/to, source_br, implemented_by, db_objects_used exists
    - edges[].label is in edge_labels
    """
    violations: list[str] = []

    edge_labels: set[str] = set(data.get("edge_labels", []))
    all_ids = _collect_all_ids(data)

    # 1. Unique id check
    seen: dict[str, str] = {}
    for cid, loc in _iter_defined_ids(data):
        if cid in seen:
            violations.append(
                f"duplicate id '{cid}' in {loc} (first seen in {seen[cid]})"
            )
        else:
            seen[cid] = loc

    # 2. Pattern + prefix check and required fields per card
    for page in data.get("pages", []):
        page_name = page.get("page", "?")
        cards_block = page.get("cards", {})

        for kind in KIND_ORDER:
            for card in cards_block.get(kind, []):
                # ENT refs under pages are pointer-only — skip field checks
                if kind == "ENT" and card.get("ref") and not card.get("id"):
                    continue

                cid = card.get("id", "")
                loc = f"pages[{page_name}].{kind}"

                if not CARD_ID_PATTERN.match(cid):
                    violations.append(
                        f"{loc}: id '{cid}' does not match {CARD_ID_PATTERN.pattern}"
                    )
                elif kind_of(cid) != kind:
                    violations.append(
                        f"{loc}: id '{cid}' has wrong prefix for kind '{kind}'"
                    )

                for req in ("label", "summary"):
                    if not card.get(req):
                        violations.append(f"{loc}[{cid}]: missing required field '{req}'")

                evidence = card.get("evidence", [])
                if not evidence:
                    violations.append(f"{loc}[{cid}]: must have ≥1 evidence entry")

                # cross-reference checks
                for ref_field in ("source_br", "implemented_by", "db_objects_used"):
                    for ref_id in card.get(ref_field, []):
                        if ref_id and ref_id not in all_ids:
                            violations.append(
                                f"{loc}[{cid}].{ref_field}: '{ref_id}' does not exist"
                            )

    # shared_cards
    shared = data.get("shared_cards", {})
    for kind in KIND_ORDER:
        for card in shared.get(kind, []):
            cid = card.get("id", "")
            loc = f"shared_cards.{kind}"
            if not CARD_ID_PATTERN.match(cid):
                violations.append(
                    f"{loc}: id '{cid}' does not match the required pattern"
                )
            elif kind_of(cid) != kind:
                violations.append(
                    f"{loc}: id '{cid}' has wrong prefix for kind '{kind}'"
                )
            for req in ("label", "summary"):
                if not card.get(req):
                    violations.append(f"{loc}[{cid}]: missing required field '{req}'")
            evidence = card.get("evidence", [])
            if not evidence:
                violations.append(f"{loc}[{cid}]: must have ≥1 evidence entry")

    # 3. Edge label + endpoint existence
    for edge in data.get("edges", []):
        from_id = edge.get("from", "")
        to_id = edge.get("to", "")
        label = edge.get("label", "")

        if edge_labels and label not in edge_labels:
            violations.append(
                f"edge '{from_id}→{to_id}': label '{label}' not in edge_labels {sorted(edge_labels)}"
            )
        if from_id and from_id not in all_ids:
            violations.append(
                f"edge dangling from: '{from_id}' does not exist"
            )
        if to_id and to_id not in all_ids:
            violations.append(
                f"edge dangling to: '{to_id}' does not exist"
            )

    return violations


def iter_all_cards(data: dict) -> list[dict[str, Any]]:
    """Return every fully-defined card from pages and shared_cards as flat dicts.

    ENT pointer-refs under pages (only a 'ref' key) are resolved to shared_cards.
    The returned dicts always have 'id', 'kind', 'page' (empty for shared),
    'section' (0 for shared), and every field the JSON carries.
    """
    cards: list[dict[str, Any]] = []

    # Build shared ENT lookup for pointer resolution
    shared_ent: dict[str, dict] = {}
    shared = data.get("shared_cards", {})
    for kind in KIND_ORDER:
        for card in shared.get(kind, []):
            cid = card.get("id", "")
            if cid:
                shared_ent[cid] = {**card, "kind": kind, "page": "", "section": 0, "_from_shared": True}

    emitted_shared: set[str] = set()
    for page in data.get("pages", []):
        page_name = page.get("page", "")
        section = page.get("section", 0)
        cards_block = page.get("cards", {})

        for kind in KIND_ORDER:
            for card in cards_block.get(kind, []):
                # ENT pointer-ref: resolve from shared. A shared card is one card
                # however many pages point at it, so it is emitted once.
                if kind == "ENT" and card.get("ref") and not card.get("id"):
                    ref_id = card["ref"]
                    resolved = shared_ent.get(ref_id, {}).copy()
                    if resolved and ref_id not in emitted_shared:
                        emitted_shared.add(ref_id)
                        resolved["_page_ref"] = page_name
                        cards.append(resolved)
                    continue

                flat = {**card, "kind": kind}
                if "page" not in flat:
                    flat["page"] = page_name
                if "section" not in flat:
                    flat["section"] = section
                cards.append(flat)

    # Shared cards that were not already emitted as page-refs
    emitted = {c["id"] for c in cards if c.get("id")}
    for kind in KIND_ORDER:
        for card in shared.get(kind, []):
            cid = card.get("id", "")
            if cid and cid not in emitted:
                flat = {**card, "kind": kind, "page": "", "section": 0}
                cards.append(flat)
                emitted.add(cid)

    return cards


# ---------------------------------------------------------------------------
# Module intake: one entry point for either input shape
# ---------------------------------------------------------------------------

def normalise(data: Any) -> tuple[dict, list[str], list[str]]:
    """Return (cards_doc, violations, warnings) for an uploaded module JSON.

    Accepts the RED JSON (files[] per ASP file, see red_json.py) or the cards
    JSON (pages[].cards). The returned doc is always in the cards model and has
    passed validate(); a JSON that defines no cards is a violation, never an
    empty knowledge base.
    """
    if not isinstance(data, dict):
        return {}, ["the JSON root must be an object"], []

    from app.agentic_platform.fe_core.kb import red_json  # noqa: PLC0415 (imports this module)

    warnings: list[str] = []
    if red_json.is_red_json(data):
        violations = red_json.validate_red(data)
        if violations:
            return {}, violations, []
        doc = red_json.to_cards_doc(data)
        warnings = list(doc.get("warnings", []))
    elif isinstance(data.get("pages"), list):
        doc = data
    else:
        return {}, ["unrecognised JSON shape: expected files[] (RED JSON) or pages[] (cards JSON)"], []

    violations = validate(doc)
    if not violations and not iter_all_cards(doc):
        violations = ["the JSON defines no cards"]
    return doc, violations, warnings


def module_key_of(doc: dict, fallback: str = "module") -> str:
    """Stable key for a module doc: its module_key, else a slug of its name."""
    key = str(doc.get("module_key") or "").strip()
    if not key:
        key = re.sub(r"[^a-z0-9]+", "-", str(doc.get("module") or "").lower()).strip("-")
    return key or fallback


def _table_key(name: str) -> str:
    return str(name or "").strip().rsplit(".", 1)[-1].lower()


def _merge_shared_entity(target: dict, extra: dict, module_key: str, warnings: list[str]) -> None:
    """Fold `extra` into `target` (same ENT id, same table): lists are unioned."""
    cid = target.get("id", "")
    known_cols = {str(c.get("name", "")).lower() if isinstance(c, dict) else str(c).lower()
                  for c in target.get("key_columns", [])}
    for col in extra.get("key_columns", []) or []:
        name = str(col.get("name", "")).lower() if isinstance(col, dict) else str(col).lower()
        if name not in known_cols:
            target.setdefault("key_columns", []).append(col)
            known_cols.add(name)
    for field_ in ("pages", "stored_procedures", "sql_query_patterns", "evidence", "conflicts"):
        for item in extra.get(field_, []) or []:
            if item not in target.setdefault(field_, []):
                target[field_].append(item)
    theirs = extra.get("jpa_lookup")
    if theirs and target.get("jpa_lookup") and theirs != target["jpa_lookup"]:
        conflict = {"field": "jpa_lookup", "module": module_key, "value": theirs}
        if conflict not in target.setdefault("conflicts", []):
            target["conflicts"].append(conflict)
        warnings.append(f"entity '{cid}': jpa_lookup differs in module '{module_key}'; kept the first value")
    elif theirs and not target.get("jpa_lookup"):
        target["jpa_lookup"] = theirs


def merge_modules(docs: list[dict]) -> tuple[dict, list[str], list[str]]:
    """Merge normalised module docs into one library doc.

    The library is one flat namespace, so:
    - the same card id defined differently in two modules is a violation
    - the same ASP page in two modules is a violation
    - identical repeats collapse; a shared entity with the same table is merged
    - a dangling reference after the merge is a violation (validate)

    Returns (merged_doc, violations, warnings).
    """
    import json  # noqa: PLC0415

    violations: list[str] = []
    warnings: list[str] = []
    merged: dict[str, Any] = {
        "app": "", "module": "", "module_key": "", "modules": [], "edge_labels": [],
        "pages": [], "shared_cards": {}, "edges": [], "issues": [], "warnings": [],
    }
    page_owner: dict[str, str] = {}
    card_owner: dict[str, tuple[str, str]] = {}
    shared: dict[str, dict[str, dict]] = {}
    shared_owner: dict[str, str] = {}
    edge_keys: set[tuple[str, str, str]] = set()
    apps: list[str] = []

    for idx, doc in enumerate(docs):
        mkey = module_key_of(doc, f"module-{idx + 1}")
        merged["modules"].append({
            "module_key": mkey, "module": doc.get("module", ""), "app": doc.get("app", ""),
            "pages": [p.get("page", "") for p in doc.get("pages", [])],
        })
        if doc.get("app") and doc["app"] not in apps:
            apps.append(doc["app"])
        for label in doc.get("edge_labels", []):
            if label not in merged["edge_labels"]:
                merged["edge_labels"].append(label)

        for page in doc.get("pages", []):
            page_name = page.get("page", "")
            norm = normalise_page(page_name)
            if norm in page_owner:
                violations.append(
                    f"page '{page_name}' is defined in module '{page_owner[norm]}' and in module '{mkey}'")
                continue
            page_owner[norm] = mkey
            new_cards: dict[str, list[dict]] = {}
            for kind in KIND_ORDER:
                kept: list[dict] = []
                for card in page.get("cards", {}).get(kind, []):
                    cid = card.get("id", "")
                    if not cid:  # pointer to a shared card
                        kept.append(card)
                        continue
                    body = json.dumps(card, sort_keys=True, default=str)
                    if cid in shared_owner:
                        violations.append(
                            f"card id '{cid}' is a shared card in module '{shared_owner[cid]}' "
                            f"and a page card in module '{mkey}'")
                        continue
                    if cid in card_owner:
                        prev_module, prev_body = card_owner[cid]
                        if prev_body != body:
                            violations.append(
                                f"card id '{cid}' is defined differently in module "
                                f"'{prev_module}' and in module '{mkey}'")
                        continue
                    card_owner[cid] = (mkey, body)
                    kept.append(card)
                new_cards[kind] = kept
            merged["pages"].append({**page, "module_key": mkey,
                                    "section": len(merged["pages"]) + 1, "cards": new_cards})

        for kind in KIND_ORDER:
            for card in doc.get("shared_cards", {}).get(kind, []):
                cid = card.get("id", "")
                if not cid:
                    continue
                if cid in card_owner:
                    violations.append(
                        f"card id '{cid}' is a page card in module '{card_owner[cid][0]}' "
                        f"and a shared card in module '{mkey}'")
                    continue
                existing = shared.setdefault(kind, {}).get(cid)
                if existing is None:
                    shared[kind][cid] = json.loads(json.dumps(card, default=str))
                    shared_owner[cid] = mkey
                    continue
                if json.dumps(existing, sort_keys=True, default=str) == json.dumps(card, sort_keys=True, default=str):
                    continue
                if kind == "ENT" and _table_key(existing.get("maps_to_table", "")) == _table_key(
                        card.get("maps_to_table", "")):
                    _merge_shared_entity(existing, card, mkey, warnings)
                else:
                    violations.append(
                        f"shared card id '{cid}' is defined differently in module "
                        f"'{shared_owner[cid]}' and in module '{mkey}'")

        for edge in doc.get("edges", []):
            key = (edge.get("from", ""), edge.get("to", ""), edge.get("label", ""))
            if key not in edge_keys:
                edge_keys.add(key)
                merged["edges"].append(edge)
        merged["issues"].extend(doc.get("issues", []))
        merged["warnings"].extend(doc.get("warnings", []))

    merged["shared_cards"] = {kind: list(cards.values()) for kind, cards in shared.items()}
    if len(docs) == 1:
        merged["app"] = docs[0].get("app", "")
        merged["module"] = docs[0].get("module", "")
        merged["module_key"] = merged["modules"][0]["module_key"]
        for key in ("generated_at", "source", "source_format"):
            if key in docs[0]:
                merged[key] = docs[0][key]
    else:
        merged["app"] = apps[0] if len(apps) == 1 else ""
        merged["module"] = "; ".join(m["module"] or m["module_key"] for m in merged["modules"])
        merged["module_key"] = "library"

    if not violations:
        violations = validate(merged)
    warnings.extend(w for w in merged["warnings"] if w not in warnings)
    return merged, violations, warnings

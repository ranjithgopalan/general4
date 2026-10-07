"""Adapter for the RED JSON uploaded as document_kind=re-cards.

The RED JSON is the Requirements Extraction Document in JSON form: one section
per ASP file, each with six lists. Reference sample: doc/RED-Company-Search.json.

    {"module": "...", "source_project": "...", "id_convention": "...", "totals": {...},
     "files": [{"file_name": "CompanyDetail.asp", "source_path": "/aiucredit/Credit/CompanyDetail.asp",
                "layer": "UI", "business_domain": {...},
                "business_rules": [...], "functional_requirements": [...],
                "services_and_apis": [...], "data_entities": [...],
                "screens": [...], "workflows": [...]}]}

to_cards_doc() converts it into the cards model the rest of the KB code reads
(cards_schema: pages[].cards.<KIND>[], shared_cards, edges), so validate(),
kb_extract, writer, check and pg_sink work on one shape.

Ids are taken verbatim. Components keep their SRV-<APP>-NNN id and are cards of
the component kind (cards_schema.PREFIX_KIND). An entity repeated under every
file that uses it becomes one shared card that lists its pages.
"""

from __future__ import annotations

import re
from typing import Any

from app.agentic_platform.fe_core.kb.cards_schema import CARD_ID_PATTERN, kind_of

EDGE_LABELS = ["GOVERNS", "IMPLEMENTS", "CALLS", "DEPENDS_ON", "REFERENCES", "TRIGGERS"]

# RED list name -> card kind. services_and_apis is split by its `type` field.
_LIST_KIND = {
    "business_rules": "BR",
    "functional_requirements": "FR",
    "data_entities": "ENT",
    "screens": "SCR",
    "workflows": "WF",
}
_SERVICE_TYPE_KIND = {"service": "CMP", "integration": "API"}

_SUMMARY_MAX = 400
_LABEL_MAX = 80
_SNIPPET_MAX = 300


def is_red_json(data: Any) -> bool:
    """True when `data` has the RED JSON shape (files[] keyed by file_name)."""
    return (
        isinstance(data, dict)
        and isinstance(data.get("files"), list)
        and "pages" not in data
    )


def _norm_page(name: str) -> str:
    return str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return (cut or text[:limit]) + "…"


def _confidence(value: Any) -> float:
    """RED confidence is 0-100; the KB stores 0-1."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return round(v / 100.0, 4) if v > 1 else v


def _table_key(name: str) -> str:
    """'dbo.allCompany' / 'allCompany' -> 'allcompany' for table-name matching."""
    return str(name or "").strip().rsplit(".", 1)[-1].lower()


def _service_kind(entry: dict) -> str:
    return _SERVICE_TYPE_KIND.get(str(entry.get("type", "")).strip().lower(), "")


def _entries(file_: dict, key: str) -> list[dict]:
    value = file_.get(key) or []
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Validation of the RED shape itself
# ---------------------------------------------------------------------------

def validate_red(data: dict) -> list[str]:
    """Return violations of the RED JSON contract (empty = valid).

    - files[] is non-empty and every file has a unique file_name
    - every entry has an id matching the card id pattern, of the kind its list holds
    - services_and_apis[].type is Service or Integration
    - ids are unique across files, except entities, which may repeat
    - a repeated entity always maps to the same table
    - every FR.source_br id exists
    """
    violations: list[str] = []
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return ["RED JSON has no files[] entries"]

    seen_pages: dict[str, str] = {}
    seen_ids: dict[str, str] = {}
    ent_table: dict[str, str] = {}
    br_ids: set[str] = set()

    for idx, file_ in enumerate(files):
        if not isinstance(file_, dict):
            violations.append(f"files[{idx}] is not an object")
            continue
        name = str(file_.get("file_name") or "").strip()
        if not name:
            violations.append(f"files[{idx}] has no file_name")
            continue
        norm = _norm_page(name)
        if norm in seen_pages:
            violations.append(
                f"file_name '{name}' appears twice (also as '{seen_pages[norm]}')")
        else:
            seen_pages[norm] = name

        def _check_id(entry: dict, expected_kind: str, loc: str) -> str:
            cid = str(entry.get("id") or "").strip()
            if not cid:
                violations.append(f"{loc}: entry has no id")
                return ""
            if not CARD_ID_PATTERN.match(cid):
                violations.append(f"{loc}: id '{cid}' does not match {CARD_ID_PATTERN.pattern}")
            elif kind_of(cid) != expected_kind:
                violations.append(f"{loc}: id '{cid}' is not a {expected_kind} id")
            return cid

        for list_name, kind in _LIST_KIND.items():
            for entry in _entries(file_, list_name):
                loc = f"files[{name}].{list_name}"
                cid = _check_id(entry, kind, loc)
                if not cid:
                    continue
                if kind == "ENT":
                    table = _table_key(entry.get("maps_to_table", ""))
                    if cid in ent_table and table and ent_table[cid] and ent_table[cid] != table:
                        violations.append(
                            f"{loc}: entity '{cid}' maps to '{entry.get('maps_to_table')}' "
                            f"here and to another table elsewhere")
                    ent_table.setdefault(cid, table)
                    continue
                if cid in seen_ids:
                    violations.append(f"duplicate id '{cid}' in {loc} (first seen in {seen_ids[cid]})")
                else:
                    seen_ids[cid] = loc
                if kind == "BR":
                    br_ids.add(cid)

        for entry in _entries(file_, "services_and_apis"):
            loc = f"files[{name}].services_and_apis"
            kind = _service_kind(entry)
            if not kind:
                violations.append(
                    f"{loc}: '{entry.get('id', '?')}' has type '{entry.get('type')}' "
                    f"(expected Service or Integration)")
                continue
            cid = _check_id(entry, kind, loc)
            if not cid:
                continue
            if cid in seen_ids:
                violations.append(f"duplicate id '{cid}' in {loc} (first seen in {seen_ids[cid]})")
            else:
                seen_ids[cid] = loc

    for file_ in files:
        if not isinstance(file_, dict):
            continue
        for entry in _entries(file_, "functional_requirements"):
            for ref in entry.get("source_br") or []:
                if ref and ref not in br_ids:
                    violations.append(
                        f"files[{file_.get('file_name', '?')}].functional_requirements"
                        f"[{entry.get('id', '?')}].source_br: '{ref}' does not exist")

    if not seen_ids and not ent_table:
        violations.append("RED JSON defines no cards")
    return violations


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _evidence(source: str, source_path: str, snippet: str, lines: list | None = None) -> list[dict]:
    locus: dict[str, Any] = {"file": source_path or source}
    if lines:
        locus["lines"] = list(lines)
    return [{"source": source, "locus": locus, "snippet": _shorten(snippet, _SNIPPET_MAX)}]


def _carry(entry: dict, exclude: tuple[str, ...]) -> dict:
    """Every RED field not explicitly mapped travels with the card unchanged."""
    return {k: v for k, v in entry.items() if k not in exclude}


def _merge_list(target: list, extra: list) -> None:
    for item in extra or []:
        if item not in target:
            target.append(item)


def to_cards_doc(data: dict) -> dict:
    """Convert a validated RED JSON into the cards model. See module docstring."""
    files = [f for f in data.get("files", []) if isinstance(f, dict) and f.get("file_name")]
    module = str(data.get("module") or "").strip()
    warnings: list[str] = []
    issues: list[dict] = []

    # --- pass 1: entities, merged across files ------------------------------
    entities: dict[str, dict] = {}
    table_to_ent: dict[str, str] = {}
    app_code = ""
    for file_ in files:
        page = str(file_["file_name"]).strip()
        source_path = str(file_.get("source_path") or page)
        seen_here: set[str] = set()
        for entry in _entries(file_, "data_entities"):
            cid = str(entry.get("id") or "").strip()
            if not cid:
                continue
            if cid in seen_here:
                warnings.append(f"{page}: entity '{cid}' is listed more than once")
                continue
            seen_here.add(cid)
            table = str(entry.get("maps_to_table") or "").strip()
            card = entities.get(cid)
            if card is None:
                card = {
                    "id": cid,
                    "label": table.rsplit(".", 1)[-1] or cid,
                    "maps_to_table": table,
                    "key_columns": [],
                    "stored_procedures": [],
                    "sql_query_patterns": [],
                    "jpa_lookup": str(entry.get("jpa_lookup") or ""),
                    "pages": [],
                    "conflicts": [],
                    "confidence": _confidence(entry.get("confidence")),
                    "origin": "red_json",
                    "evidence": [],
                }
                entities[cid] = card
                if table:
                    table_to_ent.setdefault(_table_key(table), cid)
            known_cols = {str(c.get("name", "")).lower() for c in card["key_columns"] if isinstance(c, dict)}
            for col in entry.get("key_columns") or []:
                name = str(col.get("name", "")).lower() if isinstance(col, dict) else str(col).lower()
                if name not in known_cols:
                    card["key_columns"].append(col)
                    known_cols.add(name)
            _merge_list(card["stored_procedures"], entry.get("stored_procedures") or [])
            _merge_list(card["sql_query_patterns"], entry.get("sql_query_patterns") or [])
            lookup = str(entry.get("jpa_lookup") or "")
            if lookup and card["jpa_lookup"] and lookup != card["jpa_lookup"]:
                conflict = {"field": "jpa_lookup", "page": page, "value": lookup}
                if conflict not in card["conflicts"]:
                    card["conflicts"].append(conflict)
            elif lookup and not card["jpa_lookup"]:
                card["jpa_lookup"] = lookup
            card["confidence"] = max(card["confidence"], _confidence(entry.get("confidence")))
            if page not in card["pages"]:
                card["pages"].append(page)
                patterns = entry.get("sql_query_patterns") or []
                snippet = patterns[0] if patterns else f"{page} uses table {table or cid}"
                card["evidence"].extend(_evidence(page, source_path, str(snippet)))

    for cid, card in entities.items():
        table = card["maps_to_table"] or cid
        card["summary"] = _shorten(
            f"Table {table}, used by {len(card['pages'])} page(s): {', '.join(card['pages'])}",
            _SUMMARY_MAX)
        if card["conflicts"]:
            issues.append({"type": "entity_conflict", "id": cid,
                           "fields": sorted({c["field"] for c in card["conflicts"]})})

    # --- pass 2: pages and their cards ---------------------------------------
    pages: list[dict] = []
    edges: list[dict] = []
    edge_keys: set[tuple[str, str, str]] = set()
    scr_by_page: dict[str, list[str]] = {}
    api_callers: list[tuple[str, str, str]] = []  # (api id, callers text, own page)

    def _edge(src: str, dst: str, label: str, basis: str, tag: str) -> None:
        key = (src, dst, label)
        if src and dst and key not in edge_keys:
            edge_keys.add(key)
            edges.append({"from": src, "to": dst, "label": label, "basis": basis, "tag": tag})

    for section, file_ in enumerate(files, start=1):
        page = str(file_["file_name"]).strip()
        source_path = str(file_.get("source_path") or page)
        domain = file_.get("business_domain") if isinstance(file_.get("business_domain"), dict) else {}
        domain_name = str(domain.get("domain") or module or "")
        cards: dict[str, list[dict]] = {k: [] for k in ("BR", "FR", "SCR", "CMP", "API", "ENT", "WF")}

        def _base(entry: dict) -> dict:
            return {"origin": "red_json", "business_domain": domain_name,
                    "confidence": _confidence(entry.get("confidence"))}

        for entry in _entries(file_, "business_rules"):
            cid = str(entry.get("id") or "").strip()
            if not app_code and cid.count("-") >= 2:
                app_code = cid.split("-")[1]
            desc = str(entry.get("description") or "")
            lines = entry.get("source_lines") or []
            if not lines:
                issues.append({"type": "no_source_lines", "id": cid, "page": page})
            cards["BR"].append({
                **_carry(entry, ("id", "rule_name", "confidence")),
                **_base(entry),
                "id": cid,
                "label": str(entry.get("rule_name") or _shorten(desc, _LABEL_MAX) or cid),
                "summary": _shorten(desc, _SUMMARY_MAX) or str(entry.get("rule_name") or cid),
                "description": desc,
                "evidence": _evidence(str(entry.get("source_file") or page), source_path,
                                      str(entry.get("logic") or desc or cid), lines),
            })

        for entry in _entries(file_, "functional_requirements"):
            cid = str(entry.get("id") or "").strip()
            desc = str(entry.get("description") or "")
            cards["FR"].append({
                **_carry(entry, ("id", "confidence")),
                **_base(entry),
                "id": cid,
                "label": _shorten(desc, _LABEL_MAX) or cid,
                "summary": _shorten(desc, _SUMMARY_MAX) or cid,
                "source_br": list(entry.get("source_br") or []),
                "evidence": _evidence(page, source_path, desc or cid),
            })
            for br in entry.get("source_br") or []:
                _edge(br, cid, "GOVERNS", "FR.source_br", "stated")

        for entry in _entries(file_, "services_and_apis"):
            cid = str(entry.get("id") or "").strip()
            kind = _service_kind(entry)
            name = str(entry.get("name") or cid)
            function = str(entry.get("function") or "")
            if kind == "CMP":
                raw_objects = entry.get("db_objects") or ""
                names = raw_objects if isinstance(raw_objects, list) else str(raw_objects).split(",")
                used: list[str] = []
                for obj in (str(n).strip() for n in names):
                    if not obj:
                        continue
                    ent = table_to_ent.get(_table_key(obj))
                    if ent:
                        if ent not in used:
                            used.append(ent)
                    else:
                        warnings.append(f"{page}: {cid}.db_objects names '{obj}', which matches no entity")
                        issues.append({"type": "unresolved_db_object", "id": cid, "page": page, "name": obj})
                cards["CMP"].append({
                    **_carry(entry, ("id", "name", "function", "type", "confidence")),
                    **_base(entry),
                    "id": cid,
                    "label": name,
                    "summary": _shorten(function, _SUMMARY_MAX) or name,
                    "description": function,
                    "component": name,
                    "business_function": function,
                    "db_objects_used": used,
                    "evidence": _evidence(page, source_path, function or name),
                })
                for ent in used:
                    _edge(cid, ent, "DEPENDS_ON", "SRV.db_objects", "inferred")
            elif kind == "API":
                cards["API"].append({
                    **_carry(entry, ("id", "type", "confidence")),
                    **_base(entry),
                    "id": cid,
                    "label": name,
                    "summary": _shorten(function, _SUMMARY_MAX) or name,
                    "description": function,
                    "integration_type": str(entry.get("type") or ""),
                    "evidence": _evidence(page, source_path, function or name),
                })
                api_callers.append((cid, str(entry.get("callers") or ""), page))

        seen_ent: set[str] = set()
        for entry in _entries(file_, "data_entities"):
            cid = str(entry.get("id") or "").strip()
            if cid and cid not in seen_ent:
                seen_ent.add(cid)
                cards["ENT"].append({"ref": cid, "defined_in": "shared_cards"})

        for entry in _entries(file_, "screens"):
            cid = str(entry.get("id") or "").strip()
            screen = str(entry.get("screen") or cid)
            route = str(entry.get("route") or "")
            roles = list(entry.get("roles") or [])
            if not (entry.get("key_fields") or entry.get("buttons")):
                issues.append({"type": "screen_without_fields", "id": cid, "page": page})
            source = str(entry.get("source") or page)
            if _norm_page(source) != _norm_page(page):
                warnings.append(f"{page}: screen '{cid}' names source '{source}'")
            summary = f"{screen} screen"
            if route:
                summary += f" at {route}"
            if roles:
                summary += f"; visible to {', '.join(str(r) for r in roles)}"
            cards["SCR"].append({
                **_carry(entry, ("id", "screen", "roles", "confidence")),
                **_base(entry),
                "id": cid,
                "label": screen,
                "summary": summary,
                "roles_visible": roles,
                "key_fields": list(entry.get("key_fields") or []),
                "buttons": list(entry.get("buttons") or []),
                "evidence": _evidence(page, source_path, f"Screen '{screen}' rendered by {page}"),
            })
            scr_by_page.setdefault(_norm_page(page), []).append(cid)

        for entry in _entries(file_, "workflows"):
            cid = str(entry.get("id") or "").strip()
            entity = str(entry.get("entity") or cid)
            states = [str(s) for s in entry.get("states") or []]
            events = [str(e) for e in entry.get("events_fired") or []]
            cards["WF"].append({
                **_carry(entry, ("id", "confidence")),
                **_base(entry),
                "id": cid,
                "label": entity,
                "summary": _shorten(
                    f"State machine for {entity}" + (f": {' → '.join(states)}" if states else ""),
                    _SUMMARY_MAX),
                "evidence": _evidence(page, source_path,
                                      (events[0] if events else f"Workflow on {entity}")),
            })
            ent = table_to_ent.get(_table_key(re.split(r"[\s.(]", entity.strip(), 1)[0]))
            if ent:
                _edge(cid, ent, "REFERENCES", "WF.entity", "inferred")

        pages.append({
            "section": section,
            "page": page,
            "source_path": source_path,
            "layer": file_.get("layer", ""),
            "risk_level": file_.get("risk_level", ""),
            "loc": file_.get("loc", ""),
            "cyclomatic_complexity": file_.get("cyclomatic_complexity", ""),
            "business_domain": domain,
            "cards": cards,
        })

    # A screen calls the APIs whose `callers` names the screen's ASP file.
    for api_id, callers, own_page in api_callers:
        named = [_norm_page(tok) for tok in re.findall(r"[A-Za-z0-9_]+\.asp", callers, flags=re.I)]
        for norm in named or [_norm_page(own_page)]:
            for scr_id in scr_by_page.get(norm, []):
                _edge(scr_id, api_id, "CALLS", "API.callers", "stated")

    counts: dict[str, int] = {k: 0 for k in ("BR", "FR", "SCR", "CMP", "API", "ENT", "WF")}
    for page_ in pages:
        for kind, items in page_["cards"].items():
            if kind != "ENT":
                counts[kind] += len(items)
    counts["ENT"] = len(entities)

    return {
        "app": app_code,
        "module": module,
        "module_key": _slug(module) or _slug(data.get("source_project", "")) or "module",
        "source_format": "red-json",
        "source": {"source_project": data.get("source_project", ""),
                   "id_convention": data.get("id_convention", "")},
        "edge_labels": list(EDGE_LABELS),
        "summary": {"pages": len(pages), "cards": counts,
                    "cards_total": sum(counts.values()), "edges": len(edges),
                    "issues": len(issues)},
        "pages": pages,
        "shared_cards": {"ENT": list(entities.values())},
        "edges": edges,
        "issues": issues,
        "warnings": warnings,
    }

"""LLM fallback for KB inputs the procedural parsers cannot read.

Procedural parsing is always tried first (cards_schema.normalise for the module
JSON, rag.extract._docx_extract_images for the screens document). A model is
asked only when that fails, and it never writes card content:

  module JSON      the model sees a structural outline of the upload and returns
                   a *mapping* onto the RED JSON layout; apply_red_mapping()
                   copies the data across, and the usual validation decides.
                   Ids therefore stay exactly as uploaded.

  screens document the model sees the text around each picture no file-name
                   line claimed and names the ASP file(s) it shows; a name that
                   is neither a known page nor in that text is discarded.

Everything the fallback did is returned as notes, so the upload reply and the
build report show it and a reviewer can check it. FE_KB_LLM_FALLBACK=false
turns it off. Tests pass their own `complete` callable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from app.agentic_platform.fe_core.kb.cards_schema import normalise, normalise_page

logger = logging.getLogger(__name__)

# (system, user) -> the model's text
Completer = Callable[[str, str], str]

RED_LISTS = ("business_rules", "functional_requirements", "services_and_apis",
             "data_entities", "screens", "workflows")
# Lists a mapping may name. Services and APIs may be one source list or two.
MAPPING_LISTS = ("business_rules", "functional_requirements", "services", "apis",
                 "data_entities", "screens", "workflows")

_OUTLINE_MAX_CHARS = 14_000
_ASP_RE = re.compile(r"[A-Za-z0-9_]+\.asp\b", re.I)


class FallbackUnavailable(RuntimeError):
    """The fallback is switched off or the model cannot be reached."""


def enabled(settings=None) -> bool:
    if settings is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        settings = get_settings()
    return bool(getattr(settings, "fe_kb_llm_fallback", True))


def default_completer(settings=None) -> Completer:
    """A completer backed by the Bedrock answer path the RAG chat already uses."""
    if settings is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        settings = get_settings()
    model = getattr(settings, "fe_kb_llm_fallback_model", "") or None

    def _complete(system: str, user: str) -> str:
        from app.agentic_platform.fe_core.rag import bedrock  # noqa: PLC0415
        try:
            text, _usage = bedrock.answer(user, system, model=model, max_tokens=2500)
        except Exception as exc:  # noqa: BLE001
            raise FallbackUnavailable(f"model call failed: {exc}") from exc
        return text

    return _complete


def _first_json_object(text: str) -> dict:
    """The first JSON object in a model reply (code fences and prose tolerated)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("the model reply holds no JSON object")
    obj, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("the model reply is not a JSON object")
    return obj


# ---------------------------------------------------------------------------
# Module JSON
# ---------------------------------------------------------------------------

def outline(data: Any, *, max_items: int = 2, max_str: int = 80, max_keys: int = 40,
            _depth: int = 0) -> Any:
    """A small structural sample of `data`: every key, the first items of each
    list, strings cut short. This is all of the upload the model sees."""
    if _depth > 8:
        return "…"
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for i, (key, value) in enumerate(data.items()):
            if i >= max_keys:
                out["…"] = f"{len(data) - max_keys} more keys"
                break
            out[str(key)] = outline(value, max_items=max_items, max_str=max_str,
                                    max_keys=max_keys, _depth=_depth + 1)
        return out
    if isinstance(data, list):
        items = [outline(v, max_items=max_items, max_str=max_str, max_keys=max_keys,
                         _depth=_depth + 1) for v in data[:max_items]]
        if len(data) > max_items:
            items.append(f"… ({len(data)} items in total)")
        return items
    if isinstance(data, str) and len(data) > max_str:
        return data[:max_str] + "…"
    return data


_MAPPING_SYSTEM = """You map a JSON document that describes a legacy ASP application onto a fixed target layout. \
You never rewrite content; you only say where each piece is.

Target layout (the RED JSON):
{"module": "<module name>",
 "files": [{"file_name": "<Page>.asp", "source_path": "<path of the ASP file>",
            "business_rules":          [{"id": "BR-…",  "rule_name", "rule_type", "description", "logic", "business_impact", "acceptance_criteria", "source_file", "source_lines", "confidence"}],
            "functional_requirements": [{"id": "FR-…",  "description", "source_br": ["BR-…"], "priority", "verification", "confidence"}],
            "services_and_apis":       [{"id": "SRV-… or API-…", "type": "Service" | "Integration", "name", "function", "callers", "db_objects", "target", "protocol", "migration_target", "error_handling", "confidence"}],
            "data_entities":           [{"id": "ENT-…", "maps_to_table", "key_columns", "stored_procedures", "sql_query_patterns", "jpa_lookup", "sources", "confidence"}],
            "screens":                 [{"id": "SCR-…", "screen", "route", "roles", "key_fields", "buttons", "source", "confidence"}],
            "workflows":               [{"id": "WF-…",  "entity", "states", "transitions", "events_fired", "confidence"}]}]}

Reply with ONE JSON object and nothing else:
{"layout": "per_file" | "flat",
 "module": "<dot path to the module name, or null>",
 "files": "<per_file only: dot path from the root to the list of per-ASP-file objects; use [] to step into a list, e.g. \\"modules[].pages\\"; \\"\\" when the root is that list>",
 "file_name": "<per_file: key in each file object holding the ASP file name. flat: key in each entry naming its ASP file>",
 "source_path": "<key holding the ASP file path, or null>",
 "lists": {"business_rules":          {"path": "<key or dot path of the source list>", "fields": {"<target field>": "<source key>"}},
           "functional_requirements": {...}, "services": {...}, "apis": {...},
           "data_entities": {...}, "screens": {...}, "workflows": {...}},
 "service_type": {"field": "<key that tells a service from an API when both are in one list, or null>",
                  "service": ["<values meaning service>"], "integration": ["<values meaning API or integration>"]}}

Rules:
- "per_file": the source groups its entries under one object per ASP file. List paths are relative to that object.
- "flat": the source has top-level lists and every entry names its ASP file. List paths are relative to the root, and may add "file_name" inside a list spec when that list uses a different key.
- In "fields" list only the target fields whose source key has a different name. Fields with the same name are copied as they are.
- When services and APIs are in one source list, give that same path for both "services" and "apis" and fill "service_type".
- Leave a list out when the source has nothing for it. Never invent a path that is not in the outline."""


def propose_red_mapping(data: Any, complete: Completer) -> dict:
    """Ask the model where the RED fields are in an upload of unknown layout."""
    sample = json.dumps(outline(data), indent=1, ensure_ascii=False)
    if len(sample) > _OUTLINE_MAX_CHARS:
        sample = json.dumps(outline(data, max_items=1, max_str=40), indent=1, ensure_ascii=False)
    sample = sample[:_OUTLINE_MAX_CHARS]
    user = ("Outline of the uploaded JSON (lists are cut to their first items, strings are cut short):\n\n"
            f"{sample}\n\nReturn the mapping object.")
    return _first_json_object(complete(_MAPPING_SYSTEM, user))


def _walk(obj: Any, path: str | None) -> list[Any]:
    """Values at a dot path. A segment ending in [] steps into every list item."""
    if path is None:
        return []
    values = [obj]
    for segment in [s for s in str(path).split(".") if s != ""]:
        flatten = segment.endswith("[]")
        key = segment[:-2] if flatten else segment
        step: list[Any] = []
        for value in values:
            if key:
                if not isinstance(value, dict) or key not in value:
                    continue
                value = value[key]
            if flatten:
                if isinstance(value, list):
                    step.extend(value)
            else:
                step.append(value)
        values = step
    return values


def _as_list(obj: Any, path: str | None) -> list[dict]:
    """Dict entries of the list(s) at `path` (a final [] is implied)."""
    out: list[dict] = []
    for value in _walk(obj, path):
        if isinstance(value, list):
            out.extend(v for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            out.append(value)
    return out


def _scalar(obj: Any, path: str | None) -> Any:
    values = _walk(obj, path) if path else []
    return values[0] if values else None


def _basename(value: Any) -> str:
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _mapped(entry: dict, fields: dict | None) -> dict:
    """The entry with its own keys kept and the renamed target fields added."""
    out = dict(entry)
    for target, source in (fields or {}).items():
        if isinstance(source, str) and source in entry:
            out[target] = entry[source]
    return out


def _service_type(entry: dict, list_name: str, spec: dict | None) -> str:
    """'Service' or 'Integration' for one services/apis entry."""
    spec = spec or {}
    field_ = spec.get("field")
    if field_ and entry.get(field_) is not None:
        value = str(entry[field_]).strip().lower()
        if value in {str(v).strip().lower() for v in spec.get("service") or []}:
            return "Service"
        if value in {str(v).strip().lower() for v in spec.get("integration") or []}:
            return "Integration"
    prefix = str(entry.get("id") or "").split("-")[0].upper()
    if prefix in ("SRV", "CMP"):
        return "Service"
    if prefix == "API":
        return "Integration"
    return "Service" if list_name == "services" else "Integration"


def apply_red_mapping(data: Any, mapping: dict) -> dict:
    """Build a RED JSON from `data` by following `mapping`. Pure and deterministic."""
    lists = mapping.get("lists") or {}
    if not isinstance(lists, dict):
        raise ValueError("mapping.lists must be an object")
    service_spec = mapping.get("service_type") if isinstance(mapping.get("service_type"), dict) else {}
    same_source = (
        isinstance(lists.get("services"), dict) and isinstance(lists.get("apis"), dict)
        and lists["services"].get("path") == lists["apis"].get("path")
    )

    def _fill(file_obj: dict, source: Any, list_name: str, spec: dict, keep) -> None:
        for entry in _as_list(source, spec.get("path")):
            if not keep(entry):
                continue
            row = _mapped(entry, spec.get("fields"))
            if list_name in ("services", "apis"):
                kind = _service_type(row, list_name, service_spec)
                if same_source and kind != ("Service" if list_name == "services" else "Integration"):
                    continue
                row["type"] = kind
                file_obj["services_and_apis"].append(row)
            else:
                file_obj[list_name].append(row)

    def _empty(name: str, source_path: Any = "") -> dict:
        file_obj: dict[str, Any] = {"file_name": name, "source_path": str(source_path or name)}
        file_obj.update({k: [] for k in RED_LISTS})
        return file_obj

    files: list[dict] = []
    if str(mapping.get("layout") or "per_file").lower() == "flat":
        by_page: dict[str, dict] = {}
        for list_name in MAPPING_LISTS:
            spec = lists.get(list_name)
            if not isinstance(spec, dict):
                continue
            file_key = spec.get("file_name") or mapping.get("file_name")
            for entry in _as_list(data, spec.get("path")):
                name = _basename(entry.get(file_key)) if file_key else ""
                if not name:
                    continue
                norm = normalise_page(name)
                if norm not in by_page:
                    by_page[norm] = _empty(name, entry.get(mapping.get("source_path") or "") or name)
                    files.append(by_page[norm])
                _fill(by_page[norm], [entry], list_name, {**spec, "path": ""}, lambda _e: True)
    else:
        for source in _as_list(data, mapping.get("files") or ""):
            name = _basename(_scalar(source, mapping.get("file_name") or "file_name"))
            if not name:
                continue
            file_obj = _empty(name, _scalar(source, mapping.get("source_path")) or name)
            for key in ("layer", "risk_level", "loc", "cyclomatic_complexity", "business_domain"):
                if key in source:
                    file_obj[key] = source[key]
            for list_name in MAPPING_LISTS:
                spec = lists.get(list_name)
                if isinstance(spec, dict):
                    _fill(file_obj, source, list_name, spec, lambda _e: True)
            files.append(file_obj)

    module = _scalar(data, mapping.get("module")) if mapping.get("module") else None
    if module is None and isinstance(data, dict):
        module = data.get("module")
    return {
        "module": str(module or ""),
        "source_project": str(data.get("source_project", "")) if isinstance(data, dict) else "",
        "id_convention": str(data.get("id_convention", "")) if isinstance(data, dict) else "",
        "files": files,
    }


def normalise_with_fallback(
    data: Any, *, complete: Completer | None = None, settings=None,
) -> dict[str, Any]:
    """Normalise a module JSON, using the model only when procedural parsing fails.

    Returns {"doc", "violations", "warnings", "parsed_by", "red_json", "mapping", "notes"}:
      parsed_by  "procedural" | "llm-fallback"
      red_json   the RED JSON the fallback built (store this, so the build reads
                 a file procedural code understands); None otherwise
      notes      what the fallback did or why it could not help
    """
    doc, violations, warnings = normalise(data)
    result: dict[str, Any] = {"doc": doc, "violations": violations, "warnings": warnings,
                              "parsed_by": "procedural", "red_json": None, "mapping": None,
                              "notes": []}
    if not violations:
        return result
    if complete is None:
        if not enabled(settings):
            result["notes"].append("LLM fallback is switched off (FE_KB_LLM_FALLBACK=false)")
            return result
        complete = default_completer(settings)

    try:
        mapping = propose_red_mapping(data, complete)
        red_json = apply_red_mapping(data, mapping)
    except FallbackUnavailable as exc:
        result["notes"].append(f"LLM fallback unavailable: {exc}")
        return result
    except Exception as exc:  # noqa: BLE001 — a bad mapping must not hide the real violations
        logger.warning("LLM fallback could not map the upload: %s", exc)
        result["notes"].append(f"LLM fallback could not map the upload: {exc}")
        return result

    fb_doc, fb_violations, fb_warnings = normalise(red_json)
    if fb_violations:
        result["notes"].append(
            "LLM fallback proposed a mapping, but the result is still invalid: "
            + "; ".join(fb_violations[:5]))
        result["mapping"] = mapping
        return result

    cards = sum(len(f.get(k, [])) for f in red_json["files"] for k in RED_LISTS)
    note = (f"parsed with the LLM fallback: the upload was not in the RED JSON layout "
            f"({violations[0]}); a model proposed a field mapping and code applied it "
            f"({len(red_json['files'])} ASP file(s), {cards} entries). Review the mapping.")
    return {"doc": fb_doc, "violations": [], "warnings": [note, *fb_warnings],
            "parsed_by": "llm-fallback", "red_json": red_json, "mapping": mapping, "notes": [note]}


# ---------------------------------------------------------------------------
# Screens document
# ---------------------------------------------------------------------------

_BIND_SYSTEM = """You read a walkthrough document of a legacy ASP application. Each screenshot in it shows \
one ASP page (sometimes two). For every screenshot listed, decide which ASP file it shows, using only the \
text printed before and after it.

Reply with ONE JSON object and nothing else:
{"bindings": [{"image": <number>, "pages": ["<File>.asp"], "reason": "<a few words>"}]}

Rules:
- Use a file name only when the text supports it. If the text does not say, return "pages": [].
- Prefer names from the list of known pages; spell them exactly as listed.
- A screenshot may show two pages when the text names both."""


def bind_unbound_images(
    images: list[dict], known_pages: list[str] | None = None,
    *, complete: Completer | None = None, settings=None,
) -> list[str]:
    """Bind pictures no file-name line claimed, in place. Returns notes.

    `images` is the list from _docx_extract_images(); an entry with an empty
    page_set is unbound. A bound entry gets page_set / page_norm_set and
    bound_by="llm". Procedural bindings are never changed.
    """
    unbound = [(n, img) for n, img in enumerate(images, start=1) if not img.get("page_set")]
    if not unbound:
        return []
    if complete is None:
        if not enabled(settings):
            return []
        complete = default_completer(settings)

    known = [p for p in (known_pages or []) if p]
    known_norm = {normalise_page(p): p for p in known}
    blocks: list[str] = []
    for number, img in unbound:
        blocks.append(
            f"Screenshot {number}\n"
            f"  text before: {str(img.get('context_before') or '(none)')[:900]}\n"
            f"  text after: {str(img.get('context_after') or '(none)')[:500]}")
    user = (f"Known pages: {', '.join(known) if known else '(none given)'}\n\n"
            + "\n\n".join(blocks) + "\n\nReturn the bindings object.")

    try:
        reply = _first_json_object(complete(_BIND_SYSTEM, user))
    except FallbackUnavailable as exc:
        return [f"LLM fallback unavailable for {len(unbound)} unbound picture(s): {exc}"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM fallback could not bind pictures: %s", exc)
        return [f"LLM fallback could not bind {len(unbound)} unbound picture(s): {exc}"]

    by_number = {number: img for number, img in unbound}
    notes: list[str] = []
    for item in reply.get("bindings") or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("image"))
        except (TypeError, ValueError):
            continue
        img = by_number.get(number)
        if img is None:
            continue
        nearby = f"{img.get('context_before') or ''} {img.get('context_after') or ''}"
        nearby_norm = {normalise_page(t) for t in _ASP_RE.findall(nearby)}
        pages: list[str] = []
        for name in item.get("pages") or []:
            norm = normalise_page(_basename(name))
            if not norm.endswith(".asp"):
                continue
            # A name must be a known page or be printed next to the picture.
            if norm in known_norm:
                spelled = known_norm[norm]
            elif norm in nearby_norm:
                spelled = _basename(name)
            else:
                notes.append(f"picture {number}: the model named '{name}', which is neither a known "
                             f"page nor in the text around the picture; ignored")
                continue
            if spelled not in pages:
                pages.append(spelled)
        if pages:
            img["page_set"] = pages
            img["page_norm_set"] = [normalise_page(p) for p in pages]
            img["bound_by"] = "llm"
            notes.append(f"picture {number} bound to {', '.join(pages)} by the LLM fallback "
                         f"({str(item.get('reason') or 'no reason given')[:120]}); review")
    return notes

"""Screen catalogue builder for document_kind=screens intake.

Processes images extracted from the screens Word document and produces a
screens-catalog.json that maps ASP page names → images.

The binding rule matches §1.2 of IMPLEMENTATION-PLAN-card-pipeline-v1.md,
verified against the real Admin-Company_Search.docx:

  - Walk paragraphs in document order
  - A paragraph matching FILE_NAME_RE opens a current page set (one picture may
    cover two pages: "UpdateCompany.asp and DeleteCompany.asp")
  - Pictures bind to the current page set; a picture before any file-name line
    is recorded as unbound
  - Page names are normalised (strip path prefix, lower-case) before matching
    against the cards JSON 'page' field; the catalogue is keyed by that
    normalised name, so "adminCompanySearch.asp" and "AdminCompanySearch.asp"
    are one page
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from app.agentic_platform.fe_core.kb.cards_schema import normalise_page

# Matches: "File name - /path/CompanySelect.asp"
#          "File Name- AdminCompanySearch.asp"
#          "File – NewCompany.asp"
FILE_NAME_RE = re.compile(
    r"(?i)file\s*(?:name)?\s*[-–:]\s*(?:/\S+?/)?([A-Za-z0-9_]+\.asp)"
)


def _normalise_page(name: str) -> str:
    """Strip path prefix and lower-case for join key matching."""
    return normalise_page(name)


def _image_file(img: dict[str, Any]) -> str:
    """File name of an extracted image, relative to its catalogue version folder."""
    return str(img.get("file") or Path(str(img.get("local_path") or "")).name)


def build_catalog(
    images_json: list[dict[str, Any]],
    app: str = "",
    version: int = 1,
) -> dict[str, Any]:
    """Group extracted images into a screens catalogue.

    images_json is the list of dicts produced by _docx_extract_images():
    each entry has {local_path, page_set (list[str]), capture_order, width, height, sha256}.

    Returns a catalog dict:
      {
        "app": app, "version": version, "captured": "YYYY-MM-DD",
        "screens": [
          {"page": "CompanySelect.asp",            # first spelling seen
           "page_norm": "companyselect.asp",       # normalised, the join key
           "spellings": ["CompanySelect.asp"],     # every spelling in the document
           "images": [{"file": "...", "version": N, "sha256": "...", "order": 1, ...}]}
        ],
        "unbound": [{"sha256": "...", "order": N}],   # before first file-name line
        "report": [...]                               # issues for human review
      }
    """
    screens: dict[str, dict] = {}  # page_norm → {page, page_norm, spellings, images[]}
    unbound: list[dict] = []
    report: list[str] = []

    for img in images_json:
        page_set: list[str] = img.get("page_set", [])
        if not page_set:
            unbound.append({"sha256": img.get("sha256", ""), "order": img.get("capture_order", 0),
                            "local_path": img.get("local_path", ""), "file": _image_file(img),
                            "version": version})
            report.append(f"image {img.get('capture_order','?')} is unbound (appears before any file-name line)")
            continue

        # One picture covering multiple pages: record once, bind to each page
        norms = []
        for page_orig in page_set:
            norm = _normalise_page(page_orig)
            if norm not in norms:
                norms.append(norm)
            sc = screens.setdefault(
                norm, {"page": page_orig, "page_norm": norm, "spellings": [], "images": []})
            if page_orig not in sc["spellings"]:
                sc["spellings"].append(page_orig)
        for norm in norms:
            sc = screens[norm]
            sc["images"].append({
                "key": img.get("s3_key", ""),
                "local_path": img.get("local_path", ""),
                "file": _image_file(img),
                "version": version,
                "sha256": img.get("sha256", ""),
                "order": len(sc["images"]) + 1,
                "width": img.get("width", 0),
                "height": img.get("height", 0),
                "shared": len(norms) > 1,
                "bound_by": img.get("bound_by", "file-name line"),
            })
        if img.get("bound_by") == "llm":
            report.append(f"picture {img.get('capture_order', '?')} was bound to "
                          f"{', '.join(page_set)} by the LLM fallback; review")

    for sc in screens.values():
        # A picture placed by the LLM fallback never outranks one a file-name
        # line placed: the first picture of a page is the screen card's main one.
        sc["images"].sort(key=lambda image: image.get("bound_by") == "llm")
        for position, image in enumerate(sc["images"], start=1):
            image["order"] = position
        if len(sc["images"]) > 1:
            report.append(f"page '{sc['page']}' has {len(sc['images'])} pictures")
        if len(sc["spellings"]) > 1:
            report.append(f"page '{sc['page']}' is spelled {len(sc['spellings'])} ways: "
                          f"{', '.join(sc['spellings'])}")

    return {
        "app": app,
        "version": version,
        "captured": str(date.today()),
        "screens": list(screens.values()),
        "unbound": unbound,
        "report": report,
    }


def merge_catalogs(catalogs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge every catalogue version into one. Per page the highest version wins.

    Each image keeps the `version` it was extracted under, which is the folder
    (v<N>/) its file lives in. Catalogues written before entries were keyed by
    normalised page name are folded on the way in.
    """
    if not catalogs:
        return {}

    def _version(cat: dict) -> int:
        try:
            return int(cat.get("version") or 0)
        except (TypeError, ValueError):
            return 0

    ordered = sorted(catalogs, key=_version)
    pages: dict[str, dict] = {}
    unbound: list[dict] = []
    report: list[str] = []
    for cat in ordered:
        version = _version(cat)
        this: dict[str, dict] = {}
        for sc in cat.get("screens", []):
            norm = sc.get("page_norm") or _normalise_page(sc.get("page", ""))
            norm = _normalise_page(norm)
            target = this.setdefault(
                norm, {"page": sc.get("page", ""), "page_norm": norm, "spellings": [],
                       "images": [], "version": version})
            for spelling in sc.get("spellings") or [sc.get("page", "")]:
                if spelling and spelling not in target["spellings"]:
                    target["spellings"].append(spelling)
            for img in sc.get("images", []):
                target["images"].append({
                    **img, "file": _image_file(img),
                    "version": img.get("version") or version,
                    "order": len(target["images"]) + 1,
                })
        for norm, sc in this.items():
            previous = pages.get(norm)
            if previous is not None and previous["version"] != version:
                report.append(f"page '{sc['page']}': pictures from screens v{version} "
                              f"replace those from v{previous['version']}")
            pages[norm] = sc
        unbound.extend({**u, "version": u.get("version") or version} for u in cat.get("unbound", []))
        report.extend(r for r in cat.get("report", []) if r not in report)

    latest = ordered[-1]
    return {
        "app": latest.get("app", ""),
        "version": _version(latest),
        "versions": [_version(c) for c in ordered],
        "captured": latest.get("captured", ""),
        "screens": list(pages.values()),
        "unbound": unbound,
        "report": report,
    }


def lookup_by_page(catalog: dict[str, Any], page_name: str) -> dict | None:
    """Find a catalog entry by normalised page name (case-insensitive, no path prefix)."""
    needle = _normalise_page(page_name)
    found = [sc for sc in catalog.get("screens", [])
             if _normalise_page(sc.get("page_norm") or sc.get("page", "")) == needle]
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    # A catalogue written by an older build may hold one entry per spelling.
    images: list[dict] = []
    for sc in found:
        for img in sc.get("images", []):
            images.append({**img, "order": len(images) + 1})
    return {**found[0], "images": images}


def _binding(scr_id: str, img: dict[str, Any], match: str, **extra: Any) -> dict[str, Any]:
    return {
        "scr_id": scr_id, "local_path": img.get("local_path", ""),
        "file": _image_file(img), "version": img.get("version"),
        "s3_key": img.get("key", ""), "sha256": img.get("sha256", ""),
        "order": img.get("order", 1), "match": match, **extra,
    }


def bind(
    scr_cards: list[dict[str, Any]],
    catalog_entry: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Match a list of SCR cards for one ASP page to the catalogue entry.

    Binding priority:
    1. An explicit SCR id in the image caption text wins.
    2. Single SCR on the page → it gets every image of the page.
    3. Multiple SCRs → bind by capture_order; extras are shared=true.
    4. No catalogue entry → no screenshot bound.

    Returns a list of binding dicts:
      {"scr_id": ..., "local_path": ..., "file": ..., "version": N, "sha256": ..., "order": N}
    """
    if not catalog_entry or not catalog_entry.get("images"):
        return []

    images = catalog_entry["images"]
    bindings: list[dict] = []

    # Priority 1: explicit caption match
    for img in images:
        caption = img.get("caption", "") or ""
        for scr in scr_cards:
            scr_id = scr.get("id", "")
            if scr_id and scr_id in caption:
                bindings.append(_binding(scr_id, img, "caption"))
        if bindings:
            return bindings

    # Priority 2: single SCR
    if len(scr_cards) == 1:
        scr_id = scr_cards[0].get("id", "")
        return [_binding(scr_id, img, "single") for img in images]

    # Priority 3: by capture_order
    for idx, scr in enumerate(scr_cards):
        scr_id = scr.get("id", "")
        img = images[idx] if idx < len(images) else images[-1]
        bindings.append(_binding(scr_id, img, "order", shared=idx >= len(images)))
    return bindings

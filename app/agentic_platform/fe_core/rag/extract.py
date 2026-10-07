"""Turn an uploaded file into text the chunker can use.

A reverse-engineering report arrives as `.docx` -- 994 paragraphs and 99 tables in
the report this was written against. Refusing it as "not UTF-8" was correct about
the bytes and useless about the intent, so the container is unwrapped here rather
than at each call site.

Tables are rendered as Markdown pipes deliberately: both the markdown and tabular
chunkers understand that shape, so a table keeps its header when it is split. A
RED report is mostly tables, so losing them would lose most of the content.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"


class UnreadableUpload(ValueError):
    """The file cannot be turned into text. Surfaced as 422."""


#: Formats the intake path accepts, and how each becomes canonical Markdown.
SUPPORTED_SUFFIXES = (".docx", ".pdf", ".xlsx", ".xlsm", ".csv", ".pptx", ".drawio",
                      ".md", ".markdown", ".txt", ".html", ".htm", ".json", ".yaml", ".yml")


def extract_text(data: bytes, filename: str) -> tuple[str, str]:
    """Return (canonical_markdown, how). Raises UnreadableUpload when there is no text.

    Every container format is unwrapped to Markdown so the same chunkers, the
    same prompt inlining and the same evidence-citation rules apply regardless
    of how the document arrived. Tables become pipe tables; diagrams become a
    node/edge listing; spreadsheets become one pipe table per sheet.
    """
    suffix = Path(filename or "").suffix.lower()

    if suffix == ".pdf" or data[:5] == PDF_MAGIC:
        return _pdf(data, filename), "pdf"
    if suffix in (".xlsx", ".xlsm"):
        return _xlsx(data, filename), "xlsx"
    if suffix == ".pptx":
        return _pptx(data, filename), "pptx"
    if suffix == ".drawio" or (suffix in (".xml",) and b"<mxfile" in data[:2000]):
        return _drawio(data, filename), "drawio"
    if suffix == ".csv":
        return _csv(data, filename), "csv"
    if suffix == ".docx" or data[:4] == ZIP_MAGIC:
        return _docx(data, filename), "docx"
    if suffix in (".html", ".htm"):
        return _html(data, filename), "html"

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnreadableUpload(
            f"'{filename}' is neither UTF-8 text nor a supported document "
            f"({', '.join(SUPPORTED_SUFFIXES)}). Ingesting undecodable bytes would put "
            "replacement characters into the corpus, and retrieval would later cite "
            "them as grounding."
        ) from exc
    return text, "utf-8"


# ---------------------------------------------------------------------------
# additional formats (Phase 2 intake)
# ---------------------------------------------------------------------------
def _pdf(data: bytes, filename: str) -> str:
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise UnreadableUpload("reading .pdf needs pypdf. Run: pip install pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableUpload(f"'{filename}' is not a readable PDF: {exc}") from exc
    parts: list[str] = [f"# {Path(filename).stem}"]
    empty = 0
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            empty += 1
            continue
        parts.append(f"\n## Page {i}\n\n{text}")
    if len(parts) == 1:
        raise UnreadableUpload(
            f"'{filename}' contains no extractable text ({empty} page(s) look like scans). "
            "Run OCR or export a text PDF / .docx first."
        )
    if empty:
        parts.append(f"\n> {empty} page(s) had no extractable text (likely images).")
    return "\n".join(parts)


def _table_md(rows: list[list[str]]) -> str:
    rows = [[("" if c is None else str(c)).replace("|", "\\|").replace("\n", " ").strip() for c in r] for r in rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _xlsx(data: bytes, filename: str) -> str:
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise UnreadableUpload("reading .xlsx needs openpyxl. Run: pip install openpyxl") from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise UnreadableUpload(f"'{filename}' is not a readable workbook: {exc}") from exc
    parts = [f"# {Path(filename).stem}"]
    for ws in wb.worksheets:
        rows: list[list[str]] = []
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(v.strip() for v in vals):
                rows.append(vals)
        if not rows:
            continue
        parts.append(f"\n## Sheet: {ws.title}\n\n{_table_md(rows)}")
    if len(parts) == 1:
        raise UnreadableUpload(f"'{filename}' has no non-empty sheets.")
    return "\n".join(parts)


def _csv(data: bytes, filename: str) -> str:
    import csv  # noqa: PLC0415
    text = data.decode("utf-8-sig", errors="replace")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise UnreadableUpload(f"'{filename}' is empty.")
    return f"# {Path(filename).stem}\n\n{_table_md(rows)}"


def _pptx(data: bytes, filename: str) -> str:
    try:
        from pptx import Presentation  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise UnreadableUpload("reading .pptx needs python-pptx. Run: pip install python-pptx") from exc
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableUpload(f"'{filename}' is not a readable presentation: {exc}") from exc
    parts = [f"# {Path(filename).stem}"]
    for i, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                lines.extend(t for t in shape.text_frame.text.splitlines() if t.strip())
            if getattr(shape, "has_table", False):
                rows = [[c.text for c in r.cells] for r in shape.table.rows]
                lines.append(_table_md(rows))
        if lines:
            parts.append(f"\n## Slide {i}\n\n" + "\n".join(lines))
    if len(parts) == 1:
        raise UnreadableUpload(f"'{filename}' contains no text.")
    return "\n".join(parts)


def _drawio(data: bytes, filename: str) -> str:
    """Draw.io: one section per diagram tab, listing shapes (labels) and edges.

    Compressed diagrams (base64 + raw deflate + URL-encoding) are inflated.
    """
    import base64  # noqa: PLC0415
    import html as _html_mod  # noqa: PLC0415
    import re  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import zlib  # noqa: PLC0415
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnreadableUpload(f"'{filename}' is not a readable draw.io/XML file: {exc}") from exc

    def _plain(v: str) -> str:
        v = _html_mod.unescape(v or "")
        v = re.sub(r"<br\s*/?>", " ", v)
        v = re.sub(r"<[^>]+>", " ", v)
        return re.sub(r"\s+", " ", v).strip()

    parts = [f"# {Path(filename).stem}"]
    diagrams = root.findall(".//diagram") or [root]
    for idx, dia in enumerate(diagrams, start=1):
        name = dia.get("name") or f"Diagram {idx}"
        model = dia.find(".//mxGraphModel")
        if model is None and (dia.text or "").strip():
            try:
                raw = base64.b64decode(dia.text.strip())
                inflated = zlib.decompress(raw, -15).decode("utf-8")
                model = ET.fromstring(urllib.parse.unquote(inflated))
            except Exception:  # noqa: BLE001
                model = None
        if model is None:
            continue
        cells = {c.get("id"): c for c in model.iter("mxCell")}
        nodes, edges = [], []
        for c in cells.values():
            label = _plain(c.get("value", ""))
            if c.get("edge") == "1":
                src = _plain(cells.get(c.get("source"), ET.Element("x")).get("value", "")) or c.get("source", "?")
                dst = _plain(cells.get(c.get("target"), ET.Element("x")).get("value", "")) or c.get("target", "?")
                edges.append(f"- {src} -> {dst}" + (f" : {label}" if label else ""))
            elif c.get("vertex") == "1" and label:
                nodes.append(f"- {label}")
        parts.append(f"\n## Tab: {name}\n\n### Shapes\n" + ("\n".join(nodes) or "- (none)")
                     + "\n\n### Connections\n" + ("\n".join(edges) or "- (none)"))
    if len(parts) == 1:
        raise UnreadableUpload(f"'{filename}' has no readable diagrams.")
    return "\n".join(parts)


def _html(data: bytes, filename: str) -> str:
    import html as _html_mod  # noqa: PLC0415
    import re  # noqa: PLC0415
    text = data.decode("utf-8-sig", errors="replace")
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if not text:
        raise UnreadableUpload(f"'{filename}' contains no text.")
    return text


def _heading_level(style_name: str) -> int | None:
    """Markdown level for a Word style, or None when it is body text."""
    lowered = (style_name or "").lower()
    if lowered.startswith("title"):
        return 1
    if lowered.startswith("heading"):
        digits = "".join(c for c in lowered if c.isdigit())
        return min(int(digits), 6) if digits else 2
    return None


def _docx(data: bytes, filename: str) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise UnreadableUpload(
            "reading .docx needs python-docx. Run: pip install python-docx"
        ) from exc

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableUpload(
            f"'{filename}' is not a readable .docx: {exc}"
        ) from exc

    parts: list[str] = []
    table_count = 0

    # Build O(1) lookup maps to avoid O(n²) iteration
    para_map = {p._element: p for p in document.paragraphs}
    table_map = {t._tbl: t for t in document.tables}

    # Process elements in document order to preserve module→table relationships
    for element in document.element.body:
        tag = element.tag.split('}')[-1]  # Remove namespace

        if tag == 'p':  # Paragraph
            para = para_map.get(element)
            if para:
                text = para.text.strip()
                if not text:
                    continue
                level = _heading_level(para.style.name if para.style else "")
                # A Word heading becomes a Markdown heading, which is what gives the
                # markdown chunker its heading trail -- without it every chunk from an
                # 84,000-character report would be unattributed prose.
                parts.append(f"{'#' * level} {text}" if level else text)

        elif tag == 'tbl':  # Table
            table = table_map.get(element)
            if table:
                table_count += 1
                rows: list[list[str]] = []
                # `row.cells` rebuilds the whole table's merged-cell grid on every call,
                # which is quadratic in the row count and pinned the event loop for
                # minutes on a real RED report. Walk the raw <w:tc> elements once instead;
                # merged cells are then repeated per source column, which is fine for a
                # Markdown rendering.
                for tr in table._tbl.tr_lst:
                    cells = [
                        "\n".join(p.text for p in tc.p_lst).strip().replace("|", r"\|")
                        for tc in tr.tc_lst
                    ]
                    if any(cells):
                        rows.append(cells)
                if not rows:
                    continue
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]

                parts.append("")
                parts.append(f"### Table {table_count}")
                parts.append("| " + " | ".join(rows[0]) + " |")
                parts.append("|" + "|".join(["---"] * width) + "|")
                for row in rows[1:]:
                    parts.append("| " + " | ".join(row) + " |")

    text = "\n\n".join(parts).strip()
    if not text:
        raise UnreadableUpload(
            f"'{filename}' opened as .docx but contained no text. It may be a scan, "
            "which needs OCR rather than extraction."
        )
    logger.info(
        "Extracted %d chars from %s (%d paragraphs, %d tables)",
        len(text), filename, len(document.paragraphs), len(document.tables),
    )
    return text


# ---------------------------------------------------------------------------
# Image extraction for document_kind=screens intake (WP1)
# ---------------------------------------------------------------------------
_FILE_NAME_RE = re.compile(
    r"(?i)file\s*(?:name)?\s*[-–:]\s*(?:/\S+?/)?([A-Za-z0-9_]+\.asp)"
)


def _docx_extract_images(
    raw: bytes,
    filename: str,
    dest_dir: Path | None = None,
) -> list[dict]:
    """Walk a screens Word document and return image metadata dicts.

    Each dict: {local_path, page_set, capture_order, width, height, sha256}.

    Algorithm (§1.2 of IMPLEMENTATION-PLAN-card-pipeline-v1.md):
    - Walk paragraphs in document order
    - FILE_NAME_RE match opens the current page set (one line may name two pages)
    - Pictures bind to the current page set; pictures before any file-name line are unbound
    - Page names normalised (strip path prefix, lower-case) are stored in page_norm_set
    """
    import hashlib  # noqa: PLC0415
    import zipfile  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    try:
        from docx import Document  # noqa: PLC0415
        from docx.oxml.ns import qn, nsmap  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise UnreadableUpload("reading .docx needs python-docx. Run: pip install python-docx") from exc

    # Register VML namespace if not already present (required for legacy VML images in older Word docs)
    if 'v' not in nsmap:
        nsmap['v'] = 'urn:schemas-microsoft-com:vml'

    try:
        document = Document(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableUpload(f"'{filename}' is not a readable .docx: {exc}") from exc

    # Build relationship map: rId → media part name
    rels: dict[str, str] = {}
    for rel in document.part.rels.values():
        if "image" in rel.reltype:
            rels[rel.rId] = rel.target_ref  # e.g. "media/image4.png"

    # Read all media parts from the zip
    media_bytes: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                media_bytes[name.split("word/")[-1]] = zf.read(name)  # key: "media/imageN.xxx"

    if dest_dir:
        dest_dir.mkdir(parents=True, exist_ok=True)

    current_pages: list[str] = []  # original spellings
    page_capture_count: dict[str, int] = {}
    results: list[dict] = []

    def _extract_para_images(para_elem) -> list[str]:
        """Return list of rIds for all blip fills inside a paragraph element."""
        rids = []
        for blip in para_elem.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
            if rid:
                rids.append(rid)
        # Also handle w:pict / v:imagedata
        for imgdata in para_elem.iter(qn("v:imagedata")):
            rid = imgdata.get(qn("r:id"))
            if rid:
                rids.append(rid)
        return rids

    para_map = {p._element: p for p in document.paragraphs}
    para_texts: list[str] = []  # every body paragraph in order, for picture context

    for element in document.element.body:
        tag = element.tag.split("}")[-1]
        if tag != "p":
            continue
        para = para_map.get(element)
        if para is None:
            continue

        text = para.text.strip()
        para_texts.append(text)

        # Check for file-name lines
        matches = list(_FILE_NAME_RE.finditer(text or ""))
        if matches:
            current_pages = [m.group(1) for m in matches]
            # "File - X.asp and Y.asp": every other .asp name on a file-name
            # line joins the page set, so one picture can cover both pages.
            named = {p.lower() for p in current_pages}
            for token in _re.findall(r"([A-Za-z0-9_]+\.asp)\b", text, flags=_re.I):
                if token.lower() not in named:
                    current_pages.append(token)
                    named.add(token.lower())
            for p in current_pages:
                page_capture_count.setdefault(p.lower(), 0)

        # Collect images in this paragraph
        rids = _extract_para_images(element)
        for rid in rids:
            media_path = rels.get(rid, "")
            media_data = media_bytes.get(media_path, b"")
            if not media_data:
                continue

            sha = hashlib.sha256(media_data).hexdigest()

            # Try to get dimensions
            width, height = 0, 0
            try:
                from PIL import Image as _PILImage  # noqa: PLC0415
                img = _PILImage.open(io.BytesIO(media_data))
                width, height = img.size

                # Convert EMF/WMF to PNG
                if media_path.lower().endswith((".emf", ".wmf")):
                    import os  # noqa: PLC0415
                    if os.environ.get("FE_REJECT_EMF", "").lower() in ("1", "true"):
                        raise UnreadableUpload(
                            f"'{filename}': EMF/WMF image in screens document. "
                            "Export as PNG before uploading (FE_REJECT_EMF=true)."
                        )
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    media_data = buf.getvalue()
                    media_path = _re.sub(r"\.(emf|wmf)$", ".png", media_path, flags=_re.I)
                    sha = hashlib.sha256(media_data).hexdigest()
            except ImportError:
                pass
            except UnreadableUpload:
                raise

            # Determine capture order per page. Pages are counted by their
            # lower-case name: the document spells one page several ways, and
            # counting per spelling gave two pictures the same file name on a
            # case-insensitive disk.
            page_set = list(current_pages)
            if not page_set:
                order = len(results) + 1
            else:
                for p in page_set:
                    page_capture_count[p.lower()] = page_capture_count.get(p.lower(), 0) + 1
                order = page_capture_count[page_set[0].lower()]

            # Write to dest_dir
            local_path = ""
            if dest_dir and media_data:
                suffix = Path(media_path).suffix or ".png"
                page_label = _re.sub(r"[^\w]", "_", page_set[0].lower()) if page_set else "unbound"
                img_filename = f"{page_label}_{order}{suffix}"
                out_path = dest_dir / img_filename
                out_path.write_bytes(media_data)
                local_path = str(out_path)

            results.append({
                "local_path": local_path,
                "media_path": media_path,
                "page_set": page_set,
                "page_norm_set": [Path(p).name.lower() for p in page_set],
                "capture_order": order,
                "width": width,
                "height": height,
                "sha256": sha,
                "s3_key": "",  # filled in by the router after S3 upload
                "paragraph_index": len(para_texts) - 1,
            })

    # The text around each picture. A picture no file-name line claimed can only
    # be placed by what the document says near it (fe_core.kb.llm_fallback).
    for entry in results:
        idx = entry["paragraph_index"]
        before = [t for t in para_texts[max(0, idx - 16): idx + 1] if t][-6:]
        after = [t for t in para_texts[idx + 1: idx + 13] if t][:3]
        entry["context_before"] = " | ".join(before)[-900:]
        entry["context_after"] = " | ".join(after)[:500]

    logger.info(
        "_docx_extract_images: %d images from %s (%d pages)",
        len(results), filename, len(page_capture_count),
    )
    return results



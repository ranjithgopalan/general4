"""Render a Markdown document to .docx in the UW CR house style.

    python scripts/md_to_docx.py docs/PRD_ADLC_Forward_Engineering.md
    python scripts/md_to_docx.py <in.md> -o <out.docx> --title "..." --subtitle "..."

Style constants match `genlite/.codex_tmp/uw_cr_prd/build_prd.py`, which produced
`UW-CR-Implementation-PRD-V1.docx`, so the two documents sit together in a review
pack without looking like they came from different tools.

Written as a converter rather than a hand-built document because the Markdown is
the source of truth: the PRD changes, and a hand-built .docx would immediately
drift from it. Re-run this instead of editing the .docx.

Supports: ATX headings, pipe tables (with header repeat and page-break control),
fenced code, bullet and numbered lists, blockquotes, horizontal rules, and inline
**bold** / *italic* / `code` / [links](url).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from docx import Document
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
except ImportError:  # pragma: no cover
    sys.exit("python-docx is required:  pip install python-docx")

# --- house style -----------------------------------------------------------
NAVY = "1F3C88"
BLUE = "2E74B5"
DARK_BLUE = "1F4D78"
INK = "1F2937"
MUTED = "5B6573"
LIGHT_GRAY = "F2F4F7"
CODE_BG = "F6F8FA"
CODE_INK = "7A1F5C"
WHITE = "FFFFFF"

TABLE_WIDTH_DXA = 9360
BODY_PT = 10.5
TABLE_PT = 8.8
CODE_PT = 8.6
MONO = "Consolas"
SANS = "Calibri"


# --- low-level docx helpers ------------------------------------------------

def set_font(run, name=SANS, size=BODY_PT, color=INK, bold=None, italic=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), name)
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    return run


def shade(element, fill: str) -> None:
    pr = element.get_or_add_tcPr() if element.tag.endswith("tc") else element.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.append(shd)


def cell_margins(cell, top=60, start=100, bottom=60, end=100) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = OxmlElement(f"w:{tag}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    tc_pr.append(margins)


def repeat_header(row) -> None:
    """Without this a table splitting across pages loses its header."""
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def no_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant = OxmlElement("w:cantSplit")
    cant.set(qn("w:val"), "true")
    tr_pr.append(cant)


def add_hyperlink(paragraph, text: str, url: str) -> None:
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    colour = OxmlElement("w:color")
    colour.set(qn("w:val"), BLUE)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(int(BODY_PT * 2)))
    rpr.append(colour)
    rpr.append(underline)
    rpr.append(size)
    run.append(rpr)
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    link.append(run)
    paragraph._p.append(link)


def add_field(paragraph, code: str) -> None:
    """A real Word field, so page numbers update on open."""
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = code
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    set_font(run, size=8.5, color=MUTED)
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


# --- inline markdown ------------------------------------------------------

_INLINE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<italic>(?<![*\w])\*[^*\n]+\*(?!\*))"
)


def write_inline(paragraph, text: str, *, size=BODY_PT, color=INK,
                 bold=False, italic=False) -> None:
    """Emit runs for one line of Markdown, honouring nested emphasis."""
    pos = 0
    for match in _INLINE.finditer(text):
        if match.start() > pos:
            set_font(paragraph.add_run(_unescape(text[pos:match.start()])),
                     size=size, color=color, bold=bold, italic=italic)
        kind = match.lastgroup
        raw = match.group()
        if kind == "code":
            set_font(paragraph.add_run(raw[1:-1]), name=MONO,
                     size=size - 0.7, color=CODE_INK, bold=bold)
        elif kind == "link":
            label, _, url = raw[1:-1].partition("](")
            if url.startswith(("http://", "https://", "mailto:")):
                add_hyperlink(paragraph, label, url)
            else:
                # A relative path is meaningless in a distributed .docx, so keep
                # the target visible as text rather than emitting a dead link.
                set_font(paragraph.add_run(f"{label} ({url})"),
                         size=size, color=color, bold=bold, italic=italic)
        elif kind == "bold":
            write_inline(paragraph, raw[2:-2], size=size, color=color,
                         bold=True, italic=italic)
        else:
            write_inline(paragraph, raw[1:-1], size=size, color=color,
                         bold=bold, italic=True)
        pos = match.end()
    if pos < len(text):
        set_font(paragraph.add_run(_unescape(text[pos:])),
                 size=size, color=color, bold=bold, italic=italic)


def _unescape(text: str) -> str:
    return text.replace("\\|", "|").replace("\\_", "_").replace("\\*", "*")


def plain(text: str) -> str:
    """Inline Markdown stripped, for measuring column widths."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "").replace("*", "")


# --- block builders -------------------------------------------------------

def add_heading(doc, text: str, level: int):
    level = min(level, 4)
    para = doc.add_paragraph(style=f"Heading {min(level, 3)}")
    size, colour = {1: (17, NAVY), 2: (13.5, BLUE), 3: (11.5, DARK_BLUE),
                    4: (11, DARK_BLUE)}[level]
    write_inline(para, text, size=size, color=colour, bold=True)
    for run in para.runs:
        run.font.size = Pt(size)
        run.font.color.rgb = RGBColor.from_string(colour)
        run.font.bold = True
    if level == 4:
        para.paragraph_format.space_before = Pt(8)
    return para


def add_code_block(doc, lines: list[str], language: str = "") -> None:
    """One shaded single-cell table, so the block cannot be split awkwardly."""
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    cell = table.cell(0, 0)
    cell.width = Inches(6.5)
    shade(cell._tc, CODE_BG)
    cell_margins(cell, top=100, start=140, bottom=100, end=140)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    cell.text = ""
    for index, line in enumerate(lines):
        para = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.line_spacing = 1.0
        # Code is literal: no inline Markdown parsing inside a fence.
        set_font(para.add_run(line if line.strip() else " "),
                 name=MONO, size=CODE_PT, color=INK)
    _thin_borders(table, CODE_BG)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def _thin_borders(table, colour: str) -> None:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "4")
        node.set(qn("w:color"), colour)
        borders.append(node)
    tbl_pr.append(borders)


def add_table(doc, headers: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False

    widths = _column_widths(headers, rows)
    for index, header in enumerate(headers):
        cell = table.cell(0, index)
        cell.text = ""
        cell.width = Inches(widths[index] / 1440)
        shade(cell._tc, NAVY)
        cell_margins(cell)
        para = cell.paragraphs[0]
        para.paragraph_format.space_after = Pt(0)
        write_inline(para, header, size=TABLE_PT + 0.4, color=WHITE, bold=True)
    repeat_header(table.rows[0])

    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        no_row_split(table.rows[-1])
        for index, value in enumerate(row[:len(headers)]):
            cell = cells[index]
            cell.text = ""
            cell.width = Inches(widths[index] / 1440)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            cell_margins(cell)
            if row_index % 2:
                shade(cell._tc, LIGHT_GRAY)
            para = cell.paragraphs[0]
            para.paragraph_format.space_after = Pt(0)
            write_inline(para, value, size=TABLE_PT)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _column_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    """Proportional to content, floored so a narrow column stays readable."""
    count = len(headers)
    weights = []
    for index in range(count):
        cells = [plain(headers[index])] + [
            plain(r[index]) for r in rows if index < len(r)
        ]
        longest = max((len(c) for c in cells), default=1)
        typical = sum(len(c) for c in cells) / max(len(cells), 1)
        weights.append(max(min(longest, 60) * 0.45 + typical * 0.55, 6))
    total = sum(weights)
    floor = TABLE_WIDTH_DXA * 0.055
    widths = [max(TABLE_WIDTH_DXA * w / total, floor) for w in weights]
    scale = TABLE_WIDTH_DXA / sum(widths)
    widths = [int(w * scale) for w in widths]
    widths[-1] += TABLE_WIDTH_DXA - sum(widths)  # exact total
    return widths


def add_list_item(doc, text: str, *, ordered: bool, level: int) -> None:
    if ordered:
        style = "List Number" if level == 0 else "List Number 2"
    else:
        style = "List Bullet" if level == 0 else "List Bullet 2"
    try:
        para = doc.add_paragraph(style=style)
    except KeyError:
        para = doc.add_paragraph(style="List Bullet")
    para.paragraph_format.space_after = Pt(3)
    write_inline(para, text)


def add_rule(doc) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(8)
    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "D6DBE3")
    borders.append(bottom)
    p_pr.append(borders)


def add_quote(doc, text: str) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.left_indent = Inches(0.25)
    para.paragraph_format.space_after = Pt(6)
    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:color"), BLUE)
    left.set(qn("w:space"), "8")
    borders.append(left)
    p_pr.append(borders)
    write_inline(para, text, color=MUTED, italic=True)


# --- document chrome ------------------------------------------------------

def set_styles(doc) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = SANS
    normal.font.size = Pt(BODY_PT)
    normal.font.color.rgb = RGBColor.from_string(INK)
    rpr = normal._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi"):
        rfonts.set(qn(attr), SANS)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.10

    for name, (size, colour, before, after) in {
        "Heading 1": (17, NAVY, 16, 8),
        "Heading 2": (13.5, BLUE, 13, 6),
        "Heading 3": (11.5, DARK_BLUE, 9, 4),
    }.items():
        style = doc.styles[name]
        style.font.name = SANS
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(colour)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    for name in ("List Bullet", "List Bullet 2", "List Number", "List Number 2"):
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.font.name = SANS
        style.font.size = Pt(BODY_PT)


def set_page(doc) -> None:
    for section in doc.sections:
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
        for attr in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
            setattr(section, attr, Inches(1))
        section.header_distance = Inches(0.49)
        section.footer_distance = Inches(0.49)


def set_header_footer(doc, header_text: str, footer_text: str) -> None:
    for section in doc.sections:
        para = section.header.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        para.paragraph_format.space_after = Pt(0)
        set_font(para.add_run(header_text), size=8.5, color=MUTED, bold=True)

        foot = section.footer.paragraphs[0]
        foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(foot.add_run(f"{footer_text}    |    Page "),
                 size=8.5, color=MUTED)
        add_field(foot, "PAGE")
        set_font(foot.add_run(" of "), size=8.5, color=MUTED)
        add_field(foot, "NUMPAGES")


def add_toc(doc, headings: list[tuple[int, str]]) -> None:
    """A static contents list.

    Deliberately not a Word TOC field: that renders as "right-click to update"
    until someone opens it in Word, which looks broken in a PDF export or in a
    viewer that does not calculate fields.
    """
    add_heading(doc, "Contents", 2)
    for level, text in headings:
        if level not in (2, 3):
            continue
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(2)
        para.paragraph_format.left_indent = Inches(0.0 if level == 2 else 0.25)
        set_font(para.add_run(plain(text)), size=BODY_PT - 0.5,
                 color=INK if level == 2 else MUTED, bold=level == 2)
    add_rule(doc)


# --- the converter --------------------------------------------------------

def split_row(line: str) -> list[str]:
    """Split a pipe-table row, honouring escaped \\| inside cells."""
    body = line.strip().strip("|")
    cells, current, index = [], "", 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body) and body[index + 1] == "|":
            current += "\\|"
            index += 2
            continue
        if char == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += char
        index += 1
    cells.append(current.strip())
    return cells


_DIVIDER = re.compile(r"^\|?[\s:|-]+\|[\s:|-]*$")


def convert(md: str, doc) -> list[tuple[int, str]]:
    lines = md.splitlines()
    headings: list[tuple[int, str]] = []
    index, total = 0, len(lines)
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        text = " ".join(pending).strip()
        pending.clear()
        if not text:
            return
        para = doc.add_paragraph()
        write_inline(para, text)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            language = stripped[3:].strip()
            index += 1
            block: list[str] = []
            while index < total and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            add_code_block(doc, block, language)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            add_heading(doc, text, level)
            headings.append((level, text))
            index += 1
            continue

        if stripped in ("---", "***", "___"):
            flush()
            add_rule(doc)
            index += 1
            continue

        if stripped.startswith("|") and index + 1 < total and _DIVIDER.match(lines[index + 1].strip()):
            flush()
            headers = split_row(stripped)
            index += 2
            rows = []
            while index < total and lines[index].strip().startswith("|"):
                rows.append(split_row(lines[index].strip()))
                index += 1
            add_table(doc, headers, rows)
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        ordered = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if bullet or ordered:
            flush()
            match = bullet or ordered
            indent = len(match.group(1).expandtabs(4))
            text = match.group(2).strip()
            # Absorb continuation lines so a wrapped item stays one bullet.
            while index + 1 < total:
                nxt = lines[index + 1]
                if (not nxt.strip() or re.match(r"^\s*([-*+]|\d+[.)])\s", nxt)
                        or re.match(r"^#{1,6}\s", nxt.strip())
                        or nxt.strip().startswith(("|", "```", "---"))):
                    break
                text += " " + nxt.strip()
                index += 1
            add_list_item(doc, text, ordered=bool(ordered), level=1 if indent >= 2 else 0)
            index += 1
            continue

        if stripped.startswith(">"):
            flush()
            add_quote(doc, stripped.lstrip("> ").strip())
            index += 1
            continue

        if not stripped:
            flush()
            index += 1
            continue

        # A metadata line like "**Version:** 2.0" is its own paragraph, not a
        # continuation of the previous one.
        if stripped.startswith("**") and pending:
            flush()
        pending.append(stripped)
        if stripped.startswith("**") and stripped.count("**") >= 2 and ":" in stripped[:40]:
            flush()
        index += 1

    flush()
    return headings


def build(source: Path, out: Path, *, title: str | None,
          subtitle: str | None, toc: bool) -> Path:
    md = source.read_text(encoding="utf-8")
    doc = Document()
    set_styles(doc)
    set_page(doc)

    first = re.match(r"^#\s+(.*)$", md.splitlines()[0].strip()) if md.strip() else None
    doc_title = title or (first.group(1) if first else source.stem.replace("_", " "))
    if first:
        md = "\n".join(md.splitlines()[1:])

    heading = doc.add_paragraph()
    heading.paragraph_format.space_after = Pt(2)
    set_font(heading.add_run(doc_title), size=22, color=NAVY, bold=True)
    if subtitle:
        sub = doc.add_paragraph()
        sub.paragraph_format.space_after = Pt(10)
        set_font(sub.add_run(subtitle), size=11.5, color=MUTED, italic=True)
    add_rule(doc)

    if toc:
        headings = convert(md, Document())  # cheap pre-pass for the contents list
        add_toc(doc, headings)

    convert(md, doc)
    set_header_footer(doc, doc_title, doc_title)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--subtitle", default=None)
    parser.add_argument("--no-toc", action="store_true")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"no such file: {args.source}")
        return 1
    out = args.out or args.source.with_suffix(".docx")
    built = build(args.source, out, title=args.title, subtitle=args.subtitle,
                  toc=not args.no_toc)
    print(f"wrote {built}  ({built.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

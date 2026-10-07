"""File parsing utilities for workspace INTAKE requirement documents.

Supports: .md, .txt (plain text extraction), .pdf (via pdfplumber), .docx (via python-docx).
Returns extracted text + file metadata for requirement_text population + audit trail.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple


class ParsedFile(NamedTuple):
    """Extracted content from a file."""

    filename: str
    file_type: str
    content: str
    char_count: int
    page_count: int | None = None  # For PDFs


def parse_text_file(content: bytes, filename: str) -> ParsedFile:
    """Parse plain-text files (.md, .txt).

    Args:
        content: Raw file bytes
        filename: Original filename (for type detection)

    Returns:
        ParsedFile with extracted text
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # Fallback to latin-1
        text = content.decode("latin-1", errors="replace")

    return ParsedFile(
        filename=filename,
        file_type=Path(filename).suffix.lower().lstrip("."),
        content=text.strip(),
        char_count=len(text),
    )


def parse_docx_file(content: bytes, filename: str) -> ParsedFile:
    """Parse DOCX files via python-docx.

    Args:
        content: Raw file bytes
        filename: Original filename

    Returns:
        ParsedFile with extracted text

    Raises:
        ImportError: If python-docx is not installed
    """
    try:
        from docx import Document
        from io import BytesIO
    except ImportError as e:
        raise ImportError(
            "python-docx is required for DOCX parsing. Install with: pip install python-docx"
        ) from e

    try:
        doc = Document(BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)

        return ParsedFile(
            filename=filename,
            file_type="docx",
            content=text.strip(),
            char_count=len(text),
        )
    except Exception as e:
        raise ValueError(f"Failed to parse DOCX file {filename}: {e}") from e


def parse_pdf_file(content: bytes, filename: str, *, max_pages: int | None = None) -> ParsedFile:
    """Parse PDF files via pdfplumber (pure Python, no C++ compilation).

    Args:
        content: Raw file bytes
        filename: Original filename
        max_pages: Optional cap on pages to extract (None = all)

    Returns:
        ParsedFile with extracted text + page_count

    Raises:
        ImportError: If pdfplumber is not installed
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "pdfplumber is required for PDF parsing. Install with: pip install pdfplumber"
        ) from e

    try:
        from io import BytesIO

        with pdfplumber.open(BytesIO(content)) as pdf:
            total_pages = len(pdf.pages)
            pages_to_extract = min(total_pages, max_pages) if max_pages else total_pages
            texts = []

            for page_num in range(pages_to_extract):
                page = pdf.pages[page_num]
                text = page.extract_text()
                if text and text.strip():
                    texts.append(text)

            full_text = "\n\n".join(texts)

            return ParsedFile(
                filename=filename,
                file_type="pdf",
                content=full_text.strip(),
                char_count=len(full_text),
                page_count=pages_to_extract,
            )
    except Exception as e:
        raise ValueError(f"Failed to parse PDF file {filename}: {e}") from e


def parse_file(content: bytes, filename: str, *, max_pdf_pages: int | None = None) -> ParsedFile:
    """Route file parsing based on file extension.

    Args:
        content: Raw file bytes
        filename: Original filename (extension used for routing)
        max_pdf_pages: Optional cap on PDF pages to extract

    Returns:
        ParsedFile with extracted text

    Raises:
        ValueError: If file type is not supported or parsing fails
    """
    ext = Path(filename).suffix.lower()

    if ext in (".md", ".txt"):
        return parse_text_file(content, filename)
    elif ext == ".docx":
        return parse_docx_file(content, filename)
    elif ext == ".pdf":
        return parse_pdf_file(content, filename, max_pages=max_pdf_pages)
    else:
        raise ValueError(f"Unsupported file type: {ext} (file: {filename})")


def merge_parsed_files(files: list[ParsedFile]) -> str:
    """Merge multiple parsed files into a single requirement text.

    Format: "Requirement from: file1.md\n\n{content1}\n\n---\n\nRequirement from: file2.txt\n\n{content2}"

    Args:
        files: List of ParsedFile results

    Returns:
        Merged text with file separators and metadata
    """
    if not files:
        return ""

    if len(files) == 1:
        f = files[0]
        return f"Requirement from {f.filename}:\n\n{f.content}"

    parts = []
    for f in files:
        parts.append(f"Requirement from {f.filename}:\n\n{f.content}")

    return "\n\n" + "—" * 40 + "\n\n".join(parts)


def clean_requirement_text(text: str) -> str:
    """Normalize requirement text: strip whitespace, collapse extra newlines.

    Args:
        text: Raw requirement text

    Returns:
        Cleaned text
    """
    # Collapse multiple blank lines to single blank line
    text = re.sub(r"\n\n+", "\n\n", text)
    return text.strip()

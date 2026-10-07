"""Type-aware chunking. Six strategies, one per artefact shape.

A single splitter would cut a Gherkin scenario in half, orphan a table row from
its header, and split a YAML mapping from its key -- each of which produces a
chunk that retrieves well and answers wrongly, because the fragment reads as
complete while missing the part that gave it meaning.

Sizes follow the reference implementation: 800 characters with 120 overlap, with
per-type overrides. Overlap exists so a sentence spanning a boundary is present
whole in at least one chunk.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Hard-coded fallbacks — overridden by FE_CHUNK_SIZE / FE_CHUNK_OVERLAP from
# config.json (pushed into env by apply_config_json on API/worker startup).
# PER_ARTIFACT overrides below still take precedence over these defaults.
_DEFAULT_SIZE_FALLBACK = 800
_DEFAULT_OVERLAP_FALLBACK = 120


def _default_size() -> int:
    return int(os.environ.get("FE_CHUNK_SIZE", str(_DEFAULT_SIZE_FALLBACK)))


def _default_overlap() -> int:
    return int(os.environ.get("FE_CHUNK_OVERLAP", str(_DEFAULT_OVERLAP_FALLBACK)))


# Module-level aliases kept for backward compatibility (e.g. tests that import them).
DEFAULT_SIZE = _DEFAULT_SIZE_FALLBACK
DEFAULT_OVERLAP = _DEFAULT_OVERLAP_FALLBACK

#: Per-artefact overrides. A design document benefits from larger chunks because
#: its arguments run long; a table or a log line is meaningless past its row.
PER_ARTIFACT: dict[str, dict[str, object]] = {
    "sdd": {"size": 1200, "overlap": 160, "strategy": "markdown"},
    "srd": {"size": 1000, "overlap": 150, "strategy": "markdown"},
    "frd": {"size": 1000, "overlap": 150, "strategy": "markdown"},
    "prd": {"size": 1000, "overlap": 150, "strategy": "markdown"},
    "adr": {"size": 900, "overlap": 120, "strategy": "markdown"},
    # Markdown, not tabular: these are documents that contain tables, and the
    # heading trail ("## Performance") identifies a requirement better than a
    # repeated table header does. `tabular` stays for genuine table exports.
    "nfr": {"size": 700, "overlap": 100, "strategy": "markdown"},
    "impact-analysis": {"size": 1000, "overlap": 150, "strategy": "markdown"},
    "epic": {"size": 700, "overlap": 100, "strategy": "markdown"},
    "feature": {"size": 700, "overlap": 100, "strategy": "markdown"},
    "user-story": {"size": 600, "overlap": 80, "strategy": "bdd"},
    "coverage-report": {"size": 700, "overlap": 60, "strategy": "tabular"},
    "data-dictionary": {"size": 700, "overlap": 60, "strategy": "tabular"},
    "api-openapi": {"size": 900, "overlap": 120, "strategy": "yaml"},
    "schema-snapshot": {"size": 700, "overlap": 60, "strategy": "tabular"},
    "api-tests": {"size": 800, "overlap": 100, "strategy": "bdd"},
    "e2e-results": {"size": 600, "overlap": 60, "strategy": "logs"},
    "ui-smoke-results": {"size": 600, "overlap": 60, "strategy": "logs"},
    "security-report": {"size": 800, "overlap": 100, "strategy": "markdown"},
    "documentation": {"size": 1000, "overlap": 150, "strategy": "markdown"},
    "run-log": {"size": 600, "overlap": 40, "strategy": "logs"},
    "agent-transcript": {"size": 900, "overlap": 100, "strategy": "agent_tx"},
}

STRATEGY_NAMES = ("markdown", "yaml", "logs", "bdd", "tabular", "agent_tx", "rule_catalogue")


@dataclass
class Chunk:
    index: int
    content: str
    strategy: str
    metadata: dict = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        # ~4 characters per token is close enough for budgeting; the exact count
        # only matters at the model boundary, where the model counts it anyway.
        return max(1, len(self.content) // 4)


def _pack(text: str, size: int, overlap: int) -> list[str]:
    """Split on paragraph boundaries, packing up to `size`.

    Packing rather than hard-slicing: a 300-character paragraph followed by a
    200-character one belongs in a single chunk, and slicing at exactly `size`
    would cut mid-sentence for no benefit.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    out: list[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            if current:
                out.append(current)
                current = ""
            # A single oversized paragraph is sliced with overlap, which is the
            # one case where a hard cut is unavoidable.
            step = max(1, size - overlap)
            for start in range(0, len(para), step):
                piece = para[start:start + size]
                if piece.strip():
                    out.append(piece.strip())
            continue
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= size:
            current = candidate
        else:
            out.append(current)
            # Carry the tail forward so a boundary sentence survives intact.
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = f"{tail}\n\n{para}".strip() if tail else para
    if current.strip():
        out.append(current.strip())
    return out


def chunk_markdown(text: str, size: int, overlap: int) -> list[Chunk]:
    """Split on headings, keeping the heading trail with each chunk.

    The trail is why this beats plain packing: a chunk reading "must complete
    within 2 seconds" is useless without "## Performance / ### Search", and a
    retrieved fragment has no other way to say where it came from.
    """
    lines = text.splitlines()
    sections: list[tuple[list[str], list[str]]] = []
    trail: list[str] = []
    body: list[str] = []

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            if body:
                sections.append((list(trail), body))
                body = []
            level = len(heading.group(1))
            trail = trail[: level - 1]
            while len(trail) < level - 1:
                trail.append("")
            trail.append(heading.group(2).strip())
        else:
            body.append(line)
    if body:
        sections.append((list(trail), body))

    chunks: list[Chunk] = []
    for heads, content_lines in sections:
        content = "\n".join(content_lines).strip()
        if not content:
            continue
        crumb = " > ".join(h for h in heads if h)
        for piece in _pack(content, size, overlap):
            prefixed = f"{crumb}\n\n{piece}" if crumb else piece
            chunks.append(Chunk(len(chunks), prefixed, "markdown",
                                {"heading_trail": crumb}))
    if not chunks:
        chunks = [Chunk(i, p, "markdown", {})
                  for i, p in enumerate(_pack(text, size, overlap))]
    return chunks


def chunk_yaml(text: str, size: int, overlap: int) -> list[Chunk]:
    """Split on top-level keys, so a mapping is never separated from its key."""
    lines = text.splitlines()
    blocks: list[tuple[str, list[str]]] = []
    key = ""
    body: list[str] = []
    for line in lines:
        top = re.match(r"^([A-Za-z_][\w./-]*):", line)
        if top:
            if body or key:
                blocks.append((key, body))
            key = top.group(1)
            body = [line]
        else:
            body.append(line)
    if body or key:
        blocks.append((key, body))

    chunks: list[Chunk] = []
    for block_key, block_lines in blocks:
        content = "\n".join(block_lines).strip()
        if not content:
            continue
        for piece in _pack(content, size, overlap):
            # Repeat the key on continuation pieces: without it the second half of
            # a long path definition does not say which path it describes.
            body_text = piece if piece.startswith(f"{block_key}:") else (
                f"# under: {block_key}\n{piece}" if block_key else piece)
            chunks.append(Chunk(len(chunks), body_text, "yaml", {"key": block_key}))
    return chunks or [Chunk(0, text.strip(), "yaml", {})]


def chunk_logs(text: str, size: int, overlap: int) -> list[Chunk]:
    """Group by log entry, keeping a stack trace attached to its message.

    A traceback split from the line that raised it is unusable: the fragment says
    an error occurred without saying which.
    """
    entry_start = re.compile(
        r"^(?:\[|\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|"
        r"(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b)"
    )
    entries: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if entry_start.match(line.strip()) and current:
            entries.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append(current)

    chunks: list[Chunk] = []
    buffer: list[str] = []
    length = 0
    for entry in entries:
        block = "\n".join(entry)
        if length + len(block) > size and buffer:
            chunks.append(Chunk(len(chunks), "\n".join(buffer).strip(), "logs", {}))
            buffer, length = [], 0
        buffer.append(block)
        length += len(block)
    if buffer:
        chunks.append(Chunk(len(chunks), "\n".join(buffer).strip(), "logs", {}))
    return [c for c in chunks if c.content] or [Chunk(0, text.strip(), "logs", {})]


def chunk_bdd(text: str, size: int, overlap: int) -> list[Chunk]:
    """One chunk per scenario, with the Feature line and Background attached.

    A Given/When/Then split across chunks retrieves as an assertion with no
    precondition, which reads as a rule the system does not actually have.
    """
    lines = text.splitlines()
    feature = ""
    background: list[str] = []
    scenarios: list[list[str]] = []
    current: list[str] | None = None
    in_background = False

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("feature:"):
            feature = stripped
            continue
        if stripped.lower().startswith("background:"):
            in_background = True
            background = [line]
            continue
        if re.match(r"^\s*(scenario|scenario outline|example):", stripped,
                    re.IGNORECASE):
            in_background = False
            if current:
                scenarios.append(current)
            current = [line]
            continue
        if in_background:
            background.append(line)
        elif current is not None:
            current.append(line)
    if current:
        scenarios.append(current)

    chunks: list[Chunk] = []
    header = "\n".join(filter(None, [feature, "\n".join(background).strip()]))
    for scenario in scenarios:
        content = "\n".join(scenario).strip()
        if not content:
            continue
        full = f"{header}\n\n{content}".strip() if header else content
        # A scenario over `size` is kept whole regardless: splitting it would
        # separate steps that only mean anything together.
        chunks.append(Chunk(len(chunks), full, "bdd",
                            {"feature": feature, "has_background": bool(background)}))
    if not chunks:
        return chunk_markdown(text, size, overlap)
    return chunks


def chunk_tabular(text: str, size: int, overlap: int) -> list[Chunk]:
    """Repeat the header row with every group of rows.

    Rows without their header are unreadable -- "| 4.68 | yes |" says nothing --
    so the header is duplicated into each chunk rather than living only in the
    first.
    """
    lines = text.splitlines()
    chunks: list[Chunk] = []
    header: list[str] = []
    rows: list[str] = []
    prose: list[str] = []

    def flush_rows() -> None:
        nonlocal rows
        if not rows:
            return
        block: list[str] = []
        length = 0
        for row in rows:
            if length + len(row) > size and block:
                chunks.append(Chunk(len(chunks),
                                    "\n".join(header + block).strip(),
                                    "tabular", {"rows": len(block)}))
                block, length = [], 0
            block.append(row)
            length += len(row)
        if block:
            chunks.append(Chunk(len(chunks), "\n".join(header + block).strip(),
                                "tabular", {"rows": len(block)}))
        rows = []

    def flush_prose() -> None:
        nonlocal prose
        text_block = "\n".join(prose).strip()
        prose = []
        if text_block:
            for piece in _pack(text_block, size, overlap):
                chunks.append(Chunk(len(chunks), piece, "tabular", {"prose": True}))

    for line in lines:
        if line.lstrip().startswith("|"):
            if not header:
                flush_prose()
                header = [line]
                continue
            if re.match(r"^\s*\|[\s:|-]+\|?\s*$", line):
                header.append(line)
                continue
            rows.append(line)
        else:
            if rows or header:
                flush_rows()
                header = []
            prose.append(line)
    flush_rows()
    flush_prose()
    return chunks or chunk_markdown(text, size, overlap)


def chunk_agent_tx(text: str, size: int, overlap: int) -> list[Chunk]:
    """One chunk per turn of an agent transcript.

    A turn is the unit that means something: a tool call split from its result
    retrieves as an intention with no outcome.
    """
    turn = re.compile(
        r"^\s*(?:###\s*)?(?:\[?(?:user|assistant|system|tool|tool_result|"
        r"thinking)\]?\s*[:>]|Turn\s+\d+)",
        re.IGNORECASE,
    )
    turns: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if turn.match(line) and current:
            turns.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        turns.append(current)

    chunks: list[Chunk] = []
    buffer: list[str] = []
    length = 0
    for block_lines in turns:
        block = "\n".join(block_lines).strip()
        if not block:
            continue
        if length + len(block) > size and buffer:
            chunks.append(Chunk(len(chunks), "\n\n".join(buffer), "agent_tx", {}))
            buffer, length = [], 0
        buffer.append(block)
        length += len(block)
    if buffer:
        chunks.append(Chunk(len(chunks), "\n\n".join(buffer), "agent_tx", {}))
    return chunks or [Chunk(0, text.strip(), "agent_tx", {})]


def _detect_rule_catalogue(text: str) -> bool:
    """Detect structured rule catalogue format.

    Criteria (verified across 5 REDs):
    1. Module headers: "### X.X.X NN-module-name Module (N rules)"
    2. Table structure: "| Rule Name | Type | Description | Source File | Confidence |"
    3. Confidence markers: "XX%" pattern

    All 3 patterns must be present to avoid false positives.
    """
    import re

    module_pattern = r"###\s+\d+\.\d+\.\d+\s+\d+-[\w-]+\s+Module\s+\(\d+\s+rules?\)"
    has_modules = bool(re.search(module_pattern, text, re.MULTILINE))

    table_header = (
        r"\|\s*Rule Name\s*\|\s*Type\s*\|\s*Description\s*\|"
        r"\s*Source File\s*\|\s*Confidence\s*\|"
    )
    has_table = bool(re.search(table_header, text, re.IGNORECASE))

    confidence_pattern = r"\|\s*\d{1,3}%\s*\|"
    has_confidence = bool(re.search(confidence_pattern, text))

    detected = has_modules and has_table and has_confidence

    if detected:
        logger.info("Detected structured rule catalogue (RED format)")

    return detected


def chunk_rule_catalogue(text: str, size: int, overlap: int) -> list[Chunk]:
    """Parse structured rule catalogue into one chunk per rule.

    Structure:
        ### 2.1.2 02-account-company Module (116 rules)
        #### 2.1.2.1 Account Management (3 rules)

        | Rule Name | Type | Description | Source File | Confidence |
        |-----------|------|-------------|-------------|------------|
        | Rule X    | Process | ...       | file.asp    | 95%        |

    Each chunk contains: Module + Category + Table header + Rule row
    """
    import re

    lines = text.splitlines()
    chunks: list[Chunk] = []
    current_module = ""
    current_category = ""
    table_header: list[str] = []
    in_table = False

    module_re = re.compile(
        r"###\s+\d+\.\d+\.(\d+)\s+(\d+-[\w-]+)\s+Module\s+\((\d+)\s+rules?\)"
    )
    category_re = re.compile(r"####\s+\d+\.\d+\.\d+\.\d+\s+(.*?)\s+\(\d+\s+rules?\)")
    # Stop at code documentation sections (## 6.X Module -- N files, N LOC)
    code_doc_section_re = re.compile(r"^##\s+\d+\.\d+\s+\d+-[\w-]+\s+Module\s+--")

    for line in lines:
        # Stop at code documentation sections to avoid parsing duplicate rules
        if code_doc_section_re.match(line):
            logger.info(f"Stopping at code documentation section: {line[:60]}")
            break
        # Module heading
        module_match = module_re.match(line)
        if module_match:
            _, module_slug, _ = module_match.groups()
            current_module = module_slug
            current_category = ""
            in_table = False
            continue

        # Category heading
        category_match = category_re.match(line)
        if category_match:
            current_category = category_match.group(1)
            in_table = False
            continue

        # Table header
        if "Rule Name" in line and "Type" in line and "Confidence" in line:
            table_header = [line]
            in_table = True
            continue

        # Table separator
        if in_table and re.match(r"^\s*\|[\s:|-]+\|?\s*$", line):
            table_header.append(line)
            continue

        # Table row (actual rule)
        if in_table and re.match(r"^\|\s*(.+?)\s*\|", line):
            cells = [c.strip() for c in line.split("|")[1:-1]]

            if len(cells) >= 5:
                rule_name, rule_type, description, source_file, confidence_str = cells[:5]

                conf_match = re.search(r"(\d+)%", confidence_str)
                confidence = int(conf_match.group(1)) if conf_match else 0

                # Build self-contained chunk
                content_parts = [
                    f"Module: {current_module}",
                    f"Category: {current_category}",
                    "",
                ] + table_header + [line]

                metadata = {
                    "module": current_module,
                    "category": current_category,
                    "rule_name": rule_name,
                    "rule_type": rule_type,
                    "source_file": source_file,
                    "confidence": confidence,
                }

                chunk = Chunk(
                    index=len(chunks),
                    content="\n".join(content_parts),
                    strategy="rule_catalogue",
                    metadata=metadata,
                )
                chunks.append(chunk)

    if not chunks:
        logger.warning("chunk_rule_catalogue found no rules, falling back to markdown")
        return chunk_markdown(text, size, overlap)

    logger.info(f"Parsed rule catalogue: {len(chunks)} rules from {len({c.metadata.get('module') for c in chunks})} modules")
    return chunks


_STRATEGIES = {
    "markdown": chunk_markdown,
    "yaml": chunk_yaml,
    "logs": chunk_logs,
    "bdd": chunk_bdd,
    "tabular": chunk_tabular,
    "agent_tx": chunk_agent_tx,
    "rule_catalogue": chunk_rule_catalogue,
}


def strategy_for(artifact_type: str, source_path: str = "") -> str:
    """Which strategy an artefact gets, by declared type then by extension."""
    override = PER_ARTIFACT.get(artifact_type)
    if override and override.get("strategy"):
        return str(override["strategy"])
    lowered = source_path.lower()
    if lowered.endswith((".yaml", ".yml")):
        return "yaml"
    if lowered.endswith((".log", ".txt")) and "log" in lowered:
        return "logs"
    if lowered.endswith(".feature"):
        return "bdd"
    if lowered.endswith(".csv"):
        return "tabular"
    return "markdown"


def chunk(text: str, artifact_type: str, source_path: str = "",
          strategy: str | None = None) -> list[Chunk]:
    """Chunk `text` for `artifact_type`. Never returns an empty-content chunk."""
    if not text or not text.strip():
        return []

    override = PER_ARTIFACT.get(artifact_type, {})
    # _default_size() / _default_overlap() read FE_CHUNK_SIZE / FE_CHUNK_OVERLAP
    # from the environment at call time so config.json values take effect without
    # a code restart (PER_ARTIFACT overrides still win over env-var defaults).
    size = int(override.get("size", _default_size()))
    overlap = int(override.get("overlap", _default_overlap()))

    # AUTO-DETECT: Check for rule catalogue structure
    # (Only if no explicit strategy provided)
    if strategy is None and _detect_rule_catalogue(text):
        chosen = "rule_catalogue"
        logger.info("Auto-detected rule_catalogue strategy")
    else:
        chosen = strategy or strategy_for(artifact_type, source_path)
    if chosen not in _STRATEGIES:
        logger.warning("Unknown chunk strategy %r; using markdown", chosen)
        chosen = "markdown"

    chunks = [c for c in _STRATEGIES[chosen](text, size, overlap) if c.content.strip()]
    for position, item in enumerate(chunks):
        item.index = position
        item.metadata.update({
            "artifact_type": artifact_type,
            "source_path": source_path,
            "strategy": chosen,
        })
    logger.info(
        "Chunked %s with %s: %d chunk(s), size=%d overlap=%d, src=%s",
        artifact_type, chosen, len(chunks), size, overlap, source_path or "-",
    )
    return chunks

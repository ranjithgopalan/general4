"""Extract stable requirement identifiers from SDLC artifact markdown.

Parses FRD, PRD, NFR, and LLD markdown blobs and assigns human-readable
stable IDs (FR-001, BR-001, NFR-001, DD-001) to each requirement heading
or bullet.  Results are persisted to fe_requirements for downstream
traceability (code/test → LLD → story → FRD → requirement → BRD).

Usage (called from StageExecutor._finalize after key stages):

    from app.services.requirement_extractor import extract_and_persist
    await extract_and_persist(markdown, artifact_id, workspace_id, stage_key)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

# Stage key → requirement ID prefix + kind
_STAGE_CONFIG: dict[str, tuple[str, str]] = {
    "prd":  ("BR",  "BUSINESS"),
    "frd":  ("FR",  "FUNCTIONAL"),
    "nfr":  ("NFR", "NON_FUNCTIONAL"),
    "lld":  ("DD",  "DESIGN"),
    "adr":  ("ADR", "DESIGN"),
    "srd":  ("SR",  "FUNCTIONAL"),
    "epic-set": ("EP", "FUNCTIONAL"),
}

# Heading and list-item patterns that indicate a requirement
_REQ_RE = re.compile(
    r"^(#{1,4}\s+.{10,}|[-*]\s+.{10,}|\d+\.\s+.{10,})",
    re.MULTILINE,
)


@dataclass
class Requirement:
    req_id: str
    artifact_id: str
    workspace_id: str
    stage_key: str
    req_text: str
    kind: str


def extract_requirements(
    markdown: str,
    artifact_id: str,
    workspace_id: str,
    stage_key: str,
    *,
    start_from: int = 0,
) -> list[Requirement]:
    """Return a list of Requirement objects extracted from `markdown`.

    Each heading or substantive list item becomes one requirement with a
    stable prefix-padded ID.  Items shorter than 10 characters are skipped
    (they are typically section dividers or empty bullets).

    start_from: first counter value (0-based).  Pass the number of
    requirements already stored in the DB for this (workspace, stage) to
    avoid reusing IDs when an artifact is regenerated.
    """
    prefix, kind = _STAGE_CONFIG.get(stage_key, ("REQ", "FUNCTIONAL"))
    reqs: list[Requirement] = []
    counter = start_from
    for match in _REQ_RE.finditer(markdown):
        text = match.group(0).lstrip("#-*0123456789. \t").strip()
        if len(text) < 10:
            continue
        counter += 1
        reqs.append(Requirement(
            req_id=f"{prefix}-{counter:03d}",
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            stage_key=stage_key,
            req_text=text[:1000],   # guard against extremely long lines
            kind=kind,
        ))
    return reqs


async def _fetch_max_counter(postgres, schema: str, workspace_id: str, stage_key: str) -> int:
    """Return the highest numeric suffix already stored for (workspace, stage).

    Returns 0 when no rows exist or DB is unavailable.  The returned value is
    used as ``start_from`` so new IDs continue from the stored high-watermark
    rather than restarting at 001 and silently overwriting prior mappings.
    """
    prefix, _ = _STAGE_CONFIG.get(stage_key, ("REQ", "FUNCTIONAL"))
    try:
        row = await postgres.fetch_one(
            f"SELECT MAX(CAST(SPLIT_PART(req_id, '-', 2) AS INTEGER)) AS max_n"
            f" FROM {schema}.fe_requirements"
            f" WHERE workspace_id = %s AND stage_key = %s AND req_id LIKE %s",
            (workspace_id, stage_key, f"{prefix}-%"),
        )
        if row and row.get("max_n") is not None:
            return int(row["max_n"])
    except Exception as exc:
        log.debug("[req-extractor] max-counter query skipped (non-fatal): %s", exc)
    return 0


async def extract_and_persist(
    markdown: str,
    artifact_id: str,
    workspace_id: str,
    stage_key: str,
) -> list[Requirement]:
    """Extract requirements and upsert them into fe_requirements.

    Non-fatal: if the DB pool is not open (laptop dev build) the extracted
    list is returned but nothing is written.

    Counter collision prevention: queries the existing high-watermark for
    (workspace_id, stage_key) before numbering so that re-running a stage
    does not reuse IDs that already map to a different artifact.
    """
    if stage_key not in _STAGE_CONFIG:
        return []

    try:
        from app.dao import postgres  # noqa: PLC0415
        from app.config.settings import get_settings  # noqa: PLC0415

        schema = get_settings().PG_SCHEMA
        start_from = await _fetch_max_counter(postgres, schema, workspace_id, stage_key)
    except Exception as exc:
        log.debug("[req-extractor] DB unavailable, using start_from=0 (non-fatal): %s", exc)
        postgres = None  # type: ignore[assignment]
        schema = ""
        start_from = 0

    reqs = extract_requirements(markdown, artifact_id, workspace_id, stage_key, start_from=start_from)
    if not reqs:
        return reqs

    if postgres is None:
        return reqs

    sql = f"""
        INSERT INTO {schema}.fe_requirements
            (req_id, artifact_id, workspace_id, stage_key, req_text, kind)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (workspace_id, req_id)
        DO UPDATE SET
            req_text    = EXCLUDED.req_text,
            artifact_id = EXCLUDED.artifact_id
    """
    for req in reqs:
        try:
            await postgres.execute(
                sql,
                (req.req_id, req.artifact_id, req.workspace_id,
                 req.stage_key, req.req_text, req.kind),
            )
        except Exception as exc:
            log.warning("[req-extractor] upsert failed for %s (non-fatal): %s", req.req_id, exc)

    log.info("[req-extractor] %d requirements extracted from %s stage '%s'",
             len(reqs), artifact_id, stage_key)
    return reqs

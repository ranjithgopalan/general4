"""Assign each KB card to its owning EPIC via a heuristic join.

After fan_out() creates mini workspaces, this module stamps
metadata.owner_epic on fe_kb_nodes rows so Get-EpicKbCards
(the PowerShell worker) can filter cards per epic with
?owner_epic={epicId}.

Heuristic (two-pass, additive):
  1. SR-key match  — card metadata has a related_srn or source_sr tag that
     contains the epic's key (e.g. "SR-014" → epic whose key is "SR-014").
  2. Keyword overlap — score each epic against card label + summary; assign
     the epic with the highest overlap (>= 2 shared content words).

Both passes are best-effort: unmatched cards are logged but never block
the fan_out() flow.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_KB_SCHEMA = os.environ.get("FE_KB_DB_SCHEMA", "form_rationalization_anh")
_WORD_RE = re.compile(r"[A-Za-z0-9_]{3,}")


def _t(table: str) -> str:
    return f"{_KB_SCHEMA}.{table}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EpicMap:
    kb_version: str
    card_to_epic: dict[str, str] = field(default_factory=dict)
    uncovered: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------

def _get_pool(db_url: str):
    """Return (or lazily create) the module-level psycopg3 pool."""
    import psycopg_pool  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    # Reuse pg_sink's pool when available (same process, same URL).
    try:
        from app.agentic_platform.fe_core.kb import pg_sink  # noqa: PLC0415
        existing = pg_sink._pool  # type: ignore[attr-defined]
        if existing is not None:
            return existing
    except Exception:  # noqa: BLE001
        pass

    return psycopg_pool.ConnectionPool(
        db_url,
        min_size=1,
        max_size=2,
        timeout=15,
        kwargs={"row_factory": dict_row, "connect_timeout": 10},
        open=True,
    )


def _latest_staging_version(app_id: str, pool) -> str | None:
    """Return the most recent STAGING kb_version for the given gear_id / app_id."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT kb_version FROM {_t('fe_kb_versions')} "
                f"WHERE gear_id = %s AND status = 'STAGING' "
                f"ORDER BY build_date DESC LIMIT 1",
                (app_id,),
            )
            row = cur.fetchone()
            return row["kb_version"] if row else None


def _fetch_cards(kb_version: str, pool) -> list[dict[str, Any]]:
    """Fetch id, label, category, metadata for all nodes in kb_version."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, label, category, metadata "
                f"FROM {_t('fe_kb_nodes')} "
                f"WHERE kb_version = %s",
                (kb_version,),
            )
            return list(cur.fetchall())


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return set(w.lower() for w in _WORD_RE.findall(text))


def _match_by_sr(card: dict, epics) -> str | None:
    """Pass 1: explicit SR-key reference in card metadata."""
    meta = card.get("metadata") or {}
    # metadata is already a dict (psycopg3 returns jsonb as dict)
    if isinstance(meta, str):
        import json  # noqa: PLC0415
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}

    sr_refs: list[str] = []
    for key in ("related_srn", "source_sr", "sr_ref", "source_key"):
        val = meta.get(key)
        if not val:
            continue
        if isinstance(val, list):
            sr_refs.extend(str(v) for v in val)
        else:
            sr_refs.append(str(val))

    if not sr_refs:
        return None

    for epic in epics:
        ekey = (epic.key or "").upper()
        if not ekey:
            continue
        if any(ekey in ref.upper() or ref.upper().startswith(ekey) for ref in sr_refs):
            return epic.key
    return None


def _match_by_keyword(card: dict, epics) -> str | None:
    """Pass 2: keyword overlap between card label+category and epic title+summary."""
    card_words = _words(f"{card.get('label', '')} {card.get('category', '')}")
    best_key, best_score = None, 0
    for epic in epics:
        epic_words = _words(f"{getattr(epic, 'title', '')} {getattr(epic, 'summary', '')}")
        score = len(card_words & epic_words)
        if score > best_score:
            best_key, best_score = epic.key, score
    return best_key if best_score >= 2 else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute(app_id: str, epics, *, db_url: str | None = None) -> EpicMap:
    """Compute card → epic ownership for the latest STAGING KB version.

    Args:
        app_id: kb_application_id (used as gear_id in fe_kb_versions).
        epics:  list[Epic] — the epics extracted from the approved EPIC-set artefact.
        db_url: Postgres connection string; defaults to settings.fe_db_url.

    Returns:
        EpicMap with card_to_epic populated (may be empty if no STAGING version).
    """
    if not epics:
        return EpicMap(kb_version="")

    if db_url is None:
        try:
            from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
            db_url = get_settings().fe_db_url
        except Exception:  # noqa: BLE001
            pass

    if not db_url:
        logger.warning("epic_map.compute: no db_url — skipping owner_epic stamping")
        return EpicMap(kb_version="")

    try:
        pool = _get_pool(db_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("epic_map.compute: cannot get DB pool: %s", exc)
        return EpicMap(kb_version="")

    kb_version = _latest_staging_version(app_id, pool)
    if not kb_version:
        logger.info("epic_map.compute: no STAGING KB version for %s — skipping", app_id)
        return EpicMap(kb_version="")

    cards = _fetch_cards(kb_version, pool)
    if not cards:
        logger.info("epic_map.compute: no nodes for kb_version=%s", kb_version)
        return EpicMap(kb_version=kb_version)

    result = EpicMap(kb_version=kb_version)

    for card in cards:
        card_id = card["id"]
        epic_key = _match_by_sr(card, epics) or _match_by_keyword(card, epics)
        if epic_key:
            result.card_to_epic[card_id] = epic_key
        else:
            result.uncovered.append(card_id)

    logger.info(
        "epic_map.compute: kb_version=%s epics=%d cards=%d matched=%d uncovered=%d",
        kb_version, len(epics), len(cards),
        len(result.card_to_epic), len(result.uncovered),
    )
    return result

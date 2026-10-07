"""
Active-KB Overview service (docs/20 §4a) — the RE dashboard rollup for the ACTIVE version.

Deterministic (no LLM). Computes the "what's in this KB" summary live from the DB — family counts
grouped into the 5 display buckets, per-product split, a tech-stack rollup (file-extension → tech),
code repos, and card/node/edge/chunk counts. Computing live keeps it correct against manually-loaded
data (where ``kb_versions.coverage_stats`` may be null); the stored ``coverage_stats`` from the
kb-indexer loader is a future fast-path.

The family-group + extension→tech tables mirror the kb-indexer ``coverage.py`` (universal, not
LOB-specific); duplicated here because the two services live in separate repos.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

from app.config.settings import Settings, get_settings
from app.dao import overview_dao
from app.models.kb import FamilyGroup, KbOverview, NameCount

# KB ID family → display group (5 buckets).
_FAMILY_GROUP = {
    "BR": "Business", "FR": "Business", "PROC": "Business", "WF": "Business", "SEQ": "Business", "STM": "Business",
    "ENT": "Domain", "DOM": "Domain", "TERM": "Domain", "REL": "Domain", "ALIAS": "Domain",
    "SCR": "Screens",
    "SYS": "Systems", "INT": "Systems", "CMP": "Systems", "API": "Systems", "EXT": "Systems",
    "ROLE": "People",
}  # fmt: skip

# technology labels reused across many extensions (constants avoid duplicated string literals).
_ANGULAR_TS = "Angular / TypeScript"
_IBM_ESB = "IBM ESB"
_PG = "PostgreSQL / ASACDP"

# file extension → technology label (universal; drives the tech-stack rollup).
_EXT_TECH = {
    ".ts": _ANGULAR_TS, ".tsx": _ANGULAR_TS, ".html": _ANGULAR_TS, ".scss": _ANGULAR_TS, ".css": _ANGULAR_TS,
    ".java": "Java / Spring",
    ".wsdl": _IBM_ESB, ".xsd": _IBM_ESB, ".esql": _IBM_ESB, ".msgflow": _IBM_ESB, ".subflow": _IBM_ESB,
    ".xdp": "Adobe LiveCycle (XDP)",
    ".sql": _PG, ".csv": _PG,
    ".xml": "XML", ".pdf": "Documents", ".json": "JSON / config", ".py": "Python",
}  # fmt: skip


def _is_repo_segment(segment: str) -> bool:
    """True for a code-repo path segment like 'aig-connect-auto-services-3396-master' (linear; no regex)."""
    if not segment.endswith("-master"):
        return False
    mid = segment[: -len("-master")]
    dash = mid.rfind("-")
    return dash > 0 and mid[dash + 1 :].isdigit()


def _pool_or_none():
    """The Postgres pool if initialized, else None (no ACTIVE KB to summarize)."""
    try:
        from app.dao.postgres import get_pool

        return get_pool()
    except Exception:
        return None


class OverviewService:
    """Assemble the Active-KB Overview from the DB (deterministic)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def get(self) -> KbOverview:
        pool = _pool_or_none()
        if pool is None:
            return KbOverview(kb_version="(none)", status="NONE", source="none")
        row = await overview_dao.active_version_row(pool, self._settings.GEAR_ID)
        if not row:
            return KbOverview(kb_version="(none)", status="NONE", source="none")

        version = row["kb_version"]
        counts = await overview_dao.entity_counts(pool, version)
        families = {r["kind"]: int(r["n"]) for r in await overview_dao.family_counts(pool, version)}
        by_product = {(r["category"] or "unspecified"): int(r["n"]) for r in await overview_dao.product_counts(pool, version)}
        tech_stack, repos = self._tech_and_repos(await overview_dao.source_uris(pool, version))
        health = await overview_dao.health_counts(pool, version)
        gates_passed, gates_total = self._gate_counts(row.get("gate_results"))

        return KbOverview(
            kb_version=version,
            status=row["status"],
            build_date=str(row.get("build_date") or "") or None,
            signed_off_by=row.get("signed_off_by"),
            signed_off_at=str(row.get("signed_off_at") or "") or None,
            card_count=counts.get("cards", 0),
            node_count=counts.get("nodes", 0),
            edge_count=counts.get("edges", 0),
            chunk_count=counts.get("chunks", 0),
            gates_passed=gates_passed,
            gates_total=gates_total,
            fabricated=health.get("fabricated", 0),
            review_open=health.get("review_open", 0),
            running_jobs=0,  # no build-jobs table yet; wired when /re/jobs lands
            families=families,
            family_groups=self.group_families(families),
            by_product=by_product,
            tech_stack=tech_stack,
            repos=repos,
            source="computed",
        )

    @staticmethod
    def group_families(families: dict[str, int]) -> list[FamilyGroup]:
        """Bucket family counts into the 5 display groups, richest group first."""
        group_count: Counter[str] = Counter()
        group_items: dict[str, list[dict]] = defaultdict(list)
        for kind, n in families.items():
            group = _FAMILY_GROUP.get(kind, "Other")
            group_count[group] += n
            group_items[group].append({"kind": kind, "count": n})
        return [
            FamilyGroup(group=g, count=group_count[g], items=sorted(items, key=lambda x: -x["count"]))
            for g, items in sorted(group_items.items(), key=lambda kv: -group_count[kv[0]])
        ]

    @staticmethod
    def _gate_counts(gate_results: Any) -> tuple[int, int]:
        """(passed, total) from the stored gate_results map, e.g. {"G1": "PASS", ...}."""
        if isinstance(gate_results, str):
            try:
                gate_results = json.loads(gate_results or "{}")
            except ValueError:
                gate_results = {}
        gates = gate_results if isinstance(gate_results, dict) else {}
        passed = sum(1 for v in gates.values() if str(v).upper() == "PASS")
        return passed, len(gates)

    @staticmethod
    def _tech_and_repos(src_rows: list[dict[str, Any]]) -> tuple[list[NameCount], list[NameCount]]:
        """Roll source URIs up into a tech-stack histogram (ext→tech) + a code-repo histogram."""
        ext_counts: Counter[str] = Counter()
        repo_counts: Counter[str] = Counter()
        for row in src_rows:
            uri = (row.get("uri") or "").replace("\\", "/")
            ext = PurePosixPath(uri).suffix.lower()
            if ext:
                ext_counts[ext] += 1
            for segment in uri.split("/"):
                if _is_repo_segment(segment):
                    repo_counts[segment] += 1
                    break
        tech: Counter[str] = Counter()
        for ext, n in ext_counts.items():
            label = _EXT_TECH.get(ext)
            if label:
                tech[label] += n
        tech_stack = [NameCount(name=n, files=c) for n, c in sorted(tech.items(), key=lambda x: -x[1])]
        repos = [NameCount(name=r, files=c) for r, c in sorted(repo_counts.items(), key=lambda x: -x[1])]
        return tech_stack, repos


@lru_cache
def get_overview_service() -> OverviewService:
    """Process-wide singleton OverviewService (cached)."""
    return OverviewService()

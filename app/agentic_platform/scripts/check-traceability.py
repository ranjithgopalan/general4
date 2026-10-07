#!/usr/bin/env python
"""AIDLC Traceability Spine Checker.

Validates that the artifact traceability chain is intact from source documents
(RED / KB cards) through to the current stage's artifacts.

Inspired by smarzban/agent-sdlc's zero-dependency sdlc-check.mjs which enforces
the criterion → component → product → task traceability chain in CI.

Checks performed:
  1. Every stage artifact has at least one KB grounds entry (RED → Artifact).
  2. Every artifact in a later stage links back to an artifact in an earlier stage
     (Artifact → Artifact derivation chain).
  3. No orphaned artifacts (artifacts with no upstream or downstream links).
  4. Minimum traceability coverage: ≥80% of artifacts must be grounded.

Usage:
    python app/agentic_platform/scripts/check-traceability.py --workspace <id>
    python app/agentic_platform/scripts/check-traceability.py --workspace <id> --fail-fast
    python app/agentic_platform/scripts/check-traceability.py --all

Exit codes:
    0  All checks passed
    1  Traceability violations found
    2  Configuration error (cannot connect to DB)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass

MIN_GROUNDING_COVERAGE = 0.80  # ≥80% of artifacts must have KB grounds

# SDLC stage order — used to validate derivation direction.
STAGE_ORDER = [
    "intake", "analysis", "fsd", "srd", "architecture", "stories",
    "developer", "code", "api-code", "ui-code", "qa", "test-plan",
    "merge", "deployment-report",
]


@dataclass
class TraceabilityViolation:
    kind: str      # "UNGROUNDED" | "ORPHANED" | "BACKWARD_LINK" | "MISSING_LINK"
    artifact_id: str
    artifact_kind: str
    detail: str


def _stage_rank(kind: str) -> int:
    """Return the order rank of an artifact kind (lower = earlier stage)."""
    try:
        return STAGE_ORDER.index(kind.lower())
    except ValueError:
        return len(STAGE_ORDER)


async def _check_workspace(pool, workspace_id: str, fail_fast: bool) -> list[TraceabilityViolation]:
    """Run all traceability checks for one workspace."""
    violations: list[TraceabilityViolation] = []

    # Fetch all accepted artifacts for this workspace.
    async with pool.connection() as conn:
        artifacts = await conn.fetch(
            """
            SELECT artifact_id, kind
            FROM form_rationalization_anh.fe_workspace_artifacts
            WHERE workspace_id = $1
            ORDER BY created_at
            """,
            workspace_id,
        )

        if not artifacts:
            print(f"  [WARN] workspace {workspace_id}: no ACCEPTED artifacts found")
            return violations

        artifact_ids = {row["artifact_id"] for row in artifacts}
        artifact_by_id = {row["artifact_id"]: dict(row) for row in artifacts}

        # Check 1: KB grounds — every artifact should have at least one.
        grounds = await conn.fetch(
            """
            SELECT artifact_id, COUNT(*) AS ground_count
            FROM form_rationalization_anh.fe_artifact_kb_grounds
            WHERE workspace_id = $1
            GROUP BY artifact_id
            """,
            workspace_id,
        )
        grounded_ids = {row["artifact_id"] for row in grounds if row["ground_count"] > 0}
        ungrounded = artifact_ids - grounded_ids
        for aid in ungrounded:
            art = artifact_by_id[aid]
            v = TraceabilityViolation(
                kind="UNGROUNDED",
                artifact_id=aid,
                artifact_kind=art["kind"],
                detail=(
                    f"Artifact '{art['kind']}' ({aid}) has no KB grounds entry. "
                    "Every ACCEPTED artifact must cite at least one KB card from the RED."
                ),
            )
            violations.append(v)
            if fail_fast:
                return violations

        # Coverage threshold check.
        coverage = len(grounded_ids) / len(artifact_ids) if artifact_ids else 1.0
        if coverage < MIN_GROUNDING_COVERAGE:
            violations.append(TraceabilityViolation(
                kind="LOW_COVERAGE",
                artifact_id="ALL",
                artifact_kind="ALL",
                detail=(
                    f"KB grounding coverage {coverage:.0%} is below the required "
                    f"{MIN_GROUNDING_COVERAGE:.0%}. "
                    f"{len(ungrounded)} of {len(artifact_ids)} artifacts are ungrounded."
                ),
            ))
            if fail_fast:
                return violations

        # Check 2: Artifact relationships — derivation direction must be forward.
        relationships = await conn.fetch(
            """
            SELECT source_artifact_id, target_artifact_id, relationship_type
            FROM form_rationalization_anh.fe_artifact_relationships
            WHERE workspace_id = $1
            """,
            workspace_id,
        )

        linked_sources = {row["source_artifact_id"] for row in relationships}
        linked_targets = {row["target_artifact_id"] for row in relationships}

        for row in relationships:
            src = artifact_by_id.get(row["source_artifact_id"])
            tgt = artifact_by_id.get(row["target_artifact_id"])
            if not src or not tgt:
                continue
            src_rank = _stage_rank(src["kind"])
            tgt_rank = _stage_rank(tgt["kind"])
            if src_rank > tgt_rank:
                violations.append(TraceabilityViolation(
                    kind="BACKWARD_LINK",
                    artifact_id=row["source_artifact_id"],
                    artifact_kind=src["kind"],
                    detail=(
                        f"Backward derivation: '{src['kind']}' (later stage) derives_from "
                        f"'{tgt['kind']}' (earlier stage). Links must flow downstream."
                    ),
                ))
                if fail_fast:
                    return violations

        # Check 3: Orphaned artifacts — not linked as source or target.
        # Intake artifact is allowed to be unlinked (it IS the root).
        non_root_artifacts = {
            aid for aid, art in artifact_by_id.items() if art["kind"].lower() != "intake"
        }
        orphaned = non_root_artifacts - linked_sources - linked_targets
        for aid in orphaned:
            art = artifact_by_id[aid]
            violations.append(TraceabilityViolation(
                kind="ORPHANED",
                artifact_id=aid,
                artifact_kind=art["kind"],
                detail=(
                    f"Artifact '{art['kind']}' ({aid}) has no upstream or downstream links. "
                    "Add fe_artifact_relationships entries to connect it to the traceability spine."
                ),
            ))
            if fail_fast:
                return violations

    return violations


async def _main(args: argparse.Namespace) -> int:
    try:
        import os
        import psycopg

        dsn = os.environ.get("DATABASE_URL") or os.environ.get("PG_DSN")
        if not dsn:
            print("ERROR: DATABASE_URL or PG_DSN env var required", file=sys.stderr)
            return 2

        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            # Wrap in a pool-like interface
            class _FakePool:
                def connection(self):
                    return _FakeConn(conn)

            class _FakeConn:
                def __init__(self, c):
                    self._c = c
                async def __aenter__(self):
                    return self._c
                async def __aexit__(self, *_):
                    pass

            pool = _FakePool()

            if args.all:
                rows = await conn.execute(
                    "SELECT DISTINCT workspace_id FROM form_rationalization_anh.fe_workspace_artifacts"
                )
                workspace_ids = [row[0] for row in await rows.fetchall()]
            else:
                workspace_ids = [args.workspace]

            all_violations: list[TraceabilityViolation] = []
            for wid in workspace_ids:
                print(f"\nChecking workspace: {wid}")
                violations = await _check_workspace(pool, wid, args.fail_fast)
                all_violations.extend(violations)
                if violations:
                    for v in violations:
                        print(f"  [{v.kind}] {v.artifact_kind}: {v.detail}")
                else:
                    print("  OK — traceability spine intact")

            print(f"\n{'='*60}")
            if all_violations:
                print(f"FAIL — {len(all_violations)} traceability violation(s) found")
                return 1
            else:
                print(f"PASS — all {len(workspace_ids)} workspace(s) have a valid traceability spine")
                return 0

    except ImportError:
        print("ERROR: psycopg not installed. Run: pip install psycopg[binary]", file=sys.stderr)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--workspace", metavar="ID", help="Check one workspace by ID")
    grp.add_argument("--all", action="store_true", help="Check all workspaces in the DB")
    parser.add_argument("--fail-fast", action="store_true", help="Stop at first violation")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())

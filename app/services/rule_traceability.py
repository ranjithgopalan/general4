"""
RuleTraceabilityService — rule-level traceability chain management.

Maintains the chain: RED rule → requirement artifact → code artifact → test artifact.
Each chain link is stored in form_rationalization_anh.fe_rule_traceability (migration 0013).

This service complements the existing artifact-relationship traceability in
traceability.py (which walks fe_artifact_relationships). This service tracks the
specific RED business rule → generated artifact mapping needed for compliance.

Security: rule_id values come from the RED document extraction pipeline.
All DB operations are parameterized — no raw string concatenation of user values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.utils.logging import log
from app.utils.request_context import get_agent_run_id, get_workflow_run_id


@dataclass
class TraceabilityLink:
    rule_id: str
    module_id: str = ""
    source_file: str = ""       # Legacy ASP file path (read-only reference)
    requirement_id: str = ""    # BRD / FSD artifact id
    code_artifact_id: str = ""  # Generated code artifact id
    test_artifact_id: str = ""  # Generated test artifact id
    workspace_id: str = ""
    verification_status: str = "UNVERIFIED"


class RuleTraceabilityService:
    """Record and query RED rule-to-artifact traceability chains."""

    def __init__(self, pool: Any, schema: str) -> None:
        self._pool = pool
        self._schema = schema

    async def record_requirement_link(
        self,
        *,
        rule_id: str,
        requirement_id: str,
        module_id: str = "",
        source_file: str = "",
        workspace_id: str = "",
    ) -> None:
        """Record that a requirement artifact was generated for a rule (BRD/FSD phase)."""
        await self._enqueue(TraceabilityLink(
            rule_id=rule_id,
            module_id=module_id,
            source_file=source_file,
            requirement_id=requirement_id,
            workspace_id=workspace_id,
        ))

    async def record_code_link(
        self,
        *,
        rule_id: str,
        code_artifact_id: str,
        requirement_id: str = "",
        workspace_id: str = "",
    ) -> None:
        """Record that a code artifact was generated for a rule (Developer/Codegen phase)."""
        await self._enqueue(TraceabilityLink(
            rule_id=rule_id,
            code_artifact_id=code_artifact_id,
            requirement_id=requirement_id,
            workspace_id=workspace_id,
        ))

    async def record_test_link(
        self,
        *,
        rule_id: str,
        test_artifact_id: str,
        code_artifact_id: str = "",
        workspace_id: str = "",
    ) -> None:
        """Record that a test artifact was generated for a rule (QA phase)."""
        await self._enqueue(TraceabilityLink(
            rule_id=rule_id,
            test_artifact_id=test_artifact_id,
            code_artifact_id=code_artifact_id,
            workspace_id=workspace_id,
        ))

    async def verify_chain(self, *, rule_id: str, workspace_id: str = "") -> bool:
        """Mark a rule's chain VERIFIED if all four links are present."""
        sql = f"""
            UPDATE {self._schema}.fe_rule_traceability
            SET verification_status = 'VERIFIED'
            WHERE rule_id = %s
              AND requirement_id IS NOT NULL
              AND code_artifact_id IS NOT NULL
              AND test_artifact_id IS NOT NULL
              AND workspace_id = %s
        """
        try:
            async with self._pool.connection() as conn:
                result = await conn.execute(sql, (rule_id, workspace_id))
                return bool(result and "UPDATE 1" in str(result))
        except Exception as exc:
            log.warning(f"[rule_traceability] verify_chain failed: {exc}")
            return False

    async def get_chain(self, *, rule_id: str) -> dict | None:
        sql = f"""
            SELECT rule_id, module_id, source_file, requirement_id,
                   code_artifact_id, test_artifact_id, verification_status,
                   agent_run_id, workspace_id, traced_at
            FROM {self._schema}.fe_rule_traceability
            WHERE rule_id = %s
            ORDER BY traced_at DESC
            LIMIT 1
        """
        try:
            async with self._pool.connection() as conn:
                row = await conn.fetchrow(sql, (rule_id,))
                return dict(row) if row else None
        except Exception as exc:
            log.warning(f"[rule_traceability] get_chain failed: {exc}")
            return None

    async def coverage_summary(self, *, workspace_id: str, module_id: str = "") -> dict:
        params: list[Any] = [workspace_id]
        extra = (" AND module_id = %s", [module_id]) if module_id else ("", [])
        sql = f"""
            SELECT verification_status, COUNT(*) AS count
            FROM {self._schema}.fe_rule_traceability
            WHERE workspace_id = %s{extra[0]}
            GROUP BY verification_status
        """
        try:
            async with self._pool.connection() as conn:
                rows = await conn.fetch(sql, params + extra[1])
                return {r["verification_status"]: r["count"] for r in rows}
        except Exception as exc:
            log.warning(f"[rule_traceability] coverage_summary failed: {exc}")
            return {}

    async def _enqueue(self, link: TraceabilityLink) -> None:
        import asyncio
        asyncio.create_task(self._do_upsert(link))

    async def _do_upsert(self, link: TraceabilityLink) -> None:
        sql = f"""
            INSERT INTO {self._schema}.fe_rule_traceability
                (rule_id, module_id, source_file, requirement_id,
                 code_artifact_id, test_artifact_id, workspace_id,
                 verification_status, agent_run_id, workflow_run_id, traced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (rule_id, code_artifact_id) WHERE code_artifact_id IS NOT NULL
            DO UPDATE SET
                requirement_id   = EXCLUDED.requirement_id,
                test_artifact_id = EXCLUDED.test_artifact_id,
                agent_run_id     = EXCLUDED.agent_run_id,
                workflow_run_id  = EXCLUDED.workflow_run_id,
                traced_at        = NOW()
        """
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, (
                    link.rule_id,
                    link.module_id or None,
                    link.source_file or None,
                    link.requirement_id or None,
                    link.code_artifact_id or None,
                    link.test_artifact_id or None,
                    link.workspace_id or None,
                    link.verification_status,
                    get_agent_run_id() or None,
                    get_workflow_run_id() or None,
                ))
        except Exception as exc:
            log.warning(f"[rule_traceability] upsert failed: {exc}")

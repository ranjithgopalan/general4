"""Traceability Service - orchestrates artifact lineage tracking."""

import json
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.lifecycle.traceability.dao import GroundsLinkDAO
from app.lifecycle.traceability.relationships_dao import ArtifactRelationshipDAO
from app.lifecycle.traceability.schema import (
    ArtifactData,
    ImpactMap,
    KBCard,
    Manifest,
    SystemAffected,
    TracingResult,
)
from app.utils.logging import log


class TraceabilityService:
    """Service for managing workspace traceability (lineage + evidence + artifact relationships)."""

    def __init__(
        self,
        grounds_dao: GroundsLinkDAO,
        relationship_dao: ArtifactRelationshipDAO,
    ):
        """Initialize traceability service with injected DAOs.

        Args:
            grounds_dao: DAO for artifact-to-KB-card GROUNDS links (no DB session needed)
            relationship_dao: DAO for artifact-to-artifact relationships (no DB session needed)
        """
        self.grounds_dao = grounds_dao
        self.relationship_dao = relationship_dao

    async def add_grounds(
        self,
        workspace_id: str,
        from_artifact_id: str,
        to_kb_card_id: str,
        persona: str,
        artifact_kind: str,
        kb_version: str,
        source_locus: str,
        stage: str,
        applied_because: str,
        kb_card_kind: str = "Unknown",
        kb_card_label: str = "",
        source_item_id: str | None = None,
    ) -> None:
        """Create a GROUNDS traceability link from artifact to KB card.

        Uses fe_artifact_kb_grounds table (not fe_manifest_artifact_links).
        This allows linking to KB card IDs without FK constraint violations.

        Args:
            workspace_id: Workspace ID
            from_artifact_id: Artifact ID (e.g., analysis-123)
            to_kb_card_id: KB card ID (e.g., BR-JAUTO-001)
            persona: Persona who initiated this link (e.g., 'ba', 'architect')
            artifact_kind: Type of artifact (e.g., 'analysis', 'fsd', 'story')
            kb_version: KB version at time of grounding
            source_locus: Source evidence location (file §section char:start-end)
            stage: SDLC stage (e.g., 'ANALYSIS', 'FSD', 'ARCHITECTURE')
            applied_because: Reason for link (e.g., 'RETRIEVAL_MATCH', 'GRAPH_WALK')
            kb_card_kind: Kind of KB card ('BR', 'FR', 'SCR', 'WF', etc.)
            kb_card_label: Display name of KB card
        """
        await self.grounds_dao.create(
            workspace_id=workspace_id,
            artifact_id=from_artifact_id,
            source_item_id=source_item_id,
            kb_card_id=to_kb_card_id,
            kb_card_kind=kb_card_kind,
            kb_card_label=kb_card_label,
            source_locus=source_locus,
            applied_because=applied_because,
            persona=persona,
            kb_version=kb_version,
            stage=stage,
            reason=f"{applied_because} (persona={persona}, kb_version={kb_version}, stage={stage})",
        )

    async def link_artifacts(
        self,
        workspace_id: str,
        from_artifact_id: str,
        to_artifact_id: str,
        relationship_type: str,
        description: Optional[str] = None,
    ) -> None:
        """Create a semantic relationship between two artifacts.

        This is the PRIMARY way to link artifacts across stages. Replaces the old
        manifest-based approach with explicit artifact-to-artifact relationships.

        Args:
            workspace_id: Workspace ID
            from_artifact_id: Source artifact (earlier stage)
            to_artifact_id: Target artifact (later stage)
            relationship_type: Type of relationship (derives_from, implements, depends_on, tests)
            description: Justification text explaining why they're linked
        """
        # Check if relationship already exists (avoid duplicates)
        if await self.relationship_dao.exists(workspace_id, from_artifact_id, to_artifact_id):
            log.debug(f"[traceability] Relationship already exists: {from_artifact_id} → {to_artifact_id}")
            return

        await self.relationship_dao.create(
            workspace_id=workspace_id,
            source_artifact_id=from_artifact_id,
            target_artifact_id=to_artifact_id,
            relationship_type=relationship_type,
            description=description,
        )
        log.info(
            f"[traceability] Artifact relationship created: {from_artifact_id} "
            f"--[{relationship_type}]--> {to_artifact_id}"
        )

    async def add_derives(
        self,
        workspace_id: str,
        from_artifact_id: str,
        to_artifact_id: str,
        stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Record a ``derives_from`` link (compatibility wrapper used by the stage handlers).

        Handlers pass ``from_artifact_id`` = the new (downstream) artifact and ``to_artifact_id`` =
        its predecessor (upstream). We normalize to the upstream→downstream direction that
        ``auto_link_to_previous``/``link_artifacts`` use, so it dedups against the auto-created link
        instead of adding a reversed duplicate. ``stage`` is accepted for call-site compatibility.
        """
        await self.link_artifacts(
            workspace_id=workspace_id,
            from_artifact_id=to_artifact_id,
            to_artifact_id=from_artifact_id,
            relationship_type="derives_from",
            description=reason,
        )

    async def _resolve_kb_labels(self, ids: List[str]) -> Dict[str, Dict[str, str]]:
        """id -> {kind, label} from the ACTIVE KB — the single source of truth for display.

        GROUNDS rows (fe_artifact_kb_grounds) may store empty/"Unknown" kb_card_label/kind (they were
        written before enrichment), which is why the trace UI shows "Unknown". We re-resolve every
        grounded id against the ACTIVE version's kb_cards (falling back to kb_nodes for graph-only
        ids). Best-effort: returns {} on any error so the trace never fails on enrichment."""
        uniq = [i for i in dict.fromkeys(ids) if i]
        if not uniq:
            return {}
        gear = get_settings().GEAR_ID
        schema = get_settings().PG_SCHEMA
        resolved: Dict[str, Dict[str, str]] = {}
        try:
            rows = await self.relationship_dao.fetch_all(
                f"""
                SELECT c.id, c.kind, c.label
                FROM {schema}.fe_kb_cards c
                JOIN {schema}.fe_kb_versions v
                  ON v.kb_version = c.kb_version AND v.status = 'ACTIVE' AND v.gear_id = %s
                WHERE c.id = ANY(%s)
                """,
                (gear, uniq),
            )
            resolved = {r["id"]: {"kind": r["kind"], "label": r["label"]} for r in rows}
            miss = [i for i in uniq if i not in resolved]
            if miss:
                nrows = await self.relationship_dao.fetch_all(
                    f"""
                    SELECT n.id, n.kind, n.label
                    FROM {schema}.fe_kb_nodes n
                    JOIN {schema}.fe_kb_versions v
                      ON v.kb_version = n.kb_version AND v.status = 'ACTIVE' AND v.gear_id = %s
                    WHERE n.id = ANY(%s)
                    """,
                    (gear, miss),
                )
                for r in nrows:
                    resolved.setdefault(r["id"], {"kind": r["kind"], "label": r["label"]})
        except Exception as e:  # noqa: BLE001 — enrichment is best-effort; never fail the trace
            log.warning(f"[traceability] KB label enrichment failed: {e}")
        return resolved

    async def get_tracing_result(
        self, workspace_id: str
    ) -> Optional[TracingResult]:
        """Get complete tracing result for workspace by querying artifacts + GROUNDS links directly.

        Skip manifest storage entirely. Build timeline from fe_workspace_artifacts,
        KB grounding from fe_artifact_kb_grounds.
        """
        try:
            # Query workspace artifacts (all stages) + actor provenance columns
            schema = get_settings().PG_SCHEMA
            artifacts = await self.relationship_dao.fetch_all(
                f"""
                SELECT artifact_id, kind, content, created_at, s3_uri, grounding_score,
                       triggered_by_user, triggered_by_name, triggered_by_persona,
                       reviewed_by_user, reviewed_by_name, reviewed_at
                FROM {schema}.fe_workspace_artifacts
                WHERE workspace_id = %s
                ORDER BY created_at ASC
                """,
                (workspace_id,),
            )

            # Fetch current workspace state — authoritative source for acceptance status.
            # An artifact whose stage is still the current workspace state has been generated
            # but not yet accepted; stages the workspace has moved past are truly ACCEPTED.
            ws_row = await self.relationship_dao.fetch_all(
                f"SELECT state FROM {schema}.fe_workspaces WHERE workspace_id = %s",
                (workspace_id,),
            )
            ws_state: str = ws_row[0]["state"] if ws_row else "INTAKE"

            if not artifacts:
                log.warning(f"[traceability] No artifacts found for {workspace_id}")
                return TracingResult(
                    workspace_id=workspace_id,
                    timeline=[],
                    artifact_chain={},
                    kb_grounding=[],
                    systems_affected=[],
                    scope_analysis={
                        "stages_completed": 0,
                        "kb_cards_matched": 0,
                        "systems_affected": 0,
                        "database_changes": 0,
                    },
                )

            # Build timeline and artifact chain from raw artifacts
            timeline = []
            artifact_chain = {}
            all_kb_cards = []
            all_systems = []

            for i, artifact in enumerate(artifacts, 1):
                try:
                    artifact_data = json.loads(artifact["content"]) if isinstance(artifact["content"], str) else artifact["content"]

                    # Map kind to stage name
                    stage_name = self._kind_to_stage(artifact["kind"])

                    # Query KB grounds for this artifact
                    grounds = await self.grounds_dao.get_by_artifact(workspace_id, artifact["artifact_id"])

                    # Enrich label/kind from the ACTIVE KB — GROUNDS may store empty/"Unknown"; the KB
                    # is the source of truth for display (fixes the "SCR-… - Unknown" labels).
                    resolved = await self._resolve_kb_labels([g["kb_card_id"] for g in grounds])
                    kb_cards = [
                        KBCard(
                            kb_id=g["kb_card_id"],
                            kind=(resolved.get(g["kb_card_id"], {}).get("kind")
                                  or g.get("kb_card_kind") or "Unknown"),
                            label=(resolved.get(g["kb_card_id"], {}).get("label")
                                   or g.get("kb_card_label") or "Unknown"),
                            source_locus=g.get("source_locus") or "",
                            confidence=0.9,  # Default confidence for grounded cards
                        )
                        for g in grounds
                    ]
                    all_kb_cards.extend(kb_cards)
                    # Derive affected systems from the resolved SYS-* cards (previously always empty)
                    all_systems.extend(
                        SystemAffected(system_id=c.kb_id, system_name=c.label)
                        for c in kb_cards if c.kb_id.startswith("SYS-")
                    )

                    # Derive true acceptance status: the workspace state is the authoritative source.
                    # If reviewed_at is set the stage was explicitly accepted; otherwise fall back to
                    # comparing stage order — a stage is ACCEPTED only if the workspace has advanced
                    # past it (ws_state index > this artifact's stage index).
                    artifact_status = self._compute_artifact_status(
                        artifact["kind"], ws_state, artifact.get("reviewed_at")
                    )

                    # Build artifact data (minimalist)
                    artifact_model = ArtifactData(
                        artifact_id=artifact["artifact_id"],
                        stage=stage_name,
                        status=artifact_status,
                        created_by=artifact_data.get("created_by", "system"),
                        created_at=artifact["created_at"].isoformat() if artifact["created_at"] else None,
                        what_changed=artifact_data.get("what_changed", []),
                        kb_sources=kb_cards,
                        systems_affected=artifact_data.get("systems_affected", []),
                    )

                    # Add to chain using stage key for consistency
                    stage_key = f"stage_{i}_{stage_name}"
                    artifact_chain[stage_key] = artifact_model

                    # Timeline entry
                    timeline.append({
                        "number": i,
                        "stage": stage_name,
                        "status": artifact_status,
                        "artifact_id": artifact["artifact_id"],
                        "created_by": artifact_data.get("created_by", "system"),
                        "created_at": artifact["created_at"].isoformat() if artifact["created_at"] else None,
                        "what_changed": artifact_data.get("what_changed", []),
                        "summary": self._artifact_summary(artifact["kind"], artifact_data),
                        "owner": self._artifact_owner(artifact_data),
                        "grounding": artifact_data.get("grounding_score"),
                        "triggered_by": {
                            "persona": artifact.get("triggered_by_persona"),
                            "name": artifact.get("triggered_by_name"),
                            "user": artifact.get("triggered_by_user"),
                        },
                        "reviewed_by": {
                            "name": artifact.get("reviewed_by_name"),
                            "user": artifact.get("reviewed_by_user"),
                            "at": artifact["reviewed_at"].isoformat() if artifact.get("reviewed_at") else None,
                        },
                        "kb_sources": kb_cards,
                    })

                except Exception as e:
                    log.warning(f"[traceability] Failed to process artifact {artifact['artifact_id']}: {e}")
                    continue

            # Dedupe KB cards
            seen_ids = set()
            unique_kb_cards = []
            for card in all_kb_cards:
                if card.kb_id not in seen_ids:
                    seen_ids.add(card.kb_id)
                    unique_kb_cards.append(card)

            # Dedupe affected systems by id
            seen_sys = set()
            unique_systems = []
            for s in all_systems:
                if s.system_id not in seen_sys:
                    seen_sys.add(s.system_id)
                    unique_systems.append(s)

            return TracingResult(
                workspace_id=workspace_id,
                timeline=timeline,
                artifact_chain=artifact_chain,
                kb_grounding=unique_kb_cards,
                systems_affected=unique_systems,
                scope_analysis={
                    "stages_completed": len(artifact_chain),
                    "kb_cards_matched": len(unique_kb_cards),
                    "systems_affected": len(unique_systems),
                    "database_changes": 0,
                },
            )

        except Exception as e:
            log.error(f"[traceability] Failed to get tracing result: {e}")
            return None

    # Produced collections per kind → (content field, item-type prefix, preferred title field).
    # The Artifacts tab lists the actual item TITLES (not counts) so it reads as a real inventory.
    _ITEM_GROUPS = {
        "analysis": [("matched", "Area", "label")],
        "fsd": [("functional_requirements", "FR", "title")],
        "brd": [("business_requirements", "BR", "title")],
        "srd": [("component_design", "Component", "label"), ("integration_design", "Integration", "label")],
        "stories": [("stories", "Story", "title")],
        "dev": [("change_plan", "File", "file")],
        "code": [("change_plan", "File", "file")],
        "dev-pr": [("files_changed", "File", "file_path"), ("tests_written", "Test", "test_file")],
        "dev_offline": [("items", "File", "file_path")],
        "test-plan": [("story_test_cases", "Test", "title"), ("integration_tests", "Integration test", "label")],
        "qa": [("story_test_cases", "Test", "title")],
        "qa_offline": [("test_cases", "Test", "scenario")],
    }
    _ITEM_CAP = 15  # per group — keeps a card readable; the rest roll up into "…and N more"
    _PERSONA_LABEL = {
        "po": "Product Owner", "ba": "Business Analyst", "architect": "Architect",
        "scrum": "Scrum Master", "dev": "Developer", "developer": "Developer", "qa": "QA",
    }

    @staticmethod
    def _item_title(it: Any, preferred: str) -> str:
        """Best display title for a produced item (preferred field, then common fallbacks)."""
        if not isinstance(it, dict):
            return str(it).strip()
        for key in (preferred, "title", "label", "file", "filename", "id"):
            val = it.get(key)
            if val:
                return str(val).strip()
        return ""

    @classmethod
    def _artifact_summary(cls, kind: str, content: Any) -> List[str]:
        """The artifact's produced items as TITLES (Intake → the requirement text). Extractive only."""
        if not isinstance(content, dict):
            return []
        k = (kind or "").lower()
        if k == "intake":
            req = (content.get("requirement_text") or "").strip()
            if not req:
                return []
            return [req if len(req) <= 400 else req[:397] + "…"]
        out: List[str] = []
        for field, prefix, title_field in cls._ITEM_GROUPS.get(k, []):
            items = content.get(field)
            if not isinstance(items, list) or not items:
                continue
            for it in items[: cls._ITEM_CAP]:
                title = cls._item_title(it, title_field)
                if title:
                    out.append(f"{prefix}: {title}")
            if len(items) > cls._ITEM_CAP:
                out.append(f"…and {len(items) - cls._ITEM_CAP} more")
        return out

    @classmethod
    def _artifact_owner(cls, content: Any) -> str:
        """Owning persona for the artifact (from persona field), else created_by."""
        if not isinstance(content, dict):
            return "system"
        persona = (content.get("persona") or "").lower()
        return cls._PERSONA_LABEL.get(persona) or content.get("created_by") or "system"

    # SDLC state order — index determines whether a stage has been accepted.
    # A stage is ACCEPTED if ws_state index > stage index (workspace has moved past it).
    _WS_STATE_ORDER: Dict[str, int] = {
        "INTAKE": 0, "ANALYSIS": 1, "FSD": 2, "BRD": 2,
        "ARCHITECTURE": 3, "STORIES": 4, "DEVELOPMENT": 5,
        "QA_TESTING": 6, "MERGE": 7, "PENDING_SYNC": 8, "CLOSED": 9,
    }
    # Map artifact kind to the workspace state that is active while that stage is being worked on.
    _KIND_TO_WS_STATE: Dict[str, str] = {
        "intake": "INTAKE",
        "analysis": "ANALYSIS",
        "fsd": "FSD", "brd": "FSD",
        "srd": "ARCHITECTURE", "architecture": "ARCHITECTURE",
        "stories": "STORIES",
        "dev": "DEVELOPMENT", "code": "DEVELOPMENT", "development": "DEVELOPMENT",
        "dev-pr": "DEVELOPMENT", "dev_pr": "DEVELOPMENT", "dev_offline": "DEVELOPMENT",
        "qa": "QA_TESTING", "test": "QA_TESTING", "test-plan": "QA_TESTING",
        "qa_offline": "QA_TESTING",
        "merge": "MERGE",
    }

    @classmethod
    def _compute_artifact_status(cls, kind: str, ws_state: str, reviewed_at: Any) -> str:
        """Return ACCEPTED or IN_PROGRESS for an artifact given the current workspace state.

        Primary signal: reviewed_at column — set by WorkspaceService.advance() when accept is called.
        Fallback: compare stage order against the workspace's current state so that stages the
        workspace has moved past are marked ACCEPTED even for historical rows without reviewed_at.
        """
        if reviewed_at is not None:
            return "ACCEPTED"
        stage_ws = cls._KIND_TO_WS_STATE.get((kind or "").lower(), "INTAKE")
        ws_idx = cls._WS_STATE_ORDER.get(ws_state, 0)
        stage_idx = cls._WS_STATE_ORDER.get(stage_ws, 0)
        return "ACCEPTED" if ws_idx > stage_idx else "IN_PROGRESS"

    @staticmethod
    def _kind_to_stage(kind: str) -> str:
        """Map artifact kind to SDLC stage name."""
        mapping = {
            "intake": "intake",
            "analysis": "analysis",
            "fsd": "fsd",
            "brd": "brd",
            "srd": "srd",
            "stories": "stories",
            "code": "code",
            "qa": "qa",
            "test": "qa",
            "merge": "merge_gate",
            "release": "kb_sync",
        }
        return mapping.get(kind.lower(), kind.lower())

    # ── Canonical SDLC chain for auto-linking (XPF semantics) ───────────────────────
    # Each stage links to the NEAREST preceding stage that has an artifact → the forward chain
    # Requirement(Intake) → Analysis → PRD → SRD → User Stories → Development → QA (→ Merge).
    _CANON_ORDER = ["intake", "analysis", "fsd", "srd", "stories", "dev", "test-plan", "merge"]
    # Normalize the many artifact-kind spellings to one canonical stage key.
    _KIND_ALIAS = {
        "code": "dev", "development": "dev", "architecture": "srd", "brd": "fsd",
        "qa": "test-plan", "test": "test-plan", "release": "merge",
        "dev-pr": "dev", "dev_pr": "dev", "dev_offline": "dev",
        "qa_offline": "test-plan",
    }
    # XPF relationship type for how each stage relates to its predecessor.
    _REL_TYPE = {
        "analysis": "derives_from", "fsd": "derives_from", "srd": "implements",
        "stories": "implements", "dev": "depends_on", "test-plan": "tests", "merge": "verifies",
    }
    # Friendly names for the justification text (avoids raw FSD/CODE spellings).
    _STAGE_LABEL = {
        "intake": "Requirement (Intake)", "analysis": "Impact Analysis", "fsd": "PRD",
        "srd": "SRD (Design)", "stories": "User Stories", "dev": "Development",
        "test-plan": "QA", "merge": "Merge",
    }

    @classmethod
    def _canon(cls, kind: str) -> str:
        """Normalize an artifact kind to its canonical stage key."""
        k = (kind or "").lower()
        return cls._KIND_ALIAS.get(k, k)

    async def auto_link_to_previous(
        self,
        workspace_id: str,
        current_artifact_id: str,
        current_kind: str,
        justification: str | None = None,
    ) -> None:
        """Link this artifact to the NEAREST preceding SDLC stage that has an artifact.

        Produces the full forward chain (Intake → Analysis → PRD → SRD → Stories → Dev → QA),
        robust to kind aliasing (code/dev, architecture/srd, qa/test-plan) and skipped stages.
        Idempotent via ``link_artifacts`` (skips if the relationship already exists).

        Args:
            workspace_id: Workspace ID
            current_artifact_id: ID of the newly created artifact
            current_kind: Kind of the new artifact (fsd, srd, stories, dev, test-plan, ...)
            justification: Optional rich description explaining the relationship
        """
        canon = self._canon(current_kind)
        if canon not in self._CANON_ORDER:
            return
        idx = self._CANON_ORDER.index(canon)
        if idx == 0:
            return  # intake is the root — no predecessor
        rel_type = self._REL_TYPE.get(canon, "derives_from")
        try:
            schema = get_settings().PG_SCHEMA
            # Walk backwards to the nearest preceding stage that actually has an artifact.
            for prev_canon in reversed(self._CANON_ORDER[:idx]):
                artifacts = await self.relationship_dao.fetch_all(
                    f"""
                    SELECT artifact_id
                    FROM {schema}.fe_workspace_artifacts
                    WHERE workspace_id = %s AND kind = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (workspace_id, prev_canon),
                )
                if artifacts:
                    prev_id = artifacts[0]["artifact_id"]
                    description = justification or f"{canon.upper()} {rel_type} {prev_canon.upper()}"
                    await self.link_artifacts(
                        workspace_id=workspace_id,
                        from_artifact_id=prev_id,
                        to_artifact_id=current_artifact_id,
                        relationship_type=rel_type,
                        description=description,
                    )
                    log.info(f"[traceability] auto-linked {prev_id} --[{rel_type}]--> {current_artifact_id}")
                    return
        except Exception as e:  # noqa: BLE001
            log.warning(f"[traceability] auto-link failed for {current_artifact_id}: {e}")

    async def backfill_relationships(self, workspace_id: str) -> int:
        """Backfill the full sequential chain for an EXISTING workspace (idempotent).

        Runs the same nearest-preceding-stage linking for every artifact. Safe to re-run
        (``link_artifacts`` upserts ON CONFLICT). Returns the number of artifacts processed.
        """
        schema = get_settings().PG_SCHEMA
        rows = await self.relationship_dao.fetch_all(
            f"""
            SELECT artifact_id, kind, created_at
            FROM {schema}.fe_workspace_artifacts
            WHERE workspace_id = %s
            ORDER BY created_at ASC
            """,
            (workspace_id,),
        )
        for r in rows:
            await self.auto_link_to_previous(workspace_id, r["artifact_id"], r["kind"])
        return len(rows)

    async def cleanup_stage_artifacts(self, workspace_id: str, kind: str) -> None:
        """Clean up previous artifacts of the same kind for a workspace.

        On re-run, delete old artifacts of the same stage (e.g., old ANALYSIS) plus their child
        GROUNDS links and artifact-to-artifact relationships, so only the latest version exists.
        Children are removed first to satisfy the ``fe_artifact_kb_grounds`` foreign key.

        Args:
            workspace_id: Workspace ID
            kind: Artifact kind to clean up (e.g., 'analysis', 'fsd', 'srd')
        """
        try:
            schema = get_settings().PG_SCHEMA

            # 1. Get all existing artifacts of this kind
            artifacts = await self.relationship_dao.fetch_all(
                f"""
                SELECT artifact_id FROM {schema}.fe_workspace_artifacts
                WHERE workspace_id = %s AND kind = %s
                """,
                (workspace_id, kind),
            )

            if not artifacts:
                return  # Nothing to clean up

            artifact_ids = [a["artifact_id"] for a in artifacts]

            for artifact_id in artifact_ids:
                # 2. Delete child GROUNDS rows first (FK fe_artifact_kb_grounds -> fe_workspace_artifacts).
                await self.relationship_dao.execute(
                    f"""
                    DELETE FROM {schema}.fe_artifact_kb_grounds
                    WHERE workspace_id = %s AND artifact_id = %s
                    """,
                    (workspace_id, artifact_id),
                )
                # 3. Delete artifact-to-artifact relationships (both directions).
                await self.relationship_dao.delete_all_for_artifact(workspace_id, artifact_id)
                # 4. Delete the artifact row itself (now free of FK references).
                await self.relationship_dao.execute(
                    f"""
                    DELETE FROM {schema}.fe_workspace_artifacts
                    WHERE workspace_id = %s AND artifact_id = %s
                    """,
                    (workspace_id, artifact_id),
                )

            log.info(
                f"[traceability] Cleaned up {len(artifact_ids)} old {kind} artifact(s) for {workspace_id}"
            )
        except Exception as e:
            log.warning(
                f"[traceability] Failed to cleanup {kind} artifacts for {workspace_id}: {e}"
            )

    async def trace_graph(self, workspace_id: str) -> dict:
        """Build traceability graph using EXPLICIT artifact relationships (not temporal order).

        Returns a dict matching TraceGraph schema:
        {
            'workspace_id': str,
            'nodes': [{'id': str, 'label': str, 'stage': str, 'status': str}, ...],
            'edges': [{'from': str, 'to': str, 'type': str, 'description': str}, ...]
        }
        """
        try:
            result = await self.get_tracing_result(workspace_id)
            if not result:
                log.warning(f"[traceability] No tracing result for {workspace_id}")
                return {
                    "workspace_id": workspace_id,
                    "nodes": [],
                    "edges": [],
                }

            # Build nodes from timeline
            node_map = {}  # artifact_id → node dict
            nodes = []
            for entry in result.timeline:
                node = {
                    "id": entry.get("artifact_id") or f"{entry['stage']}-{workspace_id}",
                    "label": entry["stage"].upper(),
                    "stage": entry["stage"],
                    "status": entry.get("status", "NOT_STARTED"),
                }
                nodes.append(node)
                node_map[node["id"]] = node

            # Query explicit artifact relationships
            relationships = await self.relationship_dao.get_all(workspace_id)

            # Build edges from explicit relationships
            edges = []
            for rel in relationships:
                edge = {
                    "from": rel.get("source_artifact_id"),
                    "to": rel.get("target_artifact_id"),
                    "type": rel.get("relationship_type", "unknown"),
                    "description": rel.get("description", ""),
                }
                edges.append(edge)

            log.debug(
                f"[traceability] Built trace graph: {len(nodes)} nodes, {len(edges)} edges "
                f"(from {len(relationships)} explicit relationships)"
            )

            return {
                "workspace_id": workspace_id,
                "nodes": nodes,
                "edges": edges,
            }

        except Exception as e:
            log.error(f"[traceability] Failed to build trace graph: {e}")
            return {
                "workspace_id": workspace_id,
                "nodes": [],
                "edges": [],
            }


    @staticmethod
    def _stage_number(stage: str) -> int:
        """Map stage name to number (1-8)."""
        stages = {
            "analysis": 1,
            "fsd": 2,
            "srd": 3,
            "stories": 4,
            "code": 5,
            "qa": 6,
            "merge_gate": 7,
            "kb_sync": 8,
        }
        return stages.get(stage, 0)

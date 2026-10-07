"""Shared base for FE lifecycle stage handlers.

Every stage handler (analysis, FSD, BRD, stories, …) repeats the same five
infrastructure pieces.  This module owns them once:

  - Service injection (8 shared constructor params)
  - ``_s3_key()``     — S3 path: ``<workspace>/<stage_folder>/<kind>.json``
  - ``get()``         — S3-first → DB-fallback pattern
  - ``export_docx()`` — get() + ResourceNotFoundError + render
  - ``_ground_field()`` — grounding core: verify → BLOCK → fallback → re-verify

Concrete stages subclass ``StageHandlerBase`` and declare three ClassVar strings
plus three abstract methods:

  _KIND          e.g. "analysis" | "fsd" | "brd"
  _STAGE_FOLDER  e.g. "impact-analysis" | "fsd" | "brd"
  _TEMPLATE_NAME e.g. "analysis" | "fsd" | "brd"

  _parse(workspace_id, text, *, source)   → typed artifact | None
  _render_docx(artifact)                  → bytes
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.lifecycle.templates.registry import get_template
from app.models.kb import KbCitation
from app.services.artifact_store import ArtifactStore, get_artifact_store
from app.services.graph_provider import GraphProvider
from app.services.grounding import GroundingSpine
from app.services.kb_query import KbQueryService
from app.services.personas import PersonaRegistry
from app.services.traceability_service import TraceabilityService
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import ResourceNotFoundError
from app.utils.logging import log


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 format — used as ``generated_at`` in every stage artifact."""
    return datetime.now(UTC).isoformat()


class StageHandlerBase(abc.ABC):
    """Abstract base for FE lifecycle stage handlers (analysis, FSD, BRD, …).

    Subclasses must declare three class variables and implement two abstract methods:

    Class variables (no default — subclass must provide a concrete value)::

        _KIND          = "analysis"        # DB artifact kind string
        _STAGE_FOLDER  = "impact-analysis" # S3 sub-folder (docs/09 §3.6 s3_uri)
        _TEMPLATE_NAME = "analysis"        # template registry key

    Abstract methods::

        _parse(workspace_id, text, *, source) → typed artifact | None
        _render_docx(artifact) → bytes
    """

    _KIND: ClassVar[str]
    _STAGE_FOLDER: ClassVar[str]
    _TEMPLATE_NAME: ClassVar[str]

    def __init__(
        self,
        *,
        workspace: WorkspaceService,
        kb_query: KbQueryService,
        graph: GraphProvider,
        personas: PersonaRegistry,
        trace: TraceabilityService,
        spine: GroundingSpine | None = None,
        model: Any = None,
        store: ArtifactStore | None = None,
        template_version: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._kb = kb_query
        self._graph = graph
        self._personas = personas
        self._trace = trace
        self._spine = spine or GroundingSpine()
        self._model = model
        self._store = store or get_artifact_store()
        self._template = get_template(self._TEMPLATE_NAME, template_version)

    # ── S3 key ─────────────────────────────────────────────────────────────────────

    def _s3_key(self, workspace_id: str) -> str:
        """S3 object key — single overwritten object per workspace/stage (docs/09 §3.6)."""
        return self._store.key(workspace_id, self._STAGE_FOLDER, f"{self._KIND}.json")

    # ── get: DB-first → S3-fallback ───────────────────────────────────────────────

    async def get(self, workspace_id: str) -> Any | None:
        """Return the persisted artifact — DB mirror first (fast, indexed single-row), S3 fallback.

        Persist writes BOTH the S3 object and the DB mirror with identical content, so the DB row is
        authoritative for reads and avoids a cross-region S3 ``head+get`` on every request (that call
        dominates read latency when the bucket is remote/slow). S3 is only read if the DB mirror is
        missing (e.g. a best-effort DB write once failed) — correctness preserved, hot path fast.
        """
        # Targeted read: newest artifact of THIS kind only (O(1)). Also raises 404 if workspace missing.
        latest = await self._workspace.latest_artifact(workspace_id, self._KIND)
        if latest and latest.get("content"):
            parsed = self._parse(workspace_id, latest["content"], source="db")
            if parsed is not None:
                return parsed

        # Fallback only: read the S3 object (reached when the DB mirror is absent/unparseable).
        s3_text = await self._store.get_text(self._s3_key(workspace_id))
        if s3_text:
            return self._parse(workspace_id, s3_text, source="s3")
        return None

    @abc.abstractmethod
    def _parse(self, workspace_id: str, text: str, *, source: str) -> Any | None:
        """Deserialize the stage artifact from a JSON string; return ``None`` on invalid content."""

    # ── export_docx template ──────────────────────────────────────────────────────

    async def export_docx(self, workspace_id: str) -> bytes:
        """Render the persisted artifact to an official AIG .docx (bytes)."""
        artifact = await self.get(workspace_id)
        if artifact is None:
            raise ResourceNotFoundError(
                f"no {self._KIND} artifact for workspace {workspace_id}",
                {"workspace_id": workspace_id},
            )
        return self._render_docx(artifact)

    @abc.abstractmethod
    def _render_docx(self, artifact: Any) -> bytes:
        """Stage-specific: render the typed artifact to .docx bytes."""

    # ── grounding core ────────────────────────────────────────────────────────────

    def _ground_field(
        self,
        text: str,
        whitelist: set[str],
        fallback_fn: Callable[[], str],
    ) -> tuple[str, float]:
        """Grounding gate on a free-text field.

        Verifies ``text`` against ``whitelist``; on BLOCK, replaces with ``fallback_fn()``
        and re-verifies.  Returns ``(grounded_text, coverage_score)``.

        The caller is responsible for patching the artifact with the returned values — the
        field name and update mechanism (direct assignment vs model_copy) vary per stage.
        """
        allowed = [KbCitation(id=i, kind="", label="") for i in whitelist]
        report = self._spine.verify(text, allowed)
        if report.verdict == "BLOCK":
            text = fallback_fn()
            report = self._spine.verify(text, allowed)
        return text, report.coverage

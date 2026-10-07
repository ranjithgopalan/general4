"""
FastAPI dependency-injection providers (M1).

Thin re-exports of the module-level singletons so routes/middleware obtain them
consistently via ``Depends``. lru_cache in each factory guarantees one instance
per process. Business providers (model router, PII, tools client) land later.

    from fastapi import Depends
    from app.core.dependencies import get_vault_client

    @router.get("/foo")
    async def foo(vault: VaultClient = Depends(get_vault_client)):
        creds = await vault.get_credentials(settings.PG_VAULT_NAME)
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Request

from app.config import get_settings
from app.lifecycle.generate import FeGenerateService
from app.lifecycle.stages.analysis.handler import ImpactAnalysisService
from app.lifecycle.stages.architecture.handler import ArchitectureService
from app.lifecycle.stages.brd.handler import BRDService
from app.lifecycle.stages.developer.handler import DeveloperService
from app.lifecycle.stages.fsd.handler import FSDService
from app.lifecycle.stages.qa.handler import QAService
from app.lifecycle.stages.stories.handler import StoriesService
from app.lifecycle.stages.intake.handler import IntakeService
from app.lifecycle.traceability.analysis_dao import WorkspaceAnalysisDAO
from app.lifecycle.traceability.dao import GroundsLinkDAO
from app.lifecycle.traceability.relationships_dao import ArtifactRelationshipDAO
from app.lifecycle.traceability.graph_service import TraceGraphService
from app.lifecycle.traceability.service import TraceabilityService
from app.services.artifact_store import ArtifactStore
from app.services.auth import AuthService
from app.services.auth import get_auth_service as _gas
from app.services.chat_model import get_chat_model
from app.services.chat_service import ChatService
from app.services.chat_service import get_chat_service as _gcsvc
from app.services.classifier import ChangeClassifier
from app.services.entitlement import EntitlementService
from app.services.entitlement import get_entitlement_service as _ges
from app.services.kb_query import KbQueryService
from app.services.personas import PersonaRegistry
from app.services.personas import get_persona_registry as _gpr
from app.services.specialists import PersonaSpecialist
from app.services.telemetry import TelemetryService
from app.services.telemetry import get_telemetry_service as _gts
from app.services.workspace_service import WorkspaceService
from app.utils.exceptions import AuthorizationError
from app.utils.hashicorp_client import VaultClient
from app.utils.hashicorp_client import get_vault_client as _gvc
from app.utils.logging import log


@lru_cache
def get_vault_client() -> VaultClient:
    """DI provider: Vault credential client (singleton)."""
    return _gvc()


@lru_cache
def get_auth_service() -> AuthService:
    """DI provider: Okta JWT auth service (singleton)."""
    return _gas()


@lru_cache
def get_entitlement_service() -> EntitlementService:
    """DI provider: external entitlement service (singleton)."""
    return _ges()


@lru_cache
def get_telemetry_service() -> TelemetryService:
    """DI provider: telemetry emit service (singleton)."""
    return _gts()


@lru_cache
def get_workspace_service() -> WorkspaceService:
    """DI provider: FE workspace lifecycle service (default repos + refresh enqueuer)."""
    return WorkspaceService()


@lru_cache
def get_artifact_store() -> ArtifactStore:
    """DI provider: S3 artifact store for workspace artifacts (singleton).

    Best-effort backup (DB is source of truth); gracefully degrades if S3 is unconfigured.
    """
    return ArtifactStore()


@lru_cache
def get_grounds_dao() -> GroundsLinkDAO:
    """DI provider: GroundsLinkDAO for artifact-to-KB-card links (psycopg3 + BaseRepository)."""
    return GroundsLinkDAO()


@lru_cache
def get_relationship_dao() -> ArtifactRelationshipDAO:
    """DI provider: ArtifactRelationshipDAO for artifact-to-artifact relationships (psycopg3 + BaseRepository)."""
    return ArtifactRelationshipDAO()


@lru_cache
def get_analysis_dao() -> WorkspaceAnalysisDAO:
    """DI provider: WorkspaceAnalysisDAO for systems, scope, metrics (psycopg3 + BaseRepository)."""
    return WorkspaceAnalysisDAO()


@lru_cache
def get_traceability_service() -> TraceabilityService:
    """DI provider: workspace traceability service (GROUNDS / artifact relationships).

    Injects GroundsLinkDAO and ArtifactRelationshipDAO
    (no SQLAlchemy, no DB session). Uses shared psycopg3 pool for all data access.
    """
    grounds_dao = get_grounds_dao()
    relationship_dao = get_relationship_dao()
    return TraceabilityService(
        grounds_dao=grounds_dao,
        relationship_dao=relationship_dao,
    )


@lru_cache
def get_trace_graph_service() -> TraceGraphService:
    """DI provider: item-level trace-graph assembler (RTM P2).

    Builds the requirement→system→rule→story→file→test item graph from stage artifacts + grounds,
    hydrating the system→rule leg from the KB graph. Reuses the grounds DAO (fetch_all) + GraphProvider.
    """
    from app.services.graph_provider import get_graph_provider

    return TraceGraphService(dao=get_grounds_dao(), graph_provider=get_graph_provider())


@lru_cache
def get_persona_registry() -> PersonaRegistry:
    """DI provider: persona manifest + 3-point enforcement guards (singleton)."""
    return _gpr()


@lru_cache
def get_kb_query_service() -> KbQueryService:
    """DI provider: deterministic persona-filtered kb.query (fixture-backed until the KB lands)."""
    return KbQueryService()


@lru_cache
def get_chat_service() -> ChatService:
    """DI provider: chatbot orchestration (guardrails → intake → query-PII-mask → kb.query → session)."""
    return _gcsvc()


@lru_cache
def get_change_classifier() -> ChangeClassifier:
    """DI provider: the ANALYSIS change-classifier (requirement -> New/Enhancement vs the KB)."""
    return ChangeClassifier()


@lru_cache
def get_persona_specialist() -> PersonaSpecialist:
    """DI provider: the config-driven grounded persona specialist.

    Uses the real Bedrock model at runtime (``get_chat_model``); falls back to the deterministic
    grounded path when no model is configured (dev/test/no-Bedrock).
    """
    return PersonaSpecialist(
        service=WorkspaceService(),
        kb_query=KbQueryService(),
        trace=get_traceability_service(),
        personas=_gpr(),
        model=get_chat_model(),
    )


@lru_cache
def get_impact_analysis_service() -> ImpactAnalysisService:
    """DI provider: the detailed grounded Impact Analysis service (ANALYSIS stage).

    Real Bedrock model at runtime (``get_chat_model``); deterministic grounded fallback when no
    model is configured. Graph walk via the process-wide ``GraphProvider`` singleton.
    """
    from app.services.graph_provider import get_graph_provider

    return ImpactAnalysisService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        model=get_chat_model(),
    )


@lru_cache
def get_intake_service() -> IntakeService:
    """DI provider: S0·INTAKE artifact service (requirement snapshot → S3 + DB)."""
    return IntakeService(workspace=WorkspaceService())


@lru_cache
def get_fsd_service() -> FSDService:
    """DI provider: the FSD stage service — reads analysis artifact, runs ReAct, assembles FSDDocument."""
    from app.services.graph_provider import get_graph_provider

    impact = get_impact_analysis_service()
    return FSDService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        impact=impact,
        model=get_chat_model(),
    )


@lru_cache
def get_brd_service() -> BRDService:
    """DI provider: the BRD stage service — reads accepted FSD, runs ReAct, assembles BRDDocument."""
    from app.services.graph_provider import get_graph_provider

    return BRDService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        fsd=get_fsd_service(),
        model=get_chat_model(),
    )


@lru_cache
def get_stories_service() -> StoriesService:
    """DI provider: the Stories stage service — reads accepted BRD, runs ReAct, assembles StoriesDocument."""
    from app.services.graph_provider import get_graph_provider

    return StoriesService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        fsd=get_fsd_service(),  # BRD merged into FSD: Stories is grounded in the FSD
        srd=get_architecture_service(),  # design-first: Stories' prerequisite is the accepted SRD
        model=get_chat_model(),
    )


@lru_cache
def get_architecture_service() -> ArchitectureService:
    """DI provider: the Architecture stage service — reads accepted Stories, runs ReAct, assembles SRDDocument."""
    from app.services.graph_provider import get_graph_provider

    return ArchitectureService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        fsd=get_fsd_service(),  # BRD merged into FSD: Architecture is built from the FSD (+ Analysis)
        impact=get_impact_analysis_service(),
        model=get_chat_model(),
    )


@lru_cache
def get_kb_refresh_service():
    """DI provider: the central KB-refresh round-trip (docs/23) — transforms a merged workspace's
    accepted FSD+SRD into a KB delta and loads it as a NEW STAGING version via the kb-indexer CLI."""
    from app.lifecycle.kb_sync import KbRefreshService

    return KbRefreshService(fsd=get_fsd_service(), architecture=get_architecture_service())


@lru_cache
def get_developer_service() -> DeveloperService:
    """DI provider: the Developer stage service.

    Reads accepted SRD (required) + StoriesDocument (P1, optional) + FSDDocument (P2, optional).
    Both optional services are best-effort — DeveloperService never raises on their absence.
    """
    from app.services.graph_provider import get_graph_provider

    return DeveloperService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        srd=get_architecture_service(),
        stories=get_stories_service(),   # P1 — Gherkin AC + story_text + DoD
        fsd=get_fsd_service(),           # P2 — FR delta (as-is→to-be) + BR summaries
        model=get_chat_model(),
    )


@lru_cache
def get_workspace_bundle_service():
    """DI provider: the workspace bundle read (docs/27 §6.1) — composes the per-stage services into one
    grounded payload for the local Developer plugin. Pure composition; no new persistence."""
    from app.services.workspace_bundle import WorkspaceBundleService

    return WorkspaceBundleService(
        workspace=WorkspaceService(),
        impact=get_impact_analysis_service(),
        fsd=get_fsd_service(),
        srd=get_architecture_service(),
        stories=get_stories_service(),
        developer=get_developer_service(),
    )


@lru_cache
def get_qa_service() -> QAService:
    """DI provider: the QA stage service.

    Reads accepted DevDocument (required) + StoriesDocument + SRDDocument + FSDDocument + BRDDocument
    (all optional — best-effort; QAService never raises on their absence).
    """
    from app.services.graph_provider import get_graph_provider

    return QAService(
        workspace=WorkspaceService(),
        kb_query=KbQueryService(),
        graph=get_graph_provider(),
        personas=_gpr(),
        trace=get_traceability_service(),
        dev=get_developer_service(),
        stories=get_stories_service(),
        srd=get_architecture_service(),
        fsd=get_fsd_service(),
        brd=get_brd_service(),
        model=get_chat_model(),
    )


@lru_cache
def get_developer_offline_service():
    """DI provider: offline developer work submission service (singleton)."""
    from app.lifecycle.stages.developer.offline_service import DeveloperOfflineService  # noqa: PLC0415
    return DeveloperOfflineService(workspace=WorkspaceService(), trace=get_traceability_service())


@lru_cache
def get_qa_offline_service():
    """DI provider: offline QA work submission service (singleton)."""
    from app.lifecycle.stages.qa.offline_service import QAOfflineService  # noqa: PLC0415
    return QAOfflineService(workspace=WorkspaceService(), trace=get_traceability_service())


@lru_cache
def get_fe_generate_service() -> FeGenerateService:
    """DI provider: the fe.generate dispatcher (docs/11 §2.3) — the single FE generate path."""
    return FeGenerateService(
        personas=_gpr(),
        impact=get_impact_analysis_service(),
        fsd=get_fsd_service(),
        brd=get_brd_service(),
        stories=get_stories_service(),
        architecture=get_architecture_service(),
        qa=get_qa_service(),
    )


def get_current_persona(request: Request) -> str:
    """Enforcement point #1 (Okta group -> persona). When ``PERSONA_GROUP_CHECK_ENABLED`` is off (dev),
    return ``DEV_DEFAULT_PERSONA`` without a group check; when on, map the JWT groups on
    ``request.state.user`` to a persona or raise 403."""
    settings = get_settings()
    if not settings.PERSONA_GROUP_CHECK_ENABLED:
        return settings.DEV_DEFAULT_PERSONA
    user = getattr(request.state, "user", None) or {}
    persona = _gpr().resolve_persona(user.get("groups", []))
    if persona is None:
        raise AuthorizationError("no persona is mapped to your Okta groups", {"groups": user.get("groups", [])})
    return persona.id


def get_current_user(request: Request) -> str:
    """The authenticated user's stable id, used to scope chat sessions per user.

    Prefers the Okta JWT ``sub`` (then ``email``/``uid``) placed on ``request.state.user`` by the
    ``EntitlementMiddleware``. That middleware is only registered when ``ENTITLEMENT_ENABLED`` is on,
    so in dev (``ENTITLEMENT_ENABLED=false``) ``request.state.user`` is absent — resolve the identity
    through the same ``AuthService`` the app authenticates with, which returns the dev stub principal
    (``dev-user@aig.com``) under JWT bypass. ``DEV_DEFAULT_USER`` is only the last-resort default."""
    user = getattr(request.state, "user", None) or {}
    uid = user.get("sub") or user.get("email") or user.get("uid")
    if uid:
        return uid
    # Middleware not in the chain (dev, ENTITLEMENT_ENABLED=false): resolve identity from the
    # request's own bearer token. The UI AuthInterceptor attaches the Okta access token, so
    # AuthService (dev-bypass = unverified decode) yields the real sub/email. No token → dev stub.
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header[:7].lower() == "bearer " else ""
    log.debug(
        f"[get_current_user] auth_header={'present (' + auth_header[:30] + '…)' if auth_header else 'MISSING'}"
        f" token_len={len(token)}"
    )
    try:
        principal = _gas().validate_token(token)
        uid = principal.get("sub") or principal.get("email") or principal.get("uid")
    except Exception:  # noqa: BLE001 — no principal resolvable (e.g. live mode with no token here)
        uid = None
    resolved = uid or get_settings().DEV_DEFAULT_USER
    log.debug(f"[get_current_user] resolved uid={resolved!r}")
    return resolved


def get_current_user_name(request: Request) -> str:
    """The authenticated user's display NAME (name → first+last → email/sub); '' if unresolved.

    Used for provenance display (Triggered/Reviewed By). Mirrors ``get_current_user`` resolution:
    prefers the principal on ``request.state.user`` (entitlement middleware), else decodes the
    request's own bearer token via ``AuthService`` (dev-bypass = unverified)."""
    user = getattr(request.state, "user", None) or {}
    name = user.get("name") or user.get("email") or user.get("sub")
    if name:
        return name
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header[:7].lower() == "bearer " else ""
    try:
        principal = _gas().validate_token(token)
        return principal.get("name") or principal.get("email") or principal.get("sub") or ""
    except Exception:  # noqa: BLE001 — no principal (live mode with no token here)
        return ""

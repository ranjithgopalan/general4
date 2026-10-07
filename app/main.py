"""
AIG Core AIDLC Platform — Agents: FastAPI application entry point (FE backend).

Owns: the public REST API, the LangGraph supervisor + persona ReAct specialists,
`kb.query`, the grounding spine, and `fe.generate` subgraphs (all business logic
lands as later milestones). This module is the base framework: lifespan, middleware
(LIFO: CORS → ApiKey → Entitlement), correlation id, exception handlers, health.

M1 (security) and M2 (data) hooks are lazy-imported and guarded, so the app boots
cleanly before those modules exist.
"""

import os
import sys
import time
import traceback
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status

from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import (
    bundle_router,
    chat_router,
    document_export_router,
    fe_analysis_router,
    fe_architecture_router,
    fe_brd_router,
    fe_developer_router,
    fe_fsd_router,
    fe_generate_router,
    fe_impact_router,
    fe_merge_router,
    fe_qa_router,
    fe_relationships_router,
    fe_stories_router,
    health_router,
    overview_router,
    personas_router,
    re_graph_router,
    re_review_router,
    sessions_router,
    versions_router,
    workspace_router,
)
from app.api.observability import router as observability_router
from app.config import get_settings
from app.core.config_validator import validate_configuration
from app.agentic_platform.mount import include_agentic_routers, agentic_startup
from app.utils.exceptions import BaseAppException, ErrorCode
# ── CRITICAL: Windows event loop policy must be set BEFORE any async imports ──────
# This must run FIRST, before any other imports that might create an event loop.
if sys.platform == "win32":
    import asyncio as _asyncio

    _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())

from app.utils.logging import log

# ── M1 (security) — lazy/guarded so M0 boots before these files exist ──────────
try:
    from app.middleware.api_key_middleware import ApiKeyMiddleware
    from app.middleware.entitlement_middleware import EntitlementMiddleware

    _M1_AVAILABLE = True
except ImportError as _m1_err:  # pragma: no cover
    _M1_AVAILABLE = False
    log.warning(f"M1 security middleware not available yet (non-fatal): {_m1_err}")

settings = get_settings()

_EXCLUDED_AUTH_PATHS = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown — validate config, init process-wide singletons, warm caches."""
    log.info("Initializing AIDLC Platform Agents...")
    validate_configuration(settings)

    # ── CRITICAL: Windows event loop — force SelectorEventLoop for psycopg3 ────────
    # Even if the policy is set, uvicorn may have already created a ProactorEventLoop.
    # Explicitly create and set a SelectorEventLoop to ensure psycopg3 compatibility.
    if sys.platform == "win32":
        import asyncio
        try:
            current_loop = asyncio.get_running_loop()
            # We're already in an async context with a running loop.
            # Check if it's a ProactorEventLoop and log a warning if so.
            if current_loop.__class__.__name__ == "ProactorEventLoop":
                log.warning(
                    "[startup] Running on ProactorEventLoop (suboptimal for psycopg3). "
                    "Consider setting PYTHONPATH or running with an explicit event loop policy."
                )
        except RuntimeError:
            # No running loop yet; create one
            pass

    # ── M1 startup hooks (Vault, JWKS pre-warm) ──────────────────────────────
    try:
        from app.utils.hashicorp_client import get_vault_client  # type: ignore

        get_vault_client()
        log.info("[startup] Vault client ready")
    except Exception as exc:
        log.warning(f"[startup] Vault client init skipped/failed (non-fatal): {exc}")

    try:
        from app.services.auth import get_auth_service  # type: ignore

        get_auth_service().pre_warm_jwks()
        log.info("[startup] JWKS pre-warmed")
    except Exception as exc:
        log.warning(f"[startup] JWKS pre-warm skipped/failed (non-fatal): {exc}")

    # ── M2 startup hooks (Postgres async pool) ───────────────────────────────
    try:
        from app.dao.postgres import open_pool  # type: ignore

        await open_pool()
        log.info("[startup] Postgres pool opened")

        # Wire the Postgres telemetry sink now that the pool is ready.
        try:
            from app.dao.postgres import get_pool  # type: ignore
            from app.services.telemetry import register_sink
            from app.services.telemetry_pg_sink import PostgresTelemetrySink

            register_sink(PostgresTelemetrySink(get_pool()))
            log.info("[startup] Postgres telemetry sink registered")
        except Exception as _sink_exc:
            log.warning(f"[startup] Telemetry sink registration skipped (non-fatal): {_sink_exc}")
    except Exception as exc:
        log.warning(f"[startup] Postgres pool init skipped/failed (non-fatal): {exc}")

    # ── M3 startup hooks (Table impact discovery) ─────────────────────────────
    try:
        from app.lifecycle.stages.analysis.extractors.table_reference import TableReferenceExtractor
        from app.services.kb_query import KbQueryService

        # Load table mapping from KB or fallback to empty (table impact discovery is optional)
        # TODO: Load from domain-pack.json when available
        table_mapping = {
            'WEB_RECEIPTS': 'ENT-JAUTO-DB-051',
            'WEB_RECEIPTS_HISTORY': 'ENT-JAUTO-DB-053',
            'DRCT_MSG_REGST': 'ENT-JAUTO-DB-055',
            'PEGA_CUSTOMERS': 'ENT-JAUTO-DB-060',
            'RECEIPT_DETAILS': 'ENT-JAUTO-DB-062',
            'POLICY_INFO': 'ENT-JAUTO-DB-065',
            'CLAIM_HISTORY': 'ENT-JAUTO-DB-068',
            'PAYMENT_RECORDS': 'ENT-JAUTO-DB-070',
        }
        TableReferenceExtractor.set_table_mapping(table_mapping)
        log.info(f"[startup] Table impact discovery initialized with {len(table_mapping)} table mappings")
    except Exception as exc:
        log.debug(f"[startup] Table impact discovery init skipped (non-fatal): {exc}")

    # ── UWCR forward-engineering subsystem (vendored) ─────────────────────────
    # Best-effort; agentic_startup swallows its own failures so it cannot block boot.
    try:
        agentic_startup()
        log.info("[startup] UWCR subsystem ready")
    except Exception as exc:
        log.warning(f"[startup] UWCR subsystem init skipped/failed (non-fatal): {exc}")

    log.info(f"[startup] Agent ready — env={settings.ENV!r} gear={settings.GEAR_ID!r}")
    yield

    log.info("Shutting down AIDLC Platform Agents...")
    try:
        from app.dao.postgres import close_pool  # type: ignore

        await close_pool()
    except Exception:
        pass


app = FastAPI(
    title="AIG Core AIDLC Platform — Agent API",
    description=(
        "FE backend: LangGraph supervisor + persona ReAct specialists over one grounded KB. "
        "Cite-or-abstain. Base framework (plumbing) — business logic lands as later milestones."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV != "prod" else None,
    redoc_url="/redoc" if settings.ENV != "prod" else None,
)


def custom_openapi():
    """Add JWT Bearer auth to the OpenAPI schema (Authorize button in Swagger)."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Okta JWT access token",
        },
    }
    for path, methods in schema["paths"].items():
        if path in _EXCLUDED_AUTH_PATHS:
            continue
        for method in methods:
            methods[method]["security"] = [{"BearerAuth": []}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

# ── Middleware (Starlette runs these LIFO — last added is outermost) ───────────
# Desired dispatch order (outermost → innermost):
#   CORS → ApiKey (verify HMAC) → Entitlement (verify JWT + entitlement) → route
cors_origins = settings.CORS_ORIGINS
if settings.ENV in ("uat", "prod") and "*" in cors_origins:
    raise ValueError("Wildcard CORS origins are not allowed in uat/prod.")
log.info(f"CORS configured for env '{settings.ENV}': {cors_origins}")


class _SSESafeGZipMiddleware(GZipMiddleware):
    """GZip, but never compress SSE endpoints (compression breaks event-stream framing)."""

    _EXCLUDED_PATH_SUFFIXES = ("/stream", "/chat")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith(self._EXCLUDED_PATH_SUFFIXES):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


# Added first → innermost of the explicit stack (closest to routes) after CORS.
if _M1_AVAILABLE and settings.ENTITLEMENT_ENABLED:
    app.add_middleware(EntitlementMiddleware, excluded_paths=_EXCLUDED_AUTH_PATHS)
    log.info("[main] EntitlementMiddleware registered")
elif settings.ENTITLEMENT_ENABLED:
    log.warning("[main] ENTITLEMENT_ENABLED but middleware not available yet")

if _M1_AVAILABLE and settings.API_KEY_ENABLED:
    app.add_middleware(ApiKeyMiddleware, excluded_paths=_EXCLUDED_AUTH_PATHS)
    log.info("[main] ApiKeyMiddleware registered")
elif settings.API_KEY_ENABLED:
    log.warning("[main] API_KEY_ENABLED but middleware not available yet")

app.add_middleware(_SSESafeGZipMiddleware, minimum_size=1000)

# Stamp the request-scoped actor (id + display name) in the route's own task/context, so artifact
# provenance writes (triggered_by / reviewed_by) — including SSE-streamed generates — capture the real
# user. Must be a pure ASGI middleware; a Depends or BaseHTTPMiddleware does not propagate the ContextVar.
from app.middleware.actor_context_middleware import ActorContextMiddleware  # noqa: E402

app.add_middleware(ActorContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(observability_router)  # /observability/* — agent telemetry, eval, approvals, traceability
app.include_router(re_graph_router)  # /re/graph — Knowledge-Graph Explorer (GraphProvider)
app.include_router(re_review_router)  # /re/kb/{v}/review — pre-activation gap ledger (docs/25 Phase 1)
app.include_router(workspace_router)  # /ws — FE workspace lifecycle (docs/19)
app.include_router(personas_router)  # /personas — standalone persona-scoped grounded Q&A (docs/04 §6)
app.include_router(chat_router)     # /fe — chatbot: grounded Q&A (/fe/query) + conversational SSE (/fe/chat) (docs/20)
app.include_router(sessions_router) # /fe/sessions — session list + history (docs/20 §12)
app.include_router(versions_router)  # /re/versions — version listing + deterministic version-diff (docs/20 §9)
app.include_router(overview_router)  # /re/overview — Active-KB Overview dashboard (docs/20 §4a)
# Remaining business surface lands as later milestones: fe.generate (POST /ws/{id}/generate subgraph).
app.include_router(fe_generate_router)  # /ws/{id}/generate — fe.generate, the single FE generate path (docs/11 §2.3)
app.include_router(document_export_router)  # /ws/{id}/export + /templates — document exports with template system
app.include_router(fe_impact_router)  # /ws/{id}/impact — read/lifecycle/export for the Impact Analysis (docs/21)
app.include_router(fe_fsd_router)       # /ws/{id}/fsd — read/lifecycle/export for the FSD stage
app.include_router(fe_brd_router)          # /ws/{id}/brd — read/lifecycle/export for the BRD stage
app.include_router(fe_stories_router)      # /ws/{id}/stories — read/lifecycle/export.csv for the Stories stage
app.include_router(fe_architecture_router) # /ws/{id}/srd — read/lifecycle/export for the Architecture (SRD) stage
app.include_router(fe_developer_router)    # /ws/{id}/dev — read/generate(SSE)/accept/export.docx/codegen(SSE)
app.include_router(bundle_router)          # /ws/{id}/bundle — grounded workspace bundle for the local Developer plugin (docs/27 §6.1)
app.include_router(fe_qa_router)           # /ws/{id}/qa — read/accept/export.docx/execute for the QA stage
app.include_router(fe_relationships_router) # /ws/{id}/relationships — artifact-to-artifact traceability links
app.include_router(fe_analysis_router)      # /ws/{id}/analysis/* — systems, scope, metrics (unified)
app.include_router(fe_merge_router)         # /ws/{id}/merge — MERGE (DevOps): background KB-refresh round-trip (docs/23)
# TODO(business): remaining /re routers.

# ── UWCR forward-engineering subsystem — vendored pipeline API under /agentic_platform ──────
include_agentic_routers(app)  # /agentic_platform/health, /agentic_platform/api/v1/{projects,runs,workspaces,...}


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    """Attach a correlation id, time the request, log start/finish."""
    correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    start = time.time()
    try:
        response = await call_next(request)
        elapsed = time.time() - start
        log.info(
            f"{request.method} {request.url.path} -> {response.status_code} ({elapsed:.3f}s) | id={correlation_id}"
        )
        response.headers["X-Request-ID"] = correlation_id
        return response
    except Exception as exc:
        elapsed = time.time() - start
        log.error(
            f"{request.method} {request.url.path} FAILED ({elapsed:.3f}s) | id={correlation_id}: {exc}", exc_info=True
        )
        raise


# ── Exception handlers ───────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    errors = [
        {
            "location": " -> ".join(str(loc) for loc in e.get("loc", [])),
            "message": e.get("msg", ""),
            "type": e.get("type", ""),
        }
        for e in exc.errors()
    ]
    log.warning(f"Validation error: {request.url.path} | id={correlation_id}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_code": ErrorCode.VALIDATION_ERROR,
            "message": "Request validation error",
            "details": {"errors": errors},
            "request_id": correlation_id,
        },
    )


@app.exception_handler(BaseAppException)
async def app_exception_handler(request: Request, exc: BaseAppException):
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    log.error(f"{exc.error_code}: {exc.message} | id={correlation_id}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "request_id": correlation_id,
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    log.error(f"Unhandled exception: {exc} | id={correlation_id}", exc_info=True)
    details = {"error": str(exc), "traceback": traceback.format_exc()} if settings.DEBUG else {}
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": ErrorCode.INTERNAL_SERVER_ERROR,
            "message": "Internal server error",
            "details": details,
            "request_id": correlation_id,
        },
    )


if __name__ == "__main__":
    # CRITICAL: Set Windows event loop policy AGAIN before uvicorn creates the event loop
    # This ensures psycopg3 can use async mode on Windows
    if sys.platform == "win32":
        import asyncio as _asyncio_main
        _asyncio_main.set_event_loop_policy(_asyncio_main.WindowsSelectorEventLoopPolicy())

    port = int(os.getenv("PORT", str(settings.PORT)))
    # NOTE: On Windows, uvicorn's reload mode doesn't work well with psycopg3's async mode.
    # The reload worker creates a new event loop that ignores our WindowsSelectorEventLoopPolicy.
    # Disable reload on Windows to avoid "ProactorEventLoop" errors.
    reload = settings.ENV == "dev" and sys.platform != "win32"
    if sys.platform == "win32" and settings.ENV == "dev":
        log.warning(
            "[startup] Windows detected: uvicorn reload disabled (incompatible with psycopg3 async). "
            "Restart the backend manually when you change code."
        )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        # Watch ONLY our source, never .venv/site-packages. Watching the whole cwd makes the reloader
        # fire on any dependency change (e.g. a pip install touching pydantic) and churn the Windows
        # reload worker (os.fdopen(stdin) KeyboardInterrupt). Scope it to app/ so reloads are source-only.
        reload_dirs=["app"] if reload else None,
        log_level="debug" if settings.DEBUG else "info",
        workers=settings.WORKERS if settings.ENV != "dev" else 1,
    )

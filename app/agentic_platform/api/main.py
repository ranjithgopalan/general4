"""Control-plane API for the UW CR forward-engineering pipeline.

PRD 5.2 boundary: this app authenticates, exposes application/context/run/
approval/artifact APIs and streams events. It must not contain generated
business logic or expose secrets to clients. Agent execution happens in the
worker (`apps/worker`), which is a separate deployable.

Run:
    uvicorn api_app.main:app --reload --port 8100
"""

from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agentic_platform.api.routers import (
    catalog,
    health,
    kb_cards,
    library,
    logs,
    projects,
    rag,
    re_graph,
    red_graph,
    runs,
    schema,
    sync,
    workspaces,
)
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.pipeline.registry import registry
from app.agentic_platform.fe_core.plugins import PluginLoadError, discover

logger = logging.getLogger(__name__)


class _DropKbUnreachable(logging.Filter):
    """Drop every record about the optional KB being unreachable.

    This deployment does not run the source-code KB (lmod_AIG, port 8000), so
    "Cannot reach the knowledge base ..." repeats from every caller that probes
    it — project listing, artefact publish retries, KB mirroring — and buries
    the log. The degraded state still reaches the UI in API responses; the
    operator asked for it to stay out of the log entirely.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "Cannot reach the knowledge base" not in record.getMessage()


def _configure_logging(level: str) -> None:
    from app.agentic_platform.fe_core.config import SERVICE_ROOT

    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    kb_filter = _DropKbUnreachable()

    # Console handler — always on.
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(kb_filter)

    # File handler — writes to logs/api.log next to the project root.
    log_dir = SERVICE_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    file_h = logging.handlers.RotatingFileHandler(
        log_dir / "api.log", maxBytes=10_485_760, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    file_h.addFilter(kb_filter)

    root = logging.getLogger()
    root.setLevel(numeric)
    # Avoid duplicate handlers on uvicorn --reload (which re-imports this module).
    if not root.handlers:
        root.addHandler(console)
        root.addHandler(file_h)
    else:
        # Already configured — just attach the file handler if missing.
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            root.addHandler(file_h)
        for handler in root.handlers:
            if not any(isinstance(f, _DropKbUnreachable) for f in handler.filters):
                handler.addFilter(kb_filter)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.fe_log_level)
    logger.info(
        "UW CR forward-engineering API starting on %s:%d",
        settings.fe_host, settings.fe_port,
    )

    # Pipeline definitions must be valid: a malformed YAML should not be
    # discovered on the first run request.
    try:
        loaded = registry()
        logger.info("Pipelines loaded: %s", ", ".join(sorted(loaded)))
    except Exception as exc:  # noqa: BLE001
        logger.error("Pipeline registry failed to load: %s", exc)

    try:
        found = discover(settings.genlite_plugin_root)
        logger.info("GATHER plugins discovered: %d", len(found))
    except PluginLoadError as exc:
        logger.warning("GATHER plugins unavailable: %s", exc)

    # FR-015: a run left RUNNING by a restart would otherwise be stuck forever.
    try:
        from app.agentic_platform.api.services.jobs import JobService

        recovered = JobService().recover_orphans()
        if recovered:
            logger.warning("Recovered %d interrupted run(s)", len(recovered))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orphan recovery skipped: %s", exc)

    logger.info(
        "KB transport=%s, agent tools=%s, runner=%s",
        settings.fe_kb_transport, settings.fe_kb_agent_tools, settings.fe_runner,
    )
    if not settings.fe_auth_enabled:
        # Loud on every start. the knowledge base's equivalent returns a mock admin silently,
        # which is indistinguishable from working auth in production.
        logger.warning(
            "AUTHENTICATION IS DISABLED. Every caller holds all eight personas "
            "plus administrator, so neither the Global/Hybrid code boundary "
            "(FR-P1) nor per-persona approval authority (FR-P3) is enforced. "
            "Set FE_AUTH_ENABLED=true with FE_AUTH_ISSUER before any multi-user "
            "deployment."
        )
    yield
    logger.info("API shutting down")


app = FastAPI(
    title="UW CR Forward Engineering API",
    description=(
        "Control plane for the 15-stage UW Credit Risk forward-engineering "
        "pipeline over the knowledge base knowledge base. Agents execute GATHER plugins "
        "through the Claude Agent SDK or the Claude Code CLI."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

_settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    # The workspace UI is a separate deployable; the KB platform's own UI ports are allowed
    # so it can link across.
    allow_origins=[
        "http://localhost:4100", "http://localhost:4000", "http://localhost:4200",
        "http://localhost:4201",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router, prefix=_settings.fe_api_prefix)
app.include_router(library.router, prefix=_settings.fe_api_prefix)
app.include_router(rag.router, prefix=_settings.fe_api_prefix)
app.include_router(kb_cards.router, prefix=_settings.fe_api_prefix)
app.include_router(catalog.router, prefix=_settings.fe_api_prefix)
app.include_router(workspaces.router, prefix=_settings.fe_api_prefix)
app.include_router(runs.router, prefix=_settings.fe_api_prefix)
app.include_router(schema.router, prefix=_settings.fe_api_prefix)
app.include_router(sync.router, prefix=_settings.fe_api_prefix)
app.include_router(logs.router, prefix=_settings.fe_api_prefix)
app.include_router(re_graph.router, prefix=_settings.fe_api_prefix)
app.include_router(red_graph.router, prefix=_settings.fe_api_prefix)


@app.get("/", tags=["health"])
async def root() -> dict:
    return {
        "service": "uwcr-forward-engineering",
        "version": "0.2.0",
        "api": _settings.fe_api_prefix,
        "docs": "/docs",
        "deep_health": "/health/deep",
    }

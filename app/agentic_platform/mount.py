"""Mount the vendored UW CR forward-engineering subsystem onto the agent app.

The UWCR pipeline API (originally its own FastAPI service in the
``forward_engineering`` repo) is vendored under :mod:`app.agentic_platform`. Rather than run a
second ASGI app, its routers are attached to the agent's existing FastAPI
instance under a single ``/agentic_platform`` prefix so one ``uvicorn`` process serves both
surfaces. This keeps the agent Dockerfile/pipeline unchanged.

Route layout after mounting:

    /agentic_platform/health, /agentic_platform/health/deep, /agentic_platform/config   (liveness + config)
    /agentic_platform/api/v1/...                                 (projects, runs, workspaces, ...)

``agentic_startup`` runs the same one-time checks the standalone service did
(pipeline registry load, plugin discovery, orphan recovery). It is intentionally
best-effort: a failure logs and is swallowed so it can never block agent boot.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.agentic_platform.api.routers import (
    catalog,
    evaluation,
    health,
    kb_cards,
    library,
    logs,
    projects,
    rag,
    red_graph,
    runs,
    schema,
    sync,
    workspaces,
)
from app.agentic_platform.fe_core.config import get_settings

logger = logging.getLogger(__name__)

#: Single namespace for the whole vendored subsystem so nothing collides with the
#: agent's own /health, /ws, /re, /fe surface.
AGENTIC_PREFIX = "/agentic_platform"

#: Routers that carry the /api/v1 business surface (everything except health).
_API_ROUTERS = (projects, library, rag, kb_cards, catalog, workspaces, runs, schema, sync, logs, evaluation, red_graph)


def include_agentic_routers(app: FastAPI) -> None:
    """Attach every UWCR router to ``app`` under the ``/agentic_platform`` namespace."""
    settings = get_settings()
    api_prefix = f"{AGENTIC_PREFIX}{settings.fe_api_prefix}"

    # Health/config sit directly under /agentic_platform (not /api/v1) to mirror the original.
    app.include_router(health.router, prefix=AGENTIC_PREFIX)
    for module in _API_ROUTERS:
        app.include_router(module.router, prefix=api_prefix)

    logger.info("UWCR subsystem mounted at %s (api at %s)", AGENTIC_PREFIX, api_prefix)


def _load_pipelines() -> None:
    from app.agentic_platform.fe_core.pipeline.registry import registry

    loaded = registry()
    logger.info("UWCR pipelines loaded: %s", ", ".join(sorted(loaded)))


def _discover_plugins() -> None:
    from app.agentic_platform.fe_core.plugins import discover

    found = discover(get_settings().genlite_plugin_root)
    logger.info("UWCR GATHER plugins discovered: %d", len(found))


def _recover_orphans() -> None:
    from app.agentic_platform.api.services.jobs import JobService

    recovered = JobService().recover_orphans()
    if recovered:
        logger.warning("UWCR recovered %d interrupted run(s)", len(recovered))


def agentic_startup() -> None:
    """Best-effort startup checks for the UWCR subsystem.

    Each step is isolated: a broken pipeline YAML, a missing plugin tree, or an
    unavailable store must not stop the agent from booting.
    """
    for step in (_load_pipelines, _discover_plugins, _recover_orphans):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - startup must stay non-fatal
            logger.warning("UWCR startup step %s skipped: %s", step.__name__, exc)

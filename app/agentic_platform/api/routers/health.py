"""Health and diagnostics.

`/health/deep` reports whether each dependency is *actually usable*, not merely
configured. It exists because the two most expensive failures in this estate are
both silent: a stub `claude_agent_sdk` whose import succeeds, and a GATHER plugin
path that the runner skips without complaint.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb.gateway import build_gateway
from app.agentic_platform.fe_core.pipeline.registry import get_pipeline, registry
from app.agentic_platform.fe_core.plugins import PluginLoadError, discover
from app.agentic_platform.fe_core.store import get_store

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness only. Never fails because a dependency is down."""
    return {"status": "ok", "service": "uwcr-forward-engineering"}


@router.get("/health/deep")
async def health_deep() -> dict:
    settings = get_settings()
    report: dict = {"status": "ok", "checks": {}}

    def degrade(name: str, payload: dict) -> None:
        report["checks"][name] = payload
        if not payload.get("ok", True):
            report["status"] = "degraded"

    # --- runner: which of sdk / cli can actually execute -------------------
    try:
        from app.agentic_platform.worker.runners.cli_runner import CliRunner
        from app.agentic_platform.worker.runners.sdk_runner import SdkRunner, sdk_is_stub, sdk_location

        sdk_ok, sdk_detail = await SdkRunner().preflight()
        cli_ok, cli_detail = await CliRunner(binary=settings.claude_cli_path).preflight()
        degrade("runner", {
            "ok": sdk_ok or cli_ok,
            "configured": settings.fe_runner,
            "sdk": {"ok": sdk_ok, "detail": sdk_detail,
                    "is_stub": sdk_is_stub(), "location": sdk_location()},
            "cli": {"ok": cli_ok, "detail": cli_detail},
            "note": (
                "Neither adapter passes --dangerously-skip-permissions; each stage "
                "runs with its declared tool allowlist."
            ),
        })
    except Exception as exc:  # noqa: BLE001
        degrade("runner", {"ok": False, "error": str(exc)})

    # --- GATHER plugins ---------------------------------------------------
    try:
        found = discover(settings.genlite_plugin_root)
        degrade("plugins", {
            "ok": True,
            "root": str(settings.genlite_plugin_root),
            "count": len(found),
            "names": sorted(found),
        })
    except PluginLoadError:
        degrade("plugins", {
            "ok": True,
            "root": str(settings.genlite_plugin_root),
            "count": 0,
            "names": [],
            "note": "plugin root absent — stages run via builtin handlers",
        })

    # --- pipelines --------------------------------------------------------
    try:
        loaded = registry()
        active = get_pipeline()
        known = set(report["checks"].get("plugins", {}).get("names", []))
        missing = [n for n in active.required_plugins() if n not in known]
        degrade("pipelines", {
            "ok": not missing,
            "available": sorted(loaded),
            "active": active.name,
            "stages": len(active.stages),
            "required_plugins_missing": missing,
        })
    except Exception as exc:  # noqa: BLE001
        degrade("pipelines", {"ok": False, "error": str(exc)})

    # --- knowledge base ---------------------------------------------------
    try:
        gateway = build_gateway(settings)
        kb_ok, kb_detail = await gateway.ping()
        degrade("knowledge_base", {
            "ok": kb_ok,
            "service_transport": settings.fe_kb_transport,
            "agent_tools": settings.fe_kb_agent_tools,
            "detail": kb_detail,
            "note": (
                "agent_tools=none is the non-MCP variant: context is injected into "
                "the prompt and the agent is granted no KB tools."
            ),
        })
    except Exception as exc:  # noqa: BLE001
        degrade("knowledge_base", {"ok": False, "error": str(exc)})

    # --- datasources (config only; no connection attempted) ---------------
    try:
        from app.agentic_platform.fe_core.db.datasource import load_datasources

        sources = load_datasources(settings.fe_datasources_file)
        degrade("datasources", {
            "ok": True,
            "file": str(settings.fe_datasources_file),
            "items": [
                {
                    "name": d.name, "dialect": d.dialect, "readonly": d.readonly,
                    "configured": d.is_configured(), "password_set": d.password_available(),
                }
                for d in sources
            ],
            "note": "no connection attempted here; introspection is read-only",
        })
    except Exception as exc:  # noqa: BLE001
        degrade("datasources", {"ok": False, "error": str(exc)})

    # --- authentication ---------------------------------------------------
    auth_ok = settings.fe_auth_enabled and bool(settings.fe_auth_issuer)
    degrade("auth", {
        "ok": auth_ok,
        "enabled": settings.fe_auth_enabled,
        "issuer": settings.fe_auth_issuer,
        "audience_checked": bool(settings.fe_auth_audience),
        "note": (
            "AUTH DISABLED - every caller is treated as an administrator. "
            "Development only; never deploy in this state."
            if not settings.fe_auth_enabled
            else "Okta OIDC: tokens validated against the issuer's JWKS."
        ),
    })

    # --- store ------------------------------------------------------------
    store = get_store()
    report["checks"]["store"] = {
        "ok": True,
        "kind": getattr(store, "kind", "json"),
        "path": str(getattr(store, "path", getattr(store, "schema", ""))),
        "runs": len(store.list_runs()),
        "pending_outbox": len(store.pending_outbox(500)),
    }

    # --- platform topology (ECS readiness) --------------------------------
    s = get_settings()
    topo = {
        "store": s.fe_store, "artifact_store": s.fe_artifact_store, "execution": s.fe_execution,
        "orchestrator": s.fe_orchestrator, "tools_mcp": s.fe_tools_mcp, "runner": s.fe_runner,
        "claude_cli_use": s.claude_cli_use, "s3_bucket": s.fe_s3_bucket if s.fe_artifact_store == "s3" else None,
    }
    try:
        from app.agentic_platform.fe_core.artifacts.store import get_artifact_store  # noqa: PLC0415
        a = get_artifact_store(s)
        topo["artifact_store_ok"] = True
        topo["artifact_store_uri"] = a.uri("") if a.kind == "s3" else str(getattr(a, "root", ""))
    except Exception as exc:  # noqa: BLE001
        topo["artifact_store_ok"] = False
        topo["artifact_store_error"] = str(exc)[:200]
        report["status"] = "degraded"
    report["checks"]["topology"] = topo
    return report


@router.get("/config")
async def config() -> dict:
    """Effective configuration.

    Datasource credentials are absent by construction -- `datasources.yaml`
    references them by environment-variable name, so there is nothing to redact.
    """
    return get_settings().redacted()

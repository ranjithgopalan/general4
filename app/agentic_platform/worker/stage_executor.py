"""Executes one pipeline stage through the ClaudeRunner port.

Responsibilities, in PRD terms:
  * build the prompt from approved upstream artefacts plus KB context
  * grant only the stage's declared tools (FR-018)
  * run in an isolated per-run worktree (FR-017)
  * record full provenance (FR-019)
  * persist exactly one Draft artefact per declared output, idempotently (FR-023)
  * treat "generated but not persisted" as a failure (FR-022)

It never approves anything. Approval is a separate, human, API-side action
(FR-024) -- and in CLI mode it *must* be, because in-loop permission callbacks
cannot be relied upon.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import operator
import os
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, TypedDict

from app.agentic_platform.fe_core.config import Settings, get_settings
from app.agentic_platform.fe_core.kb.gateway import KbGateway, build_gateway
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.pipeline.eligibility import readable_workspace_ids, require_runnable
from app.agentic_platform.fe_core.artifacts.reader import materialise, read_markdown_files
from app.agentic_platform.fe_core.artifacts.store import artifact_key, get_artifact_store, local_tree_manifest
from app.agentic_platform.fe_core.pipeline.project_config import load_project_config, project_config_block, constraint_sentence
from app.agentic_platform.fe_core.pipeline.models import (
    OwnerKind,
    Pipeline,
    Stage,
    StageRun,
    StageState,
)
from app.agentic_platform.fe_core.pipeline.registry import get_pipeline
from app.agentic_platform.fe_core.plugins import discover, load_plugin
from app.agentic_platform.fe_core.ports.claude_runner import (
    ClaudeRunner,
    McpServerSpec,
    PermissionPolicy,
    PluginSpec,
    RunLimits,
    RunRequest,
    RunResult,
    ToolPermission,
)
from app.agentic_platform.fe_core.store import JsonFileStore, get_store, make_idempotency_key, sha256
from app.services.telemetry import EventType, get_telemetry_service
from app.utils.request_context import set_agent_run_id, set_workflow_run_id, get_workflow_run_id


def _win_long(p: Path) -> str:
    """Extended-length path string on Windows to bypass MAX_PATH (260 chars)."""
    if os.name != "nt":
        return str(p)
    s = str(p.resolve())
    return s if s.startswith("\\\\?\\") else "\\\\?\\" + s


def _walk_files(root: Path):
    """Enumerate files under root with Windows long-path support (MAX_PATH bypass)."""
    root_str = _win_long(root)
    pfx = "\\\\?\\" if os.name == "nt" else ""
    for dirpath, _, filenames in os.walk(root_str):
        real_dir = dirpath[len(pfx):] if pfx and dirpath.startswith(pfx) else dirpath
        for name in filenames:
            yield Path(real_dir) / name

logger = logging.getLogger(__name__)

_POLICY_MAP = {
    "deny_unlisted": PermissionPolicy.DENY_UNLISTED,
    "accept_edits": PermissionPolicy.ACCEPT_EDITS,
    "plan_only": PermissionPolicy.PLAN_ONLY,
}

# Stages that narrow/decompose an upstream artifact into a more specific form.
_REFINES_STAGES = frozenset({"feature", "user-story"})
# Stages that turn a design/spec into a concrete implementation artifact.
_IMPLEMENTS_STAGES = frozenset({"lld", "ui-code", "api-code", "db-integration", "feature-branch"})
# Stages that produce test artifacts that verify upstream code/implementation.
_TEST_STAGES = frozenset({"api-test", "ui-smoke", "ui-e2e"})

# Upstream artifact types that are refined by _REFINES_STAGES.
_REFINES_SOURCES = frozenset({"epic", "feature"})
# Upstream artifact types that are implemented by _IMPLEMENTS_STAGES.
_IMPLEMENTS_SOURCES = frozenset({
    "user-story", "ui-tdd", "feature-tdd", "service-tdd",
    "api-data-access", "lld",
})
# Upstream artifact types that are tested by _TEST_STAGES.
_TESTS_SOURCES = frozenset({
    "api-source", "api-openapi", "api-integration", "api-batch",
    "ui-source", "api-code", "db-code",
})


def _edge_type(stage_key: str, upstream_type: str) -> str:
    """Return the most specific relationship type for a stage→upstream pair."""
    if stage_key in _REFINES_STAGES and upstream_type in _REFINES_SOURCES:
        return "refines"
    if stage_key in _IMPLEMENTS_STAGES and upstream_type in _IMPLEMENTS_SOURCES:
        return "implements"
    if stage_key in _TEST_STAGES and upstream_type in _TESTS_SOURCES:
        return "tests"
    return "derives_from"


class StageExecutionError(RuntimeError):
    pass


class _ModuleExtractionState(TypedDict, total=False):
    """State for the per-module RE Graph extraction LangGraph subgraph."""
    gear_id: str
    business_area: str | None
    app_id: str | None
    workspace: str                           # Path serialised as str
    modules: list[str]                       # populated by fetch_modules node
    extractions: Annotated[list[str], operator.add]  # accumulated per-module Markdown strings


class StageExecutor:
    def __init__(
        self,
        runner: ClaudeRunner,
        *,
        pipeline: Pipeline | None = None,
        settings: Settings | None = None,
        store: JsonFileStore | None = None,
        kb: KbGateway | None = None,
    ):
        self.runner = runner
        self.settings = settings or get_settings()
        self.pipeline = pipeline or get_pipeline()
        self.store = store or get_store()
        self.kb = kb or build_gateway(self.settings)
        # KB chunks retrieved per (project, stage) during build_prompt; consumed by execute()
        self._kb_hits: dict[tuple[str, str], list[dict]] = {}

    # -- request assembly -------------------------------------------------
    def _plugin_specs(self, stage: Stage) -> list[PluginSpec]:
        if stage.owner.kind is not OwnerKind.PLUGIN or not stage.owner.plugin:
            return []
        root = self.settings.genlite_plugin_root
        plugin_dir = root / stage.owner.plugin
        if not plugin_dir.is_dir():
            available = ", ".join(sorted(discover(root))) if root.is_dir() else "none"
            raise StageExecutionError(
                f"stage '{stage.key}' requires plugin '{stage.owner.plugin}', which is "
                f"not present under {root}. Available: {available}"
            )
        plugin = load_plugin(plugin_dir)
        skill = stage.owner.qualified_skill
        return [PluginSpec(
            name=plugin.name,
            path=plugin.path,
            required_skills=(skill,) if skill else (),
        )]

    def _mcp_specs(self) -> list[McpServerSpec]:
        """MCP servers granted to the agent session.

        * fe-tools (FE_TOOLS_MCP=stdio|http): the platform's own tool surface —
          kb_query / kb_read / fe_artifact_get / fe_workspace_bundle / dev_pr.
          Spawned per session over stdio, or pointed at the shared http server.
        * kb (FE_KB_AGENT_TOOLS=mcp): the legacy KB MCP server, when configured.
        """
        specs: list[McpServerSpec] = []
        # fe-tools: always available to server-side sessions; the API base URL is
        # this service, the service key travels via env (never in the file).
        import sys as _sys  # noqa: PLC0415
        tools_mode = getattr(self.settings, "fe_tools_mcp", "stdio")
        tools_env = {"FE_TOOLS_API_BASE_URL": f"http://{self.settings.fe_host if self.settings.fe_host != '0.0.0.0' else '127.0.0.1'}:{self.settings.fe_port}",
                     "PYTHONPATH": os.pathsep.join(p for p in _sys.path if p)}
        if tools_mode == "http" and getattr(self.settings, "fe_tools_http_url", None):
            specs.append(McpServerSpec(name="fe-tools", command="", args=(),
                                       env={"FE_TOOLS_HTTP_URL": self.settings.fe_tools_http_url}))
        else:
            specs.append(McpServerSpec(name="fe-tools", command=_sys.executable,
                                       args=("-m", "fe_tools.server", "--transport", "stdio"),
                                       env=tools_env))
        if not self.settings.agent_grants_mcp():
            return specs
        launch = self.settings.kb_mcp_launch()
        if not launch.get("cwd"):
            logger.warning(
                "FE_KB_AGENT_TOOLS=mcp but KB_BACKEND_ROOT is unset, so the KB "
                "MCP server cannot be launched. Running without agent KB tools; "
                "context is injected into the prompt instead."
            )
            return specs
        specs.append(McpServerSpec(
            name="kb",
            command=launch["command"],
            args=tuple(launch["args"]),
            cwd=launch["cwd"],
        ))
        return specs

    def _permissions(self, stage: Stage) -> ToolPermission:
        return ToolPermission(
            allow=tuple(stage.permissions.allow),
            deny=tuple(stage.permissions.deny),
            policy=_POLICY_MAP[stage.permissions.policy],
        )

    def _limits(self, stage: Stage) -> RunLimits:
        return RunLimits(
            max_turns=stage.max_turns or self.settings.fe_max_turns,
            timeout_seconds=stage.timeout_seconds or self.settings.fe_timeout_seconds,
            max_retries=self.settings.fe_max_retries,
        )

    def workspace_spend(self, run: StageRun) -> tuple[float, int]:
        """(USD reported by the harness, total tokens) across the workspace's runs."""
        runs = self.store.list_runs(self.pipeline.name, workspace_id=run.workspace_id) \
            if run.workspace_id else self.store.list_runs(self.pipeline.name)
        cost = sum(r.cost_usd or 0.0 for r in runs if r.run_id != run.run_id)
        tokens = sum(r.input_tokens + r.output_tokens + r.cache_read_input_tokens
                     + r.cache_creation_input_tokens
                     for r in runs if r.run_id != run.run_id)
        return cost, tokens

    def _enforce_workspace_budget(self, run: StageRun) -> None:
        """Refuse to start a run once the workspace has spent its USD allowance."""
        cap = self.settings.fe_max_cost_per_workspace_usd
        if not cap:
            return
        spent, _ = self.workspace_spend(run)
        if spent >= cap:
            msg = (f"workspace {run.workspace_id or '(unscoped)'} has spent "
                   f"${spent:.2f} >= budget ${cap:.2f} (FE_MAX_COST_PER_WORKSPACE_USD); "
                   f"refusing to start {run.stage_key}")
            run.transition(StageState.FAILED, error=msg, error_code="budget_exceeded")
            self.store.save_run(run)
            logger.warning(msg)
            raise StageExecutionError(msg)

    # -- RE Graph extraction (PRD stage) ----------------------------------------
    # Architecture: LangGraph 3-node subgraph (fetch_modules → extract_all → aggregate)
    # fetch_modules : GET /re/graph/overview → module list
    # extract_all   : POST /fe/query/raw + Bedrock per module in parallel (asyncio.gather)
    # aggregate     : join — writes all-modules-rules.md to disk + S3

    async def _fetch_graph_rules(
        self,
        module_scope: str | None,
        workspace: Path,
        gear_id: str | None = None,
        business_area: str | None = None,
        role: str | None = None,
        app_id: str | None = None,
    ) -> None:
        """Orchestrate RE Graph extraction via a LangGraph 3-node subgraph.

        All paths go through the same LangGraph graph (fetch_modules → extract_all → aggregate):
          - All modules (module_scope=None): fetch_modules discovers submodule names via
            POST /fe/query/raw, then extract_all processes each in parallel.
          - Specific modules (comma/semicolon list or single name): modules are pre-populated
            in the initial state so fetch_modules skips the discovery API call.

        Only runs when workspace has gear_id (RE Engineering workspace).
        RED/RAG flows independently via _local_context() — never affected here.
        """
        from langgraph.graph import StateGraph, END, START  # noqa: PLC0415
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        # Normalise module_scope:
        #   None or "all" (case-insensitive)  → [] (fetch_modules will discover via API)
        #   comma/semicolon-separated string  → pre-split list (fetch_modules skips API call)
        #   single module name                → single-item list
        import re as _re_split  # noqa: PLC0415
        _scope = (module_scope or "").strip()
        if not _scope or _scope.lower() == "all":
            initial_modules: list[str] = []   # fetch_modules node will discover them
        elif _re_split.search(r"[,;]", _scope):
            initial_modules = [m.strip() for m in _re_split.split(r"\s*[,;]\s*", _scope) if m.strip()]
            logger.info("RE Graph: specific modules pre-set for LangGraph: %s", initial_modules)
        else:
            initial_modules = [_scope]
            logger.info("RE Graph: single module pre-set for LangGraph: %s", initial_modules)

        # Always run through the 3-node LangGraph subgraph
        g = StateGraph(_ModuleExtractionState)
        g.add_node("fetch_modules", self._lg_fetch_modules)
        g.add_node("extract_all", self._lg_extract_all)
        g.add_node("aggregate", self._lg_aggregate)
        g.add_edge(START, "fetch_modules")
        g.add_edge("fetch_modules", "extract_all")
        g.add_edge("extract_all", "aggregate")
        g.add_edge("aggregate", END)
        graph = g.compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        await graph.ainvoke(
            {
                "gear_id": gear_id or "",
                "business_area": business_area,
                "app_id": app_id,
                "workspace": str(workspace),
                "modules": initial_modules,
                "extractions": [],
            },
            config=config,
        )

    async def _lg_fetch_modules(self, state: _ModuleExtractionState) -> dict:
        """LangGraph node 1 — discover submodule names via GET /re/graph/overview.

        Skipped when modules are already pre-populated in state (specific-module path).
        Uses /re/graph/overview (no body validation) instead of POST /fe/query/raw so
        that discovery works even when business_area is not provided.
        """
        # If modules were pre-set by the caller (specific module path), skip discovery
        if state.get("modules"):
            logger.info("RE Graph [fetch_modules]: modules pre-set, skipping discovery: %s",
                        state["modules"])
            return {}

        import httpx  # noqa: PLC0415
        from app.config.settings import get_settings as _gs  # noqa: PLC0415
        _app_settings = _gs()
        base_url = (getattr(self.settings, "RE_GRAPH_BASE_URL", None)
                    or _app_settings.RE_GRAPH_BASE_URL).rstrip("/")
        gear_id = state.get("gear_id") or ""
        # Use GET /re/graph/overview to discover all module names — no body validation required
        params: dict = {}
        if gear_id:
            params["gear_id"] = gear_id
        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            resp = await client.get(f"{base_url}/re/graph/overview", params=params)
            resp.raise_for_status()
            data = resp.json()
        # Extract unique module/submodule names from the response nodes
        nodes = data.get("nodes") or (data if isinstance(data, list) else [])
        seen: set[str] = set()
        modules: list[str] = []
        for n in nodes:
            mod = n.get("module") or n.get("category") or n.get("type")
            if mod and mod not in seen:
                seen.add(mod)
                modules.append(mod)
        logger.info("RE Graph [fetch_modules]: discovered %d submodules via GET /re/graph/overview: %s",
                    len(modules), modules)
        return {"modules": modules}

    async def _lg_extract_all(self, state: _ModuleExtractionState) -> dict:
        """LangGraph node 2 — extract all modules in parallel via asyncio.gather."""
        modules = state.get("modules") or []
        gear_id = state.get("gear_id")
        business_area = state.get("business_area")
        app_id = state.get("app_id")
        if not modules:
            logger.warning("RE Graph [extract_all]: no modules to process")
            return {"extractions": []}
        logger.info("RE Graph [extract_all]: processing %d modules in parallel", len(modules))
        results = await asyncio.gather(
            *[self._extract_module_md(m, gear_id, business_area, app_id) for m in modules],
            return_exceptions=True,
        )
        sections: list[str] = []
        for mod, result in zip(modules, results):
            if isinstance(result, Exception):
                logger.warning("RE Graph [extract_all]: module %r failed (skipped): %s", mod, result)
            else:
                sections.append(f"# Module: {mod}\n\n{result}")
        return {"extractions": sections}

    def _lg_aggregate(self, state: _ModuleExtractionState) -> dict:
        """LangGraph node 3 — join: write combined Markdown file and upload to S3."""
        sections = state.get("extractions") or []
        workspace = Path(state["workspace"])
        self._write_combined(workspace, sections)
        # S3 upload (non-fatal — fire-and-forget in sync context; run in thread if needed)
        gear_id = state.get("gear_id")
        app_id = state.get("app_id")
        try:
            import boto3  # noqa: PLC0415
            from app.config.settings import get_settings as _gs  # noqa: PLC0415
            _app_settings = _gs()
            s3_bucket = (getattr(self.settings, "FE_S3_BUCKET", None)
                         or getattr(self.settings, "S3_BUCKET_NAME", None)
                         or _app_settings.FE_S3_BUCKET)
            region = getattr(self.settings, "AWS_REGION", None) or _app_settings.AWS_REGION
            s3_prefix = getattr(self.settings, "FE_S3_PREFIX", None) or getattr(_app_settings, "FE_S3_PREFIX", "fe")
            ws_folder = app_id or "unknown"
            if s3_bucket:
                combined_md = "\n\n---\n\n".join(sections)
                s3 = boto3.client("s3", region_name=region)
                s3_key = f"{s3_prefix}/{ws_folder}/re-graph-rules/{gear_id}/all-modules-rules.md"
                s3.put_object(Bucket=s3_bucket, Key=s3_key,
                              Body=combined_md.encode("utf-8"), ContentType="text/markdown")
                logger.info("RE Graph [aggregate]: uploaded combined file to S3 s3://%s/%s", s3_bucket, s3_key)
        except Exception as s3_exc:
            logger.warning("RE Graph [aggregate]: S3 upload failed (non-fatal): %s", s3_exc)
        return {}

    def _write_combined(self, workspace: Path, sections: list[str]) -> None:
        """Write all module extraction sections into one combined Markdown file."""
        combined_md = "\n\n---\n\n".join(sections)
        out_dir = workspace / "inputs" / "re-graph-rules"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "all-modules-rules.md"
        out_path.write_text(combined_md, encoding="utf-8")
        logger.info("RE Graph: wrote combined rules (%d sections, %d chars) to %s",
                    len(sections), len(combined_md), out_path)

    async def _upload_combined_s3(self, workspace: Path, gear_id: str | None, app_id: str | None) -> None:
        """Upload all-modules-rules.md to S3 for audit trail (non-fatal)."""
        import boto3  # noqa: PLC0415
        from app.config.settings import get_settings as _gs  # noqa: PLC0415
        try:
            _app_settings = _gs()
            s3_bucket = (getattr(self.settings, "FE_S3_BUCKET", None)
                         or getattr(self.settings, "S3_BUCKET_NAME", None)
                         or _app_settings.FE_S3_BUCKET)
            region = getattr(self.settings, "AWS_REGION", None) or _app_settings.AWS_REGION
            s3_prefix = getattr(self.settings, "FE_S3_PREFIX", None) or getattr(_app_settings, "FE_S3_PREFIX", "fe")
            ws_folder = app_id or "unknown"
            if s3_bucket:
                out_path = workspace / "inputs" / "re-graph-rules" / "all-modules-rules.md"
                content = out_path.read_text(encoding="utf-8")
                s3 = boto3.client("s3", region_name=region)
                s3_key = f"{s3_prefix}/{ws_folder}/re-graph-rules/{gear_id}/all-modules-rules.md"
                s3.put_object(Bucket=s3_bucket, Key=s3_key,
                              Body=content.encode("utf-8"), ContentType="text/markdown")
                logger.info("RE Graph: uploaded combined rules to S3 s3://%s/%s", s3_bucket, s3_key)
        except Exception as s3_exc:
            logger.warning("RE Graph: S3 combined upload failed (non-fatal): %s", s3_exc)

    async def _extract_module_md(
        self,
        module: str,
        gear_id: str | None,
        business_area: str | None,
        app_id: str | None,
    ) -> str:
        """POST /fe/query/raw for one module → Bedrock extraction → return Markdown string.

        Called by _lg_extract_all for each module in parallel.
        No :60000 truncation — per-module JSON is focused and small.
        S3 raw upload key is module-scoped to avoid parallel overwrites.
        """
        import httpx  # noqa: PLC0415
        import boto3  # noqa: PLC0415
        import re as _re  # noqa: PLC0415
        from app.config.settings import get_settings as _gs  # noqa: PLC0415

        _app_settings = _gs()
        base_url = (getattr(self.settings, "RE_GRAPH_BASE_URL", None)
                    or _app_settings.RE_GRAPH_BASE_URL).rstrip("/")
        region = getattr(self.settings, "AWS_REGION", None) or _app_settings.AWS_REGION
        s3_bucket = (getattr(self.settings, "FE_S3_BUCKET", None)
                     or getattr(self.settings, "S3_BUCKET_NAME", None)
                     or _app_settings.FE_S3_BUCKET)
        s3_prefix = getattr(self.settings, "FE_S3_PREFIX", None) or getattr(_app_settings, "FE_S3_PREFIX", "fe")
        ws_folder = app_id or "unknown"
        scope_filename = _re.sub(r'[\t\r\n\\/:*?"<>|,]+', '_', module).strip('_')[:80] or "module"

        # POST /fe/query/raw — role is intentionally NOT sent (API contract)
        payload: dict = {"gear_id": gear_id}
        if module:
            payload["module"] = module
        if business_area:
            payload["business_area"] = business_area

        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            resp = await client.post(f"{base_url}/fe/query/raw", json=payload)
            resp.raise_for_status()
            raw_data = resp.json()

        raw_response_json = json.dumps(raw_data, indent=2)
        logger.info("RE Graph [%s] raw response: %d chars", module, len(raw_response_json))

        # Truncate to ~300K chars before Bedrock — per-module responses can be 6-10MB
        # which exceeds the model's context window. 300K chars ≈ 75K tokens, safely within limits.
        _BEDROCK_CHAR_LIMIT = 300_000
        bedrock_input_json = raw_response_json[:_BEDROCK_CHAR_LIMIT]
        if len(raw_response_json) > _BEDROCK_CHAR_LIMIT:
            logger.info("RE Graph [%s]: truncated input from %d to %d chars for Bedrock",
                        module, len(raw_response_json), _BEDROCK_CHAR_LIMIT)

        # S3 audit: raw response per module (module-scoped key — no parallel overwrite)
        if s3_bucket:
            try:
                s3 = boto3.client("s3", region_name=region)
                s3_key = f"{s3_prefix}/{ws_folder}/re-graph-rules/{gear_id}/_raw-{scope_filename}.json"
                s3.put_object(Bucket=s3_bucket, Key=s3_key,
                              Body=raw_response_json.encode("utf-8"), ContentType="application/json")
                logger.info("RE Graph [%s]: uploaded raw to S3 s3://%s/%s", module, s3_bucket, s3_key)
            except Exception as s3_exc:
                logger.warning("RE Graph [%s]: S3 raw upload failed (non-fatal): %s", module, s3_exc)

        # Extraction mode controlled by RE_RESPONSE_PARSE config flag:
        #   "Deterministic" — Python parser, preserves ALL BR/FR/CMP/ENT/SCR/ROLE nodes (no LLM, no truncation)
        #   "LLM"           — Bedrock extraction on 300K-char truncated raw JSON
        from app.config.settings import get_settings as _gs2  # noqa: PLC0415
        parse_mode = (getattr(self.settings, "RE_RESPONSE_PARSE", None)
                      or _gs2().RE_RESPONSE_PARSE or "Deterministic")
        logger.info("RE Graph [%s]: extraction mode = %s", module, parse_mode)

        if parse_mode.strip().lower() == "llm":
            # LLM path — Bedrock with 300K truncation
            extraction_system = json.loads(
                (Path(__file__).resolve().parent / "runners" / "prompts" / "re-graph-extraction.json")
                .read_text(encoding="utf-8-sig")
            )["system"].format(
                gear_id=gear_id or "",
                module_scope=module,
                business_area=business_area or "",
            )
            model_id = getattr(self.settings, "FE_RAG_ANSWER_MODEL",
                               "us.anthropic.claude-sonnet-4-5-20250929-v1:0")

            def _invoke_bedrock() -> str:
                from botocore.config import Config as _BotoCfg  # noqa: PLC0415
                bedrock = boto3.client(
                    "bedrock-runtime", region_name=region,
                    config=_BotoCfg(read_timeout=600, retries={"max_attempts": 1}),
                )
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 8192,
                    "system": extraction_system,
                    "messages": [{"role": "user", "content": bedrock_input_json}],
                })
                response = bedrock.invoke_model(
                    modelId=model_id, body=body,
                    contentType="application/json", accept="application/json",
                )
                return json.loads(response["body"].read())["content"][0]["text"]

            _re_request_ts = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc)
            extracted_md = await asyncio.to_thread(_invoke_bedrock)
            logger.info("RE Graph [%s]: LLM extracted %d chars", module, len(extracted_md))
            try:
                from app.services import ai_call_recorder as _rec  # noqa: PLC0415
                asyncio.create_task(_rec.record(_rec.AICallRecord(
                    model_provider="bedrock",
                    model_name=model_id,
                    request_ts=_re_request_ts,
                    response_ts=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc),
                    stage_id="re-graph-extraction",
                    request_status="success",
                )))
            except Exception:  # noqa: BLE001
                pass
        else:
            # Deterministic path — Python parser preserves ALL nodes, no truncation
            extracted_md = self._parse_raw_to_sections(module, raw_data)
            logger.info("RE Graph [%s]: deterministic extraction produced %d chars", module, len(extracted_md))

        # S3 audit: individual extracted Markdown per module
        if s3_bucket:
            try:
                s3 = boto3.client("s3", region_name=region)
                s3_key = f"{s3_prefix}/{ws_folder}/re-graph-rules/{gear_id}/{scope_filename}-rules.md"
                s3.put_object(Bucket=s3_bucket, Key=s3_key,
                              Body=extracted_md.encode("utf-8"), ContentType="text/markdown")
                logger.info("RE Graph [%s]: uploaded individual extracted rules to S3 s3://%s/%s",
                            module, s3_bucket, s3_key)
            except Exception as s3_exc:
                logger.warning("RE Graph [%s]: S3 individual MD upload failed (non-fatal): %s", module, s3_exc)

        return extracted_md

    @staticmethod
    def _parse_raw_to_sections(module: str, raw_data: dict) -> str:
        """Deterministic procedural extraction — preserves all BR/FR/ENT/SCR/ROLE cards (CMP excluded).

        POST /fe/query/raw returns {"cards": [...], "card_count": N, ...}
        Each card has: id, kind (BR/SCR/CMP/ENT/ROLE/FR/WF/...), label, source_locus, prose.
        We keep only PRD-relevant kinds and format them into a 5-section Markdown.

        Switch: config RE_RESPONSE_PARSE = "Deterministic" (default) | "LLM"
        """
        # CMP (ASP components/includes) are implementation detail — excluded from PRD input.
        # They belong in FRD/SDD. PRD needs: BR, FR (rules), SCR (screens), ENT (data), ROLE (access).
        KEPT_KINDS = {"BR", "FR", "ENT", "SCR", "ROLE"}

        # ── 1. Extract cards list (actual API response key is "cards") ────────────
        cards: list[dict] = []
        if isinstance(raw_data, list):
            cards = raw_data
        elif isinstance(raw_data, dict):
            cards = (
                raw_data.get("cards")
                or raw_data.get("nodes")
                or raw_data.get("data", {}).get("cards")
                or []
            )

        # ── 2. Partition by "kind" field ──────────────────────────────────────────
        partitions: dict[str, list[dict]] = {k: [] for k in KEPT_KINDS}
        for card in cards:
            kind = (card.get("kind") or card.get("type") or "").upper().strip()
            if kind in KEPT_KINDS:
                partitions[kind].append(card)

        counts = {k: len(v) for k, v in partitions.items()}
        total = sum(counts.values())
        logger.info(
            "RE Graph [%s]: parsed %d cards (BR=%d FR=%d ENT=%d SCR=%d ROLE=%d) "
            "from %d total cards",
            module, total,
            counts["BR"], counts["FR"],
            counts["ENT"], counts["SCR"], counts["ROLE"],
            len(cards),
        )

        if total == 0:
            return (
                f"# Module: {module}\n\n"
                f"_No BR/FR/CMP/ENT/SCR/ROLE cards found. "
                f"Total cards returned: {len(cards)}. "
                f"Kinds present: {list({c.get('kind','?') for c in cards[:20]})}._\n"
            )

        # ── 3. Format as Markdown tables — same structure as RED input format ─────
        # Columns: ID | Rule Name | Type | Description | Source File | Confidence
        TABLE_HEADER = (
            "| ID | Rule Name | Type | Description | Source File | Confidence |\n"
            "|---|---|---|---|---|---|"
        )

        def _source_file(locus: str) -> str:
            # Extract just the filename from source_locus path
            part = locus.split("/")[-1] if "/" in locus else locus
            return part.split(" §")[0][:60]  # strip section anchor, cap length

        def _description(prose: str) -> str:
            # First non-empty line, stripped of markdown bold markers, capped at 120 chars
            for line in prose.splitlines():
                line = line.strip().lstrip("*#").strip()
                if line:
                    return line[:120].replace("|", "\\|")
            return ""

        def _confidence(card: dict) -> str:
            score = card.get("rrf_score") or card.get("dense_score") or 0.0
            return f"{min(int(score * 100 * 33), 99)}%"  # scale rrf (0-0.03) to %

        def _table_row(card: dict) -> str:
            cid = card.get("id", "")
            label = (card.get("label") or cid).replace("|", "\\|")[:60]
            kind = card.get("kind", "")
            prose = card.get("prose") or card.get("summary") or card.get("description") or ""
            desc = _description(prose)
            src = _source_file(card.get("source_locus", ""))
            conf = _confidence(card)
            return f"| {cid} | {label} | {kind} | {desc} | {src} | {conf} |"

        SECTION_MAP = [
            ("Business Rules",     ["BR", "FR"]),
            ("DB Entities",        ["ENT"]),
            ("Screens (→ Angular)", ["SCR"]),
            ("Roles",              ["ROLE"]),
        ]

        lines: list[str] = [f"# Module: {module}\n"]
        for section_title, kinds in SECTION_MAP:
            section_cards = []
            for k in kinds:
                section_cards.extend(partitions.get(k, []))
            if not section_cards:
                continue
            lines.append(f"## {section_title} ({len(section_cards)} rules)\n")
            lines.append(TABLE_HEADER)
            lines.extend(_table_row(c) for c in section_cards)

        return "\n".join(lines)

    @staticmethod
    def _format_graph_sections(
        scope_label: str,
        partitions: dict[str, list[dict]],
        details: dict[str, dict],
    ) -> list[str]:
        """Return one Markdown string per rule/node (BR/FR first, then CMP/ENT/SCR/ROLE)."""
        sections: list[str] = []

        # §1 Business Rules (BR + FR)
        for node in partitions.get("BR", []) + partitions.get("FR", []):
            nid = node["id"]
            d = details.get(nid, {})
            nd = d.get("node", node)
            prose = d.get("prose") or nd.get("summary") or nd.get("label", "")
            evidence = (d.get("evidence") or [{}])[0].get("anchor", "")
            sections.append(
                f"## §1 [{nid}] {nd.get('label', nid)}\n"
                f"**Source:** {nd.get('source_locus', '')}\n"
                f"**Rule:** {prose}\n"
                f"**Evidence:** {evidence}\n"
                f"**Confidence:** {nd.get('confidence', 'MEDIUM')}"
            )

        # §2 Service Candidates (CMP → Spring Boot @Service)
        for node in partitions.get("CMP", []):
            nid = node["id"]
            d = details.get(nid, {})
            nd = d.get("node", node)
            edges = d.get("edges", [])
            reads = [e["target"] for e in edges if e.get("label") in ("READS_FROM", "QUERIES")]
            writes = [e["target"] for e in edges if e.get("label") == "WRITES_TO"]
            impls = [e["target"] for e in edges if e.get("label") == "IMPLEMENTS"]
            sections.append(
                f"## §2 [{nid}] {nd.get('label', nid)}\n"
                f"**Legacy:** {nd.get('source_locus', '')}\n"
                f"**Spring Boot:** {nd.get('label', nid)}Service.java\n"
                f"**READS_FROM/QUERIES:** {', '.join(reads) or 'none'}\n"
                f"**WRITES_TO:** {', '.join(writes) or 'none'}\n"
                f"**IMPLEMENTS rules:** {', '.join(impls) or 'none'}"
            )

        # §3 DB Entities (ENT — kept as-is)
        for node in partitions.get("ENT", []):
            nid = node["id"]
            d = details.get(nid, {})
            nd = d.get("node", node)
            edges = d.get("edges", [])
            read_by = [e["source"] for e in edges if e.get("label") in ("READS_FROM", "QUERIES")]
            written_by = [e["source"] for e in edges if e.get("label") == "WRITES_TO"]
            sections.append(
                f"## §3 [{nid}] {nd.get('label', nid)}\n"
                f"**Table locus:** {nd.get('source_locus', '')}\n"
                f"**Read by:** {', '.join(read_by) or 'none'}\n"
                f"**Written by:** {', '.join(written_by) or 'none'}\n"
                f"**Spring Boot:** @Entity + @Repository (existing schema, no migration)"
            )

        # §4 Screens (SCR → Angular component)
        for node in partitions.get("SCR", []):
            nid = node["id"]
            d = details.get(nid, {})
            nd = d.get("node", node)
            sections.append(
                f"## §4 [{nid}] {nd.get('label', nid)}\n"
                f"**Source:** {nd.get('source_locus', '')}\n"
                f"**Angular component:** {nd.get('label', nid).lower().replace(' ', '-')}.component.ts"
            )

        # §5 Roles (ROLE → Spring Security @PreAuthorize)
        for node in partitions.get("ROLE", []):
            nid = node["id"]
            d = details.get(nid, {})
            nd = d.get("node", node)
            label = nd.get("label", nid)
            sections.append(
                f"## §5 [{nid}] {label}\n"
                f"**Spring Security:** @PreAuthorize(\"hasRole('{label}')\")"
            )

        return sections

    async def build_prompt(
        self, stage: Stage, app_id: str, upstream: list[SdlcArtifact],
        inputs: dict[str, Path | None] | None = None,
        gear_id: str | None = None,
        intake_source: str | None = None,
        workspace: Path | None = None,
    ) -> str:
        # A builtin stage owns its own prompt: Impact Analysis correlates three KB
        # surfaces and must report which of them actually answered, which the
        # generic single-context path below cannot express.
        if stage.owner.kind is OwnerKind.BUILTIN and stage.owner.handler:
            built = await self._builtin_prompt(stage, app_id)
            if built is not None:
                # The builtin owns its analysis prompt, but the project configuration
                # and the output contract (which files to write) apply to every stage;
                # without the contract the deliverable check has nothing to verify.
                project_cfg = load_project_config(self.settings.fe_workspace_root, app_id)
                tools = ", ".join(stage.permissions.allow) or "(none)"
                return "\n\n".join(filter(None, [
                    built,
                    project_config_block(project_cfg, stage.key),
                    self._output_contract(stage, tools, project_cfg),
                ]))

        skill = stage.owner.qualified_skill
        header = f"/{skill}" if skill else f"# Stage: {stage.name}"

        # Context assembly — two completely separate paths that never interfere:
        #   RE Graph flow: gear_id set + intake_source="re-graph"
        #                  → rules pre-extracted to disk by _fetch_graph_rules()
        #                  → agent reads files with Glob/Read tools (no Postgres query)
        #   RED flow:      all other intake sources (reverse-engineering, requirements, etc.)
        #                  → query Postgres RAG (fe_kb_chunk) for indexed RED document chunks
        if gear_id and intake_source == "re-graph" and workspace:
            rules_dir = workspace / "inputs" / "re-graph-rules"
            # Deterministic extraction writes all BR/FR/CMP/ENT/SCR/ROLE cards to disk.
            # The file can be large (10MB+) so we never inject its full content into the prompt —
            # instead we give the PRD agent a file pointer and let it Read the file with its tools.
            if rules_dir.exists() and any(rules_dir.glob("*.md")):
                md_files = sorted(rules_dir.glob("*.md"))
                file_list = "\n".join(f"- `inputs/re-graph-rules/{f.name}` ({f.stat().st_size:,} bytes)" for f in md_files)
                context_block = (
                    "## RE Graph rules\n\n"
                    "The following pre-extracted rule files are available in your working directory. "
                    "Each file contains all BR (Business Rules), FR (Functional Rules), "
                    "ENT (DB Entities), SCR (Screens) and ROLE cards for that module scope.\n\n"
                    f"{file_list}\n\n"
                    "Use your `Read` tool to read `inputs/re-graph-rules/all-modules-rules.md` "
                    "(or individual module files) before generating `prd.md` and `business-rules.md`. "
                    "Ground every requirement, business rule and screen directly in the card IDs "
                    "(e.g. BR-UWCR-001, SCR-UWCR-001) found in those files."
                )
                logger.info("RE Graph: injected file pointer for %d rule file(s) into PRD prompt", len(md_files))
            else:
                context_block = (
                    "## RE Graph rules\n\n"
                    "RE Graph rules have not been pre-extracted yet for this run. "
                    "Use your `Read` tool to check `inputs/re-graph-rules/` if it exists, "
                    "then generate `prd.md` and `business-rules.md` from its content."
                )
            # Upload context block to S3 for audit trail (no local write — ECS-safe)
            from app.config.settings import get_settings as _get_app_settings_bp  # noqa: PLC0415
            _bp_settings = _get_app_settings_bp()
            s3_bucket = (getattr(self.settings, "FE_S3_BUCKET", None)
                         or getattr(self.settings, "S3_BUCKET_NAME", None)
                         or _bp_settings.FE_S3_BUCKET
                         or _bp_settings.S3_BUCKET_NAME)
            if s3_bucket:
                try:
                    import boto3  # noqa: PLC0415
                    _region = getattr(self.settings, "AWS_REGION", "us-east-1")
                    s3 = boto3.client("s3", region_name=_region)
                    s3_prefix = getattr(self.settings, "FE_S3_PREFIX", "fe")
                    s3_key = f"{s3_prefix}/re-graph-rules/{gear_id}/_context-block-input.txt"
                    s3.put_object(
                        Bucket=s3_bucket,
                        Key=s3_key,
                        Body=context_block.encode("utf-8"),
                        ContentType="text/plain",
                    )
                    logger.info("RE Graph: uploaded context block input to S3 s3://%s/%s", s3_bucket, s3_key)
                except Exception as s3_exc:
                    logger.warning("RE Graph: S3 context block upload failed (non-fatal): %s", s3_exc)
        else:
            # RED flow: query Postgres RAG (fe_kb_chunk) — completely untouched by RE Graph changes
            context_block = await asyncio.to_thread(self._local_context, app_id, stage)
        # Card KB context (typed, addressable knowledge with citeable ids). Injected
        # so the agent grounds on cards up front instead of exploring for them,
        # and knows the id syntax the grounding check will verify.
        # First hydrate KB from S3 if distributed (CLI stages)
        if stage.target in {"docs", "lld", "ui-code", "api-code"}:
            await asyncio.to_thread(self._hydrate_kb, app_id)
        card_block = await asyncio.to_thread(self._card_context, app_id, stage)

        upstream_block = ""
        if upstream:
            lines = ["## Approved upstream artefacts (your inputs)", ""]
            inline: list[str] = []
            for art in sorted(upstream, key=lambda a: a.artifact_type):
                local = (inputs or {}).get(art.artifact_type)
                if local:
                    where = f"local copy at `{local}`"
                elif getattr(art, "git_commit", None):
                    where = f"git {art.git_repo}@{art.git_commit}"
                else:
                    where = f"at {art.path}"
                lines.append(
                    f"- {art.artifact_type} v{art.version} - {where} "
                    f"(approved by {art.approved_by})"
                )
                if local and stage.target == "docs":
                    # Check if we should use batch processing
                    should_batch = (
                        self.pipeline.tier in ("global", "architecture") and
                        self.settings.fe_batch_enabled and
                        self._should_use_batching(Path(local), art.artifact_type)
                    )

                    if should_batch:
                        logger.info(
                            "Artifact %s exceeds threshold, marking for batch processing (tier=%s)",
                            art.artifact_type, self.pipeline.tier
                        )
                        # Mark this artifact for batch processing (handled later in execute flow)
                        if not hasattr(self, '_batch_artifacts'):
                            self._batch_artifacts = []
                        self._batch_artifacts.append((art, Path(local)))
                        # Add placeholder text to inline
                        inline.append(
                            f"### {art.artifact_type} v{art.version}\n\n"
                            f"[Large artifact - will be processed in batches]"
                        )
                    else:
                        # Single-read mode with tier-specific limits:
                        # - Global/architecture stages get raised cap (200K default)
                        # - Mini stages keep original 30K limit
                        read_limit = self.settings.get_artifact_read_limit(self.pipeline.tier)
                        logger.info(
                            "Reading upstream artifact %s with tier=%s, limit=%d chars (batch=disabled)",
                            art.artifact_type, self.pipeline.tier, read_limit
                        )
                        for rel, text in read_markdown_files(Path(local), max_chars=read_limit).items():
                            inline.append(f"### {art.artifact_type} v{art.version} - {rel}\n\n{text}")
            upstream_block = "\n".join(lines) + "\n"
            if inline:
                upstream_block += "\n## Upstream content (inlined)\n\n" + "\n\n".join(inline) + "\n"

        # Per-project configuration: the domain pack (facts) plus stage/agent
        # instruction overrides under <workspace_root>/<project>/agent-config/.
        # This is how an application customises a generic agent without editing
        # any plugin. Precedence: run request > stage override > agent override
        # > domain pack > plugin defaults.
        project_cfg = load_project_config(self.settings.fe_workspace_root, app_id)
        project_block = project_config_block(project_cfg, stage.key)

        tools = ", ".join(stage.permissions.allow) or "(none)"
        is_react = stage.owner.kind is OwnerKind.BUILTIN and stage.owner.handler == "react"
        return "\n\n".join(filter(None, [
            header,
            f"## Deliverable\n\n{stage.deliverable}",
            project_block,
            upstream_block,
            context_block,
            card_block,
            # The react builtin writes the files itself; its output contract is
            # the JSON schema in its system prompt, not a Markdown file list.
            None if is_react else self._output_contract(stage, tools, project_cfg),
        ]))

    @staticmethod
    def _output_contract(stage: Stage, tools: str, project_cfg=None) -> str:
        """What the stage must leave on disk, and in what format.

        Document stages are pinned to Markdown with a filename derived from the
        artefact type. Without that the format was whatever the plugin skill felt
        like -- one stage emitting .docx and the next .txt -- and nothing
        downstream could find a named input, because `_persist_artifacts` records
        the worktree as the artefact path rather than an individual file.
        """
        produces = ", ".join(stage.produces)
        if getattr(stage, "publish_kb", False):
            # Reverse-engineering stage: the deliverable is the kb/ tree in the
            # skill's layout, not documents or source + SUMMARY.md. Saying
            # otherwise here made the agent invent its own layout.
            fmt = (
                f"Produce artefact type(s): {produces}.\n\n"
                "**Deliverable: the `kb/` directory in the working directory, exactly in the layout "
                "the skill specifies** -- `kb/knowledge/<kind>/<ID>.md` cards with YAML front-matter "
                "(id, kind, label, summary, sources, tags), `kb/knowledge/ontology/graph.json` "
                "(`nodes[]`, `edges[]` with `source`/`target`/`label`), `kb/evidence/evidence-map.jsonl` "
                "(one JSON object per line: id, source_doc, locus, snippet, confidence), `kb/cards.jsonl` "
                "and `kb/_build-report.md`. Do not invent another layout (no `cards/*.json`, no "
                "`SUMMARY.md` instead of the report). The platform runs a structural gate on "
                "completion — ensure all required files are present before finishing.\n"
            )
        elif stage.target == "docs":
            names = ", ".join(f"`{t}.md`" for t in stage.produces)
            fmt = (
                f"Produce artefact type(s): {produces}.\n\n"
                f"**Format: Markdown.** Write one file per artefact type, named "
                f"exactly: {names}. No .docx, .pdf, .txt or .html -- the pipeline "
                "renders those downstream from the Markdown.\n\n"
                "Each file must stand on its own: open with a single H1 naming the "
                "document, then `##` sections. Use tables for anything enumerable, "
                "and fenced code blocks for schema identifiers, payloads or "
                "signatures. Cite every substantive claim to its source.\n\n"
                "**Write long documents incrementally.** Create each file with its "
                "first sections, then append the remaining sections in further "
                "writes of at most ~250 lines each. A single write of a whole "
                "document times out and loses everything.\n\n"
                "An artefact that is inherently not prose -- a diagram, an image -- "
                "keeps its native format and is linked from the Markdown rather than "
                "embedded as text.\n"
            )
        else:
            fmt = (
                f"Produce artefact type(s): {produces}.\n\n"
                "Source files take their language's conventional layout and "
                "extension. Alongside them write `SUMMARY.md` -- what you generated, "
                "the decisions you made, and anything a reviewer must check by hand.\n"
            )
        return (
            "## Output contract\n\n"
            + fmt
            + f"\nTools available to you: {tools}. Anything else is denied.\n"
            "Write files under the working directory only. "
            + constraint_sentence(project_cfg)
            + " If a required input is missing, stop and say what is missing "
            "rather than inventing it."
        )

    def _card_context(self, app_id: str, stage: Stage, *, top_k: int = 12) -> str:
        """Top card KB hits for the stage, as a citeable block. Empty when no KB."""
        _KB_CONTEXT_TARGETS = {"docs", "lld", "ui-code", "api-code"}
        store = self._card_store(app_id)
        if store is None or stage.target not in _KB_CONTEXT_TARGETS:
            return ""
        seen: dict[str, float] = {}
        cards = {}
        for q in [f"{stage.name}: {stage.deliverable}", *stage.retrieval_queries]:
            for hit in store.search(q, top_k=top_k):
                if hit.score > seen.get(hit.card.id, 0.0):
                    seen[hit.card.id] = hit.score
                    cards[hit.card.id] = hit.card
        if not cards:
            return ""
        ranked = sorted(cards.values(), key=lambda c: seen[c.id], reverse=True)[:top_k]
        lines = ["## Knowledge cards (cite by id)", "",
                 "The application's knowledge base holds typed cards with stable ids. "
                 "Ground every substantive statement in one, citing the id in square "
                 "brackets, e.g. `[BR-UWCR-0007]`. Use the `kb_card`, `graph_neighbors` "
                 "and `kb_cards_search` tools for more; never invent an id -- cited ids "
                 "are verified after the run.", ""]
        for c in ranked:
            summary = (c.summary or c.body[:240]).replace("\n", " ").strip()
            lines.append(f"- [{c.id}] {c.kind}: {c.label} -- {summary}")
        return "\n".join(lines)

    def _local_context(self, app_id: str, stage: Stage) -> str:
        """Background KB context with auto-detection of structured vs unstructured."""
        if not self.settings.uses_database():
            return (
                "## Knowledge Base context\n\n"
                "UNAVAILABLE — FE_DB_URL is not set.\n"
            )

        try:
            from app.agentic_platform.fe_core.rag.retriever import ChunkStore  # noqa: PLC0415

            cs = ChunkStore(self.settings.fe_db_url, self.settings.fe_db_schema)

            # Map-reduce over the raw RED rule catalogue is only meaningful for the
            # stage that grounds directly in it; every later stage works off the
            # distilled document ladder and would otherwise get the same 575-rule
            # dump injected on top of its actual (already-summarised) inputs.
            modules = cs.list_modules(app_id) if stage.key == "prd" else []

            if modules:
                logger.info(
                    f"Stage {stage.key}: Using map-reduce ({len(modules)} modules detected)"
                )
                return self._map_reduce_context(cs, app_id, stage, modules)
            else:
                logger.info(
                    f"Stage {stage.key}: Using vector search (no structured catalogue)"
                )
                return self._vector_search_context(cs, app_id, stage)

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Stage {stage.key}: RAG query failed: {exc}")
            return (
                "## Knowledge Base context\n\n"
                f"UNAVAILABLE — RAG store error: {type(exc).__name__}: {exc}.\n"
            )

    def _map_reduce_context(
        self, cs, app_id: str, stage: Stage, modules: list[str]
    ) -> str:
        """Map-reduce enumeration for structured rule catalogues."""
        from app.agentic_platform.fe_core.rag.retriever import ChunkStore  # noqa: PLC0415

        try:
            all_rules = []

            # MAP PHASE: Enumerate all modules
            for i, module in enumerate(modules, start=1):
                logger.info(f"Map phase {i}/{len(modules)}: {module}")
                rules = cs.get_module_rules(app_id, module)
                all_rules.extend(rules)

            if not all_rules:
                logger.warning("Map-reduce found no rules, falling back to vector search")
                return self._vector_search_context(cs, app_id, stage)

            # Track for traceability
            self._kb_hits[(app_id, stage.key)] = [
                {
                    "kb_chunk_id": r.id,
                    "artifact_type": r.artifact_type,
                    "artifact_id": r.artifact_id,
                    "module": r.metadata.get("module"),
                }
                for r in all_rules
            ]

            # REDUCE PHASE: Format by module + category
            by_module = {}
            for rule in all_rules:
                mod = rule.metadata.get("module", "unknown")
                by_module.setdefault(mod, []).append(rule)

            sections = []
            for module in sorted(by_module.keys()):
                rules = by_module[module]
                module_text = f"### Module: {module} ({len(rules)} rules)\n\n"
                module_text += "\n\n".join(r.content for r in rules)
                sections.append(module_text)

            content = "\n\n".join(sections)

            logger.info(
                f"Stage {stage.key}: map-reduce retrieved {len(all_rules)} rules "
                f"from {len(modules)} modules"
            )

            return (
                "## Knowledge Base context\n\n"
                f"_(map-reduce — {len(all_rules)} rules from {len(modules)} modules, 100% coverage)_\n\n"
                + content + "\n"
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Map-reduce failed: {exc}, falling back to vector search")
            return self._vector_search_context(cs, app_id, stage)

    def _vector_search_context(self, cs, app_id: str, stage: Stage) -> str:
        """Existing hybrid vector search (extracted from original _local_context)."""
        from app.agentic_platform.fe_core.rag.retriever import retrieve_many  # noqa: PLC0415

        questions = [f"{stage.name}: {stage.deliverable}", *stage.retrieval_queries]
        result = retrieve_many(cs, app_id, questions)

        if result.refused or not result.chunks:
            reason = result.reason or "no matching chunks found"
            return (
                "## Knowledge Base context\n\n"
                f"No relevant content in local store ({reason}).\n"
            )

        sources = sorted({c.artifact_type for c in result.chunks})

        self._kb_hits[(app_id, stage.key)] = [
            {"kb_chunk_id": c.id, "artifact_type": c.artifact_type, "artifact_id": c.artifact_id}
            for c in result.chunks
        ]

        content = "\n\n".join(
            f"[{c.artifact_type} · chunk {c.chunk_index + 1}]\n{c.content}"
            for c in result.chunks
        )

        logger.info(
            f"Stage {stage.key}: {len(result.chunks)} chunk(s) from vector search"
        )

        return (
            "## Knowledge Base context\n\n"
            f"_(vector search — {len(result.chunks)} chunk(s) from: {', '.join(sources)})_\n\n"
            + content + "\n"
        )

    def _index_artifact_local(self, artifact) -> None:
        """Index a stage artifact into fe_kb_chunk for RAG retrieval by later stages.

        Each stage's output is indexed immediately after the run so the NEXT stage
        can retrieve it as background context via _local_context().  Agents also
        read upstream artifacts directly via file paths (upstream_block), so this
        is an enhancement, not a dependency -- errors are non-fatal warnings.

        Called via asyncio.to_thread() because Indexer I/O is synchronous.
        """
        if not self.settings.uses_database():
            return
        try:
            from app.agentic_platform.fe_core.rag.indexer import Indexer  # noqa: PLC0415
            from app.agentic_platform.fe_core.rag.retriever import ChunkStore  # noqa: PLC0415

            cs = ChunkStore(self.settings.fe_db_url, self.settings.fe_db_schema)
            report = Indexer(cs).index_artifact(artifact, embed_now=True)
            logger.info(
                "RAG-indexed %s v%d: %d chunk(s), %d embedded",
                artifact.artifact_type, artifact.version,
                report.chunks_written, report.embedded,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RAG-index skipped for %s (next stage still reads file via "
                "upstream_block): %s", artifact.artifact_type, exc,
            )

    async def _builtin_prompt(self, stage: Stage, app_id: str) -> str | None:
        """Prompt for a builtin handler, or None to fall back to the generic path."""
        handler = stage.owner.handler
        if handler == "react":
            return None      # the react builtin uses the generic context blocks as its user message
        if handler == "impact_analysis":
            from app.agentic_platform.worker.builtins import impact_analysis

            prompt, inputs = await impact_analysis.prepare(
                stage, kb=self.kb, app_id=app_id, store=self.store,
                settings=self.settings,
            )
            if not inputs.can_correlate:
                logger.warning(
                    "Impact analysis has %d of %d KB surfaces; it will report "
                    "that it could not correlate rather than infer a baseline",
                    len(inputs.available), len(inputs.surfaces),
                )
            return prompt
        logger.warning(
            "Stage '%s' declares builtin handler '%s', which is not implemented; "
            "falling back to the generic prompt", stage.key, handler,
        )
        return None

    def _upstream_artifacts(self, run: StageRun, stage: Stage) -> list[SdlcArtifact]:
        """Approved inputs visible to this run.

        Three sources:
          1. This pipeline's own artefacts (same pipeline name, any readable workspace).
          2. Parent-tier artefacts declared in `inherited_inputs` (Mini -> Global).
          3. Architecture Workspace artefacts declared in `arch_inputs` (Global epic-set -> SRD).

        What it must NOT find is a sibling EPIC's work, which is why the
        workspace filter is applied rather than left to the caller.
        """
        readable = readable_workspace_ids(self.store, run.workspace_id)
        upstream = list(self.store.approved_artifacts(
            self.pipeline.name, stage.consumes, workspace_ids=readable,
        ))
        inherited = [c for c in stage.consumes
                     if c in set(self.pipeline.inherited_inputs)]
        if inherited and self.pipeline.parent:
            upstream.extend(self.store.approved_artifacts(
                self.pipeline.parent, inherited, workspace_ids=readable,
            ))
        arch_consumed = [c for c in stage.consumes
                         if c in set(self.pipeline.arch_inputs)]
        if arch_consumed:
            arch_name = getattr(self.settings, "fe_pipeline_architecture", None)
            if arch_name:
                upstream.extend(self.store.approved_artifacts(
                    arch_name, arch_consumed, workspace_ids=readable,
                ))
        # Latest approved version per type wins (FR-025).
        # Workspace proximity takes precedence: an artifact from the current run's
        # workspace always beats a same-type artifact from a parent workspace, even
        # if the parent has a higher version number.  This prevents a stale global
        # artifact from shadowing an EPIC-scoped artifact in a mini workspace.
        latest: dict[str, SdlcArtifact] = {}
        for artifact in sorted(
            upstream,
            key=lambda a: (a.workspace_id == run.workspace_id, a.version),
        ):
            latest[artifact.artifact_type] = artifact
        return list(latest.values())

    def _skill_system_prompt(self, stage: Stage, run: "StageRun | None" = None) -> str | None:
        """Return the system prompt for a skill-owned stage, or None if absent.

        Lookup order: ``{skill}.json`` (e.g. ba-brd.json) then ``sm-{stage-key}.json``
        (e.g. sm-feature.json).  Uses str.replace() for {persona}/{N} substitution so
        the many other {placeholder} patterns in the templates are left intact.
        """
        skill = stage.owner.skill or stage.owner.handler
        if not skill:
            return None
        prompts_dir = Path(__file__).resolve().parent / "runners" / "prompts"
        prompt_file = next(
            (p for p in (prompts_dir / f"{skill}.json",
                         prompts_dir / f"sm-{stage.key}.json") if p.is_file()),
            None,
        )
        if prompt_file is None:
            return None
        try:
            raw = json.loads(prompt_file.read_text(encoding="utf-8-sig"))["system"]
            persona = stage.approval_persona or "agent"
            raw = raw.replace("{persona}", persona)
            epic_id = getattr(run, "epic_id", None)
            if not epic_id:
                # Global-pipeline stages carry the mini workspace id in workspace_id
                # (e.g. "uw-credit-risk-mini--epic-2") but epic_id is None because
                # the StageRun is registered against the global workspace.
                workspace_id = getattr(run, "workspace_id", "") or ""
                if "--epic-" in workspace_id:
                    epic_id = workspace_id.split("--epic-")[-1]
            if not epic_id:
                # UI triggers send the global workspace (ends --global) rather than the
                # mini workspace.  If exactly one mini workspace is open for this KB
                # application we can unambiguously determine the EPIC.
                kb_app = getattr(run, "kb_application_id", None)
                if kb_app:
                    open_minis = self.store.list_workspaces(
                        kb_app, tier="mini", status="open"
                    )
                    if len(open_minis) == 1:
                        epic_id = open_minis[0].epic_id
            epic_num = epic_id.replace("epic-", "") if epic_id else "N"
            raw = raw.replace("{N}", epic_num)
            logger.info(
                "system_prompt loaded stage=%s file=%s persona=%s epic=%s",
                stage.key, prompt_file.name, persona, epic_num,
            )
            return raw
        except (KeyError, json.JSONDecodeError) as exc:
            logger.warning("Could not load system prompt for skill %s: %s", skill, exc)
            return None

    # -- execution --------------------------------------------------------
    async def execute(self, run: StageRun) -> StageRun:
        import time as _time  # noqa: PLC0415 — local to avoid shadowing stdlib at module level
        _t0 = _time.perf_counter()

        # Stamp per-run traceability IDs in ContextVar so telemetry events and
        # log lines are correlated without parameter threading.
        agent_run_id = uuid.uuid4().hex[:12]
        set_agent_run_id(agent_run_id)
        if run.workflow_run_id:
            set_workflow_run_id(run.workflow_run_id)

        telemetry = get_telemetry_service()
        stage = self.pipeline.stage(run.stage_key)
        telemetry.emit(EventType.STAGE_GENERATE, {
            "stage": stage.key, "agent_run_id": agent_run_id,
            "workflow_run_id": run.workflow_run_id or get_workflow_run_id(),
            "workspace_id": run.workspace_id, "status": "start",
        })

        _status = "ok"
        try:
            return await self._execute_inner(run, stage, agent_run_id)
        except Exception:
            _status = "error"
            raise
        finally:
            latency_ms = (_time.perf_counter() - _t0) * 1000
            telemetry.emit(EventType.STAGE_GENERATE, {
                "stage": stage.key, "agent_run_id": agent_run_id,
                "workflow_run_id": run.workflow_run_id or get_workflow_run_id(),
                "workspace_id": run.workspace_id,
                "tokens_in": run.input_tokens, "tokens_out": run.output_tokens,
                "latency_ms": round(latency_ms, 1), "status": _status,
            })
            telemetry.emit_agent_run(
                agent_run_id=agent_run_id,
                stage=stage.key,
                persona=stage.approval_persona or "",
                iterations=run.num_turns or 0,
                status=_status,
                latency_ms=latency_ms,
                tokens_in=run.input_tokens,
                tokens_out=run.output_tokens,
                # Traceability tags so /observability/agents + rule/module views can
                # scope pipeline runs. module_id = kb_application_id (app/module scope);
                # rule-level tagging is derived downstream from artifact content.
                workspace_id=run.workspace_id or "",
                module_id=run.kb_application_id or "",
                artifact_id=(run.artifact_ids[0] if getattr(run, "artifact_ids", None) else ""),
            )
            # GAP-007: automatic threshold evaluation per stage run → fe_evaluations.
            # Non-fatal: evaluation must never break a pipeline run. LLM-as-judge is
            # opt-in elsewhere (needs reference rules + costs tokens).
            try:
                from app.services.evaluation import get_evaluation_service  # noqa: PLC0415
                from app.dao.postgres import get_pool as _get_pool  # noqa: PLC0415
                from app.config import get_settings as _get_settings  # noqa: PLC0415
                _eval = get_evaluation_service()
                _results = _eval.evaluate_threshold(
                    agent_run_id=agent_run_id,
                    artifact_id=(run.artifact_ids[0] if getattr(run, "artifact_ids", None) else ""),
                    latency_ms=latency_ms,
                    tokens_total=(run.input_tokens or 0) + (run.output_tokens or 0),
                )
                await _eval.persist(_results, pool=_get_pool(), schema=_get_settings().PG_SCHEMA)
            except Exception as _eval_exc:  # noqa: BLE001 — evaluation is best-effort
                logger.warning("Stage %s: threshold evaluation failed (non-fatal): %s",
                               stage.key, _eval_exc)

    async def _execute_inner(self, run: StageRun, stage: Stage, agent_run_id: str) -> StageRun:
        workspace = self.settings.run_workspace(
            run.kb_application_id, run.run_id,
            stage_key=run.stage_key, workspace_id=run.workspace_id or "")
        workspace.mkdir(parents=True, exist_ok=True)
        await self._register_workspace(run)

        upstream = self._upstream_artifacts(run, stage)
        inputs = await asyncio.to_thread(self._materialise_upstream, upstream, workspace)
        if stage.corpus:
            await asyncio.to_thread(self._materialise_corpus, run.kb_application_id, workspace)
            await asyncio.to_thread(self._materialise_library_ref, run, workspace)

        # RE Graph extraction — ONLY for RE Engineering workspaces (gear_id + intake_source="re-graph").
        # RED/RAG workspaces (intake_source="reverse-engineering" etc.) are never affected.
        logger.info(
            "RE Graph gate check: stage_key=%r gear_id=%r intake_source=%r",
            run.stage_key, run.gear_id, getattr(run, "intake_source", "ATTR_MISSING"),
        )
        if run.stage_key == "prd" and run.gear_id and getattr(run, "intake_source", None) == "re-graph":
            try:
                await self._fetch_graph_rules(
                    run.module_scope, workspace,
                    gear_id=run.gear_id,
                    business_area=getattr(run, "business_area", None),
                    role=getattr(run, "role", None),
                    app_id=run.kb_application_id,
                )
            except Exception as _re_exc:  # noqa: BLE001
                logger.warning("RE Graph fetch failed (non-fatal, agent proceeds without it): %s", _re_exc)

        prompt = await self.build_prompt(
            stage, run.kb_application_id, upstream, inputs,
            gear_id=run.gear_id,
            intake_source=getattr(run, "intake_source", None),
            workspace=workspace,
        )
        hits = self._kb_hits.pop((run.kb_application_id, stage.key), [])
        run.kb_chunk_ids = [h["kb_chunk_id"] for h in hits]
        _phash: str | None = None
        try:
            import hashlib as _hashlib  # noqa: PLC0415
            _phash = _hashlib.sha256(prompt.encode()).hexdigest()[:16]
            _token_est = len(prompt) // 4
            telemetry.emit_prompt_built(
                stage=stage.key,
                token_estimate=_token_est,
                chunk_count=len(run.kb_chunk_ids),
                prompt_hash=_phash,
                workspace_id=run.workspace_id or "",
            )
            asyncio.create_task(self._persist_prompt_snapshot(
                run_id=run.run_id, workspace_id=run.workspace_id or "",
                stage=stage.key, prompt_hash=_phash,
                token_estimate=_token_est, chunk_count=len(run.kb_chunk_ids),
            ))
        except Exception as _pb_exc:  # noqa: BLE001
            logger.debug("PROMPT_BUILT emit failed (non-fatal): %s", _pb_exc)

        # Workspace cost budget: refuse to start when the workspace has already
        # spent its allowance. Checked before any tokens are spent on this run.
        self._enforce_workspace_budget(run)

        # Context token budget guard (F5): refuse if prompt already exceeds model window.
        try:
            from app.services.tokenizer_service import get_tokenizer_service as _gtok  # noqa: PLC0415
            _tok = _gtok()
            _model_for_budget = self.settings.resolve_model(stage.model, stage_key=stage.key)
            _ctx_window = _tok.model_context_window(_model_for_budget)
            _safety_margin = getattr(self.settings, "FE_CONTEXT_SAFETY_MARGIN", 5_000)
            _max_out = getattr(self.settings, "BEDROCK_LLM_DEFAULT_MAX_TOKENS", 60_000)
            _prompt_tokens = _tok.count_tokens(prompt, _model_for_budget).count
            _available = _ctx_window - _prompt_tokens - _max_out - _safety_margin
            if _available < 0:
                raise ValueError(
                    f"Prompt exceeds model context window by {-_available} tokens "
                    f"(window={_ctx_window}, prompt={_prompt_tokens}, "
                    f"max_out={_max_out}, margin={_safety_margin})"
                )
            logger.debug("Token budget: prompt=%d available=%d window=%d",
                         _prompt_tokens, _available, _ctx_window)
        except ValueError:
            raise
        except Exception as _tok_exc:  # noqa: BLE001
            logger.debug("Token budget check failed (non-fatal): %s", _tok_exc)

        # Flag to track if batch processing occurred (skips normal execution)
        batch_processed = False

        # Check if we have artifacts marked for batch processing (Phase 2)
        if hasattr(self, '_batch_artifacts') and self._batch_artifacts:
            logger.info("Processing %d artifacts in batch mode", len(self._batch_artifacts))
            for artifact, artifact_path in self._batch_artifacts:
                batches = self._split_into_batches(artifact_path)
                batch_results = []

                for idx, batch_content in enumerate(batches, start=1):
                    logger.info(
                        "Processing batch %d/%d for artifact %s",
                        idx, len(batches), artifact.artifact_type
                    )
                    batch_prompt = self._build_batch_prompt(
                        prompt, batch_content, idx, len(batches), artifact.artifact_type
                    )
                    plugins = self._plugin_specs(stage)
                    model = self.settings.resolve_model(stage.model, stage_key=stage.key)
                    batch_request = RunRequest(
                        prompt=batch_prompt,
                        workspace=workspace,
                        correlation_id=f"{run.correlation_id or run.run_id}-batch-{idx}",
                        model=model,
                        effort=self.settings.effort_for_model(
                            stage.effort or self.settings.fe_effort, model),
                        plugins=tuple(plugins),
                        mcp_servers=tuple(self._mcp_specs()),
                        permissions=self._permissions(stage),
                        limits=self._limits(stage),
                        system_prompt_append=self._skill_system_prompt(stage, run),
                        workspace_id=run.workspace_id,
                    )
                    batch_result = await self.runner.run(batch_request)
                    batch_results.append(batch_result.transcript or "")

                merged = await self._merge_batch_results(batch_results, artifact.artifact_type)
                output_path = workspace / f"{artifact.artifact_type}-batch-merged.md"
                output_path.write_text(merged, encoding="utf-8")
                logger.info("Batch processing complete, merged output at %s", output_path)

            delattr(self, '_batch_artifacts')
            batch_processed = True
            logger.info("Batch processing completed, will skip normal execution")

        plugins = self._plugin_specs(stage)
        model = self.settings.resolve_model(stage.model, stage_key=stage.key)
        request = RunRequest(
            prompt=prompt,
            workspace=workspace,
            correlation_id=run.correlation_id or run.run_id,
            model=model,
            effort=self.settings.effort_for_model(
                stage.effort or self.settings.fe_effort, model),
            plugins=tuple(plugins),
            mcp_servers=tuple(self._mcp_specs()),
            permissions=self._permissions(stage),
            limits=self._limits(stage),
            system_prompt_append=self._skill_system_prompt(stage, run),
            workspace_id=run.workspace_id,
            # Per-workspace output directory. A shared generated/ui would have
            # ten EPICs writing over each other's components (FR-P5).
            # When FE_UI_TARGET_REPO / FE_API_TARGET_REPO is configured, code
            # goes directly into the developer's local checkout so tests can
            # compile and run immediately (no copy step needed).
            additional_dirs=((target_dir,)
                             if (target_dir := self.settings.stage_target_dir(
                                 stage.target, workspace_id=run.workspace_id,
                                 epic_id=run.epic_id)) is not None else ()),
        )
        # `--add-dir` on a path that does not exist makes the CLI exit 1
        # ("Path ... was not found") at the end of an otherwise good run.
        for extra in request.additional_dirs:
            try:
                Path(extra).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Cannot create output dir %s (%s); the CLI may "
                               "refuse it as --add-dir", extra, exc)

        # provenance (FR-019)
        run.worktree = str(workspace)
        run.model = request.model
        run.runner = self.runner.name
        run.plugin = stage.owner.plugin
        # A builtin stage owns no plugin, so there is no version to record. The
        # previous form indexed plugins[0] unconditionally, which made G1 Impact
        # Analysis -- the only builtin, and the pipeline's entry point -- crash
        # before it could produce anything.
        run.plugin_version = _plugin_version(plugins) if plugins else None
        run.source_artifact_ids = [a.id for a in upstream]
        run.transition(StageState.RUNNING)
        self.store.save_run(run)

        if batch_processed:
            # Batch processing already executed — create a summary result.
            from app.agentic_platform.fe_core.runner_protocol import RunResult
            logger.info("Skipping normal execution due to batch processing")
            result = RunResult(
                correlation_id=run.correlation_id or run.run_id,
                files_written=[],
                input_tokens=0,
                output_tokens=0,
                transcript="Batch processing completed - see batch-merged output files",
            )
        else:
            try:
                if stage.owner.kind is OwnerKind.BUILTIN and stage.owner.handler == "kb-extract":
                    # WP2: In-process LangGraph KB extract — no LLM, no plugin.
                    result = await self._run_kb_extract(run, stage, request)
                    run.runner = "kb-extract"
                elif stage.owner.kind is OwnerKind.BUILTIN and stage.owner.handler == "react":
                    # Bounded in-process ReAct loop over the card KB -- no Claude
                    # Code session for structured JSON stages (cheap, id-whitelisted).
                    result = await self._run_react(run, stage, request)
                    run.runner = "react"
                    run.model = result.model or run.model
                else:
                    result = await self.runner.run(request)
            except Exception as exc:  # noqa: BLE001
                run.transition(StageState.FAILED, error=str(exc),
                               error_code=type(exc).__name__)
                self.store.save_run(run)
                logger.exception("Stage %s failed", stage.key)
                # F1: record failed AI call so error rates are visible in fe_ai_call.
                try:
                    from app.services import ai_call_recorder as _rec  # noqa: PLC0415
                    from datetime import datetime, timezone as _tz  # noqa: PLC0415
                    asyncio.create_task(_rec.record(_rec.AICallRecord(
                        model_provider=run.runner or "claude",
                        model_name=run.model or request.model,
                        request_ts=datetime.now(tz=_tz.utc),
                        run_id=run.run_id,
                        stage_id=run.stage_key,
                        stage_name=stage.key,
                        execution_id=run.workspace_id,
                        prompt_hash=_phash,
                        request_status="error",
                        error_type=type(exc).__name__,
                        error_message=str(exc)[:500],
                    )))
                except Exception:  # noqa: BLE001
                    pass
                raise

        run.input_tokens = result.input_tokens
        run.output_tokens = result.output_tokens
        run.cache_read_input_tokens = result.cache_read_input_tokens
        run.cache_creation_input_tokens = result.cache_creation_input_tokens
        run.cost_usd = result.cost_usd
        run.num_turns = result.num_turns
        run.max_turns = request.limits.max_turns
        run.files_written = result.files_written
        run.tools_invoked = result.tools_invoked
        run.tools_denied = result.tools_denied
        run.session_id = result.session_id

        # AI Call Observability (F1) — record runner invocation to fe_ai_call.
        try:
            from app.services import ai_call_recorder as _rec  # noqa: PLC0415
            from datetime import datetime, timezone as _tz  # noqa: PLC0415
            asyncio.create_task(_rec.record(_rec.AICallRecord(
                model_provider=run.runner or "claude",
                model_name=run.model or request.model,
                request_ts=datetime.now(tz=_tz.utc),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cached_input_tokens=(result.cache_read_input_tokens or 0)
                                    + (result.cache_creation_input_tokens or 0),
                estimated_cost_usd=result.cost_usd,
                run_id=run.run_id,
                stage_id=run.stage_key,
                stage_name=stage.key,
                execution_id=run.workspace_id,
                prompt_hash=_phash,
                request_status="success",
            )))
        except Exception as _rec_exc:  # noqa: BLE001
            logger.debug("[ai_call_recorder] stage record failed (non-fatal): %s", _rec_exc)

        run.log.extend(
            f"{e.kind.value}: {e.text or e.path or ''}".strip() for e in result.events[-50:]
        )
        ratio = result.cache_hit_ratio
        logger.info(
            "Stage %s usage: turns=%s/%s in=%d cache_read=%d cache_write=%d out=%d "
            "cache_hit=%s cost=%s model=%s",
            stage.key, result.num_turns, request.limits.max_turns,
            result.input_tokens, result.cache_read_input_tokens,
            result.cache_creation_input_tokens, result.output_tokens,
            f"{ratio:.0%}" if ratio is not None else "n/a",
            f"${result.cost_usd:.4f}" if result.cost_usd is not None else "n/r",
            request.model,
        )

        if not result.ok:
            from app.agentic_platform.fe_core.ports.claude_runner import TerminationReason  # noqa: PLC0415
            _context_overflow = (
                result.terminated is TerminationReason.ERROR
                and any("too long" in (e.text or "").lower() for e in result.events)
            )
            salvage = (
                (result.terminated is TerminationReason.MAX_TURNS or _context_overflow)
                and stage.max_turns_policy == "salvage"
                and not self._verify_deliverables(stage, workspace)
            )
            if salvage:
                # The cap fired (or the context window overflowed), but the
                # deliverable the contract demands is on disk. Long document
                # stages write incrementally, so the final turn is often a
                # closing edit; discarding it wastes the spend. Accept it,
                # mark it, and let the checks below decide. The approver sees
                # `capped` on the run.
                reason = "context-overflow" if _context_overflow else f"max_turns cap ({request.limits.max_turns})"
                run.log.append(f"salvaged: {reason} hit with the deliverable complete; policy=salvage")
                run.capped = True
                result.terminated = TerminationReason.COMPLETED
                result.error = None
            else:
                # Includes TerminationReason.MAX_TURNS: a run cut off by its turn
                # cap is not a completion, whatever files it left behind.
                run.transition(StageState.FAILED, error=result.error or "runner reported failure",
                               error_code=result.terminated.value)
                self.store.save_run(run)
                return run

        # Per-stage token budget (0 = off). Exceeding it almost always means the
        # agent looped; the artefact is not trusted and the run is failed.
        budget = self.settings.fe_max_tokens_per_stage
        if budget and result.total_tokens > budget:
            run.transition(
                StageState.FAILED,
                error=(f"token budget exceeded: {result.total_tokens} > {budget} "
                       f"(FE_MAX_TOKENS_PER_STAGE)"),
                error_code="budget_exceeded",
            )
            self.store.save_run(run)
            logger.warning("Stage %s exceeded its token budget (%d > %d)",
                           stage.key, result.total_tokens, budget)
            return run

        # A stage "succeeds" only if the files the output contract demanded exist.
        # Code stages write into the configured target repo (FE_UI_TARGET_REPO /
        # FE_API_TARGET_REPO) rather than the run worktree, so the check -- and
        # the artefact body -- must look where the files actually went. Before
        # this, a run that wrote 74 Angular files was failed as "missing
        # deliverable" because the worktree was empty.
        return await self._finalize(run, stage, workspace, target_dir, result, upstream)

    async def _run_kb_extract(self, run: StageRun, stage: Stage, request: RunRequest) -> RunResult:
        """WP2: Execute the in-process LangGraph KB extract builtin.

        Returns a RunResult so the rest of execute() (deliverables, grounding,
        persistence) is unchanged.  tokens=0 — no LLM is used.
        """
        from app.agentic_platform.fe_core.ports.claude_runner import TerminationReason  # noqa: PLC0415
        from app.agentic_platform.worker.builtins.kb_extract import run_kb_extract  # noqa: PLC0415

        worktree = str(request.workspace)
        result = RunResult(
            correlation_id=request.correlation_id,
            runner="kb-extract",
            model="none",
            started_at=datetime.now(timezone.utc),
        )
        try:
            outcome = await asyncio.to_thread(
                run_kb_extract, worktree, run.kb_application_id
            )
        except RuntimeError as exc:
            result.terminated = TerminationReason.ERROR
            result.error = str(exc)
            # "kb_input_missing: no_library: ..." / "kb_input_missing: no_match: ..."
            reason = str(exc).split(":")[1].strip() if str(exc).count(":") >= 2 else ""
            result.error_code = {"no_library": "kb_no_library",
                                 "no_match": "kb_no_match"}.get(reason, "kb_input_missing")
            result.finished_at = datetime.now(timezone.utc)
            return result

        result.output_tokens = 0
        result.input_tokens = 0
        result.finished_at = datetime.now(timezone.utc)

        if outcome.mode == "library":
            run.log.append(
                f"kb-extract: built from global library v{outcome.library_version}; "
                f"{len(outcome.unmatched_files)} uploaded ASP file(s) have no cards in the library"
                + (f": {', '.join(outcome.unmatched_files[:10])}" if outcome.unmatched_files else ""))
        for warning in outcome.warnings[:20]:
            run.log.append(f"kb-extract warning: {warning}")

        # Write kb.md:
        #   1. pipe table   → parseKbCards() in pipeline.component.ts renders the type/count summary
        #   2. JSON comment → parseKbCardIndex() renders the inline card-list expander per type
        kb_knowledge = Path(worktree) / "kb" / "knowledge"
        type_rows = ""
        cards_by_type: dict[str, list[dict]] = {}
        if kb_knowledge.is_dir():
            for kind_dir in sorted(kb_knowledge.iterdir()):
                if not kind_dir.is_dir():
                    continue
                cards: list[dict] = []
                for card_file in sorted(kind_dir.glob("*.md")):
                    cid, label = "", ""
                    in_fm = False
                    for line in card_file.read_text(encoding="utf-8").splitlines():
                        if line.strip() == "---":
                            in_fm = not in_fm
                            continue
                        if in_fm:
                            if line.startswith("id:"):
                                cid = line[3:].strip().strip('"')
                            elif line.startswith("label:"):
                                label = line[6:].strip().strip('"')
                    if cid:
                        cards.append({"id": cid, "label": label})
                if cards:
                    cards_by_type[kind_dir.name] = cards
                    rel = f"kb/knowledge/{kind_dir.name}"
                    type_rows += f"| {kind_dir.name} | {rel} | {len(cards)} |\n"
        import json as _json
        cards_json = _json.dumps(cards_by_type, separators=(",", ":"))
        Path(worktree, "kb.md").write_text(
            f"# Knowledge Base Build\n\n"
            f"**Cards written:** {outcome.cards} | "
            f"**Edges:** {outcome.edges} | "
            f"**SCR with screenshot:** {outcome.scr_with_screenshot} | "
            f"**Gate:** {'PASS' if outcome.gate_passed else 'FAIL'}\n\n"
            f"| Type | Directory | Count |\n"
            f"|------|-----------|-------|\n"
            f"{type_rows}\n"
            f"<!-- KB_CARDS_JSON:{cards_json} -->\n",
            encoding="utf-8",
        )

        if not outcome.gate_passed:
            result.terminated = TerminationReason.ERROR
            issues_summary = "; ".join(outcome.gate_findings[:5])
            if len(outcome.gate_findings) > 5:
                issues_summary += f" … ({len(outcome.gate_findings) - 5} more)"
            result.error = f"kb-extract gate FAIL: {issues_summary}"
            result.error_code = "kb_gate_failed"
        else:
            result.transcript = (
                f"kb-extract: {outcome.cards} cards, {outcome.edges} edges, "
                f"{outcome.scr_with_screenshot} SCR screenshots, gate PASS"
            )
        return result

    async def _run_react(self, run: StageRun, stage: Stage, request: RunRequest) -> RunResult:
        """Execute the bounded ReAct builtin and present it as a RunResult so the
        rest of execute() (deliverables, grounding, persistence) is unchanged."""
        import json as _json  # noqa: PLC0415
        from app.agentic_platform.fe_core.ports.claude_runner import AgentEvent, EventKind, TerminationReason  # noqa: PLC0415
        from app.agentic_platform.worker.builtins import react_stage  # noqa: PLC0415

        store = self._card_store(run.kb_application_id)
        result = RunResult(correlation_id=request.correlation_id, runner="react",
                           model=self.settings.fe_react_model or "bedrock-default",
                           started_at=datetime.now(timezone.utc))
        if store is None:
            result.terminated = TerminationReason.ERROR
            result.error = (f"react builtin needs a card KB for '{run.kb_application_id}' at "
                            f"{self.settings.kb_root_for(run.kb_application_id)}; none built")
            result.finished_at = datetime.now(timezone.utc)
            return result

        def on_step(tool: str, detail: str) -> None:
            result.events.append(AgentEvent(kind=EventKind.TOOL_CALL, tool_name=tool, text=detail))
            result.tools_invoked.append(tool)

        outcome = await react_stage.run_stage(
            stage=stage, prompt=request.prompt, store=store, settings=self.settings, on_step=on_step)
        result.input_tokens = outcome.input_tokens
        result.output_tokens = outcome.output_tokens
        result.cache_read_input_tokens = outcome.cache_read_input_tokens
        result.cache_creation_input_tokens = outcome.cache_creation_input_tokens
        result.num_turns = outcome.steps
        result.finished_at = datetime.now(timezone.utc)
        if outcome.data is None:
            result.terminated = (TerminationReason.MAX_TURNS if outcome.hit_limit
                                 else TerminationReason.ERROR)
            result.error = "react builtin produced no valid JSON" + (
                f" within {outcome.steps} steps" if outcome.hit_limit else "")
            return result
        payload = dict(outcome.data)
        payload["_grounding"] = {
            "retrieved_ids": sorted(outcome.collected),
            "dropped_ids": list(outcome.dropped_ids),
            "steps": outcome.steps, "hit_limit": outcome.hit_limit, "retried": outcome.retried,
        }
        for artifact_type in stage.produces:
            json_path = request.workspace / f"{artifact_type}.json"
            md_path = request.workspace / f"{artifact_type}.md"
            json_path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
            md_path.write_text(react_stage.render_markdown(f"{stage.name} -- {artifact_type}",
                                                           outcome.data, outcome), encoding="utf-8")
            result.files_written += [str(json_path), str(md_path)]
        result.text = _json.dumps(outcome.data)
        run.log.append(
            f"react: steps={outcome.steps} ids_retrieved={len(outcome.collected)} "
            f"dropped={len(outcome.dropped_ids)}{' limit-hit' if outcome.hit_limit else ''}"
            f"{' retried' if outcome.retried else ''}")
        return result

    # -- reverse-engineering (card KB) helpers -------------------------------
    def _materialise_corpus(self, app_id: str, worktree: Path) -> None:
        """Copy the application's raw corpus into <worktree>/inputs/corpus/."""
        import shutil  # noqa: PLC0415

        src = self.settings.corpus_root_for(app_id)
        dest = worktree / "inputs" / "corpus"
        if not src.is_dir() or not any(src.iterdir()):
            raise StageExecutionError(
                f"KB stage requires a corpus at '{src}' but none was found. "
                "Upload the RED documents before running this stage."
            )
        if not src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            return
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
        # Binary office/PDF sources get an extracted `.txt` sibling: the agent's
        # Read tool cannot open .docx/.pdf/.xlsx, and the extractor is the same
        # one the upload path indexes from, so both views agree.
        extracted = 0
        try:
            from app.agentic_platform.fe_core.rag.extract import UnreadableUpload, extract_text  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            extract_text = None  # type: ignore[assignment]
        if extract_text is not None:
            for path in list(dest.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in (".docx", ".pdf", ".xlsx", ".pptx"):
                    continue
                sidecar = path.with_suffix(path.suffix + ".md")
                if sidecar.exists():
                    continue
                try:
                    text, _how = extract_text(path.read_bytes(), path.name)
                    sidecar.write_text(text, encoding="utf-8")
                    extracted += 1
                except (UnreadableUpload, OSError, ValueError) as exc:  # noqa: PERF203
                    logger.warning("RE stage: could not extract %s: %s", path.name, exc)
        logger.info("RE stage: corpus %s materialised into %s (%d text sidecar(s))",
                    src, dest, extracted)

    def _materialise_library_ref(self, run: StageRun, worktree: Path) -> None:
        """Point an ASP-only project's KB build at the approved global library.

        A project that uploads only ASP files has no module JSON of its own: its
        cards are selected from the global library (fe_core.kb.library). This
        writes <worktree>/inputs/library/ref.json naming the latest APPROVED
        library build, materialised from the artefact store when it is not on
        this host. It is a pointer rather than a copy, because the whole
        worktree is hashed and uploaded when the run's artefacts are persisted.

        Nothing is written for the library's own build, for a project that
        uploaded its own module JSON, or when no library build is approved; in
        the last case kb-extract fails the run with `no_library`.
        """
        import json as _json  # noqa: PLC0415
        from app.agentic_platform.fe_core.kb import library  # noqa: PLC0415

        if library.is_library(run.kb_application_id):
            return
        corpus = worktree / "inputs" / "corpus"
        cards_dir = corpus / "re-cards"
        if cards_dir.is_dir() and any(cards_dir.glob("*.json")):
            return
        asp_dir = corpus / library.ASP_SOURCE_KIND
        if not asp_dir.is_dir() or not any(asp_dir.iterdir()):
            return
        ref = library.materialise_library(self.store, self.settings)
        if ref is None:
            logger.warning("Run %s: ASP files uploaded but no approved global library build", run.run_id)
            return
        dest = worktree / "inputs" / "library"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "ref.json").write_text(_json.dumps(ref, indent=2), encoding="utf-8")
        run.log.append(
            f"library: using global library v{ref['version']} (artefact {ref['artifact_id']})")

    def _kb_gate(self, kb_dir: Path) -> dict:
        """Validate KB directory structure inline (no plugin required)."""
        if not kb_dir.is_dir():
            return {"pass": False, "findings": [{"check": "kb_dir", "msg": f"no kb/ directory at {kb_dir}"}]}

        # Normalise variant layouts before checking (cards/*.json → cards.jsonl etc.)
        try:
            from app.agentic_platform.fe_core.kb.cards import normalize_kb_layout  # noqa: PLC0415
            norm = normalize_kb_layout(kb_dir)
            if any(norm.values()):
                logger.info("kb layout normalised at %s: %s", kb_dir, norm)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb layout normalisation failed (gate runs on raw layout): %s", exc)

        findings = []

        # 1. cards.jsonl must exist and be non-empty
        cards_file = kb_dir / "cards.jsonl"
        if not cards_file.is_file() or cards_file.stat().st_size == 0:
            findings.append({"check": "cards", "msg": "kb/cards.jsonl is missing or empty"})

        # 2. At least one knowledge card must exist
        knowledge_dir = kb_dir / "knowledge"
        card_files = list(knowledge_dir.rglob("*.md")) if knowledge_dir.is_dir() else []
        if not card_files:
            findings.append({"check": "knowledge", "msg": "kb/knowledge/ contains no card files"})

        # 3. evidence-map.jsonl must exist when cards are present
        evidence_file = kb_dir / "evidence" / "evidence-map.jsonl"
        if card_files and not evidence_file.is_file():
            findings.append({"check": "evidence", "msg": "kb/evidence/evidence-map.jsonl is missing"})

        # 4. graph.json must exist
        graph_file = kb_dir / "knowledge" / "ontology" / "graph.json"
        if not graph_file.is_file():
            findings.append({"check": "graph", "msg": "kb/knowledge/ontology/graph.json is missing"})

        # Collect card/node/edge counts for the run log
        card_count = len(card_files)
        node_count = edge_count = 0
        if graph_file.is_file():
            try:
                import json as _json  # noqa: PLC0415
                g = _json.loads(graph_file.read_text(encoding="utf-8"))
                node_count = len(g.get("nodes", []))
                edge_count = len(g.get("edges", []))
            except Exception:  # noqa: BLE001
                pass

        return {
            "pass": len(findings) == 0,
            "cards": card_count,
            "nodes": node_count,
            "edges": edge_count,
            "findings": findings,
        }

    def _publish_kb(self, app_id: str, kb_dir: Path) -> None:
        """Replace the application's card KB with the freshly built one."""
        import shutil  # noqa: PLC0415

        target = self.settings.kb_root_for(app_id)
        if not kb_dir.is_dir():
            logger.warning("publish_kb: nothing to publish at %s", kb_dir)
            return
        backup = target.with_name(target.name + ".prev")
        if target.exists():
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
        shutil.copytree(kb_dir, target)
        logger.info("Card KB for %s published to %s (previous kept at %s)", app_id, target,
                    backup if backup.exists() else "n/a")

    def _hydrate_kb(self, app_id: str) -> None:
        """Pull ECS-built KB from S3 to laptop's kb_root_for with cache-hit validation."""
        if self.settings.fe_artifact_store != "s3":
            return

        from app.agentic_platform.fe_core.artifacts.store import get_artifact_store  # noqa: PLC0415

        target = self.settings.kb_root_for(app_id)
        manifest_path = target / "manifest.json"

        local_tree_sha = None
        if manifest_path.is_file():
            try:
                from app.agentic_platform.fe_core.artifacts.store import Manifest  # noqa: PLC0415
                local_manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
                local_tree_sha = local_manifest.tree_sha256
                logger.debug(f"_hydrate_kb: found local manifest (sha={local_tree_sha[:12] if local_tree_sha else 'N/A'})")
            except Exception as e:
                logger.warning(f"_hydrate_kb: local manifest parse error: {e}")

        artifact_store = get_artifact_store(self.settings)

        # Query for latest KB artifact: look for most recent v{N} directory
        key_prefix_pattern = f"fe/{app_id}/_global/kb/v"
        try:
            all_keys = artifact_store.list_keys(key_prefix_pattern)
            kb_keys = [k for k in all_keys if "/manifest.json" in k]
            if not kb_keys:
                logger.warning(f"_hydrate_kb: no KB artifacts found for {app_id}")
                return

            latest_key = sorted(kb_keys)[-1]
            latest_prefix = latest_key.rsplit("/manifest.json", 1)[0]

            remote_manifest = artifact_store.read_manifest(latest_prefix)
            if not remote_manifest:
                logger.warning(f"_hydrate_kb: cannot read remote manifest at {latest_prefix}")
                return

            remote_tree_sha = remote_manifest.tree_sha256

            if local_tree_sha and remote_tree_sha and local_tree_sha == remote_tree_sha:
                logger.info(f"_hydrate_kb: cache hit (sha={local_tree_sha[:12]})")
                return

            logger.info(f"_hydrate_kb: pulling from S3 (prefix={latest_prefix})")

            target.parent.mkdir(parents=True, exist_ok=True)

            artifact_store.get_tree(key_prefix=latest_prefix, dest=target)

            logger.info(f"_hydrate_kb: complete ({len(remote_manifest.files)} files)")

        except Exception as e:
            logger.error(f"_hydrate_kb: S3 download failed: {e}")
            raise

    def _next_kb_version(self, app_id: str) -> int:
        """Next KB version number for this application: one more than the highest
        ``<app_id>-re-v<N>`` already published to fe_kb_versions, so every build keeps its own rows
        (the S3 key ``kb/v<N>`` and the kb_version share N). 1 when no database is configured or
        nothing has been published yet; a lookup failure also falls back to 1 so a build never
        blocks on the version counter.
        """
        if not self.settings.uses_database():
            return 1
        try:
            import re as _re  # noqa: PLC0415
            import psycopg  # noqa: PLC0415
            from app.agentic_platform.fe_core.kb.pg_sink import _KB_SCHEMA  # noqa: PLC0415

            prefix = f"{app_id}-re-v"
            like = prefix.replace("\\", "\\\\").replace("_", r"\_").replace("%", r"\%") + "%"
            with psycopg.connect(self.settings.fe_db_url, connect_timeout=10) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT kb_version FROM {_KB_SCHEMA}.fe_kb_versions WHERE kb_version LIKE %s ESCAPE '\\'", (like,))
                numbers = []
                for (kb_version,) in cur.fetchall():
                    m = _re.search(r"-re-v(\d+)$", kb_version or "")
                    if m:
                        numbers.append(int(m.group(1)))
            return (max(numbers) + 1) if numbers else 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("_next_kb_version: could not read fe_kb_versions (%s); using 1", exc)
            return 1

    async def _persist_kb_artifacts(self, run: StageRun, kb_dir: Path) -> None:
        """Persist KB artifacts to S3 (if FE_ARTIFACT_STORE=s3)."""
        if self.settings.fe_artifact_store != "s3":
            return

        app_id = run.kb_application_id

        if not kb_dir.is_dir():
            logger.warning(f"_persist_kb_artifacts: kb_dir not found at {kb_dir}")
            return

        artifact_id = f"kb-{uuid.uuid4().hex[:8]}"
        version = await asyncio.to_thread(self._next_kb_version, app_id)

        from app.agentic_platform.fe_core.artifacts.store import get_artifact_store  # noqa: PLC0415
        artifact_store = get_artifact_store(self.settings)

        key_prefix = f"fe/{app_id}/_global/kb/v{version}/{artifact_id}"

        manifest = await asyncio.to_thread(
            artifact_store.put_tree,
            local_dir=kb_dir,
            key_prefix=key_prefix,
            artifact_id=artifact_id,
            artifact_type="kb",
            version=version,
            project_id=app_id,
            workspace_id=None
        )

        logger.info(
            f"Artifacts persisted: {key_prefix} "
            f"({sum(f.size for f in manifest.files) / 1_000_000:.2f} MB)"
        )

    def _card_store(self, app_id: str):
        """The application's card KB, or None when none has been built."""
        from app.agentic_platform.fe_core.kb.cards import CardStore  # noqa: PLC0415

        store = CardStore(self.settings.kb_root_for(app_id))
        return store if store.exists else None

    def _check_grounding(self, run: StageRun, stage: Stage, content_root: Path) -> bool:
        """Verify the produced Markdown cites known card ids.

        Returns True when the run may proceed. `stage.grounding == "required"`
        fails the run on zero or unknown citations; `"warn"` records the report
        only; `"off"` skips. Code stages are exempt (their evidence is the tests).
        """
        if stage.grounding == "off" or stage.target != "docs":
            return True
        store = self._card_store(run.kb_application_id)
        if store is None:
            run.log.append("grounding: no card KB for this application; check skipped")
            return True
        texts: list[str] = []
        for t in stage.produces:
            for path in content_root.rglob(f"{t}.md"):
                try:
                    texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
        report = store.check_citations("\n".join(texts))
        # Ids authored by the approved upstream artefacts (the FRD's FR-BL-11, the
        # BRD's BR-CO-002 ...) are legitimate cross-document citations, not KB
        # cards and not fabrications. They are materialised under
        # <worktree>/inputs by _materialise_upstream; harvest their ids.
        upstream_ids: set[str] = set()
        inputs_dir = content_root / "inputs"
        if inputs_dir.is_dir():
            from app.agentic_platform.fe_core.kb.cards import CARD_ID_RE  # noqa: PLC0415
            for path in inputs_dir.rglob("*.md"):
                try:
                    upstream_ids.update(CARD_ID_RE.findall(
                        path.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue
        upstream_known = [c for c in report.unknown if c in upstream_ids]
        if upstream_known:
            report.unknown = [c for c in report.unknown if c not in upstream_ids]
        payload = report.as_dict()
        payload["known_upstream"] = upstream_known
        payload["grounded"] = bool(report.known or upstream_known) and not report.unknown
        run.grounding = payload
        run.log.append(
            f"grounding: cited={len(report.cited)} kb={len(report.known)} "
            f"upstream={len(upstream_known)} unknown={len(report.unknown)} coverage="
            f"{'n/a' if report.coverage is None else f'{report.coverage:.0%}'}"
        )
        try:
            _grounding_verdict = "PASS" if payload.get("grounded") else (
                "BLOCK" if stage.grounding == "required" else "WARN"
            )
            telemetry = get_telemetry_service()
            telemetry.emit_grounding(
                stage=stage.key,
                artifact_id=run.artifact_ids[0] if run.artifact_ids else "",
                verdict=_grounding_verdict,
                cited=len(report.cited),
                known=len(report.known),
                unknown=len(report.unknown),
                coverage=report.coverage,
                workspace_id=run.workspace_id or "",
            )
        except Exception as _ge:  # noqa: BLE001
            logger.debug("GROUNDING emit failed (non-fatal): %s", _ge)
        if stage.grounding == "required" and not payload["grounded"]:
            reason = ("cites no known KB card id" if not report.cited else
                      f"cites unknown card id(s): {', '.join(report.unknown[:10])}")
            run.transition(StageState.FAILED,
                           error=f"grounding check failed: artefact {reason}",
                           error_code="ungrounded")
            logger.warning("Stage %s failed grounding: %s", stage.key, reason)
            return False
        if report.unknown:
            logger.warning("Stage %s cites unknown card ids: %s", stage.key, report.unknown[:10])
        return True

    async def _finalize(self, run: StageRun, stage: Stage, workspace: Path, target_dir,
                        result: RunResult, upstream: list[SdlcArtifact]) -> StageRun:
        """Post-run tail shared by execute() and recover(): deliverables, grounding,
        KB gate, persistence, indexing, publish, then WAITING_FOR_APPROVAL."""
        content_root = self._content_root(workspace, target_dir, result)
        git_ref = None
        if content_root != workspace:
            git_ref = self._commit_target_repo(content_root, run, stage, result)
        elif target_dir is not None and stage.target in ("ui", "api", "db", "policies", "tests"):
            # The agent kept its output in the worktree (under <artifact_type>/
            # folders). Mirror it into the target repo so the application code
            # accumulates in one place, and commit it on the EPIC branch.
            if self._sync_worktree_to_target(workspace, Path(target_dir), stage):
                git_ref = self._commit_target_repo(Path(target_dir), run, stage, result)
        missing = self._verify_deliverables(stage, content_root)
        if missing and content_root != workspace:
            missing = self._verify_deliverables(stage, workspace)
        if missing:
            run.transition(
                StageState.FAILED,
                error="missing deliverable(s): " + ", ".join(missing),
                error_code="missing_deliverable",
            )
            self.store.save_run(run)
            logger.warning("Stage %s produced no %s", stage.key, ", ".join(missing))
            return run

        # Grounding: does the artefact cite card ids that actually exist? Runs
        # before persistence so an ungrounded artefact never becomes a Draft.
        grounded_ok = self._check_grounding(run, stage, content_root)
        if not grounded_ok:
            self.store.save_run(run)
            return run

        # Reverse-engineering stage: the KB it built must pass the deterministic
        # gate before it is persisted or published. A KB with edges to unknown
        # nodes or cards without evidence poisons every downstream grounding check.
        if stage.publish_kb:
            gate = await asyncio.to_thread(self._kb_gate, content_root / "kb")
            run.log.append(f"kb-check: {'PASS' if gate.get('pass') else 'FAIL'} "
                           f"cards={gate.get('cards')} nodes={gate.get('nodes')} "
                           f"edges={gate.get('edges')} findings={len(gate.get('findings') or [])}")
            if not gate.get("pass"):
                first = (gate.get("findings") or [{}])[0]
                run.transition(StageState.FAILED,
                               error=f"kb-check gate failed: {first.get('check')}: {first.get('msg')}"
                                     f" (+{max(0, len(gate.get('findings') or []) - 1)} more)",
                               error_code="kb_gate_failed")
                self.store.save_run(run)
                return run

        persisted = self._persist_artifacts(run, stage, result, upstream,
                                            content_root=content_root, git_ref=git_ref)
        if not persisted:
            # FR-022: a "successful" stage with no artefact is a failure.
            run.transition(
                StageState.FAILED,
                error="stage produced no persisted artefact",
                error_code="no_artifact",
            )
            self.store.save_run(run)
            return run

        # Write DERIVES_FROM lineage + GROUNDS to DB (non-fatal — DB unavailable
        # on a laptop dev build should not block artifact persistence).
        await self._record_lineage(persisted, run, stage, upstream)

        # Auto LLM evaluation for document stages (non-fatal, fire-and-forget).
        if stage.target == "docs" and persisted:
            asyncio.create_task(
                self._auto_llm_eval(persisted, run, stage, content_root)
            )

        # Context provenance + stage diff + claim verification (non-fatal).
        asyncio.create_task(self._post_stage_observability(persisted, run, stage, content_root))

        # Extract stable requirement IDs (FR-001, BR-001 …) from document stages
        # and persist to fe_requirements for downstream traceability.
        if stage.key in {"prd", "frd", "nfr", "lld", "adr", "srd", "epic-set"}:
            try:
                from app.services.requirement_extractor import extract_and_persist  # noqa: PLC0415

                for artifact in persisted:
                    md_candidates = list((content_root or Path(run.worktree or ".")).rglob(
                        f"{artifact.artifact_type}.md")) if content_root else []
                    if md_candidates:
                        md_text = md_candidates[0].read_text(encoding="utf-8", errors="replace")
                        await extract_and_persist(md_text, artifact.id, run.workspace_id or "", stage.key)
            except Exception as _re_exc:
                logger.debug("[req-extractor] extraction skipped (non-fatal): %s", _re_exc)

        # Index each produced artifact into fe_kb_chunk so later stages can
        # retrieve its content as RAG context via _local_context().  Non-fatal:
        # if indexing fails, the next stage still reads the file via upstream_block.
        for artifact in persisted:
            await asyncio.to_thread(self._index_artifact_local, artifact)

        if stage.publish_kb:
            # Publish KB tree to local workspaces path.
            await asyncio.to_thread(self._publish_kb, run.kb_application_id, content_root / "kb")
            # Publish KB graph to Postgres (fe_kb_* tables) when DB is configured.
            if self.settings.uses_database():
                try:
                    from app.agentic_platform.fe_core.kb import pg_sink  # noqa: PLC0415
                    kb_version = f"{run.kb_application_id}-re-v{self._next_kb_version(run.kb_application_id)}"
                    _pg_summary = await asyncio.to_thread(
                        pg_sink.publish,
                        content_root / "kb",
                        run.kb_application_id,
                        getattr(stage, "label", stage.key),
                        kb_version,
                        gear_id=run.kb_application_id,
                    )
                    run.log.append(
                        f"pg_sink: kb_version={kb_version} "
                        f"nodes={_pg_summary['nodes']} edges={_pg_summary['edges']} "
                        f"cards={_pg_summary['cards']} evidence={_pg_summary['evidence']}"
                    )
                except Exception as _pg_exc:  # noqa: BLE001
                    logger.warning("pg_sink.publish failed (non-fatal): %s", _pg_exc)
                    run.log.append(f"pg_sink: skipped ({_pg_exc})")

        # Soft validation: check required sections and assumption density for
        # document stages.  Results are appended to run.log so the approver sees
        # them without blocking the approval gate.
        if stage.key in {"prd", "frd", "nfr", "lld", "adr", "srd", "sdd"}:
            try:
                from app.services.artifact_schema_validator import validate_artifact  # noqa: PLC0415
                from app.services.assumption_detector import assumption_summary  # noqa: PLC0415

                for artifact in persisted:
                    md_files = list((content_root or Path(run.worktree or ".")).rglob(
                        f"{artifact.artifact_type}.md")) if content_root else []
                    if md_files:
                        md_text = md_files[0].read_text(encoding="utf-8", errors="replace")
                        v_result = validate_artifact(md_text, stage.key)
                        a_summary = assumption_summary(md_text)
                        if not v_result.valid:
                            run.log.append(
                                f"structure-check: missing sections {v_result.missing_sections} "
                                f"in {artifact.artifact_type} (soft warning — does not block approval)"
                            )
                        if a_summary["assumption_count"]:
                            run.log.append(
                                f"assumption-check: {a_summary['assumption_count']} assumption(s) "
                                f"detected in {artifact.artifact_type} "
                                f"(ratio={a_summary['assumption_ratio']:.1%})"
                            )
            except Exception as _va_exc:
                logger.debug("[artifact-validation] skipped (non-fatal): %s", _va_exc)

        run.transition(StageState.WAITING_FOR_APPROVAL)
        self.store.save_run(run)
        logger.info(
            "Stage %s in %s awaiting approval by '%s' (%d artefact(s), %d tokens)",
            stage.key, run.workspace_id or "unscoped", stage.approval_persona,
            len(run.artifact_ids), result.total_tokens,
        )
        return run

    async def recover(self, run: StageRun, *, by: str | None = None) -> StageRun:
        """Operator action: re-run the post-run tail for a run the turn cap failed
        whose deliverable is complete on disk (`error_code == "max_turns"`).

        Nothing is re-generated and no tokens are spent: the worktree is taken as
        it was left, and every check the normal path applies (deliverables,
        grounding, KB gate, persistence) still applies. The run is marked
        `capped` and the recovery is logged so the approver knows.
        """
        stage = self.pipeline.stage(run.stage_key)
        # Recoverable when the deliverable is already complete on disk but the run was
        # marked FAILED by the turn cap OR a transient client/AWS error at the persist
        # step (e.g. an S3 PutObject that hit an expired SSO token). _verify_deliverables
        # below still guards against finalising an incomplete run.
        _RECOVERABLE_ERRORS = ("max_turns", "ClientError")
        if run.state is not StageState.FAILED or run.error_code not in _RECOVERABLE_ERRORS:
            raise StageExecutionError(
                f"run {run.run_id} is {run.state.value}/{run.error_code}; only runs failed by "
                "the max_turns cap or a transient ClientError (with the deliverable complete "
                "on disk) can be recovered")
        workspace = Path(run.worktree) if run.worktree else self.settings.run_workspace(
            run.kb_application_id, run.run_id,
            stage_key=run.stage_key, workspace_id=run.workspace_id or "")
        if not workspace.is_dir():
            raise StageExecutionError(f"worktree {workspace} no longer exists")
        missing = self._verify_deliverables(stage, workspace)
        if missing:
            raise StageExecutionError("deliverable(s) missing, nothing to recover: " + ", ".join(missing))
        target_dir = self.settings.stage_target_dir(stage.target, workspace_id=run.workspace_id,
                                                    epic_id=run.epic_id)
        result = RunResult(correlation_id=run.correlation_id or run.run_id, runner=run.runner or "",
                           model=run.model or "", files_written=list(run.files_written),
                           input_tokens=run.input_tokens, output_tokens=run.output_tokens,
                           cache_read_input_tokens=run.cache_read_input_tokens,
                           cache_creation_input_tokens=run.cache_creation_input_tokens,
                           cost_usd=run.cost_usd, num_turns=run.num_turns)
        upstream = self._upstream_artifacts(run, stage)
        run.capped = True
        run.error = None
        run.error_code = None
        run.log.append(f"recovered: max_turns cap hit with the deliverable complete; "
                       f"post-run checks re-applied by {by or 'operator'}")
        # FAILED -> QUEUED -> RUNNING is the only legal path back into the flow.
        run.transition(StageState.QUEUED)
        run.transition(StageState.RUNNING)
        self.store.save_run(run)
        return await self._finalize(run, stage, workspace, target_dir, result, upstream)

    @staticmethod
    def _content_root(workspace: Path, target_dir, result: RunResult) -> Path:
        """Where this run's deliverables live: the target repo when the agent
        wrote there, else the run worktree."""
        if target_dir is None:
            return workspace
        target = Path(target_dir).resolve()
        written = [Path(f).resolve() for f in (result.files_written or [])]
        under_target = sum(1 for f in written if target in f.parents)
        under_ws = sum(1 for f in written if workspace.resolve() in f.parents)
        return target if under_target and under_target >= under_ws else workspace

    @staticmethod
    def _sync_worktree_to_target(worktree: Path, target: Path, stage: Stage) -> bool:
        """Copy a code stage's worktree output into the target repo.

        Agents sometimes write `<worktree>/<artifact_type>/...` (mirroring the
        contract's artefact names) instead of writing into the target repo. The
        content of each such folder is overlaid onto the repo root; anything
        else at the worktree top level (SUMMARY.md, extra files) is copied
        alongside. Returns True when something was copied.
        """
        import shutil  # noqa: PLC0415

        copied = False
        skip = {"inputs", ".mcp-config.json", ".upstream"}
        try:
            target.mkdir(parents=True, exist_ok=True)
            for entry in worktree.iterdir():
                if entry.name in skip:
                    continue
                if entry.is_dir() and entry.name in set(stage.produces):
                    shutil.copytree(entry, target, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
                    copied = True
                elif entry.is_dir():
                    shutil.copytree(entry, target / entry.name, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
                    copied = True
                elif entry.is_file():
                    shutil.copy2(entry, target / entry.name)
                    copied = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mirror %s output into %s: %s", stage.key, target, exc)
        if copied:
            logger.info("Mirrored %s worktree output into %s", stage.key, target)
        return copied

    @staticmethod
    def _commit_target_repo(repo: Path, run: StageRun, stage: Stage,
                            result: RunResult) -> dict | None:
        """Commit a code stage's output on `fe/<workspace-id>` in the target repo.

        This is the merge model: every EPIC lands as commits on its own branch
        of the shared UI / API repo, merged to main by PR. Non-fatal -- a repo
        that is not git (or a failing git) still leaves the files in place.
        """
        import subprocess  # noqa: PLC0415

        if not (repo / ".git").exists():
            return None
        branch = f"fe/{run.workspace_id}"
        env = {"GIT_AUTHOR_NAME": "ADLC pipeline", "GIT_AUTHOR_EMAIL": "adlc@pipeline",
               "GIT_COMMITTER_NAME": "ADLC pipeline", "GIT_COMMITTER_EMAIL": "adlc@pipeline"}

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                                  timeout=120, env={**os.environ, **env})
        try:
            ignore = repo / ".gitignore"
            wanted = ["node_modules/", "dist/", "target/", ".angular/", "build/", ".gradle/", "*.log"]
            existing = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
            add = [w for w in wanted if w not in existing]
            if add:
                ignore.write_text("\n".join([*existing, *add]) + "\n", encoding="utf-8")
            git("checkout", "-B", branch)
            git("add", "-A")
            msg = (f"{stage.key}: {run.workspace_id} (run {run.run_id})\n\n"
                   f"Generated by {run.plugin or stage.owner.plugin or 'ADLC'} for "
                   f"{run.epic_id or 'global'}; {len(result.files_written or [])} file(s) written.")
            commit = git("commit", "-q", "-m", msg)
            if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
                logger.warning("git commit in %s failed: %s", repo, (commit.stderr or commit.stdout)[:300])
            sha = git("rev-parse", "HEAD").stdout.strip()
            logger.info("Committed %s output to %s@%s (%s)", stage.key, branch, sha[:12], repo)
            return {"repo": str(repo), "branch": branch, "commit": sha}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not commit %s output in %s: %s", stage.key, repo, exc)
            return None

    @staticmethod
    def _verify_deliverables(stage: Stage, worktree: Path) -> list[str]:
        """Names the output contract promised but the run did not leave on disk.

        Docs stages: one `<type>.md` per produced type. Code stages: at least one
        source/test/policy file. Anything else is not verifiable here and passes.
        """
        missing: list[str] = []
        if stage.target == "docs":
            for t in stage.produces:
                target_name = f"{t}.md"
                if not any(p.name == target_name for p in _walk_files(worktree)):
                    missing.append(target_name)
        elif stage.target in ("ui", "api", "db", "policies", "tests"):
            files = [p for p in _walk_files(worktree)
                     if ".mcp-config" not in p.name and "inputs" not in p.parts]
            if not files:
                missing.append("<any source file>")
            elif not any(p.name == "SUMMARY.md" for p in files):
                # SUMMARY.md is a provenance nicety, not the deliverable. Agents
                # intermittently end without it even when the real output (code/tests/
                # policies) is complete, which was hard-failing fully-generated stages.
                # Accept on the source files; don't block on a missing summary.
                logger.warning(
                    "Stage %s produced deliverables but no SUMMARY.md; accepting on source files.",
                    stage.key,
                )
        return missing

    def _materialise_upstream(self, upstream: list[SdlcArtifact], worktree: Path) -> dict[str, Path | None]:
        """Bring each approved input into <worktree>/inputs/<type>/ so the agent reads
        local files regardless of where the body lives (S3, local store, old worktree)."""
        import shutil  # noqa: PLC0415

        out: dict[str, Path | None] = {}
        for art in upstream:
            dest = worktree / "inputs" / art.artifact_type
            try:
                local = materialise(art, dest=dest, settings=self.settings)
                if local is None:                       # git-ref artefact: nothing to copy
                    out[art.artifact_type] = None
                    continue
                if Path(local).resolve() != dest.resolve():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(local, dest,
                                    ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", "inputs"))
                out[art.artifact_type] = dest
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not materialise upstream %s v%s: %s",
                               art.artifact_type, art.version, exc)
                out[art.artifact_type] = None
        return out

    async def _persist_prompt_snapshot(
        self,
        *,
        run_id: str,
        workspace_id: str,
        stage: str,
        prompt_hash: str,
        token_estimate: int,
        chunk_count: int,
    ) -> None:
        """Write a prompt_snapshot row for the run. Non-fatal."""
        try:
            from app.dao.postgres import get_pool as _gp  # noqa: PLC0415
            from app.config import get_settings as _gs  # noqa: PLC0415

            schema = _gs().PG_SCHEMA
            sql = f"""
                INSERT INTO {schema}.fe_prompt_snapshot
                    (run_id, workspace_id, stage, prompt_hash, token_estimate, chunk_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
            """
            async with _gp().connection() as conn:
                await conn.execute(sql, (
                    run_id, workspace_id, stage, prompt_hash, token_estimate, chunk_count,
                ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[prompt_snapshot] insert failed (non-fatal): %s", exc)

    async def _post_stage_observability(
        self,
        persisted: list[SdlcArtifact],
        run: StageRun,
        stage: Stage,
        content_root: Path | None,
    ) -> None:
        """Persist provenance, stage diff, and claim evidence after a stage completes."""
        try:
            from app.dao.postgres import get_pool as _gp  # noqa: PLC0415
            from app.config import get_settings as _gs  # noqa: PLC0415
            from app.services.stage_diff import compute_and_persist  # noqa: PLC0415
            pool = _gp()
            schema = _gs().PG_SCHEMA
            if run.workspace_id:
                await compute_and_persist(
                    workspace_id=run.workspace_id,
                    current_stage=stage.key,
                    pool=pool, schema=schema,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[post_obs] stage diff failed (non-fatal): %s", exc)

        if stage.target == "docs" and persisted:
            await self._run_claim_verifier(persisted, run, stage, content_root)

    async def _run_claim_verifier(
        self,
        persisted: list[SdlcArtifact],
        run: StageRun,
        stage: Stage,
        content_root: Path | None,
    ) -> None:
        """Extract and ground claims from the produced artifact. Non-fatal."""
        try:
            from app.dao.postgres import get_pool as _gp  # noqa: PLC0415
            from app.config import get_settings as _gs  # noqa: PLC0415
            from app.services.claim_verifier import verify_claims  # noqa: PLC0415
            artifact_text = self._read_artifact_text(persisted, stage, content_root)
            if not artifact_text or not run.workspace_id:
                return
            pool = _gp()
            schema = _gs().PG_SCHEMA
            await verify_claims(
                artifact_id=persisted[0].id,
                workspace_id=run.workspace_id,
                stage=stage.key,
                artifact_text=artifact_text,
                kb_chunks=[],
                pool=pool, schema=schema,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[claim_verifier] failed (non-fatal): %s", exc)

    async def _register_workspace(self, run: StageRun) -> None:
        """Upsert the workspace into fe_workspaces so observability queries have a registry row.

        Non-fatal: DB unavailability (laptop dev build) must never block a stage run.
        """
        if not run.workspace_id:
            return
        try:
            from app.dao.postgres import get_pool as _get_pool  # noqa: PLC0415
            from app.config import get_settings as _get_settings  # noqa: PLC0415

            schema = _get_settings().PG_SCHEMA
            pool = _get_pool()
            sql = f"""
                INSERT INTO {schema}.fe_workspaces
                    (workspace_id, kb_application_id, status, created_at, updated_at)
                VALUES (%s, %s, 'active', NOW(), NOW())
                ON CONFLICT (workspace_id) DO UPDATE SET updated_at = NOW()
            """
            async with pool.connection() as conn:
                await conn.execute(sql, (run.workspace_id, run.kb_application_id or ""))
        except Exception as _exc:  # noqa: BLE001
            logger.debug("Workspace registration skipped (non-fatal): %s", _exc)

    async def _auto_llm_eval(
        self,
        persisted: list[SdlcArtifact],
        run: StageRun,
        stage: Stage,
        content_root: Path | None,
    ) -> None:
        """Run LLM-as-judge evaluation for document stages and persist scores.

        Non-fatal: errors are logged at WARNING level and never propagate.
        """
        try:
            from app.services.evaluation import get_evaluation_service  # noqa: PLC0415
            from app.dao.postgres import get_pool as _get_pool  # noqa: PLC0415
            from app.config import get_settings as _get_settings  # noqa: PLC0415

            artifact_text = self._read_artifact_text(persisted, stage, content_root)
            if not artifact_text:
                return
            reference_rules = [cid for cid in run.kb_chunk_ids[:20]]
            _eval = get_evaluation_service()
            llm_results = await _eval.evaluate_with_llm(
                agent_run_id=run.run_id,
                artifact_content=artifact_text,
                reference_rules=reference_rules,
                artifact_id=persisted[0].id if persisted else "",
                workspace_id=run.workspace_id or "",
            )
            await _eval.persist(llm_results, pool=_get_pool(), schema=_get_settings().PG_SCHEMA)
        except Exception as _exc:  # noqa: BLE001
            logger.warning("Stage %s: LLM evaluation failed (non-fatal): %s", stage.key, _exc)

    def _read_artifact_text(
        self, persisted: list[SdlcArtifact], stage: Stage, content_root: Path | None,
    ) -> str:
        """Read the first Markdown artifact from the content root or worktree."""
        root = content_root
        if root is None or not root.exists():
            return ""
        for artifact_type in stage.produces:
            for path in root.rglob(f"{artifact_type}.md"):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    return text[:8000]  # cap to avoid huge LLM eval prompts
                except OSError:
                    continue
        return ""

    async def _record_lineage(
        self,
        persisted: list[SdlcArtifact],
        run: StageRun,
        stage: Stage,
        upstream: list[SdlcArtifact],
    ) -> None:
        """Write DERIVES_FROM relationships and GROUNDS links to Postgres for every persisted artifact.

        Non-fatal: DB may be unavailable on a laptop dev build. Both DAOs use the
        shared asyncpg pool via BaseRepository — if the pool is not open this
        swallows the error so artifact persistence is never blocked.
        """
        try:
            from app.lifecycle.traceability.relationships_dao import ArtifactRelationshipDAO  # noqa: PLC0415
            from app.lifecycle.traceability.dao import GroundsLinkDAO  # noqa: PLC0415

            rel_dao = ArtifactRelationshipDAO()
            grounds_dao = GroundsLinkDAO()
        except Exception as exc:
            logger.debug("[lineage] DAO import failed (non-fatal): %s", exc)
            return

        ws = run.workspace_id or ""

        for artifact in persisted:
            # --- Semantic edge per upstream artifact (derives_from / implements / tests / refines) ---
            for src in upstream:
                try:
                    rel_type = _edge_type(stage.key, src.artifact_type)
                    await rel_dao.create(
                        workspace_id=ws,
                        source_artifact_id=artifact.id,
                        target_artifact_id=src.id,
                        relationship_type=rel_type,
                        description=(f"Stage '{stage.key}' output '{artifact.artifact_type}' "
                                     f"{rel_type} '{src.artifact_type}' v{src.version}"),
                    )
                except Exception as exc:
                    logger.warning("[lineage] relationship write failed (non-fatal): %s", exc)

            # --- GROUNDS: one edge per KB chunk retrieved into the prompt ---
            kb_version = getattr(self.kb, "kb_version", "") or ""
            for chunk_id in run.kb_chunk_ids:
                try:
                    await grounds_dao.create(
                        workspace_id=ws,
                        artifact_id=artifact.id,
                        kb_card_id=chunk_id,
                        kb_card_kind="chunk",
                        kb_card_label=chunk_id,
                        source_locus=stage.key,
                        applied_because="RETRIEVAL_MATCH",
                        persona=stage.approval_persona or run.initiated_by_persona or "",
                        kb_version=kb_version,
                        stage=stage.key.upper(),
                        reason=f"KB chunk retrieved during '{stage.key}' context assembly",
                        source_item_id=None,
                    )
                except Exception as exc:
                    logger.warning("[lineage] GROUNDS write failed (non-fatal): %s", exc)

    def _persist_artifacts(
        self, run: StageRun, stage: Stage, result: RunResult,
        upstream: list[SdlcArtifact], *,
        content_root: Path | None = None, git_ref: dict | None = None,
    ) -> list[SdlcArtifact]:
        """One Draft artefact per declared output, idempotent by checksum."""
        created: list[SdlcArtifact] = []
        parent = upstream[0].id if upstream else None
        # Checksum from the files actually on disk (stable across retries), falling
        # back to the runner text for stages that produce no files.
        worktree = (Path(content_root) if content_root is not None
                    else Path(run.worktree) if run.worktree else None)
        tree_refs = local_tree_manifest(worktree) if worktree and worktree.exists() else []
        if tree_refs:
            checksum_base = sha256("\n".join(f"{f.path}:{f.sha256}" for f in tree_refs))
        else:
            checksum_base = sha256(result.text or "") if result.text else sha256(
                "\n".join(sorted(result.files_written))
            )
        artifact_store = get_artifact_store(self.settings)

        for artifact_type in stage.produces:
            checksum = sha256(f"{artifact_type}:{checksum_base}")
            existing = self.store.find_artifact_by_checksum(
                self.pipeline.name, stage.key, artifact_type, checksum,
                workspace_id=run.workspace_id,
            )
            if existing is not None:
                # FR-023: a retry returns the existing reference, no duplicate.
                logger.info(
                    "Artefact %s v%d already exists for identical content; reusing",
                    existing.artifact_type, existing.version,
                )
                created.append(existing)
                if existing.id not in run.artifact_ids:
                    run.artifact_ids.append(existing.id)
                continue

            superseded = self.store.supersede_previous(
                self.pipeline.name, stage.key, artifact_type,
                workspace_id=run.workspace_id,
            )
            artifact = SdlcArtifact(
                id=uuid.uuid4().hex[:12],
                kb_application_id=run.kb_application_id,
                pipeline=self.pipeline.name,
                stage_key=stage.key,
                sdlc_stage=stage.key,
                artifact_type=artifact_type,
                version=self.store.next_version(
                    self.pipeline.name, stage.key, artifact_type,
                    workspace_id=run.workspace_id),
                supersedes_id=superseded,
                parent_id=parent,
                # Workspace scoping (FR-P4/FR-P5) and persona provenance (FR-P3).
                workspace_id=run.workspace_id,
                tier=run.tier,
                epic_id=run.epic_id,
                produced_by_persona=(run.initiated_by_persona
                                     or stage.approval_persona),
                artifact_tier=stage.artifact_tier,
                path=str(worktree) if worktree else str(run.worktree),
                content_uri=str(worktree) if worktree else str(run.worktree),
                checksum=checksum,
                content_sha256=checksum,
                status=ArtifactStatus.DRAFT,
                source_workspace=run.worktree,
                git_repo=(git_ref or {}).get("repo"),
                git_branch=(git_ref or {}).get("branch"),
                git_commit=(git_ref or {}).get("commit"),
                run_id=run.run_id,
                plugin=run.plugin,
                plugin_version=run.plugin_version,
                model=run.model,
                runner=run.runner,
                source_artifact_ids=[a.id for a in upstream],
                links=([{"type": "DERIVES_FROM", "artifact_id": a.id, "artifact_type": a.artifact_type,
                         "version": a.version} for a in upstream]
                       + [{"type": "GROUNDS", "kb_chunk_id": cid} for cid in run.kb_chunk_ids]),
                created_by=run.initiated_by,
                created_at=datetime.now(timezone.utc),
                created_by_run=run.run_id,
            )
            # Upload the body to the artefact store (S3 on ECS, local dir on a laptop)
            # so the next agent - possibly on another task - can read it. The
            # worktree stays as a local cache.
            if worktree and worktree.exists() and tree_refs:
                # Filter tree_refs to only include files under the artifact_type subdirectory
                # (e.g., only api/* for integrated-api, ui/* for integrated-ui).
                # If no subdirectory match, include all files (for artifact types like validation-report
                # that produce flat documents).
                subdir = worktree / artifact_type
                if subdir.exists() and subdir.is_dir():
                    # Upload only the subdirectory for this artifact type
                    upload_tree = subdir
                else:
                    # No matching subdirectory; upload the whole worktree (common for document artifacts)
                    upload_tree = worktree

                key = artifact_key(
                    self.settings.fe_s3_prefix if artifact_store.kind == "s3" else "fe",
                    run.kb_application_id, run.workspace_id, artifact_type,
                    artifact.version, artifact.id,
                )
                manifest = artifact_store.put_tree(
                    upload_tree, key, artifact_id=artifact.id, artifact_type=artifact_type,
                    version=artifact.version, project_id=run.kb_application_id,
                    workspace_id=run.workspace_id,
                )
                artifact.storage_kind = artifact_store.kind
                artifact.content_uri = artifact_store.uri(key)
                artifact.manifest_uri = artifact_store.uri(f"{key}/manifest.json")
                artifact.files = [
                    {"path": f.path, "sha256": f.sha256, "size": f.size, "content_type": f.content_type}
                    for f in manifest.files
                ]
            else:
                artifact.storage_kind = "worktree"
            self.store.put_artifact(artifact)
            run.artifact_ids.append(artifact.id)
            created.append(artifact)
        return created

    # -- batch processing methods (Phase 2) ----------------------------------

    def _should_use_batching(self, artifact_path: Path, artifact_type: str) -> bool:
        """Determine if an artifact should be processed in batches.

        Returns True only if:
        1. FE_BATCH_ENABLED is true
        2. Pipeline tier is global or architecture (never mini)
        3. Total artifact size > FE_BATCH_THRESHOLD

        Args:
            artifact_path: Path to the artifact directory
            artifact_type: Type of artifact being processed

        Returns:
            True if batch processing should be used, False otherwise
        """
        if not self.settings.fe_batch_enabled:
            return False
        if self.pipeline.tier not in ("global", "architecture"):
            return False

        # Calculate total size of all .md files in the artifact
        total_chars = 0
        for p in sorted(artifact_path.rglob("*.md")):
            try:
                total_chars += len(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue

        exceeds_threshold = total_chars > self.settings.fe_batch_threshold
        logger.info(
            "Batch check: artifact=%s, tier=%s, size=%d, threshold=%d, batch=%s",
            artifact_type, self.pipeline.tier, total_chars,
            self.settings.fe_batch_threshold, exceeds_threshold
        )
        return exceeds_threshold

    def _split_into_batches(self, artifact_path: Path) -> list[dict[str, str]]:
        """Split artifact markdown files into batches based on FE_BATCH_SIZE.

        Returns a list of batches, where each batch is a dict[rel_path, text].
        Ensures each batch stays under FE_BATCH_SIZE chars total.

        Args:
            artifact_path: Path to the artifact directory

        Returns:
            List of batch dictionaries, each containing {rel_path: text}
        """
        all_files = read_markdown_files(artifact_path, max_chars=self.settings.fe_artifact_read_max_chars)
        batches: list[dict[str, str]] = []
        current_batch: dict[str, str] = {}
        current_size = 0

        for rel_path, text in all_files.items():
            text_size = len(text)

            # If single file exceeds batch size, split it into chunks
            if text_size > self.settings.fe_batch_size:
                # Flush current batch first
                if current_batch:
                    batches.append(current_batch)
                    current_batch = {}
                    current_size = 0

                # Split large file into chunks
                chunks = [
                    text[i:i + self.settings.fe_batch_size]
                    for i in range(0, len(text), self.settings.fe_batch_size)
                ]
                for idx, chunk in enumerate(chunks):
                    batches.append({f"{rel_path} (part {idx+1}/{len(chunks)})": chunk})
            else:
                # Check if adding this file would exceed batch size
                if current_size + text_size > self.settings.fe_batch_size:
                    # Flush current batch
                    batches.append(current_batch)
                    current_batch = {}
                    current_size = 0

                # Add file to current batch
                current_batch[rel_path] = text
                current_size += text_size

        # Flush final batch
        if current_batch:
            batches.append(current_batch)

        logger.info(
            "Split artifact into %d batches (batch_size=%d)",
            len(batches), self.settings.fe_batch_size
        )
        return batches

    def _build_batch_prompt(
        self,
        base_prompt: str,
        batch_content: dict[str, str],
        batch_num: int,
        total_batches: int,
        artifact_type: str
    ) -> str:
        """Build a prompt for processing a single batch of an artifact.

        Includes context about the batch position and instructions for partial output.

        Args:
            base_prompt: The base prompt without artifact content
            batch_content: Dictionary of {rel_path: text} for this batch
            batch_num: Current batch number (1-indexed)
            total_batches: Total number of batches
            artifact_type: Type of artifact being processed

        Returns:
            Complete prompt for this batch
        """
        batch_header = f"""
## Batch Processing Context

You are processing batch {batch_num} of {total_batches} for artifact type: {artifact_type}

**Important Instructions**:
- This is a partial view of the complete artifact
- Focus on extracting/analyzing content from THIS batch only
- Your output will be merged with other batches
- Maintain consistency in formatting and structure
- If you reference content, note that it may span multiple batches
"""

        batch_content_str = "\n\n".join(
            f"### {rel_path}\n\n{text}"
            for rel_path, text in batch_content.items()
        )

        return f"{base_prompt}\n\n{batch_header}\n\n## Batch Content\n\n{batch_content_str}"

    async def _merge_batch_results(
        self,
        batch_results: list[str],
        artifact_type: str
    ) -> str:
        """Merge results from multiple batch executions.

        Strategy depends on FE_BATCH_MERGE_STRATEGY:
        - sequential: Concatenate results in order with section markers
        - consolidate: Use LLM to consolidate/deduplicate (future)
        - hierarchical: Build hierarchical summary (future)

        Args:
            batch_results: List of output strings from each batch
            artifact_type: Type of artifact being processed

        Returns:
            Merged output string
        """
        strategy = self.settings.fe_batch_merge_strategy
        logger.info(
            "Merging %d batch results using strategy: %s",
            len(batch_results), strategy
        )

        if strategy == "sequential":
            # Simple concatenation with clear boundaries
            merged = f"# {artifact_type} (Merged from {len(batch_results)} batches)\n\n"
            for idx, result in enumerate(batch_results, start=1):
                merged += f"\n\n## Batch {idx} Results\n\n{result}\n\n---\n"
            return merged

        elif strategy == "consolidate":
            # TODO: Use LLM to consolidate and deduplicate
            # For now, fall back to sequential
            logger.warning("Consolidate strategy not yet implemented, falling back to sequential")
            return await self._merge_batch_results(batch_results, artifact_type)

        else:  # hierarchical
            # TODO: Build hierarchical summary
            # For now, fall back to sequential
            logger.warning("Hierarchical strategy not yet implemented, falling back to sequential")
            return await self._merge_batch_results(batch_results, artifact_type)


def _plugin_version(plugins: list[PluginSpec]) -> str | None:
    for spec in plugins:
        manifest = spec.path / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            import json

            try:
                return json.loads(manifest.read_text(encoding="utf-8")).get("version")
            except Exception:  # noqa: BLE001
                return None
    return None

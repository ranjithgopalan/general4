"""Service configuration.

Two independent transport switches, per PRD 1.1 ("complementary boundaries, not
competing choices") and the two architecture diagrams:

    FE_KB_TRANSPORT    how this service reads the KB   -> rest | mcp | both
    FE_KB_AGENT_TOOLS  whether the agent gets KB tools -> mcp | none
    FE_RUNNER          how agents execute              -> sdk | cli | mock | auto

Legacy-database connection details are NOT here. They live in `datasources.yaml`
with secrets referenced by environment-variable name. the knowledge base put a single UW CR
connection in config and then read only the password from it -- HOST, PORT, DB,
USER, ENCRYPT and TRUST_CERT became dead config. Keeping datasources declarative
avoids repeating that.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Vendored into the agent repo under app/agentic_platform/. This file lives at
# app/agentic_platform/fe_core/config.py, so:
#   parents[1] -> app/agentic_platform   (committed assets: config, plugins, pipelines, scripts)
#   parents[3] -> <repo root>  (runtime data: .env, workspaces, generated, logs)
_HERE = Path(__file__).resolve()
AGENTIC_ROOT = _HERE.parents[1]        # .../agent/app/agentic_platform
SERVICE_ROOT = _HERE.parents[3]     # .../agent (repo root)
SRC_ROOT = SERVICE_ROOT             # backwards-compatible alias
API_ROOT = AGENTIC_ROOT                # config / plugins / pipelines / scripts live here
APPS_ROOT = AGENTIC_ROOT               # backwards-compatible alias

#: Non-secret configuration lives here (JSON, committed). Environment variables and
#: the .env file override it; .env is reserved for secrets and machine-local values
#: such as FE_DB_URL / FE_DB_SCHEMA. Override the location with FE_CONFIG_JSON.
# Single source of truth: the merged agent config.json (the FE_* keys were merged
# into it, so there is one config file for the whole service, not two).
CONFIG_JSON_PATH = Path(os.environ.get("FE_CONFIG_JSON") or (SERVICE_ROOT / "app" / "config" / "config.json"))

_log = logging.getLogger(__name__)


def load_config_json(path: Path | None = None) -> dict[str, str]:
    """Read config.json into a flat {ENV_NAME: value} mapping.

    Accepts either a flat object or an object of named groups (one level deep) whose
    keys are the environment-variable names; group names are documentation only.
    Values are stringified because they feed the same parser as environment
    variables. Missing or unreadable file -> empty mapping (defaults still apply).
    """
    path = Path(path or CONFIG_JSON_PATH)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        _log.warning("config.json unreadable at %s: %s", path, exc)
        return {}
    flat: dict[str, str] = {}
    for key, value in (data or {}).items():
        if key.startswith("_"):
            continue                       # "_comment" style keys
        if isinstance(value, dict):
            for k2, v2 in value.items():
                if k2.startswith("_") or v2 is None:
                    continue
                flat[str(k2)] = _stringify(v2)
        elif value is not None:
            flat[str(key)] = _stringify(value)
    return flat


def _stringify(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


def apply_config_json(path: Path | None = None) -> dict[str, str]:
    """Load config.json and set each key into os.environ **only if not already set**.

    Precedence therefore is: process environment > .env (read by pydantic) > config.json
    > code defaults. Called on first get_settings(); safe to call again.
    """
    values = load_config_json(path)
    for k, v in values.items():
        os.environ.setdefault(k, v)
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Both files are optional; app/.env (local-machine overrides, see PG_CONNECTION_MODE) wins over the root one.
        env_file=(SERVICE_ROOT / ".env", SERVICE_ROOT / "app" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- this service ------------------------------------------------------
    fe_host: str = "0.0.0.0"
    fe_port: int = 8100
    fe_log_level: str = "INFO"
    fe_api_prefix: str = "/api/v1"

    # --- the knowledge base (the knowledge base) -----------------------------------------
    # Old KB_* / LMOD_* env names kept for MCP transport only (REST write-back removed).
    kb_backend_root: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("KB_BACKEND_ROOT", "LMOD_BACKEND_ROOT"),
    )
    kb_mcp_module: str = Field(
        default="backend.mcp.kb_server",
        validation_alias=AliasChoices("KB_MCP_MODULE", "LMOD_MCP_MODULE"),
    )

    # --- transports --------------------------------------------------------
    fe_kb_transport: Literal["rest", "mcp", "both"] = "rest"
    fe_kb_agent_tools: Literal["mcp", "none"] = "mcp"
    fe_runner: Literal["sdk", "cli", "mock", "auto", "bedrock"] = "auto"
    claude_cli_path: str | None = None

    # When true the CLI subprocess runs without ANTHROPIC_API_KEY in its
    # environment, forcing it to use the subscription session established by
    # `claude auth login` (Claude Pro / Team / Enterprise).
    # Set this when your API-key workspace has a rate-limit that blocks the CLI,
    # or when you are billed through a Claude.ai plan rather than the API.
    claude_cli_use: bool = False

    # --- platform topology (ECS readiness; defaults keep today's behaviour) --
    # FE_STORE          json      one state.json under <workspace_root>/_state (single writer)
    #                   postgres  fe.* tables in FE_DB_URL (multi-task safe, SKIP LOCKED queues)
    # FE_ARTIFACT_STORE local     artefact files under FE_ARTIFACT_ROOT
    #                   s3        artefact files in FE_S3_BUCKET/FE_S3_PREFIX (SSE-KMS, presigned GET)
    # FE_EXECUTION      inprocess stages run inside the API process (asyncio task)
    #                   worker    API only enqueues; the fe-orchestrator service claims and runs
    # FE_ORCHESTRATOR   manual    humans start each stage (today)
    #                   langgraph a StateGraph per workspace auto-runs to each approval gate
    fe_store: Literal["json", "postgres"] = "json"
    fe_artifact_store: Literal["local", "s3"] = "local"
    fe_artifact_root: Path = SERVICE_ROOT / "workspaces" / "_artifacts"
    fe_s3_bucket: str | None = None
    fe_s3_prefix: str = "fe"
    fe_s3_kms_key_id: str | None = None
    fe_s3_endpoint_url: str | None = None          # LocalStack / MinIO for local runs
    fe_execution: Literal["inprocess", "worker"] = "inprocess"
    fe_orchestrator: Literal["manual", "langgraph"] = "manual"
    fe_lg_checkpoint_schema: str = "form_rationalization_anh"
    fe_tools_mcp: Literal["stdio", "http"] = "stdio"
    #: MERGE (Plan v3 C): which approved Global documents feed the KB refresh, and how
    #: the STAGING -> ACTIVE promote happens (`manual` = recorded here, flipped in the KB
    #: tool; `api` = ask the KB gateway).
    fe_merge_types: str = "prd,frd,sdd,srd"
    fe_tools_http_url: str | None = None
    fe_worker_id: str | None = None                 # defaults to hostname:pid in the worker
    fe_worker_poll_seconds: float = 2.0
    fe_worker_heartbeat_seconds: float = 15.0
    fe_worker_workspace_filter: str | None = None   # e.g. "test-nk-v1-uw" — only claim runs whose workspace_id starts with this prefix

    # --- model and limits (PRD FR-020) -------------------------------------
    # Default tier for every stage that does not override `model:` in its
    # pipeline YAML. Sonnet 4.6 is what config.json ships and what the plugin
    # sub-agents pin; the previous code default (Opus 5) meant any process that
    # started without config.json ran the whole pipeline at ~1.7x the price.
    fe_model: str = "claude-sonnet-4-6"
    # Escalation tier for stages that name `model: high` (architecture
    # synthesis, large refactors). Kept explicit so cost is a per-stage choice.
    fe_model_high: str = "claude-opus-5"
    fe_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    fe_max_turns: int = 120
    fe_timeout_seconds: int = 3600
    fe_max_retries: int = 0
    # Budget guards. Both are post-run checks on the harness' own usage report;
    # 0 disables. A stage over its token budget is failed (its artefact is not
    # trusted -- it usually means the agent looped); a workspace over its cost
    # budget refuses to start further stages until an operator raises it.
    fe_max_tokens_per_stage: int = 0
    fe_max_cost_per_workspace_usd: float = 0.0

    # --- LLM call parameters -----------------------------------------------
    # Applied to every Bedrock (and other runner) LLM call.
    # temperature=0 → deterministic output (best for document generation).
    # fe_llm_max_tokens: max output tokens per LLM call; Bedrock Sonnet 4.6
    # supports up to 64,000 output tokens.
    # fe_llm_read_timeout: boto3 read timeout in seconds; the default of 60s
    # is too short for large documents (58K+ input tokens); 600s = 10 minutes.
    fe_llm_temperature: float = 0.0
    fe_llm_max_tokens: int = 8000
    fe_llm_read_timeout: int = 600

    # --- chunking parameters -----------------------------------------------
    # Default chunk size (chars) and overlap for artifact types that have no
    # per-type override in chunker.PER_ARTIFACT. Titan v2 limit is 40,000 chars.
    # PER_ARTIFACT overrides in chunker.py still take precedence over these.
    fe_chunk_size: int = 6000
    fe_chunk_overlap: int = 500

    # --- batch processing and artifact reading limits ----------------------
    # FE_ARTIFACT_READ_MAX_CHARS applies to global/architecture tiers only when
    # FE_BATCH_ENABLED=false. Mini tier always uses the original 30K limit.
    # See batch-processing-analysis.md for detailed rationale.
    fe_batch_enabled: bool = False
    fe_batch_threshold: int = 150_000
    fe_batch_size: int = 80_000
    fe_batch_merge_strategy: Literal["sequential", "consolidate", "hierarchical"] = "sequential"
    fe_artifact_read_max_chars: int = 200_000

    def get_artifact_read_limit(self, pipeline_tier: str) -> int:
        """Return the appropriate artifact read limit based on pipeline tier.

        Mini tier always gets 30K (preserve existing behavior).
        Global/architecture tiers get FE_ARTIFACT_READ_MAX_CHARS (200K default).

        This ensures backward compatibility and safe isolation between tiers.

        Args:
            pipeline_tier: "global", "mini", or "architecture"

        Returns:
            Character limit for artifact reading
        """
        if pipeline_tier == "mini":
            return 30_000  # original hardcoded limit, never changed
        return self.fe_artifact_read_max_chars

    def resolve_model(self, model: str | None, stage_key: str | None = None) -> str:
        """Map a stage's `model:` to a concrete id.

        Accepts the logical tiers `standard` / `high` (so pipelines do not hard-code
        ids), or any concrete model id, or None for the default.

        Per-stage overrides can be set via env vars (or config.json entries):
          FE_MODEL_SDD=claude-haiku-4-5   overrides only the SDD stage
          FE_MODEL_ADR=claude-haiku-4-5   overrides only the ADR stage
          (key is upper-cased, hyphens → underscores; e.g. epic-set → EPIC_SET)
        Stage overrides take precedence over `model: high` in the pipeline YAML.
        """
        import os
        if stage_key:
            env_key = f"FE_MODEL_{stage_key.upper().replace('-', '_')}"
            override = os.environ.get(env_key, "").strip()
            if override:
                return override
        if not model:
            return self.fe_model
        low = model.strip().lower()
        if low in ("standard", "default", "sonnet"):
            return self.fe_model
        if low in ("high", "opus"):
            return self.fe_model_high
        return model

    @staticmethod
    def effort_for_model(effort: str | None, model: str) -> str | None:
        """Clamp an effort level to what the model family supports.

        `xhigh` exists on Opus 4.7+/Sonnet 5/Fable; Sonnet 4.6 and Opus 4.6 accept
        low/medium/high/max only. Passing an unsupported level fails the request,
        which is a worse outcome than running one notch lower.
        """
        if not effort:
            return effort
        m = model.lower()
        no_xhigh = ("sonnet-4-6" in m or "opus-4-6" in m or "haiku" in m)
        if effort == "xhigh" and no_xhigh:
            return "high"
        return effort

    # --- authentication (PRD FR-P1, FR-017) --------------------------------
    # Disabled is a development convenience only. It is warned at startup and
    # reported by /health/deep, so it cannot be mistaken for working auth the way
    # the knowledge base's silent mock-admin fallback can.
    fe_auth_enabled: bool = False
    fe_auth_issuer: str | None = None
    fe_auth_audience: str | None = None
    fe_auth_jwks_uri: str | None = None

    # --- control-plane database --------------------------------------------
    # Empty means the JSON file store, which is the default and needs no server.
    # Set to use Postgres instead -- the SAME instance as the knowledge base, in a
    # SEPARATE schema, so pipeline state and KB state stay independently restorable.
    # FE_DB_URL is a standard Postgres connection string kept in .env / Vault (never
    # committed); when it is empty the store builds one from the Vault PG creds.
    fe_db_url: str | None = None
    fe_db_schema: str = "fe"

    # --- RED code graph -----------------------------------------------------
    # The Postgres that holds lmod's `red_file_analyses` (per-file RED output) and its
    # `graph_nodes` / `graph_edges` code graph. The red_graph builder derives page-to-page
    # edges from the analyses and writes them into those graph tables; the /red-graph API
    # traverses them. Local dev: postgresql://lmod:password@localhost:5435/lmod. Empty
    # falls back to FE_DB_URL (same instance, different schema).
    red_db_url: str | None = None
    red_db_schema: str = "lmod"

    # --- plugins -----------------------------------------------------------
    # The vendored copy under `src/api/plugins/`, kept in step with genlite by
    # `src/api/scripts/sync-plugins.py`. Defaulting to a sibling checkout made the
    # service unrunnable anywhere that checkout was not at that exact path.
    genlite_plugin_root: Path = API_ROOT / "plugins"
    fe_verify_plugins: bool = True

    # --- pipeline ----------------------------------------------------------
    fe_pipeline_dir: Path = API_ROOT / "pipelines"

    # Two tiers (PRD 5.3). `fe_pipeline` is the default when no tier is given and
    # points at the Global tier, because that is where a programme starts.
    fe_pipeline: str = "uw-cr-global"
    fe_pipeline_global: str = "uw-cr-global"
    fe_pipeline_architecture: str = "uw-cr-architecture"
    fe_pipeline_mini: str = "uw-cr-mini"
    fe_pipeline_assembler: str = "uw-cr-epic-assembler"
    fe_datasources_file: Path = SERVICE_ROOT / "datasources.yaml"

    # --- workspaces and generated output (PRD 5.1) -------------------------
    fe_workspace_root: Path = SRC_ROOT / "workspaces"
    fe_generated_root: Path = SERVICE_ROOT / "generated"

    # --- local target repos (optional) ------------------------------------
    # When set, code-generation and testing stages write directly into the
    # developer's checked-out application repos rather than the generic
    # generated/ folder.  Leave blank to fall back to generated/<target>/.
    #
    #   FE_UI_TARGET_REPO   — root of the local Angular / UI project repo
    #   FE_API_TARGET_REPO  — root of the local Spring Boot / API project repo
    #
    # Each value must be an absolute path to a directory that already exists
    # (the repo should be cloned and have its dependencies installed so the
    # testing stages can run `ng test`, `mvn test`, `playwright` etc.).
    fe_ui_target_repo: Path | None = None
    fe_api_target_repo: Path | None = None

    @field_validator(
        "fe_pipeline_dir", "fe_workspace_root", "fe_generated_root",
        "fe_datasources_file", "genlite_plugin_root", "fe_artifact_root", mode="after",
    )
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (SERVICE_ROOT / v).resolve()

    @field_validator("fe_ui_target_repo", "fe_api_target_repo", mode="after")
    @classmethod
    def _absolute_optional(cls, v: Path | None) -> Path | None:
        if v is None:
            return None
        return v if v.is_absolute() else (SERVICE_ROOT / v).resolve()

    # --- derived paths -----------------------------------------------------
    def stage_target_dir(
        self,
        target: str,
        *,
        workspace_id: str | None = None,
        epic_id: str | None = None,
    ) -> Path | None:
        """Return the write-root for a code-generation stage's `target` type.

        Priority:
          1. If the caller has configured a local repo for this target type
             (``FE_UI_TARGET_REPO`` / ``FE_API_TARGET_REPO``), return that.
             Code is written directly into the developer's checkout so tests
             can compile and run immediately.
          2. Otherwise fall back to the per-epic sub-directory inside
             ``generated/``, the original behaviour.

        Returns ``None`` when ``target`` is ``"none"`` (no additional dir).
        """
        if target == "none":
            return None
        if target == "ui" and self.fe_ui_target_repo:
            return self.fe_ui_target_repo
        if target in ("api", "tests") and self.fe_api_target_repo:
            # Tests live alongside the API source in the same repo.
            return self.fe_api_target_repo
        return self.generated_dir(target, workspace_id=workspace_id, epic_id=epic_id)

    def run_workspace(
        self,
        application_id: str,
        run_id: str,
        *,
        stage_key: str = "",
        workspace_id: str = "",
    ) -> Path:
        """PRD FR-017: an isolated worktree per run.

        Folder layout: {epic_suffix}--{stage_key}--{run_id}
        where epic_suffix is extracted from workspace_id when it contains '--epic-'
        (e.g. 'test-nk-ev2--epic-3' → 'epic-3').  Global-tier runs omit the prefix.
        """
        epic_suffix = workspace_id.split("--epic-")[-1] if "--epic-" in workspace_id else ""
        parts = [p for p in (epic_suffix, stage_key, run_id) if p]
        folder = "--".join(parts)
        return self.fe_workspace_root / _safe(application_id) / _safe(folder)

    # --- card / graph knowledge base (fe_core.kb.cards) --------------------
    # One card KB per application: workspaces/{application_id}/kb/. Built by the
    # `re-kb-build` pipeline (AIDLC-kb skill, or any compatible RE engine dropped
    # into src/api/plugins), read by the kb_card / graph_neighbors tools, the
    # bounded ReAct builtin and the post-run grounding check. FE_KB_ROOT
    # overrides the location for every application (single shared KB).
    fe_kb_root: Path | None = None

    def kb_root_for(self, application_id: str) -> Path:
        if self.fe_kb_root is not None:
            root = self.fe_kb_root
            return root if root.is_absolute() else (SERVICE_ROOT / root).resolve()
        return self.fe_workspace_root / _safe(application_id) / "kb"

    # Raw corpus the RE pipeline reads (uploads are kept here by kind):
    # workspaces/{application_id}/corpus/. FE_CORPUS_ROOT overrides for all apps.
    fe_corpus_root: Path | None = None

    def corpus_root_for(self, application_id: str) -> Path:
        if self.fe_corpus_root is not None:
            root = self.fe_corpus_root
            return root if root.is_absolute() else (SERVICE_ROOT / root).resolve()
        return self.fe_workspace_root / _safe(application_id) / "corpus"

    # --- bounded ReAct builtin (structured stages) --------------------------
    # Runs on Bedrock through fe_core.rag.bedrock (already the RAG answer path),
    # not through Claude Code: a bounded reason -> tool -> observe loop with
    # in-process KB tools and an id whitelist. Empty model = the RAG answer model.
    fe_react_model: str = ""
    fe_react_max_steps: int = 24
    fe_react_max_tokens: int = 8000

    # --- KB intake: LLM fallback -------------------------------------------
    # The module JSON and the screens Word document are parsed by procedural
    # code. When that code cannot read an upload (an unknown JSON layout, a
    # picture no file-name line claims), a model is asked for a *mapping* that
    # the same code then applies and validates (fe_core.kb.llm_fallback). Set
    # FE_KB_LLM_FALLBACK=false to refuse such uploads instead. Empty model =
    # the RAG answer model.
    fe_kb_llm_fallback: bool = True
    fe_kb_llm_fallback_model: str = ""

    def generated_dir(self, target: str, *, workspace_id: str | None = None,
                      epic_id: str | None = None) -> Path:
        """PRD 5.1: generated/{ui,api,db,policies,tests,docs}.

        Per-EPIC when a Mini Workspace is in play (FR-P5). Ten EPICs generating
        UI components into one shared `generated/ui` would overwrite each other,
        and the loss would look like a bad generation rather than a collision.
        Global-tier output keeps the flat layout, since there is only ever one.
        """
        base = self.fe_generated_root / _safe(target)
        if epic_id:
            return base / "epics" / _safe(epic_id)
        # Global and the Architecture Workspace are singletons — flat layout.
        singleton = str(workspace_id or "").endswith(("--global", "--architecture"))
        if workspace_id and not singleton:
            return base / "workspaces" / _safe(workspace_id)
        return base

    def workspace_dir(self, workspace_id: str) -> Path:
        """Root for one workspace's own scratch state."""
        return self.fe_workspace_root / _safe(workspace_id)

    def kb_mcp_launch(self) -> dict[str, Any]:
        """Launch spec for the knowledge base's stdio KB MCP server.

        Run from lmod_AIG/ (the parent of backend/) so the `backend.` package
        resolves for `python -m backend.mcp.kb_server`.
        """
        cwd = str(Path(self.kb_backend_root).parent) if self.kb_backend_root else None
        return {
            "command": sys.executable,
            "args": ["-m", self.kb_mcp_module],
            "cwd": cwd,
        }

    def uses_database(self) -> bool:
        return bool(self.fe_db_url and self.fe_db_url.strip())

    def agent_grants_mcp(self) -> bool:
        return self.fe_kb_agent_tools == "mcp"

    def redacted(self) -> dict:
        """Config for /health and logs. No datasource secrets live here at all,
        because datasources.yaml references them by env-var name only."""
        return self.model_dump(mode="json")


def _safe(component: str) -> str:
    """Keep an id from escaping the workspace root."""
    cleaned = "".join(c for c in str(component) if c.isalnum() or c in "-_.")
    return cleaned or "unknown"


@lru_cache
def get_settings() -> Settings:
    """Settings resolved from: process env > .env > src/api/config/config.json > defaults."""
    apply_config_json()
    return Settings()

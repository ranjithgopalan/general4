"""
Application settings for the AIG Core AIDLC Platform — Agents (FE backend).

Loaded from environment variables (Pydantic v2 ``BaseSettings``). The environment is
populated by ``app/config/__init__.py`` in a fixed precedence (highest wins):

    OS env  >  .env  >  config.json  >  field defaults

SECRETS ARE NEVER STORED HERE OR IN config.json. All credentials/tokens are
Vault-managed; settings hold only Vault *names* (appcode + vault name), non-secret
endpoints, flags, and the (config-driven) model registry.

House decisions (see docs/04-fe-architecture.md):
  * DB driver = psycopg 3 async pool + pgvector (NOT DynamoDB, NOT SQLAlchemy).
  * Okta OIDC + Entitlement API + HashiCorp Vault — real in dev, no stub.
  * Model ids come from the registry (config), never hardcoded.
  * One GEAR ID per KB: japan.
"""

import json
from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration. One instance, cached via ``get_settings()``."""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    # ── General ────────────────────────────────────────────────────────────────
    ENV: str = Field(default="dev", description="Environment name (dev/uat/prod)")
    DEBUG: bool = Field(default=False, description="Debug mode flag")
    APPLICATION_NAME: str = Field(default="AIG Core AIDLC Platform — Agents", description="Application name")
    APP_VERSION: str = Field(default="0.1.0", description="Application version (telemetry)")
    GEAR_ID: str = Field(default="japan", description="LOB scope — one KB per GEAR ID")
    # KB-refresh round-trip (docs/23) — the MERGE stage's background service.
    # The KB base is sourced from the DB SSOT via `kb-indexer export` (NOT a local kb/ folder), so the
    # FE can run centrally (Fargate) where no source-tree kb/ exists. Only an ephemeral scratch dir is used.
    KB_INDEXER_DIR: str = Field(
        default="",
        description="Path to the central kb-indexer package (KbRefreshService invokes its CLI in a "
        "subprocess to export the KB base + load the merged version). Required in the central deployment.",
    )
    KB_INDEXER_PYTHON: str = Field(
        default="",
        description="Interpreter for the kb-indexer CLI (default: its own .venv, then this process').",
    )
    KB_SYNC_WORK_DIR: str = Field(
        default="",
        description="Ephemeral scratch dir for export+merge (default: <system temp>/aidlc-kb-refresh).",
    )
    PORT: int = Field(default=8080, description="FastAPI listen port")
    WORKERS: int = Field(default=4, description="Uvicorn worker processes")
    LOCAL_RUN: bool = Field(default=True, description="Running locally (dev convenience)")

    # ── CORS ─────────────────────────────────────────────────────────────────────
    CORS_ORIGINS_STR: str = Field(default="*", description="Allowed CORS origins (comma-separated)")

    @property
    def CORS_ORIGINS(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS_STR.split(",") if o and o.strip()]

    # ── Logging ──────────────────────────────────────────────────────────────────
    ENABLE_FILE_LOGGING: bool = Field(default=True, description="Write rotating logs to LOG_FILE_PATH")
    LOG_FILE_PATH: str = Field(default="logs/app.log", description="Rotating log file path")
    LOG_FILE_MAX_BYTES: int = Field(default=10485760, description="Max bytes per log file before rotation")
    LOG_FILE_BACKUP_COUNT: int = Field(default=5, description="Rotated log files to keep")

    # ── HashiCorp Vault (secrets — names only here) ───────────────────────────────
    HASHICORP_API_URL: str = Field(default="", description="Vault HTTP API endpoint")
    HASHICORP_API_TIMEOUT: int = Field(default=30, description="Vault API timeout (s)")
    HASHICORP_AUTH_TOKEN: str = Field(default="", description="Optional Bearer token for the Vault API")
    HASHICORP_VAULT_REGION: str = Field(default="", description="Vault region (falls back to ENV when empty)")
    VAULT_APP_CODE: str = Field(default="aidlc", description="Default Vault application code (appcode)")
    VAULT_CRED_CACHE_TTL: int = Field(default=3600, description="In-memory Vault credential cache TTL (s)")
    PG_VAULT_NAME: str = Field(default="pg_db_vault", description="Vault name for Postgres creds (keys PG_DB_USER/PG_DB_PWD)")
    PG_DB_VAULT_APP_CODE: str = Field(default="ah", description="Vault appcode for the Postgres creds (overrides VAULT_APP_CODE for PG only)")
    NEO4J_VAULT_NAME: str = Field(default="japan_neo4j_creds", description="Vault name for Neo4j creds (v2, deferred)")
    S3_VAULT_NAME: str = Field(default="japan_s3_role", description="Vault name for the S3 role")
    BEDROCK_VAULT_NAME: str = Field(default="japan_bedrock_key", description="Vault name for the Bedrock key")
    API_KEY_VAULT_NAME: str = Field(default="", description="Vault name holding the X-Platform-Api-Key HMAC secret")

    # ── Okta / JWT ────────────────────────────────────────────────────────────────
    JWT_VALIDATION_ENABLED: bool = Field(default=False, description="Validate Okta JWTs")
    JWT_ALGORITHM: str = Field(default="RS256", description="JWT signing algorithm")
    OKTA_JWKS_URL: str = Field(default="", description="Okta JWKS URL for token validation")
    OKTA_ISSUER: str = Field(default="", description="Expected Okta token issuer")
    OKTA_AUDIENCE: str = Field(default="", description="Expected Okta token audience")
    JWT_DEV_BYPASS: bool = Field(default=True, description="Dev-only: accept a stub principal without a real JWT")

    # ── Entitlement API ────────────────────────────────────────────────────────────
    ENTITLEMENT_ENABLED: bool = Field(default=False, description="Enforce entitlement checks (disable for dev/test)")
    ENTITLEMENT_API_URL: str = Field(default="", description="Entitlement API base URL")
    ENTITLEMENT_API_TIMEOUT: int = Field(default=10, description="Entitlement API timeout (s)")
    ENTITLEMENT_CACHE_TTL: int = Field(default=300, description="Entitlement cache TTL (s)")
    ENTITLEMENT_APPLICATION_NAME: str = Field(default="aidlc-platform", description="App name for entitlement requests")
    ENTITLEMENT_USER_AGENT: str = Field(
        default="aidlc-platform-agents", description="User-Agent for the entitlement API"
    )

    # ── Custom API-key header (service auth atop Okta; HMAC-signed) ────────────────
    API_KEY_ENABLED: bool = Field(default=False, description="Require the custom signed API-key header")
    API_KEY_HEADER_NAME: str = Field(default="X-Platform-Api-Key", description="Custom API-key header name")
    API_KEY_SIGNATURE_HEADER: str = Field(default="X-Platform-Signature", description="HMAC signature header name")
    API_KEY_CLOCK_SKEW_SECONDS: int = Field(default=300, description="Allowed signature timestamp skew (s)")

    # ── Postgres + pgvector (psycopg 3 async pool — sole relational + vector store) ─
    PGHOST: str = Field(default="", description="Postgres host")
    PGPORT: int = Field(default=5432, description="Postgres port")
    PGDATABASE: str = Field(default="sdlcaiassist", description="Postgres database name")
    PG_SSLMODE: str = Field(default="require", description="libpq sslmode — RDS requires SSL (require|verify-full)")
    PG_SCHEMA: str = Field(
        default="form_rationalization_anh", description="Postgres schema for all AIDLC tables (set as search_path per connection)"
    )
    PG_POOL_MIN_SIZE: int = Field(default=1, description="psycopg async pool min connections")
    PG_POOL_MAX_SIZE: int = Field(default=10, description="psycopg async pool max connections")
    PG_POOL_TIMEOUT: float = Field(default=10.0, description="Seconds to wait for a pooled connection")
    PG_CONNECT_TIMEOUT: int = Field(default=10, description="Postgres connect timeout (s)")
    PG_STATEMENT_TIMEOUT_MS: int = Field(default=30000, description="Per-statement timeout (ms); 0 = disabled")
    # Local machines cannot reach the Vault endpoint or the RDS cluster. With PG_CONNECTION_MODE=local every
    # Postgres consumer (async pool, fe_core store, KB publish, RED graph) uses PG_LOCAL_URL verbatim and
    # skips Vault. Set both in app/.env; config.json keeps the Vault defaults.
    PG_CONNECTION_MODE: str = Field(
        default="vault", description="vault = creds from HashiCorp (default) | local = use PG_LOCAL_URL as-is"
    )
    PG_LOCAL_URL: str = Field(
        default="", description="Full Postgres URL used when PG_CONNECTION_MODE=local, e.g. postgresql://user:pwd@localhost:5435/db"
    )

    @property
    def pg_local(self) -> bool:
        return self.PG_CONNECTION_MODE.strip().lower() == "local" and bool(self.PG_LOCAL_URL.strip())
    EMBED_DIM: int = Field(
        default=1024, description="pgvector embedding dimension (1024 Titan v2 / 1536 v1) — never hardcode"
    )

    # ── Retrieval (hybrid RAG — docs/20 §8) ───────────────────────────────────────
    RETRIEVAL_TOP_K: int = Field(default=8, description="Cards returned to synthesis after RRF fusion")
    CHAT_RETRIEVAL_TOP_K: int = Field(
        default=16,
        description="Cards returned for the CHATBOT path (larger than RETRIEVAL_TOP_K so answers are "
        "comprehensive/elaborate — the chat assistant favours recall + graph-neighborhood context).",
    )
    RETRIEVAL_DOMAIN_BOOST: float = Field(
        default=0.5,
        description="Intent-aware boost: multiplicative score lift (×(1+boost)) applied to fused "
        "candidates whose family matches the classified question domains (0 = disable).",
    )
    # Workspace ANALYSIS (epic → impact): the seed must be RECALL-biased + read ALL kinds — a business
    # epic can impact code (API/CMP), and a missed seed = a missed break (whole affected subtree lost).
    # Persona still gates FE writes; this only widens the impact-analysis READ. (docs/26 impact priority.)
    ANALYSIS_SEED_ALL_KINDS: bool = Field(
        default=True, description="Impact-analysis seed retrieval reads all kinds (code incl.), not persona-scoped."
    )
    ANALYSIS_SEED_TOP_K: int = Field(
        default=16,
        description="Seed candidates retrieved for impact analysis (recall-biased; reduced from 24 for performance).",
    )
    ANALYSIS_PRIMARY_CAP: int = Field(
        default=10, description="Primary matched cards that seed the graph impact walk (substantive change)."
    )
    ANALYSIS_PRIMARY_CAP_LIGHT: int = Field(
        default=5, description="Primary cap for a light/small change (fewer seeds)."
    )
    ANALYSIS_DISABLE_LIGHT_WALK: bool = Field(
        default=True,
        description="Disable light-walk optimization: always use full graph walk regardless of change type (recommended: True for comprehensive analysis).",
    )
    RETRIEVAL_RRF_K: int = Field(default=60, description="Reciprocal-rank-fusion constant k")
    RETRIEVAL_LANE_CANDIDATES: int = Field(
        default=40, description="Per-lane candidate pool size fetched before RRF fusion"
    )
    RETRIEVAL_HYBRID_DEFAULT: bool = Field(
        default=True, description="Default to graph+dense+BM25 hybrid (false = graph+BM25 only, no embed call)"
    )

    # ── Grounding gate (serve-time re_anchor — docs/20 §10) ───────────────────────
    GROUNDING_REQUIRE_VERIFIED: bool = Field(
        default=False,
        description="Strict: require a VERIFIED re_anchor verdict on cited evidence (lenient default blocks only FABRICATED)",
    )
    GROUNDING_STRICT_NUMERIC: bool = Field(
        default=False,
        description="Strict: an ungrounded number in the answer is a hard BLOCK (lenient default flags + downgrades)",
    )

    # ── Chatbot endpoint / security / session (docs/20 §4, §12, §13) ──────────────
    GUARDRAILS_ENABLED: bool = Field(
        default=True, description="Deterministic input guardrails (injection/exfil/PII-fishing)"
    )
    QUERY_PII_MASK_ENABLED: bool = Field(
        default=True, description="Mask obvious PII in the inbound query before LLM/log"
    )
    RERANK_ENABLED: bool = Field(
        default=False, description="Cross-encoder rerank of fused candidates (Bedrock Rerank); off until eval warrants"
    )
    RERANK_TOP_N: int = Field(default=8, description="Candidates kept after rerank")
    CHAT_HISTORY_MAX_TURNS: int = Field(default=10, description="Prior turns threaded into a multi-turn chat")
    FOLLOWUP_RESOLVER_ENABLED: bool = Field(
        default=True, description="Rewrite elliptical follow-ups into standalone queries using session history"
    )
    INTAKE_CLASSIFY_PROMPT: str = Field(
        default='You are a deterministic intent classifier for the {gear} knowledge base chatbot.\nReturn ONLY a valid JSON object — no markdown, no explanation, no extra text.\n\nJSON schema (all fields required):\n{\n  "route": "DIRECT" | "ANSWER",\n  "intent": "greeting" | "meta" | "out_of_scope" | "answerable" | "needs_clarify",\n  "needs_clarify": true | false,\n  "domains": [zero or more of: "code", "architecture", "systems", "process", "workflow", "rules", "screens", "data", "support"]\n}\n\nRules:\n- route=DIRECT / intent=greeting     — pure social exchange with no KB question ("hi", "thanks", "bye")\n- route=DIRECT / intent=meta         — question about chatbot capabilities ("what can you do?", "what\'s in the KB?")\n- route=DIRECT / intent=out_of_scope — clearly outside the {gear} knowledge base (weather, sports, general coding help)\n- route=ANSWER / intent=answerable   — substantive question about {gear} KB content\n- route=ANSWER / intent=needs_clarify — too vague to answer without clarification; set needs_clarify=true\n\nKey rule: a message that starts with a greeting word but also contains a substantive question is route=ANSWER / intent=answerable — never route=DIRECT.\n\nOutput format: your entire response must be the raw JSON object only. Begin with { and end with }. Do not use markdown code fences or backticks.',
        description=(
            "System prompt for the LLM intake classifier (Tier 2, docs/20 §7). "
            "Defined in config.json; use {gear} as a placeholder for the LOB name. "
            "Empty string disables the LLM tier (regex-only fallback)."
        ),
    )

    # ── AWS / Bedrock (LLM — used by later business milestones) ────────────────────
    AWS_REGION: str = Field(default="us-east-1", description="AWS region for Bedrock/S3")
    AWS_PROFILE: str | None = Field(default=None, description="AWS profile (local dev only)")
    S3_BUCKET_NAME: str = Field(default="", description="S3 bucket for FE artifacts (empty = S3 off → DB only)")
    FE_S3_BUCKET: str = Field(default="", description="S3 bucket for RE Graph audit trail (loaded from config.json)")
    S3_ARTIFACT_PREFIX: str = Field(default="japan/workspaces", description="S3 key prefix for workspace artifacts")
    BEDROCK_MAX_ATTEMPTS: int = Field(default=3, description="Bedrock retry attempts")
    BEDROCK_RETRY_MODE: str = Field(default="adaptive", description="Bedrock retry mode")
    BEDROCK_CONNECT_TIMEOUT: int = Field(default=10, description="Bedrock connect timeout (s)")
    BEDROCK_READ_TIMEOUT: int = Field(default=60, description="Bedrock read timeout (s)")
    BEDROCK_LLM_DEFAULT_TEMPERATURE: float = Field(
        default=0.0, description="Default LLM temperature (0 = deterministic)"
    )
    BEDROCK_LLM_DEFAULT_MAX_TOKENS: int = Field(default=60000, description="Default LLM max output tokens")
    BEDROCK_MAX_POOL_CONNECTIONS: int = Field(default=10, description="botocore max pool connections")
    BEDROCK_DISABLE_STREAMING: str = Field(
        default="tool_calling",
        description=(
            "ChatBedrockConverse disable_streaming policy: 'tool_calling' streams plain-text answers "
            "(chatbot word-by-word) but buffers when tools are bound (ReAct — avoids tool-call breakage); "
            "'true'/'false' force off/on. langchain_aws defaults to True (no streaming)."
        ),
    )
    LLM_PROVIDER: str = Field(
        default="bedrock",
        description="LLM backend: 'bedrock' (real, the default in every env) | 'fake' (test double only)",
    )

    # ── Multi-model registry (config-driven — NO hardcoded model ids) ──────────────
    MODEL_REGISTRY_JSON: str = Field(
        default="{}", description="JSON map: logical name -> Bedrock model/inference-profile id"
    )
    MODEL_ROUTING_JSON: str = Field(default="{}", description="JSON map: task -> logical model name")
    MODEL_DEFAULT: str = Field(default="", description="Logical model used when a task has no explicit route")
    MODEL_PRICING_JSON: str = Field(
        default="{}",
        description="JSON map: Bedrock model id -> {input_per_1k_tokens, output_per_1k_tokens} for cost analytics",
    )
    LLM_COST_CURRENCY: str = Field(default="USD", description="Currency label for estimated LLM costs")
    TASK_MAX_TOKENS_JSON: str = Field(
        default="{}",
        description="JSON map: task -> max_tokens override; takes precedence over BEDROCK_LLM_DEFAULT_MAX_TOKENS for that task. Defined in config.json.",
    )

    @property
    def TASK_MAX_TOKENS(self) -> dict[str, int]:
        """Per-task max_tokens overrides — task name → token limit."""
        result: dict[str, int] = {}
        for k, v in self._parse_json_map("TASK_MAX_TOKENS_JSON").items():
            try:
                result[str(k)] = int(v)
            except (TypeError, ValueError):
                pass
        return result

    @property
    def MODEL_REGISTRY(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self._parse_json_map("MODEL_REGISTRY_JSON").items()}

    @property
    def MODEL_ROUTING(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self._parse_json_map("MODEL_ROUTING_JSON").items()}

    @property
    def MODEL_PRICING(self) -> dict[str, dict[str, float]]:
        """Return model-id keyed token pricing for estimated LLM cost analytics."""
        pricing: dict[str, dict[str, float]] = {}
        for model_id, value in self._parse_json_map("MODEL_PRICING_JSON").items():
            if not isinstance(value, dict):
                continue
            try:
                pricing[str(model_id)] = {
                    "input_per_1k_tokens": float(value.get("input_per_1k_tokens", 0.0) or 0.0),
                    "output_per_1k_tokens": float(value.get("output_per_1k_tokens", 0.0) or 0.0),
                }
            except (TypeError, ValueError):
                continue
        return pricing

    def model_id_for(self, task: str) -> str | None:
        """Resolve a Bedrock model id for a task via routing -> registry (config-driven)."""
        logical = self.MODEL_ROUTING.get(task) or self.MODEL_DEFAULT
        return self.MODEL_REGISTRY.get(logical) if logical else None

    # ── Graph runtime ──────────────────────────────────────────────────────────────
    RE_GRAPH_BASE_URL: str = Field(
        default="http://localhost:9090",
        description="RE Graph HTTP service base URL (branch core-aidlc-11)",
    )
    RE_RESPONSE_PARSE: str = Field(
        default="Deterministic",
        description="RE Graph extraction mode: 'Deterministic' (Python parser, preserves all BR/CMP/ENT/SCR/ROLE) | 'LLM' (Bedrock extraction with 300K char truncation)",
    )
    GRAPH_PROVIDER: str = Field(
        default="postgres", description="GraphProvider backend: 'postgres' (v1) | 'neo4j' (v2, deferred)"
    )
    GRAPH_MAX_NODES: int = Field(default=400, description="Max nodes returned by /re/graph/overview")
    FE_REACT_RECURSION_LIMIT: int = Field(
        default=24,
        description="Bounded step budget for the FE ANALYSIS OPEN ReAct loop (docs/04 §2). 12 was too "
        "low for large impact sets — the agent exhausted its steps before emitting the enrichment JSON.",
    )
    STORIES_REACT_RECURSION_LIMIT: int = Field(
        default=6,
        description="Bounded step budget for the STORIES stage ReAct loop (faster, context-light). "
        "4 forces quick decisions from the deterministic context; if empty stories result, retry-with-merge fires.",
    )
    GRAPH_FIXTURE_PATH: str = Field(
        default="",
        description="Override path to the dev fixture graph (empty = packaged app/fixtures/graph.sample.json)",
    )
    GRAPH_FIXTURE_VERSION: str = Field(
        default="fixture-dev",
        description="kb_version label reported when serving the dev fixture (no ACTIVE version yet)",
    )
    GRAPH_SEED_CANDIDATES: int = Field(
        default=40,
        description="Max seed nodes returned by the graph lane — aligned with RETRIEVAL_LANE_CANDIDATES so all three lanes contribute equally to RRF",
    )
    GRAPH_SHORT_TERM_ALLOWLIST: str = Field(
        default="AU,AUW,WF,WD,JP,ID,BR,FR,SYS,INT,CMP,API",
        description="Comma-separated tokens of ≤2 chars preserved by the graph tokenizer; prevents dropping Japan-specific acronyms",
    )
    GRAPH_EXPANSION_ENABLED: bool = Field(
        default=True,
        description="BFS neighbour expansion from seed nodes via structural edges; enable once a real KB is ACTIVE",
    )
    GRAPH_EXPANSION_DEPTH: int = Field(
        default=2,
        description="Max BFS hops from seed nodes during graph expansion",
    )
    GRAPH_EXPANSION_EDGE_LABELS: str = Field(
        default="GOVERNED_BY,FEEDS_INTO,TRIGGERS,IMPLEMENTS,VALIDATES,CALLS,DEPENDS_ON,PRODUCES,SCREEN_OF,REFERENCES",
        description="Comma-separated edge labels followed during expansion; unlisted labels are skipped",
    )
    GRAPH_DOMAIN_VOCAB_JSON: str = Field(
        default="{}",
        description="JSON map: canonical term -> list of aliases injected into graph lane query before seed search; defined in config.json",
    )

    @property
    def GRAPH_DOMAIN_VOCAB(self) -> dict[str, list[str]]:
        """Domain vocabulary map for graph lane query expansion — canonical term → aliases."""
        raw = self._parse_json_map("GRAPH_DOMAIN_VOCAB_JSON")
        return {str(k): [str(a) for a in v] if isinstance(v, list) else [str(v)] for k, v in raw.items()}

    # ── Personas ───────────────────────────────────────────────────────────────────
    PERSONAS_PATH: str = Field(
        default="", description="Override path to personas.json (empty = packaged app/config/personas.json)"
    )
    PERSONA_GROUP_CHECK_ENABLED: bool = Field(
        default=False,
        description="Enforcement point #1: map the Okta group -> persona. Off (dev) = use DEV_DEFAULT_PERSONA.",
    )
    DEV_DEFAULT_PERSONA: str = Field(
        default="ba", description="Persona used when the group check is disabled or no group maps to one."
    )
    CHAT_RETRIEVAL_ALL_KINDS: bool = Field(
        default=True,
        description=(
            "Chatbot read scope: when True the KB assistant retrieves across ALL card kinds so it can "
            "answer any question (incl. API/CMP code) regardless of the caller's persona. The persona "
            "still governs FE artifact generation (writes) + the JA-SME/DISPUTED safeguards still apply. "
            "Set False to restrict chat reads to the caller persona's includeKinds."
        ),
    )
    DEV_DEFAULT_USER: str = Field(
        default="dev-user",
        description="User id used to scope chat sessions when the JWT is bypassed (dev/no-auth).",
    )

    # ── LOB / domain tokens (multi-LOB: NEVER hardcode these in core — read them here) ──
    # Every value defaults to a domain-neutral placeholder so the core works for any LOB; the
    # Japan-Auto specifics live in config.json (per-LOB), keeping the code LOB-agnostic.
    LOB_STORY_ID_PREFIX: str = Field(
        default="STR",
        description="Prefix for generated user-story IDs (e.g. 'STR-JAUTO'). Per-LOB; set in config.json.",
    )
    LOB_DEFAULT_ACTOR: str = Field(
        default="User",
        description="Fallback actor/persona for a user story when none is derivable from context.",
    )
    LOB_KNOWN_ROLES_JSON: str = Field(
        default="[]",
        description="JSON list of known role labels for this LOB (used to grade story grounding). config.json.",
    )
    LOB_SYSTEM_PARTICIPANTS_JSON: str = Field(
        default="[]",
        description="JSON list of fallback system participants for SRD sequence diagrams when none matched. config.json.",
    )

    @property
    def LOB_KNOWN_ROLES(self) -> list[str]:
        """Known role labels for this LOB (empty list when unset → no domain assumption)."""
        return [str(r) for r in self._parse_json_list("LOB_KNOWN_ROLES_JSON")]

    @property
    def LOB_SYSTEM_PARTICIPANTS(self) -> list[str]:
        """Fallback SRD sequence participants for this LOB (empty when unset → neutral placeholder)."""
        return [str(s) for s in self._parse_json_list("LOB_SYSTEM_PARTICIPANTS_JSON")]

    # ── Codegen (Developer stage) ─────────────────────────────────────────────────
    CODEBASE_ROOT: str = Field(
        default="input/Auto",
        description=(
            "Root path of the Japan Auto source corpus (TypeScript, Java, ESB, XDP). "
            "Codegen ReAct agent reads original files relative to this path. "
            "Default = Japan corpus in-repo; override in config.json or env for external repos."
        ),
    )
    CODEGEN_OUTPUT_ROOT: str = Field(
        default="outputs/codegen",
        description="Root path under which per-workspace codegen output dirs are created.",
    )
    DEV_REACT_MAX_ITERS: int = Field(
        default=6,
        description=(
            "Max reason→tool→observe iterations for the Developer dev-reasoning ReAct agent. "
            "Each iteration is one LLM call; lower = fewer calls / faster / cheaper. Was hardcoded 10."
        ),
    )
    CODEGEN_RECURSION_LIMIT: int = Field(
        default=15,
        description="Max ReAct iterations for the codegen agent (tool-use rounds).",
    )

    # ── FE Lifecycle — Handoff Mode (dev/QA offline) ─────────────────────────────
    FE_STORIES_HANDOFF_MODE_ENABLED: bool = Field(
        default=True,
        description=(
            "Stories handoff mode: when True, pause the workflow at STORIES state "
            "(do NOT auto-advance to DEVELOPMENT). Allows dev/QA to take generated "
            "artifacts offline for local development + testing. Dev/QA POST their "
            "results back via offline work endpoints (/ws/{id}/dev/submit, /ws/{id}/qa/submit). "
            "Set True for local dev, False for production (auto-advance enabled)."
        ),
    )

    # ── Traceability chain ───────────────────────────────────────────────────────
    TRACEABILITY_CHAIN_STAGES_STR: str = Field(
        default="prd,frd,srd,sdd,epic-set,feature,user-story,dev,test",
        description=(
            "Ordered AIDLC pipeline stages for the traceability chain, broadest→narrowest "
            "(comma-separated). Drives the Timeline tab ordering and Coverage Matrix columns."
        ),
    )

    @property
    def TRACEABILITY_CHAIN_STAGES(self) -> list[str]:
        """['prd','frd','srd','sdd','epic-set','feature','user-story','dev','test']"""
        return [s.strip() for s in self.TRACEABILITY_CHAIN_STAGES_STR.split(",") if s.strip()]

    TRACEABILITY_HOP_LINK_TYPES_JSON: str = Field(
        default=(
            '{"dev":"IMPLEMENTS","user-story":"TRACES_TO","feature":"TRACES_TO",'
            '"epic-set":"TRACES_TO","sdd":"DERIVES_FROM","srd":"DERIVES_FROM",'
            '"frd":"DERIVES_FROM"}'
        ),
        description=(
            "JSON map: from-stage → link_type for each traceability chain hop. "
            "IMPLEMENTS = code implements a requirement. "
            "TRACES_TO = structural parent relationship. "
            "DERIVES_FROM = document derived from upstream document."
        ),
    )

    @property
    def TRACEABILITY_HOP_LINK_TYPES(self) -> dict[str, str]:
        """{'dev':'IMPLEMENTS', 'user-story':'TRACES_TO', ...}"""
        return {
            str(k): str(v)
            for k, v in self._parse_json_map("TRACEABILITY_HOP_LINK_TYPES_JSON").items()
        }

    # ── Telemetry ────────────────────────────────────────────────────────────────
    TELEMETRY_ENABLED: bool = Field(default=True, description="Emit telemetry events")

    # ── Circuit breaker (external calls) ───────────────────────────────────────────
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=5, description="Failures before opening the breaker")
    CIRCUIT_BREAKER_TIMEOUT_DURATION: int = Field(default=30, description="Half-open recovery wait (s)")

    # ── Entitlement retry (F9) ─────────────────────────────────────────────────────
    ENTITLEMENT_MAX_RETRIES: int = Field(default=3, description="Max tenacity retries for transient entitlement errors")
    ENTITLEMENT_RETRY_BASE_DELAY: float = Field(default=1.0, description="Retry base delay (s)")

    # ── Context engine token budget (F5) ───────────────────────────────────────────
    FE_MAX_PROMPT_TOKENS: int = Field(default=200_000, description="Maximum total prompt tokens before context budget error")
    FE_CONTEXT_SAFETY_MARGIN: int = Field(default=5_000, description="Safety margin subtracted from model context window")
    MODEL_CONTEXT_WINDOWS_JSON: str = Field(
        default='{"claude-sonnet-4-6":200000,"claude-3-5-sonnet":200000,"claude-3-5-haiku":200000,"claude-3-opus":200000,"claude-haiku-4-5":200000}',
        description="JSON map: model id substring -> context window size in tokens",
    )

    @property
    def MODEL_CONTEXT_WINDOWS(self) -> dict[str, int]:
        """Model context window sizes — model id substring → token limit."""
        result: dict[str, int] = {}
        for k, v in self._parse_json_map("MODEL_CONTEXT_WINDOWS_JSON").items():
            try:
                result[str(k)] = int(v)
            except (ValueError, TypeError):
                pass
        return result

    # ── Service identity (telemetry) ───────────────────────────────────────────────
    SERVICE_NAME: str = Field(default="aidlc-agents", description="Service name emitted in telemetry")

    # ── helpers ──────────────────────────────────────────────────────────────────
    def _parse_json_map(self, field_name: str) -> dict[str, Any]:
        raw = getattr(self, field_name, None) or "{}"
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def _parse_json_list(self, field_name: str) -> list[Any]:
        raw = getattr(self, field_name, None) or "[]"
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide singleton Settings instance (cached)."""
    return Settings()


# Module-level singleton instance for backward compatibility
settings = get_settings()

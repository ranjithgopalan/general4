"""BedrockReActRunner — ECS-ready LLM runner using AWS Bedrock.

Uses LangGraph's create_react_agent (micro-level ReAct loop) together with
ChatBedrockConverse (IAM task-role auth, no Anthropic API key required).

On ECS:  FE_RUNNER=bedrock — boto3 picks up the task role automatically.
On laptop: FE_RUNNER=bedrock — uses ~/.aws/credentials / AWS_PROFILE.

This runner is intentionally thin:
  - It delegates reasoning to create_react_agent (LangGraph prebuilt).
  - It delegates LLM calls to ChatBedrockConverse (langchain-aws).
  - Tools are plain Python functions that call fe_core.rag directly
    (no MCP subprocess, no HTTP overhead — works identically on ECS and laptop).
  - Token tracking flows via AIMessage.usage_metadata → USAGE events
    → _common.fold_event() → RunResult.total_tokens.

Model aliases → Bedrock cross-region inference profiles:
  claude-sonnet-4-6  →  us.anthropic.claude-sonnet-4-6-20250514-v1:0
  claude-opus-5      →  us.anthropic.claude-opus-5-20250514-v1:0
  claude-sonnet-5    →  us.anthropic.claude-sonnet-5-20250514-v1:0
  claude-haiku-4-5   →  us.anthropic.claude-haiku-4-5-20251001-v1:0

Credential auto-refresh (laptop dev only):
  AWS SSO tokens expire after ~1 hour. Long stages like SDD (~54 min) can hit
  ExpiredTokenException mid-run. To avoid this, _make_refreshable_boto_session()
  reads credentials DIRECTLY from AWS_SHARED_CREDENTIALS_FILE (not from boto3's
  in-process cache) via configparser, with a 10-min TTL. When the TTL lapses
  boto3 calls _load() again which re-reads the file from disk.
  Update credentials_clean (watch_keys.py / refresh_keys.sh) when the SSO token
  expires; the running process picks up the new token within 10 minutes — no restart.

  On ECS the task role auto-refreshes via IMDSv2 — this code path is a no-op
  there because get_credentials() returns AssumeRoleWithWebIdentity credentials
  that boto3 already manages as RefreshableCredentials internally.
"""

from __future__ import annotations

import logging
import os
from asyncio import get_event_loop
from pathlib import Path
from typing import AsyncIterator

from app.agentic_platform.fe_core.ports.claude_runner import (
    AgentEvent,
    EventKind,
    RunRequest,
    RunResult,
)

from app.agentic_platform.worker.runners._common import fold_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model alias → Bedrock cross-region inference profile ID
# Verified against list_inference_profiles(typeEquals="SYSTEM_DEFINED") 2026-08-19
# ---------------------------------------------------------------------------
_MODEL_MAP: dict[str, str] = {
    "claude-sonnet-4-6": "us.anthropic.claude-sonnet-4-6",
    "claude-opus-5":     "us.anthropic.claude-opus-4-7",
    "claude-opus-4-7":   "us.anthropic.claude-opus-4-7",
    "claude-opus-4-6":   "us.anthropic.claude-opus-4-6-v1",
    "claude-sonnet-5":   "us.anthropic.claude-sonnet-4-6",           # fallback: no sonnet-5 profile yet
    "claude-haiku-4-5":  "anthropic.claude-haiku-4-5-20251001-v1:0", # direct foundation model
    "claude-sonnet-4-5": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
}
_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
_REGION = "us-east-1"

# How often to re-read credentials from disk (minutes). Deliberately short: broker
# tokens often have only a couple of minutes of runway, so re-read almost every call
# to pick up a freshly-supplied token before a multi-minute run hits expiry.
_CREDENTIAL_REFRESH_INTERVAL_MINUTES = 2


def _make_refreshable_boto_session() -> "boto3.Session":  # type: ignore[name-defined]
    """Return a boto3 Session whose credentials auto-refresh from disk.

    On laptop (SSO / STS temp creds):
      - Reads credentials DIRECTLY from AWS_SHARED_CREDENTIALS_FILE (or
        ~/.aws/credentials) using configparser — bypasses boto3's cache so
        an updated credentials_clean file is picked up at the next TTL.
      - TTL = 10 min; _load() re-reads from disk every 10 minutes.
      - watch_keys.py keeps credentials_clean in sync with ~/.aws/credentials.
      - Run `bash refresh_keys.sh` after updating SSO creds; the running process
        picks up the new token at the next 10-min refresh — no restart needed.

    On ECS (IAM task role / IRSA):
      - boto3's default chain returns AssumeRoleWithWebIdentity credentials that
        are already RefreshableCredentials managed by IMDSv2/IRSA; this wrapper
        re-reads from the standard chain and is essentially a transparent pass-through.
    """
    import boto3  # type: ignore[import]
    import configparser
    from datetime import datetime, timedelta, timezone

    try:
        from botocore.credentials import RefreshableCredentials  # type: ignore[import]
        from botocore.session import get_session as botocore_get_session  # type: ignore[import]
    except ImportError:
        logger.warning("botocore not available; using non-refreshable boto3 session")
        profile = os.environ.get("AWS_PROFILE", "")
        return boto3.Session(profile_name=profile or None, region_name=_REGION)

    profile = os.environ.get("AWS_PROFILE", "") or "default"

    def _load() -> dict:
        """Read credentials directly from the credentials file — no boto3 cache."""
        creds_file = os.environ.get(
            "AWS_SHARED_CREDENTIALS_FILE",
            os.path.expanduser("~/.aws/credentials"),
        )
        cfg = configparser.ConfigParser()
        cfg.read(creds_file)

        if profile in cfg:
            section = cfg[profile]
            access_key = section.get("aws_access_key_id", "").strip()
            secret_key = section.get("aws_secret_access_key", "").strip()
            token = section.get("aws_session_token", "").strip() or None
            if access_key and secret_key:
                expiry = datetime.now(timezone.utc) + timedelta(
                    minutes=_CREDENTIAL_REFRESH_INTERVAL_MINUTES
                )
                logger.debug(
                    "Credential refresh: read %s from %s (key=%s...)",
                    profile, creds_file, access_key[:12],
                )
                return {
                    "access_key": access_key,
                    "secret_key": secret_key,
                    "token": token,
                    "expiry_time": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }

        # Credentials file missing / profile absent — fall back to boto3 chain
        # (works on ECS where there is no credentials file).
        logger.info(
            "Profile '%s' not in %s; falling back to boto3 default chain", profile, creds_file
        )
        s = boto3.Session(profile_name=None, region_name=_REGION)
        creds = s.get_credentials()
        if creds is None:
            raise RuntimeError("No AWS credentials found via file or boto3 default chain")
        frozen = creds.get_frozen_credentials()
        expiry = datetime.now(timezone.utc) + timedelta(
            minutes=_CREDENTIAL_REFRESH_INTERVAL_MINUTES
        )
        return {
            "access_key": frozen.access_key,
            "secret_key": frozen.secret_key,
            "token": frozen.token,
            "expiry_time": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    try:
        refreshable = RefreshableCredentials.create_from_metadata(
            metadata=_load(),
            refresh_using=_load,
            method="sts-credentials-file",
        )
        botocore_session = botocore_get_session()
        botocore_session._credentials = refreshable  # type: ignore[attr-defined]
        return boto3.Session(botocore_session=botocore_session, region_name=_REGION)
    except Exception as exc:
        logger.warning("Could not create refreshable session (%s); using plain session", exc)
        profile_or_none = os.environ.get("AWS_PROFILE") or None
        return boto3.Session(profile_name=profile_or_none, region_name=_REGION)


class BedrockReActRunner:
    """ECS-ready runner: create_react_agent + ChatBedrockConverse + Python tools.

    Implements the ClaudeRunner protocol (fe_core.ports.claude_runner).
    """

    name = "bedrock"

    # ------------------------------------------------------------------
    # Protocol: preflight
    # ------------------------------------------------------------------

    async def preflight(self) -> tuple[bool, str]:
        """Check Bedrock credentials without making an LLM call.

        Uses _load() (direct configparser read from credentials file) as the
        primary check — this bypasses boto3's credential cache and is immune to
        the AWS_PROFILE env-var not being picked up in certain subprocess contexts.
        Falls back to credential_status() (boto3-based) for ECS / IRSA paths
        where there is no credentials file.
        """
        import configparser as _cp

        try:
            creds_file = os.environ.get(
                "AWS_SHARED_CREDENTIALS_FILE",
                os.path.expanduser("~/.aws/credentials"),
            )
            profile = os.environ.get("AWS_PROFILE", "") or "default"
            cfg = _cp.ConfigParser()
            cfg.read(creds_file)
            if profile in cfg and cfg[profile].get("aws_access_key_id", "").strip():
                key = cfg[profile]["aws_access_key_id"].strip()
                return True, f"credentials from {creds_file} [{profile}] key={key[:12]}..."
        except Exception:
            pass

        # Fallback: boto3 default chain (ECS task role / IRSA / env vars)
        try:
            from app.agentic_platform.fe_core.rag.bedrock import credential_status  # type: ignore[import]
            status = credential_status()
            detail = status.get("detail", "")
            if status.get("source") is None:
                return False, detail or "no AWS credentials found"
            if status.get("expired"):
                return False, detail or "AWS session token has expired"
            return True, detail or f"credentials from {status.get('source', 'unknown')}"
        except Exception as exc:  # pragma: no cover
            return False, f"Bedrock credential check failed: {exc}"

    # ------------------------------------------------------------------
    # Protocol: run (collects all stream events into a RunResult)
    # ------------------------------------------------------------------

    async def run(self, request: RunRequest) -> RunResult:
        result = RunResult(correlation_id=request.correlation_id or "")
        async for event in self.stream(request):
            fold_event(event, result)
        return result

    # ------------------------------------------------------------------
    # Protocol: stream
    # ------------------------------------------------------------------

    async def stream(self, request: RunRequest) -> AsyncIterator[AgentEvent]:  # type: ignore[override]
        from langchain_aws import ChatBedrockConverse  # type: ignore[import]
        from langchain_core.messages import HumanMessage  # type: ignore[import]
        from langgraph.prebuilt import create_react_agent  # type: ignore[import]

        model_id = _MODEL_MAP.get(request.model or "", _DEFAULT_MODEL)
        workspace: Path = Path(request.workspace) if request.workspace else Path(".")

        # Build workspace-scoped tools
        tools = _make_tools(workspace, request)

        # LLM — IAM task role auth via boto3 default credential chain.
        # FE_LLM_TEMPERATURE and FE_LLM_MAX_TOKENS come from config.json
        # (pushed into env by apply_config_json on startup).
        # FE_LLM_READ_TIMEOUT: Bedrock read timeout in seconds. Default boto3
        # timeout is 60s which is too short for large PRD/SDD generations
        # (58K+ input tokens can take 2-5 minutes to generate). Set to 600s.
        _temperature = float(os.environ.get("FE_LLM_TEMPERATURE", "0"))
        _max_tokens = int(os.environ.get("FE_LLM_MAX_TOKENS", "8000"))
        _read_timeout = int(os.environ.get("FE_LLM_READ_TIMEOUT", "600"))

        from botocore.config import Config as BotocoreConfig  # type: ignore[import]
        _boto_config = BotocoreConfig(
            read_timeout=_read_timeout,
            connect_timeout=30,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        # Use a refreshable boto3 session so long-running stages (SDD ~54 min)
        # can outlast the ~60-min SSO token expiry on laptop. On ECS the task role
        # already auto-refreshes; this is a harmless no-op there.
        _boto_session = _make_refreshable_boto_session()
        _bedrock_client = _boto_session.client(
            "bedrock-runtime",
            region_name=_REGION,
            config=_boto_config,
        )
        llm = ChatBedrockConverse(
            model_id=model_id,
            client=_bedrock_client,
            temperature=_temperature,
            max_tokens=_max_tokens,
        )

        # System prompt = optional plugin persona appended to the stage spec.
        # RunRequest carries the stage deliverable spec in `prompt` (human turn)
        # and an optional persona override in `system_prompt_append` (system turn).
        system_prompt = request.system_prompt_append or None

        # Micro-level ReAct loop (LangGraph manages Reason → Tool → Observe)
        agent = create_react_agent(
            llm,
            tools,
            prompt=system_prompt,
        )

        yield AgentEvent(kind=EventKind.SESSION_START, text="bedrock runner started",
                         detail={"session_id": request.correlation_id})

        total_input = 0
        total_output = 0
        final_text_parts: list[str] = []

        try:
            async for chunk in agent.astream(
                {"messages": [HumanMessage(content=request.prompt)]},
                config={"recursion_limit": max(
                    # FE_REACT_MAX_STEPS caps the agent loop globally (default 12, was 24).
                    # Pipeline YAML max_turns is a per-stage ceiling; we take the lower of the two.
                    min(
                        int(os.environ.get("FE_REACT_MAX_STEPS", "12")),
                        request.limits.max_turns if request.limits else 40,
                    ), 10) * 3},
            ):
                # LangGraph emits dict chunks; map to AgentEvent
                for event in _map_chunk(chunk):
                    if event.kind == EventKind.USAGE:
                        total_input += int(event.detail.get("input_tokens") or 0)
                        total_output += int(event.detail.get("output_tokens") or 0)
                    if event.kind == EventKind.TEXT and event.text:
                        final_text_parts.append(event.text)
                    yield event

        except Exception as exc:
            logger.exception("BedrockReActRunner stream error")
            yield AgentEvent(
                kind=EventKind.SESSION_END,
                text=str(exc),
                detail={"is_error": True, "input_tokens": total_input, "output_tokens": total_output},
            )
            return

        yield AgentEvent(
            kind=EventKind.SESSION_END,
            text="\n".join(final_text_parts) if final_text_parts else None,
            detail={"input_tokens": total_input, "output_tokens": total_output},
        )


# ---------------------------------------------------------------------------
# Tool builders
# ---------------------------------------------------------------------------

def _make_chunk_store():
    """Build a ChunkStore from env vars (FE_DB_URL + FE_DB_SCHEMA).

    Called lazily inside each tool so the import path is only needed at
    tool-call time, not at module load.
    """
    from app.agentic_platform.fe_core.rag.retriever import ChunkStore  # type: ignore[import]
    db_url = os.environ.get("FE_DB_URL", "")
    schema = os.environ.get("FE_DB_SCHEMA", "fe")
    if not db_url:
        raise RuntimeError("FE_DB_URL is not set — cannot reach the KB chunk store")
    return ChunkStore(url=db_url, schema=schema)


def _project_id_from_workspace(workspace_id: str | None) -> str:
    """Derive project_id from workspace_id.

    Convention: workspace_id = '<project_id>--<tier>' (e.g. 'uw-credit-risk--global').
    Falls back to the full workspace_id if no '--' separator is found.
    """
    if not workspace_id:
        return ""
    return workspace_id.split("--")[0]


def _make_tools(workspace: Path, request: RunRequest):  # noqa: ANN201
    """Return LangChain tool functions scoped to the current workspace."""
    from langchain_core.tools import tool  # type: ignore[import]

    workspace_id: str | None = getattr(request, "workspace_id", None)
    # project_id derived from workspace_id (e.g. 'uw-credit-risk--global' -> 'uw-credit-risk')
    # Used as the ChunkStore filter key — chunks are stored per-project, not per-workspace.
    project_id: str = _project_id_from_workspace(workspace_id)

    @tool
    def write_file(filename: str, content: str) -> str:
        """Write a deliverable file to the stage workspace.

        Args:
            filename: Relative path within the workspace (e.g. 'BRD.md').
            content: Full file content to write.
        """
        target = workspace / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.info("BedrockReActRunner wrote: %s", target)
        return f"Written: {filename} ({len(content)} chars)"

    @tool
    def read_file(filename: str) -> str:
        """Read an existing file from the stage workspace.

        Args:
            filename: Relative path within the workspace.
        """
        target = workspace / filename
        if not target.is_file():
            return f"File not found: {filename}"
        return target.read_text(encoding="utf-8")

    @tool
    def append_file(filename: str, content: str) -> str:
        """Append content to an existing file in the stage workspace.

        Use this for incremental writes where you need to add content to a file
        without reading it first. Much more efficient than read→write pattern.
        Creates the file if it doesn't exist.

        Args:
            filename: Relative path within the workspace (e.g. 'business-rules.md').
            content: Content to append to the file.
        """
        target = workspace / filename
        target.parent.mkdir(parents=True, exist_ok=True)

        # Append mode - no read required
        with target.open('a', encoding='utf-8') as f:
            f.write(content)

        logger.info("BedrockReActRunner appended to: %s (%d chars)", target, len(content))
        return f"Appended {len(content)} chars to {filename}"

    @tool
    def kb_search(query: str, top_k: int = 8) -> str:
        """Search the knowledge base (pgvector RAG) for relevant document chunks.

        Args:
            query: Natural-language search query.
            top_k: Maximum number of results to return (default 8, max 20).
        """
        try:
            from app.agentic_platform.fe_core.rag.retriever import retrieve  # type: ignore[import]
            if not project_id:
                return "KB search unavailable: no project_id (workspace_id not set on request)"
            store = _make_chunk_store()
            # workspace_ids=None: chunks were indexed with workspace_id=None (project-level),
            # so we don't filter by workspace — all project chunks are eligible.
            result = retrieve(store, project_id, query, workspace_ids=None, top_k=min(top_k, 20))
            if result.refused:
                return f"No relevant KB chunks found ({result.refuse_reason})"
            lines = []
            for chunk in result.chunks:
                src = getattr(chunk, "source_path", "")
                content = getattr(chunk, "content", str(chunk))
                lines.append(f"[{src}]\n{content[:500]}")
            return "\n\n---\n\n".join(lines) if lines else "No KB results found."
        except Exception as exc:
            logger.warning("kb_search failed: %s", exc)
            return f"KB search unavailable: {exc}"

    @tool
    def kb_search_multi(queries: list, top_k: int = 8) -> str:
        """Search the KB with multiple queries at once and merge results.

        Use this when a single query would miss synonyms or related concepts.

        Args:
            queries: List of natural-language search queries (2–5 recommended).
            top_k: Maximum total results after merging (default 8).
        """
        try:
            from app.agentic_platform.fe_core.rag.retriever import retrieve_many  # type: ignore[import]
            if not project_id:
                return "KB search unavailable: no project_id"
            if not queries or not isinstance(queries, list):
                return "queries must be a non-empty list of strings"
            store = _make_chunk_store()
            result = retrieve_many(
                store, project_id, [str(q) for q in queries],
                workspace_ids=None, per_query=top_k, total=top_k * 2,
            )
            if result.refused:
                return f"No relevant KB chunks found ({result.refuse_reason})"
            lines = []
            for chunk in result.chunks:
                src = getattr(chunk, "source_path", "")
                content = getattr(chunk, "content", str(chunk))
                lines.append(f"[{src}]\n{content[:500]}")
            return "\n\n---\n\n".join(lines) if lines else "No KB results found."
        except Exception as exc:
            logger.warning("kb_search_multi failed: %s", exc)
            return f"KB search unavailable: {exc}"

    @tool
    def get_upstream_artifact(stage_key: str) -> str:
        """Read the approved output from a prior pipeline stage.

        Args:
            stage_key: Stage identifier (e.g. 'prd', 'frd', 'sdd').
        """
        # _materialise_upstream downloads upstream artifacts from S3 into
        # <worktree>/inputs/<artifact_type>/ before the agent starts.
        inputs_dir = workspace / "inputs" / stage_key
        if inputs_dir.is_dir():
            for f in sorted(inputs_dir.rglob("*.md")):
                return f.read_text(encoding="utf-8")
        exact = inputs_dir.with_suffix(".md")
        if exact.is_file():
            return exact.read_text(encoding="utf-8")
        return f"No upstream artifact found for stage: {stage_key!r}"

    return [write_file, read_file, append_file, kb_search, kb_search_multi, get_upstream_artifact]


# ---------------------------------------------------------------------------
# LangGraph chunk → AgentEvent mapper
# ---------------------------------------------------------------------------

def _map_chunk(chunk: dict) -> list[AgentEvent]:
    """Convert a LangGraph astream chunk dict to a list of AgentEvents.

    AgentEvent only has: kind, at, text, tool_name, path, detail.
    Token counts go in detail={"input_tokens": n, "output_tokens": n}.
    """
    events: list[AgentEvent] = []

    # LangGraph emits chunks keyed by node name.
    # The "agent" node contains AIMessages; "tools" node contains ToolMessages.
    for node_name, node_output in chunk.items():
        messages = node_output.get("messages", []) if isinstance(node_output, dict) else []
        for msg in messages:
            msg_type = type(msg).__name__

            if msg_type == "AIMessage":
                # Text content
                content = getattr(msg, "content", "")
                if isinstance(content, str) and content:
                    events.append(AgentEvent(kind=EventKind.TEXT, text=content))
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                events.append(AgentEvent(kind=EventKind.TEXT, text=text))

                # Token usage — put counts in detail dict (not direct fields)
                usage = getattr(msg, "usage_metadata", None)
                if usage:
                    inp = usage.get("input_tokens", 0)
                    out = usage.get("output_tokens", 0)
                    cache_r = usage.get("cache_read_input_tokens", 0)
                    cache_c = usage.get("cache_creation_input_tokens", 0)
                    if inp or out:
                        events.append(AgentEvent(
                            kind=EventKind.USAGE,
                            detail={
                                "input_tokens": inp,
                                "output_tokens": out,
                                "cache_read_input_tokens": cache_r,
                                "cache_creation_input_tokens": cache_c,
                            },
                        ))

                # Tool calls
                for tc in getattr(msg, "tool_calls", []):
                    events.append(AgentEvent(
                        kind=EventKind.TOOL_CALL,
                        tool_name=tc.get("name", ""),
                        text=str(tc.get("args", {}))[:200],
                    ))

            elif msg_type == "ToolMessage":
                tool_name = getattr(msg, "name", "tool")
                result = str(getattr(msg, "content", ""))
                # Surface write_file calls as FILE_WRITTEN events
                if tool_name == "write_file" and result.startswith("Written:"):
                    filename = result.split("Written:")[-1].split("(")[0].strip()
                    events.append(AgentEvent(
                        kind=EventKind.FILE_WRITTEN,
                        path=filename,
                        text=result,
                    ))
                else:
                    events.append(AgentEvent(
                        kind=EventKind.TOOL_CALL,
                        tool_name=tool_name,
                        text=result[:200],
                    ))

    return events

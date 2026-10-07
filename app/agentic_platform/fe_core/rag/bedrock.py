"""Bedrock access: Titan embeddings and Claude answers.

Credentials come from boto3's default chain, which reads `~/.aws/credentials` and
`~/.aws/config` -- no keys in this repo, in `.env`, or in any log line.

The one thing worth engineering here is the error surface. A temporary session
token expires, and the raw failure is `ExpiredTokenException: The security token
included in the request is expired`, which says nothing about what to do. That
message appears mid-run, after chunks have been written and before any of them
can be embedded, so it needs to name the fix.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EMBED_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIMENSIONS = 1024

#: Answering model. Sonnet 4.6 cross-region inference profile — enabled in this account.
ANSWER_MODEL = "us.anthropic.claude-sonnet-4-6"

#: Routing model. Haiku 4.5 -- classifying a question to an artefact type is a
#: cheap decision that runs on every query.
ROUTER_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

#: Titan's per-request input ceiling. Chunks are far below it; a whole document
#: is not, so the guard belongs here rather than at the call site.
MAX_EMBED_CHARS = 40_000


class BedrockUnavailableError(RuntimeError):
    """Bedrock cannot be used. Carries the operator action, not the driver error."""


def credential_status() -> dict:
    """What the credential chain resolved, and whether it has expired.

    Read from the shared credentials file rather than by making a call, so a
    readiness check costs nothing and works while offline.
    """
    status: dict = {"source": None, "region": None, "expires_at": None,
                    "expired": None, "detail": ""}
    try:
        import boto3
    except ImportError:
        status["detail"] = "boto3 is not installed. Run: pip install boto3"
        return status

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    status["region"] = session.region_name
    creds = session.get_credentials()
    if creds is None:
        status["detail"] = (
            "no AWS credentials found. Configure ~/.aws/credentials, or set "
            "AWS_PROFILE."
        )
        return status
    status["source"] = getattr(creds, "method", "unknown")

    # A temporary token carries its own expiry in the credentials file. Reading it
    # turns "ExpiredTokenException" mid-run into a readiness answer beforehand.
    path = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE",
                               Path.home() / ".aws" / "credentials"))
    profile = os.environ.get("AWS_PROFILE", "default")
    if path.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(path)
            raw = (parser[profile].get("x_security_token_expires")
                   if parser.has_section(profile) or profile == "DEFAULT" else None)
        except Exception:  # noqa: BLE001
            raw = None
        if raw:
            status["expires_at"] = raw
            try:
                expiry = datetime.fromisoformat(raw)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                status["expired"] = expiry <= datetime.now(timezone.utc)
                if status["expired"]:
                    status["detail"] = (
                        f"the session token expired at {raw}. Refresh it "
                        "(re-run your SSO or credential-broker login) -- every "
                        "embedding and answer call will fail until you do."
                    )
                else:
                    status["detail"] = f"session token valid until {raw}"
            except ValueError:
                status["detail"] = f"could not parse the expiry {raw!r}"
    if not status["detail"]:
        status["detail"] = f"credentials from {status['source']}"
    return status


class _Clients:
    """One bedrock-runtime client per process. Thread-safe and lazy."""

    _lock = threading.Lock()
    _runtime = None

    @classmethod
    def runtime(cls):
        if cls._runtime is None:
            with cls._lock:
                if cls._runtime is None:
                    try:
                        import boto3  # noqa: F401
                    except ImportError as exc:
                        raise BedrockUnavailableError(
                            "boto3 is not installed. Run: pip install boto3"
                        ) from exc
                    # Refreshable credentials (re-read from the credentials file on a
                    # short TTL) so a re-supplied token is picked up without a restart
                    # and a long embedding run is not interrupted mid-way. Region is
                    # resolved inside (AWS_REGION / default us-east-1) — this also
                    # fixed "no AWS region resolved". On ECS it falls back to the task
                    # role transparently.
                    from app.agentic_platform.fe_core.aws_session import refreshable_session  # noqa: PLC0415
                    cls._runtime = refreshable_session().client("bedrock-runtime")
        return cls._runtime

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._runtime = None


def _invoke(model_id: str, payload: dict) -> dict:
    try:
        response = _Clients.runtime().invoke_model(
            modelId=model_id, body=json.dumps(payload))
        return json.loads(response["body"].read())
    except BedrockUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "ExpiredToken" in name or "ExpiredToken" in str(exc):
            status = credential_status()
            raise BedrockUnavailableError(
                "the AWS session token has expired"
                + (f" (expiry {status['expires_at']})" if status["expires_at"] else "")
                + ". Refresh it and retry -- nothing was embedded or answered."
            ) from exc
        if "AccessDenied" in name or "AccessDenied" in str(exc):
            raise BedrockUnavailableError(
                f"access denied invoking {model_id}. The role needs "
                "bedrock:InvokeModel on that model, and the model has to be "
                "enabled in this account and region."
            ) from exc
        if "ResourceNotFound" in name:
            raise BedrockUnavailableError(
                f"{model_id} is not available in this account or region. List what "
                "is enabled with `aws bedrock list-inference-profiles`."
            ) from exc
        if "Throttling" in name or "TooManyRequests" in str(exc):
            raise BedrockUnavailableError(
                f"Bedrock throttled the request to {model_id}. Retry with backoff; "
                "embedding pauses between batches for this reason."
            ) from exc
        raise BedrockUnavailableError(f"{name} invoking {model_id}: {exc}") from exc


def embed(texts: list[str]) -> list[list[float]]:
    """Embed each text with Titan v2 at 1024 dimensions, normalised.

    Titan embeds one input per call, so this loops. It is written as a batch
    function anyway because the caller has a batch, and pacing belongs in one
    place rather than at each call site.
    """
    import time

    vectors: list[list[float]] = []
    for position, text in enumerate(texts):
        payload = {
            "inputText": text[:MAX_EMBED_CHARS],
            "dimensions": EMBED_DIMENSIONS,
            # Normalised, which is what makes cosine distance and inner product
            # agree in pgvector.
            "normalize": True,
        }
        body = _invoke(EMBED_MODEL, payload)
        vector = body.get("embedding")
        if not vector or len(vector) != EMBED_DIMENSIONS:
            raise BedrockUnavailableError(
                f"Titan returned {len(vector or [])} dimensions, expected "
                f"{EMBED_DIMENSIONS}. The column is fixed-width, so a mismatch "
                "cannot be stored."
            )
        vectors.append(vector)
        # Small pause between calls: a long backlog otherwise throttles, and a
        # throttle mid-batch loses the whole batch's progress.
        if position and position % 8 == 0:
            time.sleep(0.3)
    return vectors


def _record_ai_call_sync(model_name: str, request_ts: datetime, latency_ms: int,
                         input_tokens: int | None, output_tokens: int | None,
                         status: str = "success") -> None:
    """Fire-and-forget AI call recording from sync context. Non-fatal."""
    try:
        from app.services import ai_call_recorder as _rec  # noqa: PLC0415
        rec = _rec.AICallRecord(
            model_provider="bedrock",
            model_name=model_name,
            request_ts=request_ts,
            response_ts=datetime.now(tz=timezone.utc),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_status=status,
        )
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_rec.record(rec), loop)
    except Exception:  # noqa: BLE001
        pass


def answer(prompt: str, system: str, *, model: str | None = None,
           max_tokens: int = 1200) -> tuple[str, dict]:
    """Ask Claude. Returns (text, usage)."""
    request_ts = datetime.now(tz=timezone.utc)
    t0 = time.perf_counter()
    status = "success"
    body: dict = {}
    try:
        body = _invoke(model or ANSWER_MODEL, {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        })
    except Exception:
        status = "error"
        raise
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = body.get("usage") or {}
        _record_ai_call_sync(
            model or ANSWER_MODEL, request_ts, latency_ms,
            usage.get("input_tokens"), usage.get("output_tokens"), status,
        )
    blocks = body.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") != "thinking")
    return text.strip(), {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "model": model or ANSWER_MODEL,
    }


def converse(messages: list[dict], *, system: str, tools: list[dict] | None = None,
             model: str | None = None, max_tokens: int = 4000, temperature: float = 0.0,
             cache_system: bool = True) -> dict:
    """One Bedrock Converse call with optional tool definitions.

    `messages` are Converse-shaped (`{"role", "content": [{"text"|"toolUse"|"toolResult"}]}`);
    `tools` are `{"name", "description", "input_schema"}` dicts (Anthropic shape) and
    are translated to `toolSpec`. Returns the raw response (`output.message`,
    `stopReason`, `usage`). Errors map to BedrockUnavailableError like `_invoke`.

    `cache_system=True` marks the system prompt as a prompt-cache breakpoint --
    on a bounded ReAct loop the system prompt is resent every step, so this is
    where caching pays.
    """
    system_blocks: list[dict] = [{"text": system}]
    if cache_system:
        system_blocks.append({"cachePoint": {"type": "default"}})
    kwargs: dict = {
        "modelId": model or ANSWER_MODEL,
        "system": system_blocks,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if tools:
        kwargs["toolConfig"] = {"tools": [
            {"toolSpec": {"name": t["name"], "description": t.get("description", ""),
                          "inputSchema": {"json": t.get("input_schema") or
                                          {"type": "object", "properties": {}}}}}
            for t in tools
        ]}
    request_ts = datetime.now(tz=timezone.utc)
    t0 = time.perf_counter()
    status = "success"
    result: dict = {}
    try:
        result = _Clients.runtime().converse(**kwargs)
        return result
    except BedrockUnavailableError:
        status = "error"
        raise
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "cachePoint" in str(exc) and cache_system:
            return converse(messages, system=system, tools=tools, model=model,
                            max_tokens=max_tokens, temperature=temperature, cache_system=False)
        status = "error"
        raise BedrockUnavailableError(f"{name} in converse({model or ANSWER_MODEL}): {exc}") from exc
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        usage = (result.get("usage") or {}) if result else {}
        _record_ai_call_sync(
            model or ANSWER_MODEL, request_ts, latency_ms,
            usage.get("inputTokens"), usage.get("outputTokens"), status,
        )


def classify(question: str, artifact_types: list[str]) -> list[str]:
    """Route a question to the artefact types worth searching.

    Narrowing beats searching everything: with one corpus per project, an
    unfiltered search lets a large document type crowd out the one that actually
    answers the question. A failure here is not fatal -- it falls back to
    searching all types, which is slower but never wrong.
    """
    if not artifact_types:
        return []
    listed = ", ".join(sorted(artifact_types))
    try:
        text, _ = answer(
            f"Question: {question}\n\nAvailable artefact types: {listed}",
            system=(
                "You route a question to the document types that could answer it. "
                "Reply with a comma-separated subset of the given types and "
                "nothing else. Include every type that might help; prefer 2-4. "
                "If unsure, reply ALL."
            ),
            model=ROUTER_MODEL,
            max_tokens=100,
        )
    except BedrockUnavailableError as exc:
        logger.warning("Router unavailable (%s); searching all types", exc)
        return list(artifact_types)

    if "ALL" in text.upper():
        return list(artifact_types)
    chosen = [t.strip() for t in text.replace("\n", ",").split(",")]
    valid = [t for t in chosen if t in set(artifact_types)]
    if not valid:
        logger.info("Router returned nothing usable (%r); searching all types", text)
        return list(artifact_types)
    logger.info("Router chose %s for %r", valid, question[:60])
    return valid

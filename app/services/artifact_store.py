"""S3 artifact store — save/read generated SDLC artifacts per workspace + stage.

Mirrors the TMA ``s3_upload_service`` pattern (put_object / get_object) but uses the already-present
``boto3`` via ``asyncio.to_thread`` (no new async dep). **Config-driven** bucket/prefix/region (never
hardcoded); creds come from the default AWS chain (``AWS_PROFILE`` in dev, task role in prod; the Vault
S3 role — ``S3_VAULT_NAME`` — is the prod cred source). **Overwrite-on-write** (same key replaces), so a
re-run replaces the prior artifact. **Best-effort**: methods log + return ``None``/``False`` on failure
(e.g. no creds in dev) so a stage never breaks — the DB row remains the fallback.

Key scheme (generic per stage): ``{prefix}/{workspace_id}/{stage}/{name}`` — e.g.
``japan/workspaces/ws-abc/impact-analysis/analysis.json``.
"""

from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import Any

from app.config.settings import get_settings
from app.utils.logging import log


class ArtifactStore:
    """Per-workspace/stage S3 store for FE artifacts (overwrite-on-write, best-effort)."""

    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.S3_BUCKET_NAME
        self._prefix = settings.S3_ARTIFACT_PREFIX.strip("/")
        self._region = settings.AWS_REGION
        self._profile = settings.AWS_PROFILE
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        """False when no bucket is configured — callers fall back to the DB mirror."""
        return bool(self._bucket)

    def _s3(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            session = boto3.Session(profile_name=self._profile) if self._profile else boto3.Session()
            self._client = session.client(
                "s3",
                region_name=self._region,
                config=Config(
                    signature_version="s3v4",
                    # Fail fast: S3 is a best-effort mirror (DB is the source of truth for reads), so a
                    # slow/unreachable bucket must not stall a request. Short connect + few retries.
                    retries={"max_attempts": 2, "mode": "standard"},
                    read_timeout=15,
                    connect_timeout=3,
                ),
            )
        return self._client

    def key(self, workspace_id: str, stage: str, name: str) -> str:
        """Deterministic S3 key for a workspace stage artifact (same key overwrites on re-run)."""
        return f"{self._prefix}/{workspace_id}/{stage}/{name}"

    def uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"

    async def put_json(self, key: str, obj: Any) -> str | None:
        """Write an object as JSON (overwrite). Returns the s3:// URI, or None if S3 is off/failed."""
        return await self.put_bytes(key, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> str | None:
        """Write raw bytes to S3 (overwrite). Returns the s3:// URI, or None if S3 is off/failed."""
        if not self.enabled:
            return None
        try:
            await asyncio.to_thread(
                lambda: self._s3().put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
            )
            log.info(f"[s3] wrote {len(data)}B → {self.uri(key)}")
            return self.uri(key)
        except Exception as exc:  # noqa: BLE001 — best-effort; DB row is the fallback
            log.warning(f"[s3] write failed {self.uri(key)}: {exc}")
            return None

    async def get_text(self, key: str) -> str | None:
        """Read an object's text, or None if S3 is off / the key is missing / on error."""
        if not self.enabled:
            return None

        def _read() -> str | None:
            # Single GET (a missing key raises → caught below). Avoids the extra HEAD round-trip:
            # HEAD+GET doubled the S3 latency on every read, and a missing object is the common case
            # when a stage's S3 write failed (s3_uri=None) — the DB mirror is the real source anyway.
            resp = self._s3().get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")

        try:
            return await asyncio.to_thread(_read)
        except Exception as exc:  # noqa: BLE001 — missing key / no creds → fall back to the DB mirror
            log.info(f"[s3] read miss {self.uri(key)}: {exc}")
            return None


@lru_cache
def get_artifact_store() -> ArtifactStore:
    """Process-wide singleton ArtifactStore (cached)."""
    return ArtifactStore()

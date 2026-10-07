"""A shared boto3 Session whose credentials auto-refresh from the credentials file.

The point is that re-supplying credentials must not require a restart and must not
interrupt work already in flight:

* **Laptop (SSO / STS temp creds):** credentials are read DIRECTLY from
  ``AWS_SHARED_CREDENTIALS_FILE`` (or ``~/.aws/credentials``) on a short TTL, so a
  running process picks up a freshly-supplied token within the TTL, mid-run,
  without a restart. Every client built on this session (Bedrock, S3, embeddings)
  refreshes together.
* **ECS (task role / IRSA):** the profile/file is absent, so this transparently
  falls back to boto3's default chain, whose credentials are already refreshable.
  Nothing here is laptop-specific in a way that breaks the container.

This mirrors ``worker/runners/bedrock_runner._make_refreshable_boto_session`` and is
factored into ``fe_core`` so the S3 store and the embedding client can share it
without importing from the worker package.
"""

from __future__ import annotations

import configparser
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

#: Re-read the credentials file this often (minutes). Deliberately short: on a
#: laptop the broker token often has only a couple of minutes of runway, so the
#: session must re-read a freshly-supplied token quickly (effectively on almost
#: every call) or a multi-minute run dies mid-way. On ECS this is irrelevant —
#: the task role's own refreshable creds are used.
CREDENTIAL_REFRESH_MINUTES = 2


def _resolve_region(region: str | None) -> str:
    return (region or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")


def refreshable_session(region: str | None = None):
    """Return a ``boto3.Session`` whose credentials auto-refresh from the credentials
    file, falling back to boto3's default chain (ECS task role) when no file/profile."""
    import boto3  # noqa: PLC0415

    region = _resolve_region(region)
    try:
        from botocore.credentials import RefreshableCredentials  # noqa: PLC0415
        from botocore.session import get_session as botocore_get_session  # noqa: PLC0415
    except ImportError:
        logger.warning("botocore unavailable; using a non-refreshable session")
        return boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None,
                             region_name=region)

    profile = os.environ.get("AWS_PROFILE", "") or "default"

    def _load() -> dict:
        creds_file = os.environ.get(
            "AWS_SHARED_CREDENTIALS_FILE", os.path.expanduser("~/.aws/credentials"))
        cfg = configparser.ConfigParser()
        cfg.read(creds_file)
        if profile in cfg:
            sec = cfg[profile]
            access_key = sec.get("aws_access_key_id", "").strip()
            secret_key = sec.get("aws_secret_access_key", "").strip()
            token = sec.get("aws_session_token", "").strip() or None
            if access_key and secret_key:
                expiry = datetime.now(timezone.utc) + timedelta(minutes=CREDENTIAL_REFRESH_MINUTES)
                return {"access_key": access_key, "secret_key": secret_key, "token": token,
                        "expiry_time": expiry.strftime("%Y-%m-%dT%H:%M:%SZ")}
        # No file/profile -> boto3 default chain (e.g. ECS task role).
        default = boto3.Session(profile_name=None, region_name=region).get_credentials()
        if default is None:
            raise RuntimeError("No AWS credentials via file or boto3 default chain")
        frozen = default.get_frozen_credentials()
        expiry = datetime.now(timezone.utc) + timedelta(minutes=CREDENTIAL_REFRESH_MINUTES)
        return {"access_key": frozen.access_key, "secret_key": frozen.secret_key,
                "token": frozen.token, "expiry_time": expiry.strftime("%Y-%m-%dT%H:%M:%SZ")}

    try:
        refreshable = RefreshableCredentials.create_from_metadata(
            metadata=_load(), refresh_using=_load, method="sts-credentials-file")
        botocore_session = botocore_get_session()
        botocore_session._credentials = refreshable  # noqa: SLF001
        return boto3.Session(botocore_session=botocore_session, region_name=region)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build refreshable session (%s); using plain session", exc)
        return boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None, region_name=region)

"""ArtifactStore port with Local and S3 implementations.

Design rules
------------
* One key layout for both backends (see package docstring), so `content_uri` is
  either `file:///…/<key>` or `s3://<bucket>/<key>` and nothing else changes.
* Every `put_tree` writes a `manifest.json` (files, sha256, size) as the last
  object; a consumer treats a prefix without a manifest as incomplete.
* Credentials never come from config: boto3's default chain (laptop profile /
  env vars / ECS task role). Config supplies only bucket, prefix, KMS key and an
  optional endpoint (LocalStack / MinIO).
* Presigned URLs are short-lived (default 15 min) and only for GET; uploads from
  laptops go through the API, not straight to the bucket.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
DEFAULT_PRESIGN_SECONDS = 900

#: Directories never uploaded from a worktree (VCS, caches, runner scratch).
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mcp-config.json", ".upstream", "inputs"}
SKIP_FILES = {".mcp-config.json"}


def _win_long(p: Path) -> str:
    """Return extended-length path string on Windows to bypass MAX_PATH (260 chars)."""
    if os.name != "nt":
        return str(p)
    s = str(p.resolve())
    return s if s.startswith("\\\\?\\") else "\\\\?\\" + s


# ---------------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FileRef:
    path: str          # relative to the artefact prefix, forward slashes
    sha256: str
    size: int
    content_type: str | None = None


@dataclass
class Manifest:
    artifact_id: str
    artifact_type: str
    version: int
    project_id: str
    workspace_id: str | None
    key_prefix: str
    files: list[FileRef] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tree_sha256: str = ""       # sha256 over sorted "path:sha256" lines — the artefact checksum

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        d = json.loads(text)
        d["files"] = [FileRef(**f) for f in d.get("files", [])]
        return cls(**d)


def artifact_key(prefix: str, project_id: str, workspace_id: str | None,
                 artifact_type: str, version: int, artifact_id: str) -> str:
    """The canonical prefix for one artefact version. Safe characters only."""
    def _s(x: str | None) -> str:
        x = str(x or "_")
        return "".join(c for c in x if c.isalnum() or c in "-_.") or "_"
    parts = [p for p in [prefix.strip("/"), _s(project_id), _s(workspace_id) if workspace_id else "_global",
                         _s(artifact_type), f"v{int(version)}", _s(artifact_id)] if p]
    return "/".join(parts)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(_win_long(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_tree(root: Path) -> Iterable[Path]:
    root_str = _win_long(root)
    pfx = "\\\\?\\" if os.name == "nt" else ""
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_FILES:
                continue
            real_dir = dirpath[len(pfx):] if pfx and dirpath.startswith(pfx) else dirpath
            yield Path(real_dir) / name


def local_tree_manifest(root: Path) -> list[FileRef]:
    """FileRefs (path, sha256, size) for a local tree WITHOUT uploading — used to
    compute an artefact checksum before deciding whether to store it."""
    root = Path(root)
    if not root.is_dir():
        return []
    refs: list[FileRef] = []
    for path in _iter_tree(root):
        refs.append(FileRef(path=path.relative_to(root).as_posix(), sha256=_sha256_file(path),
                            size=os.stat(_win_long(path)).st_size, content_type=mimetypes.guess_type(path.name)[0]))
    return refs


def _tree_checksum(files: list[FileRef]) -> str:
    h = hashlib.sha256()
    for f in sorted(files, key=lambda f: f.path):
        h.update(f"{f.path}:{f.sha256}\n".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# port
# ---------------------------------------------------------------------------
class ArtifactStore(Protocol):
    kind: str

    def uri(self, key: str) -> str: ...
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def put_tree(self, local_dir: Path, key_prefix: str, *, artifact_id: str, artifact_type: str,
                 version: int, project_id: str, workspace_id: str | None) -> Manifest: ...
    def get_tree(self, key_prefix: str, dest: Path) -> Manifest: ...
    def read_manifest(self, key_prefix: str) -> Manifest | None: ...
    def presign_get(self, key: str, expires_seconds: int = DEFAULT_PRESIGN_SECONDS) -> str: ...
    def list_keys(self, key_prefix: str) -> list[str]: ...
    def delete_prefix(self, key_prefix: str) -> int: ...


# ---------------------------------------------------------------------------
# shared tree logic
# ---------------------------------------------------------------------------
class _TreeMixin:
    """put_tree / get_tree expressed over put_bytes / get_bytes / list_keys."""

    def put_tree(self, local_dir: Path, key_prefix: str, *, artifact_id: str, artifact_type: str,
                 version: int, project_id: str, workspace_id: str | None) -> Manifest:
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            raise FileNotFoundError(f"artefact tree not found: {local_dir}")
        key_prefix = key_prefix.strip("/")
        refs: list[FileRef] = []
        for path in _iter_tree(local_dir):
            rel = path.relative_to(local_dir).as_posix()
            with open(_win_long(path), "rb") as _fh:
                data = _fh.read()
            ctype = mimetypes.guess_type(path.name)[0]
            self.put_bytes(f"{key_prefix}/{rel}", data, ctype)          # type: ignore[attr-defined]
            refs.append(FileRef(path=rel, sha256=hashlib.sha256(data).hexdigest(),
                                size=len(data), content_type=ctype))
        manifest = Manifest(artifact_id=artifact_id, artifact_type=artifact_type, version=version,
                            project_id=project_id, workspace_id=workspace_id, key_prefix=key_prefix,
                            files=refs, tree_sha256=_tree_checksum(refs))
        # manifest last: its presence means "complete"
        self.put_bytes(f"{key_prefix}/{MANIFEST_NAME}", manifest.to_json().encode("utf-8"),   # type: ignore[attr-defined]
                       "application/json")
        return manifest

    def read_manifest(self, key_prefix: str) -> Manifest | None:
        key = f"{key_prefix.strip('/')}/{MANIFEST_NAME}"
        if not self.exists(key):                                        # type: ignore[attr-defined]
            return None
        return Manifest.from_json(self.get_bytes(key).decode("utf-8"))  # type: ignore[attr-defined]

    def get_tree(self, key_prefix: str, dest: Path) -> Manifest:
        manifest = self.read_manifest(key_prefix)
        if manifest is None:
            raise FileNotFoundError(f"no {MANIFEST_NAME} under {key_prefix} — artefact incomplete or missing")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        for f in manifest.files:
            data = self.get_bytes(f"{key_prefix.strip('/')}/{f.path}")   # type: ignore[attr-defined]
            if hashlib.sha256(data).hexdigest() != f.sha256:
                raise ValueError(f"checksum mismatch for {f.path} under {key_prefix}")
            target = dest / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (dest / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
        return manifest


# ---------------------------------------------------------------------------
# local
# ---------------------------------------------------------------------------
class LocalArtifactStore(_TreeMixin):
    kind = "local"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        key = key.strip("/")
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"key escapes artefact root: {key}")
        return p

    def uri(self, key: str) -> str:
        return self._path(key).as_uri()

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
        return self.uri(key)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def presign_get(self, key: str, expires_seconds: int = DEFAULT_PRESIGN_SECONDS) -> str:
        # No signing locally: the API streams the file itself. Return the file URI
        # so callers have one code path.
        return self.uri(key)

    def list_keys(self, key_prefix: str) -> list[str]:
        base = self._path(key_prefix)
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def delete_prefix(self, key_prefix: str) -> int:
        base = self._path(key_prefix)
        if not base.exists():
            return 0
        n = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base)
        return n


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------
class S3ArtifactStore(_TreeMixin):
    kind = "s3"

    def __init__(self, bucket: str, *, prefix: str = "fe", kms_key_id: str | None = None,
                 endpoint_url: str | None = None, region_name: str | None = None, client=None):
        if not bucket:
            raise ValueError("FE_S3_BUCKET is required for FE_ARTIFACT_STORE=s3")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.kms_key_id = kms_key_id or None
        if client is None:
            try:
                import boto3  # noqa: F401, PLC0415
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("boto3 is required for the S3 artefact store: pip install boto3") from exc
            # Refreshable credentials (re-read from the credentials file on a short
            # TTL) so a re-supplied token is picked up without a restart and an
            # upload/download in flight is not interrupted. On ECS this falls back
            # to the task role transparently.
            from app.agentic_platform.fe_core.aws_session import refreshable_session  # noqa: PLC0415
            kwargs = {}
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            client = refreshable_session(region_name).client("s3", **kwargs)
        self.client = client

    # keys passed in already include the configured prefix (artifact_key does that)
    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key.strip('/')}"

    def _sse(self) -> dict:
        if self.kms_key_id:
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        return {"ServerSideEncryption": "AES256"}

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        extra = dict(self._sse())
        if content_type:
            extra["ContentType"] = content_type
        self.client.put_object(Bucket=self.bucket, Key=key.strip("/"), Body=data, **extra)
        return self.uri(key)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key.strip("/"))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key.strip("/"))
            return True
        except Exception as exc:  # noqa: BLE001 - botocore ClientError 404 / NoSuchKey
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def presign_get(self, key: str, expires_seconds: int = DEFAULT_PRESIGN_SECONDS) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key.strip("/")},
            ExpiresIn=int(expires_seconds))

    def list_keys(self, key_prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key_prefix.strip("/") + "/"):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return sorted(keys)

    def delete_prefix(self, key_prefix: str) -> int:
        keys = self.list_keys(key_prefix)
        for i in range(0, len(keys), 1000):
            batch = [{"Key": k} for k in keys[i:i + 1000]]
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
        return len(keys)


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------
_store: ArtifactStore | None = None


def get_artifact_store(settings=None) -> ArtifactStore:
    """Build (once) the artefact store selected by FE_ARTIFACT_STORE."""
    global _store
    if _store is not None:
        return _store
    if settings is None:
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415
        settings = get_settings()
    if settings.fe_artifact_store == "s3":
        _store = S3ArtifactStore(settings.fe_s3_bucket or "", prefix=settings.fe_s3_prefix,
                                 kms_key_id=settings.fe_s3_kms_key_id,
                                 endpoint_url=settings.fe_s3_endpoint_url)
        logger.info("Artefact store: s3://%s/%s", settings.fe_s3_bucket, settings.fe_s3_prefix)
    else:
        _store = LocalArtifactStore(settings.fe_artifact_root)
        logger.info("Artefact store: local %s", settings.fe_artifact_root)
    return _store


def reset_artifact_store() -> None:
    """Tests only."""
    global _store
    _store = None

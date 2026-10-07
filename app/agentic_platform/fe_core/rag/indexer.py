"""Index an artefact: read its files, chunk them, store them, embed them.

Chunking and embedding are separate steps on purpose. Chunking is local and always
succeeds; embedding calls Bedrock and can fail on an expired token or a throttle.
Splitting them means a Bedrock outage leaves rows with `embedding IS NULL` -- still
findable by lexical search, and completable later by draining the backlog -- rather
than losing the chunking work.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.agentic_platform.fe_core.kb.models import SdlcArtifact
from app.agentic_platform.fe_core.rag.chunker import chunk
from app.agentic_platform.fe_core.rag.retriever import ChunkStore

logger = logging.getLogger(__name__)

#: Text this service will read. Anything else is listed, not ingested: a binary
#: decoded as text becomes noise chunks that later get cited as grounding.
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv", ".sql",
    ".html", ".xml", ".feature", ".log", ".ts", ".js", ".py", ".java", ".cs",
    ".rego", ".sh", ".bat", ".properties", ".env", ".gradle", ".xml",
}
MAX_FILE_CHARS = 400_000
#: Overlap between consecutive segments so a sentence split at the boundary
#: is captured in at least one segment.
_SEGMENT_OVERLAP = 2_000


@dataclass
class IndexReport:
    artifact_id: str
    files_indexed: int = 0
    chunks_written: int = 0
    embedded: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "files_indexed": self.files_indexed,
            "chunks_written": self.chunks_written,
            "embedded": self.embedded,
            "awaiting_embedding": max(0, self.chunks_written - self.embedded),
            "skipped": self.skipped,
            "errors": self.errors,
        }


class Indexer:
    def __init__(self, store: ChunkStore, embedder=None):
        self.store = store
        self._embedder = embedder

    def index_artifact(self, artifact: SdlcArtifact, *,
                       embed_now: bool = True) -> IndexReport:
        report = IndexReport(artifact_id=artifact.id)
        # Wherever the body lives (worktree, S3, local artefact store), read it
        # from a local directory. Git-ref artefacts have no body to index.
        from app.agentic_platform.fe_core.artifacts.reader import materialise  # noqa: PLC0415
        try:
            root = materialise(artifact)
        except Exception as exc:  # noqa: BLE001
            root = None
            report.errors.append(f"materialise failed: {exc}")
        if root is None or not Path(root).exists():
            report.errors.append(f"no local body for artefact (path={artifact.path}, "
                                 f"storage={getattr(artifact, 'storage_kind', None)})")
            return report
        root = Path(root)

        # A run worktree also holds the materialised upstream inputs, the MCP
        # config and other dotfiles -- none of which is this artefact's content
        # (indexing inputs/ would re-index the upstream document under the
        # wrong artefact and stage).
        def _own_content(p: Path) -> bool:
            rel = p.relative_to(root).parts if p != root else (p.name,)
            return not any(part.startswith(".") or part in ("inputs", "node_modules", "__pycache__")
                           for part in rel)

        files = [root] if root.is_file() else sorted(
            p for p in root.rglob("*") if p.is_file() and _own_content(p))

        # The chunk table's FKs point at fe_workspace / fe_sdlc_artifact, which
        # exist only when FE_STORE=postgres. With the JSON store those rows are
        # absent, so the linkage columns are dropped rather than losing the
        # index (the run/artefact/stage remain identifiable via source_path,
        # artifact_type and stage_key).
        link_ids = {"artifact_id": artifact.id, "workspace_id": artifact.workspace_id}

        for path in files:
            if path.suffix.lower() not in TEXT_SUFFIXES:
                report.skipped.append(f"{path.name} (not text)")
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except UnicodeDecodeError:
                # Strict on purpose: `errors="replace"` would produce chunks of
                # replacement characters that retrieve and then mislead.
                report.skipped.append(f"{path.name} (not UTF-8)")
                continue
            except OSError as exc:
                report.errors.append(f"{path.name}: {exc}")
                continue

            segments = _split_segments(text)
            chunks = []
            for seg in segments:
                chunks.extend(chunk(seg, artifact.artifact_type, str(path)))
            if not chunks:
                continue
            def _write(ids: dict) -> int:
                return self.store.replace_chunks(
                    project_id=artifact.kb_application_id,
                    source_path=f"artifact://{artifact.id}/{path.name}",
                    chunks=chunks,
                    artifact_id=ids["artifact_id"],
                    artifact_type=artifact.artifact_type,
                    workspace_id=ids["workspace_id"],
                    tier=artifact.tier,
                    epic_id=artifact.epic_id,
                    stage_key=artifact.stage_key,
                )
            try:
                written = _write(link_ids)
            except Exception as exc:  # noqa: BLE001
                if "foreign key" not in str(exc).lower() and "fkey" not in str(exc).lower():
                    raise
                if link_ids["artifact_id"] is None and link_ids["workspace_id"] is None:
                    raise
                logger.info("chunk FK rejected linkage ids for %s (%s); indexing without them",
                            artifact.artifact_type, str(exc).split("\n")[0][:120])
                link_ids = {"artifact_id": None, "workspace_id": None}
                written = _write(link_ids)
            report.files_indexed += 1
            report.chunks_written += written

        if embed_now and report.chunks_written:
            try:
                report.embedded = self.embed_pending(artifact.kb_application_id)
            except Exception as exc:  # noqa: BLE001
                # Not fatal: the chunks are stored and lexically searchable, and
                # `embed_pending` can finish the job once Bedrock is usable.
                report.errors.append(f"embedding deferred: {exc}")

        logger.info(
            "Indexed %s (%s): %d file(s), %d chunk(s), %d embedded, %d skipped",
            artifact.id, artifact.artifact_type, report.files_indexed,
            report.chunks_written, report.embedded, len(report.skipped),
        )
        return report

    def embed_pending(self, project_id: str | None = None,
                      batch: int = 25, max_batches: int = 40) -> int:
        """Embed chunks that have none yet. Returns how many were embedded."""
        embed = self._embedder or _bedrock_embed
        total = 0
        for _ in range(max_batches):
            pending = self.store.pending_embeddings(project_id, batch)
            if not pending:
                break
            vectors = embed([content for _, content in pending])
            total += self.store.store_embeddings(
                list(zip((cid for cid, _ in pending), vectors)))
        if total:
            logger.info("Embedded %d chunk(s)", total)
        return total

    def embed_all(self, project_id: str | None = None, batch: int = 25,
                  stop: "threading.Event | None" = None) -> int:
        """Embed every pending chunk, however many. For background draining.

        `embed_pending` is capped so an upload response returns in bounded time;
        the RED for this project chunks to ~20,000 rows and the cap left 95% of
        it invisible to semantic search. This keeps going until nothing is
        pending (or `stop` is set), logging progress every 1,000.
        """
        total = 0
        while stop is None or not stop.is_set():
            done = self.embed_pending(project_id, batch=batch, max_batches=40)
            if not done:
                break
            total += done
            logger.info("Embedding drain for %s: %d so far", project_id or "*", total)
        return total


def _split_segments(text: str) -> list[str]:
    """Split text larger than MAX_FILE_CHARS into overlapping segments.

    Splits on paragraph boundaries (blank lines) where possible so chunks
    do not start mid-sentence. Falls back to a hard split if no boundary
    is found within the window.
    """
    if len(text) <= MAX_FILE_CHARS:
        return [text]
    segments: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_FILE_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start, end)
            if boundary == -1:
                boundary = text.rfind("\n", start, end)
            if boundary > start:
                end = boundary + 1
        segments.append(text[start:end])
        start = max(start + 1, end - _SEGMENT_OVERLAP)
    return segments


def _bedrock_embed(texts: list[str]) -> list[list[float]]:
    from app.agentic_platform.fe_core.rag.bedrock import embed

    return embed(texts)

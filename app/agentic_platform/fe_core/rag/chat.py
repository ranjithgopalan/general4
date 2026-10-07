"""Per-project chat: retrieve, answer with citations, or refuse.

The refusal path is the important one. A RAG answer that quietly falls back to the
model's general knowledge is indistinguishable from a grounded one, and in this
setting that is how invented requirements get quoted back as if they came from the
project's own documents. So when retrieval finds nothing above the relevance
floor, this says so and stops.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from app.agentic_platform.fe_core.rag.retriever import ChunkStore, RetrievedChunk, retrieve

logger = logging.getLogger(__name__)

SYSTEM = """You answer questions about one software modernization project, using \
only the excerpts provided.

Rules:
- Use ONLY the excerpts. If they do not contain the answer, say so plainly and \
stop. Do not fill the gap from general knowledge.
- Cite the excerpt number inline as [1], [2] for every substantive claim.
- If excerpts disagree, say so and cite both rather than choosing silently.
- Quote exact identifiers (table, procedure, field, requirement ids) verbatim; \
never adjust or guess one.
- Be concise. A short cited answer is worth more than a long uncited one.
- These excerpts are generated project documents, some awaiting human approval. \
If an answer rests on an unapproved artefact, note that."""

REFUSAL = (
    "I could not find anything in this project's indexed documents that answers "
    "that.\n\n{reason}\n\nWhat would help:\n"
    "- if the document exists but is not indexed, run indexing for it\n"
    "- if the stage that produces it has not run yet, there is nothing to find\n"
    "- rephrasing with a term used in the documents (a requirement id, a table "
    "name) often finds it, because half of the search is literal"
)


def build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    parts = [f"Question: {question}", "", "Excerpts:"]
    for position, chunk in enumerate(chunks, 1):
        label = f"[{position}] {chunk.artifact_type}"
        if chunk.stage_key:
            label += f" (stage {chunk.stage_key})"
        if chunk.epic_id:
            label += f" (EPIC {chunk.epic_id})"
        parts.append(f"\n{label}\n{chunk.content.strip()}")
    return "\n".join(parts)


class ChatService:
    def __init__(self, store: ChunkStore, schema: str = "fe"):
        self.store = store
        self.schema = schema

    def ask(self, project_id: str, question: str, *,
            workspace_ids: list[str] | None = None,
            conversation_id: str | None = None,
            asked_by: str | None = None,
            route: bool = True) -> dict:
        started = time.perf_counter()
        conversation = conversation_id or uuid.uuid4().hex[:12]

        if not question or not question.strip():
            raise ValueError("a question is required")

        available = self.store.artifact_types(project_id)
        if not available:
            return self._record(
                project_id, conversation, question,
                REFUSAL.format(reason="Nothing is indexed for this project yet."),
                [], True, {}, workspace_ids, asked_by, started)

        types = available
        if route and len(available) > 2:
            from app.agentic_platform.fe_core.rag.bedrock import classify

            types = classify(question, available)

        result = retrieve(self.store, project_id, question,
                          workspace_ids=workspace_ids, artifact_types=types)

        if result.refused or not result.chunks:
            return self._record(
                project_id, conversation, question,
                REFUSAL.format(reason=result.reason or "No relevant excerpt."),
                [], True, {"searched_types": types,
                            "semantic_hits": result.semantic_hits,
                            "bm25_hits": result.bm25_hits}, workspace_ids,
                asked_by, started)

        from app.agentic_platform.fe_core.rag.bedrock import BedrockUnavailableError, answer

        try:
            text, usage = answer(build_prompt(question, result.chunks), SYSTEM)
        except BedrockUnavailableError as exc:
            # The chunks were found; only the answering model is unavailable. Hand
            # back the excerpts so the question is not a dead end.
            listing = "\n".join(
                f"[{i}] {c.artifact_type} - {c.source_path}"
                for i, c in enumerate(result.chunks, 1))
            return self._record(
                project_id, conversation, question,
                f"I found relevant excerpts but could not generate an answer: "
                f"{exc}\n\nRelevant documents:\n{listing}",
                result.chunks, False, {"error": str(exc)}, workspace_ids,
                asked_by, started)

        return self._record(project_id, conversation, question, text,
                            result.chunks, False,
                            {**usage, "searched_types": types,
                             "top_score": round(result.top_score, 5)},
                            workspace_ids, asked_by, started)

    def _record(self, project_id: str, conversation: str, question: str,
                text: str, chunks: list[RetrievedChunk], refused: bool,
                usage: dict, workspace_ids: list[str] | None,
                asked_by: str | None, started: float) -> dict:
        latency = int((time.perf_counter() - started) * 1000)
        citations = [c.citation() for c in chunks]
        workspace = (workspace_ids or [None])[0]

        try:
            self._persist(project_id, conversation, question, text, citations,
                          refused, usage, workspace, asked_by, latency)
        except Exception as exc:  # noqa: BLE001
            # History is useful, not load-bearing. Losing the answer because the
            # transcript could not be written would be the wrong trade.
            logger.warning("Could not persist chat turn: %s", exc)

        return {
            "conversation_id": conversation,
            "question": question,
            "answer": text,
            "refused": refused,
            "citations": citations,
            "latency_ms": latency,
            "model": usage.get("model"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "searched_types": usage.get("searched_types", []),
            "top_score": usage.get("top_score"),
        }

    def _persist(self, project_id, conversation, question, text, citations,
                 refused, usage, workspace, asked_by, latency) -> None:
        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                cur.execute(
                    "INSERT INTO fe_chat_message (project_id, workspace_id, "
                    "conversation_id, role, content, asked_by) "
                    "VALUES (%s,%s,%s,'user',%s,%s)",
                    (project_id, workspace, conversation, question, asked_by))
                cur.execute(
                    "INSERT INTO fe_chat_message (project_id, workspace_id, "
                    "conversation_id, role, content, citations, refused, model, "
                    "input_tokens, output_tokens, latency_ms, asked_by) "
                    "VALUES (%s,%s,%s,'assistant',%s,%s,%s,%s,%s,%s,%s,%s)",
                    (project_id, workspace, conversation, text,
                     json.dumps(citations), refused, usage.get("model"),
                     usage.get("input_tokens"), usage.get("output_tokens"),
                     latency, asked_by))

    def history(self, project_id: str, conversation_id: str | None = None,
                limit: int = 50) -> list[dict]:
        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
                if conversation_id:
                    cur.execute(
                        "SELECT role, content, citations, refused, created_at "
                        "FROM fe_chat_message WHERE project_id=%s AND "
                        "conversation_id=%s ORDER BY created_at LIMIT %s",
                        (project_id, conversation_id, limit))
                else:
                    cur.execute(
                        "SELECT role, content, citations, refused, created_at "
                        "FROM fe_chat_message WHERE project_id=%s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (project_id, limit))
                rows = cur.fetchall()
        return [
            {"role": r[0], "content": r[1], "citations": r[2] or [],
             "refused": r[3], "created_at": r[4].isoformat() if r[4] else None}
            for r in rows
        ]

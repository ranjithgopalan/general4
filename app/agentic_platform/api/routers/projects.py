"""Projects — one per the KB application (onboarding entry point).

`GET /projects` is what the console loads first: it answers "what can I work on,
and where has work started?". Every other view is scoped by the project chosen
here, then by a workspace within it.

Onboarding is `POST /projects` and does exactly one thing: open the project's
Global Workspace. The pipelines are shared templates, so there is nothing
per-project to copy — which is also why onboarding is idempotent and cheap.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Form, Query, UploadFile
from pydantic import BaseModel, Field

from app.agentic_platform.api.security import current_principal
from app.agentic_platform.fe_core.auth.roles import Principal
from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.kb.gateway import build_gateway
from app.agentic_platform.fe_core.projects.service import ProjectOnboardingError, ProjectService
from app.agentic_platform.fe_core.store import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])


def _service() -> ProjectService:
    settings = get_settings()
    return ProjectService(get_store(), build_gateway(settings),
                          settings.fe_pipeline_global)


@router.get("")
async def list_projects(
    principal: Principal = Depends(current_principal),
    lightweight: bool = Query(False, description="Skip per-project progress queries for faster list view"),
) -> dict:
    """the knowledge base's application catalogue joined with locally opened workspaces."""
    projects, warning = await _service().list_projects(lightweight=lightweight)
    payload = {
        "count": len(projects),
        "onboarded": sum(1 for p in projects if p.onboarded),
        "items": [p.to_dict() for p in projects],
    }
    if warning:
        # Say the list is partial. An empty or short list with no explanation
        # reads as "there are no projects", which is a different fact entirely.
        payload["degraded"] = warning
    return payload


@router.get("/{kb_application_id}")
async def get_project(
    kb_application_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    projects, warning = await _service().list_projects()
    match = next(
        (p for p in projects if p.kb_application_id == kb_application_id), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=(f"no project '{kb_application_id}'."
                    + (f" {warning}" if warning else "")),
        )
    body = match.to_dict()
    body["workspaces"] = [
        {
            "id": w.id, "tier": w.tier.value, "label": w.label,
            "epic_id": w.epic_id, "epic_title": w.epic_title,
            "status": w.status.value,
        }
        for w in get_store().list_workspaces(kb_application_id)
    ]
    return body


@router.delete("/{kb_application_id}", status_code=200)
async def delete_project(
    kb_application_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Delete a project's local state (workspaces, runs, artefacts, approvals).

    This removes everything this service has recorded for the project. The KB
    application entry in the knowledge base's catalogue is **not** touched —
    the project will reappear in the list from the catalogue and can be
    re-onboarded cleanly.

    Returns 404 when the project was never onboarded here (nothing to delete).
    """
    try:
        deleted = _service().delete(kb_application_id)
    except ProjectOnboardingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                f"project '{kb_application_id}' has no local state here "
                "(it may exist in the KB catalogue but was never onboarded)."
            ),
        )
    return {
        "kb_application_id": kb_application_id,
        "deleted": True,
        "workspaces_removed": deleted,
    }


class OnboardRequest(BaseModel):
    kb_application: str = Field(
        description=(
            "KB application name or id, as it appears in the knowledge base. This is the "
            "project: its code knowledge, business specs and schema all hang "
            "off it."
        ),
    )
    category: str = Field(
        default="brownfield",
        description=(
            "greenfield (no legacy system; requirements only) or brownfield "
            "(a legacy system exists). Recorded rather than inferred: for a "
            "greenfield project 'no code knowledge' is correct and expected, "
            "while for a brownfield one it means indexing has not finished."
        ),
    )
    intake_source: str = Field(
        default="existing-kb",
        description="requirements | reverse-engineering | git-repository | existing-kb | re-graph",
    )
    source_language: str | None = Field(
        default=None,
        description=(
            "Brownfield only: vb6-vba | vbscript-asp | mainframe. Recorded because "
            "'no code knowledge' then has a known cause instead of looking like an "
            "indexing failure."
        ),
    )
    target_framework: str | None = Field(
        default=None,
        description=(
            "angular-springboot | angular-fastapi. Decides which generators the "
            "code stages need, so a mismatch with the configured pipeline is "
            "reported here rather than eight stages later."
        ),
    )
    gear_id: str | None = Field(
        default=None,
        description=(
            "Application gear identifier for RE Graph context (e.g. 'japan'). "
            "If not provided, falls back to settings.GEAR_ID."
        ),
    )


@router.post("", status_code=201)
async def onboard_project(
    body: OnboardRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Onboard a project: resolve its KB application and open the Global Workspace.

    Idempotent — the Global Workspace id is derived from the application id, so
    onboarding twice returns the same project rather than creating a second.
    """
    from app.agentic_platform.fe_core.projects.intake import (
        IntakeError,
        IntakeSource,
        ProjectCategory,
        describe,
        validate,
    )

    # Validate the pairing before touching the KB: a greenfield project onboarded
    # from a reverse-engineering report describes a system that does not exist.
    try:
        category = ProjectCategory(body.category)
        source = IntakeSource(body.intake_source)
        validate(category, source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Parsed before onboarding so an unknown value is a 422 about the request
    # rather than a stack unwind from inside the store.
    try:
        from app.agentic_platform.fe_core.projects.stacks import SourceLanguage, TargetFramework

        language = SourceLanguage(body.source_language) if body.source_language else None
        target = TargetFramework(body.target_framework) if body.target_framework else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        summary = await _service().onboard(
            body.kb_application,
            intake_source=source.value,
            intake={
                "category": category.value,
                "source_language": language.value if language else None,
                "target_framework": target.value if target else None,
                "gear_id": body.gear_id,
            },
        )
    except ProjectOnboardingError as exc:
        # 422: the request named something that cannot be resolved. Distinguished
        # from 503 because retrying an unknown application will never succeed.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = summary.to_dict()
    payload["category"] = category.value
    payload["intake_source"] = source.value
    payload["intake_note"] = describe(category, source)

    # Source and target, plus whether the configured pipeline can actually build
    # that target. Reported at onboarding: discovering it at the API stage means
    # the wrong language has already been generated.
    if language or target:
        from app.agentic_platform.fe_core.pipeline.registry import get_pipeline
        from app.agentic_platform.fe_core.projects.stacks import SOURCES, TARGETS, alignment
        from app.agentic_platform.fe_core.projects.stacks import describe as describe_stack

        payload["source_language"] = language.value if language else None
        payload["target_framework"] = target.value if target else None
        payload["stack_note"] = describe_stack(language, target)
        if language:
            payload["source_profile"] = {
                "label": SOURCES[language].label,
                "extensions": list(SOURCES[language].extensions),
                "note": SOURCES[language].note,
            }
        if target:
            settings = get_settings()
            declared: set[str] = set()
            for name in (settings.fe_pipeline_global, settings.fe_pipeline_mini):
                try:
                    declared |= set(get_pipeline(name).required_plugins())
                except Exception:  # noqa: BLE001
                    pass
            missing = alignment(target, declared)
            payload["target_profile"] = {
                "label": TARGETS[target].label,
                "ui": TARGETS[target].ui,
                "api": TARGETS[target].api,
                "tdd_agent": TARGETS[target].tdd_agent,
                "note": TARGETS[target].note,
            }
            payload["agents_aligned"] = not missing
            payload["missing_generators"] = missing
            if missing:
                payload["alignment_warning"] = (
                    f"the configured pipeline does not declare {', '.join(missing)}, "
                    f"which {TARGETS[target].label} needs. The code stages will "
                    "fail until those generators exist and the pipeline names them."
                )
    return payload


_embedding_drains: dict[str, "threading.Thread"] = {}


def _start_embedding_drain(settings, project_id: str) -> None:
    """Embed a project's remaining chunks in a daemon thread, one drain per project."""
    import threading  # noqa: PLC0415

    live = _embedding_drains.get(project_id)
    if live is not None and live.is_alive():
        return

    def _run() -> None:
        try:
            from app.agentic_platform.fe_core.rag.indexer import Indexer  # noqa: PLC0415
            from app.agentic_platform.fe_core.rag.retriever import ChunkStore  # noqa: PLC0415

            store = ChunkStore(settings.fe_db_url, settings.fe_db_schema)
            n = Indexer(store).embed_all(project_id)
            logger.info("Embedding drain finished for %s: %d chunk(s)", project_id, n)
        except Exception:  # noqa: BLE001
            logger.exception("Embedding drain failed for %s", project_id)

    t = threading.Thread(target=_run, name=f"embed-drain-{project_id}", daemon=True)
    _embedding_drains[project_id] = t
    t.start()


@router.post("/{kb_application_id}/documents", status_code=202)
async def upload_reverse_engineering_document(
    kb_application_id: str,
    file: UploadFile = File(...),
    document_kind: str = Form(
        default="reverse-engineering",
        description=("requirements | reverse-engineering | business-spec | schema-export | "
                     "target-architecture | sp-catalog | re-cards (module JSON) | screens (Word)"),
    ),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Seed a project's knowledge with an uploaded document.

    The second onboarding path. Linking an existing the KB application assumes
    the legacy system has already been reverse-engineered into the KB; for a
    project where it has not, this is how the starting knowledge arrives.

    Ingested at authority tier **C**, not D: a reverse-engineering report is
    documentary evidence produced by a tool against real source, which outranks
    unverified LLM output. It is still below A/B, because it is a derived summary
    rather than the source itself.

    Returns 202: the document is handed to the knowledge base and ingestion (chunking and
    embedding) continues asynchronously. Blocking here would make a slow KB look
    like a failed upload.
    """
    from app.agentic_platform.fe_core.rag.extract import UnreadableUpload, extract_text

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")

    # Resolved against projects onboarded HERE first, and only then against the
    # catalogue. The order matters: this document is the project's knowledge, so
    # requiring the project to already exist in a reverse-engineering catalogue
    # refused the upload for exactly the projects that need it most -- a greenfield
    # project is never in that catalogue, and a brownfield one being seeded from a
    # RED report is not in it yet either.
    from app.agentic_platform.fe_core.projects.models import local_project_id
    from app.agentic_platform.fe_core.workspaces.models import global_workspace_id

    store_ = get_store()
    # Resolve the workspace the same way onboarding derived its id (a lowercase
    # slug), so a name with uppercase or underscores ("Ranjith_new" ->
    # "ranjith-new") still finds its Global Workspace instead of falling through to
    # the KB catalogue (which, when down, stalls on the read timeout then 404s).
    # The raw-id lookup is kept as a fallback for any project stored under it.
    _local_id = local_project_id(kb_application_id)
    workspace = (store_.get_workspace(global_workspace_id(_local_id))
                 or store_.get_workspace(global_workspace_id(kb_application_id)))
    project_id = workspace.kb_application_id if workspace is not None else None

    if project_id is None:
        onboarded = sorted(
            w.kb_application_id for w in store_.list_workspaces() if w.is_global)
        raise HTTPException(
            status_code=404,
            detail=(
                f"no project '{kb_application_id}' found locally. "
                f"Onboarded projects: "
                    f"{', '.join(onboarded) or '(none)'}. Onboard it first — the "
                    "upload attaches to a project, so there has to be one."
                ),
            )

    # ------------------------------------------------------------------
    # WP1: fast-path for new structured document kinds that skip RAG indexing
    # ------------------------------------------------------------------
    if document_kind == "re-cards":
        return await _handle_re_cards(
            raw=raw, file=file, project_id=project_id,
            workspace=workspace, store_=store_, principal=principal,
        )
    if document_kind == "screens":
        return await _handle_screens(
            raw=raw, file=file, project_id=project_id,
            workspace=workspace, store_=store_, principal=principal,
        )

    # Text extraction comes after the structured kinds above, which read the raw
    # bytes themselves.
    # .docx is unwrapped rather than refused: a RED report is delivered as one, and
    # "not UTF-8" was true about the bytes and useless about the intent.
    # Off the event loop: extraction is CPU-bound and a large .docx blocked every
    # other request (including the dashboard's project list) while it ran.
    try:
        text, how = await asyncio.to_thread(extract_text, raw, file.filename or "upload")
    except UnreadableUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Index into THIS service's corpus first, because that is what the chat and
    # the stage prompts actually read. Publishing onward to the knowledge base is
    # a mirror, and a platform version without an ingest endpoint must not make an
    # upload fail -- the document is usable here either way.
    source_path = f"upload://{document_kind}/{file.filename or 'document'}"
    indexed = {"chunks": 0, "embedded": 0, "detail": ""}

    # Keep the raw upload as corpus for the reverse-engineering (card KB) pipeline:
    # workspaces/<app>/corpus/<kind>/<filename>. The chunk index below is what
    # chat and stage prompts read; the card KB builder needs the source itself
    # (page/section loci) to write evidence-anchored cards. Non-fatal.
    try:
        corpus_dir = get_settings().corpus_root_for(project_id) / document_kind
        corpus_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(file.filename or "document").name
        (corpus_dir / safe_name).write_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not keep raw upload as corpus: %s", exc)
    try:
        from app.agentic_platform.fe_core.rag.chunker import chunk as chunk_text
        from app.agentic_platform.fe_core.rag.indexer import Indexer
        from app.agentic_platform.fe_core.rag.retriever import ChunkStore

        settings = get_settings()
        if not settings.uses_database():
            indexed["detail"] = (
                "not indexed: retrieval needs Postgres. Set FE_DB_URL and run "
                "`python run.py db-apply`."
            )
        else:
            # A large RED document chunks into tens of thousands of rows and
            # then embeds them via Bedrock — minutes of CPU and I/O. Run it in
            # a worker thread: done inline it pins the event loop and the whole
            # API (health checks included) goes silent until the upload ends.
            def _index_sync() -> tuple[int, int, str]:
                store = ChunkStore(settings.fe_db_url, settings.fe_db_schema)
                chunks = chunk_text(text, document_kind, source_path)
                written = store.replace_chunks(
                    project_id=project_id,
                    source_path=source_path,
                    chunks=chunks,
                    artifact_type=document_kind,
                )
                try:
                    embedded = Indexer(store).embed_pending(project_id)
                    return written, embedded, ""
                except Exception as exc:  # noqa: BLE001
                    # Chunks without embeddings are still found by keyword
                    # search, so this degrades retrieval, not the upload.
                    return written, 0, f"embedding deferred: {exc}"

            written, embedded, deferred = await asyncio.to_thread(_index_sync)
            indexed["chunks"] = written
            indexed["embedded"] = embedded
            if deferred:
                indexed["detail"] = deferred
            elif embedded and embedded < written:
                # The first pass is capped so this response returns in bounded
                # time; the rest is drained in a daemon thread. Semantic search
                # covers more of the document as it progresses; keyword search
                # covers all of it from the start.
                _start_embedding_drain(settings, project_id)
                indexed["detail"] = (
                    f"{embedded} of {written} chunks embedded; the remainder is "
                    "being embedded in the background")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Local indexing failed for %s", file.filename)
        indexed["detail"] = f"indexing failed: {exc}"

    # Durable copy: raw + canonical Markdown into the ArtifactStore as an APPROVED
    # `input-<kind>` artefact (tier C) and an `intake_ready` outbox event, so every
    # stage can materialise the document and an orchestrator can start from it.
    stored: dict = {"ok": False, "detail": ""}
    try:
        from app.agentic_platform.fe_core.artifacts.intake import register_input_document  # noqa: PLC0415

        settings = get_settings()
        art = await asyncio.to_thread(
            register_input_document,
            store=store_, settings=settings, project_id=project_id,
            workspace_id=(workspace.id if workspace is not None else global_workspace_id(project_id)),
            pipeline=(workspace.pipeline if workspace is not None else settings.fe_pipeline_global),
            filename=file.filename or "document", document_kind=document_kind, raw=raw,
            canonical_md=text, extracted_as=how, uploaded_by=principal.email or principal.subject,
        )
        stored = {"ok": True, "artifact_id": art.id, "artifact_type": art.artifact_type,
                  "version": art.version, "content_uri": art.content_uri, "storage_kind": art.storage_kind}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Intake artefact registration failed for %s", file.filename)
        stored["detail"] = f"not stored as artefact: {exc}"

    if not indexed["chunks"] and not stored["ok"]:
        # Nothing anywhere is a genuine failure.
        raise HTTPException(
            status_code=503,
            detail=(
                f"'{file.filename}' was read ({len(text):,} chars via {how}) but "
                f"stored nowhere. {indexed['detail']}"
            ),
        )

    return {
        "kb_application_id": project_id,
        "filename": file.filename,
        "document_kind": document_kind,
        "characters": len(text),
        "extracted_as": how,
        "authority_tier": "C",
        "indexed": indexed,
        "stored": stored,
    }


def _resolve_onboarded(kb_application_id: str):
    """(workspace, project_id) for an onboarded project, resolved the way onboarding
    derived its id (a lowercase slug). 404 when the project is not onboarded here."""
    from app.agentic_platform.fe_core.projects.models import local_project_id  # noqa: PLC0415
    from app.agentic_platform.fe_core.workspaces.models import global_workspace_id  # noqa: PLC0415

    store_ = get_store()
    workspace = (store_.get_workspace(global_workspace_id(local_project_id(kb_application_id)))
                 or store_.get_workspace(global_workspace_id(kb_application_id)))
    if workspace is None:
        onboarded = sorted(w.kb_application_id for w in store_.list_workspaces() if w.is_global)
        raise HTTPException(
            status_code=404,
            detail=(f"no project '{kb_application_id}' found locally. Onboarded projects: "
                    f"{', '.join(onboarded) or '(none)'}. Onboard it first."),
        )
    return workspace, workspace.kb_application_id


def _safe_file_stem(text: str) -> str:
    stem = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in (text or "").strip())
    return stem.strip("-.") or "module"


def _stored_modules(corpus_dir: Path) -> list[tuple[Path, dict]]:
    """(file, normalised doc) for every readable module JSON already in the corpus."""
    import json as _json  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.cards_schema import normalise  # noqa: PLC0415

    out: list[tuple[Path, dict]] = []
    if not corpus_dir.is_dir():
        return out
    for path in sorted(corpus_dir.glob("*.json")):
        try:
            doc, violations, _ = normalise(_json.loads(path.read_text(encoding="utf-8-sig")))
        except Exception:  # noqa: BLE001 — an unreadable file is reported by the build
            continue
        if not violations:
            out.append((path, doc))
    return out


def _known_pages(settings, project_id: str) -> list[str]:
    """ASP page names the project's module JSON(s) define."""
    pages: list[str] = []
    for _path, doc in _stored_modules(settings.corpus_root_for(project_id) / "re-cards"):
        for page in doc.get("pages", []):
            name = page.get("page", "")
            if name and name not in pages:
                pages.append(name)
    return pages


async def _handle_re_cards(
    *, raw: bytes, file, project_id: str, workspace, store_, principal,
    emit_event: bool = True,
) -> dict:
    """Handle document_kind=re-cards: the module JSON.

    Accepts the RED JSON (one section per ASP file; doc/RED-Company-Search.json)
    or the cards JSON. Parsing is procedural; when it cannot read the upload,
    the LLM fallback proposes a field mapping that the same code applies and
    validates (fe_core.kb.llm_fallback). The module is then checked against the
    modules already uploaded: card ids and ASP page names must not clash. A
    module uploaded again replaces its earlier file.
    """
    import json as _json  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.cards_schema import (  # noqa: PLC0415
        iter_all_cards, merge_modules, module_key_of,
    )
    from app.agentic_platform.fe_core.kb.llm_fallback import normalise_with_fallback  # noqa: PLC0415

    try:
        data = _json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"re-cards file is not valid JSON: {exc}") from exc

    settings = get_settings()
    parsed = await asyncio.to_thread(normalise_with_fallback, data, settings=settings)
    if parsed["violations"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "module JSON validation failed",
                    "violations": parsed["violations"], "fallback": parsed["notes"]},
        )
    doc = parsed["doc"]
    fallback_used = parsed["parsed_by"] == "llm-fallback"
    safe_name = Path(file.filename or "module.json").name
    module_key = _safe_file_stem(module_key_of(doc, Path(safe_name).stem))

    corpus_root = settings.corpus_root_for(project_id)
    corpus_dir = corpus_root / "re-cards"
    stored_modules = await asyncio.to_thread(_stored_modules, corpus_dir)
    replaced = [p for p, d in stored_modules
                if _safe_file_stem(module_key_of(d, p.stem)) == module_key]
    others = [d for p, d in stored_modules if p not in replaced]
    merged, clashes, _notes = merge_modules([*others, doc])
    if clashes:
        raise HTTPException(
            status_code=422,
            detail={"message": "the module clashes with modules already uploaded",
                    "violations": clashes},
        )

    target = corpus_dir / f"{module_key}.json"
    try:
        corpus_dir.mkdir(parents=True, exist_ok=True)
        for old in replaced:
            if old != target:
                old.unlink(missing_ok=True)
        if fallback_used:
            # The build reads the converted file; the upload itself is kept beside it.
            target.write_text(_json.dumps(parsed["red_json"], indent=1), encoding="utf-8")
            original_dir = corpus_root / "re-cards-original"
            original_dir.mkdir(parents=True, exist_ok=True)
            (original_dir / safe_name).write_bytes(raw)
            (original_dir / f"{module_key}.mapping.json").write_text(
                _json.dumps(parsed["mapping"], indent=2), encoding="utf-8")
        else:
            target.write_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not store the module JSON")
        raise HTTPException(status_code=503, detail=f"could not store the module JSON: {exc}") from exc

    # Register artifact (no chunk indexing)
    from app.agentic_platform.fe_core.workspaces.models import global_workspace_id  # noqa: PLC0415
    stored: dict = {"ok": False, "detail": "", "file": target.name,
                    "replaced": [p.name for p in replaced]}
    try:
        from app.agentic_platform.fe_core.artifacts.intake import register_input_document  # noqa: PLC0415
        art = await asyncio.to_thread(
            register_input_document,
            store=store_, settings=settings, project_id=project_id,
            workspace_id=(workspace.id if workspace is not None else global_workspace_id(project_id)),
            pipeline=(workspace.pipeline if workspace is not None else settings.fe_pipeline_global),
            filename=safe_name, document_kind="re-cards", raw=raw,
            canonical_md=target.read_text(encoding="utf-8-sig", errors="replace"),
            extracted_as="json+llm-fallback" if fallback_used else "json",
            uploaded_by=principal.email or principal.subject, emit_event=emit_event,
        )
        stored.update({"ok": True, "artifact_id": art.id, "artifact_type": art.artifact_type,
                       "version": art.version, "content_uri": art.content_uri})
    except Exception as exc:  # noqa: BLE001
        logger.exception("re-cards artifact registration failed")
        stored["detail"] = str(exc)

    cards = iter_all_cards(doc)
    reply = {
        "kb_application_id": project_id,
        "filename": file.filename,
        "document_kind": "re-cards",
        "module": doc.get("module", ""),
        "module_key": module_key,
        "format": doc.get("source_format") or "cards-json",
        "parsed_by": parsed["parsed_by"],
        "pages": len(doc.get("pages", [])),
        "cards_total": len(cards),
        "cards": dict(Counter(c["id"].split("-")[0] for c in cards)),
        "edges": len(doc.get("edges", [])),
        "warnings": parsed["warnings"],
        "modules_in_corpus": len(merged.get("modules", [])),
        "authority_tier": "C",
        "stored": stored,
    }
    if fallback_used:
        reply["fallback"] = {"mapping": parsed["mapping"], "notes": parsed["notes"]}
    return reply


def _screens_reply(project_id: str, filename: str | None, catalog: dict, stored: dict) -> dict:
    bound_files = {img.get("file") or img.get("local_path")
                   for sc in catalog.get("screens", []) for img in sc.get("images", [])}
    return {
        "kb_application_id": project_id,
        "filename": filename,
        "document_kind": "screens",
        "catalog_version": catalog.get("version"),
        "pages": sum(1 for sc in catalog.get("screens", []) if sc.get("images")),
        "images_bound": len(bound_files),
        "images_unbound": len(catalog.get("unbound", [])),
        "authority_tier": "C",
        "stored": stored,
        "report": catalog.get("report", []),
    }


async def _handle_screens(
    *, raw: bytes, file, project_id: str, workspace, store_, principal,
    emit_event: bool = True,
) -> dict:
    """Handle document_kind=screens: extract pictures, bind them to ASP pages, write the catalogue.

    Pictures are bound by the `File name - X.asp` lines of the document. A
    picture no such line claims is offered to the LLM fallback, which reads the
    text around it; whatever it binds is marked for review in the report.
    Uploading the same document again returns the catalogue already written.
    """
    import hashlib  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb import llm_fallback  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.screens import build_catalog  # noqa: PLC0415
    from app.agentic_platform.fe_core.rag.extract import _docx_extract_images, UnreadableUpload  # noqa: PLC0415

    settings = get_settings()
    name = Path(file.filename or "screens.docx").name
    digest = hashlib.sha256(raw).hexdigest()
    screens_root = settings.corpus_root_for(project_id) / "screens"

    existing: dict[int, dict] = {}
    if screens_root.is_dir():
        for path in screens_root.glob("catalog-v*.json"):
            try:
                cat = _json.loads(path.read_text(encoding="utf-8"))
                existing[int(cat.get("version") or 0)] = cat
            except Exception:  # noqa: BLE001
                continue
    for _version, cat in sorted(existing.items(), reverse=True):
        if cat.get("source_sha256") == digest:
            return _screens_reply(project_id, file.filename, cat, {
                "ok": True, "reused": True,
                "detail": "this screens document is already in the catalogue; nothing was extracted again"})

    on_disk = [int(p.name[1:]) for p in screens_root.glob("v*")
               if p.is_dir() and p.name[1:].isdigit()] if screens_root.is_dir() else []
    version = max([*existing, *on_disk], default=0) + 1
    img_dir = screens_root / f"v{version}"

    # Extract images off the event loop (CPU-bound)
    try:
        images = await asyncio.to_thread(_docx_extract_images, raw, name, img_dir)
    except UnreadableUpload as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    notes: list[str] = []
    if any(not img.get("page_set") for img in images):
        known = await asyncio.to_thread(_known_pages, settings, project_id)
        notes = await asyncio.to_thread(
            llm_fallback.bind_unbound_images, images, known, settings=settings)

    catalog = build_catalog(images, app=project_id, version=version)
    catalog["source_filename"] = name
    catalog["source_sha256"] = digest
    catalog["report"].extend(n for n in notes if n not in catalog["report"])
    screens_root.mkdir(parents=True, exist_ok=True)
    (screens_root / f"catalog-v{version}.json").write_text(
        _json.dumps(catalog, indent=2), encoding="utf-8")

    # Register artifact (raw docx). The pictures travel with the built `kb` artefact.
    from app.agentic_platform.fe_core.workspaces.models import global_workspace_id  # noqa: PLC0415
    stored: dict = {"ok": True, "path": str(img_dir), "artifact": {"ok": False}}
    try:
        from app.agentic_platform.fe_core.artifacts.intake import register_input_document  # noqa: PLC0415
        art = await asyncio.to_thread(
            register_input_document,
            store=store_, settings=settings, project_id=project_id,
            workspace_id=(workspace.id if workspace is not None else global_workspace_id(project_id)),
            pipeline=(workspace.pipeline if workspace is not None else settings.fe_pipeline_global),
            filename=name, document_kind="screens", raw=raw,
            canonical_md=_json.dumps(catalog), extracted_as="screens-docx",
            uploaded_by=principal.email or principal.subject, emit_event=emit_event,
        )
        stored["artifact"] = {"ok": True, "artifact_id": art.id, "version": art.version}
    except Exception as exc:  # noqa: BLE001
        logger.exception("screens artifact registration failed")
        stored["artifact"]["detail"] = str(exc)

    return _screens_reply(project_id, file.filename, catalog, stored)


# ---------------------------------------------------------------------------
# ASP source: the only upload a project needs once the global library is built
# ---------------------------------------------------------------------------

def _asp_source_view(project_id: str) -> dict:
    """The project's uploaded ASP files, matched against the approved library build."""
    import json as _json  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb import library  # noqa: PLC0415

    settings = get_settings()
    asp_dir = settings.corpus_root_for(project_id) / library.ASP_SOURCE_KIND
    files = sorted((p for p in asp_dir.iterdir() if p.is_file()), key=lambda p: p.name.lower()) \
        if asp_dir.is_dir() else []

    view: dict = {
        "kb_application_id": project_id,
        "files": [{"name": p.name, "size": p.stat().st_size} for p in files],
        "library": {"available": False},
        "matched": [], "unmatched": [], "ignored": [],
        "cards_selected": 0,
        "pinned_library_version": None,
        "library_stale": False,
        "ready": False,
    }

    provenance = settings.kb_root_for(project_id) / "_provenance.json"
    if provenance.is_file():
        try:
            pinned = (_json.loads(provenance.read_text(encoding="utf-8")).get("library") or {})
            view["pinned_library_version"] = pinned.get("version")
        except Exception:  # noqa: BLE001
            pass

    ref = library.materialise_library(get_store(), settings)
    if ref is None:
        return view
    page_index, _merged = library.load_library(ref["path"])
    resolution = library.resolve_files([p.name for p in files], page_index)
    shared: set[str] = set()
    page_cards = 0
    for match in resolution["matched"]:
        entry = page_index.get("pages", {}).get(match["page_norm"], {})
        page_cards += len(entry.get("card_ids", []))
        shared.update(entry.get("shared_refs", []))
    view.update({
        "library": {"available": True, "version": ref["version"], "artifact_id": ref["artifact_id"]},
        **resolution,
        "cards_selected": page_cards + len(shared),
        "library_stale": (view["pinned_library_version"] is not None
                          and view["pinned_library_version"] != ref["version"]),
        "ready": bool(resolution["matched"]),
    })
    return view


@router.post("/{kb_application_id}/asp-source", status_code=202)
async def upload_asp_source(
    kb_application_id: str,
    files: list[UploadFile] = File(..., description="The project's ASP files (.asp, .asa, .inc)."),
    start: bool = Form(default=True, description="Queue the Global thread once the files are stored."),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Upload a project's ASP files. This is the project's only input.

    The cards and screenshots live in the global library (POST /library/...).
    Each uploaded file name is matched, ignoring case and folder, against the
    library's page index. G1 then builds the project knowledge base from the
    cards of the matched pages. An ASP file the library has no cards for is a
    warning; the upload is refused only when nothing matches, or when no library
    build has been approved yet.
    """
    import hashlib  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb import library  # noqa: PLC0415

    workspace, project_id = _resolve_onboarded(kb_application_id)
    if library.is_library(project_id):
        raise HTTPException(status_code=422, detail="the global library takes documents, not ASP files")

    settings = get_settings()
    store_ = get_store()
    ref = await asyncio.to_thread(library.materialise_library, store_, settings)
    if ref is None:
        raise HTTPException(status_code=422, detail={
            "code": "no_library",
            "message": ("no approved build of the global library is available. Upload the module "
                        "JSON and the screens document to the library, build it and approve the "
                        "build, then upload the ASP files again."),
        })

    asp_dir = settings.corpus_root_for(project_id) / library.ASP_SOURCE_KIND
    asp_dir.mkdir(parents=True, exist_ok=True)
    received: list[dict] = []
    rejected: list[str] = []
    for upload in files:
        name = Path(upload.filename or "").name
        raw = await upload.read()
        if not name or not raw or Path(name).suffix.lower() not in library.ASP_EXTENSIONS:
            rejected.append(name or "(unnamed)")
            continue
        target = asp_dir / name
        replaced = target.exists()
        # Raw bytes: classic ASP is often not UTF-8, and only the name is matched.
        target.write_bytes(raw)
        received.append({"name": name, "size": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest(), "replaced": replaced})

    view = await asyncio.to_thread(_asp_source_view, project_id)
    if not view["matched"]:
        raise HTTPException(status_code=422, detail={
            "code": "no_match",
            "message": (f"none of the {len(view['files'])} uploaded file(s) has cards in the "
                        f"global library (version {ref['version']})."),
            "unmatched": view["unmatched"], "ignored": view["ignored"], "rejected": rejected,
        })

    # One artefact and one start event for the whole set, however many files.
    manifest = {
        "files": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in sorted(asp_dir.iterdir(), key=lambda p: p.name.lower()) if p.is_file()],
        "library": {"version": ref["version"], "artifact_id": ref["artifact_id"]},
        "matched": [m["page"] for m in view["matched"]],
        "unmatched": view["unmatched"],
        "ignored": view["ignored"],
    }
    summary_md = "\n".join([
        "# ASP source", "",
        f"Global library version {ref['version']}.", "",
        "| File | Result |", "|---|---|",
        *[f"| {m['file']} | matched ({m['cards']} cards) |" for m in view["matched"]],
        *[f"| {n} | no cards in the library |" for n in view["unmatched"]],
        *[f"| {n} | ignored |" for n in view["ignored"]],
    ])
    stored: dict = {"ok": False, "detail": ""}
    try:
        from app.agentic_platform.fe_core.artifacts.intake import register_input_document  # noqa: PLC0415
        art = await asyncio.to_thread(
            register_input_document,
            store=store_, settings=settings, project_id=project_id,
            workspace_id=workspace.id, pipeline=workspace.pipeline,
            filename="asp-source.json", document_kind=library.ASP_SOURCE_KIND,
            raw=_json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8"),
            canonical_md=summary_md, extracted_as="asp-source manifest",
            uploaded_by=principal.email or principal.subject, emit_event=start,
        )
        stored = {"ok": True, "artifact_id": art.id, "version": art.version}
    except Exception as exc:  # noqa: BLE001
        logger.exception("asp-source artifact registration failed")
        stored["detail"] = str(exc)

    return {
        **view,
        "received": received,
        "rejected": rejected,
        "library_version": ref["version"],
        "started": bool(start and stored["ok"]),
        "stored": stored,
    }


def _asp_source_graph(project_id: str, gear_id: str | None, version: str | None) -> dict:
    """The project's uploaded ASP files placed on the Gear ID's dependency graph (fe_kb_* tables),
    with their direct neighbours, the module matrix of that subset, and each page's library cards."""
    from app.agentic_platform.api.routers import library as library_router  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb import library as kb_library  # noqa: PLC0415
    from app.agentic_platform.fe_core.kb.cards_schema import normalise_page  # noqa: PLC0415
    from app.agentic_platform.fe_core.red_graph import kb_graph, library_cards  # noqa: PLC0415

    settings = get_settings()
    asp_dir = settings.corpus_root_for(project_id) / kb_library.ASP_SOURCE_KIND
    files = sorted((p for p in asp_dir.iterdir() if p.is_file()), key=lambda p: p.name.lower()) if asp_dir.is_dir() else []

    dao = library_router._kb_dao()
    row = library_router._deps_version_row(dao, gear_id, version)
    kb_version = row["kb_version"]
    nodes = dao.nodes(kb_version)
    edges = dao.edges(kb_version)
    by_norm: dict[str, list[dict]] = {}
    for n in nodes:
        by_norm.setdefault(normalise_page(n.get("label") or ""), []).append(n)

    project_ids: set[str] = set()
    matched: dict[str, list[str]] = {}
    unmatched: list[str] = []
    ignored: list[str] = []
    for f in files:
        norm = normalise_page(f.name)
        hits = by_norm.get(norm, [])
        if hits:
            matched[f.name] = [n["id"] for n in hits]
            project_ids.update(n["id"] for n in hits)
        elif norm.endswith(".asp"):
            unmatched.append(f.name)
        else:
            ignored.append(f.name)

    sub_edges = [e for e in edges if e["from_id"] in project_ids or e["to_id"] in project_ids]
    neighbour_ids = {e["from_id"] for e in sub_edges} | {e["to_id"] for e in sub_edges}
    neighbour_ids -= project_ids
    node_map = {n["id"]: n for n in nodes}

    store = get_store()
    library_info = library_cards.library_page_data(settings, store, dao)
    pages: list[dict] = []
    for nid in sorted(project_ids, key=lambda i: node_map[i]["label"].lower()):
        dto = kb_graph.node_dto(node_map[nid])
        dto["in_project"] = True
        summary = library_cards.cards_summary(settings, store, dto["file"], dao) if library_info else None
        if summary:
            dto.update({"cards": summary.get("counts") or {}, "cards_found": summary.get("found"), "module_key": summary.get("module_key"),
                        "library_module": summary.get("module"), "library_submodule": summary.get("submodule"),
                        "library_domain": summary.get("domain"), "shared_entities": summary.get("shared_entities")})
        pages.append(dto)
    neighbours = []
    for nid in sorted(neighbour_ids, key=lambda i: node_map[i]["label"].lower()):
        dto = kb_graph.node_dto(node_map[nid])
        dto["in_project"] = False
        neighbours.append(dto)

    shown = project_ids | neighbour_ids
    mod_of = {nid: (node_map[nid].get("category") or "") for nid in shown}
    modules: dict[str, dict] = {}
    for nid in shown:
        m = modules.setdefault(mod_of[nid], {"module": mod_of[nid], "files": 0, "loc": 0, "project_files": 0})
        m["files"] += 1
        m["loc"] += int(((node_map[nid].get("metadata") or {}).get("loc")) or 0)
        if nid in project_ids:
            m["project_files"] += 1
    pairs: dict[tuple[str, str], int] = {}
    for e in sub_edges:
        key = (mod_of[e["from_id"]], mod_of[e["to_id"]])
        pairs[key] = pairs.get(key, 0) + 1

    prefix = kb_graph.library_deps_prefix(gear_id)
    current = dao.active_version(prefix)
    return {
        "kb_application_id": project_id, "gear_id": gear_id or "", "kb_version": kb_version,
        "version": kb_graph.version_number(kb_version), "is_current": kb_version == current,
        "built_at": row["build_date"].isoformat() if row.get("build_date") else None,
        "files": [{"name": p.name, "size": p.stat().st_size} for p in files],
        "matched": matched, "unmatched": unmatched, "ignored": ignored,
        "pages": pages, "neighbours": neighbours, "edges": [kb_graph.edge_dto(e) for e in sub_edges],
        "modules": sorted(modules.values(), key=lambda m: m["module"]),
        "pairs": [{"source": a, "target": b, "count": c} for (a, b), c in sorted(pairs.items(), key=lambda kv: -kv[1])],
        "library": {"available": library_info is not None, "source": (library_info or {}).get("source"),
                    "version": (library_info or {}).get("version")},
    }


@router.get("/{kb_application_id}/asp-source/graph")
async def asp_source_graph(
    kb_application_id: str,
    gear_id: str | None = Query(default=None, description="Gear ID whose dependency graph to use (default: the library's default gear)"),
    version: str | None = Query(default=None, description="dependency-graph version (number or kb_version); default current"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """The project's ASP files on the Gear ID's dependency graph: which modules, sub-modules and
    domains they connect to, their direct neighbours, and the Global Library cards of each page."""
    _workspace, project_id = _resolve_onboarded(kb_application_id)
    return await asyncio.to_thread(_asp_source_graph, project_id, gear_id, version)


@router.get("/{kb_application_id}/asp-source")
async def asp_source_status(
    kb_application_id: str,
    principal: Principal = Depends(current_principal),
) -> dict:
    """The project's ASP files and how they match the approved library build.

    `library_stale` is true when a newer library build has been approved since
    the project's knowledge base was built; run the project's G1 again to pick it up.
    """
    _workspace, project_id = _resolve_onboarded(kb_application_id)
    return await asyncio.to_thread(_asp_source_view, project_id)


class RepositoryRequest(BaseModel):
    git_url: str = Field(
        description=(
            "https or scp-style ssh URL. Other schemes are refused because a "
            "clone executes remote configuration in some setups."
        ),
    )
    branch: str | None = Field(default=None, description="Defaults to the remote HEAD.")


@router.post("/{kb_application_id}/repository", status_code=202)
async def attach_repository(
    kb_application_id: str,
    body: RepositoryRequest = Body(...),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Register a brownfield project's source repository.

    This records the repository as a KB source and returns 202. It does **not**
    index the code: that is the knowledge base's RED engine, which parses VB6/ASP/COBOL/Java and
    writes code knowledge. Until RED has run, the G1 PRD will report the
    code surface as unavailable rather than inferring a current state -- which is
    the honest outcome, and the reason the repository is recorded with its
    indexing status rather than silently accepted.
    """
    from app.agentic_platform.fe_core.projects.intake import IntakeError, normalise_git_url

    try:
        url = normalise_git_url(body.git_url)
    except IntakeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raise HTTPException(
        status_code=501,
        detail=(
            f"Repository registration via the KB REST API is not available. "
            f"Validated URL: {url}. Configure the KB backend to use this feature."
            ),
        )

"""G1 Impact Analysis -- correlate three KB surfaces before anything is designed.

PRD 8.1 G1: *"Nothing today reads VB5/ASP code knowledge and the MS SQL schema
together to size a change."* the knowledge base's RED engine analyses code, GATHER-db captures
schema, and the KB holds the business specs -- but no component joins them.

This is a builtin rather than a plugin skill because it is a read-and-correlate
step over three sources, not a document generator working from one upstream
artefact. Its whole value is the join, and a plugin skill given one prompt could
not verify that all three sources were actually present.

The output is the ground truth for the PRD (G2). If it is wrong, every document
below it inherits the error, so it reports **coverage explicitly**: which of the
three surfaces answered, and which did not. A blank section is stated as blank
rather than quietly omitted.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: The three surfaces the analysis must join (drawio tab 2, "READ AT THE START").
SURFACES = ("code_knowledge", "business_specs", "schema")


@dataclass
class SurfaceResult:
    """What one KB surface returned, and whether it is usable."""

    name: str
    available: bool
    detail: str = ""
    content: str = ""

    @property
    def status(self) -> str:
        return "available" if self.available else "UNAVAILABLE"


@dataclass
class ImpactInputs:
    surfaces: list[SurfaceResult] = field(default_factory=list)

    @property
    def available(self) -> list[SurfaceResult]:
        return [s for s in self.surfaces if s.available]

    @property
    def missing(self) -> list[SurfaceResult]:
        return [s for s in self.surfaces if not s.available]

    @property
    def can_correlate(self) -> bool:
        """A correlation needs at least two surfaces to correlate *between*.

        One surface is a summary, not an impact analysis, and labelling it as one
        would give the PRD false grounding.
        """
        return len(self.available) >= 2


async def gather_inputs(kb, app_id: str, *, datasource: str | None = None,
                        store=None, settings=None) -> ImpactInputs:
    """Read all three surfaces, recording failures instead of raising.

    A missing surface must not abort the stage: an analysis over two of three is
    still useful, provided the gap is stated. What must never happen is silently
    producing a confident document from one surface.

    Each KB surface is tried in order:
      1. KB REST API at kb_base_url (lmod at localhost:8000) -- the primary source
         when documents have been indexed there by the RED engine.
      2. Local Postgres RAG store -- the fallback for projects onboarded via the
         "Reverse-engineering report" or "Requirements" upload path, where the
         document lands in fe.fe_kb_chunk rather than the KB catalogue.
    """
    inputs = ImpactInputs()

    # 1. Legacy code knowledge (the knowledge base RED: VB5/VB6 + Classic ASP).
    inputs.surfaces.append(await _ask(
        kb, app_id, "code_knowledge",
        "What does the existing VB5/VB6 and Classic ASP code do? List the "
        "screens, business rules, decision logic and data operations it "
        "implements.",
        policy="modernization",
        settings=settings,
    ))

    # 2. Business specifications uploaded by the analyst.
    inputs.surfaces.append(await _ask(
        kb, app_id, "business_specs",
        "What do the uploaded business specifications require of the credit "
        "risk underwriting process? List stated requirements and business "
        "rules.",
        policy="developer",
        settings=settings,
    ))

    # 3. MS SQL schema -- metadata only, never business data (NFR-1).
    inputs.surfaces.append(_read_schema(store, settings, datasource))
    return inputs


async def _ask(kb, app_id: str, name: str, question: str,
               policy: str, *, settings=None) -> SurfaceResult:
    """Query one KB surface via the local Postgres RAG store.

    LMOD KB (localhost:8000) is not part of this deployment.  All retrieval
    goes directly to fe_kb_chunk — no network call to the KB REST API is made.
    Documents reach this store when uploaded via the Onboard → Reverse-engineering
    report path; stage outputs are indexed automatically after each run.
    """
    return await asyncio.to_thread(_ask_local, app_id, name, question, settings=settings)


def _ask_local(app_id: str, name: str, question: str, *,
               settings=None) -> SurfaceResult:
    """Query the local Postgres RAG store for locally-uploaded documents.

    Documents reach this store when the user uploads via
    POST /api/v1/projects/{id}/documents (the onboarding RED / requirements
    path).  The KB at localhost:8000 never sees them unless the mirror
    succeeded, so this is the only retrieval path that works without lmod.
    """
    if settings is None or not settings.uses_database():
        return SurfaceResult(
            name, False,
            "KB unreachable and no local DB configured — set FE_DB_URL and "
            "re-upload the document to index it locally",
        )
    try:
        from app.agentic_platform.fe_core.rag.retriever import ChunkStore, retrieve

        chunk_store = ChunkStore(settings.fe_db_url, settings.fe_db_schema)
        result = retrieve(chunk_store, app_id, question)
        if result.refused or not result.chunks:
            return SurfaceResult(
                name, False,
                f"KB unreachable; local store: {result.reason or 'no matching chunks found'} "
                f"(upload the document via Onboard → Reverse-engineering report)",
            )
        content = "\n\n".join(
            f"[{c.artifact_type} · chunk {c.chunk_index + 1}]\n{c.content}"
            for c in result.chunks
        )
        logger.info(
            "Impact analysis: %s found %d chunk(s) in local store (KB was unavailable)",
            name, len(result.chunks),
        )
        return SurfaceResult(
            name, True,
            f"{len(result.chunks)} chunk(s) from locally-uploaded document "
            f"(KB unavailable — local store used)",
            content,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Impact analysis: %s local store query failed: %s", name, exc)
        return SurfaceResult(
            name, False,
            f"KB unreachable; local store error: {type(exc).__name__}: {exc}",
        )


def _read_schema(store, settings, datasource: str | None) -> SurfaceResult:
    """Latest approved schema snapshot, if one has been captured."""
    name = "schema"
    if store is None:
        return SurfaceResult(name, False, "no store available")
    try:
        snapshots = store.list_artifacts(artifact_type="schema-snapshot")
    except Exception as exc:  # noqa: BLE001
        return SurfaceResult(name, False, f"{type(exc).__name__}: {exc}")
    if not snapshots:
        return SurfaceResult(
            name, False,
            "no schema snapshot captured. Run POST /api/v1/schema/snapshot "
            "against a RESTORED COPY of CreditApp first (NFR-1: metadata only)",
        )
    latest = max(snapshots, key=lambda a: a.version)
    return SurfaceResult(
        name, True, f"schema-snapshot v{latest.version} ({latest.status.value})",
        f"Schema snapshot: {latest.path}\n"
        f"Tables, columns and stored-procedure signatures are recorded there. "
        f"Use those exact identifiers; do not invent names.",
    )


def build_prompt(stage, inputs: ImpactInputs) -> str:
    """The correlation prompt. Coverage is stated up front, not buried."""
    coverage = "\n".join(
        f"- **{s.name}**: {s.status} -- {s.detail}" for s in inputs.surfaces
    )

    if not inputs.can_correlate:
        available = ", ".join(s.name for s in inputs.available) or "none"
        return "\n\n".join([
            f"# Stage: {stage.name}",
            "## STOP -- insufficient input to correlate",
            f"Only these surfaces are available: {available}.\n\n{coverage}",
            "An Impact Analysis is a correlation between the legacy code, the "
            "business specifications and the database schema. With fewer than two "
            "surfaces there is nothing to correlate, and a document produced now "
            "would give the PRD false grounding.",
            "## Your task\n\n"
            "Write `impact-analysis.md` containing ONLY:\n"
            "1. A heading stating the analysis could not be performed.\n"
            "2. The coverage table above, verbatim.\n"
            "3. The exact steps needed to make each missing surface available.\n\n"
            "Do NOT analyse, estimate, or infer legacy behaviour. Do not pad it.",
        ])

    sections = "\n\n".join(
        f"### Surface: {s.name}\n\n{s.content}" for s in inputs.available
    )
    missing_note = ""
    if inputs.missing:
        missing_note = (
            "## Surfaces that are NOT available\n\n"
            + "\n".join(f"- **{s.name}**: {s.detail}" for s in inputs.missing)
            + "\n\nEvery finding that would have relied on these MUST be marked "
              "`UNVERIFIED (<surface> unavailable)`. Do not fill the gap by "
              "inference -- an unverified finding that reads as fact is worse "
              "than a stated gap."
        )

    return "\n\n".join(filter(None, [
        f"# Stage: {stage.name}",
        f"## Deliverable\n\n{stage.deliverable}",
        "## Input coverage\n\n" + coverage,
        "## Knowledge Base evidence\n\n" + sections,
        missing_note,
        """## Your task

Correlate the surfaces above and write `impact-analysis.md`:

1. **Input coverage** -- the table above, verbatim, first. A reader must know
   what this analysis is based on before reading its conclusions.
2. **Current-state summary** -- what the legacy system does, per capability,
   cited to the code-knowledge surface.
3. **Specification vs implementation** -- where the business specifications and
   the code disagree. This is the most valuable section: the legacy system is the
   thing being replaced, so an undetected divergence becomes a regression.
4. **Data touchpoints** -- tables and stored procedures each capability uses,
   with real identifiers from the schema snapshot only.
5. **Impact assessment per capability** -- size (S/M/L), what makes it hard, and
   the risk of getting it wrong.
6. **Stored-procedure reuse candidates** -- existing procedures that encode
   business logic worth calling rather than reimplementing. The schema is
   immutable, so this shapes the whole API design.
7. **Open questions** -- what a human must answer before the PRD can be trusted.

## Rules

- Cite every substantive claim as `[source: <surface>]`.
- Mark anything not supported by a surface `UNVERIFIED`.
- Use only real schema identifiers. Never invent a table, column or procedure.
- Never issue SQL and never read business data (NFR-1).
- Do not estimate effort in days. Size is S/M/L; a day figure implies a plan
  that does not exist yet.

## Generative AI limitations

Include this verbatim near the top:

> **This document was generated by a Large Language Model** from knowledge-base
> retrieval. It may have missed code paths, may mis-summarise business rules, and
> may be confidently wrong. It is a draft for Business Analyst review, not an
> authority on current system behaviour.""",
    ]))


async def prepare(stage, *, kb, app_id: str, store=None, settings=None,
                  datasource: str | None = None) -> tuple[str, ImpactInputs]:
    """Entry point used by the stage executor. Returns (prompt, inputs)."""
    inputs = await gather_inputs(kb, app_id, datasource=datasource, store=store,
                                 settings=settings)
    logger.info(
        "Impact analysis coverage: %d/%d surfaces (%s)",
        len(inputs.available), len(inputs.surfaces),
        ", ".join(f"{s.name}={s.status}" for s in inputs.surfaces),
    )
    return build_prompt(stage, inputs), inputs

"""Diagnostic tool for KB retrieval analysis.

Helps understand why kb.query returns certain results and debug:
- Index coverage (are screen entities indexed?)
- Query recall (does the seed query find expected entities?)
- Graph structure (do matched entities have relationships to screens?)
- Result ranking (why are generics ranked higher than specific entities?)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.services.kb_query import KbQueryService
from app.services.graph_provider import GraphProvider
from app.utils.logging import log


@dataclass
class RetrievalAnalysis:
    """Results of KB retrieval diagnostic."""

    query: str
    seed_results: list[dict[str, Any]]  # What kb.query returned
    result_kinds: dict[str, int]  # Kind distribution: {SCR: 2, ENT: 4, ...}
    top_result: dict[str, Any] | None  # The highest-ranked result
    screen_coverage: list[dict[str, Any]]  # Screen-kind results specifically
    expected_kinds: list[str]  # What we expected to find
    issue_summary: str  # Human-readable summary of retrieval quality


async def analyze_retrieval(
    kb: KbQueryService,
    query: str,
    persona: str = "ba",
    top_k: int = 15,
    expected_kinds: list[str] | None = None,
) -> RetrievalAnalysis:
    """
    Analyze what kb.query returns for a given query.

    Args:
        kb: KbQueryService instance
        query: The seed query to analyze
        persona: The persona scope for retrieval
        top_k: Number of results to fetch
        expected_kinds: What kinds we expect (e.g., ["Screen", "Entity", "BusinessRule"])
    """
    if expected_kinds is None:
        expected_kinds = ["Screen", "Entity", "BusinessRule", "FunctionalReq"]

    log.info(f"[kb-diagnostic] analyzing query: '{query}'")

    answer = await kb.query(question=query, persona=persona, top_k=top_k)

    results = [
        {
            "rank": i,
            "id": c.id,
            "kind": c.kind,
            "label": c.label,
            "confidence": getattr(c, "confidence", None),
            "source_locus": c.source_locus,
        }
        for i, c in enumerate(answer.citations)
    ]

    # Analyze kind distribution
    kind_dist: dict[str, int] = {}
    for result in results:
        kind = result["kind"]
        kind_dist[kind] = kind_dist.get(kind, 0) + 1

    # Extract screen-specific results
    screen_results = [r for r in results if r["kind"] in ("Screen", "SCR")]

    # Build issue summary
    issues = []
    if not results:
        issues.append("NO RESULTS — query returned nothing")
    if not screen_results and "Screen" in expected_kinds:
        issues.append(f"NO SCREENS FOUND — expected at least one Screen/SCR entity")
    if len([k for k, c in kind_dist.items() if c >= 3]) == 0:
        issues.append("LOW DIVERSITY — results dominated by one kind")

    # Check if results are mostly generic data models
    generic_kinds = {"Entity", "ENT", "Term"}
    generic_count = sum(c for k, c in kind_dist.items() if k in generic_kinds)
    if generic_count >= len(results) * 0.7:
        issues.append(
            f"GENERIC BIAS — {generic_count}/{len(results)} results are generic data entities "
            "(Screen/FunctionalReq are more actionable)"
        )

    # Rank quality check
    if results:
        top = results[0]
        if top["kind"] not in expected_kinds:
            issues.append(
                f"TOP RESULT MISMATCH — highest rank is {top['kind']} '{top['label']}', "
                f"not in expected: {expected_kinds}"
            )

    issue_summary = " | ".join(issues) if issues else "✓ Retrieval looks good"

    log.info(f"[kb-diagnostic] retrieved {len(results)} results")
    log.info(f"[kb-diagnostic] kind distribution: {kind_dist}")
    log.info(f"[kb-diagnostic] issue summary: {issue_summary}")

    return RetrievalAnalysis(
        query=query,
        seed_results=results,
        result_kinds=kind_dist,
        top_result=results[0] if results else None,
        screen_coverage=screen_results,
        expected_kinds=expected_kinds,
        issue_summary=issue_summary,
    )


async def compare_queries(
    kb: KbQueryService,
    queries: list[str],
    persona: str = "ba",
    top_k: int = 15,
) -> dict[str, RetrievalAnalysis]:
    """
    Run retrieval analysis across multiple queries to understand retrieval behavior.

    Returns a dict mapping each query to its RetrievalAnalysis.
    """
    log.info(f"[kb-diagnostic] comparing {len(queries)} queries")
    results: dict[str, RetrievalAnalysis] = {}

    for q in queries:
        analysis = await analyze_retrieval(kb, q, persona=persona, top_k=top_k)
        results[q] = analysis
        log.info(f"  '{q}' → {len(analysis.seed_results)} results")

    return results


async def analyze_graph_relationships(
    graph: GraphProvider,
    entity_id: str,
) -> dict[str, Any]:
    """
    Analyze what a matched entity is connected to in the graph.

    Useful for understanding why the impact walk doesn't find screens.
    """
    log.info(f"[kb-diagnostic] analyzing graph neighbors of {entity_id}")

    # Get neighbors (both directions)
    try:
        out_neighbors = await graph.neighbors(entity_id, direction="out", depth=1)
        in_neighbors = await graph.neighbors(entity_id, direction="in", depth=1)
        both_neighbors = await graph.neighbors(entity_id, direction="both", depth=1)

        out_kinds = {n.kind: out_neighbors.count(n) for n in out_neighbors}
        in_kinds = {n.kind: in_neighbors.count(n) for n in in_neighbors}

        log.info(f"[kb-diagnostic] {entity_id} outgoing edges by kind: {dict(set(out_kinds))}")
        log.info(f"[kb-diagnostic] {entity_id} incoming edges by kind: {dict(set(in_kinds))}")
        log.info(f"[kb-diagnostic] {entity_id} has {len(both_neighbors)} total neighbors at depth 1")

        return {
            "entity_id": entity_id,
            "outgoing_neighbors": len(out_neighbors),
            "incoming_neighbors": len(in_neighbors),
            "total_neighbors": len(both_neighbors),
            "outgoing_kinds": out_kinds,
            "incoming_kinds": in_kinds,
            "has_screen_neighbors": any(n.kind == "Screen" for n in both_neighbors),
        }
    except Exception as e:
        log.error(f"[kb-diagnostic] error analyzing {entity_id}: {e}")
        return {"entity_id": entity_id, "error": str(e)}


def format_retrieval_report(analysis: RetrievalAnalysis) -> str:
    """Format a RetrievalAnalysis as a human-readable report."""
    lines = [
        f"Query: '{analysis.query}'",
        f"Results: {len(analysis.seed_results)} total",
        f"Kind distribution: {analysis.result_kinds}",
        f"",
        "Top 5 results:",
    ]

    for result in analysis.seed_results[:5]:
        lines.append(
            f"  #{result['rank']+1}: [{result['id']}] {result['kind']:12} {result['label']}"
        )

    if analysis.screen_coverage:
        lines.append(f"\nScreen results ({len(analysis.screen_coverage)}):")
        for result in analysis.screen_coverage[:3]:
            lines.append(f"  #{result['rank']+1}: [{result['id']}] {result['label']}")
    else:
        lines.append("\n❌ NO SCREENS FOUND")

    lines.append(f"\nIssue summary: {analysis.issue_summary}")

    return "\n".join(lines)


def format_comparison_report(analyses: dict[str, RetrievalAnalysis]) -> str:
    """Format multiple RetrievalAnalysis results as a comparison report."""
    lines = ["KB RETRIEVAL COMPARISON REPORT", "=" * 60, ""]

    for query, analysis in analyses.items():
        lines.append(format_retrieval_report(analysis))
        lines.append("-" * 60)

    return "\n".join(lines)


# ─ Example: Run diagnostics locally ──────────────────────────────────────────────────────
# Uncomment and customize to run ad-hoc diagnostics
#
# if __name__ == "__main__":
#     from app.services.kb_query import KbQueryService
#     from app.services.graph_provider import GraphProvider
#
#     async def run_diagnostics():
#         kb_svc = KbQueryService()  # or inject via DI
#         graph_svc = GraphProvider()  # or inject via DI
#
#         # Test queries from the two versions
#         test_queries = [
#             "Add a Sales Campaign Code to the Basic Information screen",  # Expected: good results
#             "Need the Basic Information screen campaign code",  # Similar to current (weak)
#             "Basic Information",  # Generic screen search
#             "campaign code field",  # Feature-focused
#         ]
#
#         analyses = await compare_queries(kb_svc, test_queries)
#         print(format_comparison_report(analyses))
#
#         # If kb.query found screen entities, check their graph connectivity
#         if any(a.screen_coverage for a in analyses.values()):
#             best_screen = next(
#                 (r for a in analyses.values() for r in a.screen_coverage),
#                 None
#             )
#             if best_screen:
#                 graph_analysis = await analyze_graph_relationships(graph_svc, best_screen["id"])
#                 print(f"\nGraph analysis for {best_screen['id']}: {graph_analysis}")
#
#     asyncio.run(run_diagnostics())

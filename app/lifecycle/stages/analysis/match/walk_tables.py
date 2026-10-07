"""Graph walk with table-aware edge traversal.

Phase 3: Enhanced graph walking that follows TABLE ← QUERIED_BY ← CODE edges
to discover systems affected by table changes.

This module provides compute_downstream_with_tables() which handles the
scenario: requirement mentions table → find code that uses it → find systems.
"""

from typing import List, Optional, Set
import asyncio

from app.lifecycle.stages.analysis.match.walk import (
    AffectedNode,
    _collect,
    _filter_business_impact,
)
from app.services.graph_provider import GraphProvider
from app.utils.logging import log


async def compute_downstream_with_tables(
    graph: GraphProvider,
    matched_ids: List[str],
    *,
    max_depth: int = 2,
    cap: int = 8,
    fanout: int = 40,
) -> List[AffectedNode]:
    """Downstream walk that handles table entities specially.

    When matched set includes ENT (table) nodes:
    1. Find code/screens that query/read/write the table (incoming edges)
    2. Then traverse from those code components to systems

    Pattern:
        TABLE ← QUERIED_BY ← CMP ← PART_OF ← SYS

    For non-table nodes, use standard downstream walk (direction=in).

    Args:
        graph: GraphProvider instance
        matched_ids: List of matched KB IDs (may include ENT-* tables)
        max_depth: Maximum depth for non-table traversal
        cap: Max nodes to collect per source
        fanout: Max outgoing edges to follow per node

    Returns:
        List of AffectedNode objects (filtered to business kinds)
    """
    matched = list(dict.fromkeys(matched_ids))
    downstream = []

    # Separate tables from other nodes
    table_nodes = [m for m in matched if m.startswith('ENT-')]
    other_nodes = [m for m in matched if not m.startswith('ENT-')]

    log.info(
        f"[graph-walk-tables] Analyzing {len(matched)} nodes: "
        f"{len(table_nodes)} tables, {len(other_nodes)} other"
    )

    # Walk 1: From tables, find code/screens that use them
    if table_nodes:
        log.info(
            f"[graph-walk-tables] Finding consumers of {len(table_nodes)} table(s)..."
        )

        # Find incoming edges: QUERIED_BY, WRITES_TO, READS_FROM, etc.
        table_edge_labels = frozenset([
            'QUERIED_BY',
            'WRITES_TO',
            'READS_FROM',
            'INSERTS_INTO',
            'UPDATES',
            'DELETES_FROM',
        ])

        try:
            table_consumers, _ = await _collect(
                graph=graph,
                ids=table_nodes,
                direction="in",  # Find incoming edges (what uses the table?)
                depth=1,
                source_cap=cap,
                fanout_cap=fanout,
                labels=table_edge_labels,
            )

            log.info(
                f"[graph-walk-tables] Found {len(table_consumers)} code/screens "
                f"that use table(s)"
            )

            # Walk 2: From table consumers, find systems they belong to
            if table_consumers:
                consumer_ids = [n.id for n in table_consumers]

                # Find outgoing edges: PART_OF, OWNED_BY, IMPLEMENTS
                system_edge_labels = frozenset([
                    'PART_OF',
                    'OWNED_BY',
                    'IMPLEMENTS',
                    'DEPENDS_ON',
                ])

                try:
                    systems, _ = await _collect(
                        graph=graph,
                        ids=consumer_ids,
                        direction="out",  # Find outgoing edges to systems
                        depth=1,
                        source_cap=cap,
                        fanout_cap=fanout,
                        labels=system_edge_labels,
                    )

                    log.info(
                        f"[graph-walk-tables] Found {len(systems)} systems "
                        f"via table-using components"
                    )

                    downstream.extend(systems)

                except Exception as e:
                    log.warning(
                        f"[graph-walk-tables] Error collecting systems from "
                        f"table consumers: {e}"
                    )

                # Also include the table consumers themselves (screens, APIs)
                # Filter to relevant kinds (exclude tables themselves)
                consumer_kinds = {'CMP', 'Component', 'API', 'ApiOp', 'SCR', 'Screen'}
                consumer_nodes = [
                    n for n in table_consumers
                    if n.kind in consumer_kinds
                ]
                downstream.extend(consumer_nodes)

        except Exception as e:
            log.warning(f"[graph-walk-tables] Error walking from tables: {e}")

    # Walk 3: From other nodes (screens, rules, workflows), standard downstream
    if other_nodes:
        log.info(
            f"[graph-walk-tables] Standard downstream walk for {len(other_nodes)} "
            f"non-table node(s)"
        )

        try:
            other_downstream, _ = await _collect(
                graph=graph,
                ids=other_nodes,
                direction="in",
                depth=max_depth,
                source_cap=cap,
                fanout_cap=fanout,
                labels=frozenset(),  # Accept all edge types
            )

            downstream.extend(other_downstream)

        except Exception as e:
            log.warning(f"[graph-walk-tables] Error in standard walk: {e}")

    # Remove duplicates while preserving order
    seen: Set[str] = set()
    unique_downstream = []
    for node in downstream:
        if node.id not in seen:
            seen.add(node.id)
            unique_downstream.append(node)

    # Filter to business-relevant kinds (exclude tables from results)
    # This prevents showing the table itself in results, only what uses it
    business_kinds = {
        "SYS", "System",
        "INT", "Integration",
        "CMP", "Component",
        "API", "ApiOp",
        "SCR", "Screen",
        "WF", "Workflow",
        "PROC", "Process",
        "BR", "BusinessRule",
        "FR", "FunctionalReq",
    }

    filtered = [n for n in unique_downstream if n.kind in business_kinds]

    log.info(
        f"[graph-walk-tables] Table-aware downstream: "
        f"found {len(unique_downstream)} total candidates, "
        f"returning {len(filtered)} business nodes"
    )

    return filtered


async def compute_upstream_with_tables(
    graph: GraphProvider,
    matched_ids: List[str],
    *,
    max_depth: int = 2,
    cap: int = 8,
    fanout: int = 40,
) -> List[AffectedNode]:
    """Upstream walk (what depends on matched items).

    For tables, walks: CMP → PART_OF → SYS (what systems use the table?)

    Args:
        graph: GraphProvider instance
        matched_ids: List of matched KB IDs
        max_depth: Maximum depth for traversal
        cap: Max nodes to collect per source
        fanout: Max outgoing edges to follow per node

    Returns:
        List of AffectedNode objects
    """
    matched = list(dict.fromkeys(matched_ids))
    upstream = []

    try:
        upstream_nodes, _ = await _collect(
            graph=graph,
            ids=matched,
            direction="out",  # What do these nodes affect?
            depth=max_depth,
            source_cap=cap,
            fanout_cap=fanout,
            labels=frozenset(),  # Accept all edges
        )

        # Filter to business kinds
        business_kinds = {
            "SYS", "System",
            "INT", "Integration",
            "CMP", "Component",
            "API", "ApiOp",
            "WF", "Workflow",
            "PROC", "Process",
        }

        upstream = [n for n in upstream_nodes if n.kind in business_kinds]

    except Exception as e:
        log.warning(f"[graph-walk-tables] Error in upstream walk: {e}")

    log.info(
        f"[graph-walk-tables] Upstream walk: "
        f"found {len(upstream)} affected upstream nodes"
    )

    return upstream

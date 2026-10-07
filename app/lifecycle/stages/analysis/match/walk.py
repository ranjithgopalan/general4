"""Graph walk — config-driven affected-set, downstream ("what breaks"), and coverage (no LLM).

Adopted: lmod config `TraversalPolicy` (impact-as-config per route a–f) + xpf `TraceChainWalker` /
`BlindSpotDetector`. Runs over the existing NetworkX `GraphProvider`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config.edge_patterns import EdgePattern
from app.lifecycle.stages.analysis.schema import AffectedNode, BlindSpot, Coverage
from app.services.graph_provider import GraphProvider
from app.utils.logging import log

if TYPE_CHECKING:
    from app.config.edge_patterns import EdgePattern


@dataclass(frozen=True)
class TraversalPolicy:
    """What "impact" means for a workspace route (config object, not hardcoded logic)."""

    direction: str = "both"
    max_depth: int = 1
    source_cap: int = 8
    fanout_cap: int = 40
    labels: frozenset[str] = field(default_factory=frozenset)


_DEFAULT_POLICY = TraversalPolicy()
_ROUTE_POLICIES: dict[str, TraversalPolicy] = {
    "b": TraversalPolicy(direction="both", max_depth=1, source_cap=10, fanout_cap=50),  # greenfield: now with full graph walk (enhancement fix)
    "d": TraversalPolicy(direction="both", max_depth=2, source_cap=12, fanout_cap=60),  # tech-mod: full map
    "e": TraversalPolicy(direction="both", max_depth=1, source_cap=10, fanout_cap=50),  # upgrade
}


def policy_for_route(route: str | None) -> TraversalPolicy:
    """Resolve the impact policy for a workspace route (falls back to the default)."""
    return _ROUTE_POLICIES.get((route or "").lower(), _DEFAULT_POLICY)


# Change-nature proportionality: an additive / optional / non-rating change has a tiny blast radius;
# a structural / rating change spreads. Light changes get a tight walk + a small primary cap so the
# analysis stays proportional (a one-field add must NOT fan out to booking / rating / the whole flow).
_LIGHT_HINTS = (
    "optional", "no effect on premium", "non-rating", "no rating", "free-text", "free text",
    "read-only", "read only", "display-only", "display only", "reporting only", "cosmetic",
)
# An optional / additive / non-rating change touches only the named screen — no graph fan-out.
_LIGHT_POLICY = TraversalPolicy(direction="both", max_depth=0, source_cap=0, fanout_cap=0)


def change_is_light(requirement: str | None) -> bool:
    """True for additive/optional/non-rating changes (tiny blast radius)."""
    text = (requirement or "").lower()
    return any(hint in text for hint in _LIGHT_HINTS)


def policy_for_change(route: str | None, requirement: str | None) -> TraversalPolicy:
    """Proportional impact policy: tight for a light change, else the workspace-route policy.

    Respects ANALYSIS_DISABLE_LIGHT_WALK setting:
    - If True (light-walk DISABLED): always use route policy, ignore light detection
    - If False (light-walk ENABLED): use light policy for light changes, route policy otherwise
    """
    from app.config import get_settings
    s = get_settings()

    # If light-walk is DISABLED (True), always use route policy for full analysis
    if s.ANALYSIS_DISABLE_LIGHT_WALK:
        return policy_for_route(route)

    # Otherwise light-walk is ENABLED (False): use light policy for light changes
    return _LIGHT_POLICY if change_is_light(requirement) else policy_for_route(route)


def primary_cap(requirement: str | None) -> int:
    """How many top-ranked matched cards seed the impact walk. Recall-biased (config-driven): for
    impact, a missed seed loses a whole affected subtree, so this is wider than a Q&A precision cut."""
    from app.config import get_settings
    s = get_settings()
    return s.ANALYSIS_PRIMARY_CAP_LIGHT if change_is_light(requirement) else s.ANALYSIS_PRIMARY_CAP


async def _collect(
    graph: GraphProvider,
    ids: list[str],
    *,
    direction: str,
    depth: int,
    source_cap: int,
    fanout_cap: int,
    labels: frozenset[str],
) -> tuple[list[AffectedNode], set[str]]:
    """Neighbour nodes of ``ids`` (by policy) as AffectedNode + the set of source ids that had a neighbour."""
    seed_set = set(ids)
    found: dict[str, AffectedNode] = {}
    linked: set[str] = set()

    log.info(
        f"[graph-walk] _collect starting: ids={ids[:3]}{'...' if len(ids) > 3 else ''}, "
        f"direction={direction}, depth={depth}, source_cap={source_cap}, fanout_cap={fanout_cap}"
    )

    for idx, sid in enumerate(ids[:source_cap]):
        log.info(f"[graph-walk] querying neighbors for [{idx}] {sid} (direction={direction}, depth={depth})")
        sub = await graph.neighbors(sid, direction=direction, depth=depth)
        nodes_by_id = {n.id: n for n in sub.nodes}

        log.info(
            f"[graph-walk] {sid} returned {len(sub.nodes)} nodes, {len(sub.edges)} edges "
            f"| node kinds: {set(n.kind for n in sub.nodes)}"
        )

        for edge in sub.edges:
            if labels and edge.label not in labels:
                continue
            for nid in (edge.source, edge.target):
                if nid in seed_set or nid not in nodes_by_id:
                    continue
                linked.add(sid)
                node = found.get(nid)
                if node is None:
                    n = nodes_by_id[nid]
                    found[nid] = AffectedNode(
                        id=nid, label=n.label, kind=n.kind, hops=1, via=[edge.label], source_locus=n.source_locus
                    )
                    log.debug(f"[graph-walk] added affected node: {nid} ({n.kind}) via {edge.label}")
                elif edge.label not in node.via:
                    node.via.append(edge.label)

    log.info(
        f"[graph-walk] _collect complete: found {len(found)} affected nodes, "
        f"linked {len(linked)} source ids, returning {min(len(found), fanout_cap)} (capped at {fanout_cap})"
    )
    return sorted(found.values(), key=lambda a: a.id)[:fanout_cap], linked


def _filter_business_impact(nodes: list[AffectedNode]) -> tuple[list[AffectedNode], int]:
    """Filter to business-relevant kinds (code, systems, integrations); exclude database tables.

    Returns: (filtered_nodes, count_filtered_out)

    Database entities (ENT-*) are implementation details, not business impact.
    Match the filtering logic in compute_downstream for consistency.
    """
    # Business-relevant kinds (same as compute_downstream)
    business_kinds = {"SYS", "System", "INT", "Integration", "CMP", "Component", "API", "ApiOp", "SCR", "Screen", "WF", "Workflow", "PROC", "Process", "BR", "BusinessRule", "FR", "FunctionalReq"}

    filtered = [n for n in nodes if n.kind in business_kinds]
    filtered_out = len(nodes) - len(filtered)

    if filtered_out > 0:
        filtered_kinds = set(n.kind for n in nodes if n.kind not in business_kinds)
        log.info(f"[graph-walk] filtered out {filtered_out} non-business nodes (kinds: {filtered_kinds})")

    return filtered, filtered_out


async def compute_affected(
    graph: GraphProvider, matched_ids: list[str], policy: TraversalPolicy
) -> tuple[list[AffectedNode], Coverage]:
    """Impact set (the change's typed-edge neighbourhood) + coverage/blindspots.

    FIX A (Aug 12, 2026): Filter to business-relevant kinds (code, systems, integrations).
    Exclude database entities (ENT-*) which are implementation details, matching compute_downstream.
    """
    matched = list(dict.fromkeys(matched_ids))

    log.info(
        f"[graph-walk] compute_affected: matched_ids={len(matched)}, "
        f"policy=(direction={policy.direction}, depth={policy.max_depth}, "
        f"source_cap={policy.source_cap}, fanout_cap={policy.fanout_cap})"
    )

    if not matched or policy.max_depth <= 0 or policy.source_cap <= 0:
        log.info(
            f"[graph-walk] compute_affected early exit: "
            f"matched={bool(matched)}, max_depth={policy.max_depth}, source_cap={policy.source_cap}"
        )
        return [], Coverage(total=len(matched), linked=0, coverage_pct=0.0, blindspots=list(matched))

    affected, linked = await _collect(
        graph,
        matched,
        direction=policy.direction,
        depth=policy.max_depth,
        source_cap=policy.source_cap,
        fanout_cap=policy.fanout_cap,
        labels=policy.labels,
    )

    # FIX A: Filter out non-business nodes (database tables, reference data, etc.)
    affected_before_filter = len(affected)
    affected, filtered_out = _filter_business_impact(affected)

    blindspots = [mid for mid in matched if mid not in linked]
    coverage_pct = round(len(linked) / len(matched) * 100.0, 1) if matched else 0.0

    log.info(
        f"[graph-walk] compute_affected complete: affected={len(affected)} (filtered {filtered_out} from {affected_before_filter}), "
        f"linked={len(linked)}/{len(matched)}, blindspots={len(blindspots)}, coverage={coverage_pct}%"
    )

    return affected, Coverage(
        # ``total`` = the matched cards we assess for coverage (blindspots ⊆ matched), so
        # coverage_pct == linked/total and the UI can render "linked of total" coherently.
        total=len(matched), linked=len(linked), coverage_pct=coverage_pct, blindspots=blindspots
    )


async def compute_downstream(
    graph: GraphProvider, matched_ids: list[str], *, max_depth: int = 2, cap: int = 8, fanout: int = 40
) -> list[AffectedNode]:
    """Reverse walk (edges IN) — the dependents: "what breaks if this changes".

    Filter to business-relevant kinds (systems, components, screens, workflows, rules) — exclude database entities (ENT, TERM) which are
    implementation details, not business impact (docs/29 P3 fix).
    """
    matched = list(dict.fromkeys(matched_ids))

    log.info(f"[graph-walk] compute_downstream: matched_ids={len(matched)}, max_depth={max_depth}, cap={cap}")

    if not matched:
        log.info("[graph-walk] compute_downstream early exit: no matched ids")
        return []

    downstream, _ = await _collect(
        graph, matched, direction="in", depth=max_depth, source_cap=cap, fanout_cap=fanout, labels=frozenset()
    )

    log.info(
        f"[graph-walk] downstream walk complete before filter: {len(downstream)} nodes | "
        f"kinds: {set(d.kind for d in downstream)}"
    )

    # P1 Fix: Filter to business-relevant kinds — match compute_affected for consistency
    # Exclude ENT/TERM which are implementation details (database tables, glossary terms)
    # Include screens, workflows, rules so downstream impact analysis is complete
    business_kinds = {"SYS", "System", "INT", "Integration", "CMP", "Component", "API", "ApiOp", "SCR", "Screen", "WF", "Workflow", "PROC", "Process", "BR", "BusinessRule", "FR", "FunctionalReq"}
    downstream_before_filter = downstream.copy()
    downstream = [d for d in downstream if d.kind in business_kinds]

    filtered_out = len(downstream_before_filter) - len(downstream)
    if filtered_out > 0:
        filtered_kinds = set(d.kind for d in downstream_before_filter if d.kind not in business_kinds)
        log.info(f"[graph-walk] filtered out {filtered_out} nodes (kinds: {filtered_kinds})")

    log.info(f"[graph-walk] downstream after filter: {len(downstream)} business systems")

    return downstream


# ============= SEMANTIC WALKER (Aug 12, 2026) =============
# Pattern-aware graph walk that understands what patterns require which edges.
# Returns: code + systems + integrations + blind_spots (embedded, not separate).


@dataclass
class WalkResult:
    """Result of semantic graph walk — code + systems + integrations + blind_spots."""

    code_affected: list[str] = field(default_factory=list)  # CMP-*
    systems_affected: list[str] = field(default_factory=list)  # SYS-*
    integrations_affected: list[str] = field(default_factory=list)  # INT-*
    screens_affected: list[str] = field(default_factory=list)  # SCR-*
    rules_affected: list[str] = field(default_factory=list)  # BR-*, FR-*
    workflows_affected: list[str] = field(default_factory=list)  # WF-*
    blind_spots: list[BlindSpot] = field(default_factory=list)


class SemanticWalker:
    """
    Graph walk that understands requirement patterns.
    Each pattern routes to a specific walk strategy following semantically-relevant edges.
    Returns: WalkResult with code + systems + integrations + blind_spots.
    """

    def __init__(self, graph: GraphProvider):
        self.graph = graph

    async def walk(
        self, matched_ids: list[str], pattern: str, requirement: str = ""
    ) -> WalkResult:
        """
        Route to pattern-specific walk strategy.

        Args:
            matched_ids: KB node IDs from kb.query
            pattern: FIELD_ADDITION | NEW_SCREEN | ADD_INTEGRATION | UPGRADE_SYSTEM | MODIFY_RULE
            requirement: Original requirement text

        Returns:
            WalkResult with code + systems + integrations + blind_spots
        """

        # Fetch nodes
        matched_nodes = [self.graph.nodes.get(nid) for nid in matched_ids if nid in self.graph.nodes]

        if not matched_nodes:
            log.warning(f"[semantic-walk] no matched nodes found for pattern={pattern}")
            return WalkResult(
                blind_spots=[
                    BlindSpot(
                        severity="CRITICAL",
                        type="NO_MATCHES",
                        description="No matched KB nodes found in graph",
                        action="Verify requirement matches KB content",
                    )
                ]
            )

        log.info(f"[semantic-walk] starting {pattern} walk with {len(matched_nodes)} matched nodes")

        # Route by pattern
        if pattern == "FIELD_ADDITION":
            return await self._walk_field_addition(matched_nodes)
        elif pattern == "NEW_SCREEN":
            return await self._walk_new_screen(matched_nodes)
        elif pattern == "ADD_INTEGRATION":
            return await self._walk_add_integration(matched_nodes)
        elif pattern == "UPGRADE_SYSTEM":
            return await self._walk_system_upgrade(matched_nodes)
        elif pattern == "MODIFY_RULE":
            return await self._walk_modify_rule(matched_nodes)
        else:
            # Fallback: smart walk based on node kinds
            return await self._walk_smart_by_kinds(matched_nodes)

    async def _walk_field_addition(self, matched_nodes: list) -> WalkResult:
        """Walk for FIELD_ADDITION: table → code → systems → integrations."""

        result = WalkResult()

        # Find table nodes
        tables = [n for n in matched_nodes if n.kind == "Entity"]
        if not tables:
            result.blind_spots.append(
                BlindSpot(
                    severity="CRITICAL",
                    type="TABLE_RESOLUTION",
                    description="No table (Entity) node found in matched results",
                    action="Requirement must specify which table the field is added to",
                )
            )
            return result

        for table in tables:
            # Step 1: Code that touches this table
            code = await self._follow_edges(
                table.id, edge_labels=["DEPENDS_ON", "PRODUCES", "READS_FROM", "WRITES_TO"], target_kinds=["Component"]
            )
            result.code_affected.extend(code)

            if not code:
                result.blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="CODE_COVERAGE",
                        description=f"No code components found that access {table.label}",
                        action="Verify table is actually used in code",
                    )
                )

            # Step 2: Systems that own the code
            if code:
                systems = await self._follow_edges(
                    code, edge_labels=["OWNED_BY", "PART_OF", "CONTAINS"], target_kinds=["System"]
                )
                result.systems_affected.extend(systems)

                if not systems:
                    result.blind_spots.append(
                        BlindSpot(
                            severity="HIGH",
                            type="SYSTEM_OWNERSHIP",
                            description=f"Code {code} has no system owner edges",
                            action="Add OWNED_BY edges: CMP → OWNED_BY → SYS",
                        )
                    )

            # Step 3: Integrations that consume this table
            integrations = await self._follow_edges(
                table.id, edge_labels=["FEEDS_INTO", "EXPORTED_BY"], target_kinds=["Integration"]
            )
            result.integrations_affected.extend(integrations)

            # Step 4: Field-specific blind spots
            result.blind_spots.extend(await self._detect_field_blind_spots(table, code, result.systems_affected, integrations))

        return result

    async def _walk_new_screen(self, matched_nodes: list) -> WalkResult:
        """Walk for NEW_SCREEN: screen → components → systems → rules."""

        result = WalkResult()

        screens = [n for n in matched_nodes if n.kind == "Screen"]
        if not screens:
            result.blind_spots.append(
                BlindSpot(
                    severity="CRITICAL",
                    type="SCREEN_RESOLUTION",
                    description="No Screen (SCR-*) node found in matched results",
                    action="Requirement must reference a screen",
                )
            )
            return result

        for screen in screens:
            result.screens_affected.append(screen.id)

            # Components that render this screen
            components = await self._follow_edges(
                screen.id, edge_labels=["SCREEN_OF", "RENDERED_BY", "IMPLEMENTS"], target_kinds=["Component"]
            )
            result.code_affected.extend(components)

            if not components:
                result.blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="CODE_COVERAGE",
                        description=f"No components found that render {screen.label}",
                        action="Add SCREEN_OF edges: SCR → SCREEN_OF → CMP",
                    )
                )

            # Systems that own the components
            if components:
                systems = await self._follow_edges(components, edge_labels=["OWNED_BY", "PART_OF"], target_kinds=["System"])
                result.systems_affected.extend(systems)

            # Rules that apply to this screen
            rules = await self._follow_edges(
                screen.id, edge_labels=["GOVERNED_BY", "VALIDATES"], target_kinds=["BusinessRule", "FunctionalReq"]
            )
            result.rules_affected.extend(rules)

        return result

    async def _walk_add_integration(self, matched_nodes: list) -> WalkResult:
        """Walk for ADD_INTEGRATION: integration → connected systems → code."""

        result = WalkResult()

        integrations = [n for n in matched_nodes if n.kind == "Integration"]
        if not integrations:
            result.blind_spots.append(
                BlindSpot(
                    severity="CRITICAL",
                    type="INTEGRATION_RESOLUTION",
                    description="No Integration (INT-*) node found",
                    action="Requirement must reference an integration",
                )
            )
            return result

        for integration in integrations:
            result.integrations_affected.append(integration.id)

            # Systems this integration connects
            systems = await self._follow_edges(
                integration.id, edge_labels=["CONNECTS_TO", "EXPORTS_TO", "IMPORTS_FROM"], target_kinds=["System"]
            )
            result.systems_affected.extend(systems)

            if not systems:
                result.blind_spots.append(
                    BlindSpot(
                        severity="HIGH",
                        type="INTEGRATION_COVERAGE",
                        description=f"{integration.label} has no connected systems",
                        action="Add CONNECTS_TO edges: INT → CONNECTS_TO → SYS",
                    )
                )

            # Code that implements this integration
            code = await self._follow_edges(
                integration.id, edge_labels=["IMPLEMENTS", "SUPPORTED_BY"], target_kinds=["Component"]
            )
            result.code_affected.extend(code)

        return result

    async def _walk_system_upgrade(self, matched_nodes: list) -> WalkResult:
        """Walk for UPGRADE_SYSTEM: system → dependent systems → code."""

        result = WalkResult()

        systems = [n for n in matched_nodes if n.kind == "System"]
        if not systems:
            result.blind_spots.append(
                BlindSpot(
                    severity="CRITICAL",
                    type="SYSTEM_RESOLUTION",
                    description="No System (SYS-*) node found",
                    action="Requirement must reference a system",
                )
            )
            return result

        for system in systems:
            result.systems_affected.append(system.id)

            # Dependent systems (only specific edges, not all)
            dependent_systems = await self._follow_edges(
                system.id, edge_labels=["FEEDS_INTO", "DEPENDS_ON"], target_kinds=["System"]
            )
            result.systems_affected.extend(dependent_systems)

            # Code that touches this system
            code = await self._follow_edges(system.id, edge_labels=["CALLS", "INTEGRATES_WITH"], target_kinds=["Component"])
            result.code_affected.extend(code)

            # Version tracking check
            if not system.metadata.get("version"):
                result.blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="VERSION_TRACKING",
                        description=f"{system.label} missing version in metadata",
                        action="Add version field to system metadata",
                    )
                )

        return result

    async def _walk_modify_rule(self, matched_nodes: list) -> WalkResult:
        """Walk for MODIFY_RULE: rule → screens → workflows → code."""

        result = WalkResult()

        rules = [n for n in matched_nodes if n.kind in ["BusinessRule", "FunctionalReq"]]
        if not rules:
            result.blind_spots.append(
                BlindSpot(
                    severity="CRITICAL",
                    type="RULE_RESOLUTION",
                    description="No BusinessRule/FunctionalReq node found",
                    action="Requirement must reference a business or functional requirement",
                )
            )
            return result

        for rule in rules:
            result.rules_affected.append(rule.id)

            # Screens that apply this rule
            screens = await self._follow_edges(rule.id, edge_labels=["APPLIES_TO", "GOVERNS"], target_kinds=["Screen"])
            result.screens_affected.extend(screens)

            # Workflows that enforce this rule
            workflows = await self._follow_edges(
                rule.id, edge_labels=["ENFORCED_BY", "TRIGGERED_BY"], target_kinds=["Workflow"]
            )
            result.workflows_affected.extend(workflows)

            # Code that implements this rule
            code = await self._follow_edges(
                rule.id, edge_labels=["IMPLEMENTED_BY", "VALIDATED_BY"], target_kinds=["Component"]
            )
            result.code_affected.extend(code)

            # Test coverage check
            if not rule.metadata.get("test_coverage"):
                result.blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="TEST_COVERAGE",
                        description=f"{rule.label} missing test coverage metadata",
                        action="Add test_coverage references to rule",
                    )
                )

        return result

    async def _walk_smart_by_kinds(self, matched_nodes: list) -> WalkResult:
        """Fallback: Route based on dominant node kind."""

        kinds_present = set(n.kind for n in matched_nodes)

        if "Entity" in kinds_present:
            return await self._walk_field_addition(matched_nodes)
        elif "Screen" in kinds_present:
            return await self._walk_new_screen(matched_nodes)
        elif "Integration" in kinds_present:
            return await self._walk_add_integration(matched_nodes)
        elif "System" in kinds_present:
            return await self._walk_system_upgrade(matched_nodes)
        elif "BusinessRule" in kinds_present or "FunctionalReq" in kinds_present:
            return await self._walk_modify_rule(matched_nodes)
        else:
            return WalkResult(
                blind_spots=[
                    BlindSpot(
                        severity="HIGH",
                        type="PATTERN_UNKNOWN",
                        description=f"Could not determine pattern from nodes: {kinds_present}",
                        action="Provide clearer requirement or specify pattern explicitly",
                    )
                ]
            )

    async def _follow_edges(
        self, node_ids: str | list[str], edge_labels: list[str] | "EdgePattern", target_kinds: list[str]
    ) -> list[str]:
        """Follow specific semantic edges from node(s). Deduped.

        Args:
            node_ids: Node ID(s) to start from
            edge_labels: Either a list of edge label strings OR an EdgePattern enum
                        (enums are resolved to all known aliases for robustness)
            target_kinds: Node kinds to match (e.g., ["Component", "System"])

        Returns:
            List of matching target node IDs
        """
        from app.config.edge_patterns import EdgePattern, resolve_edge_pattern

        if isinstance(node_ids, str):
            node_ids = [node_ids]

        # Resolve edge pattern (enum or list of strings) to all aliases
        labels_to_match = resolve_edge_pattern(edge_labels)

        result = []
        for node_id in node_ids:
            edges = self.graph.edges.filter(from_id=node_id, label__in=labels_to_match)
            for edge in edges:
                target = self.graph.nodes.get(edge.to_id)
                if target and target.kind in target_kinds:
                    result.append(target.id)

        return list(set(result))  # dedupe

    async def _detect_field_blind_spots(
        self, table, code: list[str], systems: list[str], integrations: list[str]
    ) -> list[BlindSpot]:
        """Detect field-addition-specific blind spots."""

        blind_spots = []

        # Schema version check
        if not table.metadata.get("schema_version"):
            blind_spots.append(
                BlindSpot(
                    severity="HIGH",
                    type="SCHEMA_MANAGEMENT",
                    description=f"{table.label} missing schema_version",
                    action="Add schema_version tracking",
                    affected_nodes=[table.id],
                )
            )

        # Code migration checks
        for code_id in code:
            code_node = self.graph.nodes.get(code_id)
            if code_node and not code_node.metadata.get("migration_script"):
                blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="MIGRATION_COVERAGE",
                        description=f"{code_id} missing migration script",
                        action="Create migration script or mark as no-op",
                        affected_nodes=[code_id],
                    )
                )

        # Data backfill check
        if not table.metadata.get("backfill_script"):
            blind_spots.append(
                BlindSpot(
                    severity="MEDIUM",
                    type="DATA_MIGRATION",
                    description=f"No backfill script for {table.label}",
                    action="Create backfill or confirm default OK",
                    affected_nodes=[table.id],
                )
            )

        return blind_spots

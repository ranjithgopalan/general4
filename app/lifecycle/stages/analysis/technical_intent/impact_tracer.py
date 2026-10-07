"""Impact Tracer — Phase 2: Graph-Based Impact Tracing.

Traces technical impact through the knowledge graph by following edges.
Collects all affected components (screens, workflows, processes, code, databases, integrations, downstream).
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.technical_intent.models import (
    AffectedComponent,
    ImpactResult,
    TechnicalExtraction,
    TechnicalPattern,
)
from app.utils.logging import log


class ImpactTracer:
    """Trace technical impact through the knowledge graph.

    Takes extracted entities and follows graph edges to find all affected components.
    Works with any graph backend that supports basic node/edge queries.
    """

    def __init__(self, graph: Any):
        """Initialize tracer with a knowledge graph.

        Args:
            graph: Graph object with nodes/edges (expects methods like:
                   get_node(id), get_nodes_by_kind(kind), edges(from_id, type),
                   edges_reverse(to_id, type), etc.)
        """
        self.graph = graph

    async def trace_impact(
        self, extraction: TechnicalExtraction, pattern: TechnicalPattern
    ) -> ImpactResult:
        """Trace the complete impact of a technical change.

        Args:
            extraction: TechnicalExtraction from intent extractor
            pattern: The detected pattern type

        Returns:
            ImpactResult with all affected components and files
        """
        impact = ImpactResult(pattern=pattern, extraction=extraction)

        log.info(f"[IMPACT-TRACE] Starting impact trace for pattern: {pattern.name}")

        if pattern == TechnicalPattern.FIELD_ADDITION or pattern == TechnicalPattern.DATABASE_SCHEMA_CHANGE:
            # Both FIELD_ADDITION and DATABASE_SCHEMA_CHANGE involve adding fields to tables
            # Treat them identically for impact tracing
            await self._trace_field_addition(extraction, impact)
        elif pattern == TechnicalPattern.SCREEN_MODIFICATION:
            # SCREEN_MODIFICATION may also have database + integration components
            # (P1 component search, P2 screen search, database tables, integrations)
            await self._trace_field_addition(extraction, impact)
        elif pattern == TechnicalPattern.INTEGRATION_ADDITION:
            await self._trace_integration_addition(extraction, impact)
        elif pattern == TechnicalPattern.SCREEN_CREATION:
            await self._trace_screen_creation(extraction, impact)
        else:
            log.warning(f"[IMPACT-TRACE] No tracing implemented for pattern: {pattern.name}")

        # Assess risk level
        self._assess_risk(impact)

        log.info(
            f"[IMPACT-TRACE] Impact trace complete: "
            f"{len(impact.all_affected_components())} components, "
            f"{len(impact.blind_spots)} blind spots, "
            f"risk: {impact.risk_level}"
        )

        return impact

    async def _trace_field_addition(self, extraction: TechnicalExtraction, impact: ImpactResult) -> None:
        """Trace impact of adding field to screen/table.

        P0-P2 FIX: Enhanced to search for components mentioned in requirement text
        and use semantic search for fuzzy screen matching.
        """
        log.info(f"[IMPACT-TRACE-FIELD] Tracing field addition: {extraction.target_screen} / {extraction.target_table}")

        if not extraction.target_screen and not extraction.target_table:
            log.warning("[IMPACT-TRACE-FIELD] No target screen or table specified")
            return

        # P1 NEW: Search for components explicitly mentioned (e.g., EmailEditorModalComponent)
        if extraction.target_system and any(
            kw in extraction.target_system.lower()
            for kw in ["component", "modal", "editor", "api", "service", "handler"]
        ):
            component_nodes = await self._find_component_by_name(extraction.target_system)
            impact.affected_code.extend(component_nodes)
            log.info(f"[IMPACT-TRACE-FIELD] P1: Found {len(component_nodes)} component(s) from target_system: {extraction.target_system}")

        # 1. Find the screen node (P2: Use semantic search instead of substring match)
        if extraction.target_screen:
            screen_nodes = await self._find_screen_by_name_semantic(extraction.target_screen)
            impact.affected_screens.extend(screen_nodes)
            log.info(f"[IMPACT-TRACE-FIELD] P2: Found {len(screen_nodes)} screen(s) via semantic search")

            # 2. From screen, trace to workflows (SCREEN_OF reverse)
            for screen in screen_nodes:
                workflows = await self._trace_neighbors(screen.id, "SCREEN_OF", reverse=True)
                impact.affected_workflows.extend(workflows)
                log.info(f"[IMPACT-TRACE-FIELD] Found {len(workflows)} workflow(s) for screen {screen.id}")

                # 3. From workflows, trace to processes (TRIGGERS)
                for workflow in workflows:
                    processes = await self._trace_neighbors(workflow.id, "TRIGGERS")
                    impact.affected_processes.extend(processes)
                    log.info(f"[IMPACT-TRACE-FIELD] Found {len(processes)} process(es) for workflow {workflow.id}")

                    # 4. From processes, trace to code (CALLS)
                    for process in processes:
                        code = await self._trace_neighbors(process.id, "CALLS")
                        impact.affected_code.extend(code)
                        log.info(f"[IMPACT-TRACE-FIELD] Found {len(code)} code component(s) for process {process.id}")

        # 5. Find the table node
        if extraction.target_table:
            table_nodes = await self._find_table_by_name(extraction.target_table)
            impact.affected_databases.extend(table_nodes)
            log.info(f"[IMPACT-TRACE-FIELD] Found {len(table_nodes)} table(s)")

            for table in table_nodes:
                # Code that depends on this table (try multiple edge types for robustness)
                dependent_code = await self._trace_neighbors(table.id, "DEPENDS_ON", reverse=True)
                dependent_code += await self._trace_neighbors(table.id, "READS_FROM", reverse=True)
                dependent_code += await self._trace_neighbors(table.id, "WRITES_TO", reverse=True)
                impact.affected_code.extend(dependent_code)
                log.info(f"[IMPACT-TRACE-FIELD] Found {len(dependent_code)} code component(s) depending on table")

                # Integrations that feed into (consume) this table
                integrations = await self._trace_neighbors(table.id, "FEEDS_INTO")
                integrations += await self._trace_neighbors(table.id, "EXPORTS_TO")
                impact.affected_integrations.extend(integrations)
                log.info(f"[IMPACT-TRACE-FIELD] Found {len(integrations)} integration(s) consuming table")

                # Downstream systems
                for integ in integrations:
                    downstream = await self._trace_neighbors(integ.id, "FEEDS_INTO")
                    impact.affected_downstream_systems.extend(downstream)
                    log.info(f"[IMPACT-TRACE-FIELD] Found {len(downstream)} downstream system(s)")

        # De-duplicate components
        self._dedup_components(impact)

        # Build file change list
        await self._build_file_changes(impact)

    async def _trace_integration_addition(
        self, extraction: TechnicalExtraction, impact: ImpactResult
    ) -> None:
        """Trace impact of adding integration/ESB route."""
        log.info("[IMPACT-TRACE-INTEG] Tracing integration addition")

        if extraction.source_system:
            source_nodes = await self._find_system_by_name(extraction.source_system)
            log.info(f"[IMPACT-TRACE-INTEG] Found {len(source_nodes)} source system(s)")

        if extraction.target_system:
            target_nodes = await self._find_system_by_name(extraction.target_system)
            impact.affected_downstream_systems.extend(target_nodes)
            log.info(f"[IMPACT-TRACE-INTEG] Found {len(target_nodes)} target system(s)")

    async def _trace_screen_creation(self, extraction: TechnicalExtraction, impact: ImpactResult) -> None:
        """Trace impact of creating new screen."""
        log.info("[IMPACT-TRACE-SCREEN] Tracing screen creation")
        # New screen affects: workflows (if assigned to one), processes, code
        # This is typically a new component with no prior edges
        if extraction.flows_affected:
            log.info(f"[IMPACT-TRACE-SCREEN] New screen for flows: {extraction.flows_affected}")

    async def _find_screen_by_name(self, screen_name: str) -> list[AffectedComponent]:
        """Find screen nodes by name."""
        if not self.graph:
            return []

        nodes = await self._get_nodes_by_kind("Screen")
        matching = [n for n in nodes if screen_name.lower() in n.label.lower()]
        return matching

    async def _find_table_by_name(self, table_name: str) -> list[AffectedComponent]:
        """Find table/entity nodes by name."""
        if not self.graph:
            return []

        nodes = await self._get_nodes_by_kind("Entity")
        matching = [n for n in nodes if table_name.lower() in n.label.lower()]
        return matching

    async def _find_system_by_name(self, system_name: str) -> list[AffectedComponent]:
        """Find system nodes by name."""
        if not self.graph:
            return []

        nodes = await self._get_nodes_by_kind("System")
        matching = [n for n in nodes if system_name.lower() in n.label.lower()]
        return matching

    async def _find_screen_by_name_semantic(self, screen_name: str) -> list[AffectedComponent]:
        """P2 FIX: Find screen nodes using semantic search (fuzzy matching).

        Uses graph.seeds() for keyword-based search instead of exact substring matching.
        Handles:
        - Locale differences (English requirement vs Japanese screen name)
        - Word order variations (AU Renewal vs Renewal AU)
        - Abbreviations and partial names
        """
        if not self.graph or not screen_name:
            return []

        try:
            # Use semantic seed search (word-boundary tokenization, inverted index)
            seed_result = await self.graph.seeds(screen_name, category=None, limit=15)

            # Filter to Screen kind only with confidence threshold (P2 precision fix)
            # Keep screens with confidence >= 0.35 to get high-confidence semantic matches
            screen_nodes = [
                AffectedComponent(
                    id=s.id,
                    label=s.label,
                    kind="Screen",
                    source_locus="",
                    confidence=min(s.score / 10.0, 1.0),  # Normalize score to 0-1
                )
                for s in seed_result.seeds
                if s.kind in ("Screen", "SCR")
                and min(s.score / 10.0, 1.0) >= 0.35  # Confidence threshold for screen precision
            ]

            if screen_nodes:
                log.info(
                    f"[IMPACT-TRACE-SEMANTIC] Semantic screen search for '{screen_name}': "
                    f"found {len(screen_nodes)} screen(s)"
                )
                for node in screen_nodes:
                    log.debug(f"  - {node.id}: {node.label} (confidence={node.confidence:.2f})")
            else:
                # Fallback to basic substring search if semantic search fails
                log.info(f"[IMPACT-TRACE-SEMANTIC] No semantic matches for screen '{screen_name}', using fallback substring search")
                nodes = await self._get_nodes_by_kind("Screen")
                screen_nodes = [n for n in nodes if screen_name.lower() in n.label.lower()]

            return screen_nodes
        except Exception as e:
            log.warning(f"[IMPACT-TRACE-SEMANTIC] Error in semantic screen search: {e}, falling back to substring")
            nodes = await self._get_nodes_by_kind("Screen")
            return [n for n in nodes if screen_name.lower() in n.label.lower()]

    def _normalize_component_name(self, name: str) -> str:
        """Aug 17, 2026: Normalize component names to match KB storage.

        KB stores components as concatenated camelCase (e.g., 'EmailEditorModalComponent')
        but requirements may specify them with spaces (e.g., 'Email Editor Modal Component API').
        This normalizer removes spaces and 'API' suffix to match KB format.

        Examples:
          'Email Editor Modal Component API' → 'EmailEditorModalComponent'
          'Email Editor Modal Component' → 'EmailEditorModalComponent'
          'ValidationService' → 'ValidationService' (already normalized)
        """
        import re

        # Remove common suffixes
        cleaned = name.replace(' API', '').replace('API', '').strip()

        # If already camelCase (no spaces), return as-is
        if ' ' not in cleaned:
            return cleaned

        # Convert "Email Editor Modal Component" → "EmailEditorModalComponent"
        words = cleaned.split()
        camel_case = ''.join(word.capitalize() for word in words)

        log.debug(f"[normalize] '{name}' → '{camel_case}'")
        return camel_case

    async def _find_component_by_name(self, component_name: str) -> list[AffectedComponent]:
        """P1 FIX: Find component/API nodes by name using semantic search.

        Searches for code components explicitly mentioned in requirement text
        (e.g., 'EmailEditorModalComponent', 'ValidationService').

        Aug 17, 2026: Added component name normalization to handle KB name format mismatch.
        KB stores components as camelCase concatenated names, but requirements may use spaced names.
        """
        if not self.graph or not component_name:
            return []

        try:
            # Normalize component name to match KB storage format
            normalized_name = self._normalize_component_name(component_name)
            search_names = [component_name, normalized_name] if normalized_name != component_name else [component_name]

            # Try both original and normalized names
            all_components = []
            for search_name in search_names:
                # Use semantic seed search for component name
                seed_result = await self.graph.seeds(search_name, category=None, limit=20)

                # Filter to Component/ApiOp kind with confidence threshold (P1 precision fix)
                # Only include components with confidence >= 0.25 to eliminate false positives
                components = [
                    AffectedComponent(
                        id=s.id,
                        label=s.label,
                        kind=s.kind,
                        source_locus="",
                        confidence=min(s.score / 10.0, 1.0),
                    )
                    for s in seed_result.seeds
                    if s.kind in ("Component", "CMP", "ApiOp", "API")
                    and min(s.score / 10.0, 1.0) >= 0.30  # Confidence threshold to filter false positives
                ]
                all_components.extend(components)

                if components and search_name == search_names[0]:
                    log.info(
                        f"[IMPACT-TRACE-COMPONENT] Semantic component search for '{search_name}': "
                        f"found {len(components)} component(s)"
                    )

            # Deduplicate by ID (in case both searches returned the same component)
            seen_ids = set()
            unique_components = []
            for node in all_components:
                if node.id not in seen_ids:
                    seen_ids.add(node.id)
                    unique_components.append(node)

            if unique_components:
                log.info(
                    f"[IMPACT-TRACE-COMPONENT] Component search for '{component_name}' (normalized: '{normalized_name}'): "
                    f"found {len(unique_components)} component(s)"
                )
                for node in unique_components:
                    log.debug(f"  - {node.id}: {node.label} (kind={node.kind}, confidence={node.confidence:.2f})")
            else:
                log.info(
                    f"[IMPACT-TRACE-COMPONENT] No components found for '{component_name}' "
                    f"(tried: {', '.join(search_names)})"
                )

            return unique_components
        except Exception as e:
            log.warning(f"[IMPACT-TRACE-COMPONENT] Error searching for component '{component_name}': {e}")
            return []

    async def _get_nodes_by_kind(self, kind: str) -> list[AffectedComponent]:
        """Get all nodes of a specific kind."""
        if not self.graph:
            return []

        try:
            nodes = await self.graph.get_nodes_by_kind(kind)
            return [
                AffectedComponent(
                    id=n.get("id", ""),
                    label=n.get("label", ""),
                    kind=kind,
                    source_locus=n.get("source_locus", ""),
                    confidence=n.get("confidence", 0.9),
                )
                for n in nodes
            ]
        except Exception as e:
            log.warning(f"[IMPACT-TRACE] Failed to get nodes by kind {kind}: {e}")
            return []

    async def _trace_neighbors(
        self, node_id: str, edge_type: str, reverse: bool = False
    ) -> list[AffectedComponent]:
        """Trace neighbors across an edge type."""
        if not self.graph:
            return []

        try:
            if reverse:
                neighbors = await self.graph.get_edges_reverse(node_id, edge_type)
            else:
                neighbors = await self.graph.get_edges(node_id, edge_type)

            return [
                AffectedComponent(
                    id=n.get("id", ""),
                    label=n.get("label", ""),
                    kind=n.get("kind", ""),
                    source_locus=n.get("source_locus", ""),
                    confidence=n.get("confidence", 0.8),
                )
                for n in neighbors
            ]
        except Exception as e:
            log.warning(f"[IMPACT-TRACE] Failed to trace neighbors for {node_id}: {e}")
            return []

    async def _build_file_changes(self, impact: ImpactResult) -> None:
        """Build the file change list from affected components."""
        log.info("[IMPACT-TRACE] Building file change list...")

        # P0: Database changes
        for table in impact.affected_databases:
            if impact.extraction.fields:
                field_names = " + ".join([f.name for f in impact.extraction.fields])
                impact.files_to_change.append(
                    {
                        "priority": "P0",
                        "type": "DATABASE",
                        "file": f"scripts/add_{field_names}_to_{table.id}.sql",
                        "action": f"ALTER TABLE {table.label} ADD COLUMN ...",
                        "owner": "DBA",
                    }
                )

        # P0: UI changes
        for screen in impact.affected_screens:
            impact.files_to_change.append(
                {
                    "priority": "P0",
                    "type": "UI",
                    "file": f"src/app/screens/{screen.id.lower()}.component.ts",
                    "action": f"Add field controls to {screen.label}",
                    "owner": "Frontend",
                }
            )

        # P0: Backend code changes
        for code in impact.affected_code:
            impact.files_to_change.append(
                {
                    "priority": "P0",
                    "type": "BACKEND",
                    "file": code.id,
                    "action": f"Update {code.label} to handle field changes",
                    "owner": "Backend",
                }
            )

        # P1: Integration changes
        for integ in impact.affected_integrations:
            impact.files_to_change.append(
                {
                    "priority": "P1",
                    "type": "INTEGRATION",
                    "file": f"config/integrations/{integ.id.lower()}.xml",
                    "action": f"Update {integ.label} mapping",
                    "owner": "Integration",
                }
            )

    def _dedup_components(self, impact: ImpactResult) -> None:
        """Remove duplicate components from impact result."""
        seen_ids = set()

        for attr_name in [
            "affected_screens", "affected_workflows", "affected_processes",
            "affected_code", "affected_databases", "affected_integrations",
            "affected_downstream_systems"
        ]:
            components = getattr(impact, attr_name)
            deduped = []
            for comp in components:
                if comp.id not in seen_ids:
                    deduped.append(comp)
                    seen_ids.add(comp.id)
            setattr(impact, attr_name, deduped)

    def _assess_risk(self, impact: ImpactResult) -> None:
        """Assess overall risk level based on impact scope."""
        component_count = len(impact.all_affected_components())
        blind_spot_count = len(impact.blind_spots)

        if blind_spot_count >= 2:
            impact.risk_level = "CRITICAL"
            impact.risk_reasons.append(f"{blind_spot_count} critical blind spots identified")
        elif component_count > 10:
            impact.risk_level = "HIGH"
            impact.risk_reasons.append(f"Wide impact scope: {component_count} components affected")
        elif component_count > 5:
            impact.risk_level = "MEDIUM"
            impact.risk_reasons.append(f"Moderate impact scope: {component_count} components affected")
        else:
            impact.risk_level = "LOW"
            impact.risk_reasons.append("Localized impact")

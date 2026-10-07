"""Blind Spot Detector — Phase 3: Systematic Blind Spot Detection.

Identifies potential gaps and risks in the technical impact analysis.
Checks for missing schema, missing integrations, downstream incompatibilities, etc.
"""

from __future__ import annotations

from typing import Any

from app.lifecycle.stages.analysis.technical_intent.models import (
    BlindSpot,
    ImpactResult,
    TechnicalPattern,
)
from app.utils.logging import log


class BlindSpotDetector:
    """Detect blind spots and potential gaps in impact analysis.

    Runs systematic checks against affected components to identify what might
    be missing from the impact chain.
    """

    def __init__(self, graph: Any):
        """Initialize detector with a knowledge graph.

        Args:
            graph: Graph object for lookups
        """
        self.graph = graph

    async def detect_all(self, impact: ImpactResult) -> list[BlindSpot]:
        """Run all blind spot detection checks.

        Args:
            impact: ImpactResult from tracer

        Returns:
            List of detected blind spots, sorted by severity
        """
        blind_spots = []

        log.info("[BLIND-SPOT] Running comprehensive blind spot detection...")

        # Run all checks
        blind_spots.extend(await self._check_schema_consistency(impact))
        blind_spots.extend(await self._check_integration_coverage(impact))
        blind_spots.extend(await self._check_downstream_readiness(impact))
        blind_spots.extend(await self._check_code_coverage(impact))
        blind_spots.extend(await self._check_cross_system_dependencies(impact))
        blind_spots.extend(await self.detect_schema_gaps(impact))  # NEW: Reference schema gaps

        # Sort by severity (CRITICAL > HIGH > MEDIUM > LOW)
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        blind_spots.sort(key=lambda bs: severity_order.get(bs.severity, 99))

        log.info(f"[BLIND-SPOT] Detected {len(blind_spots)} blind spot(s)")
        for bs in blind_spots:
            log.info(f"  [{bs.severity}] {bs.type}: {bs.description[:60]}...")

        return blind_spots

    async def _check_schema_consistency(self, impact: ImpactResult) -> list[BlindSpot]:
        """Check if all affected tables have the new field in schema.

        For field additions, verify that every table in the impact chain
        has been updated to support the new field.
        """
        blind_spots = []

        if impact.pattern != TechnicalPattern.FIELD_ADDITION:
            return blind_spots

        log.info("[BLIND-SPOT-SCHEMA] Checking schema consistency...")

        if not impact.extraction.fields:
            return blind_spots

        field_name = impact.extraction.fields[0].name

        # Check each affected table
        for table in impact.affected_databases:
            has_field = await self._table_has_field(table.id, field_name)

            if not has_field:
                blind_spots.append(
                    BlindSpot(
                        severity="CRITICAL",
                        type="MISSING_SCHEMA",
                        description=f"Column '{field_name}' not found in table {table.label}",
                        action=f"Run: ALTER TABLE {table.label} ADD COLUMN {field_name} ...",
                        impact="Data will be lost or database errors will occur when code tries to write field",
                    )
                )
                log.warning(f"[BLIND-SPOT-SCHEMA] Missing field: {field_name} in {table.label}")

        return blind_spots

    async def _check_integration_coverage(self, impact: ImpactResult) -> list[BlindSpot]:
        """Check if all ESB/integration routes know about the new field.

        Verify that integration mappings include the new field so it can be
        transmitted downstream.
        """
        blind_spots = []

        if impact.pattern != TechnicalPattern.FIELD_ADDITION:
            return blind_spots

        log.info("[BLIND-SPOT-INTEG] Checking integration coverage...")

        if not impact.extraction.fields:
            return blind_spots

        field_name = impact.extraction.fields[0].name

        # Check each integration
        for integ in impact.affected_integrations:
            has_mapping = await self._integration_maps_field(integ.id, field_name)

            if not has_mapping:
                blind_spots.append(
                    BlindSpot(
                        severity="HIGH",
                        type="MISSING_INTEGRATION_MAPPING",
                        description=f"Integration '{integ.label}' does not map field '{field_name}'",
                        action=f"Update {integ.label} configuration to include {field_name} in payload mapping",
                        impact="Field value will not be transmitted to downstream systems; may cause validation or data loss downstream",
                    )
                )
                log.warning(f"[BLIND-SPOT-INTEG] Missing mapping: {field_name} in {integ.label}")

        return blind_spots

    async def _check_downstream_readiness(self, impact: ImpactResult) -> list[BlindSpot]:
        """Check if downstream systems can accept the new field.

        Verify that target systems in the integration chain are compatible
        with the new field.
        """
        blind_spots = []

        if impact.pattern != TechnicalPattern.FIELD_ADDITION:
            return blind_spots

        log.info("[BLIND-SPOT-DOWN] Checking downstream system compatibility...")

        if not impact.extraction.fields:
            return blind_spots

        field_name = impact.extraction.fields[0].name

        # Check each downstream system
        for system in impact.affected_downstream_systems:
            accepts_field = await self._system_accepts_field(system.id, field_name)

            if not accepts_field:
                blind_spots.append(
                    BlindSpot(
                        severity="HIGH",
                        type="DOWNSTREAM_INCOMPATIBILITY",
                        description=f"Downstream system '{system.label}' may not accept field '{field_name}'",
                        action=f"Coordinate with {system.label} team to verify schema/API compatibility for '{field_name}'",
                        impact="Integration may fail at runtime; policy/transaction binding may fail if system rejects unknown field",
                    )
                )
                log.warning(f"[BLIND-SPOT-DOWN] Downstream incompatibility: {system.label} and {field_name}")

        return blind_spots

    async def _check_code_coverage(self, impact: ImpactResult) -> list[BlindSpot]:
        """Find code that might need updates but wasn't found in direct impact chain.

        This catches code files that touch the affected tables but weren't
        directly found via the graph walk.
        """
        blind_spots = []

        log.info("[BLIND-SPOT-CODE] Checking code coverage...")

        if not impact.affected_databases:
            return blind_spots

        # For each table, find ALL code that touches it (not just direct neighbors)
        for table in impact.affected_databases:
            all_touching_code = await self._find_code_touching_table(table.id)
            mapped_code_ids = {c.id for c in impact.affected_code}

            unmapped = [c for c in all_touching_code if c.id not in mapped_code_ids]

            if unmapped:
                code_names = ", ".join([c.label for c in unmapped])
                blind_spots.append(
                    BlindSpot(
                        severity="MEDIUM",
                        type="MISSING_CODE_REFERENCE",
                        description=f"{len(unmapped)} code file(s) touch table {table.label} but were not found in impact chain: {code_names}",
                        action=f"Review these code files for schema-dependent logic that might break: {code_names}",
                        impact="Code may fail if it contains hard-coded schema assumptions about table {table.label}",
                    )
                )
                log.warning(f"[BLIND-SPOT-CODE] Unmapped code: {len(unmapped)} files for table {table.label}")

        return blind_spots

    async def _check_cross_system_dependencies(self, impact: ImpactResult) -> list[BlindSpot]:
        """Check for cross-system dependencies that might be affected.

        Identifies systems that depend on the changed system but weren't
        explicitly found in the impact chain.
        """
        blind_spots = []

        log.info("[BLIND-SPOT-CROSS] Checking cross-system dependencies...")

        # Find all systems that depend on affected systems
        dependent_systems = set()
        for system in impact.affected_downstream_systems:
            deps = await self._find_dependent_systems(system.id)
            dependent_systems.update([d.id for d in deps])

        # Check if any dependent system was missed
        mapped_system_ids = {s.id for s in impact.affected_downstream_systems}
        unmapped_system_ids = dependent_systems - mapped_system_ids

        if unmapped_system_ids:
            blind_spots.append(
                BlindSpot(
                    severity="MEDIUM",
                    type="UNTRACED_DOWNSTREAM_DEPENDENCIES",
                    description=f"{len(unmapped_system_ids)} additional downstream system(s) may be affected via dependencies",
                    action="Map out full dependency chain and coordinate with all affected system teams",
                    impact="Changes may have cascading effects on systems multiple hops downstream that were not accounted for",
                )
            )
            log.warning(f"[BLIND-SPOT-CROSS] {len(unmapped_system_ids)} untraced downstream dependencies")

        return blind_spots

    async def detect_schema_gaps(self, impact: ImpactResult) -> list[BlindSpot]:
        """Detect missing fields using reference schema definitions.

        For FIELD_ADDITION and DATABASE_SCHEMA_CHANGE patterns, check if target
        tables have the fields they SHOULD have to support the requested capability
        (e.g., delivery tracking requires delivery_status + delivery_timestamp fields).

        This uses reference_schemas.py to identify schema evolution requirements.

        Args:
            impact: ImpactResult from tracer

        Returns:
            List of BlindSpots for missing required fields
        """
        from app.config.reference_schemas import ENTITY_SCHEMAS, CAPABILITY_FIELD_MAP, get_schema_gaps

        blind_spots = []

        if impact.pattern not in (TechnicalPattern.FIELD_ADDITION, TechnicalPattern.DATABASE_SCHEMA_CHANGE):
            return blind_spots

        log.info("[BLIND-SPOT-SCHEMA-GAP] Checking reference schema requirements...")

        # Determine what capability is being requested
        # For delivery-related requirements, check for delivery tracking fields
        requirement_text = impact.requirement_text.lower() if impact.requirement_text else ""
        delivery_keywords = ["delivery", "email", "notification", "tracking", "status"]
        payment_keywords = ["payment", "reconciliation", "settlement", "matched"]

        capability = None
        if any(kw in requirement_text for kw in delivery_keywords):
            capability = "delivery_tracking"
        elif any(kw in requirement_text for kw in payment_keywords):
            capability = "payment_reconciliation"

        if not capability:
            return blind_spots

        # Check each affected database entity against reference schema
        for table in impact.affected_databases:
            schema = ENTITY_SCHEMAS.get(table.label)
            if not schema:
                continue

            # Get expected fields for this capability
            if capability == "delivery_tracking":
                expected_fields = schema.get("expected_delivery_tracking_fields", [])
            elif capability == "payment_reconciliation":
                expected_fields = schema.get("expected_payment_tracking_fields", [])
            else:
                expected_fields = []

            existing_fields = set(schema.get("existing_fields", []))

            # Check for missing fields
            for field_name in expected_fields:
                if field_name not in existing_fields:
                    blind_spots.append(
                        BlindSpot(
                            severity="MEDIUM",
                            type="SCHEMA_GAP",
                            description=f"Table {table.label} missing '{field_name}' field required for {capability}",
                            action=f"Add column: ALTER TABLE {table.label} ADD COLUMN {field_name} ...",
                            impact=schema.get("gap_if_missing", "Feature will not work as specified"),
                        )
                    )
                    log.warning(
                        f"[BLIND-SPOT-SCHEMA-GAP] Missing field '{field_name}' in {table.label} (capability: {capability})"
                    )

        return blind_spots

    # Helper methods (async stubs — implement with actual graph queries)

    async def _table_has_field(self, table_id: str, field_name: str) -> bool:
        """Check if table has a specific field."""
        # Stub: in real implementation, would query schema graph or metadata
        try:
            if not self.graph:
                return False
            # Check if table node has this field in properties
            node = await self.graph.get_node(table_id)
            if not node:
                return False
            fields = node.get("fields", [])
            return any(f.get("name") == field_name for f in fields)
        except Exception as e:
            log.warning(f"[BLIND-SPOT] Failed to check field in table: {e}")
            return False

    async def _integration_maps_field(self, integration_id: str, field_name: str) -> bool:
        """Check if integration mapping includes a specific field."""
        try:
            if not self.graph:
                return False
            node = await self.graph.get_node(integration_id)
            if not node:
                return False
            mappings = node.get("field_mappings", [])
            return any(m.get("source_field") == field_name or m.get("target_field") == field_name for m in mappings)
        except Exception as e:
            log.warning(f"[BLIND-SPOT] Failed to check integration mapping: {e}")
            return False

    async def _system_accepts_field(self, system_id: str, field_name: str) -> bool:
        """Check if system schema accepts a specific field."""
        try:
            if not self.graph:
                return False
            node = await self.graph.get_node(system_id)
            if not node:
                return False
            accepted_fields = node.get("accepted_fields", [])
            if not accepted_fields:
                # If no restriction defined, assume it accepts it
                return True
            return field_name in accepted_fields
        except Exception as e:
            log.warning(f"[BLIND-SPOT] Failed to check system compatibility: {e}")
            return False

    async def _find_code_touching_table(self, table_id: str) -> list[Any]:
        """Find all code files that reference a table."""
        try:
            if not self.graph:
                return []
            # Code that depends on or references this table
            return await self.graph.get_edges_reverse(table_id, "DEPENDS_ON")
        except Exception:
            return []

    async def _find_dependent_systems(self, system_id: str) -> list[Any]:
        """Find systems that depend on this system."""
        try:
            if not self.graph:
                return []
            return await self.graph.get_edges_reverse(system_id, "DEPENDS_ON")
        except Exception:
            return []

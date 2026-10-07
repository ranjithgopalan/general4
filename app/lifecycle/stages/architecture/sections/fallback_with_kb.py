"""KB-Grounded Fallback Generation — verify inferred designs against real KB data.

When architecture sections are empty, generate fallbacks BUT verify/fill from KB
to ensure real values (table names, protocols, queues, endpoints) are used.

Fallback strategy:
  1. Try KB lookup for the design element (table schema, integration pattern, API, component)
  2. If found: use REAL values from KB, mark as kb_lookup, higher confidence
  3. If not found: use pure inference, mark as inferred, lower confidence
  4. Always include kb_reference and confidence score
"""

from __future__ import annotations

import re
from typing import Any

from app.lifecycle.stages.analysis.schema import ImpactCitation, ScopeDiff
from app.lifecycle.stages.architecture.schema import (
    ApiSpec,
    IntegrationPoint,
    SchemaChange,
    SystemComponent,
)


def _extract_field_name(requirement: str) -> str:
    """Extract field/column name from requirement text.

    Examples:
      'Add Campaign Code to...' → 'campaign_code'
      'new Payment Method field' → 'payment_method'
    """
    # Match "Add X" or "new X" pattern
    match = re.search(r"(?:Add|new)\s+([A-Za-z\s]+?)(?:\s+(?:field|column|to|for)|$)", requirement)
    if match:
        raw = match.group(1).strip()
        # Convert to snake_case
        return re.sub(r"\s+", "_", raw).lower()
    return "new_field"


def _extract_field_size(requirement: str) -> int:
    """Extract field size from requirement text.

    Examples:
      'max 50 characters' → 50
      'maximum 255' → 255
    """
    match = re.search(r"(?:max|maximum)\s+(\d+)\s*(?:char|byte|size)", requirement, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 50  # Default


def _normalize_table_name(table_name: str) -> str:
    """Normalize table name to lowercase with proper separator.

    Examples:
      'ASACDP.quote' → 'asacdp.quote'
      'ASACDP_quote' → 'asacdp.quote'
    """
    normalized = table_name.lower()
    normalized = normalized.replace("_", ".")
    return normalized


class FallbackWithKBLookup:
    """Generate fallbacks grounded in KB data."""

    def __init__(self, kb_service):
        """Initialize with KB query service."""
        self.kb = kb_service

    async def build_schema_changes_with_kb(
        self,
        requirement: str,
        scope: ScopeDiff,
        enriched: dict[str, Any] | None = None,
    ) -> list[SchemaChange]:
        """Schema changes with real table names/columns from KB.

        Strategy:
          1. Identify database systems in scope (ASACDP, Oracle, etc.)
          2. Query KB for real table schema
          3. Use real table names from KB
          4. Infer column name/type from requirement
          5. Mark confidence based on KB lookup success
        """
        schema_changes = enriched.get("schema_changes", []) if enriched else []

        if not schema_changes and scope and scope.enhancement:
            # Identify database systems in scope
            db_systems = [
                s
                for s in scope.enhancement
                if any(
                    db in s.label
                    for db in ["database", "ASACDP", "Oracle", "DB", "table", "schema"]
                )
            ]

            field_name = _extract_field_name(requirement)
            field_size = _extract_field_size(requirement)

            for db_sys in db_systems:
                # STEP 1: Query KB for schema
                try:
                    kb_results = await self.kb.query(
                        f"{db_sys.label} table schema columns",
                        top_k=3,
                    )

                    if kb_results and len(kb_results) > 0:
                        kb_card = kb_results[0]  # First result is most relevant
                        real_table = kb_card.get("id") or db_sys.label.lower()
                        kb_id = kb_card.get("id")

                        # STEP 2: Create schema change with KB-verified table name
                        schema_changes.append(
                            SchemaChange(
                                table=_normalize_table_name(real_table),
                                op="ADD COLUMN",
                                column=field_name,
                                column_type=f"VARCHAR({field_size})",
                                rationale=requirement[:100],
                                kb_id=kb_id,
                                source_type="kb_lookup",
                                confidence=0.75,  # Higher: verified in KB
                                kb_reference=kb_id,
                            )
                        )
                    else:
                        # STEP 3: Fallback if KB has no schema info
                        schema_changes.append(
                            SchemaChange(
                                table=_normalize_table_name(db_sys.label),
                                op="ADD COLUMN",
                                column=field_name,
                                column_type=f"VARCHAR({field_size})",
                                rationale=f"{requirement[:100]} (table name inferred, not verified in KB)",
                                source_type="inferred",
                                confidence=0.25,
                            )
                        )
                except Exception as e:  # noqa: BLE001
                    # If KB query fails, use pure inference
                    schema_changes.append(
                        SchemaChange(
                            table=_normalize_table_name(db_sys.label),
                            op="ADD COLUMN",
                            column=field_name,
                            column_type=f"VARCHAR({field_size})",
                            rationale=f"{requirement[:100]} (KB lookup failed: {str(e)[:50]})",
                            source_type="inferred",
                            confidence=0.2,
                        )
                    )

        return schema_changes

    async def build_integrations_with_kb(
        self,
        requirement: str,
        scope: ScopeDiff,
        enriched: dict[str, Any] | None = None,
    ) -> list[IntegrationPoint]:
        """Integrations with real protocols/queues/endpoints from KB.

        Strategy:
          1. For each system in scope, query KB for integration patterns
          2. If found: use REAL protocol, queue, endpoint from KB
          3. If not found: use pure inference with lower confidence
        """
        integrations = enriched.get("integration_design", []) if enriched else []

        if not integrations and scope and scope.enhancement:
            for sys in scope.enhancement:
                try:
                    # STEP 1: Query KB for this system's integration pattern
                    kb_results = await self.kb.query(
                        f"{sys.label} integration protocol queue endpoint",
                        top_k=1,
                    )

                    if kb_results and len(kb_results) > 0:
                        kb_card = kb_results[0]

                        # STEP 2: Extract REAL values from KB
                        protocol = kb_card.get("protocol", "Unknown")
                        queue_name = kb_card.get("queue_name") or kb_card.get("endpoint")
                        direction = kb_card.get("direction", "sync")
                        kb_id = kb_card.get("id")

                        integrations.append(
                            IntegrationPoint(
                                id=f"INT-KB-{kb_id[:10]}",
                                label=sys.label,
                                protocol=protocol,
                                direction=direction,
                                from_component="",
                                to_component="",
                                source_type="kb_explicit",
                                confidence=0.90,
                                kb_reference=kb_id,
                            )
                        )
                    else:
                        # STEP 3: Fallback to inference
                        inferred_protocol = _infer_protocol(sys.label)
                        integrations.append(
                            IntegrationPoint(
                                id=f"INT-INFERRED-{len(integrations):03d}",
                                label=sys.label,
                                protocol=inferred_protocol,
                                direction="sync",
                                source_type="inferred",
                                confidence=0.30,
                            )
                        )
                except Exception as e:  # noqa: BLE001
                    # KB lookup failed, use inference
                    integrations.append(
                        IntegrationPoint(
                            id=f"INT-INFERRED-{len(integrations):03d}",
                            label=sys.label,
                            protocol=_infer_protocol(sys.label),
                            direction="sync",
                            source_type="inferred",
                            confidence=0.20,
                        )
                    )

        return integrations

    async def build_api_specs_with_kb(
        self,
        requirement: str,
        component_design: list[SystemComponent] | None = None,
        enriched: dict[str, Any] | None = None,
    ) -> list[ApiSpec]:
        """API specs with patterns from KB.

        Strategy:
          1. Look for similar API patterns in KB
          2. Extract HTTP method, base path, auth from KB
          3. Fill in request/response fields from requirement
        """
        api_specs = enriched.get("api_specs", []) if enriched else []

        if not api_specs:
            try:
                # STEP 1: Query KB for similar REST API patterns
                kb_results = await self.kb.query(
                    "REST API POST endpoint request response schema",
                    top_k=1,
                )

                field_name = _extract_field_name(requirement)

                if kb_results and len(kb_results) > 0:
                    kb_card = kb_results[0]

                    # STEP 2: Extract REAL API pattern from KB
                    base_path = kb_card.get("base_path", "/api/v1")
                    http_method = kb_card.get("http_method", "POST")
                    kb_id = kb_card.get("id")

                    api_specs.append(
                        ApiSpec(
                            id=f"API-KB-{kb_id[:10]}",
                            label=f"{http_method} {base_path}/{field_name}",
                            http_method=http_method,
                            path=f"{base_path}/{field_name}",
                            request_fields=[field_name, "resource_id"],
                            response_fields=["status", "message"],
                            source_type="kb_lookup",
                            confidence=0.65,
                            kb_reference=kb_id,
                        )
                    )
                else:
                    # STEP 3: Fallback to inference
                    api_specs.append(
                        ApiSpec(
                            id="API-INFERRED-001",
                            label=f"POST /api/v1/{field_name}",
                            http_method="POST",
                            path=f"/api/v1/{field_name}",
                            request_fields=[field_name, "resource_id"],
                            response_fields=["status", "message"],
                            source_type="inferred",
                            confidence=0.30,
                        )
                    )
            except Exception as e:  # noqa: BLE001
                # KB lookup failed
                field_name = _extract_field_name(requirement)
                api_specs.append(
                    ApiSpec(
                        id="API-INFERRED-001",
                        label=f"POST /api/v1/{field_name}",
                        http_method="POST",
                        path=f"/api/v1/{field_name}",
                        request_fields=[field_name, "resource_id"],
                        response_fields=["status"],
                        source_type="inferred",
                        confidence=0.20,
                    )
                )

        return api_specs


def _infer_protocol(system_label: str) -> str:
    """Infer protocol from system name.

    Heuristics:
      'ESB' / 'queue' / 'message' → SOAP/XML or Message Queue
      'PEGA' → SOAP/XML
      'Database' / 'Oracle' → JDBC/SQL
      Default → REST
    """
    label_lower = system_label.lower()

    if any(x in label_lower for x in ["esb", "pega", "message", "queue"]):
        return "SOAP/XML"
    if any(x in label_lower for x in ["database", "oracle", "postgres", "mysql"]):
        return "JDBC/SQL"
    if any(x in label_lower for x in ["api", "rest", "http"]):
        return "REST"

    return "REST"  # Default fallback

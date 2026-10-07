"""Build graph edges from code-to-table relationships.

Converts TableReference findings into graph edges:
- TABLE ← QUERIED_BY ← CMP/API (code reads from table)
- TABLE ← WRITES_TO ← CMP/API (code writes to table)
- SCREEN ← READS ← TABLE (screen displays table data)

This is Phase 2 of comprehensive table impact discovery.
"""

from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

from app.lifecycle.stages.analysis.extractors.table_reference import TableReference, TableOperation
from app.utils.logging import log


class TableEdgeType(str, Enum):
    """Types of edges between code and tables."""
    QUERIED_BY = "QUERIED_BY"  # Table is read by code
    WRITES_TO = "WRITES_TO"    # Code writes to table (INSERT/UPDATE/DELETE)
    READS_FROM = "READS_FROM"  # Screen/component reads from table
    INSERTS_INTO = "INSERTS_INTO"  # Explicitly INSERT
    UPDATES = "UPDATES"        # Explicitly UPDATE
    DELETES_FROM = "DELETES_FROM"  # Explicitly DELETE


@dataclass
class TableEdge:
    """Graph edge connecting table to code component."""
    from_id: str  # ENT-JAUTO-DB-051 (table)
    to_id: str    # CMP-JAUTO-DB-003 (code component)
    edge_type: TableEdgeType
    reason: str   # "SELECT WEB_RECEIPTS at line 42"
    source_locus: str  # "input/Auto/CodeBase/...sql:line-42"
    confidence: float = 0.95

    def to_graph_edge(self) -> Dict:
        """Convert to graph.json edge format."""
        return {
            "from": self.from_id,
            "to": self.to_id,
            "label": self.edge_type.value,
            "source_locus": self.source_locus,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class TableEdgeBuilder:
    """Build graph edges from table references."""

    @staticmethod
    def map_operation_to_edge_type(operation: TableOperation) -> TableEdgeType:
        """Map SQL operation to edge type.

        Args:
            operation: TableOperation from extraction

        Returns:
            Corresponding TableEdgeType
        """
        mapping = {
            TableOperation.SELECT: TableEdgeType.QUERIED_BY,
            TableOperation.INSERT: TableEdgeType.INSERTS_INTO,
            TableOperation.UPDATE: TableEdgeType.UPDATES,
            TableOperation.DELETE: TableEdgeType.DELETES_FROM,
            TableOperation.JOIN: TableEdgeType.QUERIED_BY,
            TableOperation.WRITE: TableEdgeType.WRITES_TO,
        }
        return mapping.get(operation, TableEdgeType.WRITES_TO)

    @staticmethod
    def create_edges_from_references(
        table_refs: List[TableReference],
        code_component_id: str,
        source_file_path: str,
    ) -> List[TableEdge]:
        """Create graph edges from table references in a code component.

        Args:
            table_refs: List of TableReference objects from extraction
            code_component_id: ID of the code component (e.g., CMP-JAUTO-DB-003)
            source_file_path: Path to source file for locus

        Returns:
            List of TableEdge objects ready for graph ingestion
        """
        edges = []

        for ref in table_refs:
            # Only create edges if we resolved the ENT ID
            if not ref.ent_id:
                log.debug(
                    f"[table-edges] Skipping unknown table: {ref.table_name} "
                    f"(line {ref.line} in {source_file_path})"
                )
                continue

            edge_type = TableEdgeBuilder.map_operation_to_edge_type(ref.operation)

            edge = TableEdge(
                from_id=ref.ent_id,
                to_id=code_component_id,
                edge_type=edge_type,
                reason=f"{ref.operation.value} {ref.table_name} at line {ref.line}",
                source_locus=f"{source_file_path}:line-{ref.line}",
                confidence=ref.confidence,
            )

            edges.append(edge)

            log.debug(
                f"[table-edges] Created edge: {ref.ent_id} ← {edge_type.value} ← {code_component_id}"
            )

        return edges

    @staticmethod
    def add_edges_to_graph(
        graph_data: Dict,
        edges: List[TableEdge],
    ) -> Dict:
        """Add table-usage edges to graph.json.

        Args:
            graph_data: Graph JSON structure (with 'edges' key)
            edges: List of TableEdge objects to add

        Returns:
            Updated graph_data
        """
        if 'edges' not in graph_data:
            graph_data['edges'] = []

        added_count = 0
        for edge in edges:
            graph_data['edges'].append(edge.to_graph_edge())
            added_count += 1

        log.info(f"[table-edges] Added {added_count} table-usage edges to graph")
        return graph_data


class TableUsageAnalyzer:
    """Analyze code components for table usage patterns."""

    @staticmethod
    def analyze_code_component(
        component_id: str,
        component_kind: str,
        source_code: str,
        source_language: str,
        source_path: str,
    ) -> Dict:
        """Analyze a code component for table references.

        Returns a dict with:
        {
            'component_id': 'CMP-JAUTO-DB-003',
            'tables_referenced': [TableReference, ...],
            'edges': [TableEdge, ...],
            'has_database_operations': bool,
            'primary_operations': ['SELECT', 'INSERT', ...],
        }

        Args:
            component_id: KB ID of code component
            component_kind: 'CMP', 'API', 'StoredProc', etc.
            source_code: Source code content
            source_language: 'sql', 'java', 'typescript', etc.
            source_path: Path for locus tracking

        Returns:
            Analysis dict
        """
        from app.lifecycle.stages.analysis.extractors.table_reference import extract_table_references

        # Extract table references
        table_refs = extract_table_references(
            code=source_code,
            language=source_language,
            source_path=source_path,
        )

        # Create edges
        edges = TableEdgeBuilder.create_edges_from_references(
            table_refs=table_refs,
            code_component_id=component_id,
            source_file_path=source_path,
        )

        # Collect unique operations
        operations = set(ref.operation.value for ref in table_refs)

        return {
            'component_id': component_id,
            'component_kind': component_kind,
            'tables_referenced': table_refs,
            'edges': edges,
            'has_database_operations': len(table_refs) > 0,
            'primary_operations': sorted(list(operations)),
            'table_count': len(set(ref.table_name for ref in table_refs)),
        }

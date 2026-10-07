"""Table reference extraction from source code.

Identifies where code components read/write tables, creating evidence
for graph edges: TABLE ← QUERIED_BY ← CODE COMPONENT.
"""

import re
from typing import List, Optional
from dataclasses import dataclass
from enum import Enum

from app.utils.logging import log


class TableOperation(str, Enum):
    """Type of table operation detected in code."""
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    JOIN = "JOIN"
    WRITE = "WRITE"  # Generic INSERT/UPDATE/DELETE


@dataclass
class TableReference:
    """Single table reference found in code."""
    table_name: str
    ent_id: Optional[str]  # Resolved KB entity ID, if known
    operation: TableOperation
    line: int
    match_text: str  # The matched text from source
    confidence: float = 0.95  # Regex extraction confidence
    source_path: Optional[str] = None


class TableReferenceExtractor:
    """Extract table references from source code files."""

    # Known table → ENT ID mapping (from KB, built during stage initialization)
    # This is initialized from domain-pack.json tableMapping
    TABLE_TO_ENT_ID = {
        # Japan Auto specific mappings
        'WEB_RECEIPTS': 'ENT-JAUTO-DB-051',
        'WEB_RECEIPTS_HISTORY': 'ENT-JAUTO-DB-053',
        'DRCT_MSG_REGST': 'ENT-JAUTO-DB-055',
        'PEGA_CUSTOMERS': 'ENT-JAUTO-DB-060',
        'RECEIPT_DETAILS': 'ENT-JAUTO-DB-062',
        'POLICY_INFO': 'ENT-JAUTO-DB-065',
        'CLAIM_HISTORY': 'ENT-JAUTO-DB-068',
        'PAYMENT_RECORDS': 'ENT-JAUTO-DB-070',
    }

    # SQL patterns to find table references
    # Pattern order matters — more specific patterns first
    SQL_PATTERNS = [
        # SELECT FROM table_name
        (r'\bFROM\s+(?:aig\.)?\s*(\w+)', 'SELECT'),
        # INSERT INTO table_name
        (r'\bINSERT\s+INTO\s+(?:aig\.)?\s*(\w+)', 'INSERT'),
        # UPDATE table_name
        (r'\bUPDATE\s+(?:aig\.)?\s*(\w+)', 'UPDATE'),
        # DELETE FROM table_name
        (r'\bDELETE\s+FROM\s+(?:aig\.)?\s*(\w+)', 'DELETE'),
        # JOIN table_name
        (r'\b(?:INNER\s+|OUTER\s+|LEFT\s+|RIGHT\s+)?JOIN\s+(?:aig\.)?\s*(\w+)', 'JOIN'),
    ]

    @classmethod
    def set_table_mapping(cls, mapping: dict[str, str]) -> None:
        """Update the table-to-ENT mapping from domain pack.

        Args:
            mapping: Dict from table_name to ENT-* ID
        """
        cls.TABLE_TO_ENT_ID.update(mapping)
        log.info(f"[table-extractor] Updated table mapping with {len(mapping)} entries")

    @staticmethod
    def extract_from_sql(sql_code: str, source_path: Optional[str] = None) -> List[TableReference]:
        """Extract all table references from SQL source code.

        Args:
            sql_code: SQL source code (single or multiple statements)
            source_path: Optional source file path (for logging)

        Returns:
            List of TableReference objects found in the code
        """
        references = []
        lines = sql_code.split('\n')

        # Convert to uppercase for pattern matching (preserve original for display)
        sql_upper = sql_code.upper()

        for line_num, line in enumerate(lines, 1):
            line_upper = line.upper()

            for pattern, operation in TableReferenceExtractor.SQL_PATTERNS:
                try:
                    matches = re.finditer(pattern, line_upper, re.IGNORECASE)

                    for match in matches:
                        table_name = match.group(1).strip()

                        # Skip system tables
                        if table_name.startswith('SYS_') or table_name in ['INFORMATION_SCHEMA']:
                            continue

                        # Resolve ENT ID if known
                        ent_id = TableReferenceExtractor.TABLE_TO_ENT_ID.get(table_name)

                        ref = TableReference(
                            table_name=table_name,
                            ent_id=ent_id,
                            operation=TableOperation(operation),
                            line=line_num,
                            match_text=match.group(0).strip(),
                            source_path=source_path,
                        )

                        references.append(ref)

                except Exception as e:
                    log.warning(
                        f"[table-extractor] Error matching pattern on line {line_num}: {e}"
                    )

        return references

    @staticmethod
    def extract_from_java(java_code: str, source_path: Optional[str] = None) -> List[TableReference]:
        """Extract table references from Java code.

        Looks for:
        - JDBC: statement.executeQuery("SELECT * FROM table")
        - Spring Data: @Query("SELECT ... FROM table")
        - Hibernate: @Table(name = "table")
        - Prepared statements with column references

        Args:
            java_code: Java source code
            source_path: Optional source file path

        Returns:
            List of TableReference objects
        """
        references = []
        lines = java_code.split('\n')

        patterns = [
            # JDBC executeQuery/executeUpdate
            (r'execute(?:Query|Update)\s*\(\s*["\']([^"\']*)\b(?:FROM|INTO|UPDATE)\s+(\w+)', 'SELECT'),
            # @Query annotation
            (r'@Query\s*\(\s*["\']([^"\']*FROM\s+(\w+))', 'SELECT'),
            # @Table annotation
            (r'@Table\s*\(\s*name\s*=\s*["\'](\w+)["\']', 'SELECT'),
            # Prepared statement string with table
            (r'(?:INSERT|UPDATE|DELETE|SELECT).*?\b(?:FROM|INTO|FROM)\s+(\w+)', 'WRITE'),
        ]

        for line_num, line in enumerate(lines, 1):
            for pattern, operation in patterns:
                try:
                    # Try to extract table name from the last capturing group
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        # Get the last group which should be the table name
                        groups = match.groups()
                        if groups:
                            table_name = groups[-1].strip().upper()

                            # Skip system/framework tables
                            if any(prefix in table_name for prefix in ['SYS_', 'SPRING_', 'HIBERNATE_']):
                                continue

                            ent_id = TableReferenceExtractor.TABLE_TO_ENT_ID.get(table_name)

                            ref = TableReference(
                                table_name=table_name,
                                ent_id=ent_id,
                                operation=TableOperation(operation),
                                line=line_num,
                                match_text=match.group(0).strip(),
                                source_path=source_path,
                            )

                            references.append(ref)

                except Exception as e:
                    log.debug(f"[table-extractor] Java pattern match issue on line {line_num}: {e}")

        return references

    @staticmethod
    def extract_from_typescript(ts_code: str, source_path: Optional[str] = None) -> List[TableReference]:
        """Extract table references from TypeScript/JavaScript code.

        Looks for:
        - SQL query strings: SELECT * FROM table
        - Parameterized queries: FROM table_name
        - Database client calls: client.query("SELECT ... FROM table")

        Args:
            ts_code: TypeScript/JavaScript source code
            source_path: Optional source file path

        Returns:
            List of TableReference objects
        """
        references = []
        lines = ts_code.split('\n')

        # Look for SQL in string literals and template literals
        patterns = [
            # Template literals: SELECT * FROM `table`
            (r'FROM\s+`(\w+)`', 'SELECT'),
            # String literals: "SELECT ... FROM table"
            (r'["\'].*?SELECT.*?\bFROM\s+(\w+)', 'SELECT'),
            (r'["\'].*?INSERT\s+INTO\s+(\w+)', 'INSERT'),
            (r'["\'].*?UPDATE\s+(\w+)', 'UPDATE'),
            (r'["\'].*?DELETE\s+FROM\s+(\w+)', 'DELETE'),
        ]

        for line_num, line in enumerate(lines, 1):
            for pattern, operation in patterns:
                try:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        table_name = match.group(1).strip().upper()

                        if table_name.startswith('SYS_'):
                            continue

                        ent_id = TableReferenceExtractor.TABLE_TO_ENT_ID.get(table_name)

                        ref = TableReference(
                            table_name=table_name,
                            ent_id=ent_id,
                            operation=TableOperation(operation),
                            line=line_num,
                            match_text=match.group(0).strip(),
                            source_path=source_path,
                        )

                        references.append(ref)

                except Exception as e:
                    log.debug(f"[table-extractor] TypeScript pattern issue on line {line_num}: {e}")

        return references


def extract_table_references(code: str, language: str, source_path: Optional[str] = None) -> List[TableReference]:
    """Dispatcher function to extract table references by language.

    Args:
        code: Source code text
        language: Programming language ('sql', 'java', 'typescript', 'javascript')
        source_path: Optional source file path

    Returns:
        List of table references found
    """
    language_lower = language.lower()

    if language_lower in ('sql', 'plpgsql'):
        return TableReferenceExtractor.extract_from_sql(code, source_path)
    elif language_lower == 'java':
        return TableReferenceExtractor.extract_from_java(code, source_path)
    elif language_lower in ('typescript', 'javascript', 'ts', 'js'):
        return TableReferenceExtractor.extract_from_typescript(code, source_path)
    else:
        log.debug(f"[table-extractor] Unsupported language: {language}")
        return []

"""Edge pattern mappings — abstract edge semantics from graph label variations.

When graphs evolve, edge labels may change (DEPENDS_ON vs DEPENDS_UPON, etc.).
This module maps semantic patterns to all known label aliases, so graph walks
stay robust across schema changes.
"""

from enum import Enum


class EdgePattern(Enum):
    """Semantic edge patterns — what the relationship MEANS, not what it's labeled."""

    CODE_TO_DATA = "code_touches_data"  # Code component reads/writes/produces/depends on data entity
    SYSTEM_OWNS_CODE = "system_owns_code"  # System owns/hosts/implements code components
    SYSTEM_TO_SYSTEM = "system_integration"  # System calls/integrates/depends on another system


EDGE_LABEL_ALIASES = {
    EdgePattern.CODE_TO_DATA: [
        "PRODUCES",
        "WRITES_TO",
        "READS_FROM",
        "DEPENDS_ON",
        "QUERIES",
        "PERSISTED_BY",
        "CONSUMES",
        "GENERATES",
    ],
    EdgePattern.SYSTEM_OWNS_CODE: [
        "OWNED_BY",
        "PART_OF",
        "CONTAINS",
        "IMPLEMENTS",
        "HOSTS",
        "OWNS",
        "MANAGES",
    ],
    EdgePattern.SYSTEM_TO_SYSTEM: [
        "CALLS",
        "FEEDS_INTO",
        "INTEGRATES_WITH",
        "DEPENDS_ON",
        "TRIGGERS",
        "SENDS_TO",
        "RECEIVES_FROM",
        "SYNCS_WITH",
    ],
}


def resolve_edge_pattern(pattern: EdgePattern | list[str]) -> list[str]:
    """Resolve an edge pattern (enum or list of labels) to all known aliases.

    Args:
        pattern: Either an EdgePattern enum (resolved to aliases) or a list of label strings

    Returns:
        List of edge labels to match against
    """
    if isinstance(pattern, EdgePattern):
        return EDGE_LABEL_ALIASES.get(pattern, [])
    else:
        return pattern if isinstance(pattern, list) else [pattern]

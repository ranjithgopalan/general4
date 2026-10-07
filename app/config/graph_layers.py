"""
Knowledge-graph layer bands + ID-family taxonomy (presentation config).

A node's **layer** (its swimlane band in the Graph Explorer's layered/traceability mode) is a
pure function of its **ID family** — the prefix of its stable KB id (``ENT-JAUTO-001`` → ``ENT``).
It is computed HERE at query time and returned in the ``/re/graph`` DTO; it is **not** a stored
column (docs/09 §3.4). Re-banding, splitting, or reordering bands = edit this file only — the
"config not core-edit" rule.

In v2 this same map can move into the domain pack (``graphLayers``) so each LOB tunes its own bands.
"""

from __future__ import annotations

# Ordered architecture swimlane bands, top → bottom — the TRACEABILITY FLOW (who → what → how →
# governed-by → data → code → wiring → platform). Reading top-to-bottom follows the natural traverse:
#   Actor/Role  →  Process & Workflow  →  Presentation (Screen)  →  Business Rules  →
#   Domain/Entity  →  Components & APIs  →  Integration Fabric  →  System / Platform
# So a role OPERATES a workflow (ROLE_IN), the workflow is realized THROUGH screens (SCREEN_OF), each
# screen is GOVERNED_BY rules, REFERENCES entities, implemented by code, wired via integrations, on a
# platform. Process sits ABOVE Presentation so role→workflow→screen reads as a top-down flow (not
# role→screen→workflow). `split` → laned AU (left) / AUW (right); `shared` → full-width cross-cutting.
GRAPH_LAYERS: list[dict] = [
    # Actors/roles are the distribution channel (agents, support) serving BOTH AU + AUW — a shared band,
    # not AU/AUW-laned. (Any future AUW-specific role still renders here; category-agnostic full width.)
    {"key": "actor", "title": "Actor / Role", "order": 0, "kinds": ["ROLE"], "split": False, "shared": True},
    {
        "key": "process",
        "title": "Process & Workflow",
        "order": 1,
        "kinds": ["WF", "PROC", "SEQ", "STM"],
        "split": True,
        "shared": False,
    },
    {"key": "presentation", "title": "Presentation", "order": 2, "kinds": ["SCR"], "split": True, "shared": False},
    {"key": "rules", "title": "Business Rules", "order": 3, "kinds": ["BR", "FR"], "split": True, "shared": False},
    {
        "key": "entity",
        "title": "Domain / Entity",
        "order": 4,
        "kinds": ["ENT", "REL", "DOM", "TERM", "ALIAS"],
        "split": True,
        "shared": False,
    },
    {"key": "code", "title": "Components & APIs", "order": 5, "kinds": ["CMP", "API"], "split": True, "shared": False},
    {
        "key": "integration",
        "title": "Integration Fabric",
        "order": 6,
        "kinds": ["INT", "EXT"],
        "split": False,
        "shared": True,
    },
    {"key": "system", "title": "System / Platform", "order": 7, "kinds": ["SYS"], "split": False, "shared": True},
]

# ID family → human label (Neo4j node label). 18 families per CLAUDE.md / base-taxonomy.
FAMILY_LABELS = {
    "ENT": "Entity",
    "REL": "Relationship",
    "PROC": "Process",
    "WF": "Workflow",
    "SEQ": "Sequence",
    "STM": "State Machine",
    "SCR": "Screen",
    "BR": "Business Rule",
    "FR": "Functional Req",
    "SYS": "System",
    "INT": "Integration",
    "ROLE": "Role",
    "TERM": "Term",
    "DOM": "Domain",
    "ALIAS": "Alias",
    "EXT": "External",
    "API": "API",
    "CMP": "Component",
}

_LAYER_BY_FAMILY = {fam: band["key"] for band in GRAPH_LAYERS for fam in band["kinds"]}

# Fallback: a stored Neo4j label / freeform ``kind`` → family, when the id has no usable prefix.
_KIND_TO_FAMILY = {
    "entity": "ENT",
    "relationship": "REL",
    "process": "PROC",
    "workflow": "WF",
    "sequence": "SEQ",
    "statemachine": "STM",
    "state machine": "STM",
    "screen": "SCR",
    "businessrule": "BR",
    "business rule": "BR",
    "functionalreq": "FR",
    "functional req": "FR",
    "system": "SYS",
    "integration": "INT",
    "role": "ROLE",
    "term": "TERM",
    "domain": "DOM",
    "alias": "ALIAS",
    "external": "EXT",
    "apiop": "API",
    "api": "API",
    "component": "CMP",
}


def family_of(node_id: str, kind: str | None = None) -> str:
    """ID family of a node — 'ENT-JAUTO-001' → 'ENT'. Falls back to mapping ``kind``."""
    prefix = node_id.split("-", 1)[0].upper() if node_id else ""
    if prefix in FAMILY_LABELS:
        return prefix
    return _KIND_TO_FAMILY.get((kind or "").strip().lower(), prefix or "ENT")


def family_label(family: str) -> str:
    return FAMILY_LABELS.get(family, family)


def layer_of(family: str) -> str:
    """Swimlane band key for a family (unknown families fall to the Domain/Entity band)."""
    return _LAYER_BY_FAMILY.get(family, "entity")

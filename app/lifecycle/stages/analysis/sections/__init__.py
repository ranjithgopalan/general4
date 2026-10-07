"""Per-section builders for the ImpactAnalysis (modular — each is swappable to its own sub-agent).

- ``deterministic`` — graph-derived sections (touch points, change locations).
- ``reasoned`` — LLM-derived sections (classification, scope, modifications, risks, drift, narrative,
  gaps, effort), each whitelist-filtered to real ids.
- ``assemble`` — combines them into the typed ImpactAnalysis.
"""

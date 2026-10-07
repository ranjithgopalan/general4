"""Deterministic graph-match engine for the ANALYSIS stage (no LLM).

Produces the grounded context the OPEN ReAct agent reasons over: matched cards (retrieve), the impact
set + downstream "what breaks" + coverage (walk), conflicts with existing nodes (conflicts), assembled
into a ContextPackage (context). Reusable by future stage agents.
"""

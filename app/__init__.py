"""AIG Core AIDLC Platform — Agents (FE backend).

FastAPI + LangGraph service that turns the grounded KB into SDLC artifacts through
persona specialists. This package is the base framework (plumbing); business logic
(LangGraph supervisor, persona ReAct specialists, kb.query, grounding spine,
fe.generate subgraphs) lands on top as `# TODO(business):` seams.
"""

__version__ = "0.1.0"

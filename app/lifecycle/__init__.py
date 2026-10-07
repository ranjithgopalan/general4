"""Forward-engineering (FE) stage modules — one package per SDLC lifecycle stage.

Each stage lives under ``app/fe/stages/<stage>/`` with a ``handler`` (orchestration), a ``schema``
(typed artifact), ``prompts/`` (LLM, externalized) and ``patterns/`` (deterministic helpers).
Templates live in ``app/fe/templates/`` (single source), rendered by ``app/fe/render/`` to HTML
(screen) and official AIG ``.docx`` (export). Structure adopted from lmod's stage-as-package layout.
"""

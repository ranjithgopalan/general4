"""Technical Intent Analysis System.

4-phase pipeline for analyzing technical impact of requirements:
1. LLM Intent Analysis (LLMIntentAnalyzer via Haiku) — Pattern detection + entity extraction
2. Graph-Based Impact Tracing (ImpactTracer)
3. Blind Spot Detection (BlindSpotDetector)
4. Change List Generation (ChangeListGenerator)

Pure LLM-based (Haiku) intent analysis — no regex patterns.

Key exports:
- TechnicalIntentAnalyzer: Main orchestrator (use this)
- LLMIntentAnalyzer: Haiku-based intent analysis (used internally)
- ImpactResult: Output structure with complete analysis
- TechnicalPattern: Detected pattern enum
"""

from app.lifecycle.stages.analysis.technical_intent.intent_analyzer_llm import LLMIntentAnalyzer
from app.lifecycle.stages.analysis.technical_intent.models import (
    BlindSpot,
    ChangeTask,
    FileChange,
    ImpactResult,
    TechnicalExtraction,
    TechnicalPattern,
)
from app.lifecycle.stages.analysis.technical_intent.orchestrator import TechnicalIntentAnalyzer

__all__ = [
    "TechnicalIntentAnalyzer",
    "LLMIntentAnalyzer",
    "ImpactResult",
    "TechnicalPattern",
    "TechnicalExtraction",
    "FileChange",
    "BlindSpot",
    "ChangeTask",
]

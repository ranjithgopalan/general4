"""LLM-Based Intent Analyzer — Enhanced Pattern & Entity Detection via Haiku.

Uses Claude Haiku to understand technical intent when regex patterns don't match,
or to refine extraction when confidence is low.

Fallback/enhancement layer on top of regex-based TechnicalIntentExtractor.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.lifecycle.stages.analysis.technical_intent.models import (
    FieldSpec,
    TechnicalExtraction,
    TechnicalPattern,
)
from app.utils.logging import log

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "intent_analyzer.json"


@lru_cache(maxsize=1)
def _load_intent_prompt() -> str:
    """Load the intent analyzer prompt template (cached)."""
    try:
        return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]
    except Exception as e:
        log.error(f"Failed to load intent analyzer prompt: {e}")
        raise


class LLMIntentAnalyzer:
    """LLM-based intent analyzer for technical requirements.

    Uses the config-routed LLM to:
    1. Detect patterns when regex doesn't match (fallback)
    2. Refine entity extraction when confidence is low (enhancement)
    3. Infer unstated intent from requirement context

    Model: id resolved from config.json via the ``classify`` routing task (MODEL_ROUTING_JSON) — NOT
    hardcoded — so it picks up whatever ``classify`` maps to (currently ``sonnet`` = claude-sonnet-4-6).
    Region is fixed to ``us-east-1`` (the Bedrock region); temperature 0.3 / max-tokens 1024.
    Input: Requirement text + optionally regex extraction results
    Output: Enhanced TechnicalExtraction with higher confidence
    """

    def __init__(self, model_name: str | None = None):
        """Initialize the LLM analyzer.

        Args:
            model_name: Optional explicit Bedrock model id override. When ``None`` (default), the model
                is resolved from config.json via the ``classify`` routing task (no hardcoded id).
        """
        self.model_name = model_name
        self.prompt_template = _load_intent_prompt()
        self._model = None

    def _get_model(self) -> Any | None:
        """Lazy-load ChatBedrockConverse model.

        Returns None if unavailable (handles missing langchain_aws or Bedrock errors).
        """
        if self._model is None:
            try:
                from langchain_aws import ChatBedrockConverse

                from app.config.settings import get_settings

                # Model id comes from config.json (MODEL_ROUTING classify -> sonnet); an explicit
                # model_name overrides. Region is fixed to us-east-1 (the Bedrock region — do not change).
                model_id = self.model_name or get_settings().model_id_for("classify")
                if not model_id:
                    self._model = False
                else:
                    self._model = ChatBedrockConverse(
                        model=model_id,
                        region_name="us-east-1",
                        temperature=0.3,
                        max_tokens=1024,
                    )
            except Exception as e:
                log.error(f"[intent-analyzer-llm] Failed to create model: {e}")
                self._model = False  # Cache the failure

        return self._model or None

    async def analyze_intent(
        self,
        requirement: str,
        regex_extraction: TechnicalExtraction | None = None,
    ) -> TechnicalExtraction:
        """Analyze technical intent using LLM.

        Args:
            requirement: Raw requirement text
            regex_extraction: Optional result from regex extractor (to enhance)

        Returns:
            Enhanced TechnicalExtraction with LLM analysis

        Raises:
            json.JSONDecodeError: If LLM response is not valid JSON
            Exception: If LLM call fails
        """
        if not requirement or not requirement.strip():
            raise ValueError("Requirement text cannot be empty")

        log.info("[INTENT-LLM] === LLM INTENT ANALYSIS ===")
        log.info(f"[INTENT-LLM] INPUT: requirement ({len(requirement)} chars)")
        log.info(f"[INTENT-LLM]   Text: {requirement[:100]}...")

        if regex_extraction:
            log.info(f"[INTENT-LLM] INPUT: regex extraction result")
            log.info(f"[INTENT-LLM]   Pattern: {regex_extraction.pattern.name}")
            log.info(f"[INTENT-LLM]   Confidence: {regex_extraction.confidence:.0%}")
            log.info(f"[INTENT-LLM]   Target screen: {regex_extraction.target_screen}")
            log.info(f"[INTENT-LLM]   Fields: {[f.name for f in regex_extraction.fields]}")

        # Get model
        model = self._get_model()
        if model is None:
            log.warning("[INTENT-LLM] ChatBedrockConverse not available, returning regex result")
            return regex_extraction or TechnicalExtraction(pattern=TechnicalPattern.UNKNOWN)

        # Build user message
        user_msg = f"REQUIREMENT:\n{requirement}"
        if regex_extraction:
            user_msg += f"\n\nREGEX EXTRACTION RESULT:\n"
            user_msg += f"  Pattern: {regex_extraction.pattern.name}\n"
            user_msg += f"  Confidence: {regex_extraction.confidence:.0%}\n"
            user_msg += f"  Target Screen: {regex_extraction.target_screen}\n"
            user_msg += f"  Fields: {[f.name for f in regex_extraction.fields]}\n"
            user_msg += f"  Affected Flows: {regex_extraction.flows_affected}\n"

        try:
            from app.config.settings import get_settings

            resolved_model = self.model_name or get_settings().model_id_for("classify") or "unavailable"
            log.info("[INTENT-LLM] PROCESSING: calling the config-routed classify LLM")
            log.info(f"[INTENT-LLM]   Model: {resolved_model}")
            log.info("[INTENT-LLM]   Strategy: Semantic intent analysis + entity refinement")

            from langchain_core.messages import HumanMessage, SystemMessage

            response = await model.ainvoke([
                SystemMessage(content=self.prompt_template),
                HumanMessage(content=user_msg)
            ])

            text = response.content

            # Strip markdown code blocks if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:].lstrip()
                text = text.rstrip()

            # Parse JSON response
            result_json = json.loads(text)

            # Build enhanced extraction
            pattern_name = result_json.get("pattern", "UNKNOWN")
            try:
                pattern = TechnicalPattern[pattern_name]
            except KeyError:
                pattern = TechnicalPattern.UNKNOWN

            extraction = TechnicalExtraction(
                pattern=pattern,
                target_screen=result_json.get("target_screen"),
                target_table=result_json.get("target_table"),
                integration_type=result_json.get("integration_type"),
                source_system=result_json.get("source_system"),
                target_system=result_json.get("target_system"),
                confidence=result_json.get("confidence", 0.8),
                explanation=result_json.get("explanation", ""),
            )

            # Parse fields
            fields_list = result_json.get("fields", [])
            for field_data in fields_list:
                extraction.fields.append(
                    FieldSpec(
                        name=field_data.get("name", ""),
                        type=field_data.get("type"),
                        size=field_data.get("size"),
                        nullable=field_data.get("nullable", True),
                        default_value=field_data.get("default_value"),
                    )
                )

            # Parse affected flows
            extraction.flows_affected = result_json.get("flows_affected", [])

            # Parse data to pipe
            extraction.data_to_pipe = result_json.get("data_to_pipe", [])

            # === DEBUG: OUTPUT ===
            log.info("[INTENT-LLM] OUTPUT: Enhanced TechnicalExtraction")
            log.info(f"[INTENT-LLM]   Pattern: {extraction.pattern.name}")
            log.info(f"[INTENT-LLM]   Confidence: {extraction.confidence:.0%}")
            log.info(f"[INTENT-LLM]   Target screen: {extraction.target_screen}")
            log.info(f"[INTENT-LLM]   Target table: {extraction.target_table}")
            log.info(f"[INTENT-LLM]   Fields: {[f.name for f in extraction.fields]}")
            log.info(f"[INTENT-LLM]   Flows affected: {extraction.flows_affected}")
            log.info(f"[INTENT-LLM]   Explanation: {extraction.explanation[:100]}...")

            return extraction

        except json.JSONDecodeError as e:
            log.error(f"[intent-analyzer-llm] Invalid JSON from LLM: {text[:200]}")
            raise ValueError(f"Invalid JSON from LLM intent analyzer: {e}") from e
        except Exception as e:
            log.error(f"[intent-analyzer-llm] LLM call failed: {e}")
            raise

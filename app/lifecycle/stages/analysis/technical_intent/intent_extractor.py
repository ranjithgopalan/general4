"""Technical Intent Extractor — Phase 1: Pattern Recognition.

Detects technical patterns (field addition, screen creation, etc.) and extracts
specific entities from requirements.
"""

from __future__ import annotations

import re
from typing import Any

from app.lifecycle.stages.analysis.technical_intent.models import (
    FieldSpec,
    TechnicalExtraction,
    TechnicalPattern,
)
from app.utils.logging import log


class TechnicalIntentExtractor:
    """Extract technical intent from requirements.

    Identifies which technical pattern a requirement matches, then extracts
    specific entities (screen name, field specs, etc.) based on the pattern.

    No LLM — pure pattern matching and regex.
    """

    # Pattern trigger regexes (case-insensitive)
    PATTERN_TRIGGERS = {
        TechnicalPattern.FIELD_ADDITION: [
            r'add\s+(?:field|column|attribute)',
            r'(?:new|create)\s+(?:field|column)',
            r'add.*field.*to',
            r'include.*(?:field|column)',
        ],
        TechnicalPattern.FIELD_REMOVAL: [
            r'remove\s+(?:field|column)',
            r'delete\s+(?:field|column)',
            r'drop\s+(?:field|column)',
        ],
        TechnicalPattern.FIELD_RENAME: [
            r'rename\s+(?:field|column)',
            r'change\s+(?:field|column)\s+name',
        ],
        TechnicalPattern.FIELD_TYPE_CHANGE: [
            r'change\s+(?:field|column)\s+type',
            r'modify\s+(?:field|column)\s+type',
        ],
        TechnicalPattern.SCREEN_CREATION: [
            r'create\s+(?:a\s+)?(?:new\s+)?screen',
            r'design\s+(?:a\s+)?(?:new\s+)?screen',
            r'new\s+(?:ui|user\s+interface|page|tab)',
        ],
        TechnicalPattern.SCREEN_MODIFICATION: [
            r'modify\s+(?:the\s+)?screen',
            r'update\s+(?:the\s+)?screen',
            r'change\s+(?:the\s+)?screen',
        ],
        TechnicalPattern.SCREEN_DELETION: [
            r'remove\s+(?:the\s+)?screen',
            r'delete\s+(?:the\s+)?screen',
        ],
        TechnicalPattern.INTEGRATION_ADDITION: [
            r'add\s+(?:esb|integration|route|endpoint)',
            r'create\s+(?:esb|integration)',
            r'pipe\s+(?:data|message)\s+to',
        ],
        TechnicalPattern.INTEGRATION_MODIFICATION: [
            r'modify\s+(?:esb|integration)',
            r'update\s+(?:esb|integration)',
        ],
        TechnicalPattern.PROCESS_MODIFICATION: [
            r'modify\s+(?:process|workflow)',
            r'update\s+(?:process|workflow|flow)',
            r'change\s+(?:process|workflow)',
        ],
        TechnicalPattern.API_ENDPOINT_ADD: [
            r'add\s+(?:api|endpoint)',
            r'create\s+(?:api|endpoint)',
        ],
    }

    def detect_pattern(self, requirement: str) -> TechnicalPattern:
        """Detect which pattern this requirement matches.

        Args:
            requirement: Raw requirement text

        Returns:
            TechnicalPattern enum value (or UNKNOWN if no match)
        """
        if not requirement or not requirement.strip():
            return TechnicalPattern.UNKNOWN

        req_lower = requirement.lower()

        # Try to match against each pattern
        for pattern, triggers in self.PATTERN_TRIGGERS.items():
            for trigger in triggers:
                if re.search(trigger, req_lower):
                    log.info(f"[INTENT] Pattern detected: {pattern.name} (matched: {trigger})")
                    return pattern

        log.warning(f"[INTENT] No pattern matched for requirement: {requirement[:80]}...")
        return TechnicalPattern.UNKNOWN

    def extract_entities(
        self, requirement: str, pattern: TechnicalPattern
    ) -> TechnicalExtraction:
        """Extract specific entities based on pattern.

        Args:
            requirement: Raw requirement text
            pattern: Detected pattern type

        Returns:
            TechnicalExtraction with extracted entities
        """
        extraction = TechnicalExtraction(pattern=pattern)

        if pattern == TechnicalPattern.FIELD_ADDITION:
            extraction = self._extract_field_addition(requirement)
        elif pattern == TechnicalPattern.INTEGRATION_ADDITION:
            extraction = self._extract_integration_addition(requirement)
        elif pattern == TechnicalPattern.SCREEN_CREATION:
            extraction = self._extract_screen_creation(requirement)
        else:
            # Generic extraction for other patterns
            extraction.explanation = "Pattern recognized but entity extraction not implemented for this pattern type"
            extraction.confidence = 0.3

        log.info(f"[INTENT] Extraction complete: {extraction.explanation}")
        return extraction

    def _extract_field_addition(self, requirement: str) -> TechnicalExtraction:
        """Extract details for field addition pattern."""
        extraction = TechnicalExtraction(pattern=TechnicalPattern.FIELD_ADDITION)

        # Extract screen name (e.g., "Basic Information Screen", "Basic Info")
        screen_match = re.search(
            r'(?:to|in)\s+(?:the\s+)?(?:basic\s+information|basic\s+info|screening|submission|review|confirmation|summary|detail)'
            r'(?:\s+screen)?',
            requirement,
            re.IGNORECASE
        )
        if screen_match:
            extraction.target_screen = screen_match.group(0).replace('to', '').replace('in', '').strip()

        # Extract field name (e.g., "campaign_code", "Campaign Code")
        field_match = re.search(
            r'(?:field|column)\s+(?:named|called|for)?\s*["\']?([a-zA-Z_][a-zA-Z0-9_\s]*)["\']?',
            requirement,
            re.IGNORECASE
        )
        if not field_match:
            # Try alternative pattern: "Add X to screen"
            field_match = re.search(
                r'add\s+(?:the\s+)?([a-zA-Z_][a-zA-Z0-9_\s]+)\s+(?:field|column)',
                requirement,
                re.IGNORECASE
            )
        if not field_match:
            # Try: "Add X and Y fields"
            field_match = re.search(
                r'add\s+([a-zA-Z_][a-zA-Z0-9_\s]+)\s+(?:to|field)',
                requirement,
                re.IGNORECASE
            )

        if field_match:
            field_name = field_match.group(1).strip().replace(' ', '_').lower()
            extraction.fields.append(FieldSpec(name=field_name))
            extraction.confidence = 0.85

        # Extract field type (e.g., "VARCHAR(20)", "INT", "BOOLEAN")
        type_match = re.search(
            r'(?:type|data\s+type|as)\s+(?:a\s+)?([A-Z]+)(?:\((\d+)\))?',
            requirement,
            re.IGNORECASE
        )
        if type_match and extraction.fields:
            extraction.fields[0].type = type_match.group(1).upper()
            if type_match.group(2):
                extraction.fields[0].size = int(type_match.group(2))

        # Extract affected flows (e.g., "Renewal", "New Business")
        if re.search(r'renewal', requirement, re.IGNORECASE):
            extraction.flows_affected.append("Renewal")
        if re.search(r'new\s+business', requirement, re.IGNORECASE):
            extraction.flows_affected.append("New Business")
        if re.search(r'quote', requirement, re.IGNORECASE):
            extraction.flows_affected.append("Quotation")

        if extraction.fields and extraction.target_screen:
            extraction.confidence = max(extraction.confidence, 0.85)
        elif extraction.fields or extraction.target_screen:
            extraction.confidence = max(extraction.confidence, 0.6)

        extraction.explanation = (
            f"Field addition: {extraction.fields[0].name if extraction.fields else '?'} "
            f"to {extraction.target_screen or 'screen'}"
        )
        return extraction

    def _extract_integration_addition(self, requirement: str) -> TechnicalExtraction:
        """Extract details for integration addition pattern."""
        extraction = TechnicalExtraction(pattern=TechnicalPattern.INTEGRATION_ADDITION)

        # Extract source and target systems
        systems = self._find_systems_in_text(requirement)
        if len(systems) >= 2:
            extraction.source_system = systems[0]
            extraction.target_system = systems[1]
        elif systems:
            extraction.target_system = systems[0]

        # Determine integration type
        if re.search(r'esb', requirement, re.IGNORECASE):
            extraction.integration_type = "ESB"
        elif re.search(r'api', requirement, re.IGNORECASE):
            extraction.integration_type = "API"
        elif re.search(r'etl', requirement, re.IGNORECASE):
            extraction.integration_type = "ETL"

        extraction.confidence = 0.7
        extraction.explanation = (
            f"Integration: {extraction.integration_type or '?'} "
            f"from {extraction.source_system or '?'} to {extraction.target_system or '?'}"
        )
        return extraction

    def _extract_screen_creation(self, requirement: str) -> TechnicalExtraction:
        """Extract details for screen creation pattern."""
        extraction = TechnicalExtraction(pattern=TechnicalPattern.SCREEN_CREATION)

        # Look for screen name
        screen_match = re.search(
            r'(?:screen|page|tab|interface)(?:\s+(?:named|called|for))?(?:\s+["\']?([a-zA-Z\s]+)["\']?)?',
            requirement,
            re.IGNORECASE
        )
        if screen_match and screen_match.group(1):
            extraction.target_screen = screen_match.group(1).strip()

        extraction.confidence = 0.75
        extraction.explanation = f"Screen creation: {extraction.target_screen or 'new screen'}"
        return extraction

    def _find_systems_in_text(self, text: str) -> list[str]:
        """Find system names in text (JA Connect, PEGA, JODS, etc.)."""
        systems = []
        system_keywords = [
            "AIG Connect", "PEGA", "WOD", "JODS", "FREIA", "ESB",
            "analytics", "reporting", "billing", "policy"
        ]

        for system in system_keywords:
            if re.search(system, text, re.IGNORECASE):
                systems.append(system)

        return systems

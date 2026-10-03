"""RYVEN 3.0 — Milestone 16.1 Complexity Classifier.

Determines whether a user goal is SIMPLE, MODERATE, or COMPLEX, selecting
the optimal PlanningMode (DIRECT, DETERMINISTIC, or LLM_ASSISTED) to ensure
sub-millisecond speed on basic tasks while providing cognitive depth for multi-domain workflows.
"""

from __future__ import annotations

import re
from typing import Tuple

from app.agents.planning_models import GoalComplexity, PlanningMode


class ComplexityClassifier:
    """Classifies goal complexity and assigns optimal planning mode."""

    # Simple single-action regexes (no dependencies, no confirmation)
    SIMPLE_PATTERNS = [
        # Desktop app launch
        r"^(?:open|launch|start)\s+(?:the\s+)?(?:visual\s+studio\s+code|vs\s*code|vscode|code|chrome|google\s+chrome|edge|microsoft\s+edge|notepad|calc|calculator|windows\s+terminal|terminal|wt|file\s+explorer|explorer)$",
        # Simple web search
        r"^(?:search|google|find)\s+(?:the\s+)?(?:web|internet|google)?\s*(?:for\s+)?(?:[^&]+)$",
        # Simple URL navigation
        r"^(?:open|navigate|go\s+to|visit)\s+(?:https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9_\-]+\.(?:com|org|io|dev|net|edu))$",
        # System telemetry queries
        r"^(?:show\s+|get\s+|check\s+)?(?:system\s+(?:status|telemetry|metrics)(?:\s+and\s+telemetry)?|cpu|ram|memory|disk|battery)$",
        # Clipboard actions
        r"^(?:get\s+clipboard|set\s+clipboard|copy\s+clipboard|paste)$",
        # Safe health checks
        r"^(?:check\s+health|health\s+check)(?:\s+of\s+[^\s]+)?$",
        # Direct cancellation
        r"^(?:stop|cancel|abort|halt|stop\s+everything)$",
    ]

    # Complex keywords indicating multi-domain or conditional workflows
    COMPLEX_INDICATORS = [
        "deploy",
        "publish",
        "commit and push",
        "quality gate",
        "end to end",
        "full pipeline",
        "test and deploy",
        "analyze and fix",
        "explain and implement",
        "research this technology",
        "tested project",
    ]

    @classmethod
    def classify(cls, normalized_goal: str) -> Tuple[GoalComplexity, PlanningMode, str]:
        """Classify complexity and select planning mode.

        Returns:
            Tuple of (GoalComplexity, PlanningMode, classification_reason)
        """
        clean = normalized_goal.strip()
        if not clean:
            return GoalComplexity.SIMPLE, PlanningMode.DIRECT, "Empty goal"

        lower = clean.lower()

        # 1. Check for cancellation
        if lower in ("cancel active tasks", "stop everything", "cancel this task", "cancel all"):
            return GoalComplexity.SIMPLE, PlanningMode.DIRECT, "Direct cancellation request"

        # 2. Check for explicit complex indicators requiring cognitive LLM planning
        if any(ind in lower for ind in cls.COMPLEX_INDICATORS):
            return (
                GoalComplexity.COMPLEX,
                PlanningMode.LLM_ASSISTED if len(clean.split()) > 5 else PlanningMode.HYBRID,
                "Multi-domain workflow involving research, development, or deployment",
            )

        # 2b. Research and Build standard pattern (Multi-domain: RESEARCH -> DEVELOPER -> VERIFICATION)
        is_research_and_dev = "research" in lower and any(w in lower for w in ("build", "create", "demo", "project"))
        if is_research_and_dev:
            return (
                GoalComplexity.COMPLEX,
                PlanningMode.DETERMINISTIC,
                "Structured multi-domain workflow involving research and project synthesis",
            )


        # 3. Check for Simple Patterns
        for pat in cls.SIMPLE_PATTERNS:
            if re.match(pat, lower, re.IGNORECASE):
                # Ensure no multi-verb connector like "and then", "and build"
                if not re.search(r"\b(?:and\s+then|then|followed\s+by|after\s+that)\b", lower):
                    return GoalComplexity.SIMPLE, PlanningMode.DIRECT, "Single direct tool execution without dependencies"

        # 4. Check for Moderate Patterns (multi-step sequential in a single domain)
        # e.g., "Create a React project named TaskFlow, build it, and test it"
        is_composite_dev = any(w in lower for w in ("create", "scaffold", "build", "modify")) and \
                           any(w in lower for w in ("test", "run", "verify", "and"))
        is_composite_browser = any(w in lower for w in ("open", "launch")) and \
                              any(w in lower for w in ("search", "find", "click", "read"))

        if is_composite_dev or is_composite_browser:
            return GoalComplexity.MODERATE, PlanningMode.DETERMINISTIC, "Sequential multi-step workflow within single domain"

        # 5. Length and ambiguity check
        word_count = len(clean.split())
        if word_count > 12 or "?" in clean or "should" in lower or "maybe" in lower:
            return GoalComplexity.COMPLEX, PlanningMode.LLM_ASSISTED, "Complex or open-ended natural language request"

        # Default fallback to Moderate Deterministic
        return GoalComplexity.MODERATE, PlanningMode.DETERMINISTIC, "Standard structured task decomposition"


complexity_classifier = ComplexityClassifier()

"""RYVEN 3.0 — Milestone 16.1 Intent & Capability Analyzer.

Extracts structured domain actions and maps intent to registered AgentCapabilities.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agents.models import AgentCapability
from app.agents.planning_models import ExtractedIntent, PlanRisk


class IntentAnalyzer:
    """Analyzes normalized goal strings to extract structured intent and capabilities."""

    CONSEQUENTIAL_ACTIONS = {
        "git_commit",
        "git_push",
        "deploy",
        "form_submit",
        "delete_file",
        "publish",
    }

    @classmethod
    def analyze(cls, normalized_goal: str, params: Optional[Dict[str, Any]] = None) -> ExtractedIntent:
        """Extract structured intent, target entity, domains, and capabilities."""
        clean = normalized_goal.strip()
        lower = clean.lower()
        parameters = dict(params or {})

        domains: List[str] = []
        capabilities: List[AgentCapability] = []
        primary_action = "coordinate"
        target_entity: Optional[str] = parameters.get("app_name") or parameters.get("project_name") or parameters.get("query")
        target_project: Optional[str] = parameters.get("project_name")

        # 1. Desktop & App Domain
        if any(w in lower for w in ("open visual studio code", "open google chrome", "open notepad", "open calculator", "open windows terminal")):
            domains.extend(["DESKTOP", "WINDOWS", "COMPUTER"])
            capabilities.append(AgentCapability.WINDOWS_APP)
            primary_action = "open_application"

        # 2. Web & Research Domain
        if any(w in lower for w in ("search the web", "search web", "google", "find on web")):
            domains.append("RESEARCH")
            capabilities.append(AgentCapability.WEB_SEARCH)
            primary_action = "search"
        elif "research" in lower:
            domains.append("RESEARCH")
            capabilities.append(AgentCapability.WEB_RESEARCH)
            capabilities.append(AgentCapability.PAGE_READING)
            primary_action = "research"

        # 3. Project Creation & Development Domain
        if any(w in lower for w in ("create a", "build a", "scaffold a")) and ("project" in lower or "app" in lower):
            domains.append("DEVELOPMENT")
            capabilities.append(AgentCapability.PROJECT_SCAN)
            capabilities.append(AgentCapability.CODE_GENERATION)
            capabilities.append(AgentCapability.CODE_MODIFICATION)
            primary_action = "create_project"

        if "build" in lower:
            if "DEVELOPMENT" not in domains:
                domains.append("DEVELOPMENT")
            if AgentCapability.BUILD not in capabilities:
                capabilities.append(AgentCapability.BUILD)

        if "test" in lower:
            if "VERIFICATION" not in domains:
                domains.append("VERIFICATION")
            if AgentCapability.TEST not in capabilities:
                capabilities.append(AgentCapability.TEST)

        # 4. Git & Deployment Domain
        if any(w in lower for w in ("commit", "git", "push")):
            if "DEVELOPMENT" not in domains:
                domains.append("DEVELOPMENT")
            if AgentCapability.GIT not in capabilities:
                capabilities.append(AgentCapability.GIT)

        if "deploy" in lower:
            if "DEPLOYMENT" not in domains:
                domains.append("DEPLOYMENT")
            if AgentCapability.DEPLOY not in capabilities:
                capabilities.append(AgentCapability.DEPLOY)

        # 5. System & Health Domain
        if any(w in lower for w in ("system status", "telemetry", "metrics", "cpu", "ram")):
            domains.append("SYSTEM")
            capabilities.append(AgentCapability.SYSTEM_STATUS)
            primary_action = "system_status"

        if "health" in lower:
            domains.append("VERIFICATION")
            capabilities.append(AgentCapability.HEALTH_CHECK)

        # 6. Cancellation Domain
        if "cancel" in lower or "stop" in lower:
            domains.append("COORDINATION")
            primary_action = "cancel"

        # Check consequential & confirmation requirements
        is_consequential = any(act in lower for act in ("commit", "push", "deploy", "delete", "publish", "submit"))
        requires_confirmation = is_consequential

        # Fallback capability if empty
        if not capabilities:
            capabilities.append(AgentCapability.TASK_COORDINATION)
            domains.append("COORDINATION")

        return ExtractedIntent(
            raw_goal=normalized_goal,
            normalized_goal=normalized_goal,
            primary_action=primary_action,
            target_entity=target_entity,
            target_project=target_project,
            domains=domains,
            requested_capabilities=capabilities,
            is_consequential=is_consequential,
            requires_confirmation=requires_confirmation,
            parameters=parameters,
            missing_critical_info=None,
        )


intent_analyzer = IntentAnalyzer()


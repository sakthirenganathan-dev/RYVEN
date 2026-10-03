"""RYVEN 3.0 — Milestone 16.1 Goal Normalizer.

Standardizes messy, abbreviated, or colloquial user input into canonical
action phrasing while strictly preserving user intent and parameters.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from app.agents.models import redact_secrets


# Common shorthand application mappings
APP_SYNONYMS: Dict[str, str] = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "calc": "Calculator",
    "calculator": "Calculator",
    "notepad": "Notepad",
    "edge": "Microsoft Edge",
    "microsoft edge": "Microsoft Edge",
    "terminal": "Windows Terminal",
    "windows terminal": "Windows Terminal",
    "wt": "Windows Terminal",
    "explorer": "File Explorer",
    "file explorer": "File Explorer",
}

# Project framework mappings
FRAMEWORK_CANONICAL: Dict[str, str] = {
    "react": "React",
    "python": "Python",
    "fastapi": "FastAPI",
    "web": "Web",
    "html": "Web",
    "vanilla": "Web",
    "node": "Node",
}


class GoalNormalizer:
    """Normalizes natural language goals into consistent canonical representations."""

    @classmethod
    def normalize(cls, raw_goal: str) -> Tuple[str, Dict[str, Any], Optional[str]]:
        """Normalize a raw user goal string.

        Returns:
            Tuple of (normalized_goal, extracted_params, missing_info_notice)
        """
        clean = " ".join(raw_goal.strip().split())
        clean = redact_secrets(clean)
        lower = clean.lower()

        extracted: Dict[str, Any] = {}
        missing_info: Optional[str] = None

        # 1. Application Launch Normalization
        app_match = re.match(
            r"^(?:open|launch|start|run)\s+(?:the\s+)?(vs\s*code|vscode|code|chrome|google\s+chrome|edge|microsoft\s+edge|notepad|calc|calculator|windows\s+terminal|terminal|wt|file\s+explorer|explorer)\b(?:\s+(.*))?$",
            clean,
            re.IGNORECASE,
        )
        if app_match:
            app_raw = app_match.group(1).lower().strip()
            rest = (app_match.group(2) or "").strip()
            canonical_app = APP_SYNONYMS.get(app_raw, app_raw.title())
            extracted["app_name"] = canonical_app
            if rest:
                normalized = f"Open {canonical_app} and {rest}"
            else:
                normalized = f"Open {canonical_app}"
            return normalized, extracted, None

        # 2. Web Search Normalization
        search_match = re.match(
            r"^(?:search|google|find|look\s+up|query)\s+(?:the\s+)?(?:web|internet|google)?\s*(?:for\s+)?(.+)$",
            clean,
            re.IGNORECASE,
        )
        if search_match and not any(w in lower for w in ("and build", "and create", "then create")):
            query = search_match.group(1).strip()
            if not query:
                return "Search the web", extracted, "Search query is empty. What would you like to search for?"
            extracted["query"] = query
            normalized = f"Search the web for {query}"
            return normalized, extracted, None

        # 3. Project Creation Normalization
        create_match = re.search(
            r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?(?:(?P<type>react|python|fastapi|web|node)\s+)?(?:app|project)\s+(?:called|named)\s+['\"]?(?P<name>[^\s'\"]+)['\"]?",
            clean,
            re.IGNORECASE,
        )
        if create_match:
            ptype = (create_match.group("type") or "React").lower()
            pname = create_match.group("name")
            canonical_type = FRAMEWORK_CANONICAL.get(ptype, ptype.title())
            extracted["project_type"] = canonical_type
            extracted["project_name"] = pname
            normalized = f"Create a {canonical_type} project named {pname}"
            # Check if there is trailing workflow action
            if "build" in lower and "test" in lower:
                normalized += ", build it, and test it"
            elif "deploy" in lower:
                normalized += " and deploy it"
            return normalized, extracted, None

        # 4. Project Diagnostics Normalization
        if any(w in lower for w in ("why build fails", "why my build fails", "why does the build fail", "check build error", "diagnose build")):
            normalized = "Inspect project and diagnose build errors"
            return normalized, extracted, None

        # 5. Composite Research + Demo Normalization
        res_dev_match = re.match(
            r"^(?:research|investigate|explore)\s+(.+?)\s+and\s+(?:create|build|make)\s+(?:a\s+)?(?:demo(?:\s+project)?|project)(?:\s+(?:called|named)\s+([a-zA-Z0-9_\-]+))?",
            clean,
            re.IGNORECASE,
        )
        if res_dev_match:
            topic = res_dev_match.group(1).strip()
            demo_name = res_dev_match.group(2) or "demo-app"
            extracted["topic"] = topic
            extracted["project_name"] = demo_name
            normalized = f"Research {topic} and create a demo project named {demo_name}"
            return normalized, extracted, None

        # 6. Cancellation Normalization
        if lower in ("stop", "cancel", "abort", "halt", "stop everything", "cancel this task", "cancel all"):
            return "Cancel active tasks", {"action": "cancel"}, None

        # 7. System Status / Telemetry
        if any(w in lower for w in ("show system status", "system status", "check status", "system health")):
            return "Check system status and telemetry", {"action": "system_status"}, None
        if any(w in lower for w in ("show logs", "view logs", "system logs")):
            return "Show recent system execution logs", {"action": "show_logs"}, None

        # Default fallback: capitalized cleaned string
        normalized = clean[0].upper() + clean[1:] if clean else ""
        return normalized, extracted, None

    @classmethod
    def normalize_text(cls, raw_goal: str) -> str:
        """Helper returning just the normalized goal text."""
        return cls.normalize(raw_goal)[0]

    @classmethod
    def extract_parameters(cls, raw_goal: str) -> Dict[str, Any]:
        """Helper returning just extracted parameters."""
        params = cls.normalize(raw_goal)[1]
        # Also parse path if present
        path_match = re.search(r"(?:at|path|in)\s+([/\\][^\s]+)", raw_goal)
        if path_match:
            params["path"] = path_match.group(1)
        target_match = re.search(r"(?:called|named)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?", raw_goal, re.IGNORECASE)
        if target_match and "target_name" not in params:
            params["target_name"] = target_match.group(1)
        return params


goal_normalizer = GoalNormalizer()

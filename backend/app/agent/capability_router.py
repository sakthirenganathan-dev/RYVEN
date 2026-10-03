"""RYVEN 3.0 — Capability Router.

Maps domain intentions to CapabilityGroups and isolates relevant subsets of
registered tools, preventing context saturation and errant tool hallucinations.
"""

from __future__ import annotations

import re
from typing import Dict, List, Set
from app.agent.models import CapabilityGroup
from app.tools.registry import ToolRegistry


class CapabilityRouter:
    """Classifies user goals into CapabilityGroups and extracts tool subsets."""

    CAPABILITY_TOOL_MAP: Dict[CapabilityGroup, List[str]] = {
        CapabilityGroup.COMPUTER: [
            "open_application",
            "inspect_applications",
            "focus_application",
            "close_application",
        ],
        CapabilityGroup.BROWSER: [
            "open_browser",
            "navigate_browser",
            "get_current_page",
            "read_page",
            "find_element",
            "click_element",
            "type_text",
            "press_key",
            "scroll_page",
            "go_back",
            "go_forward",
            "refresh_page",
            "take_browser_snapshot",
            "close_browser",
            "browser_tabs",
            "browser_snapshot",
            "browser_screenshot",
            "page_ocr",
            "form_field_fill",
            "form_option_select",
            "form_checkbox_toggle",
            "form_radio_select",
            "form_submit",
        ],
        CapabilityGroup.INTERNET: [
            "internet_search",
            "web_research",
            "web_task",
            "web_verify",
            "browser_tabs",
            "browser_snapshot",
            "open_browser",
            "navigate_browser",
            "read_page",
            "find_element",
            "click_element",
            "type_text",
            "scroll_page",
            "browser_screenshot",
            "page_ocr",
            "form_field_fill",
            "form_option_select",
            "form_checkbox_toggle",
            "form_radio_select",
            "form_submit",
            "web_download",
            "web_upload",
            "web_verify_download",
            "web_verify_upload",
        ],
        CapabilityGroup.FILES: [
            "search_files",
            "open_file",
            "open_folder",
            "create_project_folder",
            "create_project_file",
            "validate_project_files",
        ],
        CapabilityGroup.DEVELOPMENT: [
            "apply_project_modification",
            "resolve_existing_project",
            "scan_existing_project",
            "plan_project_modifications",
            "validate_modification_plan",
            "build_project",
            "test_project",
            "quality_gate",
        ],
        CapabilityGroup.GIT: [
            "git_status",
            "git_diff",
            "git_branch",
            "git_remote",
            "git_log",
            "git_stage",
            "git_unstage",
            "git_commit",
            "git_push",
        ],
        CapabilityGroup.DEPLOYMENT: [
            "deployment_detect",
            "deployment_preflight",
            "deployment_preview",
            "deployment_deploy",
            "deployment_status",
            "deployment_verify",
        ],
        CapabilityGroup.HEALTH: [
            "health_check",
            "health_status",
            "health_history",
            "health_monitor_start",
            "health_monitor_stop",
        ],
        CapabilityGroup.KNOWLEDGE: [
            "graph_status",
            "graph_build",
            "graph_update",
            "graph_query",
            "graph_find_symbol",
            "graph_find_dependencies",
            "graph_find_dependents",
            "graph_find_callers",
            "graph_explain",
            "graph_path",
        ],
        CapabilityGroup.SYSTEM: [
            "time",
            "system_status",
            "system_info",
            "get_clipboard",
            "set_clipboard",
            "open_website",
        ],
        CapabilityGroup.WORKFLOW: [
            "orchestrate_task",
            "get_orchestration_status",
            "confirm_orchestration",
            "pause_orchestration",
            "resume_orchestration",
            "cancel_orchestration",
        ],
    }

    # Intent keyword heuristics for capability classification
    CAPABILITY_KEYWORDS: Dict[CapabilityGroup, List[str]] = {
        CapabilityGroup.BROWSER: [
            "browser", "chrome", "google", "youtube", "website", "web page", "search for",
            "navigate", "read page", "click element", "snapshot", "internet", "url", "http", "https",
            "form", "download", "upload", "screenshot", "ocr", "click", "type",
        ],
        CapabilityGroup.COMPUTER: [
            "open", "launch", "start", "run", "application", "app", "vscode", "vs code",
            "notepad", "calculator", "terminal", "explorer"
        ],
        CapabilityGroup.FILES: [
            "file", "folder", "directory", "search files", "open file", "open folder", "downloads", "desktop"
        ],
        CapabilityGroup.DEVELOPMENT: [
            "project", "code", "modify", "refactor", "build", "test", "quality gate", "fix error", "navbar", "dark mode"
        ],
        CapabilityGroup.GIT: [
            "git", "commit", "push", "branch", "remote", "log", "diff", "stage", "unstage"
        ],
        CapabilityGroup.DEPLOYMENT: [
            "deploy", "ship", "vercel", "render", "railway", "publish", "preflight"
        ],
        CapabilityGroup.HEALTH: [
            "health", "ping", "monitor", "uptime", "latency", "is working", "alive", "status code"
        ],
        CapabilityGroup.KNOWLEDGE: [
            "knowledge graph", "symbol", "callers", "dependencies", "dependents", "ast", "explain structure"
        ],
        CapabilityGroup.SYSTEM: [
            "time", "clock", "date", "cpu", "ram", "disk", "memory", "battery", "system status", "clipboard"
        ],
        CapabilityGroup.INTERNET: [
            "search", "internet", "web", "online", "research", "documentation",
            "docs", "summarize", "sources", "duckduckgo", "google", "youtube", "github"
        ],
    }

    @classmethod
    def classify_capabilities(cls, goal: str) -> List[CapabilityGroup]:
        """Classify a user goal into the required CapabilityGroups."""
        goal_lower = goal.lower()
        matched: Set[CapabilityGroup] = set()

        for cap, kws in cls.CAPABILITY_KEYWORDS.items():
            for kw in kws:
                if re.search(rf"\b{re.escape(kw)}\b", goal_lower):
                    matched.add(cap)
                    break

        # Fallback to general system & computer if nothing matched
        if not matched:
            matched.add(CapabilityGroup.SYSTEM)
            matched.add(CapabilityGroup.COMPUTER)

        # Cross-capability dependencies
        if CapabilityGroup.DEPLOYMENT in matched:
            matched.add(CapabilityGroup.GIT)
            matched.add(CapabilityGroup.HEALTH)
        if CapabilityGroup.DEVELOPMENT in matched:
            matched.add(CapabilityGroup.FILES)
            matched.add(CapabilityGroup.KNOWLEDGE)

        return sorted(list(matched), key=lambda c: c.value)

    @classmethod
    def get_tool_names_for_capabilities(cls, capabilities: List[CapabilityGroup]) -> List[str]:
        """Get the combined unique tool names permitted for the given capabilities."""
        tools: Set[str] = set()
        for cap in capabilities:
            tools.update(cls.CAPABILITY_TOOL_MAP.get(cap, []))
        return sorted(list(tools))

    @classmethod
    def filter_registry(cls, registry: ToolRegistry, capabilities: List[CapabilityGroup]) -> Dict[str, Any]:
        """Return a filtered tool schema dictionary containing only tools for the active capabilities."""
        allowed_tools = set(cls.get_tool_names_for_capabilities(capabilities))
        schemas = {}
        for name in allowed_tools:
            tool = registry.get(name)
            if tool:
                schemas[name] = {
                    "description": tool.description,
                    "parameters": getattr(tool, "input_schema", {}),
                }
        return schemas

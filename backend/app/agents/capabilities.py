"""RYVEN 3.0 — Agent Capabilities and ToolRegistry Mappings.

Links high-level AgentCapability definitions to underlying ToolRegistry tools.
Ensures agents operate strictly through registered, validated tools.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set
from app.agents.models import AgentCapability, AgentRole
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Capability -> ToolRegistry Tool Name Mappings
# ---------------------------------------------------------------------------
CAPABILITY_TOOL_MAP: Dict[AgentCapability, List[str]] = {
    AgentCapability.WEB_SEARCH: [
        "internet_search",
    ],
    AgentCapability.WEB_RESEARCH: [
        "web_research",
        "web_task",
    ],
    AgentCapability.PAGE_READING: [
        "read_page",
        "get_current_page",
    ],
    AgentCapability.BROWSER_ACTION: [
        "open_browser",
        "navigate_browser",
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
    ],
    AgentCapability.WINDOWS_APP: [
        "open_application",
        "open_website",
    ],
    AgentCapability.WINDOWS_FILES: [
        "search_files",
        "open_file",
        "open_folder",
        "create_project_folder",
        "create_project_file",
        "validate_project_files",
    ],
    AgentCapability.CLIPBOARD: [
        "get_clipboard",
        "set_clipboard",
    ],
    AgentCapability.SYSTEM_STATUS: [
        "system_status",
        "system_info",
        "time",
    ],
    AgentCapability.PROJECT_SCAN: [
        "scan_existing_project",
        "resolve_existing_project",
    ],
    AgentCapability.CODE_GENERATION: [
        "plan_project_modifications",
    ],
    AgentCapability.CODE_MODIFICATION: [
        "apply_project_modification",
        "validate_modification_plan",
    ],
    AgentCapability.BUILD: [
        "build_project",
    ],
    AgentCapability.TEST: [
        "test_project",
    ],
    AgentCapability.GIT: [
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
    AgentCapability.DEPLOY: [
        "deployment_detect",
        "deployment_preflight",
        "deployment_preview",
        "deployment_deploy",
        "deployment_status",
        "deployment_verify",
    ],
    AgentCapability.KNOWLEDGE_GRAPH: [
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
    AgentCapability.HEALTH_CHECK: [
        "health_check",
        "health_status",
        "health_history",
        "health_monitor_start",
        "health_monitor_stop",
    ],
    AgentCapability.VISION: [
        "vision_capture",
        "vision_analyze",
    ],
    AgentCapability.OCR: [
        "ocr_extract",
    ],
    AgentCapability.VERIFICATION: [
        "quality_gate",
        "web_verify",
    ],
    AgentCapability.MEMORY_READ: [],
    AgentCapability.MEMORY_WRITE: [],
    AgentCapability.TASK_DECOMPOSITION: [],
    AgentCapability.AGENT_ASSIGNMENT: [],
    AgentCapability.TASK_COORDINATION: [],
}


# ---------------------------------------------------------------------------
# Default Role Capabilities Mapping
# ---------------------------------------------------------------------------
ROLE_CAPABILITIES: Dict[AgentRole, Set[AgentCapability]] = {
    AgentRole.COORDINATOR: {
        AgentCapability.TASK_DECOMPOSITION,
        AgentCapability.AGENT_ASSIGNMENT,
        AgentCapability.TASK_COORDINATION,
        AgentCapability.SYSTEM_STATUS,
    },
    AgentRole.RESEARCH: {
        AgentCapability.WEB_SEARCH,
        AgentCapability.WEB_RESEARCH,
        AgentCapability.PAGE_READING,
    },
    AgentRole.DEVELOPER: {
        AgentCapability.PROJECT_SCAN,
        AgentCapability.CODE_GENERATION,
        AgentCapability.CODE_MODIFICATION,
        AgentCapability.BUILD,
        AgentCapability.TEST,
        AgentCapability.GIT,
        AgentCapability.DEPLOY,
        AgentCapability.KNOWLEDGE_GRAPH,
        AgentCapability.VERIFICATION,
    },
    AgentRole.COMPUTER: {
        AgentCapability.WINDOWS_APP,
        AgentCapability.WINDOWS_FILES,
        AgentCapability.CLIPBOARD,
        AgentCapability.SYSTEM_STATUS,
        AgentCapability.BROWSER_ACTION,
    },
    AgentRole.BROWSER: {
        AgentCapability.BROWSER_ACTION,
        AgentCapability.PAGE_READING,
        AgentCapability.WEB_SEARCH,
    },
    AgentRole.WINDOWS: {
        AgentCapability.WINDOWS_APP,
        AgentCapability.WINDOWS_FILES,
        AgentCapability.CLIPBOARD,
        AgentCapability.SYSTEM_STATUS,
    },
    AgentRole.MEMORY: {
        AgentCapability.MEMORY_READ,
        AgentCapability.MEMORY_WRITE,
    },
    AgentRole.VERIFICATION: {
        AgentCapability.VERIFICATION,
        AgentCapability.HEALTH_CHECK,
        AgentCapability.VISION,
        AgentCapability.OCR,
    },
}


def get_tools_for_capability(capability: AgentCapability) -> List[str]:
    """Return tool names associated with an agent capability."""
    return list(CAPABILITY_TOOL_MAP.get(capability, []))


def get_capabilities_for_role(role: AgentRole) -> Set[AgentCapability]:
    """Return the set of capabilities registered for a given role."""
    return set(ROLE_CAPABILITIES.get(role, set()))


def find_roles_for_capability(capability: AgentCapability) -> List[AgentRole]:
    """Find all agent roles that possess a given capability."""
    return [role for role, caps in ROLE_CAPABILITIES.items() if capability in caps]


def validate_capability_tools(registry: ToolRegistry) -> Dict[str, Any]:
    """Validate which mapped capability tools are present in the ToolRegistry."""
    registered = set(registry.list_tools())
    valid_map: Dict[str, List[str]] = {}
    missing_map: Dict[str, List[str]] = {}

    for cap, tools in CAPABILITY_TOOL_MAP.items():
        present = [t for t in tools if t in registered]
        missing = [t for t in tools if t not in registered]
        valid_map[cap.value] = present
        if missing:
            missing_map[cap.value] = missing

    return {
        "valid": valid_map,
        "missing": missing_map,
        "total_mapped_capabilities": len(CAPABILITY_TOOL_MAP),
    }

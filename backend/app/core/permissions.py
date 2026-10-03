"""Permission and validation safety layer for RYVEN tools."""

import os
import re
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from app.core.logging_config import logger

RiskLevel = Literal["safe", "confirm", "blocked"]


class PermissionResult(BaseModel):
    """Result of a permission and safety evaluation."""

    allowed: bool
    risk_level: RiskLevel
    reason: str
    tool_name: Optional[str] = None


class SafetyGuard:
    """Security validator inspecting queries and actions for malicious patterns."""

    # Patterns representing dangerous, malicious, or blocked actions
    DANGEROUS_PATTERNS = [
        # Arbitrary shell execution
        r"\b(?:cmd(?:\.exe)?|powershell(?:\.exe)?|bash|sh|zsh|wscript|cscript)\b",
        r"\b(?:powershell(?:\.exe)?\s+-(?:command|c|encodedcommand))\b",
        r"\b(?:use\s+shell|run\s+shell|execute\s+shell|shell\s+command|run\s+cmd)\b",
        r"\b(?:run\s+python|execute\s+python|python(?:\.exe)?\s+-[ce]|python\s+[^\s]+\.py)\b",
        # Dangerous discovery and process tools
        r"\b(?:whoami|ipconfig|netstat|taskkill|vssadmin|bcdedit)\b",
        # Destruction commands
        r"\b(?:format(?:\s+[a-z]:)?|delete\s+[a-z]:\\|rmdir\s+|del\s+|remove-item)\b",
        # Power state commands
        r"\b(?:shutdown(?:\.exe)?|restart(?:\.exe|-computer)?|stop-computer)\b",
        # System modifications (registry, firewall, services)
        r"\b(?:registry\s+modification|reg(?:\.exe)?\s+(?:add|delete|import|export))\b",
        r"\b(?:firewall\s+modification|netsh(?:\.exe)?\s+advfirewall)\b",
        r"\b(?:service\s+modification|sc(?:\.exe)?\s+(?:create|delete|config|stop|start))\b",
        # Destructive Git and force push patterns
        r"\b(?:git\s+reset\s+--hard|git\s+clean\s+-[a-z]*[fdx]|git\s+branch\s+-[a-z]*D|git\s+checkout\s+--\s+\.)\b",
        r"\b(?:git\s+push\s+.*(?:--force|-f\b|--force-with-lease|--force-if-includes))\b",
        r"\b(?:force\s+push|push\s+(?:with\s+)?--force|--force|-f\b)\b",
        r"\b(?:delete\s+(?:the\s+)?branch|branch\s+deletion|clean\s+(?:all\s+)?untracked\s+files|git\s+clean)\b",
        r"\b(?:execute\s+arbitrary\s+(?:git\s+)?command|arbitrary\s+(?:git\s+)?command)\b",
        # Unsafe protocol schemes
        r"(?:file://|javascript:|data:|vbscript:)",
        # Path traversal
        r"(?:\.\./|\.\.\\)",
        # Code execution injections
        r"\b(?:eval\(|exec\(|subprocess|os\.system|__import__)\b",
        # Prompt injection bypassing restrictions
        r"\b(?:ignore\s+(?:all\s+)?(?:previous\s+)?(?:instructions|restrictions|rules|security\s+rules)|bypass\s+(?:safety|rules|restrictions)|unrestricted\s+now)\b",
    ]

    def __init__(self) -> None:
        self._compiled_dangerous = [re.compile(p, re.IGNORECASE) for p in self.DANGEROUS_PATTERNS]

    def is_blocked_instruction(self, text: str) -> Optional[str]:
        """Check if an instruction matches any blocked security patterns."""
        for pattern in self._compiled_dangerous:
            match = pattern.search(text)
            if match:
                return f"Blocked dangerous or prohibited command pattern: '{match.group(0)}'"
        return None

    def validate_action(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        raw_query: str = "",
    ) -> PermissionResult:
        """Validate whether a tool execution passes the safety and permission checks."""
        # 1. Inspect raw input for blocked patterns
        blocked_reason = self.is_blocked_instruction(raw_query)
        if blocked_reason:
            logger.warning(f"Security Guard BLOCKED action on query: {blocked_reason}")
            return PermissionResult(
                allowed=False,
                risk_level="blocked",
                reason=blocked_reason,
                tool_name=tool_name,
            )

        # 2. Inspect argument values for path traversal or malicious injection
        for arg_key, arg_val in arguments.items():
            if isinstance(arg_val, str):
                blocked_arg = self.is_blocked_instruction(arg_val)
                if blocked_arg:
                    logger.warning(f"Security Guard BLOCKED argument '{arg_key}': {blocked_arg}")
                    return PermissionResult(
                        allowed=False,
                        risk_level="blocked",
                        reason=f"Blocked argument in '{arg_key}': {blocked_arg}",
                        tool_name=tool_name,
                    )

        # 3. Check for permanently blocked tool categories
        blocked_tools = {
            "shell",
            "command_execution",
            "format_drive",
            "delete_files",
            "powershell",
            "cmd",
            "shutdown",
            "registry",
            "firewall",
        }
        if tool_name.lower() in blocked_tools:
            return PermissionResult(
                allowed=False,
                risk_level="blocked",
                reason=f"Tool '{tool_name}' is permanently blocked by security policy.",
                tool_name=tool_name,
            )

        # 4. Known Phase 3 & Phase 4.1 authorized safe tools
        safe_tools = {
            "time",
            "system_status",
            "system_info",
            "open_application",
            "open_website",
            "open_folder",
            "search_files",
            "open_file",
            "get_clipboard",
            "set_clipboard",
            "create_project_folder",
            "create_project_file",
            "validate_project_files",
            "apply_project_modification",   # M8: controlled file modification
            "resolve_existing_project",     # M8.5: project resolution
            "scan_existing_project",        # M8.5: project scanning
            "plan_project_modifications",   # M8.5: modification planner
            "validate_modification_plan",   # M8.5: pre-write validation
            "build_project",                # M8.5: sandbox build
            "test_project",                 # M8.5: sandbox test
            "quality_gate",                 # M8.5: quality gate check
            # M11 Git tools
            "git_status",
            "git_diff",
            "git_branch",
            "git_remote",
            "git_log",
            "git_stage",
            "git_unstage",
            "git_commit",
            "git_push",
            # M12 Deployment tools
            "deployment_detect",
            "deployment_preflight",
            "deployment_preview",
            "deployment_deploy",
            "deployment_status",
            "deployment_verify",
            # M11.5 Knowledge Graph tools
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
            # M13 Health & Verification tools
            "health_check",
            "health_status",
            "health_history",
            "health_monitor_start",
            "health_monitor_stop",
            # M14 Autonomous Development Orchestrator tools
            "orchestrate_task",
            "get_orchestration_status",
            "confirm_orchestration",
            "pause_orchestration",
            "resume_orchestration",
            "cancel_orchestration",
            # M14.3 Controlled Computer & Browser Control tools
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
            # RYVEN 3.0 Unified Internet Agent tools
            "internet_search",
            "web_research",
            "web_task",
            "web_verify",
            "browser_tabs",
            "browser_snapshot",
            "form_field_fill",
            "form_option_select",
            "form_checkbox_toggle",
            "form_radio_select",
            "form_submit",
            "web_download",
            "web_upload",
            "web_verify_download",
            "web_verify_upload",
        }




        if tool_name.lower() in safe_tools:
            return PermissionResult(
                allowed=True,
                risk_level="safe",
                reason="Action permitted under controlled laptop intelligence policy.",
                tool_name=tool_name,
            )

        # Default fallback: elevated confirmation required for unapproved tools
        return PermissionResult(
            allowed=False,
            risk_level="confirm",
            reason=f"Tool '{tool_name}' requires explicit elevated confirmation.",
            tool_name=tool_name,
        )


# Global safety guard singleton
safety_guard = SafetyGuard()

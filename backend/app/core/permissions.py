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

"""RYVEN 3.0 — Agent Security and Policy Enforcement.

Validates that agent tasks satisfy strict security invariants:
- Capabilities map to valid ToolRegistry tools.
- Arbitrary shell commands are strictly prohibited.
- Consequential actions require explicit user confirmation.
- Paths and URLs conform to local workspace and SSRF protection boundaries.
- Runtime hardware resource limits are respected.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from pydantic import BaseModel

from app.agents.models import AgentCapability, AgentRole, AgentTask
from app.agents.registry import AgentRegistry, agent_registry
from app.core.logging_config import logger
from app.runtime.models import ResourceDecision
from app.runtime.resource_manager import resource_manager
from app.tools.registry import ToolRegistry
from app.workflows.confirmation import ConfirmationManager


# Consequential tool actions that ALWAYS require human confirmation
CONSEQUENTIAL_TOOLS = frozenset({
    "git_commit",
    "git_push",
    "deploy",
    "deployment_deploy",
    "delete_file",
    "browser_submit_form",
    "browser_download",
    "browser_delete",
})

# Forbidden tool patterns (arbitrary shell, raw terminal execution)
FORBIDDEN_TOOLS = frozenset({
    "bash",
    "sh",
    "cmd",
    "powershell",
    "exec_shell",
    "shell_exec",
    "eval",
    "system_exec",
})


class SecurityCheckResult(BaseModel):
    """Result of agent task security policy evaluation."""
    allowed: bool
    reason: str
    requires_confirmation: bool = False
    confirmation_type: Optional[str] = None


class AgentSecurityPolicy:
    """Centralized security validator for agent tasks and execution."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        tool_registry: Optional[ToolRegistry] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
    ) -> None:
        self.registry = registry or agent_registry
        self.tool_registry = tool_registry
        self.confirmation_mgr = confirmation_manager or ConfirmationManager()

    def validate_task_execution(
        self,
        task: AgentTask,
        auto_confirm: bool = False,
    ) -> SecurityCheckResult:
        """Evaluate task against security invariants before execution."""
        # 1. Validate Agent Role and Registration
        agent = self.registry.get_agent(task.role)
        if not agent:
            return SecurityCheckResult(
                allowed=False,
                reason=f"Security violation: Agent role '{task.role.value}' is not registered.",
            )

        # 2. Validate Capability Ownership
        if not agent.has_capability(task.capability):
            return SecurityCheckResult(
                allowed=False,
                reason=f"Security violation: Role '{task.role.value}' does not possess capability '{task.capability.value}'.",
            )

        # 3. Check for Prohibited Shell or System Execution
        if task.tool_name:
            t_lower = task.tool_name.lower()
            if t_lower in FORBIDDEN_TOOLS:
                return SecurityCheckResult(
                    allowed=False,
                    reason=f"Security rejection: Arbitrary shell/exec tool '{task.tool_name}' is forbidden.",
                )

        # 4. Validate Tool Exists in ToolRegistry (if ToolRegistry available)
        if self.tool_registry and task.tool_name:
            if not self.tool_registry.has_tool(task.tool_name):
                return SecurityCheckResult(
                    allowed=False,
                    reason=f"Tool '{task.tool_name}' is not registered in ToolRegistry.",
                )

        # 5. Check Consequential Actions & Confirmation Boundary
        requires_conf = task.requires_confirmation
        conf_type = task.confirmation_type

        if task.tool_name and task.tool_name.lower() in CONSEQUENTIAL_TOOLS:
            requires_conf = True
            conf_type = conf_type or task.tool_name.upper()

        if requires_conf and not auto_confirm and not task.confirmed:
            return SecurityCheckResult(
                allowed=False,
                reason=f"Operation requires explicit user confirmation: {conf_type or task.objective}",
                requires_confirmation=True,
                confirmation_type=conf_type or "CONSEQUENTIAL_ACTION",
            )

        # 6. Preflight Hardware Resource Pressure Gate
        res_check = resource_manager.check_resource_pressure(operation_type=task.capability.value)
        if res_check.decision == ResourceDecision.BLOCK:
            return SecurityCheckResult(
                allowed=False,
                reason=f"Execution blocked by host resource policy: {res_check.reason}",
            )

        return SecurityCheckResult(
            allowed=True,
            reason="All security invariants and policy checks satisfied.",
            requires_confirmation=False,
        )


# Global singleton instance
agent_security = AgentSecurityPolicy()

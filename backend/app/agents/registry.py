"""RYVEN 3.0 — Agent Registry.

Centralized registry for specialized agent roles operating under the RYVEN Control Plane.
Tracks role registrations, capability declarations, and operational status.
Prevents duplicate registrations and enforces single-control-plane architecture.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from app.agents.capabilities import (
    ROLE_CAPABILITIES,
    find_roles_for_capability,
    get_capabilities_for_role,
    get_tools_for_capability,
)
from app.agents.models import AgentCapability, AgentRole, AgentStatus
from app.core.logging_config import logger


class AgentDescriptor(BaseModel):
    """Metadata descriptor for a registered agent worker role."""
    agent_id: str
    role: AgentRole
    name: str
    description: str
    capabilities: Set[AgentCapability] = Field(default_factory=set)
    status: AgentStatus = AgentStatus.READY
    active_tasks: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def has_capability(self, capability: AgentCapability) -> bool:
        return capability in self.capabilities


class AgentRegistry:
    """Registry maintaining role definitions and state for the RYVEN Control Plane."""

    def __init__(self) -> None:
        self._agents: Dict[AgentRole, AgentDescriptor] = {}
        self._initialize_default_agents()

    def _initialize_default_agents(self) -> None:
        """Register the standard set of RYVEN specialized role workers."""
        defaults = [
            (
                AgentRole.COORDINATOR,
                "Coordinator Agent",
                "Central planning, decomposition, task assignment, and execution coordination.",
            ),
            (
                AgentRole.RESEARCH,
                "Research Agent",
                "Internet search, documentation gathering, page reading, and source synthesis.",
            ),
            (
                AgentRole.DEVELOPER,
                "Developer Agent",
                "Project scanning, code modification, build execution, testing, git, and deployment.",
            ),
            (
                AgentRole.COMPUTER,
                "Computer Agent",
                "Controlled desktop application launching, file navigation, clipboard, and browser control.",
            ),
            (
                AgentRole.BROWSER,
                "Browser Agent",
                "Web page inspection, DOM navigation, tab management, and visual snapshots.",
            ),
            (
                AgentRole.WINDOWS,
                "Windows Agent",
                "Local file search, application launching, clipboard management, and system stats.",
            ),
            (
                AgentRole.MEMORY,
                "Memory Agent",
                "Read/write interface for structured context retrieval and memory candidates.",
            ),
            (
                AgentRole.VERIFICATION,
                "Verification Agent",
                "Quality gate inspection, health checks, vision verification, and OCR extraction.",
            ),
        ]

        for role, name, desc in defaults:
            caps = get_capabilities_for_role(role)
            self._agents[role] = AgentDescriptor(
                agent_id=f"agent-{role.value.lower()}",
                role=role,
                name=name,
                description=desc,
                capabilities=caps,
                status=AgentStatus.READY,
            )

    def register_agent(
        self,
        role: AgentRole,
        name: str,
        description: str,
        capabilities: Optional[Set[AgentCapability]] = None,
        override: bool = False,
    ) -> AgentDescriptor:
        """Register a new or custom agent role in the registry."""
        if role in self._agents and not override:
            raise ValueError(f"Agent role '{role.value}' is already registered in AgentRegistry.")

        caps = capabilities if capabilities is not None else get_capabilities_for_role(role)
        desc = AgentDescriptor(
            agent_id=f"agent-{role.value.lower()}",
            role=role,
            name=name,
            description=description,
            capabilities=caps,
            status=AgentStatus.READY,
        )
        self._agents[role] = desc
        logger.info(f"[AGENT_REGISTRY] Registered agent: {name} (role={role.value}, caps={len(caps)})")
        return desc

    def get_agent(self, role: AgentRole) -> Optional[AgentDescriptor]:
        """Retrieve an agent descriptor by role."""
        return self._agents.get(role)

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentDescriptor]:
        """Retrieve an agent descriptor by its unique agent_id."""
        for desc in self._agents.values():
            if desc.agent_id == agent_id:
                return desc
        return None

    def list_agents(self) -> List[AgentDescriptor]:
        """Return a list of all registered agents."""
        return list(self._agents.values())

    def update_agent_status(self, role: AgentRole, status: AgentStatus) -> None:
        """Update an agent's current lifecycle status."""
        agent = self._agents.get(role)
        if agent:
            agent.status = status

    def assign_task_to_agent(self, role: AgentRole, task_id: str) -> bool:
        """Track an active task assigned to an agent."""
        agent = self._agents.get(role)
        if not agent:
            return False
        if task_id not in agent.active_tasks:
            agent.active_tasks.append(task_id)
            agent.status = AgentStatus.RUNNING
        return True

    def complete_task_for_agent(self, role: AgentRole, task_id: str) -> None:
        """Untrack a completed task from an agent."""
        agent = self._agents.get(role)
        if agent and task_id in agent.active_tasks:
            agent.active_tasks.remove(task_id)
            if not agent.active_tasks:
                agent.status = AgentStatus.READY

    def find_best_agent_for_capability(self, capability: AgentCapability) -> Optional[AgentDescriptor]:
        """Find the most specific agent registered for a given capability."""
        candidate_roles = find_roles_for_capability(capability)
        if not candidate_roles:
            return None
        # Prioritize non-coordinator specific workers first
        for role in candidate_roles:
            if role != AgentRole.COORDINATOR and role in self._agents:
                return self._agents[role]
        # Fall back to coordinator if it has the capability
        if AgentRole.COORDINATOR in candidate_roles and AgentRole.COORDINATOR in self._agents:
            return self._agents[AgentRole.COORDINATOR]
        return None


# Global singleton instance
agent_registry = AgentRegistry()

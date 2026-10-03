"""RYVEN 3.0 — Central Agent Engine Singleton.

Binds the AgentPlanner, CapabilityRouter, and AgentOrchestrator together into a
unified, high-level personal AI control plane.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from app.agent.capability_router import CapabilityRouter
from app.agent.models import AgentExecutionResult, AgentPlan, AgentState, CapabilityGroup, TaskStep
from app.agent.orchestrator import AgentOrchestrator
from app.agent.planner import AgentPlanner
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.tools.registry import ToolRegistry, create_default_registry


class AgentEngine:
    """Singleton facade for RYVEN 3.0 autonomous agent control plane."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guard: Optional[SafetyGuard] = None,
        workflow_engine: Optional[Any] = None,
    ) -> None:
        self.registry = registry or create_default_registry()
        self.planner = AgentPlanner()
        self.router = CapabilityRouter()
        self.orchestrator = AgentOrchestrator(registry=self.registry, guard=guard)
        if workflow_engine is None:
            from app.workflows.engine import WorkflowEngine
            workflow_engine = WorkflowEngine(registry=self.registry, guard=guard)
        self.workflow_engine = workflow_engine

    def _capability_for_tool(self, goal: str, tool_name: str) -> CapabilityGroup:
        mapped = [
            capability
            for capability, tool_names in self.router.CAPABILITY_TOOL_MAP.items()
            if tool_name in tool_names
        ]
        if not mapped:
            raise ValueError(f"Tool '{tool_name}' is not assigned to a capability domain.")
        classified = self.router.classify_capabilities(goal)
        return next((capability for capability in classified if capability in mapped), mapped[0])

    def plan_goal(self, goal: str) -> AgentPlan:
        """Decompose a natural language goal into an executable AgentPlan."""
        return self.planner.plan(goal)

    async def execute_goal(
        self,
        goal: str,
        session_id: str = "default",
        auto_confirm: bool = False,
    ) -> AgentExecutionResult:
        """Decompose, validate, and execute an autonomous multi-step agent task."""
        logger.info(f"[AGENT_ENGINE] Received agent goal: '{goal}'")
        plan = self.plan_goal(goal)
        logger.info(
            f"[AGENT_ENGINE] Constructed plan '{plan.plan_id}' with {plan.total_steps} step(s): "
            f"{[s.name for s in plan.steps]}"
        )
        return await self.orchestrator.execute_plan(
            plan=plan,
            session_id=session_id,
            auto_confirm=auto_confirm,
        )

    async def execute_tool_request(
        self,
        goal: str,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str = "default",
    ) -> AgentExecutionResult:
        """Execute a deterministic single-tool intent through the shared agent lifecycle."""
        tool = self.registry.get(tool_name)
        if not tool:
            raise KeyError(f"Tool '{tool_name}' is not registered in the active registry.")

        capability = self._capability_for_tool(goal, tool.name)
        requires_confirmation = (
            tool.requires_confirmation
            or self.orchestrator.confirmation_mgr.requires_confirmation(tool.name, arguments)
        )
        step = TaskStep(
            step_id=f"step-{uuid.uuid4().hex[:8]}",
            order=1,
            name=tool.name,
            capability=capability,
            tool_name=tool.name,
            arguments=arguments,
            description=goal,
            requires_confirmation=requires_confirmation,
        )
        plan = AgentPlan(
            goal=goal,
            capabilities_required=[capability],
            steps=[step],
        )
        return await self.orchestrator.execute_plan(
            plan=plan,
            session_id=session_id,
            auto_confirm=False,
        )

    async def execute_workflow_goal(
        self,
        goal: str,
        session_id: str = "default",
        auto_confirm: bool = False,
    ) -> AgentExecutionResult:
        """Adapt an existing validated workflow plan to the unified agent executor."""
        workflow = self.workflow_engine.plan_workflow(goal)
        if not workflow:
            raise ValueError("No supported workflow plan could be created for this request.")

        is_valid, reason = self.workflow_engine.validator.validate(workflow)
        if not is_valid:
            raise ValueError(f"Workflow plan was rejected: {reason}")

        steps: List[TaskStep] = []
        required_capabilities = set()
        previous_step_id: Optional[str] = None
        for order, workflow_step in enumerate(workflow.steps, start=1):
            capability = self._capability_for_tool(goal, workflow_step.tool_name)
            required_capabilities.add(capability)
            requires_confirmation = (
                workflow_step.requires_confirmation
                or self.orchestrator.confirmation_mgr.requires_confirmation(workflow_step)
            )
            steps.append(
                TaskStep(
                    step_id=workflow_step.step_id,
                    order=order,
                    name=workflow_step.name,
                    capability=capability,
                    tool_name=workflow_step.tool_name,
                    arguments=dict(workflow_step.arguments),
                    description=workflow_step.name,
                    dependencies=[previous_step_id] if previous_step_id else [],
                    requires_confirmation=requires_confirmation,
                )
            )
            previous_step_id = workflow_step.step_id

        plan = AgentPlan(
            plan_id=workflow.workflow_id,
            goal=goal,
            capabilities_required=sorted(required_capabilities, key=lambda item: item.value),
            steps=steps,
        )
        result = await self.orchestrator.execute_plan(
            plan=plan,
            session_id=session_id,
            auto_confirm=auto_confirm,
        )
        result.final_output.update(
            {
                "workflow_id": workflow.workflow_id,
                "workflow_name": workflow.name,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "name": step.name,
                        "tool_name": step.tool_name,
                        "status": step.status.value,
                    }
                    for step in steps
                ],
            }
        )
        return result

    def get_task_state(self, task_id: str) -> Optional[AgentState]:
        """Query state for a specific agent task."""
        return self.orchestrator.get_task(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        """List active and historical agent tasks."""
        return self.orchestrator.list_active_tasks()

    async def confirm_action(self, token: str) -> Dict[str, Any]:
        """Submit approval for a confirmation-gated step."""
        return await self.orchestrator.confirm_action(token)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        return self.orchestrator.cancel_task(task_id)


# Global singleton instance
agent_engine = AgentEngine()

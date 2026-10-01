"""WorkflowEngine coordinating planning, validation, confirmation, and execution."""

from typing import Dict, List, Optional
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.tools.registry import ToolRegistry
from app.workflows.confirmation import ConfirmationManager
from app.workflows.executor import WorkflowExecutor
from app.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
)
from app.workflows.planner import WorkflowPlanner
from app.workflows.validator import WorkflowValidator


class WorkflowEngine:
    """Central orchestration engine executing multi-step safe workflows."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guard: Optional[SafetyGuard] = None,
        planner: Optional[WorkflowPlanner] = None,
        validator: Optional[WorkflowValidator] = None,
        executor: Optional[WorkflowExecutor] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry
        self.safety_guard = guard or safety_guard
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()
        self.planner = planner or WorkflowPlanner(registry=self.registry)
        self.validator = validator or WorkflowValidator(
            registry=self.registry, guard=self.safety_guard
        )
        self.executor = executor or WorkflowExecutor(
            registry=self.registry,
            guard=self.safety_guard,
            confirmation_mgr=self.confirmation_mgr,
        )

        self._active_workflows: Dict[str, WorkflowDefinition] = {}

    def plan_workflow(self, query: str) -> Optional[WorkflowDefinition]:
        """Convert a user request into a workflow plan."""
        workflow = self.planner.plan(query)
        if workflow:
            self._active_workflows[workflow.workflow_id] = workflow
        return workflow

    async def execute_workflow(
        self, workflow: WorkflowDefinition, auto_confirm: bool = True
    ) -> WorkflowExecutionResult:
        """Validate and sequentially execute a workflow."""
        self._active_workflows[workflow.workflow_id] = workflow

        # 1. Pre-execution validation check
        is_valid, error_reason = self.validator.validate(workflow)
        if not is_valid:
            logger.warning(f"Workflow '{workflow.name}' failed pre-execution validation: {error_reason}")
            return WorkflowExecutionResult(
                workflow_id=workflow.workflow_id,
                name=workflow.name,
                status=WorkflowState.BLOCKED,
                success=False,
                message=f"Workflow execution blocked: {error_reason}",
                steps_total=workflow.total_steps,
                steps_completed=0,
                steps_failed=0,
                error=error_reason,
            )

        # 2. Sequential execution
        result = await self.executor.execute(workflow, auto_confirm=auto_confirm)
        return result

    async def run_from_query(self, query: str) -> Optional[WorkflowExecutionResult]:
        """High-level entry point: Plan, validate, and execute in one coordinated run."""
        workflow = self.plan_workflow(query)
        if not workflow:
            return None

        return await self.execute_workflow(workflow)

    def cancel_workflow(self, workflow_id: str) -> bool:
        """Signal a workflow to cancel execution safely."""
        workflow = self._active_workflows.get(workflow_id)
        if not workflow:
            return False

        if workflow.status in (WorkflowState.RUNNING, WorkflowState.WAITING_FOR_CONFIRMATION):
            workflow.status = WorkflowState.CANCELLED
            logger.info(f"Workflow '{workflow.name}' ({workflow_id}) flagged as CANCELLED.")
            return True
        return False

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowDefinition]:
        """Retrieve workflow state by ID."""
        return self._active_workflows.get(workflow_id)

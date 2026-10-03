"""Sequential, observable executor for validated RYVEN workflows."""

from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.tools.registry import ToolRegistry
from app.workflows.confirmation import ConfirmationManager
from app.workflows.models import (
    StepState,
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    utc_now_iso,
)


class WorkflowExecutor:
    """Executes validated workflow steps sequentially via the ToolRegistry and SafetyGuard."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guard: Optional[SafetyGuard] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry
        self.safety_guard = guard or safety_guard
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()

    async def execute(
        self,
        workflow: WorkflowDefinition,
        auto_confirm: bool = False,
        confirmed_step_ids: Optional[set[str]] = None,
    ) -> WorkflowExecutionResult:
        """Run workflow steps in sequential order, enforcing safety and capturing step results."""
        workflow.started_at = utc_now_iso()
        if workflow.status != WorkflowState.CANCELLED:
            workflow.status = WorkflowState.RUNNING
        logger.info(f"Starting workflow execution: '{workflow.name}' (ID: {workflow.workflow_id})")

        completed_details: List[Dict[str, Any]] = [
            {
                "step_id": step.step_id,
                "name": step.name,
                "tool": step.tool_name,
                "status": step.status.value,
                "message": (step.result or {}).get("message", "Step previously completed."),
            }
            for step in workflow.steps
            if step.status == StepState.SUCCESS
        ]
        context_state: Dict[str, Any] = {}
        if workflow.metadata:
            if "project_name" in workflow.metadata:
                context_state["project_name"] = workflow.metadata["project_name"]
            if "project_name_hint" in workflow.metadata and workflow.metadata["project_name_hint"]:
                context_state["project_name"] = workflow.metadata["project_name_hint"]

        approved_steps = set(confirmed_step_ids or ())
        for idx, step in enumerate(workflow.steps):
            if step.status == StepState.SUCCESS:
                continue
            workflow.current_step_index = idx

            # Check for cancellation before executing step
            if workflow.status == WorkflowState.CANCELLED:
                step.status = StepState.CANCELLED
                logger.info(f"Workflow '{workflow.name}' cancelled prior to step {idx + 1} ({step.name}).")
                break

            # Check if confirmation is required
            if (
                self.confirmation_mgr.requires_confirmation(step)
                and not auto_confirm
                and step.step_id not in approved_steps
            ):
                workflow.status = WorkflowState.WAITING_FOR_CONFIRMATION
                logger.info(f"Workflow paused at step {idx + 1} ({step.name}) awaiting user confirmation.")
                return WorkflowExecutionResult(
                    workflow_id=workflow.workflow_id,
                    name=workflow.name,
                    status=workflow.status,
                    success=False,
                    message=f"Workflow paused: step '{step.name}' requires user confirmation.",
                    steps_total=workflow.total_steps,
                    steps_completed=workflow.steps_completed,
                    steps_failed=workflow.steps_failed,
                    step_details=completed_details,
                    started_at=workflow.started_at,
                    metadata={
                        "pending_step_id": step.step_id,
                        "pending_tool": step.tool_name,
                        "confirmation_required": True,
                    },
                )

            # Mark step running
            step.started_at = utc_now_iso()
            step.status = StepState.RUNNING
            logger.info(f"Executing workflow step {idx + 1}/{len(workflow.steps)}: '{step.name}' [{step.tool_name}]")

            # Execute tool safely via registry
            tool = self.registry.get(step.tool_name)
            if not tool:
                step.status = StepState.FAILED
                step.error = f"Registered tool '{step.tool_name}' missing at execution time."
                step.completed_at = utc_now_iso()
                workflow.status = WorkflowState.FAILED
                workflow.error = step.error
                logger.error(f"Workflow execution failure at step {idx + 1}: {step.error}")
                break

            # Dynamic argument resolution from previous steps' context
            exec_args = dict(step.arguments)
            if context_state.get("project_name"):
                if not exec_args.get("project_name"):
                    exec_args["project_name"] = context_state["project_name"]
                if "project_name_hint" in exec_args and not exec_args["project_name_hint"]:
                    exec_args["project_name_hint"] = context_state["project_name"]
            if context_state.get("patches") and "patches" in exec_args and not exec_args["patches"]:
                exec_args["patches"] = context_state["patches"]
            if context_state.get("expected_files") and "expected_files" in exec_args and not exec_args["expected_files"]:
                exec_args["expected_files"] = context_state["expected_files"]
            if "build_success" in context_state and "build_success" in exec_args and exec_args["build_success"] is None:
                exec_args["build_success"] = context_state["build_success"]
            if "test_success" in context_state and "test_success" in exec_args and exec_args["test_success"] is None:
                exec_args["test_success"] = context_state["test_success"]

            try:
                tool_output = await tool.execute(**exec_args)
                step.result = tool_output
                step.completed_at = utc_now_iso()

                is_success = tool_output.get("success", True)
                if is_success:
                    step.status = StepState.SUCCESS
                    # Propagate context
                    if tool_output.get("project_name"):
                        context_state["project_name"] = tool_output["project_name"]
                    if tool_output.get("patches"):
                        context_state["patches"] = tool_output["patches"]
                        context_state["expected_files"] = [
                            p.get("relative_path") for p in tool_output["patches"] if isinstance(p, dict)
                        ]
                    if "exit_code" in tool_output:
                        context_state["build_success"] = tool_output.get("success", False)
                    if "tests_total" in tool_output:
                        context_state["test_success"] = tool_output.get("success", False)

                    completed_details.append({
                        "step_id": step.step_id,
                        "name": step.name,
                        "tool": step.tool_name,
                        "status": step.status.value,
                        "message": tool_output.get("message", "Step executed successfully."),
                    })
                    logger.info(f"Step {idx + 1} succeeded: {step.name}")
                else:
                    step.status = StepState.FAILED
                    err_msg = tool_output.get("message", f"Tool '{step.tool_name}' returned failure.")
                    step.error = err_msg
                    workflow.status = WorkflowState.FAILED
                    workflow.error = err_msg
                    completed_details.append({
                        "step_id": step.step_id,
                        "name": step.name,
                        "tool": step.tool_name,
                        "status": step.status.value,
                        "error": err_msg,
                    })
                    logger.warning(f"Workflow stopped: Step {idx + 1} failed with error: {err_msg}")
                    # Stop workflow on first failure
                    break

            except Exception as exc:
                step.status = StepState.FAILED
                step.error = f"Step execution encountered an error: {str(exc)}"
                step.completed_at = utc_now_iso()
                workflow.status = WorkflowState.FAILED
                workflow.error = step.error
                completed_details.append({
                    "step_id": step.step_id,
                    "name": step.name,
                    "tool": step.tool_name,
                    "status": step.status.value,
                    "error": "EXECUTION_ERROR",
                })
                logger.error(f"Workflow execution exception at step {idx + 1}: {exc}", exc_info=True)
                break

        # Finalize workflow state
        workflow.completed_at = utc_now_iso()
        if workflow.status == WorkflowState.RUNNING:
            workflow.status = WorkflowState.COMPLETED
            workflow.final_result = f"Workflow '{workflow.name}' completed successfully ({workflow.steps_completed}/{workflow.total_steps} steps)."
            logger.info(f"Workflow '{workflow.name}' reached COMPLETED state.")
        elif workflow.status == WorkflowState.FAILED:
            workflow.final_result = f"Workflow '{workflow.name}' stopped due to failure: {workflow.error}"
        elif workflow.status == WorkflowState.CANCELLED:
            workflow.final_result = f"Workflow '{workflow.name}' was cancelled."

        # Aggregate metadata from steps (e.g. project files_created)
        wf_meta: Dict[str, Any] = {}
        for d in completed_details:
            if "files_created" in d:
                wf_meta["files_created"] = d["files_created"]
            if "project_name" in d:
                wf_meta["project_name"] = d["project_name"]

        return WorkflowExecutionResult(
            workflow_id=workflow.workflow_id,
            name=workflow.name,
            status=workflow.status,
            success=(workflow.status == WorkflowState.COMPLETED),
            message=workflow.final_result or workflow.error or "Workflow ended.",
            steps_total=workflow.total_steps,
            steps_completed=workflow.steps_completed,
            steps_failed=workflow.steps_failed,
            step_details=completed_details,
            started_at=workflow.started_at,
            completed_at=workflow.completed_at,
            error=workflow.error,
            metadata=wf_meta,
        )


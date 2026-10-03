"""RYVEN 3.0 — Agent Orchestration Engine.

Executes the OBSERVE -> REASON -> ACT -> OBSERVE -> VERIFY loop.
Coordinates tool execution, state progression, human confirmation gates,
failure recovery, and M14.2 ActionEngine event telemetry.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from app.actions.action_tracker import action_tracker
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType, _redact_dict
from app.agent.models import (
    AgentExecutionResult,
    AgentPlan,
    AgentState,
    AgentStatus,
    Observation,
    StepStatus,
    TaskStep,
    _utc_now_iso,
)
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.browser.security import BrowserSecurityValidator
from app.tools.registry import ToolRegistry, create_default_registry
from app.workflows.confirmation import ConfirmationManager


class AgentOrchestrator:
    """Core autonomous agent control plane executing the Observe-Plan-Act-Verify cycle."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guard: Optional[SafetyGuard] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        self.registry = registry or create_default_registry()
        self.safety_guard = guard or safety_guard
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()

        # In-memory store of active agent tasks
        self._active_tasks: Dict[str, AgentState] = {}
        # Pending confirmation callbacks: token -> (task_id, step_id)
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _safe_tool_output(output: Dict[str, Any]) -> Dict[str, Any]:
        """Redact secrets and omit bulky browser payloads from transient chat responses."""
        safe = BrowserSecurityValidator.redact_credentials(_redact_dict(output))
        if not isinstance(safe, dict):
            return {}
        for key in ("screenshot_b64", "image_b64", "raw_html", "raw_html_truncated", "cookies", "storage_state"):
            safe.pop(key, None)
        return safe

    def get_task(self, task_id: str) -> Optional[AgentState]:
        """Retrieve state for an active or completed agent task."""
        return self._active_tasks.get(task_id)

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        """Return summary of all tracked agent tasks."""
        return [
            {
                "task_id": state.task_id,
                "goal": state.user_goal,
                "status": state.status.value,
                "current_step": state.current_step_index,
                "total_steps": state.current_plan.total_steps if state.current_plan else 0,
                "created_at": state.created_at,
            }
            for state in self._active_tasks.values()
        ]

    async def execute_plan(
        self,
        plan: AgentPlan,
        session_id: str = "default",
        auto_confirm: bool = False,
        _resume_state: Optional[AgentState] = None,
        confirmed_step_ids: Optional[set[str]] = None,
    ) -> AgentExecutionResult:
        """Execute a full multi-step agent plan through the Observe-Reason-Act-Verify loop."""
        t0 = time.monotonic()
        is_resume = _resume_state is not None
        if _resume_state:
            state = _resume_state
            if not state.current_plan or state.current_plan.plan_id != plan.plan_id:
                raise ValueError("Cannot resume agent state with a different plan.")
            task_id = state.task_id
            plan = state.current_plan
            state.status = AgentStatus.EXECUTING
        else:
            task_id = f"task-{uuid.uuid4().hex[:8]}"
            state = AgentState(
                task_id=task_id,
                user_goal=plan.goal,
                status=AgentStatus.EXECUTING,
                current_plan=plan,
                current_step_index=0,
            )
            self._active_tasks[task_id] = state

        approved_steps = set(confirmed_step_ids or ())
        if not is_resume:
            # Publish TASK_STARTED only once for the lifetime of a task.
            await action_bus.publish(
                ActionEvent(
                    task_id=task_id,
                    action_type=ActionType.TASK_STARTED,
                    status=ActionStatus.STARTED,
                    title=f"Agent Task: {plan.goal[:60]}",
                    safe_metadata={"goal": plan.goal, "total_steps": plan.total_steps},
                )
            )

        all_observations: List[Observation] = []
        final_message = ""
        last_tool_output: Dict[str, Any] = {}
        context_state: Dict[str, Any] = {}

        try:
            while state.current_step_index < plan.total_steps:
                step = state.current_step
                if not step:
                    break

                # 1. Check for cancellation or pause
                if state.status == AgentStatus.CANCELLED:
                    step.status = StepStatus.SKIPPED
                    break

                # 2. Check dependencies
                deps_met = all(
                    any(s.step_id == dep and s.status == StepStatus.COMPLETED for s in plan.steps)
                    for dep in step.dependencies
                )
                if not deps_met:
                    logger.error(f"[AGENT] Step '{step.name}' failed prerequisites. Aborting.")
                    step.status = StepStatus.FAILED
                    step.error = "Unresolved step dependencies."
                    state.status = AgentStatus.FAILED
                    break

                # 3. Security & Permission Validation
                perm = self.safety_guard.validate_action(
                    tool_name=step.tool_name,
                    arguments=step.arguments,
                    raw_query=step.name,
                )
                if not perm.allowed:
                    logger.warning(f"[AGENT] Security blocked step '{step.name}': {perm.reason}")
                    step.status = StepStatus.FAILED
                    step.error = f"Permission denied: {perm.reason}"
                    state.status = AgentStatus.FAILED
                    break

                # 4. Confirmation Boundary Gate
                needs_confirm = (
                    step.requires_confirmation
                    or self.confirmation_mgr.requires_confirmation(step)
                )

                if needs_confirm and not auto_confirm and step.step_id not in approved_steps:
                    token = f"agent-confirm-{uuid.uuid4().hex[:6]}"
                    state.status = AgentStatus.WAITING_CONFIRMATION
                    step.status = StepStatus.WAITING_CONFIRMATION
                    state.confirmation_token = token
                    state.confirmation_message = (
                        f"Action '{step.name}' requires user confirmation before proceeding."
                    )
                    self._pending_confirmations[token] = {
                        "task_id": task_id,
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "tool_name": step.tool_name,
                        "arguments": step.arguments,
                        "timestamp": _utc_now_iso(),
                    }

                    await action_bus.publish(
                        ActionEvent(
                            task_id=task_id,
                            action_type=ActionType.CONFIRMATION_REQUESTED,
                            status=ActionStatus.WAITING_CONFIRMATION,
                            title=f"Confirmation Needed: {step.name}",
                            safe_metadata={"token": token, "tool": step.tool_name},
                            confirmation_required=True,
                        )
                    )

                    duration_ms = (time.monotonic() - t0) * 1000
                    return AgentExecutionResult(
                        task_id=task_id,
                        goal=plan.goal,
                        status=AgentStatus.WAITING_CONFIRMATION,
                        success=False,
                        message=state.confirmation_message,
                        steps_total=plan.total_steps,
                        steps_completed=plan.completed_count,
                        steps_failed=plan.failed_count,
                        observations=all_observations,
                        final_output={"confirmation_token": token, "step_id": step.step_id},
                        duration_ms=duration_ms,
                    )

                # 5. EXECUTION & OBSERVATION CYCLE
                step.status = StepStatus.RUNNING
                step.started_at = _utc_now_iso()
                state.status = AgentStatus.EXECUTING
                state.last_action = step.tool_name

                logger.info(
                    f"[AGENT] Step {step.order}/{plan.total_steps}: Executing '{step.name}' using '{step.tool_name}'"
                )

                step_success = False
                output: Dict[str, Any] = {}
                exec_arguments = dict(step.arguments)
                if context_state.get("project_name") and not exec_arguments.get("project_name"):
                    exec_arguments["project_name"] = context_state["project_name"]
                if context_state.get("patches") and "patches" in exec_arguments and not exec_arguments["patches"]:
                    exec_arguments["patches"] = context_state["patches"]
                if context_state.get("expected_files") and "expected_files" in exec_arguments and not exec_arguments["expected_files"]:
                    exec_arguments["expected_files"] = context_state["expected_files"]
                if "build_success" in context_state and exec_arguments.get("build_success") is None:
                    exec_arguments["build_success"] = context_state["build_success"]
                if "test_success" in context_state and exec_arguments.get("test_success") is None:
                    exec_arguments["test_success"] = context_state["test_success"]

                # Retry loop for this step
                while step.retry_count <= step.max_retries:
                    try:
                        output = await self.registry.execute_tool(
                            name=step.tool_name,
                            arguments=exec_arguments,
                            task_id=task_id,
                        )
                        step_success = output.get("success", True)
                        if step_success:
                            break
                        else:
                            step.retry_count += 1
                            logger.warning(
                                f"[AGENT] Step '{step.name}' returned failure. Retry {step.retry_count}/{step.max_retries}..."
                            )
                    except Exception as exc:
                        step.retry_count += 1
                        logger.error(
                            f"[AGENT] Error in step '{step.name}': {exc}. Retry {step.retry_count}/{step.max_retries}..."
                        )
                        output = {"success": False, "error": str(exc)}

                # 6. Capture Observation
                last_tool_output = output
                obs_summary = output.get("message", f"Step '{step.name}' completed.")
                obs = Observation(
                    step_id=step.step_id,
                    action_target=step.tool_name,
                    success=step_success,
                    summary=obs_summary,
                    data={"tool": step.tool_name, "success": step_success},
                )
                step.observation = obs
                state.current_observation = obs
                all_observations.append(obs)

                # 7. Verification & Progression
                if step_success:
                    if output.get("project_name"):
                        context_state["project_name"] = output["project_name"]
                    if output.get("patches"):
                        context_state["patches"] = output["patches"]
                        context_state["expected_files"] = [
                            patch.get("relative_path")
                            for patch in output["patches"]
                            if isinstance(patch, dict)
                        ]
                    if "exit_code" in output:
                        context_state["build_success"] = output.get("success", False)
                    if "tests_total" in output:
                        context_state["test_success"] = output.get("success", False)
                    step.status = StepStatus.COMPLETED
                    step.completed_at = _utc_now_iso()
                    state.advance_step()
                    final_message = obs_summary
                else:
                    step.status = StepStatus.FAILED
                    step.error = output.get("error") or output.get("message") or "Execution failed after retries."
                    state.status = AgentStatus.FAILED
                    final_message = f"Agent failed at step '{step.name}': {step.error}"
                    break

            # Loop finished
            if state.status != AgentStatus.FAILED and plan.completed_count == plan.total_steps:
                state.status = AgentStatus.COMPLETED
                final_message = f"Successfully completed all {plan.total_steps} step(s) for: {plan.goal}"
            elif state.status != AgentStatus.FAILED:
                state.status = AgentStatus.COMPLETED

        except Exception as top_exc:
            logger.error(f"[AGENT] Fatal orchestration error: {top_exc}", exc_info=True)
            state.status = AgentStatus.FAILED
            final_message = f"Agent crashed with internal error: {str(top_exc)}"

        duration_ms = (time.monotonic() - t0) * 1000

        # Publish final TASK completion/failure event
        await action_bus.publish(
            ActionEvent(
                task_id=task_id,
                action_type=ActionType.TASK_COMPLETED if state.status == AgentStatus.COMPLETED else ActionType.TASK_FAILED,
                status=ActionStatus.COMPLETED if state.status == AgentStatus.COMPLETED else ActionStatus.FAILED,
                title=f"Agent Task {'Completed' if state.status == AgentStatus.COMPLETED else 'Failed'}: {plan.goal[:50]}",
                safe_metadata={
                    "steps_completed": plan.completed_count,
                    "steps_total": plan.total_steps,
                    "duration_ms": duration_ms,
                },
            )
        )

        return AgentExecutionResult(
            task_id=task_id,
            goal=plan.goal,
            status=state.status,
            success=state.status == AgentStatus.COMPLETED,
            message=final_message,
            steps_total=plan.total_steps,
            steps_completed=plan.completed_count,
            steps_failed=plan.failed_count,
            observations=all_observations,
            final_output={
                "last_observation": all_observations[-1].model_dump() if all_observations else {},
                "tool_output": self._safe_tool_output(last_tool_output),
            },
            duration_ms=duration_ms,
        )

    async def confirm_action(self, token: str) -> Dict[str, Any]:
        """Approve a pending confirmation-gated step and resume execution."""
        pending = self._pending_confirmations.pop(token, None)
        if not pending:
            return {"success": False, "message": f"Confirmation token '{token}' not found or already consumed."}

        task_id = pending.get("task_id")
        state = self.get_task(task_id)
        if not state or not state.current_plan:
            return {"success": False, "message": f"Task '{task_id}' no longer active."}

        # Clear confirmation lock and resume
        state.status = AgentStatus.EXECUTING
        state.confirmation_token = None
        state.confirmation_message = None

        await action_bus.publish(
            ActionEvent(
                task_id=task_id,
                action_type=ActionType.CONFIRMATION_RECEIVED,
                status=ActionStatus.COMPLETED,
                title=f"Confirmation Approved: {pending.get('step_name')}",
                safe_metadata={"token": token, "approved": True},
            )
        )

        # Resume the same task at the approved step only; later consequential steps pause again.
        result = await self.execute_plan(
            state.current_plan,
            auto_confirm=False,
            _resume_state=state,
            confirmed_step_ids={pending["step_id"]},
        )
        return {"success": result.success, "message": result.message, "task_id": task_id}

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or pending agent task."""
        state = self.get_task(task_id)
        if state:
            state.status = AgentStatus.CANCELLED
            return True
        return False

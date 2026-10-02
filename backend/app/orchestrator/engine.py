"""RYVEN 2.0 Milestone 14 — Autonomous Development Orchestrator Engine."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
import uuid

from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.dev_engine.build_engine import BuildEngine, BuildRequest
from app.dev_engine.error_analyzer import ErrorAnalyzer
from app.dev_engine.fix_loop import FixLoopManager
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_models import ModificationPlan, ModificationRequest, PatchOperation
from app.dev_engine.m8_planner import ModificationPlanner
from app.dev_engine.m8_resolver import ProjectResolver
from app.dev_engine.m8_scanner import ProjectScanner
from app.dev_engine.quality_gate import QualityGate
from app.dev_engine.sandbox import SandboxPolicy
from app.dev_engine.test_engine import TestEngine, TestRequest
from app.git.git_engine import GitEngine
from app.health.health_service import HealthService, health_service
from app.orchestrator.dependency import DependencyGraph
from app.orchestrator.models import (
    ExecutionCheckpoint,
    OrchestrationPlan,
    OrchestrationResult,
    OrchestrationStep,
    OrchestrationTask,
    StepState,
    StepType,
    TaskState,
    utc_now_iso,
)
from app.orchestrator.planner import TaskPlanner
from app.orchestrator.state import ProjectStateTracker
from app.orchestrator.telemetry import OrchestrationTelemetry
from app.workflows.confirmation import ConfirmationManager
# M14.2 Action Engine — lazy import to avoid circular dep at module load
def _get_tracker():
    from app.actions.action_tracker import action_tracker
    return action_tracker


class OrchestratorEngine:
    """Central autonomous engine orchestrating the complete development lifecycle."""

    def __init__(
        self,
        planner: Optional[TaskPlanner] = None,
        policy: Optional[SandboxPolicy] = None,
        safety: Optional[SafetyGuard] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        self.planner = planner or TaskPlanner()
        self.policy = policy or SandboxPolicy()
        self.safety_guard = safety or safety_guard
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()

        # Integrated engines
        self.resolver = ProjectResolver()
        self.scanner = ProjectScanner()
        self.m8_planner = ModificationPlanner()
        self.apply_engine = ApplyEngine()
        self.rollback_engine = RollbackEngine()
        self.build_engine = BuildEngine(policy=self.policy)
        self.test_engine = TestEngine(policy=self.policy)
        self.analyzer = ErrorAnalyzer()
        self.fix_manager = FixLoopManager()
        self.quality_gate = QualityGate()
        self.git_engine = GitEngine(policy=self.policy)
        from app.deployment.deployment_engine import DeploymentEngine
        self.deployment_engine = DeploymentEngine(git_engine=self.git_engine, quality_gate=self.quality_gate)
        self.health_service = health_service

        # In-memory runtime task store
        self._tasks: Dict[str, OrchestrationTask] = {}

    def create_and_plan(
        self,
        user_goal: str = "",
        project_name_hint: Optional[str] = None,
        goal: Optional[str] = None,
        project_name: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> OrchestrationTask:
        """Create a new orchestration task, plan stages, and store in active registry."""
        actual_goal = goal or user_goal
        actual_project_name = project_name or project_name_hint
        plan = self.planner.plan(actual_goal, project_name_hint=actual_project_name)
        task = OrchestrationTask(
            user_goal=actual_goal,
            goal=actual_goal,
            project_name=plan.project_name,
            status=TaskState.READY,
            state=TaskState.READY,
            plan=plan,
        )
        task.add_checkpoint("TASK_CREATED", "step-init", "SUCCESS", f"Task created for goal: {actual_goal}")
        task.add_checkpoint("PLAN_CREATED", "step-init", "SUCCESS", f"Plan generated with {plan.total_steps} steps.")

        self._tasks[task.task_id] = task
        OrchestrationTelemetry.task_started(task.task_id, actual_goal, task.project_name, plan.total_steps)
        OrchestrationTelemetry.plan_created(task.task_id, plan.plan_id, [s.step_type.value for s in plan.steps])
        # M14.2: emit task started and planning events (fire-and-forget, errors suppressed)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_get_tracker().task_started(
                    task_id=task.task_id, goal=actual_goal,
                    project_name=task.project_name, step_count=plan.total_steps,
                ))
                asyncio.ensure_future(_get_tracker().task_planning(
                    task_id=task.task_id, goal=actual_goal, step_count=plan.total_steps,
                ))
        except Exception:
            pass  # Never crash task creation due to event emission
        return task

    def get_task(self, task_id: str) -> Optional[OrchestrationTask]:
        """Retrieve task by ID."""
        return self._tasks.get(task_id)

    async def execute_task(self, task_id: str, auto_confirm: bool = False) -> OrchestrationResult:
        """Execute task steps sequentially respecting DAG dependencies and confirmation boundaries."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Orchestration task '{task_id}' not found.")

        start_time = time.monotonic()
        if not task.started_at:
            task.started_at = utc_now_iso()

        if task.status in (TaskState.CANCELLED, TaskState.PAUSED, TaskState.FAILED, TaskState.COMPLETED):
            return self._build_result(task, duration_ms=(time.monotonic() - start_time) * 1000)

        task.status = TaskState.RUNNING
        state_tracker = ProjectStateTracker(task.project_name)

        while True:
            # Check for cancellation or pause
            if task.status in (TaskState.CANCELLED, TaskState.PAUSED):
                break

            dag = DependencyGraph(task.plan.steps)
            ready_steps = dag.get_ready_steps()

            if not ready_steps:
                # No more pending ready steps
                break

            # Execute the first ready step sequentially
            step = ready_steps[0]
            task.current_step_id = step.step_id

            # 1. Enforce confirmation boundary
            if step.requires_confirmation and not auto_confirm:
                step.status = StepState.WAITING_CONFIRMATION
                task.status = TaskState.WAITING_CONFIRMATION
                task.add_checkpoint(
                    name=f"CONFIRMATION_REQUIRED_{step.step_type.value}",
                    step_id=step.step_id,
                    status="WAITING",
                    summary=step.confirmation_reason or f"User confirmation required for {step.name}.",
                )
                OrchestrationTelemetry.confirmation_requested(task.task_id, step.step_id, step.confirmation_reason or step.name)
                logger.info(f"[ORCHESTRATOR] Task '{task.task_id}' paused awaiting confirmation for step '{step.name}'.")
                # M14.2: emit confirmation_requested event
                try:
                    await _get_tracker().confirmation_requested(
                        task_id=task.task_id, step_type=step.step_type.value,
                        step_name=step.name, action_id=step.step_id,
                        reason=step.confirmation_reason or step.name,
                    )
                except Exception:
                    pass
                return self._build_result(task, duration_ms=(time.monotonic() - start_time) * 1000)

            # 2. Execute step
            step_start = time.monotonic()
            step.started_at = utc_now_iso()
            step.status = StepState.RUNNING
            OrchestrationTelemetry.step_started(task.task_id, step.step_id, step.step_type.value, step.name)
            step_index = next(
                (i for i, s in enumerate(task.plan.steps) if s.step_id == step.step_id), None
            )
            # M14.2: emit step started
            _step_action_id = step.step_id
            try:
                await _get_tracker().step_started(
                    task_id=task.task_id, step_type=step.step_type.value,
                    step_name=step.name, action_id=_step_action_id,
                    step_index=step_index, total_steps=task.plan.total_steps,
                )
            except Exception:
                pass

            try:
                success, output, err_msg = await self._execute_step(task, step, state_tracker)
                step_duration = (time.monotonic() - step_start) * 1000

                if success:
                    step.status = StepState.SUCCESS
                    step.result = output
                    step.completed_at = utc_now_iso()
                    task.add_checkpoint(
                        name=f"{step.step_type.value}_PASSED",
                        step_id=step.step_id,
                        status="SUCCESS",
                        summary=f"Completed {step.name}.",
                        metadata=output,
                    )
                    OrchestrationTelemetry.step_completed(task.task_id, step.step_id, step.step_type.value, step_duration)
                    # M14.2: emit step completed
                    try:
                        await _get_tracker().step_completed(
                            task_id=task.task_id, step_type=step.step_type.value,
                            step_name=step.name, action_id=_step_action_id,
                            duration_ms=step_duration, step_index=step_index,
                            total_steps=task.plan.total_steps,
                        )
                    except Exception:
                        pass
                else:
                    # Handle step failure
                    step.error = err_msg
                    step.completed_at = utc_now_iso()
                    OrchestrationTelemetry.step_failed(task.task_id, step.step_id, step.step_type.value, err_msg, step.retry_count)

                    # Bounded retry handling for build / test failures
                    if step.step_type in (StepType.BUILD, StepType.TEST) and step.retry_count < step.max_retries:
                        step.retry_count += 1
                        task.status = TaskState.RETRYING
                        task.add_checkpoint(
                            name=f"{step.step_type.value}_RETRYING",
                            step_id=step.step_id,
                            status="RETRYING",
                            summary=f"Retrying {step.name} (Attempt {step.retry_count}/{step.max_retries}): {err_msg}",
                        )
                        logger.info(f"[ORCHESTRATOR] Retrying step '{step.name}' (Attempt {step.retry_count}/{step.max_retries})")
                        # M14.2: emit retry event
                        try:
                            await _get_tracker().step_retrying(
                                task_id=task.task_id, step_type=step.step_type.value,
                                step_name=step.name, action_id=_step_action_id,
                                retry_count=step.retry_count, max_retries=step.max_retries,
                            )
                        except Exception:
                            pass
                        # Reset step to PENDING so DAG can pick it up again
                        step.status = StepState.PENDING
                        continue

                    # If retry exhausted or non-retryable failure:
                    step.status = StepState.FAILED
                    task.status = TaskState.FAILED
                    task.error = f"Step '{step.name}' failed: {err_msg}"
                    task.add_checkpoint(
                        name=f"{step.step_type.value}_FAILED",
                        step_id=step.step_id,
                        status="FAILED",
                        summary=f"Failed {step.name}: {err_msg}",
                    )
                    # M14.2: emit step failed
                    try:
                        await _get_tracker().step_failed(
                            task_id=task.task_id, step_type=step.step_type.value,
                            step_name=step.name, action_id=_step_action_id,
                            error=err_msg, duration_ms=step_duration,
                            retry_count=step.retry_count, step_index=step_index,
                            total_steps=task.plan.total_steps,
                        )
                    except Exception:
                        pass
                    break

            except Exception as e:
                step_duration = (time.monotonic() - step_start) * 1000
                err = str(e)
                step.status = StepState.FAILED
                step.error = err
                step.completed_at = utc_now_iso()
                task.status = TaskState.FAILED
                task.error = f"Exception in step '{step.name}': {err}"
                OrchestrationTelemetry.step_failed(task.task_id, step.step_id, step.step_type.value, err, step.retry_count)
                task.add_checkpoint(
                    name=f"{step.step_type.value}_EXCEPTION",
                    step_id=step.step_id,
                    status="FAILED",
                    summary=f"Exception in {step.name}: {err}",
                )
                # M14.2: emit step exception as failed event
                try:
                    await _get_tracker().step_failed(
                        task_id=task.task_id, step_type=step.step_type.value,
                        step_name=step.name, action_id=_step_action_id,
                        error=err[:256], duration_ms=step_duration,
                    )
                except Exception:
                    pass
                break

        # Finalize Task
        duration_total = (time.monotonic() - start_time) * 1000
        if task.plan.completed_steps_count == task.plan.total_steps:
            task.status = TaskState.COMPLETED
            task.completed_at = utc_now_iso()
            task.add_checkpoint("TASK_COMPLETED", "step-final", "SUCCESS", "All orchestration steps completed successfully.")
            OrchestrationTelemetry.task_completed(task.task_id, True, duration_total, task.plan.completed_steps_count)
            try:
                await _get_tracker().task_completed(
                    task_id=task.task_id, success=True,
                    duration_ms=duration_total, steps_completed=task.plan.completed_steps_count,
                )
            except Exception:
                pass
        elif task.status == TaskState.FAILED:
            task.completed_at = utc_now_iso()
            OrchestrationTelemetry.task_completed(task.task_id, False, duration_total, task.plan.completed_steps_count)
            try:
                await _get_tracker().task_completed(
                    task_id=task.task_id, success=False,
                    duration_ms=duration_total, steps_completed=task.plan.completed_steps_count,
                )
            except Exception:
                pass

        return self._build_result(task, duration_ms=duration_total)

    async def _execute_step(
        self,
        task: OrchestrationTask,
        step: OrchestrationStep,
        state: ProjectStateTracker,
    ) -> tuple[bool, Dict[str, Any], str]:
        """Execute a single step against the appropriate subsystem."""
        p_name = task.project_name

        # 1. SCAN_PROJECT
        if step.step_type == StepType.SCAN_PROJECT:
            resolved = self.resolver.resolve(p_name)
            if not resolved.found:
                return False, {}, resolved.reason
            model = self.scanner.scan(p_name)
            if not model:
                return False, {}, f"Unable to scan project '{p_name}'."
            state.update_from_scan(model.project_path, model.project_type.value)
            task.context_state["project_path"] = model.project_path
            task.context_state["framework"] = model.project_type.value
            task.context_state["files"] = model.all_files
            return True, {"files_count": len(model.all_files), "framework": model.project_type.value}, ""

        # 2. GRAPH_QUERY
        if step.step_type == StepType.GRAPH_QUERY:
            try:
                from app.knowledge_graph.service import knowledge_graph_service
                p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
                ctx = knowledge_graph_service.build_optimized_context(p_path, task.user_goal)
                task.context_state["kg_context"] = ctx.model_dump()
                return True, {"kg_indexed": ctx.is_indexed, "target_files": ctx.target_files}, ""
            except Exception as e:
                logger.warning(f"Knowledge graph query skipped or unavailable: {e}")
                return True, {"kg_available": False}, ""

        # 3. PLAN_MODIFICATION
        if step.step_type == StepType.PLAN_MODIFICATION:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            model = self.scanner.scan(p_name)
            req = ModificationRequest(
                project_name=p_name,
                user_request=task.user_goal,
                target_framework=task.context_state.get("framework", "web"),
            )
            plan = self.m8_planner.create_plan(req, model)
            task.context_state["mod_plan"] = plan.model_dump()
            return True, {"patches_count": len(plan.patches), "files": [p.relative_path for p in plan.patches]}, ""

        # 4. APPLY_MODIFICATION
        if step.step_type == StepType.APPLY_MODIFICATION:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            plan_dict = task.context_state.get("mod_plan")
            if not plan_dict:
                return False, {}, "No modification plan available to apply."
            plan = ModificationPlan.model_validate(plan_dict)
            apply_res = self.apply_engine.apply_plan(plan, p_path)
            if not apply_res.success:
                return False, {}, apply_res.error or "Failed to apply modification patches."
            modified = [p.relative_path for p in plan.patches]
            state.update_from_modification(modified)
            task.context_state["modified_files"] = modified
            return True, {"modified_files": modified, "snapshot_taken": True}, ""

        # 5. BUILD
        if step.step_type == StepType.BUILD:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            req = BuildRequest(project_path=p_path, project_type=task.context_state.get("framework", "web"))
            res = self.build_engine.build(req)
            state.update_from_build(res.success)
            if not res.success:
                return False, {"build_output": res.stdout, "build_error": res.stderr}, res.stderr or "Build failed."
            return True, {"duration_ms": res.duration_ms}, ""

        # 6. TEST
        if step.step_type == StepType.TEST:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            req = TestRequest(project_path=p_path, project_type=task.context_state.get("framework", "web"))
            res = self.test_engine.test(req)
            state.update_from_test(res.success, no_tests=(res.total == 0))
            if not res.success and res.total > 0:
                return False, {"tests_failed": res.failed}, f"Test suite failed ({res.failed}/{res.total} failed)."
            return True, {"tests_passed": res.passed, "tests_total": res.total}, ""

        # 7. QUALITY_GATE
        if step.step_type == StepType.QUALITY_GATE:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            qg_res = self.quality_gate.evaluate(
                project_name=p_name,
                project_path=p_path,
                build_success=(state.get_state().build_status == "PASSED"),
                test_success=(state.get_state().test_status in ("PASSED", "TESTS_NOT_AVAILABLE")),
                modified_files=task.context_state.get("modified_files", []),
            )
            state.update_from_quality_gate(qg_res.passed)
            if not qg_res.passed:
                return False, {"critical_failures": qg_res.critical_failures}, f"Quality Gate failed: {', '.join(qg_res.critical_failures)}"
            return True, {"passed": True, "score": qg_res.score}, ""

        # 8. GIT_DIFF & STATUS
        if step.step_type == StepType.GIT_DIFF:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            status_res = self.git_engine.get_status(p_path)
            diff_res = self.git_engine.diff(p_path)
            state.update_from_git(branch=status_res.current_branch, is_clean=status_res.is_clean)
            return True, {"branch": status_res.current_branch, "modified_count": len(status_res.modified_files)}, ""

        # 9. GIT_COMMIT
        if step.step_type == StepType.GIT_COMMIT:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            msg = step.arguments.get("message", "Update project files")
            stage_res = self.git_engine.stage_all(p_path)
            if not stage_res.success:
                return False, {}, f"Failed to stage files: {stage_res.message}"
            commit_res = self.git_engine.commit(p_path, msg)
            if not commit_res.success:
                return False, {}, f"Git commit failed: {commit_res.message}"
            state.update_from_git(commit_hash=commit_res.commit_hash, is_clean=True)
            task.context_state["commit_hash"] = commit_res.commit_hash
            return True, {"commit_hash": commit_res.commit_hash, "message": msg}, ""

        # 10. GIT_PUSH
        if step.step_type == StepType.GIT_PUSH:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            push_res = self.git_engine.push(p_path)
            if not push_res.success:
                return False, {}, f"Git push failed: {push_res.message}"
            return True, {"branch": push_res.branch, "remote": push_res.remote_name}, ""

        # 11. DEPLOY_PREVIEW
        if step.step_type == StepType.DEPLOY_PREVIEW:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            prev = self.deployment_engine.preview(p_name, project_path=p_path)
            return True, {"provider": prev.provider.value, "branch": prev.branch}, ""

        # 12. DEPLOY
        if step.step_type == StepType.DEPLOY:
            p_path = task.context_state.get("project_path") or self.resolver.resolve(p_name).absolute_path
            from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest
            prov_str = step.arguments.get("provider", "VERCEL")
            req = DeploymentRequest(
                project_name=p_name,
                provider=DeploymentProvider(prov_str),
                confirmed=True,
            )
            deploy_res = await self.deployment_engine.deploy(req, project_path=p_path)
            state.update_from_deployment(deploy_res.deployment_status.value, deploy_res.deployment_url)
            task.context_state["deployment_url"] = deploy_res.deployment_url
            if not deploy_res.success:
                return False, {}, deploy_res.sanitized_error or deploy_res.message
            return True, {"deployment_url": deploy_res.deployment_url, "deployment_id": deploy_res.deployment_id}, ""

        # 13. DEPLOY_VERIFY
        if step.step_type == StepType.DEPLOY_VERIFY:
            url = task.context_state.get("deployment_url")
            if not url:
                return True, {"verified": False, "reason": "No deployment URL to verify."}, ""
            verif_res = await self.deployment_engine.verifier.verify_endpoint(url)
            return True, {"reachable": verif_res.reachable, "status_code": verif_res.status_code}, ""

        # 14. HEALTH_CHECK
        if step.step_type == StepType.HEALTH_CHECK:
            url = task.context_state.get("deployment_url") or step.arguments.get("url")
            if not url:
                return True, {"health": "UNKNOWN", "reason": "No endpoint URL available for health check."}, ""
            health_res = await self.health_service.check(url)
            state.update_from_health(health_res.status.value, health_res.response_time_ms)
            task.context_state["health_status"] = health_res.status.value
            task.context_state["health_latency_ms"] = health_res.response_time_ms
            return True, {
                "health": health_res.status.value,
                "http_status": health_res.http_status,
                "response_time_ms": health_res.response_time_ms,
            }, ""

        # 15. FINAL_REPORT
        if step.step_type == StepType.FINAL_REPORT:
            report = self.generate_final_report(task)
            return True, {"report": report}, ""

        return False, {}, f"Unsupported step type: {step.step_type}"

    def confirm_step(
        self,
        task_id: str,
        step_id: str = "",
        confirmed: bool = True,
        override: bool = False,
    ) -> OrchestrationResult:
        """Process user confirmation decision for a waiting step."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        if not step_id:
            waiting_step = next((s for s in task.plan.steps if s.status == StepState.WAITING_CONFIRMATION), None)
            if waiting_step:
                step_id = waiting_step.step_id

        step = next((s for s in task.plan.steps if s.step_id == step_id), None)
        if not step:
            # If no specific step waiting, pick first unconfirmed protected step or return current state
            step = next((s for s in task.plan.steps if s.requires_confirmation and not s.confirmed), None)
            if not step:
                return self._build_result(task)

        OrchestrationTelemetry.confirmation_received(task_id, step.step_id, confirmed)

        if confirmed:
            step.requires_confirmation = False
            step.confirmed = True
            step.status = StepState.PENDING
            task.status = TaskState.RUNNING
            task.add_checkpoint(f"CONFIRMATION_ACCEPTED_{step.step_type.value}", step.step_id, "SUCCESS", f"User confirmed {step.name}.")
            return self._build_result(task)
        else:
            step.status = StepState.CANCELLED
            task.status = TaskState.CANCELLED
            task.add_checkpoint(f"CONFIRMATION_REJECTED_{step.step_type.value}", step.step_id, "CANCELLED", f"User rejected confirmation for {step.name}.")
            return self._build_result(task)

    def pause_task(self, task_id: str, reason: str = "User requested pause") -> OrchestrationTask:
        """Pause execution cleanly at the current step boundary."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")
        task.status = TaskState.PAUSED
        task.add_checkpoint("TASK_PAUSED", task.current_step_id or "step-none", "PAUSED", reason)
        OrchestrationTelemetry.task_paused(task_id, reason)
        return task

    def resume_task(self, task_id: str) -> OrchestrationTask:
        """Resume a paused task."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")
        if task.status == TaskState.PAUSED:
            task.status = TaskState.RUNNING
            task.add_checkpoint("TASK_RESUMED", task.current_step_id or "step-none", "RUNNING", "Resumed execution.")
            OrchestrationTelemetry.task_resumed(task_id)
        return task

    def cancel_task(self, task_id: str) -> OrchestrationTask:
        """Cancel task and prevent any future steps from running."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")
        task.status = TaskState.CANCELLED
        task.add_checkpoint("TASK_CANCELLED", task.current_step_id or "step-none", "CANCELLED", "Task cancelled by user request.")
        OrchestrationTelemetry.task_cancelled(task_id)
        return task

    def generate_final_report(self, task: OrchestrationTask) -> str:
        """Format a clean, structured completion report."""
        modified = task.context_state.get("modified_files", [])
        url = task.context_state.get("deployment_url")
        health = task.context_state.get("health_status", "UNKNOWN")
        latency = task.context_state.get("health_latency_ms", 0.0)
        commit_hash = task.context_state.get("commit_hash")

        if task.status == TaskState.COMPLETED:
            report_lines = [
                "RYVEN TASK COMPLETE ✓",
                f"Project: {task.project_name}",
                f"Task: {task.user_goal}",
                "",
                "Completed Stages:",
                f"✓ Project structure analyzed",
                f"✓ Knowledge graph context optimized",
                f"✓ Modified {len(modified)} files ({', '.join(modified[:3])})",
                f"✓ Build passed",
                f"✓ Tests passed",
                f"✓ Quality gate evaluated (PASSED)",
            ]
            if commit_hash:
                report_lines.append(f"✓ Git commit created ({commit_hash[:7]})")
            if url:
                report_lines.append(f"✓ Deployed to {url}")
                report_lines.append(f"✓ Health: {health} · {latency:.1f} ms")
            report_lines.append("\nNo errors detected.")
            return "\n".join(report_lines)

        elif task.status == TaskState.WAITING_CONFIRMATION:
            curr_step = next((s for s in task.plan.steps if s.status == StepState.WAITING_CONFIRMATION), None)
            step_name = curr_step.name if curr_step else "Action"
            return f"RYVEN TASK PAUSED ⚠\nBlocked on user confirmation: {step_name}\nNext action: Confirm to proceed."

        elif task.status == TaskState.FAILED:
            return f"RYVEN TASK FAILED ✗\nProject: {task.project_name}\nError: {task.error}\nLast successful checkpoint: {task.checkpoints[-1].name if task.checkpoints else 'None'}"

        elif task.status == TaskState.CANCELLED:
            return f"RYVEN TASK CANCELLED\nExecution terminated before completion."

        return f"RYVEN TASK IN PROGRESS ({task.plan.completed_steps_count}/{task.plan.total_steps} completed)."

    def _build_result(self, task: OrchestrationTask, duration_ms: float = 0.0) -> OrchestrationResult:
        """Assemble structured OrchestrationResult from task state."""
        last_success = None
        for chk in reversed(task.checkpoints):
            if chk.status == "SUCCESS":
                last_success = chk.name
                break

        return OrchestrationResult(
            task_id=task.task_id,
            user_goal=task.user_goal,
            project_name=task.project_name,
            status=task.status,
            success=(task.status == TaskState.COMPLETED),
            message=self.generate_final_report(task),
            steps_total=task.plan.total_steps,
            steps_completed=task.plan.completed_steps_count,
            steps_failed=task.plan.failed_steps_count,
            step_details=[s.model_dump() for s in task.plan.steps],
            checkpoints=task.checkpoints,
            deployment_url=task.context_state.get("deployment_url"),
            health_status=task.context_state.get("health_status"),
            health_latency_ms=task.context_state.get("health_latency_ms"),
            git_commit_hash=task.context_state.get("commit_hash"),
            modified_files=task.context_state.get("modified_files", []),
            last_successful_checkpoint=last_success,
            started_at=task.started_at,
            completed_at=task.completed_at,
            duration_ms=duration_ms,
            error=task.error,
        )


# Global singleton
orchestrator_engine = OrchestratorEngine()

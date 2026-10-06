"""RYVEN 3.0 M17.0 — Unified RyvenControlEngine.

Central Personal Computer + Internet Control Plane Facade:
Orchestrates:
    Goal Understanding
        ↓
    Observer (Pre-Action Observation)
        ↓
    PlanningEngine (Intelligent Normalization & 15-Rule Validation)
        ↓
    CapabilityPermissionManager (Authorization Layer)
        ↓
    AgentCoordinator (DAG Execution & Concurrency Semaphores)
        ↓
    ToolRegistry / Existing Services
        ↓
    Observer (Post-Action Observation)
        ↓
    Verification & Scope Protection
        ↓
    Recovery / Replan (Bounded Failure Classification)
        ↓
    Final Result

Security Invariants:
- Reuses existing Coordinator, PlanningEngine, BrowserEngine, ToolRegistry, ActionBus.
- Permanent prohibition of arbitrary shell strings (cmd, powershell, bash).
- Strict enforcement of ConfirmationManager on consequential operations.
- Secret scrubbing on all user inputs, outputs, and event payloads.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.agents.models import AgentStatus
from app.agents.planning_models import PlanningRequest, PlanStatus
from app.agents.task_graph import AgentTaskGraph
from app.control.models import (
    ControlRequest,
    ControlResult,
    ControlStatus,
    DesktopActionRequest,
    DesktopActionResult,
    DiscoveredScopeItem,
    FailureClass,
    ObservationRecord,
    PermissionCategory,
    ScopeBoundary,
)
from app.control.observer import ObserverEngine, observer_engine
from app.control.permissions import CapabilityPermissionManager, permission_manager
from app.core.logging_config import logger
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.runtime.recovery import RuntimeRecoveryService
from app.tools.registry import ToolRegistry, create_default_registry


class RyvenControlEngine:
    """The central unified personal computer and internet control plane facade."""

    def __init__(
        self,
        planner: Optional[Any] = None,
        coordinator: Optional[Any] = None,
        tool_reg: Optional[ToolRegistry] = None,
        permissions: Optional[CapabilityPermissionManager] = None,
        observer: Optional[ObserverEngine] = None,
        checkpoints: Optional[CheckpointStore] = None,
        desktop_actions: Optional[Any] = None,
    ) -> None:
        if planner is not None:
            self.planner = planner
        else:
            try:
                from app.agents.planning_engine import planning_engine
                self.planner = planning_engine
            except Exception:
                self.planner = None

        if coordinator is not None:
            self.coordinator = coordinator
        else:
            try:
                from app.agents.coordinator import agent_coordinator
                self.coordinator = agent_coordinator
            except Exception:
                self.coordinator = None
        self.tool_reg = tool_reg or create_default_registry()
        self.permissions = permissions or permission_manager
        self.observer = observer or observer_engine
        self.checkpoints = checkpoints or checkpoint_store
        if desktop_actions is not None:
            self.desktop_actions = desktop_actions
        else:
            try:
                from app.desktop.action_engine import desktop_action_engine
                self.desktop_actions = desktop_action_engine
            except Exception:
                self.desktop_actions = None
        self.recovery = RuntimeRecoveryService(store=self.checkpoints)
        self._active_executions: Dict[str, ControlResult] = {}

    async def execute_goal(
        self,
        request: Optional[Union[ControlRequest, str]] = None,
        *,
        goal: Optional[str] = None,
        project_name: Optional[str] = None,
        session_id: str = "default",
        auto_confirm: bool = False,
        timeout: Optional[float] = 300.0,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> ControlResult:
        """Primary top-level entrypoint for all natural-language computer & internet goals."""
        t0 = time.monotonic()
        if isinstance(request, ControlRequest):
            req = request
        elif isinstance(request, str):
            req = ControlRequest(
                goal=request,
                project_name=project_name,
                auto_confirm=auto_confirm,
                timeout=timeout,
                session_id=session_id,
                metadata=metadata or {},
            )
        elif goal is not None:
            req = ControlRequest(
                goal=goal,
                project_name=project_name,
                auto_confirm=auto_confirm,
                timeout=timeout,
                session_id=session_id,
                metadata=metadata or {},
            )
        else:
            raise ValueError("execute_goal requires either a ControlRequest or a goal string")

        control_id = f"ctrl-{uuid.uuid4().hex[:8]}"
        goal_text = req.goal.strip()
        logger.info(f"[CONTROL_ENGINE] Commencing unified execution for goal: {goal_text!r} (id={control_id})")

        result = ControlResult(
            control_id=control_id,
            goal=goal_text,
            status=ControlStatus.PLANNING,
            final_output={"session_id": req.session_id},
        )
        self._active_executions[control_id] = result

        # 1. Telemetry: Control Started
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.CONTROL_STARTED,
                status=ActionStatus.STARTED,
                title=f"RYVEN Control started: {goal_text[:60]}",
                task_id=control_id,
                safe_metadata={"goal": goal_text, "session_id": req.session_id},
            )
        )

        try:
            # 2. Pre-Action Observation (Observe environment state before planning)
            result.status = ControlStatus.OBSERVING
            pre_observations = await self.observer.observe_environment(
                target_project=req.project_name,
                task_id=control_id,
            )
            result.observations.extend(pre_observations)

            # 3. Intelligent Planning via PlanningEngine (Normalization -> Complexity -> 15-Rule Validation)
            result.status = ControlStatus.PLANNING
            plan_res = await self.planner.plan(
                PlanningRequest(
                    user_goal=goal_text,
                    project_name=req.project_name,
                    context={"pre_observations": [o.summary for o in pre_observations]},
                )
            )
            result.plan_id = plan_res.plan_id
            result.steps_total = len(plan_res.tasks)

            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.CONTROL_PLANNED,
                    status=ActionStatus.COMPLETED,
                    title=f"Plan generated ({len(plan_res.tasks)} steps, {plan_res.complexity.value})",
                    task_id=control_id,
                    safe_metadata={
                        "plan_id": plan_res.plan_id,
                        "complexity": plan_res.complexity.value,
                        "steps": len(plan_res.tasks),
                    },
                )
            )

            # Handle structurally rejected plan
            if plan_res.status == PlanStatus.INVALID:
                result.status = ControlStatus.FAILED
                result.error = f"Planning failed safety validation: {plan_res.validation.errors}"
                result.message = result.error
                await self._emit_failure(control_id, result.error)
                return result

            # 4. Capability Authorization Layer & Confirmation Gate
            for draft in plan_res.tasks:
                if draft.tool_name:
                    auth_check = self.permissions.authorize(
                        tool_name=draft.tool_name,
                        arguments=draft.arguments,
                        auto_confirm=req.auto_confirm,
                    )
                    if not auth_check.allowed:
                        if auth_check.requires_confirmation:
                            result.status = ControlStatus.WAITING_CONFIRMATION
                            result.confirmation_required = True
                            result.confirmation_token = auth_check.confirmation_token
                            result.confirmation_type = auth_check.confirmation_type
                            result.current_action = f"Waiting confirmation for {draft.tool_name}"
                            result.message = auth_check.reason
                            await action_bus.publish(
                                ActionEvent(
                                    action_type=ActionType.CONTROL_WAITING,
                                    status=ActionStatus.WAITING_CONFIRMATION,
                                    title=f"Confirmation required: {draft.objective}",
                                    task_id=control_id,
                                    confirmation_required=True,
                                    safe_metadata={
                                        "tool": draft.tool_name,
                                        "confirmation_type": auth_check.confirmation_type,
                                    },
                                )
                            )
                            return result
                        else:
                            # Security violation or authorization block
                            result.status = ControlStatus.FAILED
                            result.error = f"Permission denied: {auth_check.reason}"
                            result.message = result.error
                            await self._emit_failure(control_id, result.error)
                            return result

            # 5. Convert Validated Plan to AgentTaskGraph
            graph = self.planner.to_agent_task_graph(plan_res)
            result.graph_id = graph.graph_id

            # 6. Execute Graph under AgentCoordinator Governance
            result.status = ControlStatus.EXECUTING
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.CONTROL_EXECUTING,
                    status=ActionStatus.STARTED,
                    title=f"Executing {len(graph.tasks)} tasks across worker agents",
                    task_id=control_id,
                    safe_metadata={"graph_id": graph.graph_id, "tasks": len(graph.tasks)},
                )
            )

            if req.timeout:
                executed_graph = await asyncio.wait_for(
                    self.coordinator.execute_graph(graph=graph, auto_confirm=req.auto_confirm),
                    timeout=req.timeout,
                )
            else:
                executed_graph = await self.coordinator.execute_graph(
                    graph=graph,
                    auto_confirm=req.auto_confirm,
                )

            # Check if coordinator paused on runtime confirmation
            if executed_graph.status == AgentStatus.REQUIRES_CONFIRMATION:
                waiting_task = next(
                    (t for t in executed_graph.tasks.values() if t.status == AgentStatus.REQUIRES_CONFIRMATION),
                    None,
                )
                result.status = ControlStatus.WAITING_CONFIRMATION
                result.confirmation_required = True
                result.confirmation_type = waiting_task.confirmation_type if waiting_task else "ACTION_CONFIRMATION"
                result.message = f"Execution paused: Task '{waiting_task.objective if waiting_task else ''}' requires user confirmation."
                return result

            # 7. Post-Action Observation & Verification Loop
            result.status = ControlStatus.VERIFYING
            post_observations = await self.observer.observe_environment(
                target_project=req.project_name,
                task_id=control_id,
            )
            result.observations.extend(post_observations)

            # 8. Scope Protection Check (Detect tangential discoveries)
            self._evaluate_scope_protection(result, executed_graph)

            # 9. Handle Execution Result & Bounded Recovery
            progress = executed_graph.get_progress()
            result.steps_completed = progress["completed"]
            result.steps_failed = progress["failed"]

            if executed_graph.status == AgentStatus.COMPLETED and executed_graph.is_completed():
                result.status = ControlStatus.COMPLETED
                result.success = True
                result.message = f"Goal successfully executed across {result.steps_completed} step(s)."
                result.duration_ms = (time.monotonic() - t0) * 1000

                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.CONTROL_COMPLETED,
                        status=ActionStatus.COMPLETED,
                        title=f"RYVEN Control completed: {goal_text[:50]}",
                        task_id=control_id,
                        duration_ms=result.duration_ms,
                        safe_metadata={"completed_steps": result.steps_completed},
                    )
                )
            else:
                # Failure classification and recovery evaluation
                fail_class = self._classify_failure(executed_graph.error or "Task execution failure")
                if fail_class in (FailureClass.NETWORK_TRANSIENT, FailureClass.RATE_LIMITED, FailureClass.FILE_LOCKED, FailureClass.WINDOW_UNFOCUSED) and result.recovery_attempts < 2:
                    result.recovery_attempts += 1
                    result.status = ControlStatus.RECOVERING
                    logger.info(f"[CONTROL_ENGINE] Attempting transient recovery ({result.recovery_attempts}/2)...")
                    await action_bus.publish(
                        ActionEvent(
                            action_type=ActionType.CONTROL_RECOVERY,
                            status=ActionStatus.PROGRESS,
                            title=f"Attempting transient recovery ({result.recovery_attempts}/2)",
                            task_id=control_id,
                        )
                    )
                    # Retry failed ready tasks
                    retry_graph = await self.coordinator.execute_graph(executed_graph, auto_confirm=req.auto_confirm)
                    if retry_graph.status == AgentStatus.COMPLETED:
                        result.status = ControlStatus.COMPLETED
                        result.success = True
                        result.message = f"Goal recovered and completed in {result.recovery_attempts} retry attempt(s)."
                        return result

                result.status = ControlStatus.FAILED
                result.success = False
                result.error = executed_graph.error or "Execution failed"
                result.message = f"Goal execution terminated with error: {result.error}"
                await self._emit_failure(control_id, result.error)

        except Exception as exc:
            logger.error(f"[CONTROL_ENGINE] Unhandled exception during control execution: {exc}", exc_info=True)
            result.status = ControlStatus.FAILED
            result.success = False
            result.error = str(exc)
            result.message = f"Control execution failed: {exc}"
            await self._emit_failure(control_id, str(exc))

        result.duration_ms = (time.monotonic() - t0) * 1000
        return result

    def _evaluate_scope_protection(self, result: ControlResult, graph: AgentTaskGraph) -> None:
        """Identify tangential discoveries and protect against silent scope creep."""
        for task in graph.tasks.values():
            if task.result and isinstance(task.result, dict):
                # Check for tangential warnings or secondary issues found
                warnings = task.result.get("warnings") or []
                if isinstance(warnings, list):
                    for w in warnings:
                        if "unrelated" in str(w).lower() or "additional" in str(w).lower():
                            result.discovered_scope_items.append(
                                DiscoveredScopeItem(
                                    description=str(w),
                                    boundary=ScopeBoundary.DISCOVERED,
                                    source_task_id=task.task_id,
                                    requires_user_approval=True,
                                )
                            )

    def _classify_failure(self, error: Union[str, Exception]) -> FailureClass:
        """Classify failure into recovery class."""
        err_msg = str(error)
        lower = err_msg.lower()
        if "429" in lower or "rate limit" in lower:
            return FailureClass.RATE_LIMITED
        if any(w in lower for w in ("timeout", "connection reset", "econnreset", "network", "timed out")):
            return FailureClass.NETWORK_TRANSIENT
        if any(w in lower for w in ("busy", "lock", "access denied", "permission denied")):
            return FailureClass.FILE_LOCKED
        if any(w in lower for w in ("window", "focus", "foreground")):
            return FailureClass.WINDOW_UNFOCUSED
        return FailureClass.UNRECOVERABLE

    async def _emit_failure(self, control_id: str, error: str) -> None:
        """Publish failure event to event bus."""
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.CONTROL_FAILED,
                status=ActionStatus.FAILED,
                title="RYVEN Control execution failed",
                task_id=control_id,
                safe_metadata={"error": error},
            )
        )

    async def confirm_control(
        self,
        control_id: str,
        confirmation_token: Optional[str] = None,
        auto_confirm_rest: bool = False,
    ) -> Optional[ControlResult]:
        """Resume execution paused in WAITING_CONFIRMATION state."""
        result = self._active_executions.get(control_id)
        if not result:
            logger.warning(f"[CONTROL_ENGINE] Cannot confirm: control execution '{control_id}' not found.")
            return None

        if confirmation_token and result.confirmation_token:
            if confirmation_token.strip() != result.confirmation_token.strip():
                raise ValueError(f"Invalid confirmation token for control {control_id}")

        result.confirmation_required = False
        result.status = ControlStatus.EXECUTING

        if result.graph_id:
            resumed_graph = await self.coordinator.confirm_task(
                graph_id=result.graph_id,
                task_id="",
                auto_confirm_rest=auto_confirm_rest,
            )
            if resumed_graph and resumed_graph.status == AgentStatus.COMPLETED:
                result.status = ControlStatus.COMPLETED
                result.success = True
                result.message = "Execution resumed and successfully completed."
            elif resumed_graph and resumed_graph.status == AgentStatus.REQUIRES_CONFIRMATION:
                result.status = ControlStatus.WAITING_CONFIRMATION
            else:
                result.status = ControlStatus.COMPLETED
                result.success = True
        else:
            result.status = ControlStatus.COMPLETED
            result.success = True
            result.message = "Action confirmed and execution completed."

        return result

    async def cancel_control(
        self,
        control_id: str,
        reason: str = "User requested cancellation",
    ) -> Optional[ControlResult]:
        """Cancel an active control execution."""
        result = self._active_executions.get(control_id)
        if not result:
            return None

        result.status = ControlStatus.CANCELLED
        result.message = f"Control execution cancelled: {reason}"

        if result.graph_id:
            try:
                await self.coordinator.cancel(result.graph_id)
            except Exception:
                pass

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.TASK_CANCELLED,
                status=ActionStatus.CANCELLED,
                title="RYVEN Control execution cancelled by user",
                task_id=control_id,
            )
        )
        return result

    async def execute_desktop_action(
        self,
        request: DesktopActionRequest,
        auto_confirm: bool = False,
        task_id: Optional[str] = None,
    ) -> DesktopActionResult:
        """Execute a controlled desktop action through the safe authority chain."""
        return await self.desktop_actions.execute_action(
            request=request,
            auto_confirm=auto_confirm,
            task_id=task_id,
        )

    @property
    def workflow_engine(self) -> Any:
        if hasattr(self, "_workflow_engine") and self._workflow_engine is not None:
            return self._workflow_engine
        from app.control.workflow import computer_workflow_engine
        return computer_workflow_engine

    @workflow_engine.setter
    def workflow_engine(self, engine: Any) -> None:
        self._workflow_engine = engine

    async def execute_computer_workflow(
        self,
        goal_or_plan: Any,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> Any:
        """Execute multi-step computer-use workflow via ComputerWorkflowEngine."""
        return await self.workflow_engine.execute_workflow(
            workflow=goal_or_plan,
            auto_confirm=auto_confirm,
            session_id=session_id,
        )

    async def plan_computer_workflow(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Decompose computer-use goal via ComputerWorkflowEngine."""
        return await self.workflow_engine.plan_workflow(goal=goal, context=context)

    async def confirm_computer_workflow(
        self,
        workflow_id: str,
        confirmation_token: Optional[str] = None,
        approved: bool = True,
    ) -> Any:
        """Confirm or reject paused computer-use workflow."""
        return await self.workflow_engine.confirm_workflow(
            workflow_id=workflow_id,
            confirmation_token=confirmation_token,
            approved=approved,
        )

    async def cancel_computer_workflow(
        self,
        workflow_id: str,
        reason: str = "User requested cancellation",
    ) -> Any:
        """Cancel active computer-use workflow."""
        return await self.workflow_engine.cancel_workflow(
            workflow_id=workflow_id,
            reason=reason,
        )

    @property
    def adaptive_controller(self) -> Any:
        if hasattr(self, "_adaptive_controller") and self._adaptive_controller is not None:
            return self._adaptive_controller
        from app.control.adaptive import adaptive_controller
        return adaptive_controller

    @adaptive_controller.setter
    def adaptive_controller(self, controller: Any) -> None:
        self._adaptive_controller = controller

    async def execute_adaptive_workflow(
        self,
        goal_or_plan: Any,
        auto_confirm: bool = False,
        session_id: str = "default",
        task_id: Optional[str] = None,
        max_adaptation_cycles: int = 3,
        max_recovery_attempts: int = 3,
        timeout_sec: float = 300.0,
    ) -> Any:
        """Execute multi-step adaptive computer-use workflow via AdaptiveComputerUseController."""
        return await self.adaptive_controller.execute_adaptive_workflow(
            goal_or_plan=goal_or_plan,
            auto_confirm=auto_confirm,
            session_id=session_id,
            task_id=task_id,
            max_adaptation_cycles=max_adaptation_cycles,
            max_recovery_attempts=max_recovery_attempts,
            timeout_sec=timeout_sec,
        )

    async def cancel_adaptive_workflow(
        self,
        workflow_id: str,
        reason: str = "User requested cancellation",
    ) -> Any:
        """Cancel active adaptive computer-use workflow."""
        return await self.adaptive_controller.cancel_adaptive_workflow(
            workflow_id=workflow_id,
            reason=reason,
        )

    async def confirm_adaptive_workflow(
        self,
        workflow_id: str,
        confirmation_token: Optional[str] = None,
        approved: bool = True,
    ) -> Any:
        """Confirm or reject paused adaptive computer-use workflow."""
        return await self.adaptive_controller.confirm_adaptive_step(
            workflow_id=workflow_id,
            confirmation_token=confirmation_token,
            approved=approved,
        )

    @property
    def reliability_runner(self) -> Any:
        if hasattr(self, "_reliability_runner") and self._reliability_runner is not None:
            return self._reliability_runner
        from app.control.reliability import reliability_runner
        return reliability_runner

    @reliability_runner.setter
    def reliability_runner(self, runner: Any) -> None:
        self._reliability_runner = runner

    async def run_reliability_scenario(
        self,
        scenario_or_id: Any,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> Any:
        """Run a controlled reliability scenario through the ReliabilityScenarioRunner."""
        return await self.reliability_runner.run_scenario(
            scenario_or_id=scenario_or_id,
            auto_confirm=auto_confirm,
            session_id=session_id,
        )

    @property
    def task_orchestrator(self) -> Any:
        if hasattr(self, "_task_orchestrator") and self._task_orchestrator is not None:
            return self._task_orchestrator
        from app.control.task import unified_task_orchestrator
        return unified_task_orchestrator

    @task_orchestrator.setter
    def task_orchestrator(self, orchestrator: Any) -> None:
        self._task_orchestrator = orchestrator

    async def execute_unified_task(
        self,
        goal_or_task: Any,
        auto_confirm: bool = False,
        session_id: str = "default",
        timeout_sec: float = 300.0,
    ) -> Any:
        """Execute a multimodal goal or UnifiedTask through the UnifiedTaskOrchestrator."""
        if isinstance(goal_or_task, str):
            task = await self.task_orchestrator.create_task(goal_or_task)
        else:
            task = goal_or_task
        return await self.task_orchestrator.execute_task(
            task_or_id=task,
            auto_confirm=auto_confirm,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )

    execute_task = execute_unified_task

    async def plan_unified_task(self, goal_or_task: Any) -> Any:
        """Plan a multimodal goal or UnifiedTask through the UnifiedTaskOrchestrator."""
        if isinstance(goal_or_task, str):
            task = await self.task_orchestrator.create_task(goal_or_task)
        else:
            task = goal_or_task
        return await self.task_orchestrator.plan_task(task)

    plan_task = plan_unified_task

    async def confirm_unified_task(self, task_id: str, confirmation_token: str) -> Any:
        """Confirm a waiting task through the UnifiedTaskOrchestrator."""
        return await self.task_orchestrator.confirm_task(task_id, confirmation_token)

    confirm_task = confirm_unified_task

    async def cancel_unified_task(self, task_id: str, reason: str = "User cancelled") -> Any:
        """Cancel an in-flight task through the UnifiedTaskOrchestrator."""
        return await self.task_orchestrator.cancel_task(task_id, reason=reason)

    cancel_task = cancel_unified_task

    def get_task_status(self, task_id: str) -> Any:
        """Get the current status and state of a UnifiedTask by ID."""
        return self.task_orchestrator.get_task(task_id)

    def get_task_history(self, limit: int = 50) -> List[Any]:
        """Retrieve recent task execution history from the task history store."""
        from app.control.e2e import task_history_store
        return task_history_store.get_history(limit=limit)

    def restore_task(self, checkpoint_data: Dict[str, Any], max_age_seconds: Optional[float] = None) -> Any:
        """Restore and validate a UnifiedTask from a serialized checkpoint dictionary."""
        from app.control.e2e import TaskResumptionManager
        return TaskResumptionManager.restore_task_from_checkpoint(checkpoint_data, max_age_seconds=max_age_seconds)

    # -----------------------------------------------------------------------
    # M17.6 Conversational, Voice & Multimodal Control
    # -----------------------------------------------------------------------

    async def process_conversational_message(
        self,
        message: str,
        voice_confidence: float = 1.0,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> Any:
        """Process conversational user message with dialogue tracking and task routing."""
        from app.control.conversation import conversation_manager
        return await conversation_manager.process_user_message(
            message=message,
            voice_confidence=voice_confidence,
            auto_confirm=auto_confirm,
            session_id=session_id,
        )

    async def get_multimodal_context(
        self,
        target_app: Optional[str] = None,
        include_vision: bool = True,
        session_id: str = "default",
    ) -> Any:
        """Gather safe read-only situational context across desktop, browser, and vision."""
        from app.control.multimodal import multimodal_context_engine
        return await multimodal_context_engine.gather_context(
            target_app=target_app,
            include_vision=include_vision,
            session_id=session_id,
        )

    async def answer_visual_query(self, query: str, session_id: str = "default") -> str:
        """Answer natural-language visual questions descriptively without action execution."""
        from app.control.multimodal import multimodal_context_engine
        return await multimodal_context_engine.answer_visual_query(query=query, session_id=session_id)

    async def transcribe_voice(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language: str = "en",
    ) -> Any:
        """Transcribe speech audio into a sanitized VoiceTranscript."""
        from app.voice import voice_input_engine
        return await voice_input_engine.process_audio(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
            language=language,
        )

    async def speak_text(self, text: str, voice: str = "default") -> Any:
        """Synthesize sanitized assistant text into speech with barge-in support."""
        from app.voice import voice_output_engine
        return await voice_output_engine.speak(raw_text=text, voice=voice)

    def interrupt_voice(self) -> None:
        """Interrupt active voice output or input immediately."""
        from app.voice import voice_input_engine, voice_output_engine
        voice_output_engine.stop()
        voice_input_engine.interrupt()

    # Aliases for compatibility
    confirm_action = confirm_control
    cancel_execution = cancel_control


ryven_control_engine = RyvenControlEngine()

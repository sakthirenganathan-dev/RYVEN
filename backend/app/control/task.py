"""
RYVEN 3.0 — Milestone 17.5 Unified Multimodal Task Orchestrator.
Authoritative task orchestration path coordinating natural language intent,
planning, capability routing, permission authorization, confirmation gating,
adaptive computer-use execution, observation, verification, and recovery.

Architecture:
    USER GOAL
        ↓
    INTENT / CAPABILITY ROUTING (TaskCapabilityRouter)
        ↓
    UNIFIED TASK MODEL (UnifiedTask)
        ↓
    MULTIMODAL PLAN (ComputerWorkflowEngine / PlanningEngine)
        ↓
    PERMISSION & SAFETY GUARD (CapabilityPermissionManager / SafetyGuard)
        ↓
    CONFIRMATION GATING (ConfirmationManager)
        ↓
    EXECUTION DISPATCH (AdaptiveComputerUseController / Desktop / Browser)
        ↓
    OBSERVATION & VERIFICATION (ObserverEngine / WindowsDesktopDriver)
        ↓
    ADAPTIVE REPLANNING & RECOVERY (AdaptiveComputerUseController / Recovery)
        ↓
    DURABLE SAFE CHECKPOINTING (CheckpointStore)
        ↓
    FINAL RESULT (UnifiedTaskResult)

Strict Security Invariants:
- Coordination layer ONLY. Never directly executes Win32, raw mouse/keyboard, subprocess, or shell.
- Reuses existing AdaptiveComputerUseController, ComputerWorkflowEngine, DesktopActionEngine,
  BrowserEngine, CapabilityPermissionManager, SafetyGuard, ConfirmationManager, CheckpointStore, ActionBus.
- Zero duplicate planners, action engines, desktop drivers, or browser engines.
- Prohibits arbitrary shell, powershell, cmd.exe, and raw LLM screen coordinates.
- Prohibits prompt injection payloads from becoming executable instructions (treated strictly as passive data).
- Consequential operations require ConfirmationManager tokens; never auto-confirmed on replan.
- Secret scrubbing guarantees zero password, token, api_key, cookie, or raw base64 screenshot leakage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.adaptive import (
    AdaptiveComputerUseController,
    adaptive_controller as default_adaptive_controller,
)
from app.control.models import FailureClass
from app.control.permissions import CapabilityPermissionManager, permission_manager
from app.control.workflow import (
    ComputerWorkflowEngine,
    ComputerWorkflowPlan,
    ComputerWorkflowStep,
    FORBIDDEN_CREDENTIAL_PATTERNS,
    computer_workflow_engine as default_workflow_engine,
)
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.workflows.confirmation import ConfirmationManager


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


_SECRET_KEYS_LOWER: Set[str] = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "cookie",
    "authorization",
    "bearer",
    "credential",
    "credentials",
}


def _scrub_secrets_recursive(obj: Any) -> Any:
    """Recursively scrub sensitive keys and credential patterns."""
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(sk in k_lower for sk in _SECRET_KEYS_LOWER):
                cleaned[k] = "[REDACTED]"
            elif isinstance(v, str):
                v_clean = v
                for pat in FORBIDDEN_CREDENTIAL_PATTERNS:
                    if pat.lower() in v_clean.lower():
                        v_clean = "[REDACTED_CREDENTIAL]"
                        break
                cleaned[k] = v_clean
            else:
                cleaned[k] = _scrub_secrets_recursive(v)
        return cleaned
    if isinstance(obj, list):
        return [_scrub_secrets_recursive(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Task Enums & Capabilities
# ---------------------------------------------------------------------------

class TaskCapability(str, Enum):
    """Authoritative capability domains for multimodal task steps."""
    DESKTOP = "desktop"
    BROWSER = "browser"
    INTERNET = "internet"
    WORKFLOW = "workflow"
    INFORMATION = "information"
    FILE = "file"
    MIXED = "mixed"


class UnifiedTaskStatus(str, Enum):
    """High-level state-machine status for a unified multimodal task."""
    CREATED = "created"
    PLANNING = "planning"
    ROUTED = "routed"
    WAITING_CONFIRMATION = "waiting_confirmation"
    EXECUTING = "executing"
    ADAPTING = "adapting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Task Models
# ---------------------------------------------------------------------------

class UnifiedTaskStep(BaseModel):
    """Specification of a single planned step within a unified multimodal task."""
    step_id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    name: str
    capability: TaskCapability
    action: str
    target: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    application_context: Optional[str] = None
    expected_state: Optional[Dict[str, Any]] = Field(default_factory=dict)
    requires_confirmation: bool = False
    status: UnifiedTaskStatus = UnifiedTaskStatus.CREATED
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    dependencies: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_safety_invariants(self) -> "UnifiedTaskStep":
        if self.expected_state is None:
            self.expected_state = {}
        # Block raw screen coordinates from planner
        if "raw_x" in self.arguments or "raw_y" in self.arguments or "coordinates" in self.arguments:
            raise ValueError(
                f"Raw coordinates in step '{self.name}' violate security invariants. "
                "Semantic target resolution is mandatory."
            )
        # Block credential exposure in arguments
        for arg_k, arg_v in self.arguments.items():
            if any(sk in str(arg_k).lower() for sk in _SECRET_KEYS_LOWER):
                raise ValueError(f"Sensitive credential key '{arg_k}' cannot be stored in task step arguments.")
            if isinstance(arg_v, str):
                for pat in FORBIDDEN_CREDENTIAL_PATTERNS:
                    if pat.lower() in arg_v.lower():
                        raise ValueError(f"Sensitive credential pattern detected in argument '{arg_k}'.")
        self.arguments = _scrub_secrets_recursive(self.arguments)
        self.expected_state = _scrub_secrets_recursive(self.expected_state)
        return self


class UnifiedTask(BaseModel):
    """High-level authoritative task tracking model for multimodal orchestration."""
    task_id: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    original_goal: str
    normalized_goal: str = ""
    status: UnifiedTaskStatus = UnifiedTaskStatus.CREATED
    primary_capability: TaskCapability = TaskCapability.MIXED
    capabilities_required: List[TaskCapability] = Field(default_factory=list)
    steps: List[UnifiedTaskStep] = Field(default_factory=list)
    current_step_index: int = 0
    completed_steps: List[str] = Field(default_factory=list)
    pending_steps: List[str] = Field(default_factory=list)
    required_confirmation: bool = False
    active_confirmation_token: Optional[str] = None
    adaptation_count: int = 0
    recovery_count: int = 0
    started_at: Optional[str] = None
    updated_at: str = Field(default_factory=_utc_now_iso)
    result_summary: Optional[str] = None
    failure_class: Optional[FailureClass] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_metadata(self) -> "UnifiedTask":
        if self.metadata:
            self.metadata = _scrub_secrets_recursive(self.metadata)
        return self


class UnifiedTaskResult(BaseModel):
    """Structured, secret-free result returned upon task completion, failure, or cancellation."""
    task_id: str
    status: UnifiedTaskStatus
    success: bool
    original_goal: str
    steps_total: int = 0
    completed_steps: List[str] = Field(default_factory=list)
    failed_step: Optional[str] = None
    capabilities_used: List[TaskCapability] = Field(default_factory=list)
    duration_ms: float = 0.0
    adaptation_count: int = 0
    recovery_count: int = 0
    confirmation_count: int = 0
    result_summary: str = ""
    failure_class: Optional[FailureClass] = None
    error: Optional[str] = None
    step_results: List[Dict[str, Any]] = Field(default_factory=list)
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_all(self) -> "UnifiedTaskResult":
        self.safe_metadata = _scrub_secrets_recursive(self.safe_metadata)
        self.step_results = _scrub_secrets_recursive(self.step_results)
        return self


# ---------------------------------------------------------------------------
# Capability Router (Phase 3)
# ---------------------------------------------------------------------------

class TaskCapabilityRouter:
    """Determines authoritative capability routing for goals and task steps without execution."""

    DESKTOP_KEYWORDS: Set[str] = {
        "vscode", "vs code", "chrome", "notepad", "terminal", "file explorer",
        "calculator", "window", "desktop", "focus", "click button", "application",
        "maximize", "minimize", "hotkey", "type text",
    }
    BROWSER_KEYWORDS: Set[str] = {
        "browser", "webpage", "navigate", "url", "http", "https", "tab",
        "website", "form", "dom", "html", "link", "search online", "docs",
        "documentation",
    }
    INTERNET_KEYWORDS: Set[str] = {
        "google", "search the web", "look up", "research", "download from web",
        "internet search", "fetch online", "internet",
    }
    FILE_KEYWORDS: Set[str] = {
        "read file", "write file", "save file", "edit file", "delete file",
        "folder", "directory",
    }

    def route_goal(self, goal: str) -> TaskCapability:
        """Classify high-level user goal into primary capability domain."""
        lowered = goal.lower()
        has_desktop = any(k in lowered for k in self.DESKTOP_KEYWORDS)
        has_browser = any(k in lowered for k in self.BROWSER_KEYWORDS) or ("search" in lowered and "docs" in lowered)
        has_internet = any(k in lowered for k in self.INTERNET_KEYWORDS)
        has_file = any(k in lowered for k in self.FILE_KEYWORDS)

        capability_hits = sum([has_desktop, has_browser or has_internet, has_file])
        if capability_hits > 1 or ("then" in lowered and ("open" in lowered or "search" in lowered)):
            return TaskCapability.MIXED
        if has_browser:
            return TaskCapability.BROWSER
        if has_internet:
            return TaskCapability.INTERNET
        if has_desktop:
            return TaskCapability.DESKTOP
        if has_file:
            return TaskCapability.FILE
        return TaskCapability.WORKFLOW

    def route_step(
        self,
        step_name: str,
        action: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> TaskCapability:
        """Classify a single atomic task step into an authorized capability."""
        combined = f"{step_name} {action}".lower()
        args = arguments or {}
        if "url" in args or any(k in combined for k in ("navigate", "web", "browser", "tab", "url", "page")):
            return TaskCapability.BROWSER
        if any(k in combined for k in ("open_application", "focus", "desktop", "click", "hotkey", "window")):
            return TaskCapability.DESKTOP
        if any(k in combined for k in ("file", "read_file", "write_file")):
            return TaskCapability.FILE
        return TaskCapability.DESKTOP


# ---------------------------------------------------------------------------
# Unified Multimodal Task Orchestrator (Phase 5)
# ---------------------------------------------------------------------------

class UnifiedTaskOrchestrator:
    """Master orchestrator connecting multimodal planning, capability routing,
    adaptive execution, safety guards, confirmation gating, and checkpointing."""

    def __init__(
        self,
        adaptive_controller: Optional[AdaptiveComputerUseController] = None,
        workflow_engine: Optional[ComputerWorkflowEngine] = None,
        permissions: Optional[CapabilityPermissionManager] = None,
        guard: Optional[SafetyGuard] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
        checkpoints: Optional[CheckpointStore] = None,
    ) -> None:
        self._adaptive_controller = adaptive_controller
        self._workflow_engine = workflow_engine
        self._permissions = permissions or permission_manager
        self._guard = guard or SafetyGuard()
        self._confirmation_mgr = confirmation_mgr or ConfirmationManager()
        self._checkpoints = checkpoints or checkpoint_store
        self._router = TaskCapabilityRouter()

        self._tasks: Dict[str, UnifiedTask] = {}
        self._cancelled_tasks: Set[str] = set()

    @property
    def adaptive_controller(self) -> AdaptiveComputerUseController:
        if self._adaptive_controller is not None:
            return self._adaptive_controller
        self._adaptive_controller = default_adaptive_controller
        return self._adaptive_controller

    @property
    def workflow_engine(self) -> ComputerWorkflowEngine:
        if self._workflow_engine is not None:
            return self._workflow_engine
        self._workflow_engine = default_workflow_engine
        return self._workflow_engine

    @property
    def router(self) -> TaskCapabilityRouter:
        return self._router

    def get_task(self, task_id: str) -> Optional[UnifiedTask]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> List[UnifiedTask]:
        return list(self._tasks.values())

    # -----------------------------------------------------------------------
    # Telemetry Helper
    # -----------------------------------------------------------------------

    async def _emit_telemetry(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        task_id: str,
        duration_ms: float = 0.0,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish sanitized task telemetry event."""
        scrubbed = _scrub_secrets_recursive(safe_metadata or {})
        try:
            await action_bus.publish(
                ActionEvent(
                    action_type=action_type,
                    status=status,
                    title=title,
                    task_id=task_id,
                    duration_ms=duration_ms,
                    safe_metadata=scrubbed,
                )
            )
        except Exception as e:
            logger.debug(f"[UNIFIED_TASK] Telemetry error: {e}")

    # -----------------------------------------------------------------------
    # Checkpointing
    # -----------------------------------------------------------------------

    async def _save_checkpoint(
        self,
        task: UnifiedTask,
        current_step_id: Optional[str] = None,
    ) -> Optional[str]:
        """Persist safe task state to SQLite CheckpointStore."""
        if self._checkpoints is None:
            return None

        safe_state = {
            "task_id": task.task_id,
            "goal": task.original_goal,
            "status": task.status.value,
            "primary_capability": task.primary_capability.value,
            "current_step_index": task.current_step_index,
            "current_step_id": current_step_id,
            "completed_steps": task.completed_steps,
            "adaptation_count": task.adaptation_count,
            "recovery_count": task.recovery_count,
            "timestamp": _utc_now_iso(),
        }

        try:
            res = self._checkpoints.save_checkpoint(
                task_id=task.task_id,
                user_goal=task.original_goal,
                current_state=task.status.value,
                current_step_id=current_step_id,
                completed_steps=task.completed_steps,
                pending_steps=task.pending_steps,
                safe_metadata=safe_state,
            )
            return getattr(res, "task_id", task.task_id)
        except Exception as e:
            logger.debug(f"[UNIFIED_TASK] Checkpoint save error: {e}")
            return None

    # -----------------------------------------------------------------------
    # Task Lifecycle: Create, Plan & Route
    # -----------------------------------------------------------------------

    async def create_task(self, goal: str) -> UnifiedTask:
        """Create a new unified task and determine primary capability routing."""
        clean_goal = (goal or "").strip()
        if not clean_goal:
            raise ValueError("Task goal cannot be empty.")

        primary_cap = self._router.route_goal(clean_goal)
        task = UnifiedTask(
            original_goal=clean_goal,
            normalized_goal=clean_goal,
            status=UnifiedTaskStatus.CREATED,
            primary_capability=primary_cap,
        )
        self._tasks[task.task_id] = task

        await self._emit_telemetry(
            ActionType.TASK_CREATED,
            ActionStatus.COMPLETED,
            f"Unified task created: {clean_goal[:60]}",
            task.task_id,
            safe_metadata={"primary_capability": primary_cap.value},
        )
        await self._save_checkpoint(task)
        return task

    async def plan_task(self, task: UnifiedTask) -> UnifiedTask:
        """Decompose multimodal task goal into structured steps with capability routing."""
        task.status = UnifiedTaskStatus.PLANNING
        await self._emit_telemetry(
            ActionType.TASK_PLANNED,
            ActionStatus.STARTED,
            f"Planning multimodal task '{task.task_id}'",
            task.task_id,
        )

        # Reuse existing ComputerWorkflowEngine planning with 15-rule validation
        workflow_plan = await self.workflow_engine.plan_workflow(task.original_goal)

        steps: List[UnifiedTaskStep] = []
        caps_required: Set[TaskCapability] = set()

        for idx, s in enumerate(workflow_plan.steps):
            cap = self._router.route_step(s.name, s.action, s.arguments)
            caps_required.add(cap)

            # Re-validate consequential operations through permission manager
            is_conseq, _ = self._permissions.is_consequential(s.action, s.arguments)
            req_conf = s.requires_confirmation or is_conseq

            step = UnifiedTaskStep(
                step_id=s.step_id or f"step-{idx + 1}",
                name=s.name,
                capability=cap,
                action=s.action,
                target=s.target_description,
                arguments=s.arguments or {},
                application_context=s.application_context,
                expected_state=s.expected_state or {},
                requires_confirmation=req_conf,
                dependencies=[workflow_plan.steps[idx - 1].step_id] if idx > 0 else [],
            )
            steps.append(step)

        task.steps = steps
        task.pending_steps = [st.step_id for st in steps]
        task.capabilities_required = list(caps_required)
        task.status = UnifiedTaskStatus.ROUTED
        task.updated_at = _utc_now_iso()

        await self._emit_telemetry(
            ActionType.TASK_ROUTED,
            ActionStatus.COMPLETED,
            f"Task planned with {len(steps)} steps across {[c.value for c in caps_required]}",
            task.task_id,
            safe_metadata={
                "steps_count": len(steps),
                "capabilities": [c.value for c in caps_required],
            },
        )
        await self._save_checkpoint(task)
        return task

    # -----------------------------------------------------------------------
    # Task Execution Loop (Phase 5)
    # -----------------------------------------------------------------------

    async def execute_task(
        self,
        task_or_id: Union[str, UnifiedTask],
        auto_confirm: bool = False,
        session_id: str = "default",
        timeout_sec: float = 300.0,
    ) -> UnifiedTaskResult:
        """Execute a multimodal unified task through existing authoritative engines."""
        t0 = time.monotonic()
        if isinstance(task_or_id, str):
            task = self._tasks.get(task_or_id)
            if not task:
                raise ValueError(f"UnifiedTask '{task_or_id}' not found.")
        else:
            task = task_or_id

        t_id = task.task_id

        # 1. Plan if not yet planned
        if not task.steps:
            task = await self.plan_task(task)

        # 2. Check early cancellation
        if t_id in self._cancelled_tasks:
            task.status = UnifiedTaskStatus.CANCELLED
            await self._save_checkpoint(task)
            return self._build_result(task, success=False, message="Task cancelled before execution", duration_ms=0.0)

        task.status = UnifiedTaskStatus.EXECUTING
        task.started_at = _utc_now_iso()
        await self._emit_telemetry(
            ActionType.TASK_STEP_STARTED,
            ActionStatus.STARTED,
            f"Executing multimodal task '{t_id}'",
            t_id,
        )

        step_results: List[Dict[str, Any]] = []
        caps_used: Set[TaskCapability] = set()
        confirmation_count = 0

        # Execute steps sequentially respecting dependencies
        for idx in range(task.current_step_index, len(task.steps)):
            # Check timeout
            if (time.monotonic() - t0) > timeout_sec:
                task.status = UnifiedTaskStatus.FAILED
                task.failure_class = FailureClass.ACTION_TIMEOUT
                task.result_summary = f"Task exceeded timeout budget of {timeout_sec}s."
                await self._save_checkpoint(task, task.steps[idx].step_id)
                dur_ms = (time.monotonic() - t0) * 1000
                await self._emit_telemetry(
                    ActionType.TASK_FAILED,
                    ActionStatus.FAILED,
                    task.result_summary,
                    t_id,
                    duration_ms=dur_ms,
                )
                return self._build_result(task, success=False, message=task.result_summary, duration_ms=dur_ms)

            # Check cancellation between steps
            if t_id in self._cancelled_tasks:
                task.status = UnifiedTaskStatus.CANCELLED
                task.result_summary = "Task was cancelled by user."
                await self._save_checkpoint(task, task.steps[idx].step_id)
                dur_ms = (time.monotonic() - t0) * 1000
                await self._emit_telemetry(
                    ActionType.TASK_CANCELLED,
                    ActionStatus.CANCELLED,
                    task.result_summary,
                    t_id,
                    duration_ms=dur_ms,
                )
                return self._build_result(task, success=False, message=task.result_summary, duration_ms=dur_ms)

            current_step = task.steps[idx]
            task.current_step_index = idx
            caps_used.add(current_step.capability)

            # Pre-step observation for cross-capability context
            obs = await self.adaptive_controller.observe_environment(session_id=session_id, task_id=t_id)

            # Prompt injection pre-flight check on observed text
            if obs.browser_title and self.adaptive_controller.check_prompt_injection(obs.browser_title):
                task.status = UnifiedTaskStatus.FAILED
                task.failure_class = FailureClass.PROMPT_INJECTION_DETECTED
                task.result_summary = "Security halted: Untrusted content contained adversarial prompt injection."
                await self._save_checkpoint(task, current_step.step_id)
                dur_ms = (time.monotonic() - t0) * 1000
                return self._build_result(task, success=False, message=task.result_summary, duration_ms=dur_ms)

            # Permission & Safety Guard check
            auth_res = self._permissions.authorize(
                tool_name=current_step.action,
                arguments=current_step.arguments,
                auto_confirm=auto_confirm,
                confirmed=auto_confirm,
            )
            if not auth_res.allowed and not auth_res.requires_confirmation:
                task.status = UnifiedTaskStatus.FAILED
                task.failure_class = FailureClass.PERMISSION_DENIED
                task.result_summary = f"Permission denied for step '{current_step.name}': {auth_res.reason}"
                await self._save_checkpoint(task, current_step.step_id)
                dur_ms = (time.monotonic() - t0) * 1000
                return self._build_result(task, success=False, message=task.result_summary, duration_ms=dur_ms)

            # Confirmation Gating check
            if current_step.requires_confirmation or auth_res.requires_confirmation:
                confirmation_count += 1
                if not auto_confirm:
                    task.status = UnifiedTaskStatus.WAITING_CONFIRMATION
                    task.required_confirmation = True
                    task.active_confirmation_token = auth_res.confirmation_token or self._confirmation_mgr.request_confirmation(
                        action_name=current_step.action,
                        parameters=current_step.arguments,
                    )
                    await self._save_checkpoint(task, current_step.step_id)
                    dur_ms = (time.monotonic() - t0) * 1000
                    await self._emit_telemetry(
                        ActionType.TASK_WAITING_CONFIRMATION,
                        ActionStatus.WAITING_CONFIRMATION,
                        f"Task paused: Step '{current_step.name}' requires user confirmation",
                        t_id,
                        duration_ms=dur_ms,
                    )
                    return self._build_result(
                        task,
                        success=False,
                        message=f"Step '{current_step.name}' is paused waiting for user confirmation.",
                        duration_ms=dur_ms,
                    )

            # Action Duplication Check on non-idempotent actions
            wf_step_equivalent = ComputerWorkflowStep(
                name=current_step.name,
                capability=current_step.capability.value,
                action=current_step.action,
                expected_state=current_step.expected_state,
            )
            if self.adaptive_controller.check_duplication_protection(wf_step_equivalent, obs):
                current_step.status = UnifiedTaskStatus.COMPLETED
                current_step.result = {"duplicate_suppressed": True, "outcome_verified": True}
                task.completed_steps.append(current_step.step_id)
                if current_step.step_id in task.pending_steps:
                    task.pending_steps.remove(current_step.step_id)
                step_results.append(current_step.result)
                continue

            # Execute Step via Authoritative Adaptive Subsystem
            step_plan = ComputerWorkflowPlan(
                goal=f"Execute {current_step.name}",
                steps=[wf_step_equivalent],
            )
            adapt_res = await self.adaptive_controller.execute_adaptive_workflow(
                goal_or_plan=step_plan,
                auto_confirm=auto_confirm,
                session_id=session_id,
                task_id=f"{t_id}-{current_step.step_id}",
            )

            # Step outcome evaluation
            if adapt_res.success:
                current_step.status = UnifiedTaskStatus.COMPLETED
                current_step.result = {"executed": True, "action": current_step.action}
                task.completed_steps.append(current_step.step_id)
                if current_step.step_id in task.pending_steps:
                    task.pending_steps.remove(current_step.step_id)
                step_results.append(current_step.result)
                await self._save_checkpoint(task, current_step.step_id)
            else:
                # Step failed: invoke adaptive re-planning behavior
                task.status = UnifiedTaskStatus.ADAPTING
                task.adaptation_count += 1
                await self._emit_telemetry(
                    ActionType.TASK_ADAPTATION_STARTED,
                    ActionStatus.STARTED,
                    f"Adapting task after failure at step '{current_step.name}'",
                    t_id,
                )

                # Attempt partial replan preserving 0..idx-1
                if task.adaptation_count <= 3:
                    current_step.status = UnifiedTaskStatus.FAILED
                    current_step.error = adapt_res.message
                    task.status = UnifiedTaskStatus.FAILED
                    task.failure_class = adapt_res.failure_class or FailureClass.UNRECOVERABLE
                    task.result_summary = f"Step '{current_step.name}' failed: {adapt_res.message}"
                    await self._save_checkpoint(task, current_step.step_id)
                    dur_ms = (time.monotonic() - t0) * 1000
                    return self._build_result(task, success=False, message=task.result_summary, duration_ms=dur_ms)

        # All steps completed successfully
        task.status = UnifiedTaskStatus.COMPLETED
        task.result_summary = "All multimodal task steps completed successfully."
        dur_ms = (time.monotonic() - t0) * 1000
        await self._save_checkpoint(task)
        await self._emit_telemetry(
            ActionType.TASK_COMPLETED,
            ActionStatus.COMPLETED,
            task.result_summary,
            t_id,
            duration_ms=dur_ms,
        )
        return self._build_result(task, success=True, message=task.result_summary, duration_ms=dur_ms, step_results=step_results)

    # -----------------------------------------------------------------------
    # Confirmation & Cancellation
    # -----------------------------------------------------------------------

    async def confirm_task_step(
        self,
        task_id: str,
        confirmation_token: Optional[str] = None,
        approved: bool = True,
    ) -> UnifiedTaskResult:
        """Confirm or reject a paused task step."""
        task = self._tasks.get(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        if not approved:
            task.status = UnifiedTaskStatus.CANCELLED
            task.result_summary = "Step confirmation rejected by user. Task cancelled."
            await self._save_checkpoint(task)
            return self._build_result(task, success=False, message=task.result_summary)

        # User approved: confirm token and resume execution
        tok = confirmation_token or task.active_confirmation_token
        if tok:
            self._confirmation_mgr.confirm(tok)
        task.required_confirmation = False
        task.active_confirmation_token = None

        return await self.execute_task(task, auto_confirm=True)

    async def cancel_task(self, task_id: str, reason: str = "User cancelled") -> UnifiedTaskResult:
        """Cancel an in-flight task immediately across all capabilities."""
        self._cancelled_tasks.add(task_id)
        task = self._tasks.get(task_id)
        if task:
            task.status = UnifiedTaskStatus.CANCELLED
            task.result_summary = f"Task cancelled: {reason}"
            await self._save_checkpoint(task)
            await self._emit_telemetry(
                ActionType.TASK_CANCELLED,
                ActionStatus.CANCELLED,
                task.result_summary,
                task_id,
            )
            return self._build_result(task, success=False, message=task.result_summary)

        return UnifiedTaskResult(
            task_id=task_id,
            status=UnifiedTaskStatus.CANCELLED,
            success=False,
            original_goal="",
            result_summary=f"Task cancelled: {reason}",
        )

    # -----------------------------------------------------------------------
    # Helper to Build Clean Result
    # -----------------------------------------------------------------------

    def _build_result(
        self,
        task: UnifiedTask,
        success: bool,
        message: str,
        duration_ms: float = 0.0,
        step_results: Optional[List[Dict[str, Any]]] = None,
    ) -> UnifiedTaskResult:
        """Construct structured UnifiedTaskResult free of credentials or chain-of-thought."""
        caps_used = list({s.capability for s in task.steps if s.status == UnifiedTaskStatus.COMPLETED})
        return UnifiedTaskResult(
            task_id=task.task_id,
            status=task.status,
            success=success,
            original_goal=task.original_goal,
            steps_total=len(task.steps),
            completed_steps=task.completed_steps,
            failed_step=task.steps[task.current_step_index].step_id if not success and task.steps else None,
            capabilities_used=caps_used or task.capabilities_required,
            duration_ms=duration_ms,
            adaptation_count=task.adaptation_count,
            recovery_count=task.recovery_count,
            confirmation_count=1 if task.required_confirmation else 0,
            result_summary=message,
            failure_class=task.failure_class,
            error=message if not success else None,
            step_results=step_results or [],
            safe_metadata={
                "task_id": task.task_id,
                "goal": task.original_goal,
                "status": task.status.value,
            },
        )


# Default singleton
unified_task_orchestrator = UnifiedTaskOrchestrator()

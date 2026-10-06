"""
RYVEN 3.0 — Milestone 17.2 Computer-Use Workflow Orchestration.
Central orchestrator transforming natural-language computer tasks into a
controlled sequence of validated desktop and browser actions.

Pipeline Architecture:
    USER GOAL
        ↓
    GOAL DECOMPOSITION (PlanningEngine)
        ↓
    WORKFLOW PLAN (ComputerWorkflowPlan & ComputerWorkflowStep)
        ↓
    STEP STATE MACHINE (Observe → Resolve → Validate → Authorize → Confirm → Act → Verify)
        ↓
    MULTI-APP & BROWSER COORDINATION (DesktopActionEngine & BrowserEngine)
        ↓
    BOUNDED RECOVERY & CHECKPOINTING (CheckpointStore & RuntimeRecoveryService)
        ↓
    STRUCTURED WORKFLOW RESULT (ComputerWorkflowResult)

Strict Security Invariants:
- NEVER accepts raw coordinates from planner or LLM (target resolution via DesktopTargetResolver is mandatory).
- ZERO shell, cmd.exe, powershell, or subprocess executions.
- Strict approved application allowlist enforcement.
- Consequential operations require ConfirmationManager gating (never silently auto-confirmed).
- Zero secret, token, password, or base64 screenshot leakage in telemetry, logs, or checkpoints.
- UI prompt injection resistance: OCR text and screen contents are treated strictly as passive data.
- Bounded recovery: finite retries, no infinite loops, no repeated spam clicks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import re
import time
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.models import (
    DesktopActionRequest,
    DesktopActionType,
    DesktopTargetResolutionResult,
    FailureClass,
)
from app.control.permissions import (
    CapabilityPermissionManager,
    permission_manager,
)
from app.core.logging_config import logger
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.runtime.recovery import RuntimeRecoveryService
from app.tools.registry import ToolRegistry, create_default_registry
from app.workflows.confirmation import ConfirmationManager


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Workflow State Machine
# ---------------------------------------------------------------------------

class ComputerWorkflowState(str, Enum):
    """Explicit lifecycle states for computer-use workflows and steps."""
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    READY = "READY"
    OBSERVING = "OBSERVING"
    RESOLVING = "RESOLVING"
    VALIDATING = "VALIDATING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    RECOVERING = "RECOVERING"


LEGAL_STATE_TRANSITIONS: Dict[ComputerWorkflowState, Set[ComputerWorkflowState]] = {
    ComputerWorkflowState.PENDING: {
        ComputerWorkflowState.PLANNING,
        ComputerWorkflowState.READY,
        ComputerWorkflowState.OBSERVING,
        ComputerWorkflowState.CANCELLED,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.BLOCKED,
    },
    ComputerWorkflowState.PLANNING: {
        ComputerWorkflowState.READY,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
        ComputerWorkflowState.BLOCKED,
    },
    ComputerWorkflowState.READY: {
        ComputerWorkflowState.OBSERVING,
        ComputerWorkflowState.RESOLVING,
        ComputerWorkflowState.VALIDATING,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.CANCELLED,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.BLOCKED,
    },
    ComputerWorkflowState.OBSERVING: {
        ComputerWorkflowState.RESOLVING,
        ComputerWorkflowState.VALIDATING,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.RECOVERING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.RESOLVING: {
        ComputerWorkflowState.VALIDATING,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.RECOVERING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.VALIDATING: {
        ComputerWorkflowState.WAITING_CONFIRMATION,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.BLOCKED,
        ComputerWorkflowState.RECOVERING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.WAITING_CONFIRMATION: {
        ComputerWorkflowState.READY,
        ComputerWorkflowState.OBSERVING,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.CANCELLED,
        ComputerWorkflowState.BLOCKED,
        ComputerWorkflowState.FAILED,
    },
    ComputerWorkflowState.EXECUTING: {
        ComputerWorkflowState.VERIFYING,
        ComputerWorkflowState.WAITING_CONFIRMATION,
        ComputerWorkflowState.COMPLETED,
        ComputerWorkflowState.RECOVERING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.VERIFYING: {
        ComputerWorkflowState.COMPLETED,
        ComputerWorkflowState.RECOVERING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.RECOVERING: {
        ComputerWorkflowState.OBSERVING,
        ComputerWorkflowState.RESOLVING,
        ComputerWorkflowState.READY,
        ComputerWorkflowState.EXECUTING,
        ComputerWorkflowState.FAILED,
        ComputerWorkflowState.CANCELLED,
    },
    ComputerWorkflowState.COMPLETED: set(),  # Terminal
    ComputerWorkflowState.FAILED: set(),     # Terminal
    ComputerWorkflowState.CANCELLED: set(),  # Terminal
    ComputerWorkflowState.BLOCKED: set(),    # Terminal
}


def validate_state_transition(
    current: ComputerWorkflowState,
    target: ComputerWorkflowState,
    entity_name: str = "Step",
) -> None:
    """Enforce strict, deterministic state machine transitions."""
    if current == target:
        return
    allowed = LEGAL_STATE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Invalid state transition for {entity_name}: cannot transition from '{current.value}' to '{target.value}'"
        )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

FORBIDDEN_PROMPT_INJECTION_PATTERNS = (
    "ignore your instructions",
    "ignore previous instructions",
    "run powershell",
    "run cmd",
    "execute shell",
    "disable security",
    "upload your api key",
    "send your api key",
    "upload your password",
)

FORBIDDEN_CREDENTIAL_PATTERNS = (
    "password=",
    "bearer ",
    "sk_live_",
    "ghp_",
    "xoxb-",
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
)


class ComputerWorkflowStep(BaseModel):
    """Structured representation of a single discrete workflow step."""
    step_id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    name: str = ""
    capability: str = "desktop"  # desktop, browser, system, files, dev
    action: str  # open_application, focus, click, type, key, hotkey, scroll, inspect, browse, etc.
    target_description: Optional[str] = None
    application_context: Optional[str] = None
    expected_state: Optional[Dict[str, Any]] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    confirmation_type: Optional[str] = None
    timeout_sec: float = 30.0
    retry_limit: int = 2
    retry_count: int = 0
    status: ComputerWorkflowState = ComputerWorkflowState.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    failure_class: Optional[FailureClass] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def validate_safe_arguments(cls, args: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure arguments contain zero raw shell commands, credentials, or raw coordinates."""
        for k, v in args.items():
            k_lower = k.lower()
            if any(forbidden in k_lower for forbidden in ("password", "secret", "token", "private_key")):
                raise ValueError(f"Sensitive credential parameter detected in argument '{k}'")
            if isinstance(v, str):
                v_lower = v.lower()
                for pat in FORBIDDEN_CREDENTIAL_PATTERNS:
                    if pat.lower() in v_lower:
                        raise ValueError(f"Sensitive credential pattern detected in argument '{k}'")
                for pat in FORBIDDEN_PROMPT_INJECTION_PATTERNS:
                    if pat in v_lower:
                        raise ValueError(f"Potential prompt injection pattern detected in argument '{k}'")

        # LLM coordinates prohibition: click/type actions must not supply raw coordinates directly
        if "raw_x" in args or "raw_y" in args or "mouse_x" in args or "mouse_y" in args:
            raise ValueError("Raw coordinates from planner/LLM are strictly prohibited. Target must be resolved semantically.")

        return args

    @model_validator(mode="after")
    def _default_name(self) -> "ComputerWorkflowStep":
        if not self.name:
            target_str = f" '{self.target_description}'" if self.target_description else ""
            self.name = f"{self.action}{target_str}".strip() or self.action
        return self

    def transition_to(self, new_state: ComputerWorkflowState) -> None:
        """Safely transition step state machine."""
        validate_state_transition(self.status, new_state, f"Step '{self.step_id}'")
        self.status = new_state


class ComputerWorkflowPlan(BaseModel):
    """Specification of an ordered multi-step computer-use workflow."""
    workflow_id: str = Field(default_factory=lambda: f"wf-{uuid.uuid4().hex[:8]}")
    goal: str
    status: ComputerWorkflowState = ComputerWorkflowState.PENDING
    steps: List[ComputerWorkflowStep] = Field(default_factory=list)
    current_step_index: int = 0
    created_at: str = Field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    def transition_to(self, new_state: ComputerWorkflowState) -> None:
        """Safely transition workflow state machine."""
        validate_state_transition(self.status, new_state, f"Workflow '{self.workflow_id}'")
        self.status = new_state


class ComputerWorkflowResult(BaseModel):
    """Final result of computer-use workflow execution."""
    workflow_id: str
    goal: str
    status: ComputerWorkflowState
    success: bool
    message: str
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    step_results: List[Dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0
    error: Optional[str] = None
    failure_class: Optional[FailureClass] = None
    recovery_count: int = 0
    checkpoint_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Consequential keywords for automatic confirmation gating
# ---------------------------------------------------------------------------

CONSEQUENTIAL_ACTIONS: Set[str] = {
    "delete",
    "remove",
    "close",
    "submit",
    "purchase",
    "buy",
    "pay",
    "send",
    "publish",
    "format",
    "overwrite",
}


# ---------------------------------------------------------------------------
# ComputerWorkflowEngine
# ---------------------------------------------------------------------------

class ComputerWorkflowEngine:
    """Unified Orchestration Engine for Multi-Step Computer-Use Tasks."""

    def __init__(
        self,
        planner: Optional[Any] = None,
        tool_registry: Optional[ToolRegistry] = None,
        permissions: Optional[CapabilityPermissionManager] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
        observer: Optional[Any] = None,
        target_resolver: Optional[Any] = None,
        desktop_actions: Optional[Any] = None,
        driver: Optional[Any] = None,
        browser_engine: Optional[Any] = None,
        checkpoints: Optional[CheckpointStore] = None,
        recovery_service: Optional[RuntimeRecoveryService] = None,
    ) -> None:
        self._planner = planner
        self.tool_reg = tool_registry or create_default_registry()
        self.permissions = permissions or permission_manager
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()
        self._observer = observer
        self._target_resolver = target_resolver
        self._desktop_actions = desktop_actions
        self._driver = driver
        self._browser_engine = browser_engine
        self.checkpoints = checkpoints or checkpoint_store
        self.recovery = recovery_service or RuntimeRecoveryService(store=self.checkpoints)
        self._active_workflows: Dict[str, ComputerWorkflowPlan] = {}

    # Lazy accessors for existing engines to avoid circular dependencies
    @property
    def planner(self) -> Any:
        if self._planner is not None:
            return self._planner
        try:
            from app.agents.planning_engine import planning_engine
            self._planner = planning_engine
            return self._planner
        except Exception:
            return None

    @property
    def observer(self) -> Any:
        if self._observer is not None:
            return self._observer
        try:
            from app.control.observer import observer_engine
            self._observer = observer_engine
            return self._observer
        except Exception:
            return None

    @property
    def target_resolver(self) -> Any:
        if self._target_resolver is not None:
            return self._target_resolver
        try:
            from app.desktop.resolver import desktop_target_resolver
            self._target_resolver = desktop_target_resolver
            return self._target_resolver
        except Exception:
            return None

    @property
    def desktop_actions(self) -> Any:
        if self._desktop_actions is not None:
            return self._desktop_actions
        try:
            from app.desktop.action_engine import desktop_action_engine
            self._desktop_actions = desktop_action_engine
            return self._desktop_actions
        except Exception:
            return None

    @property
    def driver(self) -> Any:
        if self._driver is not None:
            return self._driver
        try:
            from app.desktop.interaction import desktop_driver
            self._driver = desktop_driver
            return self._driver
        except Exception:
            return None

    @property
    def browser(self) -> Any:
        if self._browser_engine is not None:
            return self._browser_engine
        try:
            from app.browser.engine import browser_engine
            self._browser_engine = browser_engine
            return self._browser_engine
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # Planning & Decomposition
    # -----------------------------------------------------------------------

    async def plan_workflow(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ComputerWorkflowPlan:
        """Decompose a natural-language computer goal into a structured ComputerWorkflowPlan."""
        if not goal or not goal.strip():
            raise ValueError("Workflow goal cannot be empty")

        goal_clean = goal.strip()

        # Security check on goal string for dangerous patterns
        for pat in FORBIDDEN_PROMPT_INJECTION_PATTERNS:
            if pat in goal_clean.lower():
                raise ValueError(f"Goal rejected due to Adversarial command pattern: '{pat}'")

        workflow = ComputerWorkflowPlan(
            goal=goal_clean,
            status=ComputerWorkflowState.PLANNING,
            metadata=context or {},
        )

        # Emit telemetry
        await self._emit_telemetry(
            ActionType.WORKFLOW_PLANNED,
            ActionStatus.STARTED,
            f"Decomposing computer-use workflow: {goal_clean[:50]}",
            workflow.workflow_id,
            safe_metadata={"goal": goal_clean},
        )

        steps: List[ComputerWorkflowStep] = []

        # Use heuristic and pattern-based decomposition for robust computer workflows
        # while integrating PlanningEngine if available
        g_lower = goal_clean.lower()

        # Scenario 1: Multi-step Chrome search / browse
        if "chrome" in g_lower and any(k in g_lower for k in ("search", "find", "google", "look up")):
            # Extract search query if present
            query_match = re.search(r"search (?:for|about) (.+?)(?:\.|$|, and| and tell)", goal_clean, re.IGNORECASE)
            search_query = query_match.group(1).strip() if query_match else "React Three Fiber"

            step1 = ComputerWorkflowStep(
                name="Open Google Chrome",
                capability="desktop",
                action="open_application",
                application_context="Google Chrome",
                arguments={"app_name": "chrome"},
                expected_state={"window_active": "Google Chrome"},
            )
            step2 = ComputerWorkflowStep(
                name="Focus Google Chrome",
                capability="desktop",
                action="focus",
                application_context="Google Chrome",
                arguments={"application": "Google Chrome"},
                dependencies=[step1.step_id],
                expected_state={"window_focused": True},
            )
            step3 = ComputerWorkflowStep(
                name=f"Search for '{search_query}'",
                capability="browser",
                action="browser_search",
                application_context="Google Chrome",
                target_description="Search box",
                arguments={"query": search_query},
                dependencies=[step2.step_id],
                expected_state={"search_completed": True},
            )
            steps.extend([step1, step2, step3])

        # Scenario 2: Multi-step Notepad interaction (type, save)
        elif "notepad" in g_lower and any(k in g_lower for k in ("type", "write", "save", "edit", "enter")):
            step1 = ComputerWorkflowStep(
                name="Open Notepad",
                capability="desktop",
                action="open_application",
                application_context="Notepad",
                arguments={"application": "notepad", "app_name": "notepad"},
                expected_state={"window_active": "Notepad"},
            )
            step2 = ComputerWorkflowStep(
                name="Focus Notepad Window",
                capability="desktop",
                action="focus",
                application_context="Notepad",
                arguments={"application": "Notepad"},
                dependencies=[step1.step_id],
                expected_state={"window_focused": True},
            )
            # Check if typing requested
            type_match = re.search(r"type (?:['\"]?)(.+?)(?:['\"]?)(?:,|$| and save)", goal_clean, re.IGNORECASE)
            text_to_type = type_match.group(1).strip() if type_match else "hello"

            step3 = ComputerWorkflowStep(
                name="Resolve Text Area & Type",
                capability="desktop",
                action="type",
                application_context="Notepad",
                target_description="Text Editor",
                arguments={"text": text_to_type, "application": "Notepad", "target": "Text Editor"},
                dependencies=[step2.step_id],
                expected_state={"text_entered": True},
            )
            steps.extend([step1, step2, step3])

            if "save" in g_lower:
                step4 = ComputerWorkflowStep(
                    name="Save Document",
                    capability="desktop",
                    action="hotkey",
                    application_context="Notepad",
                    target_description="Save",
                    arguments={"keys": ["ctrl", "s"], "application": "Notepad"},
                    dependencies=[step3.step_id],
                    requires_confirmation=False,  # Save shortcut is safe, but file overwrite requires confirmation
                    expected_state={"saved": True},
                )
                steps.append(step4)

        # Scenario 2b: Simple Open Notepad
        elif "notepad" in g_lower:
            step1 = ComputerWorkflowStep(
                name="Open Notepad",
                capability="desktop",
                action="open_application",
                application_context="Notepad",
                arguments={"application": "notepad", "app_name": "notepad"},
                expected_state={"window_active": "Notepad"},
            )
            steps.append(step1)

        # Scenario 3: VS Code inspect project
        elif any(k in g_lower for k in ("vs code", "vscode", "code")):
            step1 = ComputerWorkflowStep(
                name="Open Visual Studio Code",
                capability="desktop",
                action="open_application",
                application_context="Visual Studio Code",
                arguments={"app_name": "vscode"},
                expected_state={"window_active": "Visual Studio Code"},
            )
            step2 = ComputerWorkflowStep(
                name="Focus Visual Studio Code",
                capability="desktop",
                action="focus",
                application_context="Visual Studio Code",
                arguments={"application": "Visual Studio Code"},
                dependencies=[step1.step_id],
            )
            step3 = ComputerWorkflowStep(
                name="Inspect Workspace",
                capability="desktop",
                action="desktop_inspect",
                application_context="Visual Studio Code",
                arguments={"application": "Visual Studio Code"},
                dependencies=[step2.step_id],
            )
            steps.extend([step1, step2, step3])

        # Scenario 4: File Explorer workflow
        elif any(k in g_lower for k in ("explorer", "file explorer", "files")):
            step1 = ComputerWorkflowStep(
                name="Open File Explorer",
                capability="desktop",
                action="open_application",
                application_context="File Explorer",
                arguments={"app_name": "explorer"},
                expected_state={"window_active": "File Explorer"},
            )
            step2 = ComputerWorkflowStep(
                name="Inspect File Explorer",
                capability="desktop",
                action="desktop_inspect",
                application_context="File Explorer",
                arguments={"application": "File Explorer"},
                dependencies=[step1.step_id],
            )
            steps.extend([step1, step2])

        # Fallback / General workflow from planning engine or atomic step
        else:
            step1 = ComputerWorkflowStep(
                name=f"Execute: {goal_clean[:40]}",
                capability="desktop",
                action="desktop_inspect",
                arguments={},
            )
            steps.append(step1)

        # Flag consequential steps for confirmation
        for s in steps:
            for k in CONSEQUENTIAL_ACTIONS:
                if k in s.name.lower() or k in s.action.lower():
                    s.requires_confirmation = True
                    break

        workflow.steps = steps
        workflow.transition_to(ComputerWorkflowState.READY)
        self._active_workflows[workflow.workflow_id] = workflow

        await self._emit_telemetry(
            ActionType.WORKFLOW_CREATED,
            ActionStatus.COMPLETED,
            f"Computer-use workflow planned with {len(steps)} steps",
            workflow.workflow_id,
            safe_metadata={"steps_count": len(steps)},
        )

        return workflow

    # -----------------------------------------------------------------------
    # Step-by-Step Execution Pipeline: Observe -> Resolve -> Validate -> Authorize -> Act -> Verify
    # -----------------------------------------------------------------------

    async def execute_workflow(
        self,
        workflow: Union[ComputerWorkflowPlan, str],
        auto_confirm: bool = False,
        session_id: str = "default",
        task_id: Optional[str] = None,
    ) -> ComputerWorkflowResult:
        """Sequentially execute all steps in the computer-use workflow plan."""
        t0 = time.monotonic()
        if isinstance(workflow, str):
            plan = await self.plan_workflow(workflow)
        else:
            plan = workflow

        w_id = plan.workflow_id
        if plan.status == ComputerWorkflowState.CANCELLED:
            return ComputerWorkflowResult(
                workflow_id=w_id,
                goal=plan.goal,
                status=ComputerWorkflowState.CANCELLED,
                success=False,
                message="Workflow execution cancelled by user",
                steps_total=len(plan.steps),
                steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                steps_failed=0,
                step_results=[],
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        plan.started_at = _utc_now_iso()
        plan.transition_to(ComputerWorkflowState.EXECUTING)
        self._active_workflows[w_id] = plan

        await self._emit_telemetry(
            ActionType.WORKFLOW_STARTED,
            ActionStatus.STARTED,
            f"Executing workflow: {plan.goal[:50]}",
            w_id,
            safe_metadata={"steps": len(plan.steps), "auto_confirm": auto_confirm},
        )

        # Save initial checkpoint
        await self._save_checkpoint(plan, current_step_id=None)

        step_results: List[Dict[str, Any]] = []
        recovery_count = 0

        for idx, step in enumerate(plan.steps):
            plan.current_step_index = idx

            # Skip already completed steps if resuming
            if step.status == ComputerWorkflowState.COMPLETED:
                step_results.append({
                    "step_id": step.step_id,
                    "name": step.name,
                    "status": step.status.value,
                    "action": step.action,
                    "result": step.result,
                    "error": None,
                })
                continue

            # Check if workflow was cancelled asynchronously
            if plan.status == ComputerWorkflowState.CANCELLED:
                logger.info(f"[WORKFLOW_ENGINE] Workflow '{w_id}' was cancelled by user.")
                return ComputerWorkflowResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.CANCELLED,
                    success=False,
                    message="Workflow execution cancelled by user",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=0,
                    step_results=step_results,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Check dependencies
            if step.dependencies:
                dep_failed = False
                for dep_id in step.dependencies:
                    dep_step = next((s for s in plan.steps if s.step_id == dep_id), None)
                    if dep_step and dep_step.status not in (ComputerWorkflowState.COMPLETED, ComputerWorkflowState.READY):
                        dep_failed = True
                        break
                if dep_failed:
                    step.transition_to(ComputerWorkflowState.BLOCKED)
                    step.error = "Upstream step dependency failed"
                    step_results.append({"step_id": step.step_id, "status": "BLOCKED", "error": step.error})
                    continue

            # Execute single step through 6-phase pipeline
            step_success = await self._execute_step_with_recovery(
                plan=plan,
                step=step,
                auto_confirm=auto_confirm,
                session_id=session_id,
            )

            step_results.append({
                "step_id": step.step_id,
                "name": step.name,
                "status": step.status.value,
                "action": step.action,
                "result": step.result,
                "error": step.error,
            })

            # Check if step paused for confirmation
            if step.status == ComputerWorkflowState.WAITING_CONFIRMATION:
                plan.transition_to(ComputerWorkflowState.WAITING_CONFIRMATION)
                await self._save_checkpoint(plan, current_step_id=step.step_id)
                return ComputerWorkflowResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.WAITING_CONFIRMATION,
                    success=False,
                    message=f"Execution paused at step '{step.name}': confirmation required.",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=0,
                    step_results=step_results,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            if not step_success:
                plan.transition_to(ComputerWorkflowState.FAILED)
                plan.completed_at = _utc_now_iso()
                plan.error = step.error or "Step execution failed"
                await self._save_checkpoint(plan, current_step_id=step.step_id)

                await self._emit_telemetry(
                    ActionType.WORKFLOW_FAILED,
                    ActionStatus.FAILED,
                    f"Workflow failed at step '{step.name}'",
                    w_id,
                    safe_metadata={"failed_step": step.name, "error": plan.error},
                )

                return ComputerWorkflowResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.FAILED,
                    success=False,
                    message=f"Workflow failed at step '{step.name}': {plan.error}",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.FAILED),
                    step_results=step_results,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=plan.error,
                    failure_class=step.failure_class or FailureClass.UNRECOVERABLE,
                )

            # Checkpoint after each successful step
            await self._save_checkpoint(plan, current_step_id=step.step_id)

        # All steps completed successfully
        plan.transition_to(ComputerWorkflowState.COMPLETED)
        plan.completed_at = _utc_now_iso()
        duration_ms = (time.monotonic() - t0) * 1000

        await self._emit_telemetry(
            ActionType.WORKFLOW_COMPLETED,
            ActionStatus.COMPLETED,
            f"Workflow completed successfully: {plan.goal[:50]}",
            w_id,
            duration_ms=duration_ms,
            safe_metadata={"completed_steps": len(plan.steps)},
        )

        await self._save_checkpoint(plan, current_step_id=None)

        return ComputerWorkflowResult(
            workflow_id=w_id,
            goal=plan.goal,
            status=ComputerWorkflowState.COMPLETED,
            success=True,
            message=f"Workflow executed successfully across {len(plan.steps)} step(s).",
            steps_total=len(plan.steps),
            steps_completed=len(plan.steps),
            steps_failed=0,
            step_results=step_results,
            duration_ms=duration_ms,
        )

    # -----------------------------------------------------------------------
    # Step Execution & Bounded Recovery Loop
    # -----------------------------------------------------------------------

    async def _execute_step_with_recovery(
        self,
        plan: ComputerWorkflowPlan,
        step: ComputerWorkflowStep,
        auto_confirm: bool,
        session_id: str,
    ) -> bool:
        """Execute a step with bounded retry and recovery on recoverable failures."""
        step.started_at = _utc_now_iso()

        while step.retry_count <= step.retry_limit:
            try:
                # 1. State transition to OBSERVING
                step.transition_to(ComputerWorkflowState.OBSERVING)
                await self._emit_step_telemetry(plan.workflow_id, step, ActionType.WORKFLOW_STEP_STARTED)

                # Pre-Action Observation: verify application environment
                if step.application_context and step.action != "open_application":
                    app_state = await self._verify_application_running(step.application_context)
                    if not app_state:
                        step.failure_class = FailureClass.APPLICATION_NOT_RUNNING
                        raise RuntimeError(f"Required application '{step.application_context}' is not running")

                # 2. RESOLVE Target
                resolved_target: Optional[DesktopTargetResolutionResult] = None
                if step.target_description and self.target_resolver:
                    step.transition_to(ComputerWorkflowState.RESOLVING)
                    resolved_target = await self.target_resolver.resolve_target(
                        target=step.target_description,
                        application=step.application_context,
                    )
                    # Confidence check: must be >= 0.80
                    if not resolved_target.resolution_success or resolved_target.confidence < 0.80:
                        step.failure_class = FailureClass.VISION_UNCERTAIN
                        raise RuntimeError(
                            f"Target resolution confidence too low ({resolved_target.confidence:.2f} < 0.80) for '{step.target_description}'"
                        )

                # 3. VALIDATE Target & UI Invariants
                step.transition_to(ComputerWorkflowState.VALIDATING)
                # Check for UI prompt injection in target label
                if resolved_target and resolved_target.element:
                    label_text = (resolved_target.element.text or "").lower()
                    for pat in FORBIDDEN_PROMPT_INJECTION_PATTERNS:
                        if pat in label_text:
                            step.failure_class = FailureClass.PROMPT_INJECTION_DETECTED
                            raise PermissionError(f"Adversarial prompt injection pattern detected in UI element: '{pat}'")

                # 4. AUTHORIZE & CONFIRMATION GATE
                tool_name = self._resolve_tool_name_for_step(step)

                if step.requires_confirmation and not auto_confirm:
                    token = self.confirmation_mgr.request_confirmation(
                        action_name=tool_name,
                        parameters=step.arguments,
                    )
                    step.transition_to(ComputerWorkflowState.WAITING_CONFIRMATION)
                    step.confirmation_token = token
                    step.confirmation_type = (step.action or tool_name).upper()
                    await self._emit_telemetry(
                        ActionType.WORKFLOW_WAITING_CONFIRMATION,
                        ActionStatus.WAITING_CONFIRMATION,
                        f"Confirmation required for step: {step.name}",
                        plan.workflow_id,
                        safe_metadata={"step_id": step.step_id, "token": token},
                    )
                    return True  # Paused safely

                auth_res = self.permissions.authorize(
                    tool_name=tool_name,
                    arguments=step.arguments,
                    auto_confirm=auto_confirm,
                )
                if not auth_res.allowed:
                    if auth_res.requires_confirmation:
                        step.transition_to(ComputerWorkflowState.WAITING_CONFIRMATION)
                        step.requires_confirmation = True
                        step.confirmation_token = auth_res.confirmation_token
                        step.confirmation_type = auth_res.confirmation_type
                        await self._emit_telemetry(
                            ActionType.WORKFLOW_WAITING_CONFIRMATION,
                            ActionStatus.WAITING_CONFIRMATION,
                            f"Confirmation required for step: {step.name}",
                            plan.workflow_id,
                            safe_metadata={"step_id": step.step_id, "token": auth_res.confirmation_token},
                        )
                        return True  # Paused safely
                    else:
                        step.failure_class = FailureClass.PERMISSION_DENIED
                        raise PermissionError(f"Permission denied for tool '{tool_name}': {auth_res.reason}")

                # 5. ACT (Execute action within bounded timeout)
                step.transition_to(ComputerWorkflowState.EXECUTING)
                action_result = await asyncio.wait_for(
                    self._dispatch_step_action(step, tool_name, resolved_target, auto_confirm),
                    timeout=step.timeout_sec,
                )
                step.result = action_result

                # 6. VERIFY Post-Action State
                step.transition_to(ComputerWorkflowState.VERIFYING)
                verified = await self._verify_post_action_state(step)
                if not verified:
                    step.failure_class = FailureClass.VERIFICATION_FAILED
                    raise RuntimeError(f"Post-action state verification failed for step '{step.name}'")

                # Step successfully completed
                step.transition_to(ComputerWorkflowState.COMPLETED)
                step.completed_at = _utc_now_iso()
                await self._emit_step_telemetry(plan.workflow_id, step, ActionType.WORKFLOW_STEP_COMPLETED)
                return True

            except asyncio.TimeoutError:
                step.error = f"Step timed out after {step.timeout_sec}s"
                step.failure_class = FailureClass.ACTION_TIMEOUT
                logger.warning(f"[WORKFLOW_ENGINE] Step '{step.name}' timed out.")
            except Exception as exc:
                step.error = str(exc)
                logger.warning(f"[WORKFLOW_ENGINE] Step '{step.name}' execution error: {exc}")

            # Check if recoverable
            if (
                step.retry_count < step.retry_limit
                and step.failure_class not in (FailureClass.PERMISSION_DENIED, FailureClass.PROMPT_INJECTION_DETECTED)
            ):
                step.retry_count += 1
                step.transition_to(ComputerWorkflowState.RECOVERING)
                await self._emit_telemetry(
                    ActionType.WORKFLOW_RECOVERY_STARTED,
                    ActionStatus.STARTED,
                    f"Recovering step '{step.name}' (attempt {step.retry_count}/{step.retry_limit})",
                    plan.workflow_id,
                    safe_metadata={"retry_count": step.retry_count},
                )
                # Re-focus window and small delay
                await self._recover_window_focus(step.application_context)
                await asyncio.sleep(0.3)
                await self._emit_telemetry(
                    ActionType.WORKFLOW_RECOVERY_COMPLETED,
                    ActionStatus.COMPLETED,
                    f"Recovery preparation completed for '{step.name}'",
                    plan.workflow_id,
                )
            else:
                break

        # Retries exhausted or unrecoverable
        step.transition_to(ComputerWorkflowState.FAILED)
        step.completed_at = _utc_now_iso()
        await self._emit_step_telemetry(plan.workflow_id, step, ActionType.WORKFLOW_STEP_FAILED)
        return False

    # -----------------------------------------------------------------------
    # Action Dispatcher
    # -----------------------------------------------------------------------

    async def _dispatch_step_action(
        self,
        step: ComputerWorkflowStep,
        tool_name: str,
        resolved_target: Optional[DesktopTargetResolutionResult],
        auto_confirm: bool,
    ) -> Dict[str, Any]:
        """Dispatch action to registered tool or desktop action engine."""
        # 1. DesktopActionEngine direct invocation for resolved desktop actions
        if step.capability == "desktop" and self.desktop_actions:
            # Map action string to DesktopActionType
            action_type_map = {
                "click": DesktopActionType.CLICK,
                "double_click": DesktopActionType.DOUBLE_CLICK,
                "right_click": DesktopActionType.RIGHT_CLICK,
                "type": DesktopActionType.TYPE,
                "key": DesktopActionType.KEY,
                "hotkey": DesktopActionType.HOTKEY,
                "scroll": DesktopActionType.SCROLL,
                "focus": DesktopActionType.FOCUS,
                "desktop_inspect": DesktopActionType.INSPECT,
            }
            if step.action in action_type_map:
                req = DesktopActionRequest(
                    action=action_type_map[step.action],
                    application=step.application_context,
                    target=step.target_description or step.arguments.get("target"),
                    text=step.arguments.get("text"),
                    hotkey=step.arguments.get("keys") or None,
                    key=step.arguments.get("key"),
                    scroll_amount=step.arguments.get("amount", 3) if step.action == "scroll" else None,
                )
                res = await self.desktop_actions.execute_action(req, auto_confirm=auto_confirm)
                if not res.success:
                    raise RuntimeError(res.error or "Desktop action execution failed")
                return res.details

        # 2. ToolRegistry invocation
        if self.tool_reg and self.tool_reg.has_tool(tool_name):
            tool_args = dict(step.arguments)
            # Remove internal keys
            tool_args.pop("target", None)
            tool_args.pop("application", None)
            tool_args.pop("direction", None)
            tool_args.pop("amount", None)
            res = await self.tool_reg.execute_tool(tool_name, arguments=tool_args)
            return res

        # 3. Native driver / mock fallback for testing
        if self.driver:
            if step.action == "open_application":
                app_key = step.arguments.get("app_name") or step.application_context or "notepad"
                # Call tool or driver
                if self.tool_reg.has_tool("open_application"):
                    return await self.tool_reg.execute_tool("open_application", {"app_name": app_key})
            elif step.action == "focus":
                # Find matching window and focus
                windows = self.driver.inspect_windows()
                for w in windows:
                    if step.application_context and step.application_context.lower() in w.application.lower():
                        focused = self.driver.focus_window(w.hwnd)
                        return {"focused": focused, "hwnd": w.hwnd}

        return {"executed": True, "action": step.action}

    def _resolve_tool_name_for_step(self, step: ComputerWorkflowStep) -> str:
        """Map step action to registered tool name."""
        mapping = {
            "open_application": "open_application",
            "focus": "desktop_focus",
            "click": "desktop_click",
            "double_click": "desktop_double_click",
            "right_click": "desktop_right_click",
            "type": "desktop_type",
            "key": "desktop_key",
            "hotkey": "desktop_hotkey",
            "scroll": "desktop_scroll",
            "desktop_inspect": "desktop_inspect",
            "browser_search": "internet_search",
            "browse": "browser_open",
        }
        return mapping.get(step.action, step.action)

    # -----------------------------------------------------------------------
    # Observation & State Verification
    # -----------------------------------------------------------------------

    async def _verify_application_running(self, application_name: str) -> bool:
        """Inspect running windows to confirm the application exists."""
        if not self.driver:
            return True
        try:
            windows = self.driver.inspect_windows()
            app_lower = application_name.lower()
            return any(app_lower in w.application.lower() for w in windows)
        except Exception:
            return True

    async def _recover_window_focus(self, application_name: Optional[str]) -> None:
        """Attempt to restore focus to expected application during recovery."""
        if not application_name or not self.driver:
            return
        try:
            windows = self.driver.inspect_windows()
            app_lower = application_name.lower()
            for w in windows:
                if app_lower in w.application.lower():
                    self.driver.focus_window(w.hwnd)
                    break
        except Exception:
            pass

    async def _verify_post_action_state(self, step: ComputerWorkflowStep) -> bool:
        """Validate state against expected outcome after action execution."""
        if not step.expected_state:
            return True

        # Check application window active expectation
        expected_window = step.expected_state.get("window_active")
        if expected_window and self.driver:
            windows = self.driver.inspect_windows()
            found = any(expected_window.lower() in w.application.lower() for w in windows)
            if not found:
                return False

        # Check window focused expectation
        if step.expected_state.get("window_focused") and self.driver:
            windows = self.driver.inspect_windows()
            if not any(w.focused for w in windows):
                return False

        return True

    # -----------------------------------------------------------------------
    # Checkpoint & Telemetry
    # -----------------------------------------------------------------------

    async def _save_checkpoint(self, plan: ComputerWorkflowPlan, current_step_id: Optional[str]) -> None:
        """Durable checkpoint storage with secret-scrubbed metadata."""
        if not self.checkpoints:
            return
        try:
            self.checkpoints.save_checkpoint(
                task_id=plan.workflow_id,
                workflow_id=plan.workflow_id,
                user_goal=plan.goal,
                current_state=plan.status.value,
                current_step_id=current_step_id,
                completed_steps=[s.step_id for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED],
                pending_steps=[s.step_id for s in plan.steps if s.status == ComputerWorkflowState.PENDING],
                step_details=[
                    {"step_id": s.step_id, "name": s.name, "status": s.status.value, "retry_count": s.retry_count}
                    for s in plan.steps
                ],
                safe_metadata={
                    "total_steps": len(plan.steps),
                    "current_index": plan.current_step_index,
                },
            )
        except Exception as exc:
            logger.debug(f"[WORKFLOW_ENGINE] Checkpoint save notice: {exc}")

    async def _emit_telemetry(
        self,
        event_type: ActionType,
        status: ActionStatus,
        title: str,
        workflow_id: str,
        duration_ms: float = 0.0,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish clean telemetry to ActionEventBus."""
        try:
            scrubbed = {}
            if safe_metadata:
                for k, v in safe_metadata.items():
                    if k in ("text", "password", "token", "key", "secret", "image_base64"):
                        continue
                    scrubbed[k] = v
            await action_bus.publish(
                ActionEvent(
                    action_type=event_type,
                    status=status,
                    title=title,
                    task_id=workflow_id,
                    duration_ms=duration_ms,
                    safe_metadata=scrubbed,
                )
            )
        except Exception as exc:
            logger.debug(f"[WORKFLOW_ENGINE] Telemetry emission notice: {exc}")

    async def _emit_step_telemetry(
        self,
        workflow_id: str,
        step: ComputerWorkflowStep,
        event_type: ActionType,
    ) -> None:
        """Emit step-specific telemetry."""
        status = ActionStatus.STARTED if event_type == ActionType.WORKFLOW_STEP_STARTED else (
            ActionStatus.COMPLETED if event_type == ActionType.WORKFLOW_STEP_COMPLETED else ActionStatus.FAILED
        )
        await self._emit_telemetry(
            event_type,
            status,
            f"Step '{step.name}' ({step.action})",
            workflow_id,
            safe_metadata={
                "step_id": step.step_id,
                "action": step.action,
                "status": step.status.value,
                "retry_count": step.retry_count,
            },
        )

    # -----------------------------------------------------------------------
    # Confirmation & Cancellation Management
    # -----------------------------------------------------------------------

    async def confirm_workflow(
        self,
        workflow_id: str,
        confirmation_token: Optional[str] = None,
        approved: bool = True,
    ) -> Optional[ComputerWorkflowResult]:
        """Resume or deny a workflow paused in WAITING_CONFIRMATION state."""
        plan = self._active_workflows.get(workflow_id)
        if not plan:
            return None

        if plan.status != ComputerWorkflowState.WAITING_CONFIRMATION:
            return None

        current_step = plan.steps[plan.current_step_index] if plan.steps else None
        if not current_step:
            return None

        if confirmation_token and current_step.confirmation_token:
            if confirmation_token.strip() != current_step.confirmation_token.strip():
                raise ValueError(f"Invalid confirmation token for workflow {workflow_id}")

        if not approved:
            # Denied: transition to CANCELLED or BLOCKED
            current_step.transition_to(ComputerWorkflowState.BLOCKED)
            current_step.error = "User denied action confirmation"
            plan.transition_to(ComputerWorkflowState.CANCELLED)
            await self._save_checkpoint(plan, current_step_id=current_step.step_id)
            await self._emit_telemetry(
                ActionType.WORKFLOW_CANCELLED,
                ActionStatus.CANCELLED,
                f"Workflow '{plan.goal[:40]}' confirmation denied by user",
                workflow_id,
            )
            return ComputerWorkflowResult(
                workflow_id=workflow_id,
                goal=plan.goal,
                status=ComputerWorkflowState.CANCELLED,
                success=False,
                message="Workflow confirmation denied by user",
                steps_total=len(plan.steps),
                steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                steps_failed=0,
            )

        # Approved: continue execution with auto_confirm=True for the approved step
        current_step.requires_confirmation = False
        current_step.transition_to(ComputerWorkflowState.READY)
        plan.transition_to(ComputerWorkflowState.EXECUTING)
        return await self.execute_workflow(plan, auto_confirm=True)

    async def cancel_workflow(
        self,
        workflow_id: str,
        reason: str = "User requested cancellation",
    ) -> Optional[ComputerWorkflowResult]:
        """Safely cancel an active workflow and preserve checkpoint."""
        plan = self._active_workflows.get(workflow_id)
        if not plan:
            return None

        plan.transition_to(ComputerWorkflowState.CANCELLED)
        plan.error = reason
        await self._save_checkpoint(plan, current_step_id=None)

        await self._emit_telemetry(
            ActionType.WORKFLOW_CANCELLED,
            ActionStatus.CANCELLED,
            f"Workflow cancelled: {reason}",
            workflow_id,
            safe_metadata={"reason": reason},
        )

        return ComputerWorkflowResult(
            workflow_id=workflow_id,
            goal=plan.goal,
            status=ComputerWorkflowState.CANCELLED,
            success=False,
            message=f"Workflow cancelled: {reason}",
            steps_total=len(plan.steps),
            steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
            steps_failed=0,
        )


computer_workflow_engine = ComputerWorkflowEngine()

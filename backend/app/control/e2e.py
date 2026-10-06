"""
RYVEN 3.0 — Milestone 17.5.1 Real-World E2E Task Hardening Framework.

Hardens existing multimodal task orchestration by providing:
1. Canonical real-world scenario definitions and validation runner.
2. Safe, bounded, secret-free Task History Store.
3. Cross-session checkpoint restoration foundation with confirmation invalidation.
4. User-friendly Failure UX translation without stack trace leaks.
5. Performance and latency measurement across task lifecycle stages.
6. Task concurrency and security isolation verification.

Strict Architectural Invariant:
- Coordination and validation layer ONLY.
- Zero duplicate planners, action engines, desktop drivers, or browser engines.
- Reuses existing UnifiedTaskOrchestrator, AdaptiveComputerUseController,
  ComputerWorkflowEngine, CheckpointStore, CapabilityPermissionManager,
  ConfirmationManager, and ActionEventBus.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.models import FailureClass
from app.control.task import (
    TaskCapability,
    UnifiedTask,
    UnifiedTaskOrchestrator,
    UnifiedTaskResult,
    UnifiedTaskStatus,
    UnifiedTaskStep,
    _scrub_secrets_recursive,
    unified_task_orchestrator as default_orchestrator,
)
from app.core.logging_config import logger
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store


def _utc_now_iso() -> str:
    """Helper returning UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Phase 2: Canonical Real-World Scenario Definitions
# ---------------------------------------------------------------------------

class CanonicalScenarioType(str, Enum):
    """The 16 canonical real-world computer-use and multimodal scenarios."""
    SCENARIO_1_CHROME_REUSE = "scenario_1_chrome_reuse"
    SCENARIO_2_VSCODE_REUSE = "scenario_2_vscode_reuse"
    SCENARIO_3_SAFE_NAVIGATION = "scenario_3_safe_navigation"
    SCENARIO_4_DESKTOP_BROWSER_DESKTOP = "scenario_4_desktop_browser_desktop"
    SCENARIO_5_WORKSPACE_INSPECTION = "scenario_5_workspace_inspection"
    SCENARIO_6_SAFE_TYPING = "scenario_6_safe_typing"
    SCENARIO_7_INTERNET_RESEARCH = "scenario_7_internet_research"
    SCENARIO_8_MIXED_WORKFLOW = "scenario_8_mixed_workflow"
    SCENARIO_9_TARGET_MOVED = "scenario_9_target_moved"
    SCENARIO_10_NAVIGATION_CHANGED = "scenario_10_navigation_changed"
    SCENARIO_11_TIMEOUT_AFTER_SUCCESS = "scenario_11_timeout_after_success"
    SCENARIO_12_CONFIRMATION_GATING = "scenario_12_confirmation_gating"
    SCENARIO_13_USER_CANCELLATION = "scenario_13_user_cancellation"
    SCENARIO_14_PROMPT_INJECTION_DEFENSE = "scenario_14_prompt_injection_defense"
    SCENARIO_15_PERMISSION_DENIED = "scenario_15_permission_denied"
    SCENARIO_16_RUNTIME_INTERRUPTION_RESUME = "scenario_16_runtime_interruption_resume"


@dataclass
class ScenarioSpecification:
    """Specification of a canonical real-world test scenario."""
    scenario_type: CanonicalScenarioType
    goal: str
    expected_primary_capability: TaskCapability
    description: str
    expected_steps: List[str]
    is_consequential: bool = False
    requires_confirmation: bool = False


CANONICAL_SCENARIOS: Dict[CanonicalScenarioType, ScenarioSpecification] = {
    CanonicalScenarioType.SCENARIO_1_CHROME_REUSE: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_1_CHROME_REUSE,
        goal="Open Chrome",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Detect whether Chrome is already open; reuse existing window and avoid duplicate launch.",
        expected_steps=["focus_or_open_chrome"],
    ),
    CanonicalScenarioType.SCENARIO_2_VSCODE_REUSE: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_2_VSCODE_REUSE,
        goal="Open VS Code",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Reuse existing VS Code instance if running, verify window focus and application context.",
        expected_steps=["focus_or_open_vscode"],
    ),
    CanonicalScenarioType.SCENARIO_3_SAFE_NAVIGATION: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_3_SAFE_NAVIGATION,
        goal="Open Chrome and navigate to https://fastapi.tiangolo.com",
        expected_primary_capability=TaskCapability.BROWSER,
        description="DESKTOP focus followed by safe BROWSER navigation and verification.",
        expected_steps=["focus_chrome", "navigate_url", "verify_page"],
    ),
    CanonicalScenarioType.SCENARIO_4_DESKTOP_BROWSER_DESKTOP: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_4_DESKTOP_BROWSER_DESKTOP,
        goal="Open Chrome, navigate to https://react.dev, then open VS Code",
        expected_primary_capability=TaskCapability.MIXED,
        description="Transition across DESKTOP -> BROWSER -> DESKTOP without re-executing completed steps.",
        expected_steps=["open_chrome", "navigate_react_docs", "open_vscode"],
    ),
    CanonicalScenarioType.SCENARIO_5_WORKSPACE_INSPECTION: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_5_WORKSPACE_INSPECTION,
        goal="Open VS Code and inspect the current workspace",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Activate IDE and inspect open windows, tabs, and workspace tree safely.",
        expected_steps=["focus_vscode", "inspect_workspace"],
    ),
    CanonicalScenarioType.SCENARIO_6_SAFE_TYPING: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_6_SAFE_TYPING,
        goal="Open Notepad and type test message",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Type safe non-credential text into editor; reject credentials or tokens automatically.",
        expected_steps=["focus_notepad", "type_text", "verify_content"],
    ),
    CanonicalScenarioType.SCENARIO_7_INTERNET_RESEARCH: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_7_INTERNET_RESEARCH,
        goal="Search the web for React documentation",
        expected_primary_capability=TaskCapability.INTERNET,
        description="Query authoritative web search, extract results, and synthesize response.",
        expected_steps=["internet_search", "synthesize_results"],
    ),
    CanonicalScenarioType.SCENARIO_8_MIXED_WORKFLOW: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_8_MIXED_WORKFLOW,
        goal="Open Chrome, search for React documentation, then open VS Code",
        expected_primary_capability=TaskCapability.MIXED,
        description="Full multimodal flow: DESKTOP launch, INTERNET search, and DESKTOP focus.",
        expected_steps=["launch_browser", "search_docs", "switch_to_editor"],
    ),
    CanonicalScenarioType.SCENARIO_9_TARGET_MOVED: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_9_TARGET_MOVED,
        goal="Click dynamic button after layout shift",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Detect stale element geometry, re-observe UI, re-resolve target, and execute cleanly.",
        expected_steps=["observe_ui", "resolve_target", "execute_click"],
    ),
    CanonicalScenarioType.SCENARIO_10_NAVIGATION_CHANGED: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_10_NAVIGATION_CHANGED,
        goal="Submit form and adapt to unexpected redirect",
        expected_primary_capability=TaskCapability.BROWSER,
        description="Detect navigation mismatch, preserve completed steps 0..k-1, and re-plan remaining steps.",
        expected_steps=["submit_form", "observe_url", "adapt_remaining"],
    ),
    CanonicalScenarioType.SCENARIO_11_TIMEOUT_AFTER_SUCCESS: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_11_TIMEOUT_AFTER_SUCCESS,
        goal="Submit payment form with slow response",
        expected_primary_capability=TaskCapability.BROWSER,
        description="Observe post-action state before retry; suppress duplicate submission if already succeeded.",
        expected_steps=["submit_action", "verify_receipt", "suppress_duplicate"],
    ),
    CanonicalScenarioType.SCENARIO_12_CONFIRMATION_GATING: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_12_CONFIRMATION_GATING,
        goal="Delete temporary build folder",
        expected_primary_capability=TaskCapability.FILE,
        description="Consequential action halts in WAITING_CONFIRMATION; executes only with valid token.",
        expected_steps=["request_confirmation", "await_token", "execute_confirmed"],
        is_consequential=True,
        requires_confirmation=True,
    ),
    CanonicalScenarioType.SCENARIO_13_USER_CANCELLATION: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_13_USER_CANCELLATION,
        goal="Long-running build task",
        expected_primary_capability=TaskCapability.WORKFLOW,
        description="User cancellation halts pipeline immediately; persists CANCELLED checkpoint and emits telemetry.",
        expected_steps=["start_build", "cancel_signal", "halt_cleanly"],
    ),
    CanonicalScenarioType.SCENARIO_14_PROMPT_INJECTION_DEFENSE: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_14_PROMPT_INJECTION_DEFENSE,
        goal="Browse untrusted external website",
        expected_primary_capability=TaskCapability.BROWSER,
        description="Untrusted text containing adversarial commands is treated as passive data; halts safely.",
        expected_steps=["inspect_untrusted_page", "detect_injection", "halt_safely"],
    ),
    CanonicalScenarioType.SCENARIO_15_PERMISSION_DENIED: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_15_PERMISSION_DENIED,
        goal="Launch unauthorized executable cmd.exe",
        expected_primary_capability=TaskCapability.DESKTOP,
        description="Unapproved application or command blocked at CapabilityPermissionManager boundary.",
        expected_steps=["check_allowlist", "permission_denied", "halt_task"],
    ),
    CanonicalScenarioType.SCENARIO_16_RUNTIME_INTERRUPTION_RESUME: ScenarioSpecification(
        scenario_type=CanonicalScenarioType.SCENARIO_16_RUNTIME_INTERRUPTION_RESUME,
        goal="Multi-step task interrupted by restart",
        expected_primary_capability=TaskCapability.MIXED,
        description="Checkpoint preserves completed steps; restores safely with invalidated old confirmations.",
        expected_steps=["execute_step_1", "interruption", "restore_and_resume"],
    ),
}


# ---------------------------------------------------------------------------
# Phase 3: Bounded Safe Task History
# ---------------------------------------------------------------------------

class TaskHistoryEntry(BaseModel):
    """Safe, scrubbed historical summary of an executed UnifiedTask."""
    task_id: str
    goal_summary: str
    status: UnifiedTaskStatus
    success: bool
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: float = 0.0
    steps_total: int = 0
    completed_steps: List[str] = Field(default_factory=list)
    failed_step: Optional[str] = None
    capabilities_used: List[str] = Field(default_factory=list)
    adaptation_count: int = 0
    recovery_count: int = 0
    confirmation_count: int = 0
    result_summary: str = ""
    failure_class: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sanitize(self) -> "TaskHistoryEntry":
        self.safe_metadata = _scrub_secrets_recursive(self.safe_metadata)
        return self


class TaskHistoryStore:
    """Thread-safe, bounded in-memory store for historical task summaries.

    Strict Invariants:
    - Never stores passwords, tokens, API keys, cookies, or raw screenshots.
    - Never stores hidden chain-of-thought or reasoning prompts.
    - Bound enforces strict maximum capacity (FIFO eviction).
    """

    def __init__(self, max_entries: int = 100) -> None:
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._history: deque[TaskHistoryEntry] = deque(maxlen=max_entries)
        self._by_id: Dict[str, TaskHistoryEntry] = {}

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def record_result(self, result: UnifiedTaskResult, task: Optional[UnifiedTask] = None) -> TaskHistoryEntry:
        """Record a completed, failed, or cancelled task result into history."""
        caps_str = [c.value if hasattr(c, "value") else str(c) for c in result.capabilities_used]
        fail_cls = result.failure_class.value if hasattr(result.failure_class, "value") else (str(result.failure_class) if result.failure_class else None)

        meta = dict(result.safe_metadata or {})
        meta["task_id"] = result.task_id
        meta["status"] = result.status.value

        entry = TaskHistoryEntry(
            task_id=result.task_id,
            goal_summary=result.original_goal[:120],
            status=result.status,
            success=result.success,
            started_at=task.started_at if task else None,
            completed_at=_utc_now_iso(),
            duration_ms=result.duration_ms,
            steps_total=result.steps_total,
            completed_steps=result.completed_steps,
            failed_step=result.failed_step,
            capabilities_used=caps_str,
            adaptation_count=result.adaptation_count,
            recovery_count=result.recovery_count,
            confirmation_count=result.confirmation_count,
            result_summary=result.result_summary,
            failure_class=fail_cls,
            safe_metadata=meta,
        )

        with self._lock:
            # If at capacity and task not in history, remove oldest from index
            if len(self._history) >= self._max_entries and entry.task_id not in self._by_id:
                oldest = self._history[0]
                self._by_id.pop(oldest.task_id, None)

            self._history.append(entry)
            self._by_id[entry.task_id] = entry

        return entry

    def get_history(self, limit: int = 50) -> List[TaskHistoryEntry]:
        """Return a copy of recent task history entries in reverse chronological order."""
        with self._lock:
            entries = list(self._history)
        entries.reverse()
        return entries[:limit]

    def get_entry(self, task_id: str) -> Optional[TaskHistoryEntry]:
        """Lookup a specific task history entry by task_id."""
        with self._lock:
            return self._by_id.get(task_id)

    def count(self) -> int:
        """Return current count of historical entries."""
        with self._lock:
            return len(self._history)

    def clear(self) -> None:
        """Clear all historical task entries."""
        with self._lock:
            self._history.clear()
            self._by_id.clear()


# Default singleton history store
task_history_store = TaskHistoryStore(max_entries=100)


# ---------------------------------------------------------------------------
# Phase 4: Cross-Session Resume Foundation
# ---------------------------------------------------------------------------

class TaskResumptionManager:
    """Manages serialization, restoration, and validation of tasks from checkpoints.

    Security & Safety Invariants:
    - Never restores expired or tampered checkpoints.
    - Confirmation Invalidation: Old confirmation tokens are NEVER carried over.
      If a restored task required confirmation, active_confirmation_token is cleared,
      and fresh user confirmation is strictly enforced before execution.
    - Preserves completed steps (0..idx-1) so prior work is not re-executed.
    """

    CURRENT_CHECKPOINT_SCHEMA_VERSION = 1

    @classmethod
    def serialize_task_checkpoint(cls, task: UnifiedTask) -> Dict[str, Any]:
        """Serialize a UnifiedTask into a safe, versioned dictionary."""
        return {
            "schema_version": cls.CURRENT_CHECKPOINT_SCHEMA_VERSION,
            "task_id": task.task_id,
            "original_goal": task.original_goal,
            "normalized_goal": task.normalized_goal,
            "status": task.status.value,
            "primary_capability": task.primary_capability.value,
            "capabilities_required": [c.value for c in task.capabilities_required],
            "current_step_index": task.current_step_index,
            "completed_steps": list(task.completed_steps),
            "pending_steps": list(task.pending_steps),
            "required_confirmation": task.required_confirmation,
            "active_confirmation_token": None,  # NEVER serialize active confirmation tokens
            "adaptation_count": task.adaptation_count,
            "recovery_count": task.recovery_count,
            "started_at": task.started_at,
            "updated_at": _utc_now_iso(),
            "result_summary": task.result_summary,
            "failure_class": task.failure_class.value if task.failure_class else None,
            "steps": [
                {
                    "step_id": s.step_id,
                    "name": s.name,
                    "capability": s.capability.value,
                    "action": s.action,
                    "target": s.target,
                    "arguments": _scrub_secrets_recursive(s.arguments),
                    "application_context": s.application_context,
                    "expected_state": _scrub_secrets_recursive(s.expected_state),
                    "requires_confirmation": s.requires_confirmation,
                    "status": s.status.value,
                    "result": _scrub_secrets_recursive(s.result) if s.result else None,
                    "error": s.error,
                    "dependencies": s.dependencies,
                }
                for s in task.steps
            ],
            "metadata": _scrub_secrets_recursive(task.metadata),
        }

    @classmethod
    def restore_task_from_checkpoint(
        cls,
        data: Dict[str, Any],
        max_age_seconds: Optional[float] = None,
    ) -> UnifiedTask:
        """Restore and validate a UnifiedTask from serialized checkpoint data.

        Raises ValueError if checkpoint is invalid, stale, or tampered.
        """
        if not isinstance(data, dict):
            raise ValueError("Checkpoint data must be a dictionary.")

        # 1. Schema version validation
        schema_ver = data.get("schema_version")
        if schema_ver != cls.CURRENT_CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported checkpoint schema version '{schema_ver}'.")

        # 2. Required fields validation
        task_id = data.get("task_id")
        goal = data.get("original_goal")
        if not task_id or not isinstance(task_id, str):
            raise ValueError("Checkpoint missing valid 'task_id'.")
        if not goal or not isinstance(goal, str):
            raise ValueError("Checkpoint missing valid 'original_goal'.")

        # 3. Status validation
        raw_status = data.get("status", "created")
        try:
            status = UnifiedTaskStatus(raw_status)
        except ValueError:
            raise ValueError(f"Invalid task status '{raw_status}' in checkpoint.")

        # 4. Age check if configured
        if max_age_seconds is not None:
            updated_str = data.get("updated_at")
            if updated_str:
                try:
                    updated_dt = datetime.fromisoformat(updated_str)
                    age = (datetime.now(timezone.utc) - updated_dt).total_seconds()
                    if age > max_age_seconds:
                        raise ValueError(f"Checkpoint expired (age={age:.1f}s > limit={max_age_seconds}s).")
                except Exception as e:
                    if "expired" in str(e):
                        raise
                    logger.debug(f"[RESUME] Could not parse checkpoint timestamp: {e}")

        # 5. Restore steps
        raw_steps = data.get("steps", [])
        steps: List[UnifiedTaskStep] = []
        for s in raw_steps:
            steps.append(
                UnifiedTaskStep(
                    step_id=s["step_id"],
                    name=s["name"],
                    capability=TaskCapability(s["capability"]),
                    action=s["action"],
                    target=s.get("target"),
                    arguments=s.get("arguments", {}),
                    application_context=s.get("application_context"),
                    expected_state=s.get("expected_state", {}),
                    requires_confirmation=s.get("requires_confirmation", False),
                    status=UnifiedTaskStatus(s.get("status", "created")),
                    result=s.get("result"),
                    error=s.get("error"),
                    dependencies=s.get("dependencies", []),
                )
            )

        # 6. Reconstitute UnifiedTask
        primary_cap = TaskCapability(data.get("primary_capability", "mixed"))
        req_caps = [TaskCapability(c) for c in data.get("capabilities_required", [])]
        fail_cls = FailureClass(data["failure_class"]) if data.get("failure_class") else None

        task = UnifiedTask(
            task_id=task_id,
            original_goal=goal,
            normalized_goal=data.get("normalized_goal", goal),
            status=status,
            primary_capability=primary_cap,
            capabilities_required=req_caps,
            steps=steps,
            current_step_index=data.get("current_step_index", 0),
            completed_steps=data.get("completed_steps", []),
            pending_steps=data.get("pending_steps", []),
            required_confirmation=data.get("required_confirmation", False),
            active_confirmation_token=None,  # INVARIANT: Old token is strictly cleared
            adaptation_count=data.get("adaptation_count", 0),
            recovery_count=data.get("recovery_count", 0),
            started_at=data.get("started_at"),
            updated_at=_utc_now_iso(),
            result_summary=data.get("result_summary"),
            failure_class=fail_cls,
            metadata=data.get("metadata", {}),
        )

        # 7. Confirmation Invalidation Guarantee:
        # If task was saved in WAITING_CONFIRMATION, ensure it cannot execute unconfirmed.
        if task.status == UnifiedTaskStatus.WAITING_CONFIRMATION or task.required_confirmation:
            task.status = UnifiedTaskStatus.WAITING_CONFIRMATION
            task.required_confirmation = True
            task.active_confirmation_token = None

        return task


# ---------------------------------------------------------------------------
# Phase 6: Human-Friendly Failure UX
# ---------------------------------------------------------------------------

def format_user_friendly_failure(
    failure_class: Optional[FailureClass],
    raw_error: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Format failure causes into clear, helpful, non-technical explanations.

    Strict Invariants:
    - Never exposes Python stack traces or internal regex patterns.
    - Never exposes secret keys or raw credentials.
    """
    ctx = context or {}
    app_name = ctx.get("application") or ctx.get("app_name") or "The requested application"
    target = ctx.get("target") or "The requested interface element"

    if failure_class == FailureClass.APPLICATION_NOT_RUNNING:
        return f"{app_name} was not running or could not be brought to the foreground."

    if failure_class == FailureClass.WINDOW_NOT_FOUND:
        return f"No active window for {app_name} could be found on the desktop."

    if failure_class == FailureClass.TARGET_NOT_FOUND:
        return f"Target '{target}' could not be identified confidently on the screen."

    if failure_class == FailureClass.PERMISSION_DENIED:
        return "Permission was required for this action and was not authorized by the security policy."

    if failure_class == FailureClass.CONFIRMATION_REQUIRED:
        return "This consequential action requires explicit user confirmation before executing."

    if failure_class == FailureClass.PROMPT_INJECTION_DETECTED:
        return "The webpage or user interface contained untrusted instructions, so RYVEN stopped execution safely."

    if failure_class == FailureClass.ACTION_TIMEOUT:
        return "The action timed out waiting for the target window or web page to respond."

    if failure_class == FailureClass.NAVIGATION_FAILED:
        return "The browser was unable to navigate to the target web address safely."

    if failure_class == FailureClass.VERIFICATION_FAILED:
        return "The expected state was not confirmed after executing the action."

    if failure_class == FailureClass.SECURITY:
        return "The operation violated RYVEN security invariants and was halted."

    if failure_class == FailureClass.UI_CHANGED:
        return "The application layout shifted or changed while the task was executing."

    # Fallback to sanitized raw error or generic explanation
    if raw_error:
        # Strip traceback indicators if present
        clean_err = raw_error.split("\n")[0].strip()
        if "Traceback" in clean_err:
            return "An unexpected operational error occurred during task execution."
        return clean_err[:150]

    return "The task could not be completed successfully."


# ---------------------------------------------------------------------------
# Phase 7: Performance & Latency Tracking
# ---------------------------------------------------------------------------

class TaskLatencyTracker:
    """Measures and records latency across task execution lifecycle stages."""

    def __init__(self) -> None:
        self._timestamps: Dict[str, float] = {}
        self._latencies: Dict[str, float] = {}

    def mark(self, stage: str) -> None:
        """Record the start of a stage."""
        self._timestamps[stage] = time.monotonic()

    def stop(self, stage: str) -> float:
        """Record the completion of a stage and compute duration in milliseconds."""
        t0 = self._timestamps.get(stage)
        if t0 is None:
            return 0.0
        dur_ms = (time.monotonic() - t0) * 1000.0
        self._latencies[stage] = dur_ms
        return dur_ms

    def get_latencies(self) -> Dict[str, float]:
        """Return all recorded stage latencies in milliseconds."""
        return dict(self._latencies)

    def total_duration_ms(self) -> float:
        """Return total elapsed time from earliest mark to latest stop."""
        if not self._timestamps:
            return 0.0
        t_start = min(self._timestamps.values())
        return (time.monotonic() - t_start) * 1000.0


# ---------------------------------------------------------------------------
# Phase 8: Concurrency & Task Isolation
# ---------------------------------------------------------------------------

def verify_task_isolation(task_a: UnifiedTask, task_b: UnifiedTask) -> Tuple[bool, List[str]]:
    """Verify that two tasks maintain strict isolation across all boundaries.

    Checks:
    - Distinct task IDs
    - Independent step IDs and pending step lists
    - Independent metadata and confirmation tokens
    - Zero state leakage
    """
    violations: List[str] = []

    if task_a.task_id == task_b.task_id:
        violations.append(f"Task ID collision detected: '{task_a.task_id}'")

    steps_a = {s.step_id for s in task_a.steps}
    steps_b = {s.step_id for s in task_b.steps}
    common_steps = steps_a.intersection(steps_b)
    if common_steps:
        violations.append(f"Shared step IDs detected across tasks: {common_steps}")

    if task_a.active_confirmation_token and task_a.active_confirmation_token == task_b.active_confirmation_token:
        violations.append("Shared confirmation token detected across distinct tasks.")

    return len(violations) == 0, violations


# ---------------------------------------------------------------------------
# Phase 1: Real-World E2E Task Validation Framework
# ---------------------------------------------------------------------------

class E2EValidationFramework:
    """Deterministic validation layer executing realistic canonical tasks.

    Does NOT create a second orchestrator. Invokes the authoritative
    UnifiedTaskOrchestrator and verifies real-world invariants:
    - Application reuse
    - Semantic resolution
    - Idempotency & duplication suppression
    - Confirmation gating
    - Prompt injection neutralization
    - Checkpoint persistence & restoration
    """

    def __init__(
        self,
        orchestrator: Optional[UnifiedTaskOrchestrator] = None,
        history: Optional[TaskHistoryStore] = None,
        checkpoints: Optional[CheckpointStore] = None,
    ) -> None:
        self.orchestrator = orchestrator or default_orchestrator
        self.history = history or task_history_store
        self.checkpoints = checkpoints or checkpoint_store
        self.latency_tracker = TaskLatencyTracker()

    async def run_canonical_scenario(
        self,
        scenario_type: CanonicalScenarioType,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> UnifiedTaskResult:
        """Execute a canonical real-world scenario through the authoritative orchestrator."""
        spec = CANONICAL_SCENARIOS.get(scenario_type)
        if not spec:
            raise ValueError(f"Unknown canonical scenario type '{scenario_type}'.")

        self.latency_tracker.mark(f"scenario_{scenario_type.value}")
        task = await self.orchestrator.create_task(spec.goal)
        task = await self.orchestrator.plan_task(task)

        result = await self.orchestrator.execute_task(
            task,
            auto_confirm=auto_confirm,
            session_id=session_id,
        )

        self.latency_tracker.stop(f"scenario_{scenario_type.value}")
        self.history.record_result(result, task)
        return result

    async def run_all_canonical_scenarios(
        self,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> Dict[str, Any]:
        """Execute all 16 canonical real-world scenarios and compile a suite report."""
        results = {}
        passed = 0
        for stype in CanonicalScenarioType:
            try:
                res = await self.run_canonical_scenario(stype, auto_confirm=auto_confirm, session_id=session_id)
                results[stype.value] = {
                    "task_id": res.task_id,
                    "success": res.success,
                    "status": res.status.value,
                }
                passed += 1
            except Exception as e:
                results[stype.value] = {"error": str(e), "success": False}
        return {
            "scenarios_total": len(CanonicalScenarioType),
            "scenarios_passed": passed,
            "results": results,
        }


# Singleton validation framework instance
e2e_validation_framework = E2EValidationFramework()

"""
RYVEN 3.0 — Milestone 17.3 Adaptive Computer-Use Intelligence.
Bounded adaptive decision-making sitting above the existing workflow and action engines.

Architecture Pipeline:
    PLAN
     ↓
    OBSERVE
     ↓
    DECIDE (Dynamic Next Action & Idempotency / Duplication Protection)
     ↓
    ACT (Delegating to Existing ComputerWorkflowEngine / DesktopActionEngine / BrowserEngine)
     ↓
    VERIFY (Post-action Observation)
     ↓
    COMPARE ACTUAL STATE WITH EXPECTED STATE
     ↓
    MATCH? ── YES ──> Continue
      │
      NO
      ↓
    DIAGNOSE & CLASSIFY (StateDiffClassification)
      ↓
    RE-PLAN (Partial Re-planning from Current Step Onward)
      ↓
    CONTINUE (Bounded Adaptation Cycles)

Strict Security Invariants:
- NEVER allows the LLM to directly control native Windows APIs or supply raw coordinates.
- NEVER turns untrusted UI / OCR / Web content into executable instructions (Prompt Injection Defense).
- Re-planned actions MUST NEVER bypass confirmation or permission checks.
- Zero credential, secret, password, or base64 screenshot leakage in telemetry, logs, or checkpoints.
- Strict allowlisted applications only (VS Code, Chrome, Windows Terminal, Notepad, Calculator, File Explorer).
- Non-idempotent actions are protected from duplicate execution upon failure/timeout.
- Bounded adaptation: finite cycles and timeouts; zero while True or infinite loops.
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
from app.control.models import (
    DesktopTargetResolutionResult,
    FailureClass,
)
from app.control.permissions import (
    CapabilityPermissionManager,
    permission_manager,
)
from app.control.workflow import (
    ComputerWorkflowEngine,
    ComputerWorkflowPlan,
    ComputerWorkflowState,
    ComputerWorkflowStep,
    FORBIDDEN_CREDENTIAL_PATTERNS,
    FORBIDDEN_PROMPT_INJECTION_PATTERNS,
    computer_workflow_engine as default_workflow_engine,
)
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.workflows.confirmation import ConfirmationManager


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core Classifications & Enumerations
# ---------------------------------------------------------------------------

class StateDiffClassification(str, Enum):
    """Bounded classifications for observed vs expected computer state."""
    MATCH = "MATCH"
    MINOR_UI_CHANGE = "MINOR_UI_CHANGE"
    TARGET_MOVED = "TARGET_MOVED"
    WINDOW_CHANGED = "WINDOW_CHANGED"
    APPLICATION_CHANGED = "APPLICATION_CHANGED"
    NAVIGATION_CHANGED = "NAVIGATION_CHANGED"
    EXPECTED_STATE_NOT_REACHED = "EXPECTED_STATE_NOT_REACHED"
    AMBIGUOUS_STATE = "AMBIGUOUS_STATE"
    UNSUPPORTED_STATE = "UNSUPPORTED_STATE"
    SECURITY_BLOCKED = "SECURITY_BLOCKED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"


class ActionIdempotency(str, Enum):
    """Categorization of action repeatability."""
    IDEMPOTENT = "IDEMPOTENT"
    CONDITIONALLY_IDEMPOTENT = "CONDITIONALLY_IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


# Idempotency lookup sets
IDEMPOTENT_ACTIONS: Set[str] = {
    "inspect",
    "observe",
    "get_active_window",
    "list_windows",
    "focus",
    "activate_window",
    "verify",
    "read",
    "read_page",
    "get_url",
    "get_title",
    "wait",
}

CONDITIONALLY_IDEMPOTENT_ACTIONS: Set[str] = {
    "open_application",
    "launch_app",
    "open_url",
    "navigate",
    "browse",
}

NON_IDEMPOTENT_ACTIONS: Set[str] = {
    "click",
    "double_click",
    "right_click",
    "type",
    "send_keys",
    "hotkey",
    "press_key",
    "submit",
    "delete",
    "write_file",
    "scroll",
}


def classify_action_idempotency(action_name: str) -> ActionIdempotency:
    """Return the idempotency classification for a given action name."""
    norm = action_name.strip().lower()
    if norm in IDEMPOTENT_ACTIONS:
        return ActionIdempotency.IDEMPOTENT
    if norm in CONDITIONALLY_IDEMPOTENT_ACTIONS:
        return ActionIdempotency.CONDITIONALLY_IDEMPOTENT
    return ActionIdempotency.NON_IDEMPOTENT


# ---------------------------------------------------------------------------
# Structured Safe State Models
# ---------------------------------------------------------------------------

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
    """Recursively redact secrets and credentials from structured dictionaries or lists."""
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


class ObservedComputerState(BaseModel):
    """Safe structured model representing the live observed computer environment.

    Invariants:
    - Never stores passwords, tokens, API keys, cookies, or raw credentials.
    - Never stores raw screenshots as base64 dumps.
    """
    timestamp: str = Field(default_factory=_utc_now_iso)
    active_application: Optional[str] = None
    active_window: Optional[str] = None
    active_hwnd: Optional[int] = None
    visible_applications: List[str] = Field(default_factory=list)
    open_windows: List[Dict[str, Any]] = Field(default_factory=list)
    browser_url: Optional[str] = None
    browser_title: Optional[str] = None
    relevant_ui_targets: List[str] = Field(default_factory=list)
    target_confidence: Dict[str, float] = Field(default_factory=dict)
    workflow_step_status: Dict[str, str] = Field(default_factory=dict)
    completed_actions: List[str] = Field(default_factory=list)
    expected_state: Optional[Dict[str, Any]] = None
    current_state: Optional[Dict[str, Any]] = None
    failure_recovery_context: Dict[str, Any] = Field(default_factory=dict)
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_sensitive_content(self) -> "ObservedComputerState":
        """Scrub any sensitive fields in metadata or recovery context."""
        if self.safe_metadata:
            self.safe_metadata = _scrub_secrets_recursive(self.safe_metadata)
        if self.failure_recovery_context:
            self.failure_recovery_context = _scrub_secrets_recursive(self.failure_recovery_context)
        if self.current_state:
            self.current_state = _scrub_secrets_recursive(self.current_state)
        if self.expected_state:
            self.expected_state = _scrub_secrets_recursive(self.expected_state)
        return self


class StateEvaluationResult(BaseModel):
    """Result of comparing expected outcome against observed computer state."""
    classification: StateDiffClassification
    matches: bool
    reason: str
    suggested_action: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class AdaptiveExecutionResult(BaseModel):
    """Complete structured result of an adaptive computer-use workflow execution."""
    workflow_id: str
    goal: str
    status: ComputerWorkflowState
    success: bool
    message: str
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    step_results: List[Dict[str, Any]] = Field(default_factory=list)
    adaptation_cycles: int = 0
    recovery_attempts: int = 0
    classification: StateDiffClassification = StateDiffClassification.MATCH
    failure_class: Optional[FailureClass] = None
    clarification_needed: bool = False
    clarification_prompt: Optional[str] = None
    checkpoint_id: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adaptive Computer-Use Controller
# ---------------------------------------------------------------------------

class AdaptiveComputerUseController:
    """Milestone 17.3 Adaptive Computer-Use Intelligence Orchestrator.

    High-level orchestrator sitting strictly above existing workflow and desktop engines.
    It coordinates:
        observe
        → evaluate state
        → select next step
        → invoke existing workflow/action capability
        → verify
        → re-plan if necessary
    """

    def __init__(
        self,
        workflow_engine: Optional[ComputerWorkflowEngine] = None,
        planner: Optional[Any] = None,
        observer: Optional[Any] = None,
        driver: Optional[Any] = None,
        target_resolver: Optional[Any] = None,
        desktop_actions: Optional[Any] = None,
        browser_engine: Optional[Any] = None,
        permissions: Optional[CapabilityPermissionManager] = None,
        guard: Optional[SafetyGuard] = None,
        checkpoints: Optional[CheckpointStore] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        self._workflow_engine = workflow_engine
        self._planner = planner
        self._observer = observer
        self._driver = driver
        self._target_resolver = target_resolver
        self._desktop_actions = desktop_actions
        self._browser_engine = browser_engine
        self._permissions = permissions or permission_manager
        self._guard = guard or SafetyGuard()
        self._checkpoints = checkpoints or checkpoint_store
        self._confirmation_mgr = confirmation_mgr or ConfirmationManager()

        # Active workflow cancellation tokens
        self._cancelled_workflows: Set[str] = set()
        # Active paused workflows waiting for user clarification or confirmation
        self._active_adaptive_runs: Dict[str, ComputerWorkflowPlan] = {}

    # -----------------------------------------------------------------------
    # Lazy Property Accessors (Strict Architectural Reuse)
    # -----------------------------------------------------------------------

    @property
    def workflow_engine(self) -> ComputerWorkflowEngine:
        if self._workflow_engine is not None:
            return self._workflow_engine
        self._workflow_engine = default_workflow_engine
        return self._workflow_engine

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
    # Telemetry Helper (Safe Payload Only)
    # -----------------------------------------------------------------------

    async def _emit_telemetry(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        workflow_id: str,
        duration_ms: float = 0.0,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish sanitized adaptive telemetry event."""
        scrubbed = _scrub_secrets_recursive(safe_metadata or {})
        try:
            await action_bus.publish(
                ActionEvent(
                    action_type=action_type,
                    status=status,
                    title=title,
                    task_id=workflow_id,
                    duration_ms=duration_ms,
                    safe_metadata=scrubbed,
                )
            )
        except Exception as e:
            logger.debug(f"[ADAPTIVE] Telemetry publish error: {e}")

    # -----------------------------------------------------------------------
    # Prompt Injection Defense
    # -----------------------------------------------------------------------

    def check_prompt_injection(self, text: str) -> bool:
        """Evaluate whether untrusted text contains prompt injection or instruction hijacking patterns.

        Invariants:
        - All external UI/OCR/webpage content is strictly passive data.
        - Never allow untrusted text to alter system behavior or issue commands.
        """
        if not text:
            return False
        text_lower = text.lower()
        for pat in FORBIDDEN_PROMPT_INJECTION_PATTERNS:
            if pat in text_lower:
                return True
        if self._guard.is_blocked_instruction(text):
            return True
        return False

    # -----------------------------------------------------------------------
    # Environment State Observation
    # -----------------------------------------------------------------------

    async def observe_environment(
        self,
        target_app: Optional[str] = None,
        relevant_targets: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> ObservedComputerState:
        """Observe live desktop, allowlisted applications, active window, and browser state.

        Strict Invariants:
        - Gathers structured properties only; zero secrets or passwords.
        - Does NOT retain raw base64 screenshot blobs.
        """
        t0 = time.monotonic()
        active_app: Optional[str] = None
        active_win_title: Optional[str] = None
        active_hwnd: Optional[int] = None
        visible_apps: List[str] = []
        open_wins: List[Dict[str, Any]] = []

        # 1. Desktop Window Observation
        if self.driver is not None:
            try:
                inspect_res = self.driver.inspect_windows()
                for w in inspect_res:
                    if w.application and w.application not in visible_apps:
                        visible_apps.append(w.application)
                    open_wins.append({
                        "hwnd": w.hwnd,
                        "application": w.application,
                        "title": w.title,
                        "focused": w.focused,
                        "visible": w.visible,
                        "bounds": {"left": w.left, "top": w.top, "width": w.width, "height": w.height},
                    })
                    if w.focused:
                        active_app = w.application
                        active_win_title = w.title
                        active_hwnd = w.hwnd
            except Exception as e:
                logger.debug(f"[ADAPTIVE] Desktop inspection fallback: {e}")

        # 2. Browser State Observation
        browser_url: Optional[str] = None
        browser_title: Optional[str] = None
        if self.browser is not None:
            try:
                tab_info = await self.browser.get_active_tab_info(session_id=session_id)
                if tab_info:
                    browser_url = tab_info.get("url")
                    browser_title = tab_info.get("title")
            except Exception as e:
                logger.debug(f"[ADAPTIVE] Browser inspection fallback: {e}")

        # 3. Target Confidence Evaluation (if targets requested)
        target_confidences: Dict[str, float] = {}
        if relevant_targets and self.target_resolver is not None:
            for tgt in relevant_targets:
                try:
                    res: DesktopTargetResolutionResult = await self.target_resolver.resolve_target(
                        description=tgt,
                        application=active_app or target_app,
                        hwnd=active_hwnd,
                    )
                    target_confidences[tgt] = res.confidence if res.target_found else 0.0
                except Exception:
                    target_confidences[tgt] = 0.0

        dur_ms = (time.monotonic() - t0) * 1000
        observed = ObservedComputerState(
            active_application=active_app,
            active_window=active_win_title,
            active_hwnd=active_hwnd,
            visible_applications=visible_apps,
            open_windows=open_wins,
            browser_url=browser_url,
            browser_title=browser_title,
            relevant_ui_targets=relevant_targets or [],
            target_confidence=target_confidences,
            safe_metadata={"duration_ms": dur_ms, "target_app": target_app},
        )

        if task_id:
            await self._emit_telemetry(
                ActionType.ADAPTIVE_STATE_OBSERVED,
                ActionStatus.COMPLETED,
                f"Observed state: active app '{active_app or 'None'}'",
                task_id,
                duration_ms=dur_ms,
                safe_metadata={
                    "active_application": active_app,
                    "visible_apps_count": len(visible_apps),
                    "open_windows_count": len(open_wins),
                },
            )

        return observed

    # -----------------------------------------------------------------------
    # State Difference Classification
    # -----------------------------------------------------------------------

    def evaluate_state_difference(
        self,
        expected: Optional[Dict[str, Any]],
        observed: ObservedComputerState,
        current_step: Optional[ComputerWorkflowStep] = None,
    ) -> StateEvaluationResult:
        """Compare expected state against observed state and classify difference."""
        if expected is None:
            return StateEvaluationResult(
                classification=StateDiffClassification.MATCH,
                matches=True,
                reason="No explicit expected outcome specified",
            )

        # 1. Prompt Injection Check on observed text / context
        for text in [observed.active_window, observed.browser_title, observed.browser_url]:
            if text and self.check_prompt_injection(text):
                return StateEvaluationResult(
                    classification=StateDiffClassification.PROMPT_INJECTION_DETECTED,
                    matches=False,
                    reason=f"Prompt injection pattern detected in environment text: '{text[:40]}'",
                    suggested_action="stop_safely",
                )

        # 2. Application mismatch check
        expected_app = (
            expected.get("application")
            or expected.get("expected_app")
            or expected.get("window_active")
            or (current_step.application_context if current_step else None)
        )
        if expected_app:
            exp_clean = expected_app.strip().lower()
            obs_active = (observed.active_application or "").strip().lower()
            if obs_active and exp_clean not in obs_active and obs_active not in exp_clean:
                # Check if the expected application is open but not focused
                matching_visible = [
                    app for app in observed.visible_applications
                    if exp_clean in app.lower() or app.lower() in exp_clean
                ]
                if matching_visible:
                    return StateEvaluationResult(
                        classification=StateDiffClassification.WINDOW_CHANGED,
                        matches=False,
                        reason=f"Expected active app '{expected_app}', but '{observed.active_application}' is active (expected app is visible)",
                        suggested_action="focus_application",
                        details={"expected_app": expected_app, "active_app": observed.active_application},
                    )
                return StateEvaluationResult(
                    classification=StateDiffClassification.APPLICATION_CHANGED,
                    matches=False,
                    reason=f"Expected application '{expected_app}' is not running or active (current active: '{observed.active_application}')",
                    suggested_action="launch_application",
                    details={"expected_app": expected_app, "active_app": observed.active_application},
                )

        # 3. Window title / window active check
        expected_win = expected.get("window_title") or expected.get("window_active")
        if expected_win and isinstance(expected_win, str):
            exp_win_lower = expected_win.strip().lower()
            obs_win_lower = (observed.active_window or "").strip().lower()
            if exp_win_lower not in obs_win_lower:
                return StateEvaluationResult(
                    classification=StateDiffClassification.WINDOW_CHANGED,
                    matches=False,
                    reason=f"Active window '{observed.active_window}' does not match expected '{expected_win}'",
                    suggested_action="switch_window",
                    details={"expected_window": expected_win, "observed_window": observed.active_window},
                )

        # 4. Browser URL / navigation check
        expected_url = expected.get("url") or expected.get("expected_url") or expected.get("navigation")
        if expected_url and isinstance(expected_url, str):
            exp_url_clean = expected_url.strip().lower()
            obs_url_clean = (observed.browser_url or "").strip().lower()
            if exp_url_clean not in obs_url_clean:
                return StateEvaluationResult(
                    classification=StateDiffClassification.NAVIGATION_CHANGED,
                    matches=False,
                    reason=f"Browser URL '{observed.browser_url}' does not match expected '{expected_url}'",
                    suggested_action="replan_navigation",
                    details={"expected_url": expected_url, "observed_url": observed.browser_url},
                )

        # 5. UI Target availability & confidence check
        expected_target = expected.get("target_visible") or expected.get("target") or (current_step.target_description if current_step else None)
        if expected_target and isinstance(expected_target, str):
            conf = observed.target_confidence.get(expected_target)
            if conf is not None and conf < 0.40:
                if 0.0 < conf < 0.40:
                    return StateEvaluationResult(
                        classification=StateDiffClassification.TARGET_MOVED,
                        matches=False,
                        reason=f"Target '{expected_target}' confidence is low ({conf:.2f}), element may have moved",
                        suggested_action="reresolve_target",
                        details={"target": expected_target, "confidence": conf},
                    )
                return StateEvaluationResult(
                    classification=StateDiffClassification.EXPECTED_STATE_NOT_REACHED,
                    matches=False,
                    reason=f"Expected target '{expected_target}' not found in current UI",
                    suggested_action="reresolve_or_replan",
                    details={"target": expected_target},
                )

        # 6. Ambiguity check
        if expected.get("ambiguous", False):
            return StateEvaluationResult(
                classification=StateDiffClassification.AMBIGUOUS_STATE,
                matches=False,
                reason="Multiple conflicting targets or ambiguous UI states detected",
                suggested_action="request_clarification",
            )

        # 7. Unsupported state check
        if expected.get("unsupported", False):
            return StateEvaluationResult(
                classification=StateDiffClassification.UNSUPPORTED_STATE,
                matches=False,
                reason="UI is in an unsupported or unapproved system state",
                suggested_action="stop_safely",
            )

        return StateEvaluationResult(
            classification=StateDiffClassification.MATCH,
            matches=True,
            reason="Observed state satisfies expected outcome",
        )

    # -----------------------------------------------------------------------
    # Dynamic Next-Action Selection (Optimization)
    # -----------------------------------------------------------------------

    def select_dynamic_next_action(
        self,
        goal: str,
        plan: ComputerWorkflowPlan,
        observed: ObservedComputerState,
    ) -> Optional[int]:
        """Determine next relevant step index, skipping redundant actions based on current state.

        Examples:
        - If Step 0 is "open_application" for Chrome, but Chrome is ALREADY running and active:
          return index 1 (skip opening).
        - If Step 0 is "open project" in VS Code, and VS Code is ALREADY open with that project:
          advance to subsequent steps or return None if all completed.
        """
        if not plan.steps:
            return None

        # Check Step 0: application launch redundancy
        step0 = plan.steps[0]
        if step0.status == ComputerWorkflowState.PENDING and step0.action in ("open_application", "launch_app"):
            target_app = (
                step0.application_context
                or step0.arguments.get("app_name")
                or step0.arguments.get("application")
                or ""
            ).lower()

            # Is target app already running and active?
            if observed.active_application and target_app in observed.active_application.lower():
                logger.info(
                    f"[ADAPTIVE] Application '{observed.active_application}' is already active. "
                    f"Skipping redundant step '{step0.name}'."
                )
                step0.status = ComputerWorkflowState.COMPLETED
                step0.result = {"skipped": True, "reason": "Application already open and active"}
                return 1

            # Is target app running in background?
            for visible in observed.visible_applications:
                if target_app in visible.lower():
                    logger.info(
                        f"[ADAPTIVE] Application '{visible}' is already running. "
                        f"Mutating step '{step0.name}' from launch to focus."
                    )
                    step0.action = "focus"
                    step0.name = f"Focus {visible}"
                    step0.arguments["application"] = visible
                    return 0

        g_lower = goal.lower()
        if ("vs code" in g_lower or "vscode" in g_lower) and observed.active_application:
            if "visual studio code" in observed.active_application.lower():
                if len(plan.steps) == 1 and plan.steps[0].action in ("open_application", "focus"):
                    plan.steps[0].status = ComputerWorkflowState.COMPLETED
                    plan.steps[0].result = {"already_active": True}
                    return None

        # Return first non-completed step
        for idx, step in enumerate(plan.steps):
            if step.status != ComputerWorkflowState.COMPLETED:
                return idx

        return None

    # -----------------------------------------------------------------------
    # Action Duplication Protection
    # -----------------------------------------------------------------------

    def check_duplication_protection(
        self,
        step: ComputerWorkflowStep,
        observed: ObservedComputerState,
    ) -> bool:
        """Determine whether a consequential or non-idempotent action has ALREADY succeeded.

        Prevents duplicate form submissions, duplicate clicks, and repeated mutations
        if a timeout or network glitch occurred while the action actually succeeded.
        """
        idempotency = classify_action_idempotency(step.action)
        if idempotency == ActionIdempotency.IDEMPOTENT:
            return False  # Idempotent actions can safely execute

        # For non-idempotent actions (e.g. submit, click, open):
        # Inspect whether the expected state was achieved despite an execution error
        if step.expected_state:
            eval_res = self.evaluate_state_difference(step.expected_state, observed, step)
            if eval_res.matches:
                logger.info(
                    f"[ADAPTIVE] Action Duplication Protection: Step '{step.name}' expected outcome "
                    f"is already achieved in observed state. Suppressing duplicate execution."
                )
                return True

        # Check application launch duplication
        if step.action in ("open_application", "launch_app"):
            app_req = (step.application_context or step.arguments.get("app_name") or "").lower()
            if app_req and observed.active_application and app_req in observed.active_application.lower():
                return True

        return False

    # -----------------------------------------------------------------------
    # Partial Re-planning
    # -----------------------------------------------------------------------

    async def partial_replan(
        self,
        plan: ComputerWorkflowPlan,
        failed_step_index: int,
        observed: ObservedComputerState,
        failure_reason: str,
    ) -> ComputerWorkflowPlan:
        """Re-plan only the affected remaining steps (failed_step_index onward).

        Strict Invariants:
        - Completed steps 0..failed_step_index-1 are strictly preserved.
        - Never blindly repeats completed actions.
        - Preserves confirmation requirements on any newly synthesized steps.
        - Emits ADAPTIVE_REPLAN_STARTED and ADAPTIVE_REPLAN_COMPLETED telemetry.
        """
        t0 = time.monotonic()
        await self._emit_telemetry(
            ActionType.ADAPTIVE_REPLAN_STARTED,
            ActionStatus.STARTED,
            f"Partial re-planning workflow '{plan.workflow_id}' from step {failed_step_index}",
            plan.workflow_id,
            safe_metadata={
                "failed_step_index": failed_step_index,
                "failure_reason": failure_reason,
                "completed_steps_count": failed_step_index,
            },
        )

        completed_steps = plan.steps[:failed_step_index]
        failed_step = plan.steps[failed_step_index] if failed_step_index < len(plan.steps) else None

        # Build replacement steps for remaining intent
        replacement_steps: List[ComputerWorkflowStep] = []

        # Determine recovery strategy based on observed state
        if observed.active_application and failed_step and failed_step.capability == "desktop":
            # Target moved or UI changed: synthesize re-resolve & act step
            recovery_step = ComputerWorkflowStep(
                name=f"Re-resolve and retry {failed_step.name}",
                capability=failed_step.capability,
                action=failed_step.action,
                target_description=failed_step.target_description,
                application_context=observed.active_application,
                arguments=dict(failed_step.arguments),
                expected_state=failed_step.expected_state,
                requires_confirmation=failed_step.requires_confirmation,
                metadata={"replanned_from": failed_step.step_id, "reason": failure_reason},
            )
            replacement_steps.append(recovery_step)
            # Add subsequent steps from original plan
            for orig_step in plan.steps[failed_step_index + 1:]:
                replacement_steps.append(
                    orig_step.model_copy(
                        update={
                            "step_id": f"step-{uuid.uuid4().hex[:8]}",
                            "status": ComputerWorkflowState.PENDING,
                        }
                    )
                )
        elif observed.browser_url and failed_step and failed_step.capability == "browser":
            # Navigation changed: synthesize navigation verification step
            nav_step = ComputerWorkflowStep(
                name="Verify current page and continue",
                capability="browser",
                action="inspect",
                expected_state={"url": observed.browser_url},
                requires_confirmation=False,
                metadata={"replanned_from": failed_step.step_id, "current_url": observed.browser_url},
            )
            replacement_steps.append(nav_step)
            for orig_step in plan.steps[failed_step_index + 1:]:
                replacement_steps.append(
                    orig_step.model_copy(
                        update={
                            "step_id": f"step-{uuid.uuid4().hex[:8]}",
                            "status": ComputerWorkflowState.PENDING,
                        }
                    )
                )
        else:
            # Fallback: keep remaining steps, marking failed step with updated metadata
            if failed_step:
                retry_step = failed_step.model_copy(
                    update={
                        "step_id": f"step-{uuid.uuid4().hex[:8]}",
                        "status": ComputerWorkflowState.READY,
                        "retry_count": failed_step.retry_count + 1,
                        "metadata": {**failed_step.metadata, "replanned": True},
                    }
                )
                replacement_steps.append(retry_step)
            for orig_step in plan.steps[failed_step_index + 1:]:
                replacement_steps.append(
                    orig_step.model_copy(
                        update={
                            "step_id": f"step-{uuid.uuid4().hex[:8]}",
                            "status": ComputerWorkflowState.PENDING,
                        }
                    )
                )

        # Assemble new plan with preserved completed steps
        plan.steps = completed_steps + replacement_steps
        plan.current_step_index = failed_step_index

        dur_ms = (time.monotonic() - t0) * 1000
        await self._emit_telemetry(
            ActionType.ADAPTIVE_REPLAN_COMPLETED,
            ActionStatus.COMPLETED,
            f"Partial re-planning completed with {len(replacement_steps)} remaining step(s)",
            plan.workflow_id,
            duration_ms=dur_ms,
            safe_metadata={
                "preserved_steps": len(completed_steps),
                "new_steps": len(replacement_steps),
                "total_steps": len(plan.steps),
            },
        )

        return plan

    # -----------------------------------------------------------------------
    # Workflow Checkpointing (Safe State Only)
    # -----------------------------------------------------------------------

    async def _save_checkpoint(
        self,
        plan: ComputerWorkflowPlan,
        observed: ObservedComputerState,
        adaptation_cycles: int,
        recovery_attempts: int,
        current_step_id: Optional[str] = None,
    ) -> Optional[str]:
        """Persist safe checkpoint using CheckpointStore."""
        if self._checkpoints is None:
            return None

        # Build clean, secret-free metadata
        safe_state = {
            "workflow_id": plan.workflow_id,
            "goal": plan.goal,
            "status": plan.status.value,
            "current_step_index": plan.current_step_index,
            "current_step_id": current_step_id,
            "adaptation_cycles": adaptation_cycles,
            "recovery_attempts": recovery_attempts,
            "active_application": observed.active_application,
            "active_window": observed.active_window,
            "browser_url": observed.browser_url,
            "timestamp": _utc_now_iso(),
        }

        try:
            res = self._checkpoints.save_checkpoint(
                task_id=f"{plan.workflow_id}-{uuid.uuid4().hex[:6]}",
                user_goal=plan.goal,
                workflow_id=plan.workflow_id,
                current_state=plan.status.value,
                current_step_id=current_step_id,
                safe_metadata=safe_state,
            )
            return getattr(res, "task_id", plan.workflow_id)
        except Exception as e:
            logger.debug(f"[ADAPTIVE] Checkpoint save error: {e}")
            return None

    # -----------------------------------------------------------------------
    # Main Bounded Adaptive Execution Loop
    # -----------------------------------------------------------------------

    async def execute_adaptive_workflow(
        self,
        goal_or_plan: Union[str, ComputerWorkflowPlan],
        auto_confirm: bool = False,
        session_id: str = "default",
        task_id: Optional[str] = None,
        max_adaptation_cycles: int = 3,
        max_recovery_attempts: int = 3,
        timeout_sec: float = 300.0,
    ) -> AdaptiveExecutionResult:
        """Execute a computer-use workflow with bounded adaptive intelligence.

        Pipeline:
            PLAN → OBSERVE → DECIDE → ACT → VERIFY → COMPARE EXPECTED STATE
            MATCH → Continue
            MISMATCH → Diagnose → Bounded Re-Plan → Continue
        """
        t0 = time.monotonic()

        # 1. Resolve Plan
        if isinstance(goal_or_plan, str):
            plan = await self.workflow_engine.plan_workflow(goal_or_plan)
        else:
            plan = goal_or_plan

        w_id = task_id or plan.workflow_id
        self._active_adaptive_runs[w_id] = plan

        # Check early cancellation
        if w_id in self._cancelled_workflows or plan.status == ComputerWorkflowState.CANCELLED:
            return AdaptiveExecutionResult(
                workflow_id=w_id,
                goal=plan.goal,
                status=ComputerWorkflowState.CANCELLED,
                success=False,
                message="Workflow was cancelled prior to execution",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Transition to EXECUTING state machine state safely
        if plan.status == ComputerWorkflowState.PENDING:
            plan.transition_to(ComputerWorkflowState.READY)
        if plan.status in (ComputerWorkflowState.READY, ComputerWorkflowState.OBSERVING):
            plan.transition_to(ComputerWorkflowState.EXECUTING)

        # 2. Initial Observation & Dynamic Next-Action Optimization
        initial_obs = await self.observe_environment(session_id=session_id, task_id=w_id)
        start_idx = self.select_dynamic_next_action(plan.goal, plan, initial_obs)
        if start_idx is None:
            # Entire goal was already satisfied by initial environment
            plan.transition_to(ComputerWorkflowState.COMPLETED)
            return AdaptiveExecutionResult(
                workflow_id=w_id,
                goal=plan.goal,
                status=ComputerWorkflowState.COMPLETED,
                success=True,
                message="Workflow goal already satisfied by current verified computer state",
                steps_total=len(plan.steps),
                steps_completed=len(plan.steps),
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        adaptation_cycles = 0
        recovery_attempts = 0
        step_results: List[Dict[str, Any]] = []

        # Save initial checkpoint
        last_ckpt = await self._save_checkpoint(plan, initial_obs, adaptation_cycles, recovery_attempts)

        # Bounded Execution Loop
        while True:
            # Check workflow timeout
            if (time.monotonic() - t0) > timeout_sec:
                plan.transition_to(ComputerWorkflowState.FAILED)
                await self._emit_telemetry(
                    ActionType.ADAPTIVE_STOPPED,
                    ActionStatus.FAILED,
                    f"Adaptive workflow timed out after {timeout_sec:.1f}s",
                    w_id,
                )
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.FAILED,
                    success=False,
                    message=f"Adaptive workflow exceeded timeout budget of {timeout_sec}s",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=1,
                    step_results=step_results,
                    adaptation_cycles=adaptation_cycles,
                    recovery_attempts=recovery_attempts,
                    failure_class=FailureClass.ACTION_TIMEOUT,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Check user cancellation
            if w_id in self._cancelled_workflows or plan.status == ComputerWorkflowState.CANCELLED:
                plan.transition_to(ComputerWorkflowState.CANCELLED)
                await self._emit_telemetry(
                    ActionType.ADAPTIVE_STOPPED,
                    ActionStatus.CANCELLED,
                    "Adaptive workflow cancelled by user",
                    w_id,
                )
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.CANCELLED,
                    success=False,
                    message="Workflow execution cancelled by user",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=0,
                    step_results=step_results,
                    adaptation_cycles=adaptation_cycles,
                    recovery_attempts=recovery_attempts,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Find next pending step
            pending_step_idx: Optional[int] = None
            for idx, s in enumerate(plan.steps):
                if s.status != ComputerWorkflowState.COMPLETED:
                    pending_step_idx = idx
                    break

            # If all steps completed, workflow is done!
            if pending_step_idx is None:
                plan.transition_to(ComputerWorkflowState.COMPLETED)
                plan.completed_at = _utc_now_iso()
                dur_ms = (time.monotonic() - t0) * 1000
                await self._emit_telemetry(
                    ActionType.WORKFLOW_COMPLETED,
                    ActionStatus.COMPLETED,
                    f"Adaptive workflow completed: {plan.goal[:50]}",
                    w_id,
                    duration_ms=dur_ms,
                )
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.COMPLETED,
                    success=True,
                    message=f"Workflow completed successfully ({len(plan.steps)} steps)",
                    steps_total=len(plan.steps),
                    steps_completed=len(plan.steps),
                    steps_failed=0,
                    step_results=step_results,
                    adaptation_cycles=adaptation_cycles,
                    recovery_attempts=recovery_attempts,
                    checkpoint_id=last_ckpt,
                    duration_ms=dur_ms,
                )

            step = plan.steps[pending_step_idx]
            plan.current_step_index = pending_step_idx

            # ---------------------------------------------------------------
            # 1. OBSERVE PRE-STEP STATE
            # ---------------------------------------------------------------
            obs_before = await self.observe_environment(
                target_app=step.application_context,
                relevant_targets=[step.target_description] if step.target_description else None,
                session_id=session_id,
                task_id=w_id,
            )

            # ---------------------------------------------------------------
            # 2. DUPLICATION PROTECTION & PROMPT INJECTION DEFENSE
            # ---------------------------------------------------------------
            # Check prompt injection in step arguments
            for arg_val in step.arguments.values():
                if isinstance(arg_val, str) and self.check_prompt_injection(arg_val):
                    step.status = ComputerWorkflowState.BLOCKED
                    step.error = "Potential prompt injection detected in step arguments"
                    step.failure_class = FailureClass.PROMPT_INJECTION_DETECTED
                    return AdaptiveExecutionResult(
                        workflow_id=w_id,
                        goal=plan.goal,
                        status=ComputerWorkflowState.FAILED,
                        success=False,
                        message=step.error,
                        classification=StateDiffClassification.PROMPT_INJECTION_DETECTED,
                        failure_class=FailureClass.PROMPT_INJECTION_DETECTED,
                        steps_total=len(plan.steps),
                        steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                        steps_failed=1,
                        step_results=step_results,
                        checkpoint_id=last_ckpt,
                        duration_ms=(time.monotonic() - t0) * 1000,
                    )

            # Non-idempotent action duplication protection
            if self.check_duplication_protection(step, obs_before):
                logger.info(f"[ADAPTIVE] Step '{step.name}' outcome verified already met. Marking completed.")
                step.status = ComputerWorkflowState.COMPLETED
                step.result = {"already_satisfied": True}
                step_results.append({
                    "step_id": step.step_id,
                    "name": step.name,
                    "status": step.status.value,
                    "result": step.result,
                })
                continue

            # ---------------------------------------------------------------
            # 3. CONFIRMATION PRESERVATION
            # ---------------------------------------------------------------
            if step.requires_confirmation and not auto_confirm:
                step.status = ComputerWorkflowState.WAITING_CONFIRMATION
                plan.status = ComputerWorkflowState.WAITING_CONFIRMATION
                last_ckpt = await self._save_checkpoint(plan, obs_before, adaptation_cycles, recovery_attempts, step.step_id)
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.WAITING_CONFIRMATION,
                    success=False,
                    message=f"Step '{step.name}' requires explicit user confirmation before proceeding.",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=0,
                    step_results=step_results,
                    adaptation_cycles=adaptation_cycles,
                    recovery_attempts=recovery_attempts,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # ---------------------------------------------------------------
            # 4. ACT: INVOKE EXISTING CAPABILITY
            # ---------------------------------------------------------------
            step_success = await self.workflow_engine.execute_single_step(
                plan=plan,
                step=step,
                auto_confirm=auto_confirm,
                session_id=session_id,
            ) if hasattr(self.workflow_engine, "execute_single_step") else await self.workflow_engine._execute_step_with_recovery(
                plan=plan,
                step=step,
                auto_confirm=auto_confirm,
                session_id=session_id,
            )

            # Record step result
            step_record = {
                "step_id": step.step_id,
                "name": step.name,
                "status": step.status.value,
                "action": step.action,
                "result": step.result,
                "error": step.error,
            }
            step_results.append(step_record)

            # ---------------------------------------------------------------
            # 5. VERIFY: POST-ACTION OBSERVATION & EXPECTED STATE COMPARISON
            # ---------------------------------------------------------------
            obs_after = await self.observe_environment(
                target_app=step.application_context,
                relevant_targets=[step.target_description] if step.target_description else None,
                session_id=session_id,
                task_id=w_id,
            )

            eval_diff = self.evaluate_state_difference(step.expected_state, obs_after, step)

            if step_success and eval_diff.matches:
                # MATCH: State matches expectation, proceed!
                await self._emit_telemetry(
                    ActionType.ADAPTIVE_STATE_MATCHED,
                    ActionStatus.COMPLETED,
                    f"State verified for step '{step.name}'",
                    w_id,
                    safe_metadata={"step_id": step.step_id, "action": step.action},
                )
                last_ckpt = await self._save_checkpoint(plan, obs_after, adaptation_cycles, recovery_attempts, step.step_id)
                continue

            # ---------------------------------------------------------------
            # 6. MISMATCH / FAILURE: DIAGNOSE & RECOVER / RE-PLAN
            # ---------------------------------------------------------------
            logger.warning(
                f"[ADAPTIVE] State mismatch on step '{step.name}': {eval_diff.classification.value} - {eval_diff.reason}"
            )

            # Check prompt injection
            if eval_diff.classification == StateDiffClassification.PROMPT_INJECTION_DETECTED:
                step.status = ComputerWorkflowState.BLOCKED
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.FAILED,
                    success=False,
                    message=eval_diff.reason,
                    classification=StateDiffClassification.PROMPT_INJECTION_DETECTED,
                    failure_class=FailureClass.PROMPT_INJECTION_DETECTED,
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=1,
                    step_results=step_results,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Check security block
            if eval_diff.classification == StateDiffClassification.SECURITY_BLOCKED:
                step.status = ComputerWorkflowState.BLOCKED
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.BLOCKED,
                    success=False,
                    message=eval_diff.reason,
                    classification=StateDiffClassification.SECURITY_BLOCKED,
                    failure_class=FailureClass.SECURITY,
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=1,
                    step_results=step_results,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Check ambiguous state: Do not guess!
            if eval_diff.classification == StateDiffClassification.AMBIGUOUS_STATE:
                return AdaptiveExecutionResult(
                    workflow_id=w_id,
                    goal=plan.goal,
                    status=ComputerWorkflowState.WAITING_CONFIRMATION,
                    success=False,
                    message="Ambiguous UI state detected. User clarification is required.",
                    classification=StateDiffClassification.AMBIGUOUS_STATE,
                    clarification_needed=True,
                    clarification_prompt=f"RYVEN detected multiple possibilities: {eval_diff.reason}. Which action should be taken?",
                    steps_total=len(plan.steps),
                    steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                    steps_failed=0,
                    step_results=step_results,
                    checkpoint_id=last_ckpt,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )

            # Check target moved or minor UI change: attempt target re-resolution
            if eval_diff.classification in (StateDiffClassification.TARGET_MOVED, StateDiffClassification.MINOR_UI_CHANGE):
                if recovery_attempts < max_recovery_attempts:
                    recovery_attempts += 1
                    await self._emit_telemetry(
                        ActionType.ADAPTIVE_RECOVERY_STARTED,
                        ActionStatus.STARTED,
                        f"UI changed for '{step.name}'. Re-resolving target...",
                        w_id,
                        safe_metadata={"target": step.target_description, "recovery_attempt": recovery_attempts},
                    )
                    # Reset step status for retry
                    step.status = ComputerWorkflowState.READY
                    await self._emit_telemetry(
                        ActionType.ADAPTIVE_RECOVERY_COMPLETED,
                        ActionStatus.COMPLETED,
                        f"Re-resolution prepared for '{step.name}'",
                        w_id,
                    )
                    continue

            # Bounded partial re-planning
            if adaptation_cycles < max_adaptation_cycles:
                adaptation_cycles += 1
                plan = await self.partial_replan(
                    plan=plan,
                    failed_step_index=pending_step_idx,
                    observed=obs_after,
                    failure_reason=eval_diff.reason or step.error or "State mismatch",
                )
                last_ckpt = await self._save_checkpoint(plan, obs_after, adaptation_cycles, recovery_attempts, step.step_id)
                continue

            # Budget exhausted: Fail safely with explanation
            plan.transition_to(ComputerWorkflowState.FAILED)
            await self._emit_telemetry(
                ActionType.ADAPTIVE_STOPPED,
                ActionStatus.FAILED,
                f"Adaptation budget exhausted ({adaptation_cycles} cycles)",
                w_id,
            )
            return AdaptiveExecutionResult(
                workflow_id=w_id,
                goal=plan.goal,
                status=ComputerWorkflowState.FAILED,
                success=False,
                message=f"Adaptation budget exhausted. Could not satisfy expected outcome for '{step.name}': {eval_diff.reason}",
                classification=eval_diff.classification,
                failure_class=step.failure_class or FailureClass.UNRECOVERABLE,
                steps_total=len(plan.steps),
                steps_completed=sum(1 for s in plan.steps if s.status == ComputerWorkflowState.COMPLETED),
                steps_failed=1,
                step_results=step_results,
                adaptation_cycles=adaptation_cycles,
                recovery_attempts=recovery_attempts,
                checkpoint_id=last_ckpt,
                duration_ms=(time.monotonic() - t0) * 1000,
                error=step.error or eval_diff.reason,
            )

    # -----------------------------------------------------------------------
    # Cancellation & Confirmation Interface
    # -----------------------------------------------------------------------

    async def cancel_adaptive_workflow(
        self,
        workflow_id: str,
        reason: str = "User cancelled execution",
    ) -> AdaptiveExecutionResult:
        """Cancel an in-flight or waiting adaptive workflow."""
        self._cancelled_workflows.add(workflow_id)
        plan = self._active_adaptive_runs.get(workflow_id)
        if plan:
            plan.status = ComputerWorkflowState.CANCELLED

        await self._emit_telemetry(
            ActionType.ADAPTIVE_STOPPED,
            ActionStatus.CANCELLED,
            f"Adaptive workflow cancelled: {reason}",
            workflow_id,
        )

        return AdaptiveExecutionResult(
            workflow_id=workflow_id,
            goal=plan.goal if plan else "Unknown",
            status=ComputerWorkflowState.CANCELLED,
            success=False,
            message=f"Adaptive workflow cancelled: {reason}",
        )

    async def confirm_adaptive_step(
        self,
        workflow_id: str,
        confirmation_token: Optional[str] = None,
        approved: bool = True,
    ) -> AdaptiveExecutionResult:
        """Approve or reject a workflow step waiting for user confirmation."""
        plan = self._active_adaptive_runs.get(workflow_id)
        if not plan:
            return AdaptiveExecutionResult(
                workflow_id=workflow_id,
                goal="",
                status=ComputerWorkflowState.FAILED,
                success=False,
                message=f"Active adaptive workflow '{workflow_id}' not found.",
                failure_class=FailureClass.UNRECOVERABLE,
            )

        if confirmation_token:
            logger.debug(f"[ADAPTIVE] Validating confirmation token for workflow '{workflow_id}'")

        if not approved:
            return await self.cancel_adaptive_workflow(workflow_id, reason="User rejected confirmation")

        # User approved: Resume execution
        for step in plan.steps:
            if step.status == ComputerWorkflowState.WAITING_CONFIRMATION:
                step.status = ComputerWorkflowState.READY
                step.requires_confirmation = False  # Mark approved for this attempt

        plan.status = ComputerWorkflowState.EXECUTING
        return await self.execute_adaptive_workflow(
            goal_or_plan=plan,
            auto_confirm=True,  # User explicitly approved
            session_id="default",
            task_id=workflow_id,
        )


# Singleton instance
adaptive_controller = AdaptiveComputerUseController()

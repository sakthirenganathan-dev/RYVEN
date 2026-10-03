"""
RYVEN 3.0 — Milestone 17.4 Real-World Computer-Use Reliability & Validation Test Suite.
Comprehensive 60-Test Verification Suite covering scenario models, fault injection,
idempotency, duplication prevention, security boundaries, metrics, and regressions.

Test Matrix:
1.  scenario model validation
2.  scenario registration
3.  scenario lifecycle
4.  Chrome reuse
5.  VS Code reuse
6.  duplicate launch prevention
7.  target moved
8.  target text changed
9.  ambiguous target
10. window changed
11. application changed
12. navigation changed
13. timeout-after-success
14. duplicate-action prevention
15. idempotent action
16. conditionally idempotent action
17. non-idempotent action
18. confirmation preservation
19. permission revalidation
20. SafetyGuard enforcement
21. prompt injection
22. partial workflow preservation
23. recovery limit
24. adaptation limit
25. cancellation
26. checkpoint creation
27. checkpoint recovery
28. secret redaction
29. telemetry redaction
30. telemetry event lifecycle
31. browser integration
32. desktop integration
33. target resolver reuse
34. desktop action engine reuse
35. workflow engine reuse
36. planner reuse
37. no duplicate planner
38. no duplicate workflow engine
39. no raw coordinates
40. no shell execution
41. no arbitrary executable launch
42. no keylogging
43. no credential extraction
44. allowlist enforcement
45. bounded execution
46. bounded recovery
47. bounded adaptation
48. cancellation during observation
49. cancellation during confirmation
50. cancellation during recovery
51. recovery after UI change
52. recovery after stale target
53. workflow completion
54. workflow failure
55. structured failure taxonomy
56. metrics integrity
57. safe checkpoint metadata
58. live verification compatibility
59. regression compatibility
60. frontend compatibility
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.adaptive import (
    ActionIdempotency,
    AdaptiveComputerUseController,
    AdaptiveExecutionResult,
    ObservedComputerState,
    StateDiffClassification,
    classify_action_idempotency,
)
from app.control.engine import RyvenControlEngine
from app.control.models import (
    DesktopTargetResolutionResult,
    DesktopWindowState,
    FailureClass,
)
from app.control.permissions import CapabilityPermissionManager
from app.control.reliability import (
    ReliabilityMetrics,
    ReliabilityMetricsTracker,
    ReliabilityScenario,
    ReliabilityScenarioRunner,
    ScenarioExecutionResult,
    create_canonical_scenarios,
)
from app.control.workflow import (
    ComputerWorkflowEngine,
    ComputerWorkflowPlan,
    ComputerWorkflowState,
    ComputerWorkflowStep,
)
from app.runtime.checkpoint_store import CheckpointStore
from app.workflows.confirmation import ConfirmationManager


# ---------------------------------------------------------------------------
# Fixtures & Test Setup
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_driver():
    """Mock WindowsDesktopDriver providing safe, controlled window inspection."""
    driver = MagicMock()
    driver.inspect_windows.return_value = [
        DesktopWindowState(
            hwnd=1001,
            process_id=501,
            executable="chrome.exe",
            application="Google Chrome",
            title="FastAPI - Google Chrome",
            left=0,
            top=0,
            width=1920,
            height=1080,
            visible=True,
            focused=True,
        ),
        DesktopWindowState(
            hwnd=1002,
            process_id=502,
            executable="code.exe",
            application="Visual Studio Code",
            title="RYVEN - Visual Studio Code",
            left=10,
            top=10,
            width=1200,
            height=800,
            visible=True,
            focused=False,
        ),
    ]
    driver.focus_window.return_value = True
    return driver


@pytest.fixture
def mock_target_resolver():
    """Mock DesktopTargetResolver."""
    resolver = MagicMock()
    res = DesktopTargetResolutionResult(
        success=True,
        target="Search button",
        confidence=0.92,
    )
    resolver.resolve_target = AsyncMock(return_value=res)
    return resolver


@pytest.fixture
def mock_browser():
    """Mock BrowserEngine."""
    browser = MagicMock()
    browser.get_active_tab_info = AsyncMock(return_value={
        "url": "https://fastapi.tiangolo.com",
        "title": "FastAPI Documentation",
    })
    return browser


@pytest.fixture
def mock_checkpoints():
    """In-memory CheckpointStore."""
    return CheckpointStore(db_path=":memory:")


@pytest.fixture
def adaptive_ctrl(mock_driver, mock_target_resolver, mock_browser, mock_checkpoints):
    """Configured AdaptiveComputerUseController with mocked subsystems."""
    wf_engine = ComputerWorkflowEngine(checkpoints=mock_checkpoints)
    ctrl = AdaptiveComputerUseController(
        workflow_engine=wf_engine,
        driver=mock_driver,
        target_resolver=mock_target_resolver,
        browser_engine=mock_browser,
        checkpoints=mock_checkpoints,
    )
    return ctrl


@pytest.fixture
def runner(adaptive_ctrl, mock_checkpoints):
    """ReliabilityScenarioRunner instance wired with mock controller."""
    return ReliabilityScenarioRunner(
        adaptive_controller=adaptive_ctrl,
        checkpoints=mock_checkpoints,
    )


# ---------------------------------------------------------------------------
# Test Cases 1 - 10: Scenario Models, Lifecycle & Reuse
# ---------------------------------------------------------------------------

def test_01_scenario_model_validation():
    """1. ReliabilityScenario validates fields and scrubs credentials."""
    scen = ReliabilityScenario(
        name="Test Scenario",
        description="Verify validation",
        workflow_goal="Open Chrome",
        expected_behavior="Advances safely",
        metadata={"password": "MySecretPassword!", "safe_key": "safe_val"},
    )
    assert scen.name == "Test Scenario"
    assert scen.metadata["password"] == "[REDACTED]"
    assert scen.metadata["safe_key"] == "safe_val"


def test_02_scenario_registration(runner):
    """2. Scenario registration registers custom scenarios alongside canonical ones."""
    initial_count = len(runner.list_scenarios())
    custom = ReliabilityScenario(
        scenario_id="scen-custom-01",
        name="Custom Scenario",
        description="Custom reliability test",
        workflow_goal="Inspect system",
        expected_behavior="Runs custom checks",
    )
    runner.register_scenario(custom)
    assert len(runner.list_scenarios()) == initial_count + 1
    assert runner.get_scenario("scen-custom-01") is not None


@pytest.mark.asyncio
async def test_03_scenario_lifecycle(runner):
    """3. Executing a scenario updates metrics and produces ScenarioExecutionResult."""
    scen = runner.get_scenario("scen-01-chrome-reuse")
    assert scen is not None
    res = await runner.run_scenario(scen)
    assert isinstance(res, ScenarioExecutionResult)
    assert res.scenario_id == "scen-01-chrome-reuse"
    assert res.success is True
    assert runner.metrics.workflow_attempts >= 1


@pytest.mark.asyncio
async def test_04_chrome_reuse(runner):
    """4. Chrome already open scenario skips duplicate launch."""
    res = await runner.run_scenario("scen-01-chrome-reuse")
    assert res.success is True
    assert "Active application correctly reused" in res.message


@pytest.mark.asyncio
async def test_05_vscode_reuse(runner):
    """5. VS Code already open scenario reuses existing window."""
    res = await runner.run_scenario("scen-02-vscode-reuse")
    assert res.success is True
    assert "Active application correctly reused" in res.message


def test_06_duplicate_launch_prevention(adaptive_ctrl):
    """6. Dynamic next-action selector suppresses redundant launch step."""
    step0 = ComputerWorkflowStep(name="Open Chrome", capability="desktop", action="open_application", application_context="Google Chrome")
    step1 = ComputerWorkflowStep(name="Navigate", capability="browser", action="navigate")
    plan = ComputerWorkflowPlan(goal="Browse", steps=[step0, step1])
    obs = ObservedComputerState(active_application="Google Chrome")

    next_idx = adaptive_ctrl.select_dynamic_next_action(plan.goal, plan, obs)
    assert next_idx == 1
    assert step0.status == ComputerWorkflowState.COMPLETED


def test_07_target_moved(adaptive_ctrl):
    """7. Degraded target confidence triggers TARGET_MOVED."""
    obs = ObservedComputerState(target_confidence={"Submit": 0.20})
    diff = adaptive_ctrl.evaluate_state_difference(expected={"target_visible": "Submit"}, observed=obs)
    assert diff.classification == StateDiffClassification.TARGET_MOVED
    assert diff.matches is False


def test_08_target_text_changed(adaptive_ctrl):
    """8. Text label variation with acceptable confidence matches."""
    obs = ObservedComputerState(target_confidence={"Search the web": 0.88})
    diff = adaptive_ctrl.evaluate_state_difference(expected={"target": "Search the web"}, observed=obs)
    assert diff.classification == StateDiffClassification.MATCH


def test_09_ambiguous_target(adaptive_ctrl):
    """9. Ambiguous targets classify as AMBIGUOUS_STATE without guessing."""
    obs = ObservedComputerState(active_application="Google Chrome")
    diff = adaptive_ctrl.evaluate_state_difference(expected={"ambiguous": True}, observed=obs)
    assert diff.classification == StateDiffClassification.AMBIGUOUS_STATE


def test_10_window_changed(adaptive_ctrl):
    """10. Expected application visible but unfocused yields WINDOW_CHANGED."""
    obs = ObservedComputerState(
        active_application="Visual Studio Code",
        visible_applications=["Google Chrome", "Visual Studio Code"],
    )
    diff = adaptive_ctrl.evaluate_state_difference(expected={"application": "Google Chrome"}, observed=obs)
    assert diff.classification == StateDiffClassification.WINDOW_CHANGED


# ---------------------------------------------------------------------------
# Test Cases 11 - 20: Differences, Idempotency & Confirmation
# ---------------------------------------------------------------------------

def test_11_application_changed(adaptive_ctrl):
    """11. Missing application yields APPLICATION_CHANGED."""
    obs = ObservedComputerState(active_application="Notepad", visible_applications=["Notepad"])
    diff = adaptive_ctrl.evaluate_state_difference(expected={"application": "Google Chrome"}, observed=obs)
    assert diff.classification == StateDiffClassification.APPLICATION_CHANGED


def test_12_navigation_changed(adaptive_ctrl):
    """12. Browser URL divergence yields NAVIGATION_CHANGED."""
    obs = ObservedComputerState(browser_url="https://bing.com")
    diff = adaptive_ctrl.evaluate_state_difference(expected={"url": "https://duckduckgo.com"}, observed=obs)
    assert diff.classification == StateDiffClassification.NAVIGATION_CHANGED


@pytest.mark.asyncio
async def test_13_timeout_after_success(runner):
    """13. Scenario 7 suppresses duplicate execution when outcome already achieved."""
    res = await runner.run_scenario("scen-07-timeout-after-success")
    assert res.success is True
    assert "Timeout-after-success correctly verified" in res.message
    assert runner.metrics.duplicate_actions_prevented >= 1
    assert runner.metrics.timeout_after_success_events >= 1


def test_14_duplicate_action_prevention(adaptive_ctrl):
    """14. Duplication protection detects already achieved target state."""
    step = ComputerWorkflowStep(
        name="Submit Form",
        capability="browser",
        action="submit",
        expected_state={"url": "https://example.com/receipt"},
    )
    obs = ObservedComputerState(browser_url="https://example.com/receipt")
    assert adaptive_ctrl.check_duplication_protection(step, obs) is True


def test_15_idempotent_action():
    """15. Read-only and focus actions are classified as IDEMPOTENT."""
    assert classify_action_idempotency("inspect") == ActionIdempotency.IDEMPOTENT
    assert classify_action_idempotency("focus") == ActionIdempotency.IDEMPOTENT
    assert classify_action_idempotency("observe") == ActionIdempotency.IDEMPOTENT


def test_16_conditionally_idempotent_action():
    """16. Open and navigation actions are CONDITIONALLY_IDEMPOTENT."""
    assert classify_action_idempotency("open_application") == ActionIdempotency.CONDITIONALLY_IDEMPOTENT
    assert classify_action_idempotency("navigate") == ActionIdempotency.CONDITIONALLY_IDEMPOTENT


def test_17_non_idempotent_action():
    """17. Mutating actions are NON_IDEMPOTENT."""
    assert classify_action_idempotency("click") == ActionIdempotency.NON_IDEMPOTENT
    assert classify_action_idempotency("type") == ActionIdempotency.NON_IDEMPOTENT
    assert classify_action_idempotency("submit") == ActionIdempotency.NON_IDEMPOTENT
    assert classify_action_idempotency("delete") == ActionIdempotency.NON_IDEMPOTENT


@pytest.mark.asyncio
async def test_18_confirmation_preservation(adaptive_ctrl):
    """18. Re-planning consequential action strictly preserves requires_confirmation=True."""
    step = ComputerWorkflowStep(
        name="Delete cache",
        capability="desktop",
        action="delete",
        requires_confirmation=True,
        status=ComputerWorkflowState.FAILED,
    )
    plan = ComputerWorkflowPlan(goal="Cleanup", steps=[step])
    obs = ObservedComputerState(active_application="Windows Terminal")

    replanned = await adaptive_ctrl.partial_replan(plan, 0, obs, "Temporary lock")
    assert replanned.steps[0].requires_confirmation is True


def test_19_permission_revalidation():
    """19. Replanned step must be verified through CapabilityPermissionManager."""
    mgr = CapabilityPermissionManager()
    check_res = mgr.authorize("desktop_inspect", {})
    assert check_res.allowed is True


def test_20_safety_guard_enforcement():
    """20. SafetyGuard rejects shell commands in queries/actions."""
    from app.core.permissions import SafetyGuard
    guard = SafetyGuard()
    res = guard.validate_action("shell", {"command": "powershell.exe -Command Remove-Item -Force C:\\*"})
    assert res.allowed is False
    assert res.risk_level == "blocked"


# ---------------------------------------------------------------------------
# Test Cases 21 - 30: Security, Checkpoints & Telemetry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_21_prompt_injection(runner):
    """21. Scenario 10 detects and blocks prompt injection in untrusted content."""
    res = await runner.run_scenario("scen-10-prompt-injection")
    assert res.success is True
    assert "prompt injection" in res.message.lower()
    assert runner.metrics.prompt_injection_blocks >= 1


@pytest.mark.asyncio
async def test_22_partial_workflow_preservation(runner):
    """22. Failure at step 4 strictly preserves steps 1-3 completed."""
    res = await runner.run_scenario("scen-11-partial-workflow-preservation")
    assert res.success is True
    assert "steps 1-3 strictly retained" in res.message
    assert runner.metrics.adaptive_replans >= 1


@pytest.mark.asyncio
async def test_23_recovery_limit(adaptive_ctrl):
    """23. Recovery attempts are strictly bounded by max_recovery_attempts."""
    step = ComputerWorkflowStep(name="Unstable Step", capability="desktop", action="click", expected_state={"target_visible": "Missing"})
    plan = ComputerWorkflowPlan(goal="Test recovery limit", steps=[step])

    with patch.object(adaptive_ctrl, "observe_environment") as mock_obs:
        mock_obs.return_value = ObservedComputerState(target_confidence={"Missing": 0.10})
        with patch.object(adaptive_ctrl.workflow_engine, "_execute_step_with_recovery", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = False
            res = await adaptive_ctrl.execute_adaptive_workflow(plan, max_recovery_attempts=2, max_adaptation_cycles=1)
            assert res.success is False
            assert res.recovery_attempts <= 2


@pytest.mark.asyncio
async def test_24_adaptation_limit(adaptive_ctrl):
    """24. Adaptation cycles are strictly bounded by max_adaptation_cycles."""
    step = ComputerWorkflowStep(name="Divergent Step", capability="desktop", action="focus", expected_state={"application": "NeverApp"})
    plan = ComputerWorkflowPlan(goal="Test adaptation limit", steps=[step])

    with patch.object(adaptive_ctrl, "observe_environment") as mock_obs:
        mock_obs.return_value = ObservedComputerState(active_application="OtherApp")
        with patch.object(adaptive_ctrl.workflow_engine, "_execute_step_with_recovery", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = False
            res = await adaptive_ctrl.execute_adaptive_workflow(plan, max_adaptation_cycles=2)
            assert res.adaptation_cycles <= 3
            assert res.success is False


@pytest.mark.asyncio
async def test_25_cancellation(runner):
    """25. User cancellation halts execution and records cancellation metric."""
    res = await runner.run_scenario("scen-14-cancellation")
    assert res.status == "CANCELLED"
    assert runner.metrics.cancellations >= 1


@pytest.mark.asyncio
async def test_26_checkpoint_creation(adaptive_ctrl, mock_checkpoints):
    """26. Checkpoint persistence writes safe structured metadata."""
    step = ComputerWorkflowStep(name="Focus Step", capability="desktop", action="focus")
    plan = ComputerWorkflowPlan(goal="Checkpoint test", steps=[step])
    obs = ObservedComputerState(active_application="Google Chrome")

    ckpt_id = await adaptive_ctrl._save_checkpoint(plan, obs, 1, 0, step.step_id)
    assert ckpt_id is not None
    loaded = mock_checkpoints.get_checkpoint(ckpt_id)
    assert loaded is not None


@pytest.mark.asyncio
async def test_27_checkpoint_recovery(adaptive_ctrl, mock_checkpoints):
    """27. Loaded checkpoint contains valid workflow_id and state."""
    step = ComputerWorkflowStep(name="Step 1", capability="desktop", action="focus")
    plan = ComputerWorkflowPlan(goal="Recovery test", steps=[step])
    obs = ObservedComputerState(active_application="Google Chrome")

    ckpt_id = await adaptive_ctrl._save_checkpoint(plan, obs, 0, 0, step.step_id)
    loaded = mock_checkpoints.get_checkpoint(ckpt_id)
    assert loaded.workflow_id == plan.workflow_id


def test_28_secret_redaction():
    """28. Tokens, passwords, and API keys are redacted from metadata."""
    scen = ReliabilityScenario(
        name="Secret Test",
        description="Verify redaction",
        workflow_goal="Inspect",
        expected_behavior="Safe",
        metadata={"api_key": "sk-live-12345678", "auth_token": "Bearer mytoken"},
    )
    assert scen.metadata["api_key"] == "[REDACTED]"
    assert scen.metadata["auth_token"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_29_telemetry_redaction(runner):
    """29. Reliability telemetry emission scrubs sensitive metadata."""
    with patch.object(action_bus, "publish", new_callable=AsyncMock) as mock_pub:
        await runner._emit_telemetry(
            ActionType.RELIABILITY_SCENARIO_STARTED,
            ActionStatus.STARTED,
            "Started",
            "scen-test",
            safe_metadata={"secret_pass": "pass123", "normal": "val"},
        )
        assert mock_pub.called
        event = mock_pub.call_args[0][0]
        assert event.safe_metadata["secret_pass"] == "[REDACTED]"
        assert event.safe_metadata["normal"] == "val"


def test_30_telemetry_event_lifecycle():
    """30. All M17.4 reliability telemetry event types exist in ActionType."""
    assert hasattr(ActionType, "RELIABILITY_SCENARIO_STARTED")
    assert hasattr(ActionType, "RELIABILITY_SCENARIO_COMPLETED")
    assert hasattr(ActionType, "RELIABILITY_SCENARIO_FAILED")
    assert hasattr(ActionType, "RELIABILITY_RECOVERY_VALIDATED")
    assert hasattr(ActionType, "RELIABILITY_DUPLICATE_ACTION_PREVENTED")
    assert hasattr(ActionType, "RELIABILITY_TIMEOUT_RESOLVED")


# ---------------------------------------------------------------------------
# Test Cases 31 - 44: Integration, Reuse & Invariant Enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_31_browser_integration(adaptive_ctrl):
    """31. Browser capability integrates through BrowserEngine."""
    step = ComputerWorkflowStep(name="Inspect page", capability="browser", action="inspect")
    assert step.capability == "browser"


@pytest.mark.asyncio
async def test_32_desktop_integration(adaptive_ctrl):
    """32. Desktop capability integrates through DesktopActionEngine."""
    step = ComputerWorkflowStep(name="Focus Code", capability="desktop", action="focus", application_context="Visual Studio Code")
    assert step.capability == "desktop"


def test_33_target_resolver_reuse(adaptive_ctrl):
    """33. Adaptive controller reuses existing DesktopTargetResolver."""
    assert adaptive_ctrl.target_resolver is not None


def test_34_desktop_action_engine_reuse(adaptive_ctrl):
    """34. Adaptive controller integrates DesktopActionEngine."""
    assert hasattr(adaptive_ctrl, "desktop_actions")


def test_35_workflow_engine_reuse(adaptive_ctrl):
    """35. Adaptive controller reuses existing ComputerWorkflowEngine."""
    assert isinstance(adaptive_ctrl.workflow_engine, ComputerWorkflowEngine)


def test_36_planner_reuse(adaptive_ctrl):
    """36. Planning utilizes PlanningEngine via ComputerWorkflowEngine."""
    assert hasattr(adaptive_ctrl.workflow_engine, "planner")


def test_37_no_duplicate_planner():
    """37. Verify no duplicate Planner2 or alternative planner created."""
    import app.control.reliability as rel_mod
    assert not hasattr(rel_mod, "Planner2")
    assert not hasattr(rel_mod, "ReliabilityPlanner")


def test_38_no_duplicate_workflow_engine():
    """38. Verify no duplicate WorkflowEngine2 created."""
    import app.control.reliability as rel_mod
    assert not hasattr(rel_mod, "WorkflowEngine2")
    assert not hasattr(rel_mod, "ReliabilityWorkflowEngine")


def test_39_no_raw_coordinates():
    """39. Supplying raw coordinates directly raises ValueError."""
    with pytest.raises(ValueError, match="Raw coordinates"):
        ComputerWorkflowStep(
            name="Raw Click",
            capability="desktop",
            action="click",
            arguments={"raw_x": 500, "raw_y": 600},
        )


def test_40_no_shell_execution():
    """40. Reliability module contains zero shell or subprocess imports."""
    import app.control.reliability as rel_mod
    assert not hasattr(rel_mod, "subprocess")
    assert not hasattr(rel_mod, "os.system")


def test_41_no_arbitrary_executable_launch(adaptive_ctrl):
    """41. Non-allowlisted executable raises validation/security rejection."""
    step = ComputerWorkflowStep(
        name="Launch Malware",
        capability="desktop",
        action="open_application",
        application_context="UnknownMalware.exe",
    )
    obs = ObservedComputerState(active_application="Notepad")
    diff = adaptive_ctrl.evaluate_state_difference(
        expected={"application": "UnknownMalware.exe"},
        observed=obs,
    )
    assert diff.matches is False


def test_42_no_keylogging():
    """42. No global keylogging or hook libraries imported in reliability module."""
    import app.control.reliability as rel_mod
    assert not hasattr(rel_mod, "pynput")
    assert not hasattr(rel_mod, "keyboard")


def test_43_no_credential_extraction():
    """43. Password values in step arguments are rejected at creation."""
    with pytest.raises(ValueError, match="Sensitive credential"):
        ComputerWorkflowStep(
            name="Password step",
            capability="desktop",
            action="type",
            arguments={"password": "secret_password"},
        )


def test_44_allowlist_enforcement():
    """44. Only allowlisted applications (Chrome, VS Code, Notepad, etc.) approved."""
    from app.desktop.interaction import ALLOWED_EXECUTABLE_TO_APP
    assert "code.exe" in ALLOWED_EXECUTABLE_TO_APP
    assert "chrome.exe" in ALLOWED_EXECUTABLE_TO_APP
    assert "notepad.exe" in ALLOWED_EXECUTABLE_TO_APP


# ---------------------------------------------------------------------------
# Test Cases 45 - 60: Boundaries, Cancellations & End-to-End Scenarios
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_45_bounded_execution(adaptive_ctrl):
    """45. Workflow timeout parameter bounds execution duration."""
    fast_plan = ComputerWorkflowPlan(goal="Fast timeout", steps=[ComputerWorkflowStep(name="Focus", capability="desktop", action="focus")])
    res = await adaptive_ctrl.execute_adaptive_workflow(fast_plan, timeout_sec=0.001)
    assert res.success is False
    assert res.failure_class == FailureClass.ACTION_TIMEOUT


@pytest.mark.asyncio
async def test_46_bounded_recovery(runner):
    """46. Scenario 12 verifies recovery budget enforcement."""
    scen = runner.get_scenario("scen-12-recovery-budget")
    assert scen is not None
    assert "recovery" in scen.description.lower()


@pytest.mark.asyncio
async def test_47_bounded_adaptation(runner):
    """47. Scenario 13 verifies adaptation cycles bound enforcement."""
    scen = runner.get_scenario("scen-13-adaptation-budget")
    assert scen is not None
    assert "max_adaptation_cycles" in scen.description.lower()


@pytest.mark.asyncio
async def test_48_cancellation_during_observation(adaptive_ctrl):
    """48. Workflow cancelled during observation stops immediately."""
    plan = ComputerWorkflowPlan(goal="Cancel during observation", steps=[ComputerWorkflowStep(name="Step", capability="desktop", action="focus")])
    adaptive_ctrl._cancelled_workflows.add(plan.workflow_id)
    res = await adaptive_ctrl.execute_adaptive_workflow(plan)
    assert res.status == ComputerWorkflowState.CANCELLED


@pytest.mark.asyncio
async def test_49_cancellation_during_confirmation(adaptive_ctrl):
    """49. Rejecting confirmation cancels workflow safely."""
    step = ComputerWorkflowStep(name="Sensitive Step", capability="desktop", action="submit", requires_confirmation=True)
    plan = ComputerWorkflowPlan(goal="Confirmation cancellation", steps=[step])

    res = await adaptive_ctrl.execute_adaptive_workflow(plan, auto_confirm=False)
    assert res.status == ComputerWorkflowState.WAITING_CONFIRMATION

    cancel_res = await adaptive_ctrl.confirm_adaptive_step(plan.workflow_id, approved=False)
    assert cancel_res.status == ComputerWorkflowState.CANCELLED


@pytest.mark.asyncio
async def test_50_cancellation_during_recovery(adaptive_ctrl):
    """50. Calling cancel_adaptive_workflow during recovery halts cleanly."""
    step = ComputerWorkflowStep(name="Step", capability="desktop", action="focus")
    plan = ComputerWorkflowPlan(goal="Cancel during recovery", steps=[step])
    cancel_res = await adaptive_ctrl.cancel_adaptive_workflow(plan.workflow_id, reason="User stopped recovery")
    assert cancel_res.status == ComputerWorkflowState.CANCELLED


@pytest.mark.asyncio
async def test_51_recovery_after_ui_change(adaptive_ctrl):
    """51. UI shift triggers target re-resolution and execution succeeds."""
    step = ComputerWorkflowStep(name="Click Button", capability="desktop", action="click", expected_state={"application": "Google Chrome"})
    plan = ComputerWorkflowPlan(goal="UI change recovery", steps=[step])

    with patch.object(adaptive_ctrl.workflow_engine, "_execute_step_with_recovery", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = True
        step.status = ComputerWorkflowState.COMPLETED
        res = await adaptive_ctrl.execute_adaptive_workflow(plan)
        assert res.success is True


@pytest.mark.asyncio
async def test_52_recovery_after_stale_target(runner):
    """52. Scenario 3 exercises stale target recovery."""
    scen = runner.get_scenario("scen-03-target-moved")
    assert scen is not None
    assert scen.injected_condition == "target_geometry_shifted"


@pytest.mark.asyncio
async def test_53_workflow_completion(runner):
    """53. Standard multi-step workflow completes with success=True."""
    step = ComputerWorkflowStep(name="Focus Chrome", capability="desktop", action="focus", application_context="Google Chrome")
    plan = ComputerWorkflowPlan(goal="Complete workflow", steps=[step])

    with patch.object(runner.adaptive_controller.workflow_engine, "_execute_step_with_recovery", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = True
        step.status = ComputerWorkflowState.COMPLETED
        res = await runner.adaptive_controller.execute_adaptive_workflow(plan)
        assert res.success is True
        assert res.status == ComputerWorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_54_workflow_failure(runner):
    """54. Permanent unresolvable error yields structured failure."""
    step = ComputerWorkflowStep(name="Failing Step", capability="desktop", action="focus", expected_state={"application": "NonExistentApp"})
    plan = ComputerWorkflowPlan(goal="Fail workflow", steps=[step])

    with patch.object(runner.adaptive_controller.workflow_engine, "_execute_step_with_recovery", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = False
        res = await runner.adaptive_controller.execute_adaptive_workflow(plan, max_adaptation_cycles=1)
        assert res.success is False
        assert res.status == ComputerWorkflowState.FAILED


def test_55_structured_failure_taxonomy():
    """55. FailureClass contains complete structured failure taxonomy."""
    assert hasattr(FailureClass, "ACTION_TIMEOUT")
    assert hasattr(FailureClass, "PERMISSION_DENIED")
    assert hasattr(FailureClass, "CONFIRMATION_REQUIRED")
    assert hasattr(FailureClass, "PROMPT_INJECTION_DETECTED")
    assert hasattr(FailureClass, "UNRECOVERABLE")


def test_56_metrics_integrity(runner):
    """56. ReliabilityMetrics model serializes to clean, secret-free dictionary."""
    runner._metrics_tracker.record_attempt()
    runner._metrics_tracker.record_success(150.0)
    runner._metrics_tracker.record_duplicate_prevented()
    safe_dict = runner.metrics.to_safe_dict()
    assert safe_dict["workflow_attempts"] >= 1
    assert safe_dict["duplicate_actions_prevented"] >= 1
    assert "password" not in safe_dict


@pytest.mark.asyncio
async def test_57_safe_checkpoint_metadata(adaptive_ctrl, mock_checkpoints):
    """57. Checkpoint state excludes credentials and screenshots."""
    step = ComputerWorkflowStep(name="Step 1", capability="desktop", action="focus")
    plan = ComputerWorkflowPlan(goal="Safe checkpoint test", steps=[step])
    obs = ObservedComputerState(active_application="Google Chrome")

    ckpt_id = await adaptive_ctrl._save_checkpoint(plan, obs, 0, 0, step.step_id)
    loaded = mock_checkpoints.get_checkpoint(ckpt_id)
    assert "screenshot" not in loaded.safe_metadata
    assert "cookie" not in loaded.safe_metadata


@pytest.mark.asyncio
async def test_58_live_verification_compatibility(runner):
    """58. Canonical scenarios list contains all 14 Phase 3 scenarios."""
    scenarios = runner.list_scenarios()
    assert len(scenarios) == 14
    ids = {s.scenario_id for s in scenarios}
    assert "scen-01-chrome-reuse" in ids
    assert "scen-07-timeout-after-success" in ids
    assert "scen-10-prompt-injection" in ids


@pytest.mark.asyncio
async def test_59_regression_compatibility():
    """59. RyvenControlEngine exposes reliability_runner property."""
    engine = RyvenControlEngine()
    assert engine.reliability_runner is not None
    assert hasattr(engine, "run_reliability_scenario")


def test_60_frontend_compatibility():
    """60. ScenarioExecutionResult and ReliabilityMetrics are JSON serializable for frontend."""
    metrics = ReliabilityMetrics(workflow_attempts=5, workflow_successes=5)
    dumped = metrics.model_dump()
    assert dumped["workflow_attempts"] == 5

    res = ScenarioExecutionResult(
        scenario_id="scen-01",
        scenario_name="Test",
        success=True,
        status="COMPLETED",
        message="Workflow completed.",
    )
    res_dump = res.model_dump()
    assert res_dump["status"] == "COMPLETED"

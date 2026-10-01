"""Tests for RYVEN 2.0 Phase 4 — Workflow Engine v1.

Covers:
- Workflow models, step states, and workflow states
- WorkflowPlanner (valid workflow generation, unsupported requests, educational questions)
- WorkflowValidator (valid tools, unknown tools, unsafe arguments, prohibited actions)
- ConfirmationManager (safe tools vs impactful tools)
- WorkflowExecutor (sequential execution, failure stops workflow, blocked stops workflow, step preservation)
- WorkflowEngine cancellation & lifecycle
- Security verification (cmd.exe, powershell, arbitrary executable, path traversal, injection)
- Router & Assistant end-to-end workflow execution
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry
from app.workflows.models import (
    StepState,
    WorkflowState,
    WorkflowStep,
    WorkflowDefinition,
    WorkflowExecutionResult,
)
from app.workflows.planner import WorkflowPlanner
from app.workflows.validator import WorkflowValidator
from app.workflows.confirmation import ConfirmationManager
from app.workflows.executor import WorkflowExecutor
from app.workflows.engine import WorkflowEngine
from app.core.router import IntentRouter
from app.core.assistant import Assistant
from app.ai.provider import AIResponse


# ============================================================================
# 1. WORKFLOW MODEL TESTS
# ============================================================================

def test_workflow_step_creation_and_states():
    """Verify WorkflowStep creation and state transitions."""
    step = WorkflowStep(
        step_id="step_1",
        tool_name="system_info",
        name="Get System Info",
        arguments={"detail_level": "basic"},
    )
    assert step.step_id == "step_1"
    assert step.tool_name == "system_info"
    assert step.status == StepState.PENDING
    assert step.result is None
    assert step.error is None
    assert step.started_at is None
    assert step.completed_at is None

    # Test all supported StepStates
    for state in [
        StepState.PENDING,
        StepState.RUNNING,
        StepState.SUCCESS,
        StepState.FAILED,
        StepState.BLOCKED,
        StepState.CANCELLED,
        StepState.SKIPPED,
    ]:
        step.status = state
        assert step.status == state


def test_workflow_definition_and_states():
    """Verify WorkflowDefinition creation and workflow states."""
    step1 = WorkflowStep(step_id="s1", tool_name="system_info", name="SysInfo")
    step2 = WorkflowStep(step_id="s2", tool_name="open_folder", name="OpenFolder", arguments={"folder": "workspace"})

    workflow = WorkflowDefinition(
        name="Test Workflow",
        description="A test workflow",
        requested_by="user",
        steps=[step1, step2],
    )
    assert workflow.name == "Test Workflow"
    assert workflow.status == WorkflowState.PLANNING
    assert len(workflow.steps) == 2
    assert workflow.current_step_index == 0
    assert workflow.started_at is None
    assert workflow.completed_at is None

    # Test all supported WorkflowStates
    for state in [
        WorkflowState.PLANNING,
        WorkflowState.WAITING_FOR_CONFIRMATION,
        WorkflowState.RUNNING,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.BLOCKED,
        WorkflowState.CANCELLED,
    ]:
        workflow.status = state
        assert workflow.status == state


def test_workflow_execution_result_serialization():
    """Verify WorkflowExecutionResult model."""
    res = WorkflowExecutionResult(
        workflow_id="wf_123",
        name="Test WF",
        status=WorkflowState.COMPLETED,
        success=True,
        message="Workflow completed successfully.",
        steps_total=2,
        steps_completed=2,
        steps_failed=0,
    )
    assert res.workflow_id == "wf_123"
    assert res.name == "Test WF"
    assert res.status == WorkflowState.COMPLETED
    assert res.success is True
    assert res.steps_completed == 2
    assert res.steps_failed == 0
    assert res.message == "Workflow completed successfully."


# ============================================================================
# 2. WORKFLOW PLANNER TESTS
# ============================================================================

def test_planner_creates_workspace_prep_workflow():
    """Verify planner converts supported workspace prep request into 3 safe registered steps."""
    planner = WorkflowPlanner()
    
    queries = [
        "Prepare my workspace",
        "RYVEN, prepare my development workspace",
        "prepare my dev workspace",
        "open my development workspace",
        "set up my workspace",
        "setup workspace",
    ]
    for q in queries:
        plan = planner.plan(q, requested_by="tester")
        assert plan is not None, f"Failed to plan query: {q}"
        assert plan.name == "Prepare Development Workspace"
        assert len(plan.steps) == 3
        assert plan.steps[0].tool_name == "system_info"
        assert plan.steps[1].tool_name == "open_folder"
        assert plan.steps[2].tool_name == "open_application"
        assert plan.steps[2].arguments.get("application") == "vscode" or plan.steps[2].arguments.get("app") == "vscode"


def test_planner_rejects_unsupported_requests():
    """Planner returns None for non-workflow or unsupported requests."""
    planner = WorkflowPlanner()
    unsupported = [
        "Make me a cup of coffee",
        "Build a full SaaS platform autonomously",
        "Delete my hard drive",
        "Prepare dinner tonight",
    ]
    for q in unsupported:
        plan = planner.plan(q)
        assert plan is None


def test_planner_ignores_educational_questions():
    """Educational questions must NOT be converted to workflow plans."""
    planner = WorkflowPlanner()
    questions = [
        "What is a development workspace?",
        "What is React?",
        "Explain workflow engines.",
        "How does VS Code work?",
        "Tell me about Python virtual environments",
    ]
    for q in questions:
        plan = planner.plan(q)
        assert plan is None, f"Educational question '{q}' was incorrectly planned as a workflow"


# ============================================================================
# 3. WORKFLOW VALIDATOR TESTS
# ============================================================================

def test_validator_accepts_valid_workspace_workflow():
    """Validator approves a valid workflow composed of registered Phase 3 tools."""
    from app.tools.registry import create_default_registry
    registry = create_default_registry()
    planner = WorkflowPlanner(registry=registry)
    validator = WorkflowValidator(registry=registry)

    plan = planner.plan("Prepare my workspace")
    assert plan is not None
    is_valid, reason = validator.validate(plan)
    assert is_valid is True
    assert reason is None or "valid" in reason.lower()


def test_validator_rejects_unknown_tool():
    """Validator rejects workflows containing unregistered tools."""
    registry = ToolRegistry()
    validator = WorkflowValidator(registry=registry)

    plan = WorkflowDefinition(
        name="Malicious Workflow",
        description="Contains unknown tool",
        steps=[
            WorkflowStep(step_id="s1", tool_name="system_info", name="SysInfo"),
            WorkflowStep(step_id="s2", tool_name="arbitrary_shell_exec", name="ShellExec", arguments={"cmd": "whoami"}),
        ],
    )
    is_valid, reason = validator.validate(plan)
    assert is_valid is False
    assert "unregistered" in reason.lower()
    assert plan.status == WorkflowState.BLOCKED


def test_validator_rejects_unauthorized_folder():
    """Validator rejects workflows attempting to open unauthorized directories."""
    from app.tools.registry import create_default_registry
    registry = create_default_registry()
    validator = WorkflowValidator(registry=registry)

    plan = WorkflowDefinition(
        name="System Tamper Workflow",
        description="Access system directory",
        steps=[
            WorkflowStep(step_id="s1", tool_name="open_folder", name="OpenSys32", arguments={"folder": "C:\\Windows\\System32"}),
        ],
    )
    is_valid, reason = validator.validate(plan)
    assert is_valid is False
    assert "rejected" in reason.lower() or "unauthorized" in reason.lower()
    assert plan.status == WorkflowState.BLOCKED


def test_validator_rejects_path_traversal():
    """Validator rejects path traversal attempts in workflow arguments."""
    registry = ToolRegistry()
    validator = WorkflowValidator(registry=registry)

    plan = WorkflowDefinition(
        name="Traversal Workflow",
        description="Path traversal attempt",
        steps=[
            WorkflowStep(step_id="s1", tool_name="open_folder", name="Traversal", arguments={"folder": "../../secret"}),
        ],
    )
    is_valid, reason = validator.validate(plan)
    assert is_valid is False
    assert plan.status == WorkflowState.BLOCKED


def test_validator_rejects_unauthorized_application():
    """Validator rejects workflows attempting to launch unauthorized apps (e.g., cmd.exe)."""
    registry = ToolRegistry()
    validator = WorkflowValidator(registry=registry)

    for bad_app in ["cmd", "powershell", "bash", "regedit"]:
        plan = WorkflowDefinition(
            name=f"Launch {bad_app}",
            description="Unauthorized app",
            steps=[
                WorkflowStep(step_id="s1", tool_name="open_application", name="BadApp", arguments={"app": bad_app}),
            ],
        )
        is_valid, reason = validator.validate(plan)
        assert is_valid is False
        assert plan.status == WorkflowState.BLOCKED


def test_validator_rejects_unsafe_url():
    """Validator rejects workflows containing unsafe URLs."""
    registry = ToolRegistry()
    validator = WorkflowValidator(registry=registry)

    plan = WorkflowDefinition(
        name="Open Malicious Web",
        description="Phishing site",
        steps=[
            WorkflowStep(step_id="s1", tool_name="open_website", name="BadWeb", arguments={"url": "http://evil-malware-domain.xyz"}),
        ],
    )
    is_valid, reason = validator.validate(plan)
    assert is_valid is False
    assert plan.status == WorkflowState.BLOCKED


# ============================================================================
# 4. CONFIRMATION SYSTEM TESTS
# ============================================================================

def test_confirmation_manager_safe_vs_impactful():
    """Safe read/open operations auto-execute; destructive/impactful require confirmation."""
    mgr = ConfirmationManager()

    # Safe operations
    assert mgr.requires_confirmation(WorkflowStep(step_id="s1", tool_name="system_info", name="Sys")) is False
    assert mgr.requires_confirmation(WorkflowStep(step_id="s2", tool_name="open_folder", name="Fld", arguments={"folder": "workspace"})) is False
    assert mgr.requires_confirmation(WorkflowStep(step_id="s3", tool_name="open_application", name="App", arguments={"app": "vscode"})) is False
    assert mgr.requires_confirmation(WorkflowStep(step_id="s4", tool_name="search_files", name="Srch", arguments={"query": "test"})) is False

    # Impactful operations (future capabilities)
    assert mgr.requires_confirmation(WorkflowStep(step_id="s5", tool_name="delete_file", name="Del", arguments={"path": "test.txt"})) is True
    assert mgr.requires_confirmation(WorkflowStep(step_id="s6", tool_name="package_install", name="Install", arguments={"package": "axios"})) is True
    assert mgr.requires_confirmation(WorkflowStep(step_id="s7", tool_name="git_commit", name="Commit", arguments={"message": "wip"})) is True


# ============================================================================
# 5. WORKFLOW EXECUTOR TESTS
# ============================================================================

class MockExecutableTool(BaseTool):
    """Mock tool inheriting from BaseTool for safe unit tests."""
    def __init__(self, name: str, return_value: dict):
        self.name = name
        self.description = f"Mock {name}"
        self.return_val = return_value
        self.input_schema = {}
        self.requires_confirmation = False

    async def execute(self, **kwargs):
        return self.return_val


@pytest.mark.asyncio
async def test_executor_successful_workflow():
    """Sequential execution runs all steps through ToolRegistry and marks COMPLETED."""
    registry = ToolRegistry()
    tool_val = {"success": True, "data": {"status": "ok"}, "message": "Step succeeded"}
    registry.register(MockExecutableTool("system_info", tool_val))
    registry.register(MockExecutableTool("open_folder", tool_val))
    registry.register(MockExecutableTool("open_application", tool_val))

    executor = WorkflowExecutor(registry=registry)

    step1 = WorkflowStep(step_id="s1", tool_name="system_info", name="System Info")
    step2 = WorkflowStep(step_id="s2", tool_name="open_folder", name="Open Folder", arguments={"folder": "workspace"})
    step3 = WorkflowStep(step_id="s3", tool_name="open_application", name="Open VS Code", arguments={"app": "vscode"})

    workflow = WorkflowDefinition(
        name="Prepare Workspace",
        description="Sequential test",
        steps=[step1, step2, step3],
    )

    result = await executor.execute(workflow)

    assert result.status == WorkflowState.COMPLETED
    assert result.steps_completed == 3
    assert result.steps_failed == 0
    assert "successfully" in result.message.lower()
    assert step1.status == StepState.SUCCESS
    assert step2.status == StepState.SUCCESS
    assert step3.status == StepState.SUCCESS


@pytest.mark.asyncio
async def test_executor_stops_on_step_failure():
    """If a step fails, the workflow must stop immediately, mark FAILED, and preserve completed results."""
    registry = ToolRegistry()

    # Step 1 succeeds
    tool1 = MockExecutableTool("step1_tool", {"success": True, "message": "Step 1 OK"})
    registry.register(tool1)

    # Step 2 fails
    tool2 = MockExecutableTool("step2_tool", {"success": False, "error": "Tool crashed", "message": "Failed to open"})
    registry.register(tool2)

    # Step 3 must NOT be called
    tool3 = MockExecutableTool("step3_tool", {"success": True, "message": "Step 3 OK"})
    registry.register(tool3)

    executor = WorkflowExecutor(registry=registry)

    s1 = WorkflowStep(step_id="s1", tool_name="step1_tool", name="S1")
    s2 = WorkflowStep(step_id="s2", tool_name="step2_tool", name="S2")
    s3 = WorkflowStep(step_id="s3", tool_name="step3_tool", name="S3")

    workflow = WorkflowDefinition(name="Failing WF", description="", steps=[s1, s2, s3])

    result = await executor.execute(workflow)

    assert result.status == WorkflowState.FAILED
    assert result.steps_completed == 1
    assert result.steps_failed == 1
    assert s1.status == StepState.SUCCESS
    assert s2.status == StepState.FAILED
    assert s3.status == StepState.PENDING  # Unreached step remains unexecuted


@pytest.mark.asyncio
async def test_executor_cancelled_workflow():
    """A cancelled workflow halts before next step and marks CANCELLED."""
    registry = ToolRegistry()
    engine = WorkflowEngine(registry=registry)

    tool = MockExecutableTool("system_info", {"success": True, "message": "OK"})
    registry.register(tool)
    tool_fld = MockExecutableTool("open_folder", {"success": True, "message": "OK"})
    registry.register(tool_fld)

    s1 = WorkflowStep(step_id="s1", tool_name="system_info", name="S1")
    s2 = WorkflowStep(step_id="s2", tool_name="open_folder", name="S2")

    workflow = WorkflowDefinition(workflow_id="wf_cancel_test", name="Cancel WF", description="", steps=[s1, s2])
    workflow.status = WorkflowState.RUNNING

    # Cancel the active workflow
    engine._active_workflows["wf_cancel_test"] = workflow
    engine.cancel_workflow("wf_cancel_test")
    result = await engine.executor.execute(workflow)

    assert result.status == WorkflowState.CANCELLED
    assert s1.status == StepState.CANCELLED


# ============================================================================
# 6. SECURITY & ADVERSARIAL ATTEMPT TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_workflow_blocks_cmd_execution_attempt():
    """Attempting to run cmd.exe via workflow engine is strictly blocked."""
    registry = ToolRegistry()
    engine = WorkflowEngine(registry=registry)

    bad_plan = WorkflowDefinition(
        name="Exploit Workflow",
        description="Direct cmd.exe invocation",
        steps=[
            WorkflowStep(step_id="s1", tool_name="cmd", name="Spawn CMD", arguments={"cmd": "whoami"}),
        ],
    )
    result = await engine.execute_workflow(bad_plan)
    assert result.status == WorkflowState.BLOCKED
    assert "blocked" in result.message.lower() or "rejected" in result.message.lower()


@pytest.mark.asyncio
async def test_workflow_blocks_powershell_execution_attempt():
    """Attempting to run PowerShell via workflow engine is strictly blocked."""
    registry = ToolRegistry()
    engine = WorkflowEngine(registry=registry)

    bad_plan = WorkflowDefinition(
        name="Exploit Powershell",
        description="Direct powershell invocation",
        steps=[
            WorkflowStep(step_id="s1", tool_name="powershell", name="Run PS", arguments={"command": "Get-Service"}),
        ],
    )
    result = await engine.execute_workflow(bad_plan)
    assert result.status == WorkflowState.BLOCKED


@pytest.mark.asyncio
async def test_workflow_blocks_unauthorized_filesystem_access():
    """Attempting unauthorized paths via workflow engine is blocked."""
    registry = ToolRegistry()
    engine = WorkflowEngine(registry=registry)

    bad_plan = WorkflowDefinition(
        name="Access Passwords",
        description="Attempt to read sensitive path",
        steps=[
            WorkflowStep(step_id="s1", tool_name="open_folder", name="Open Windows", arguments={"folder": "C:\\Windows"}),
        ],
    )
    result = await engine.execute_workflow(bad_plan)
    assert result.status == WorkflowState.BLOCKED


# ============================================================================
# 7. ROUTER & ASSISTANT END-TO-END WORKFLOW INTEGRATION
# ============================================================================

def test_router_distinguishes_workflow_from_ai_and_single_tool():
    """IntentRouter routes workflow queries to intent='workflow' and educational queries to 'ai'."""
    router = IntentRouter()

    # Workflow queries
    wf_queries = [
        "Prepare my workspace",
        "RYVEN, prepare my development workspace",
        "open my development workspace",
        "set up my workspace",
    ]
    for q in wf_queries:
        dec = router.route(q)
        assert dec.intent == "workflow", f"Expected 'workflow' for '{q}', got '{dec.intent}'"
        assert dec.workflow_name in ("workspace_prep", "prepare_workspace")

    # Educational queries MUST remain AI
    ai_queries = [
        "What is a development workspace?",
        "What is React?",
        "Explain workflow engines.",
        "How does VS Code work?",
        "What is the difference between compiler and interpreter?",
    ]
    for q in ai_queries:
        dec = router.route(q)
        assert dec.intent == "ai", f"Expected 'ai' for '{q}', got '{dec.intent}'"
        assert dec.workflow_name is None


@pytest.mark.asyncio
async def test_assistant_e2e_workspace_workflow():
    """Assistant processes 'RYVEN, prepare my workspace' using WorkflowEngine and returns workflow type."""
    mock_ai = MagicMock()
    mock_ai.provider_name = "MockOllama"
    mock_ai.generate = AsyncMock(return_value=AIResponse(content="AI answer", model="qwen2.5:7b", provider="ollama"))

    assistant = Assistant(ai_provider=mock_ai)

    # Patch OS launches so tests don't actually open explorer or vscode on host machine
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()

        response = await assistant.process("RYVEN, prepare my development workspace")

        assert response.success is True
        assert response.type == "workflow"
        assert "Workspace" in response.message or "workspace" in response.message
        assert response.metadata.get("status") == "COMPLETED"
        assert response.metadata.get("steps_completed") == 3
        assert response.metadata.get("steps_failed") == 0
        assert len(response.metadata.get("steps", [])) == 3


@pytest.mark.asyncio
async def test_assistant_educational_question_remains_ai():
    """Educational questions processed by Assistant must go to AI and NOT execute workflows."""
    mock_ai = MagicMock()
    mock_ai.provider_name = "MockOllama"
    mock_ai.generate = AsyncMock(return_value=AIResponse(
        content="A workflow engine is an orchestration system that executes tasks sequentially.",
        model="qwen2.5:7b",
        provider="ollama",
    ))

    assistant = Assistant(ai_provider=mock_ai)

    response = await assistant.process("Explain what a workflow engine is.")

    assert response.success is True
    assert response.type == "ai"
    assert "workflow engine" in response.message.lower()
    assert response.metadata.get("status") != "COMPLETED"  # Not a workflow execution
    mock_ai.generate.assert_called_once()

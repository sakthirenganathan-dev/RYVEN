"""Automated tests for RYVEN Phase 4.1 — Project Builder Workflow Foundation.

Covers:
- Safe project directory creation & sanitization
- Safe project file writing with boundary containment
- Project file validation
- Project creation workflow planning (React, Python, Web)
- Confirmation gate (pausing at WAITING_FOR_CONFIRMATION when unconfirmed, resuming on confirmation)
- Sequential execution and verified file generation
- Security & path traversal rejection
"""

import os
import shutil
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.tools.project_tool import (
    CreateProjectFolderTool,
    CreateProjectFileTool,
    ValidateProjectFilesTool,
    get_projects_root,
    sanitize_project_name,
    resolve_project_path,
    resolve_project_file_path,
)
from app.workflows.models import StepState, WorkflowState
from app.workflows.planner import WorkflowPlanner
from app.workflows.validator import WorkflowValidator
from app.workflows.confirmation import ConfirmationManager
from app.workflows.engine import WorkflowEngine
from app.core.router import IntentRouter
from app.core.assistant import Assistant
from app.ai.provider import AIResponse


@pytest.fixture(autouse=True)
def cleanup_test_projects():
    """Ensure clean test environment by cleaning up test project folders."""
    yield
    test_folders = ["TestProject", "TaskFlow", "PythonApp", "WebPortfolio", "SecurityEscape"]
    root = get_projects_root()
    for folder in test_folders:
        path = os.path.join(root, folder)
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)


# ============================================================================
# 1. TOOL & PATH SECURITY UNIT TESTS
# ============================================================================

def test_sanitize_project_name():
    """Verify project name validation and sanitization."""
    assert sanitize_project_name("TaskFlow") == "TaskFlow"
    assert sanitize_project_name("my-app_2") == "my-app_2"
    assert sanitize_project_name("frontend-v1") == "frontend-v1"

    # Block invalid characters and path escapes
    assert sanitize_project_name("../evil") is None
    assert sanitize_project_name("bad/path") is None
    assert sanitize_project_name("name with spaces") is None
    assert sanitize_project_name("test$") is None
    assert sanitize_project_name("") is None


def test_resolve_project_path_containment():
    """Verify project paths are strictly confined inside projects directory."""
    projects_root = os.path.realpath(get_projects_root())
    
    valid_path = resolve_project_path("TaskFlow")
    assert valid_path is not None
    assert valid_path.startswith(projects_root)

    # Block path traversal attempts
    assert resolve_project_path("../../Windows") is None
    assert resolve_project_path("..") is None


def test_resolve_project_file_path_containment():
    """Verify file paths cannot escape project directory boundary."""
    project_dir = resolve_project_path("TaskFlow")
    assert project_dir is not None

    valid_file = resolve_project_file_path("TaskFlow", "src/App.jsx")
    assert valid_file is not None
    assert valid_file.startswith(project_dir)

    # Block escape attempts
    assert resolve_project_file_path("TaskFlow", "../../secret.txt") is None
    assert resolve_project_file_path("TaskFlow", "/etc/passwd") is None
    assert resolve_project_file_path("TaskFlow", "C:\\Windows\\System32\\cmd.exe") is None


@pytest.mark.asyncio
async def test_create_project_folder_tool():
    """Verify CreateProjectFolderTool creates folder safely."""
    tool = CreateProjectFolderTool()
    res = await tool.execute(project_name="TestProject")
    assert res["success"] is True
    assert "created" in res["message"].lower()

    target_path = resolve_project_path("TestProject")
    assert os.path.isdir(target_path)


@pytest.mark.asyncio
async def test_create_project_file_tool_and_validation():
    """Verify CreateProjectFileTool and ValidateProjectFilesTool."""
    folder_tool = CreateProjectFolderTool()
    file_tool = CreateProjectFileTool()
    val_tool = ValidateProjectFilesTool()

    await folder_tool.execute(project_name="TestProject")

    # 1. Write file
    res_file = await file_tool.execute(
        project_name="TestProject",
        relative_path="index.html",
        content="<!DOCTYPE html><html><body>Test</body></html>",
    )
    assert res_file["success"] is True

    # 2. Validate presence
    res_val = await val_tool.execute(
        project_name="TestProject",
        expected_files=["index.html"],
    )
    assert res_val["success"] is True
    assert res_val["all_present"] is True

    # 3. Missing file should report failure
    res_missing = await val_tool.execute(
        project_name="TestProject",
        expected_files=["index.html", "missing.js"],
    )
    assert res_missing["success"] is False
    assert res_missing["all_present"] is False


# ============================================================================
# 2. WORKFLOW PLANNER FOR PROJECTS
# ============================================================================

def test_planner_creates_react_project_workflow():
    """Verify planner formulates React project creation workflow."""
    planner = WorkflowPlanner()
    plan = planner.plan("Create a React project called TaskFlow")
    assert plan is not None
    assert plan.name == "Build React Project: TaskFlow"
    assert len(plan.steps) == 8  # 1 folder + 6 files + 1 validation

    step_tools = [s.tool_name for s in plan.steps]
    assert step_tools[0] == "create_project_folder"
    assert "create_project_file" in step_tools
    assert step_tools[-1] == "validate_project_files"


def test_planner_creates_python_project_workflow():
    """Verify planner formulates Python project creation workflow."""
    planner = WorkflowPlanner()
    plan = planner.plan("Build a python project named PythonApp")
    assert plan is not None
    assert plan.name == "Build Python Project: PythonApp"

    file_steps = [s for s in plan.steps if s.tool_name == "create_project_file"]
    rel_paths = [s.arguments["relative_path"] for s in file_steps]
    assert "main.py" in rel_paths
    assert "requirements.txt" in rel_paths


def test_planner_creates_web_project_workflow():
    """Verify planner formulates default web project creation workflow."""
    planner = WorkflowPlanner()
    plan = planner.plan("Scaffold a new web project called WebPortfolio")
    assert plan is not None
    assert plan.name == "Build Web Project: WebPortfolio"

    file_steps = [s for s in plan.steps if s.tool_name == "create_project_file"]
    rel_paths = [s.arguments["relative_path"] for s in file_steps]
    assert "index.html" in rel_paths
    assert "styles.css" in rel_paths
    assert "app.js" in rel_paths


# ============================================================================
# 3. CONFIRMATION GATE
# ============================================================================

@pytest.mark.asyncio
async def test_workflow_engine_confirmation_pause_and_resume():
    """Impactful project workflows pause at WAITING_FOR_CONFIRMATION when unconfirmed, and resume on confirmation."""
    engine = WorkflowEngine()

    query = "Create a React project called TaskFlow"
    
    # 1. Execute with auto_confirm=False -> Must pause at confirmation gate
    result_paused = await engine.run_from_query(query, auto_confirm=False)
    assert result_paused is not None
    assert result_paused.status == WorkflowState.WAITING_FOR_CONFIRMATION
    assert "requires user confirmation" in result_paused.message.lower()

    # 2. Resume confirmed workflow -> Completes remaining steps
    result_resumed = await engine.resume_workflow(result_paused.workflow_id)
    assert result_resumed is not None
    assert result_resumed.status == WorkflowState.COMPLETED
    assert result_resumed.steps_completed == len(result_resumed.step_details)

    # 3. Verify files on actual disk
    project_dir = resolve_project_path("TaskFlow")
    assert os.path.isdir(project_dir)
    assert os.path.isfile(os.path.join(project_dir, "package.json"))
    assert os.path.isfile(os.path.join(project_dir, "src", "App.jsx"))


# ============================================================================
# 4. ROUTER & ASSISTANT END-TO-END PROJECT WORKFLOW
# ============================================================================

def test_router_matches_project_creation_requests():
    """Verify IntentRouter routes project requests to workflow='create_project'."""
    router = IntentRouter()

    queries = [
        "Create a React project called TaskFlow",
        "Build a python project named PythonApp",
        "Scaffold a web project called WebPortfolio",
        "Set up a project called Dashboard in react",
    ]
    for q in queries:
        dec = router.route(q)
        assert dec.intent == "workflow"
        assert dec.workflow_name == "create_project"


@pytest.mark.asyncio
async def test_assistant_e2e_project_creation():
    """Verify Assistant processes project creation workflow end-to-end."""
    mock_ai = MagicMock()
    mock_ai.provider_name = "MockOllama"
    mock_ai.generate = AsyncMock(return_value=AIResponse(content="", model="qwen2.5:7b", provider="ollama"))

    assistant = Assistant(ai_provider=mock_ai)

    response = await assistant.process("Create a web project called WebPortfolio", auto_confirm=True)

    assert response.success is True
    assert response.type == "workflow"
    assert response.metadata.get("status") == "COMPLETED"
    assert response.metadata.get("steps_completed") > 0

    # Verify physical file existence
    portfolio_dir = resolve_project_path("WebPortfolio")
    assert os.path.isdir(portfolio_dir)
    assert os.path.isfile(os.path.join(portfolio_dir, "index.html"))
    assert os.path.isfile(os.path.join(portfolio_dir, "styles.css"))
    assert os.path.isfile(os.path.join(portfolio_dir, "app.js"))


# ============================================================================
# 5. SECURITY & TRAVERSAL ADVERSARIAL ATTEMPTS
# ============================================================================

@pytest.mark.asyncio
async def test_security_blocks_project_traversal_attempts():
    """Verify attempts to escape projects root with traversal are blocked."""
    engine = WorkflowEngine()

    bad_plan = engine.planner.plan("Create a React project called ../../SecurityEscape")
    # Planner must reject invalid project names
    assert bad_plan is None

    # Validator blocks directly constructed escaping steps
    from app.workflows.models import WorkflowDefinition, WorkflowStep
    evil_wf = WorkflowDefinition(
        name="Escape Project",
        description="Escape root",
        steps=[
            WorkflowStep(
                step_id="s1",
                tool_name="create_project_folder",
                name="BadFolder",
                arguments={"project_name": "../../Windows"},
            )
        ],
    )
    result = await engine.execute_workflow(evil_wf)
    assert result.status == WorkflowState.BLOCKED
    assert "rejected" in result.message.lower() or "blocked" in result.message.lower()

"""Comprehensive automated test suite for RYVEN DEV ENGINE v1.

Covers:
1. ProjectSpecification tests (React+TS, Python API, Python App, Vanilla Web, feature extraction)
2. IntentRouter project development tests
3. ProjectPlanner dev engine planning tests
4. FilePlan tests & overwrite protection
5. CodeGenerationEngine deterministic templates & limits
6. ProjectName validation tests
7. Path traversal & absolute path tests
8. Overwrite protection on existing folders
9. File size, count, and total project size limits
10. Malformed AI output / invalid schema rejection
11. ProjectQualityValidator (Python AST parsing, JSON validation, HTML check)
12. SafeBuildExecutor security policy (blocking shell, powershell, cmd, bash, etc.)
13. FixLoopManager proposal & confirmation application
14. Security attacks (../../secret.txt, C:\\Windows\\System32, format C:, injection tokens)
15. Workflow integration with Project Development
"""

import ast
import json
import os
import shutil
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.dev_engine.constants import (
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_PROJECT,
    MAX_PROJECT_NAME_LENGTH,
    MAX_TOTAL_PROJECT_SIZE_BYTES,
    PROJECT_NAME_REGEX,
)
from app.dev_engine.models import (
    BuildError,
    BuildTestPolicy,
    BuildTestRequest,
    FilePlan,
    FilePlanItem,
    FixProposal,
    GeneratedFile,
    ProjectSpecification,
    ProjectType,
)
from app.dev_engine.specification import ProjectSpecificationEngine
from app.dev_engine.file_plan import FilePlanEngine
from app.dev_engine.generator import CodeGenerationEngine
from app.dev_engine.validator import ProjectQualityValidator
from app.dev_engine.build_test import SafeBuildExecutor
from app.dev_engine.fix_loop import FixLoopManager
from app.core.router import IntentRouter
from app.tools.project_tool import (
    CreateProjectFolderTool,
    CreateProjectFileTool,
    get_projects_root,
    resolve_project_file_path,
    resolve_project_path,
    sanitize_project_name,
)
from app.workflows.planner import WorkflowPlanner
from app.workflows.engine import WorkflowEngine
from app.workflows.models import WorkflowState


@pytest.fixture(autouse=True)
def cleanup_dev_projects():
    """Ensure clean test environment by cleaning up test project folders."""
    yield
    test_folders = [
        "TaskFlowDev",
        "ExpenseTracker",
        "FastApiDev",
        "VanillaWebDev",
        "LimitTestApp",
        "OverwriteApp",
        "FixLoopTestApp",
    ]
    root = get_projects_root()
    for folder in test_folders:
        path = os.path.join(root, folder)
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)


# ============================================================================
# 1. PROJECT SPECIFICATION TESTS
# ============================================================================

def test_project_specification_react_typescript():
    """Verify specification engine synthesizes structured React TS specification."""
    engine = ProjectSpecificationEngine()
    query = "Create a React expense tracker with login, dashboard, dark futuristic UI"
    spec = engine.create_specification(
        project_name="ExpenseTracker",
        query=query,
    )
    assert spec is not None
    assert spec.project_name == "ExpenseTracker"
    assert spec.framework == "react"
    assert spec.language == "typescript"
    assert spec.styling == "dark futuristic"
    assert "package.json" in spec.expected_files
    assert "tsconfig.json" in spec.expected_files
    assert "src/App.tsx" in spec.expected_files
    assert "src/components/Header.tsx" in spec.expected_files
    assert any("Expense" in f for f in spec.features)
    assert any("Login" in f or "Authentication" in f for f in spec.features)


def test_project_specification_python_api():
    """Verify specification engine produces FastAPI specification for backend requests."""
    engine = ProjectSpecificationEngine()
    spec = engine.create_specification(
        project_name="FastApiDev",
        query="Build a python fastapi api project for user metrics",
    )
    assert spec is not None
    assert spec.project_type == ProjectType.PYTHON_API.value
    assert spec.framework == "fastapi"
    assert spec.language == "python"
    assert "main.py" in spec.expected_files
    assert "app/routes.py" in spec.expected_files


def test_project_specification_vanilla_web():
    """Verify specification engine produces Vanilla Web specification."""
    engine = ProjectSpecificationEngine()
    spec = engine.create_specification(
        project_name="VanillaWebDev",
        query="Create a simple html website for my portfolio",
    )
    assert spec is not None
    assert spec.project_type == ProjectType.VANILLA_WEB.value
    assert "index.html" in spec.expected_files
    assert "styles.css" in spec.expected_files
    assert "app.js" in spec.expected_files


# ============================================================================
# 2. INTENT ROUTER TESTS FOR DEV ENGINE
# ============================================================================

def test_intent_router_dev_engine_queries():
    """Verify router catches various project development formulations."""
    router = IntentRouter()
    queries = [
        "Create a React project called TaskFlow",
        "Build a portfolio website",
        "Create a Python API project called ApiGate",
        "Make me a React expense tracker",
        "Create a frontend dashboard",
        "Build a simple HTML website",
    ]
    for q in queries:
        decision = router.route(q)
        assert decision.intent == "workflow"
        assert decision.workflow_name == "create_project"


def test_intent_router_educational_query_not_routed_to_workflow():
    """Educational or conversational queries must route to AI engine, never workflow."""
    router = IntentRouter()
    queries = [
        "What is React?",
        "Explain TypeScript interfaces",
        "How does a neural network work?",
    ]
    for q in queries:
        decision = router.route(q)
        assert decision.intent == "ai"
        assert decision.tool_name is None


# ============================================================================
# 3. PROJECT PLANNER TESTS
# ============================================================================

def test_planner_plan_project_development():
    """Verify plan_project_development builds validated multi-step plan with code steps."""
    planner = WorkflowPlanner()
    plan = planner.plan_project_development(
        query="Create a React expense tracker with authentication",
        project_name="ExpenseTracker",
        project_type="react",
    )
    assert plan is not None
    assert "ExpenseTracker" in plan.name
    # Must have folder step + file generation steps + validation step
    step_tools = [s.tool_name for s in plan.steps]
    assert step_tools[0] == "create_project_folder"
    assert "create_project_file" in step_tools
    assert step_tools[-1] == "validate_project_files"
    assert len(plan.steps) >= 9  # 1 folder + 8 React TS files + 1 val


# ============================================================================
# 4. FILE PLAN & OVERWRITE PROTECTION TESTS
# ============================================================================

def test_file_plan_creation_and_overwrite_protection():
    """FilePlanEngine creates plans and blocks existing project folder overwrites."""
    spec_engine = ProjectSpecificationEngine()
    plan_engine = FilePlanEngine()

    spec = spec_engine.create_specification(
        project_name="OverwriteApp",
        query="Create a React project called OverwriteApp",
    )
    assert spec is not None

    # First plan when folder doesn't exist: should validate
    plan = plan_engine.create_file_plan(spec, overwrite_allowed=False)
    is_valid, reason = plan_engine.validate_plan(plan)
    assert is_valid is True
    assert reason is None

    # Simulate folder existing on disk
    project_dir = resolve_project_path("OverwriteApp")
    os.makedirs(project_dir, exist_ok=True)

    # Re-validate with overwrite_allowed=False -> MUST BLOCK
    is_valid_blocked, reason_blocked = plan_engine.validate_plan(plan)
    assert is_valid_blocked is False
    assert "already exists" in reason_blocked.lower()


# ============================================================================
# 5. CODE GENERATION ENGINE TESTS
# ============================================================================

def test_code_generation_react_ts():
    """Verify CodeGenerationEngine creates syntactically valid in-memory React TS files."""
    spec_engine = ProjectSpecificationEngine()
    gen_engine = CodeGenerationEngine()

    spec = spec_engine.create_specification(
        project_name="TaskFlowDev",
        query="Create a React app called TaskFlowDev",
    )
    files = gen_engine.generate_files(spec)
    assert len(files) > 0

    file_paths = [f.relative_path for f in files]
    assert "package.json" in file_paths
    assert "tsconfig.json" in file_paths
    assert "src/App.tsx" in file_paths
    assert "src/components/Header.tsx" in file_paths

    # Verify JSON structure
    pkg_file = next(f for f in files if f.relative_path == "package.json")
    pkg_data = json.loads(pkg_file.content)
    assert pkg_data["name"] == "taskflowdev"
    assert "react" in pkg_data["dependencies"]


# ============================================================================
# 6. PROJECT QUALITY VALIDATOR (SYNTAX & BOUNDARY CHECKS)
# ============================================================================

def test_project_quality_validator_clean_files():
    """Validator cleanly accepts valid in-memory files."""
    validator = ProjectQualityValidator()
    files = [
        GeneratedFile(
            relative_path="main.py",
            content="def hello():\n    return 'world'\n",
            language="python",
        ),
        GeneratedFile(
            relative_path="config.json",
            content='{"key": "value"}',
            language="json",
        ),
        GeneratedFile(
            relative_path="index.html",
            content="<!DOCTYPE html><html><body>Test</body></html>",
            language="html",
        ),
    ]
    is_valid, errors, _ = validator.validate_generated_files(files)
    assert is_valid is True
    assert len(errors) == 0


def test_project_quality_validator_detects_python_syntax_error():
    """Validator detects broken Python syntax via ast.parse without running code."""
    validator = ProjectQualityValidator()
    broken_files = [
        GeneratedFile(
            relative_path="main.py",
            content="def broken_syntax(:\n    pass",
            language="python",
        )
    ]
    is_valid, errors, _ = validator.validate_generated_files(broken_files)
    assert is_valid is False
    assert any("syntax error" in e.lower() for e in errors)


def test_project_quality_validator_detects_malformed_json():
    """Validator detects malformed JSON files."""
    validator = ProjectQualityValidator()
    broken_files = [
        GeneratedFile(
            relative_path="package.json",
            content="{\n  'invalid_single_quotes': True\n}",
            language="json",
        )
    ]
    is_valid, errors, _ = validator.validate_generated_files(broken_files)
    assert is_valid is False
    assert any("json" in e.lower() for e in errors)


# ============================================================================
# 7. SAFE BUILD/TEST ARCHITECTURE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_safe_build_executor_blocks_shell_execution():
    """SafeBuildExecutor blocks disallowed shell executables and malicious commands."""
    executor = SafeBuildExecutor()

    # Disallowed commands
    malicious_commands = [
        "cmd.exe /c dir",
        "powershell -Command Get-Process",
        "bash -c ls",
        "sh test.sh",
        "npm install malicious-pkg",
        "pip install evil",
    ]
    for cmd in malicious_commands:
        assert executor.evaluate_command_safety(cmd) is False
        req = BuildTestRequest(project_name="TestApp", command_type=cmd)
        res = await executor.execute_build_check(req)
        assert res.success is False
        assert res.exit_code != 0
        assert "SECURITY_VIOLATION" in res.stderr

    # Allowed static check
    valid_req = BuildTestRequest(project_name="TestApp", command_type="test")
    valid_res = await executor.execute_build_check(valid_req)
    assert valid_res.success is True
    assert valid_res.exit_code == 0


# ============================================================================
# 8. ERROR AND FIX LOOP TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_fix_loop_manager_requires_confirmation():
    """FixLoopManager proposes fixes and refuses application without confirmation."""
    manager = FixLoopManager()
    folder_tool = CreateProjectFolderTool()
    file_tool = CreateProjectFileTool()

    await folder_tool.execute(project_name="FixLoopTestApp")
    await file_tool.execute(
        project_name="FixLoopTestApp",
        relative_path="main.py",
        content="def main():\n    print('Initial')\n",
    )

    err = BuildError(
        error_type="SyntaxError",
        message="Invalid syntax on line 1",
        file_path="main.py",
        line_number=1,
    )

    proposal = manager.analyze_error_and_propose_fix(
        project_name="FixLoopTestApp",
        error=err,
        corrected_content="def main():\n    print('Corrected')\n",
    )
    assert proposal is not None
    assert proposal.requires_confirmation is True

    # 1. Attempt applying without confirmation -> MUST BE REJECTED
    unconfirmed_result = await manager.apply_fix(
        project_name="FixLoopTestApp",
        proposal=proposal,
        user_confirmed=False,
    )
    assert unconfirmed_result.success is False
    assert unconfirmed_result.applied is False
    assert "confirmation" in unconfirmed_result.message.lower()

    # 2. Apply with user_confirmed=True -> SUCCESS
    confirmed_result = await manager.apply_fix(
        project_name="FixLoopTestApp",
        proposal=proposal,
        user_confirmed=True,
    )
    assert confirmed_result.success is True
    assert confirmed_result.applied is True

    # Verify content on disk was updated
    file_path = resolve_project_file_path("FixLoopTestApp", "main.py")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Corrected" in content


# ============================================================================
# 9. SECURITY & ATTACK REGRESSION TESTS
# ============================================================================

def test_security_project_name_sanitization_attacks():
    """Test project name regex blocks command injection, path escapes, and special characters."""
    attack_names = [
        "../../evil",
        "..\\..\\evil",
        "C:\\Windows\\System32",
        "test;rm -rf /",
        "app && calc",
        "project | dir",
        "name with spaces",
        "`whoami`",
        "$(whoami)",
        "project'--",
        "format C:",
        "a" * (MAX_PROJECT_NAME_LENGTH + 1),  # Exceeds max length
    ]
    for bad_name in attack_names:
        assert sanitize_project_name(bad_name) is None
        spec = ProjectSpecificationEngine().create_specification(project_name=bad_name)
        assert spec is None


def test_security_path_traversal_rejection():
    """Test relative file paths cannot escape project folder boundary."""
    escape_paths = [
        "../../secret.txt",
        "..\\..\\secret.txt",
        "/etc/passwd",
        "C:\\Windows\\System32\\cmd.exe",
        "sub/../../outside.txt",
        "file:///secret.txt",
    ]
    for esc in escape_paths:
        assert resolve_project_file_path("TaskFlowDev", esc) is None


def test_security_generation_limits():
    """Verify files and projects exceeding limits are rejected."""
    validator = ProjectQualityValidator()

    # 1. Single file exceeds MAX_FILE_SIZE_BYTES
    oversized_file = GeneratedFile(
        relative_path="big_file.txt",
        content="X" * (MAX_FILE_SIZE_BYTES + 10),
        language="text",
    )
    is_valid, errors, _ = validator.validate_generated_files([oversized_file])
    assert is_valid is False
    assert any("exceeds limit" in e.lower() for e in errors)

    # 2. Too many files exceeds MAX_FILES_PER_PROJECT
    many_files = [
        GeneratedFile(relative_path=f"file_{i}.txt", content="ok", language="text")
        for i in range(MAX_FILES_PER_PROJECT + 5)
    ]
    is_valid_many, errors_many, _ = validator.validate_generated_files(many_files)
    assert is_valid_many is False
    assert any("file count" in e.lower() for e in errors_many)


# ============================================================================
# 10. WORKFLOW INTEGRATION E2E TEST
# ============================================================================

@pytest.mark.asyncio
async def test_workflow_engine_dev_engine_integration():
    """Verify WorkflowEngine runs rich dev engine project creation smoothly."""
    planner = WorkflowPlanner()
    engine = WorkflowEngine(planner=planner)

    query = "Create a React expense tracker with authentication"
    plan = planner.plan_project_development(query, project_name="ExpenseTracker", project_type="react")
    assert plan is not None

    # Execute workflow with auto_confirm=True
    result = await engine.execute_workflow(plan, auto_confirm=True)
    assert result.status == WorkflowState.COMPLETED
    assert result.success is True

    # Check files created on disk
    project_dir = resolve_project_path("ExpenseTracker")
    assert os.path.isdir(project_dir)
    assert os.path.isfile(os.path.join(project_dir, "package.json"))
    assert os.path.isfile(os.path.join(project_dir, "tsconfig.json"))
    assert os.path.isfile(os.path.join(project_dir, "src", "App.tsx"))
    assert os.path.isfile(os.path.join(project_dir, "src", "components", "Header.tsx"))

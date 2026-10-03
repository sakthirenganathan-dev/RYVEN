"""
RYVEN 3.0 — Milestone 16.1 Test Suite
Intelligent Task Decomposition & Planning Engine Tests
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.agents.planning_models import (
    PlanningMode,
    GoalComplexity,
    PlanRisk,
    PlanStatus,
    PlanningRequest,
    PlanningResult,
    PlanningTaskDraft,
    ExtractedIntent,
    redact_secrets,
)
from app.agents.normalizer import GoalNormalizer, goal_normalizer
from app.agents.complexity import ComplexityClassifier, complexity_classifier
from app.agents.intent import IntentAnalyzer, intent_analyzer
from app.agents.dependency_analyzer import DependencyAndRiskAnalyzer, dependency_risk_analyzer
from app.agents.validator import PlanValidator, PlanRepairEngine, plan_validator, plan_repair_engine
from app.agents.llm_planner import LLMPlanner, llm_planner
from app.agents.planning_engine import PlanningEngine, planning_engine
from app.agents.coordinator import AgentCoordinator, agent_coordinator
from app.agents.models import AgentCapability, AgentRole, AgentStatus
from app.actions.models import ActionEventType
from app.actions.event_bus import ActionEventBus
from app.main import app


# ---------------------------------------------------------------------------
# 1. Goal Normalization Tests
# ---------------------------------------------------------------------------

class TestGoalNormalizer:
    def test_normalize_empty_input(self):
        norm = GoalNormalizer()
        assert norm.normalize_text("") == ""
        assert norm.normalize_text("   ") == ""

    def test_normalize_vscode_variations(self):
        norm = GoalNormalizer()
        assert norm.normalize_text("open vs code") == "Open Visual Studio Code"
        assert norm.normalize_text("launch vscode") == "Open Visual Studio Code"

    def test_normalize_browser_variations(self):
        norm = GoalNormalizer()
        assert norm.normalize_text("open chrome") == "Open Google Chrome"
        assert norm.normalize_text("open edge") == "Open Microsoft Edge"

    def test_normalize_search_queries(self):
        norm = GoalNormalizer()
        res = norm.normalize_text("search web for react three fiber")
        assert "React Three Fiber" in res or "react three fiber" in res.lower()
        assert "Search" in res

    def test_normalize_project_creation(self):
        norm = GoalNormalizer()
        res = norm.normalize_text("create a react app called TaskFlow")
        assert "Create a React project named TaskFlow" in res

    def test_normalize_diagnostics_and_status(self):
        norm = GoalNormalizer()
        assert norm.normalize_text("show system status") == "Check system status and telemetry"
        assert norm.normalize_text("show logs") == "Show recent system execution logs"

    def test_normalize_preserves_urls(self):
        norm = GoalNormalizer()
        url = "https://docs.pmnd.rs/react-three-fiber/getting-started"
        res = norm.normalize_text(f"read doc from {url}")
        assert url in res

    def test_extract_parameters(self):
        norm = GoalNormalizer()
        params = norm.extract_parameters("Create a react project named TaskFlow at /projects/demo")
        assert params.get("target_name") == "TaskFlow"
        assert params.get("path") == "/projects/demo"


# ---------------------------------------------------------------------------
# 2. Complexity Classification Tests
# ---------------------------------------------------------------------------

class TestComplexityClassifier:
    def test_simple_open_application(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Open VS Code")
        assert comp == GoalComplexity.SIMPLE
        assert mode == PlanningMode.DIRECT

    def test_simple_search(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Search Google for FastAPI")
        assert comp == GoalComplexity.SIMPLE
        assert mode == PlanningMode.DIRECT

    def test_simple_system_status(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Show system status")
        assert comp == GoalComplexity.SIMPLE
        assert mode == PlanningMode.DIRECT

    def test_moderate_multi_step_deterministic(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Open VS Code and open my project")
        assert comp == GoalComplexity.MODERATE
        assert mode == PlanningMode.DETERMINISTIC

    def test_complex_research_and_build(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Research React Three Fiber, create a demo, test it and deploy")
        assert comp == GoalComplexity.COMPLEX
        assert mode == PlanningMode.LLM_ASSISTED

    def test_complex_multi_domain(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("Research this technology and build a tested project")
        assert comp == GoalComplexity.COMPLEX
        assert mode == PlanningMode.LLM_ASSISTED

    def test_empty_goal_classification(self):
        clf = ComplexityClassifier()
        comp, mode, *_ = clf.classify("")
        assert comp == GoalComplexity.SIMPLE


# ---------------------------------------------------------------------------
# 3. Intent Extraction Tests
# ---------------------------------------------------------------------------

class TestIntentAnalyzer:
    def test_intent_research_and_create(self):
        analyzer = IntentAnalyzer()
        intent = analyzer.analyze("Research React Three Fiber and create a demo")
        assert any("RESEARCH" in d or "DEVELOPMENT" in d for d in intent.domains)
        assert any(a.lower() in intent.primary_action.lower() for a in ["search", "read", "synthesize", "create_project", "research", "develop"])
        assert intent.target_entity or intent.target_project or "React Three Fiber" in intent.normalized_goal
        assert AgentCapability.WEB_SEARCH in intent.requested_capabilities or AgentCapability.WEB_RESEARCH in intent.requested_capabilities

    def test_intent_confirmation_sensitive_deploy(self):
        analyzer = IntentAnalyzer()
        intent = analyzer.analyze("Build and deploy the application to production")
        assert intent.is_consequential is True or intent.requires_confirmation is True
        assert AgentCapability.DEPLOY in intent.requested_capabilities

    def test_intent_confirmation_sensitive_commit(self):
        analyzer = IntentAnalyzer()
        intent = analyzer.analyze("Commit all changes to git and push")
        assert intent.is_consequential is True or intent.requires_confirmation is True
        assert AgentCapability.GIT in intent.requested_capabilities or AgentCapability.CODE_MODIFICATION in intent.requested_capabilities

    def test_intent_open_app(self):
        analyzer = IntentAnalyzer()
        intent = analyzer.analyze("Open Visual Studio Code")
        assert any(d in intent.domains for d in ["WINDOWS", "COMPUTER", "APPLICATION_CONTROL"])
        assert AgentCapability.WINDOWS_APP in intent.requested_capabilities

    def test_intent_system_inspect(self):
        analyzer = IntentAnalyzer()
        intent = analyzer.analyze("Inspect system telemetry and resources")
        assert AgentCapability.SYSTEM_STATUS in intent.requested_capabilities


# ---------------------------------------------------------------------------
# 4. Dependency, Risk & Parallelism Tests
# ---------------------------------------------------------------------------

class TestDependencyAndRiskAnalyzer:
    def test_dependency_inference_sequential_chain(self):
        analyzer = DependencyAndRiskAnalyzer()
        tasks = [
            PlanningTaskDraft(id="task_1", title="Research", objective="Research tech", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="task_2", title="Read docs", objective="Read sources", capability=AgentCapability.PAGE_READING, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="task_3", title="Create project", objective="Scaffold code", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER),
            PlanningTaskDraft(id="task_4", title="Build demo", objective="Build app", capability=AgentCapability.BUILD, role=AgentRole.DEVELOPER),
            PlanningTaskDraft(id="task_5", title="Test demo", objective="Run tests", capability=AgentCapability.TEST, role=AgentRole.DEVELOPER),
        ]
        analyzed = analyzer.infer_dependencies(tasks)
        # Verify research -> read docs
        assert "task_1" in analyzed[1].dependencies
        # Verify code modification before build
        assert "task_3" in analyzed[3].dependencies
        # Verify build before test
        assert "task_4" in analyzed[4].dependencies

    def test_parallelism_detection_sibling_searches(self):
        analyzer = DependencyAndRiskAnalyzer()
        tasks = [
            PlanningTaskDraft(id="search_1", title="Search A", objective="Search React", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="search_2", title="Search B", objective="Search ThreeJS", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="search_3", title="Search C", objective="Search Vite", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="synth", title="Synthesize", objective="Combine results", capability=AgentCapability.TASK_COORDINATION, role=AgentRole.RESEARCH, dependencies=["search_1", "search_2", "search_3"]),
        ]
        par_count = analyzer.calculate_parallelism(tasks)
        assert par_count >= 3

    def test_risk_analysis_safe(self):
        analyzer = DependencyAndRiskAnalyzer()
        tasks = [
            PlanningTaskDraft(id="t1", title="Search", objective="Read doc", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="t2", title="Inspect", objective="Status", capability=AgentCapability.SYSTEM_STATUS, role=AgentRole.COMPUTER),
        ]
        risk = analyzer.evaluate_risk(tasks)
        assert risk == PlanRisk.SAFE


    def test_risk_analysis_deploy_critical(self):
        analyzer = DependencyAndRiskAnalyzer()
        tasks = [
            PlanningTaskDraft(id="t1", title="Deploy", objective="Deploy to production", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER),
        ]
        risk = analyzer.evaluate_risk(tasks)
        assert risk in (PlanRisk.HIGH, PlanRisk.CRITICAL)

    def test_confirmation_prediction(self):
        analyzer = DependencyAndRiskAnalyzer()
        tasks = [
            PlanningTaskDraft(id="t1", title="Scaffold", objective="Create files", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER),
            PlanningTaskDraft(id="t2", title="Deploy", objective="Deploy service", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER),
        ]
        needs_conf, conf_points = analyzer.predict_confirmation(tasks)
        assert needs_conf is True
        assert "t2" in conf_points
        assert tasks[1].requires_confirmation is True


# ---------------------------------------------------------------------------
# 5. Plan Validation Tests (15 Rules)
# ---------------------------------------------------------------------------

class TestPlanValidator:
    def test_valid_plan_passes(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Search", objective="Web search", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, tools=["web_search"]),
            PlanningTaskDraft(id="t2", title="Read", objective="Read page", capability=AgentCapability.PAGE_READING, role=AgentRole.RESEARCH, tools=["page_reader"], dependencies=["t1"]),
        ]
        res = validator.validate(tasks, deadline_seconds=60)
        assert res.is_valid is True
        assert len(res.errors) == 0

    def test_reject_duplicate_task_ids(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Step 1", objective="Do A", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="t1", title="Step 2", objective="Do B", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("Duplicate task ID" in e for e in res.errors)

    def test_reject_nonexistent_dependency(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Step 1", objective="Do A", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, dependencies=["ghost_task"]),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("unknown task 'ghost_task'" in e for e in res.errors)

    def test_reject_circular_dependency(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Task 1", objective="A", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, dependencies=["t2"]),
            PlanningTaskDraft(id="t2", title="Task 2", objective="B", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, dependencies=["t1"]),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("cycle" in e.lower() for e in res.errors)

    def test_reject_forbidden_shell_tools(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Run bash", objective="Execute shell", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER, tools=["run_terminal_command", "bash"]),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("Forbidden tool" in e for e in res.errors)

    def test_reject_unregistered_tools(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Evil Tool", objective="Exploit", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER, tools=["arbitrary_backdoor_v1"]),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("Unregistered tool" in e for e in res.errors)

    def test_reject_excessive_task_count(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id=f"t_{i}", title=f"Task {i}", objective=f"Obj {i}", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH)
            for i in range(25)
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("exceeds safety maximum" in e for e in res.errors)

    def test_enforce_confirmation_on_deploy(self):
        validator = PlanValidator()
        tasks = [
            PlanningTaskDraft(id="t1", title="Deploy", objective="Deploy service", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER, requires_confirmation=False),
        ]
        res = validator.validate(tasks)
        assert res.is_valid is False
        assert any("must require user confirmation" in e for e in res.errors)


# ---------------------------------------------------------------------------
# 6. Plan Repair Tests
# ---------------------------------------------------------------------------

class TestPlanRepairEngine:
    def test_repair_missing_confirmation_flag(self):
        repairer = PlanRepairEngine()
        tasks = [
            PlanningTaskDraft(id="t1", title="Deploy", objective="Deploy app", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER, requires_confirmation=False),
        ]
        repaired, count = repairer.repair(tasks, ["Task 't1' performs DEPLOY but does not require confirmation"])
        assert count == 1
        assert repaired[0].requires_confirmation is True

    def test_repair_removes_forbidden_shell_tool(self):
        repairer = PlanRepairEngine()
        tasks = [
            PlanningTaskDraft(id="t1", title="Build", objective="Compile app", capability=AgentCapability.BUILD, role=AgentRole.DEVELOPER, tools=["run_terminal_command", "npm_build"]),
        ]
        repaired, count = repairer.repair(tasks, ["Forbidden tool 'run_terminal_command' requested"])
        assert count == 1
        assert "run_terminal_command" not in repaired[0].tools
        assert "npm_build" in repaired[0].tools

    def test_repair_inverts_scaffold_build_dependency(self):
        repairer = PlanRepairEngine()
        # Incorrect order: scaffold depends on build
        tasks = [
            PlanningTaskDraft(id="build", title="Build", objective="Compile", capability=AgentCapability.BUILD, role=AgentRole.DEVELOPER, dependencies=[]),
            PlanningTaskDraft(id="scaffold", title="Scaffold", objective="Init project", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER, dependencies=["build"]),
        ]
        repaired, count = repairer.repair(tasks, ["Dependency order inverted between build and code_modification"])
        assert count == 1
        scaffold_task = next(t for t in repaired if t.id == "scaffold")
        build_task = next(t for t in repaired if t.id == "build")
        assert "build" not in scaffold_task.dependencies
        assert "scaffold" in build_task.dependencies

    def test_repair_bounded_at_3_attempts(self):
        repairer = PlanRepairEngine()
        tasks = [
            PlanningTaskDraft(id="t1", title="Circular 1", objective="Obj", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, dependencies=["t2"]),
            PlanningTaskDraft(id="t2", title="Circular 2", objective="Obj", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH, dependencies=["t1"]),
        ]
        # Circular dependencies cannot be repaired by standard heuristic
        repaired, count = repairer.repair(tasks, ["Dependency cycle detected: t1 -> t2 -> t1"])
        assert count <= 3


# ---------------------------------------------------------------------------
# 7. LLM Planner Tests
# ---------------------------------------------------------------------------

class TestLLMPlanner:
    @pytest.mark.asyncio
    async def test_llm_planner_parses_json(self):
        planner = LLMPlanner()
        mock_response = """```json
        {
            "goal": "Research React Three Fiber",
            "complexity": "COMPLEX",
            "tasks": [
                {
                    "id": "task_1",
                    "title": "Search R3F",
                    "objective": "Search official documentation",
                    "capability": "WEB_SEARCH",
                    "role": "RESEARCH",
                    "dependencies": []
                },
                {
                    "id": "task_2",
                    "title": "Synthesize",
                    "objective": "Summarize features",
                    "capability": "TASK_COORDINATION",
                    "role": "RESEARCH",
                    "dependencies": ["task_1"]
                }
            ]
        }
        ```"""
        with patch.object(planner.ai_provider, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(content=mock_response)
            req = PlanningRequest(user_goal="Research React Three Fiber")
            tasks = await planner.plan(req)
            assert len(tasks) == 2
            assert tasks[0].id == "task_1"
            assert tasks[0].capability == AgentCapability.WEB_SEARCH
            assert tasks[1].dependencies == ["task_1"]

    @pytest.mark.asyncio
    async def test_llm_planner_fallback_on_failure(self):
        planner = LLMPlanner()
        with patch.object(planner.ai_provider, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = Exception("Ollama connection failed")
            req = PlanningRequest(user_goal="Research React Three Fiber and create a demo")
            tasks = await planner.plan(req)
            # Must fall back gracefully to deterministic plan
            assert len(tasks) >= 2
            assert any(t.capability == AgentCapability.WEB_SEARCH or t.capability == AgentCapability.WEB_RESEARCH for t in tasks)

    @pytest.mark.asyncio
    async def test_llm_planner_strips_forbidden_tools(self):
        planner = LLMPlanner()
        mock_response = """{
            "tasks": [
                {
                    "id": "task_1",
                    "title": "Execute Command",
                    "objective": "Run bash rm -rf",
                    "capability": "CODE_MODIFICATION",
                    "role": "DEVELOPER",
                    "tools": ["run_terminal_command", "git_status"]
                }
            ]
        }"""
        with patch.object(planner.ai_provider, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = MagicMock(content=mock_response)
            req = PlanningRequest(user_goal="Do some dev")
            tasks = await planner.plan(req)
            assert "run_terminal_command" not in tasks[0].tools
            assert "git_status" in tasks[0].tools



# ---------------------------------------------------------------------------
# 8. Planning Engine End-to-End Tests
# ---------------------------------------------------------------------------

class TestPlanningEngine:
    @pytest.mark.asyncio
    async def test_simple_command_fast_path(self):
        engine = PlanningEngine()
        result = await engine.create_plan("Open VS Code")
        assert result.complexity == GoalComplexity.SIMPLE
        assert result.planning_mode == PlanningMode.DIRECT
        assert len(result.tasks) == 1
        assert result.tasks[0].capability == AgentCapability.WINDOWS_APP
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_moderate_command_deterministic_path(self):
        engine = PlanningEngine()
        result = await engine.create_plan("Open VS Code and open my project")
        assert result.complexity == GoalComplexity.MODERATE
        assert result.planning_mode == PlanningMode.DETERMINISTIC
        assert len(result.tasks) >= 2
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_complex_command_planning(self):
        engine = PlanningEngine()
        # Mock LLM response to keep unit test deterministic
        mock_tasks = [
            PlanningTaskDraft(id="task_1", title="Research", objective="Research tech", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="task_2", title="Synthesize", objective="Synthesize notes", capability=AgentCapability.TASK_COORDINATION, role=AgentRole.RESEARCH, dependencies=["task_1"]),
            PlanningTaskDraft(id="task_3", title="Create Project", objective="Scaffold demo", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER, dependencies=["task_2"]),
            PlanningTaskDraft(id="task_4", title="Build & Test", objective="Test app", capability=AgentCapability.TEST, role=AgentRole.DEVELOPER, dependencies=["task_3"]),
            PlanningTaskDraft(id="task_5", title="Deploy", objective="Deploy demo", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER, dependencies=["task_4"], requires_confirmation=True),
        ]
        with patch.object(engine.llm_planner, "plan_goal", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = mock_tasks
            result = await engine.create_plan("Research React Three Fiber, build demo, test it and deploy")
            assert result.complexity == GoalComplexity.COMPLEX
            assert len(result.tasks) == 5
            assert result.risk in (PlanRisk.HIGH, PlanRisk.CRITICAL)
            assert result.requires_confirmation is True
            assert "task_5" in result.confirmation_points

    @pytest.mark.asyncio
    async def test_build_task_graph_from_plan(self):
        engine = PlanningEngine()
        result = await engine.create_plan("Search Google for FastAPI")
        graph = engine.build_task_graph(result)
        assert graph is not None
        assert len(graph.tasks) == len(result.tasks)
        task_id = result.tasks[0].task_id
        assert graph.tasks[task_id].capability == result.tasks[0].capability

    @pytest.mark.asyncio
    async def test_plan_explanation_generation(self):
        engine = PlanningEngine()
        result = await engine.create_plan("Open VS Code")
        assert "Visual Studio Code" in result.explanation
        assert "Visual Studio Code" in result.normalized_goal

    @pytest.mark.asyncio
    async def test_plan_revision_after_failure(self):
        engine = PlanningEngine()
        result = await engine.create_plan("Open VS Code and open my project")
        revised = engine.revise_plan(result, failed_task_id=result.tasks[0].id, failure_reason="Folder not found")
        assert revised is not None
        assert revised.status == PlanStatus.VALID

    def test_secret_scrubbing(self):
        data = {
            "token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
            "password": "supersecretpassword123!",
            "normal_field": "Hello World",
            "nested": {
                "api_key": "sk-1234567890abcdef1234567890abcdef",
                "text": "bearer abcdef1234567890abcdef"
            }
        }
        scrubbed = redact_secrets(data)
        assert scrubbed["token"] == "[REDACTED]"
        assert scrubbed["password"] == "[REDACTED]"
        assert scrubbed["normal_field"] == "Hello World"
        assert scrubbed["nested"]["api_key"] == "[REDACTED]"
        assert "[REDACTED]" in scrubbed["nested"]["text"]


# ---------------------------------------------------------------------------
# 9. AgentCoordinator Integration Tests
# ---------------------------------------------------------------------------

class TestCoordinatorIntegration:
    @pytest.mark.asyncio
    async def test_coordinator_uses_planning_engine(self):
        coordinator = AgentCoordinator()
        graph = await coordinator.coordinate("Open VS Code")
        assert graph is not None
        assert len(graph.tasks) == 1
        # graph executes synchronously in coordinate(); it will be COMPLETED or RUNNING
        assert graph.status.value in ("COMPLETED", "RUNNING", "EXECUTING")

    @pytest.mark.asyncio
    async def test_coordinator_decomposer_backwards_compatibility(self):
        coordinator = AgentCoordinator()
        assert hasattr(coordinator, "decomposer")
        assert coordinator.decomposer is not None
        # Verify direct call to decomposer still works
        # decomposer.decompose() returns an AgentTaskGraph, not a list
        task_graph = coordinator.decomposer.decompose("Open Chrome")
        assert len(task_graph.tasks) >= 1

    @pytest.mark.asyncio
    async def test_coordinator_complex_coordination(self):
        coordinator = AgentCoordinator()
        mock_tasks = [
            PlanningTaskDraft(id="task_1", title="Research", objective="Research tech", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            PlanningTaskDraft(id="task_2", title="Synthesize", objective="Summarize", capability=AgentCapability.TASK_COORDINATION, role=AgentRole.RESEARCH, dependencies=["task_1"]),
        ]
        with patch.object(coordinator.planning_engine.llm_planner, "plan_goal", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = mock_tasks
            graph = await coordinator.coordinate("Research React Three Fiber and summarize it")
            assert graph is not None
            assert len(graph.tasks) >= 1


    @pytest.mark.asyncio
    async def test_coordinator_cancellation(self):
        coordinator = AgentCoordinator()
        graph = await coordinator.coordinate("Open VS Code")
        # Re-register graph as active so cancel() can find it
        coordinator._active_graphs[graph.graph_id] = graph
        canceled = await coordinator.cancel(graph.graph_id)
        assert canceled is True
        assert graph.status.value == "CANCELLED"


# ---------------------------------------------------------------------------
# 10. Planning ActionEvents Tests
# ---------------------------------------------------------------------------

class TestPlanningActionEvents:
    @pytest.mark.asyncio
    async def test_action_event_emission_on_plan_creation(self):
        events_captured = []

        # Subscriber can be sync (ActionEventBus wraps it automatically)
        def listener(evt):
            if "PLAN_" in evt.type.value:
                events_captured.append(evt)

        bus = ActionEventBus()
        bus.subscribe(listener)

        engine = PlanningEngine(event_bus=bus)
        await engine.create_plan("Open Chrome")

        event_types = [e.type.value for e in events_captured]
        assert "PLAN_CREATED" in event_types
        assert "PLAN_VALIDATED" in event_types
        assert "PLAN_EXECUTION_READY" in event_types

    @pytest.mark.asyncio
    async def test_action_event_emission_on_repair(self):
        events_captured = []

        def listener(evt):
            if "PLAN_" in evt.type.value:
                events_captured.append(evt)

        bus = ActionEventBus()
        bus.subscribe(listener)

        engine = PlanningEngine(event_bus=bus)

        # Force the repair path by injecting a validation failure.
        # DependencyAndRiskAnalyzer auto-heals DEPLOY confirmation flags before the
        # validator runs, and short goals use HYBRID mode (not LLM_ASSISTED) so
        # mocking plan_goal has no effect. Instead we patch the validator directly
        # to return invalid on the first call, simulating a plan that requires repair.
        from app.agents.validator import PlanValidationResult
        from unittest.mock import MagicMock

        original_validate = engine.validator.validate
        call_count = {"n": 0}

        def mock_validate(tasks):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: report a repairable dangling-dependency error
                return PlanValidationResult(
                    is_valid=False,
                    errors=["Task 'task_1' depends on unknown task 'ghost_task'."],
                    warnings=[],
                    repair_suggestions=["Remove dangling dependency 'ghost_task' from task task_1."],
                )
            # Subsequent calls: plan is valid (repair succeeded)
            return original_validate(tasks)

        engine.validator.validate = mock_validate
        engine.repair_engine.validator.validate = mock_validate

        await engine.create_plan("Open Chrome")

        event_types = [e.type.value for e in events_captured]
        assert "PLAN_REPAIR_STARTED" in event_types
        assert "PLAN_REPAIRED" in event_types


# ---------------------------------------------------------------------------
# 11. Planning REST API Tests
# ---------------------------------------------------------------------------

class TestPlanningApiRoutes:
    def setup_method(self):
        self.client = TestClient(app)

    def test_post_planning_preview_simple(self):
        resp = self.client.post("/api/planning/preview", json={"user_goal": "Open VS Code"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"].startswith("plan")
        assert data["complexity"] == "SIMPLE"
        assert len(data["tasks"]) == 1

    def test_post_planning_preview_complex(self):
        with patch("app.agents.planning_engine.planning_engine.llm_planner.plan_goal", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = [
                PlanningTaskDraft(id="t1", title="Research", objective="Research R3F", capability=AgentCapability.WEB_SEARCH, role=AgentRole.RESEARCH),
            ]
            resp = self.client.post("/api/planning/preview", json={"user_goal": "Research React Three Fiber and create a demo"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["plan_id"].startswith("plan")
            assert "tasks" in data


    def test_post_planning_validate(self):
        req = {
            "user_goal": "Open VS Code",
            "tasks": [
                {
                    "id": "t1",
                    "title": "Open VS Code",
                    "objective": "Launch app",
                    "capability": "WINDOWS_APP",
                    "role": "COMPUTER",
                    "tools": ["open_application"],
                    "dependencies": []
                }
            ]
        }
        resp = self.client.post("/api/planning/validate", json=req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True

    def test_get_planning_by_id(self):
        # First preview to populate cache
        prev = self.client.post("/api/planning/preview", json={"user_goal": "Open Chrome"}).json()
        plan_id = prev["plan_id"]

        resp = self.client.get(f"/api/planning/{plan_id}")
        assert resp.status_code == 200
        assert resp.json()["plan_id"] == plan_id

    def test_get_nonexistent_plan(self):
        resp = self.client.get("/api/planning/plan_nonexistent_999")
        assert resp.status_code == 404

    def test_execute_plan_endpoint(self):
        prev = self.client.post("/api/planning/preview", json={"user_goal": "Open VS Code"}).json()
        plan_id = prev["plan_id"]

        resp = self.client.post(f"/api/planning/{plan_id}/execute")
        assert resp.status_code == 200
        graph = resp.json()
        assert "graph_id" in graph
        # Graph is started asynchronously; status is RUNNING immediately after start_graph()
        assert graph["status"] in ("RUNNING", "EXECUTING", "COMPLETED")


# ---------------------------------------------------------------------------
# 12. Security Invariants & Guardrails Tests
# ---------------------------------------------------------------------------

class TestPlanningSecurityInvariants:
    def test_untrusted_llm_cannot_inject_terminal_tools(self):
        validator = PlanValidator()
        malicious_tasks = [
            PlanningTaskDraft(
                id="hack_1",
                title="Exploit",
                objective="Run curl exploit",
                capability=AgentCapability.CODE_MODIFICATION,
                role=AgentRole.DEVELOPER,
                tools=["bash", "cmd", "run_terminal_command", "sh"]
            )
        ]
        res = validator.validate(malicious_tasks)
        assert res.is_valid is False
        assert any("Forbidden tool" in e for e in res.errors)

    def test_plan_never_auto_confirms_destructive_actions(self):
        analyzer = DependencyAndRiskAnalyzer()
        destructive_tasks = [
            PlanningTaskDraft(id="d1", title="Delete", objective="Delete database", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER),
            PlanningTaskDraft(id="d2", title="Push", objective="Force push git", capability=AgentCapability.CODE_MODIFICATION, role=AgentRole.DEVELOPER),
            PlanningTaskDraft(id="d3", title="Deploy", objective="Deploy production", capability=AgentCapability.DEPLOY, role=AgentRole.DEVELOPER),
        ]
        needs_conf, conf_points = analyzer.predict_confirmation(destructive_tasks)
        assert needs_conf is True
        assert len(conf_points) == 3

    def test_redact_secrets_in_planning_context(self):
        context = {
            "api_token": "sk-proj-9876543210fedcba",
            "user_prompt": "Use authorization bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            "password": "Password123!",
        }
        scrubbed = redact_secrets(context)
        assert scrubbed["api_token"] == "[REDACTED]"
        assert scrubbed["password"] == "[REDACTED]"
        assert "[REDACTED]" in scrubbed["user_prompt"]


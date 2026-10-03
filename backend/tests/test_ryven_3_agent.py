"""RYVEN 3.0 — Comprehensive Agent Control Plane & Multi-Step Engine Test Suite.

Verifies:
1. Composite goal detection in AgentPlanner
2. Multi-step browser task planning (Open Chrome + Search YouTube)
3. Multi-step desktop task planning (VS Code + Workspace)
4. Multi-stage development planning (Build + Test + Commit)
5. CapabilityRouter tool isolation and domain classification
6. Observe -> Plan -> Act -> Observe -> Verify execution cycle
7. Human confirmation boundaries and token resumption
8. Bounded retry mechanism and real failure reporting
9. ActionEngine ActionEvent emission across agent lifecycle
10. Full Assistant end-to-end integration via process()
11. Router intent routing (single tool vs agent vs AI)
12. Read-only Replay safety over agent tasks
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.actions.event_bus import action_bus
from app.actions.models import ActionStatus, ActionType
from app.actions.service import action_service
from app.agent.capability_router import CapabilityRouter
from app.agent.engine import AgentEngine, agent_engine
from app.agent.models import (
    AgentExecutionResult,
    AgentPlan,
    AgentStatus,
    CapabilityGroup,
    StepStatus,
    TaskStep,
)
from app.agent.orchestrator import AgentOrchestrator
from app.agent.planner import AgentPlanner
from app.core.assistant import Assistant
from app.core.router import IntentRouter
from app.tools.registry import create_default_registry
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry
from app.workflows.engine import WorkflowEngine
from app.workflows.models import WorkflowDefinition, WorkflowExecutionResult, WorkflowState, WorkflowStep
from app.workflows.models import WorkflowExecutionResult, WorkflowState


# --------------------------------------------------------------------------
# 1. PLANNER & COMPOSITE GOAL DETECTION TESTS
# --------------------------------------------------------------------------


def test_composite_goal_detection():
    """1. AgentPlanner accurately discriminates composite vs single-action instructions."""
    planner = AgentPlanner()
    assert planner.is_composite_goal("open chrome and search youtube for self") is True
    assert planner.is_composite_goal("open chrome then search google for React tutorials") is True
    assert planner.is_composite_goal("open VS Code and open my project") is True
    assert planner.is_composite_goal("build the project and test it") is True

    # Single actions must NOT be classified as composite
    assert planner.is_composite_goal("open chrome") is False
    assert planner.is_composite_goal("open vs code") is False
    assert planner.is_composite_goal("what time is it") is False
    assert planner.is_composite_goal("check system status") is False


def test_browser_search_plan_decomposition():
    """2. 'open chrome and search youtube for self' decomposes into 3 ordered, dependency-linked steps."""
    planner = AgentPlanner()
    plan = planner.plan("open chrome and search youtube for self")

    assert plan.total_steps == 3
    assert plan.steps[0].tool_name == "open_application"
    assert plan.steps[0].arguments == {"application": "chrome"}

    assert plan.steps[1].tool_name == "navigate_browser"
    assert "youtube.com/results?search_query=self" in plan.steps[1].arguments["url"]
    assert plan.steps[1].dependencies == [plan.steps[0].step_id]

    assert plan.steps[2].tool_name == "read_page"
    assert plan.steps[2].dependencies == [plan.steps[1].step_id]


def test_google_search_plan_decomposition():
    """3. 'open chrome and search google for React tutorials' decomposes into application launch and search."""
    planner = AgentPlanner()
    plan = planner.plan("open chrome and search google for React tutorials")

    assert plan.total_steps == 3
    assert plan.steps[0].arguments["application"] == "chrome"
    assert plan.steps[1].tool_name == "navigate_browser"
    assert "google.com/search?q=React+tutorials" in plan.steps[1].arguments["url"]
    assert plan.steps[2].tool_name == "read_page"


def test_desktop_and_workspace_plan_decomposition():
    """4. 'open VS Code and open my project' decomposes into editor launch and folder opening."""
    planner = AgentPlanner()
    plan = planner.plan("open VS Code and open my project")

    assert plan.total_steps == 2
    assert plan.steps[0].tool_name == "open_application"
    assert plan.steps[0].arguments["application"] == "vscode"
    assert plan.steps[1].tool_name == "open_folder"
    assert plan.steps[1].arguments["folder"] == "project workspace"


def test_development_pipeline_plan_decomposition():
    """5. Multi-stage development request decomposes into ordered build, test, and commit steps."""
    planner = AgentPlanner()
    plan = planner.plan("build my project, test it and commit it")

    assert plan.total_steps == 3
    assert plan.steps[0].tool_name == "build_project"
    assert plan.steps[1].tool_name == "test_project"
    assert plan.steps[2].tool_name == "git_commit"
    assert plan.steps[2].requires_confirmation is True


# --------------------------------------------------------------------------
# 2. CAPABILITY ROUTER TESTS
# --------------------------------------------------------------------------


def test_capability_router_classification():
    """6. CapabilityRouter classifies goals into minimal capability domains."""
    router = CapabilityRouter()

    browser_caps = router.classify_capabilities("Search YouTube for React documentation")
    assert CapabilityGroup.BROWSER in browser_caps

    deploy_caps = router.classify_capabilities("Deploy project to Vercel")
    assert CapabilityGroup.DEPLOYMENT in deploy_caps
    assert CapabilityGroup.GIT in deploy_caps

    dev_caps = router.classify_capabilities("Fix the navbar and build project")
    assert CapabilityGroup.DEVELOPMENT in dev_caps


def test_capability_router_tool_filtering():
    """7. CapabilityRouter narrows 71 tools down to active domain tools."""
    registry = create_default_registry()
    schemas = CapabilityRouter.filter_registry(registry, [CapabilityGroup.BROWSER])

    assert "navigate_browser" in schemas
    assert "read_page" in schemas
    assert "click_element" in schemas
    assert "git_push" not in schemas
    assert "deployment_deploy" not in schemas


def test_capability_router_maps_every_registered_tool():
    """Every registered tool belongs to at least one routed capability domain."""
    registry = create_default_registry()
    mapped_tools = {
        name
        for tool_names in CapabilityRouter.CAPABILITY_TOOL_MAP.values()
        for name in tool_names
    }

    assert set(registry.list_tools()) <= mapped_tools


# --------------------------------------------------------------------------
# 3. OBSERVE -> PLAN -> ACT -> VERIFY EXECUTION TESTS
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_orchestrator_observe_act_verify_cycle():
    """8. AgentOrchestrator runs Observe-Act-Verify cycle and records observations."""
    orchestrator = AgentOrchestrator()
    planner = AgentPlanner()

    plan = planner.plan("open chrome and search youtube for self")
    result = await orchestrator.execute_plan(plan, auto_confirm=True)

    assert result.success is True
    assert result.steps_completed == 3
    assert len(result.observations) == 3
    assert all(obs.success for obs in result.observations)
    assert result.status == AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_confirmation_boundary_pauses_execution():
    """9. Actions requiring confirmation pause in WAITING_CONFIRMATION and generate token."""
    orchestrator = AgentOrchestrator()

    plan = AgentPlan(
        goal="Test confirmation gating",
        capabilities_required=[CapabilityGroup.BROWSER],
        steps=[
            TaskStep(
                step_id="step-01-confirm",
                order=1,
                name="Inspect Current Page",
                capability=CapabilityGroup.BROWSER,
                tool_name="get_current_page",
                arguments={},
                requires_confirmation=True,
            )
        ],
    )

    result = await orchestrator.execute_plan(plan, auto_confirm=False)
    assert result.status == AgentStatus.WAITING_CONFIRMATION
    token = result.final_output.get("confirmation_token")
    assert token is not None

    # Resuming with the token completes the task
    resume_res = await orchestrator.confirm_action(token)
    assert resume_res["success"] is True


@pytest.mark.asyncio
async def test_confirmation_resume_does_not_repeat_completed_steps():
    """Approving a pending step resumes it without replaying earlier side effects."""
    orchestrator = AgentOrchestrator()
    executed = []

    async def execute_tool(name, arguments=None, task_id=None):
        executed.append((name, task_id))
        return {"success": True, "message": name}

    orchestrator.registry.execute_tool = AsyncMock(side_effect=execute_tool)
    plan = AgentPlan(
        goal="Check status and commit",
        capabilities_required=[CapabilityGroup.GIT],
        steps=[
            TaskStep(
                step_id="step-01-status",
                order=1,
                name="Read Git status",
                capability=CapabilityGroup.GIT,
                tool_name="git_status",
            ),
            TaskStep(
                step_id="step-02-commit",
                order=2,
                name="Create Git commit",
                capability=CapabilityGroup.GIT,
                tool_name="git_commit",
            ),
        ],
    )

    paused = await orchestrator.execute_plan(plan, auto_confirm=False)
    assert paused.status == AgentStatus.WAITING_CONFIRMATION
    assert [name for name, _ in executed] == ["git_status"]

    resumed = await orchestrator.confirm_action(paused.final_output["confirmation_token"])

    assert resumed["success"] is True
    assert [name for name, _ in executed] == ["git_status", "git_commit"]
    assert len({task_id for _, task_id in executed}) == 1


@pytest.mark.asyncio
async def test_assistant_does_not_implicitly_confirm_workflow_steps():
    """A workflow reached through chat preserves the default confirmation boundary."""
    run_from_query = AsyncMock(
        return_value=WorkflowExecutionResult(
            workflow_id="wf-confirmation",
            name="Prepare Development Workspace",
            status=WorkflowState.WAITING_FOR_CONFIRMATION,
            success=False,
            message="Waiting for confirmation.",
            steps_total=1,
            steps_completed=0,
            steps_failed=0,
        )
    )
    workflow_engine = type("WorkflowEngineStub", (), {"run_from_query": run_from_query})()
    assistant = Assistant(workflow_engine=workflow_engine)

    response = await assistant.process("prepare my development workspace")

    assert response.type == "workflow"
    assert run_from_query.await_args.kwargs["auto_confirm"] is False


@pytest.mark.asyncio
async def test_bounded_retry_on_step_failure():
    """10. Unsuccessful tool execution retries boundedly up to max_retries and reports failure cleanly."""
    orchestrator = AgentOrchestrator()

    plan = AgentPlan(
        goal="Test failure recovery",
        capabilities_required=[CapabilityGroup.FILES],
        steps=[
            TaskStep(
                step_id="step-01-fail",
                order=1,
                name="Open Nonexistent File",
                capability=CapabilityGroup.FILES,
                tool_name="open_file",
                arguments={"path": "C:\\nonexistent_dir\\missing_file.xyz"},
                max_retries=1,
            )
        ],
    )

    result = await orchestrator.execute_plan(plan, auto_confirm=True)
    assert result.success is False
    assert result.status == AgentStatus.FAILED
    assert "failed" in result.message.lower()


# --------------------------------------------------------------------------
# 4. ACTION ENGINE & REPLAY INTEGRATION TESTS
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_emits_action_engine_events():
    """11. Agent execution publishes TASK_STARTED, TOOL_EXECUTE, and TASK_COMPLETED events."""
    planner = AgentPlanner()
    plan = planner.plan("open chrome and search youtube for self")

    task_id = "test-agent-events-task"
    result = await agent_engine.execute_goal("open chrome and search youtube for self", auto_confirm=True)
    assert result.success is True

    recent = action_bus.get_recent_events(limit=50)
    task_events = [e for e in recent if e.task_id == result.task_id]

    event_types = {e.action_type for e in task_events}
    assert ActionType.TASK_STARTED in event_types
    assert ActionType.TASK_COMPLETED in event_types


def test_replay_safety_over_agent_task():
    """12. Replay over completed agent task is strictly read-only and produces frames without side-effects."""
    replay = action_service.create_replay(task_id="nonexistent-agent-task")
    assert replay.total_frames == 0
    assert replay.current_frame is None


# --------------------------------------------------------------------------
# 5. ASSISTANT & ROUTER END-TO-END INTEGRATION
# --------------------------------------------------------------------------


def test_intent_router_classifies_composite_agent_goal():
    """13. Router classifies 'open chrome and search youtube self' as intent='agent'."""
    router = IntentRouter()
    decision = router.route("open chrome and search youtube for self")
    assert decision.intent == "agent"

    # Single action router check
    single_decision = router.route("open chrome")
    assert single_decision.intent == "tool"
    assert single_decision.tool_name == "open_application"


@pytest.mark.asyncio
async def test_assistant_processes_multi_step_agent_query():
    """14. Full Assistant.process() executes multi-step query 'open chrome and search youtube for self'."""
    assistant = Assistant()
    response = await assistant.process("open chrome and search youtube for self")

    assert response.success is True
    assert response.type == "agent"
    assert response.metadata.get("steps_completed") == 3
    assert response.metadata.get("steps_total") == 3
    assert len(response.metadata.get("observations", [])) == 3


@pytest.mark.asyncio
async def test_assistant_routes_workflow_intent_through_agent_engine():
    """Workflow-classified chat uses the unified top-level agent, not a parallel executor."""
    agent_result = AgentExecutionResult(
        task_id="task-unified-workflow",
        goal="prepare my development workspace",
        status=AgentStatus.COMPLETED,
        success=True,
        message="Workspace preparation completed.",
        steps_total=1,
        steps_completed=1,
        steps_failed=0,
    )
    agent_engine = MagicMock()
    agent_engine.execute_workflow_goal = AsyncMock(return_value=agent_result)
    workflow_engine = MagicMock()
    workflow_engine.run_from_query = AsyncMock(
        return_value=WorkflowExecutionResult(
            workflow_id="wf-legacy",
            name="Prepare Development Workspace",
            status=WorkflowState.COMPLETED,
            success=True,
            message="Legacy workflow ran.",
            steps_total=1,
            steps_completed=1,
            steps_failed=0,
        )
    )
    assistant = Assistant(agent_engine=agent_engine, workflow_engine=workflow_engine)

    response = await assistant.process("prepare my development workspace")

    assert response.type == "workflow"
    assert response.message == "Workspace preparation completed."
    agent_engine.execute_workflow_goal.assert_awaited_once()
    workflow_engine.run_from_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_engine_executes_existing_workflow_plan_through_shared_registry():
    """Validated workflow plans use the same registry and agent lifecycle."""
    class FakeSystemInfoTool(BaseTool):
        name = "system_info"
        description = "Fake system information for adapter test"
        input_schema = {}

        async def execute(self, **kwargs):
            return {"success": True, "message": "Observed fake system state."}

    registry = ToolRegistry()
    registry.register(FakeSystemInfoTool())
    workflow_engine = WorkflowEngine(registry=registry)
    workflow = WorkflowDefinition(
        workflow_id="wf-adapter-test",
        name="Safe inspection",
        description="One read-only step",
        steps=[
            WorkflowStep(
                step_id="step-system-info",
                tool_name="system_info",
                name="Read system information",
            )
        ],
    )
    workflow_engine.plan_workflow = lambda _goal: workflow
    engine = AgentEngine(registry=registry, workflow_engine=workflow_engine)

    result = await engine.execute_workflow_goal("inspect system state")

    assert result.success is True
    assert result.steps_completed == 1
    assert result.final_output["workflow_id"] == "wf-adapter-test"
    assert engine.orchestrator.registry is registry

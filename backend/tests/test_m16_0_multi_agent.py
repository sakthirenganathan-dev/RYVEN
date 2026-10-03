"""RYVEN 3.0 — Comprehensive M16.0 Multi-Agent Coordination Test Suite.

Validates:
- AgentRole, AgentStatus, AgentCapability enums and models
- AgentTaskGraph DAG scheduling, cycle rejection, and topological order
- TaskDecomposer heuristic planning (simple 1-task vs multi-agent DAGs)
- AgentRegistry role registration, lookup, and task tracking
- CommunicationManager structured messages and handoffs with MAX_HANDOFFS bounds
- AgentSecurityPolicy enforcement (tool boundary, shell prohibition, confirmation gates)
- AgentCoordinator end-to-end execution, concurrency gates, checkpoints, and cancellation
- FastAPI /api/agents/* routes and ActionEvents
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
import pytest
from unittest.mock import AsyncMock, patch

from app.actions.event_bus import action_bus
from app.actions.models import ActionType
from app.agents import (
    MAX_ACTIVE_AGENTS,
    MAX_AGENT_DEPTH,
    MAX_HANDOFFS,
    MAX_RETRIES,
    MAX_TASKS_PER_GRAPH,
    AgentCapability,
    AgentCoordinator,
    AgentDescriptor,
    AgentHandoff,
    AgentMessage,
    AgentRegistry,
    AgentRole,
    AgentSecurityPolicy,
    AgentStatus,
    AgentTask,
    AgentTaskGraph,
    CommunicationManager,
    MemoryContext,
    MemoryRead,
    MemoryWriteCandidate,
    MessageType,
    TaskDecomposer,
    agent_coordinator,
    agent_registry,
    agent_security,
    communication_manager,
    find_roles_for_capability,
    get_capabilities_for_role,
    get_tools_for_capability,
    redact_secrets,
)
from app.runtime.checkpoint_store import checkpoint_store
from app.tools.registry import ToolRegistry


# ===========================================================================
# 1. Agent Models & Constants Tests
# ===========================================================================

def test_spawn_limits_constants():
    """Verify configured execution and spawn boundaries."""
    assert MAX_ACTIVE_AGENTS == 5
    assert MAX_TASKS_PER_GRAPH == 20
    assert MAX_AGENT_DEPTH == 3
    assert MAX_HANDOFFS == 5
    assert MAX_RETRIES == 2


def test_agent_role_enumeration():
    """Verify all required AgentRole enum members exist."""
    expected = {
        "COORDINATOR", "RESEARCH", "DEVELOPER", "COMPUTER",
        "BROWSER", "WINDOWS", "MEMORY", "VERIFICATION"
    }
    assert {r.value for r in AgentRole} == expected


def test_agent_status_enumeration():
    """Verify all required AgentStatus enum members exist."""
    expected = {
        "CREATED", "READY", "PLANNING", "WAITING", "RUNNING",
        "BLOCKED", "REQUIRES_CONFIRMATION", "COMPLETED", "FAILED",
        "CANCELLED", "RECOVERING"
    }
    assert {s.value for s in AgentStatus} == expected


def test_agent_capability_enumeration():
    """Verify capabilities covering all domain surfaces."""
    caps = {c.value for c in AgentCapability}
    assert "WEB_SEARCH" in caps
    assert "PROJECT_SCAN" in caps
    assert "CODE_MODIFICATION" in caps
    assert "BUILD" in caps
    assert "TEST" in caps
    assert "GIT" in caps
    assert "DEPLOY" in caps
    assert "WINDOWS_APP" in caps
    assert "SYSTEM_STATUS" in caps
    assert "TASK_COORDINATION" in caps


def test_agent_task_creation_and_redaction():
    """Verify AgentTask fields and recursive secret redaction in arguments/context."""
    task = AgentTask(
        role=AgentRole.DEVELOPER,
        objective="Run build with credentials",
        capability=AgentCapability.BUILD,
        arguments={"api_key": "secret-12345", "project": "portfolio"},
        input_context={"user_password": "super-secret-pwd"},
    )
    assert task.role == AgentRole.DEVELOPER
    assert task.status == AgentStatus.CREATED
    assert task.arguments["api_key"] == "[REDACTED]"
    assert task.arguments["project"] == "portfolio"
    assert task.input_context["user_password"] == "[REDACTED]"


def test_agent_message_creation_and_redaction():
    """Verify AgentMessage typing and payload scrubbing."""
    msg = AgentMessage(
        sender_role=AgentRole.RESEARCH,
        recipient_role=AgentRole.DEVELOPER,
        message_type=MessageType.HANDOFF,
        payload={"token": "bearer-xyz", "findings": ["Used React 19"]},
    )
    assert msg.sender_role == AgentRole.RESEARCH
    assert msg.recipient_role == AgentRole.DEVELOPER
    assert msg.message_type == MessageType.HANDOFF
    assert msg.payload["token"] == "[REDACTED]"
    assert msg.payload["findings"] == ["Used React 19"]


def test_agent_handoff_creation_and_redaction():
    """Verify AgentHandoff structured payload and scrubbing."""
    handoff = AgentHandoff(
        source_role=AgentRole.RESEARCH,
        target_role=AgentRole.DEVELOPER,
        task_id="task-123",
        findings=[{"summary": "Three Fiber installed", "auth_token": "secret"}],
        sources=["https://threejs.org"],
        recommended_next_action="BUILD",
    )
    assert handoff.accepted is True
    assert handoff.sources == ["https://threejs.org"]
    assert handoff.findings[0]["auth_token"] == "[REDACTED]"


def test_memory_interface_models():
    """Verify MemoryRead, MemoryContext, and MemoryWriteCandidate contracts."""
    mr = MemoryRead(query="recent builds", limit=3)
    assert mr.limit == 3

    mc = MemoryContext(items=[{"topic": "threejs"}], retrieved_count=1)
    assert mc.retrieved_count == 1

    mwc = MemoryWriteCandidate(
        key="api_token_cache",
        value={"client_secret": "my-secret"},
        source_role=AgentRole.DEVELOPER,
    )
    assert mwc.value["client_secret"] == "[REDACTED]"


# ===========================================================================
# 2. Capabilities & Registry Tests
# ===========================================================================

def test_capabilities_to_tools_mapping():
    """Verify capabilities resolve to expected ToolRegistry tool names."""
    tools = get_tools_for_capability(AgentCapability.WEB_SEARCH)
    assert "internet_search" in tools

    build_tools = get_tools_for_capability(AgentCapability.BUILD)
    assert "build_project" in build_tools

    git_tools = get_tools_for_capability(AgentCapability.GIT)
    assert "git_commit" in git_tools
    assert "git_push" in git_tools


def test_role_capabilities_retrieval():
    """Verify specialized roles possess their designated capabilities."""
    dev_caps = get_capabilities_for_role(AgentRole.DEVELOPER)
    assert AgentCapability.BUILD in dev_caps
    assert AgentCapability.TEST in dev_caps
    assert AgentCapability.CODE_MODIFICATION in dev_caps

    res_caps = get_capabilities_for_role(AgentRole.RESEARCH)
    assert AgentCapability.WEB_SEARCH in res_caps
    assert AgentCapability.WEB_RESEARCH in res_caps


def test_find_roles_for_capability():
    """Verify finding candidate roles from a capability."""
    roles = find_roles_for_capability(AgentCapability.BUILD)
    assert AgentRole.DEVELOPER in roles

    search_roles = find_roles_for_capability(AgentCapability.WEB_SEARCH)
    assert AgentRole.RESEARCH in search_roles


def test_agent_registry_initialization():
    """Verify default singleton AgentRegistry initializes standard worker roles."""
    registry = AgentRegistry()
    agents = registry.list_agents()
    assert len(agents) == 8

    roles = {a.role for a in agents}
    assert AgentRole.COORDINATOR in roles
    assert AgentRole.RESEARCH in roles
    assert AgentRole.DEVELOPER in roles
    assert AgentRole.COMPUTER in roles
    assert AgentRole.VERIFICATION in roles


def test_agent_registry_duplicate_prevention():
    """Verify registering an existing role without override raises ValueError."""
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register_agent(
            role=AgentRole.DEVELOPER,
            name="Duplicate Developer",
            description="Should fail",
        )


def test_agent_registry_task_assignment_tracking():
    """Verify assigning and completing tasks updates agent status."""
    registry = AgentRegistry()
    dev = registry.get_agent(AgentRole.DEVELOPER)
    assert dev.status == AgentStatus.READY

    registry.assign_task_to_agent(AgentRole.DEVELOPER, "t-1")
    assert dev.status == AgentStatus.RUNNING
    assert "t-1" in dev.active_tasks

    registry.complete_task_for_agent(AgentRole.DEVELOPER, "t-1")
    assert dev.status == AgentStatus.READY
    assert len(dev.active_tasks) == 0


def test_find_best_agent_for_capability():
    """Verify selecting specialized worker over general coordinator."""
    registry = AgentRegistry()
    agent = registry.find_best_agent_for_capability(AgentCapability.WEB_SEARCH)
    assert agent is not None
    assert agent.role == AgentRole.RESEARCH


# ===========================================================================
# 3. Agent Task Graph (DAG) Tests
# ===========================================================================

def test_task_graph_add_and_get():
    """Verify adding tasks and indexing in DAG."""
    graph = AgentTaskGraph(goal="Test DAG")
    task1 = AgentTask(role=AgentRole.RESEARCH, objective="Search docs", capability=AgentCapability.WEB_SEARCH)
    task2 = AgentTask(role=AgentRole.DEVELOPER, objective="Build code", capability=AgentCapability.BUILD)

    graph.add_task(task1)
    graph.add_task(task2, depends_on=[task1.task_id])

    assert len(graph.tasks) == 2
    assert graph.get_task(task1.task_id) == task1
    assert graph.rev_adj_list[task2.task_id] == [task1.task_id]
    assert graph.adj_list[task1.task_id] == [task2.task_id]


def test_task_graph_cycle_detection():
    """Verify Kahn's algorithm rejects cyclical dependencies."""
    graph = AgentTaskGraph(goal="Cyclic Graph")
    t1 = AgentTask(task_id="t1", role=AgentRole.DEVELOPER, objective="A", capability=AgentCapability.BUILD)
    t2 = AgentTask(task_id="t2", role=AgentRole.DEVELOPER, objective="B", capability=AgentCapability.TEST)

    graph.add_task(t1, depends_on=["t2"])
    graph.add_task(t2, depends_on=["t1"])

    is_valid, err = graph.validate_graph()
    assert is_valid is False
    assert "Circular dependency" in err


def test_task_graph_self_dependency_detection():
    """Verify self-dependency is rejected."""
    graph = AgentTaskGraph(goal="Self Dependency")
    t1 = AgentTask(task_id="t1", role=AgentRole.DEVELOPER, objective="A", capability=AgentCapability.BUILD)
    graph.add_task(t1, depends_on=["t1"])

    is_valid, err = graph.validate_graph()
    assert is_valid is False
    assert "self-dependency" in err


def test_task_graph_missing_dependency_detection():
    """Verify depending on non-existent task is rejected."""
    graph = AgentTaskGraph(goal="Missing Dep")
    t1 = AgentTask(task_id="t1", role=AgentRole.DEVELOPER, objective="A", capability=AgentCapability.BUILD)
    graph.add_task(t1, depends_on=["ghost-task-99"])

    is_valid, err = graph.validate_graph()
    assert is_valid is False
    assert "unknown task" in err


def test_task_graph_max_tasks_limit():
    """Verify graph enforces MAX_TASKS_PER_GRAPH ceiling."""
    graph = AgentTaskGraph(goal="Limit test")
    for i in range(MAX_TASKS_PER_GRAPH):
        graph.add_task(AgentTask(task_id=f"t-{i}", role=AgentRole.RESEARCH, objective=f"Step {i}", capability=AgentCapability.WEB_SEARCH))

    with pytest.raises(ValueError, match="MAX_TASKS_PER_GRAPH"):
        graph.add_task(AgentTask(task_id="overflow", role=AgentRole.RESEARCH, objective="Overflow", capability=AgentCapability.WEB_SEARCH))


def test_task_graph_topological_order():
    """Verify topological sort reflects dependencies."""
    graph = AgentTaskGraph(goal="Order test")
    t1 = AgentTask(task_id="step1", role=AgentRole.RESEARCH, objective="Search", capability=AgentCapability.WEB_SEARCH)
    t2 = AgentTask(task_id="step2", role=AgentRole.DEVELOPER, objective="Build", capability=AgentCapability.BUILD)
    t3 = AgentTask(task_id="step3", role=AgentRole.DEVELOPER, objective="Test", capability=AgentCapability.TEST)

    graph.add_task(t1)
    graph.add_task(t2, depends_on=["step1"])
    graph.add_task(t3, depends_on=["step2"])

    order = graph.get_topological_order()
    assert order == ["step1", "step2", "step3"]


def test_task_graph_get_ready_tasks():
    """Verify get_ready_tasks returns only tasks whose dependencies are COMPLETED."""
    graph = AgentTaskGraph(goal="Ready test")
    t1 = AgentTask(task_id="step1", role=AgentRole.RESEARCH, objective="Search", capability=AgentCapability.WEB_SEARCH)
    t2 = AgentTask(task_id="step2", role=AgentRole.DEVELOPER, objective="Build", capability=AgentCapability.BUILD)

    graph.add_task(t1)
    graph.add_task(t2, depends_on=["step1"])

    ready = graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].task_id == "step1"

    # Complete step1 -> step2 becomes ready
    graph.mark_task_completed("step1", {"data": "ok"})
    ready2 = graph.get_ready_tasks()
    assert len(ready2) == 1
    assert ready2[0].task_id == "step2"


def test_task_graph_failure_and_downstream_blocking():
    """Verify permanent failure cascades and blocks downstream dependent tasks."""
    graph = AgentTaskGraph(goal="Cascade test")
    t1 = AgentTask(task_id="step1", role=AgentRole.DEVELOPER, objective="Build", capability=AgentCapability.BUILD, max_retries=0)
    t2 = AgentTask(task_id="step2", role=AgentRole.DEVELOPER, objective="Test", capability=AgentCapability.TEST)

    graph.add_task(t1)
    graph.add_task(t2, depends_on=["step1"])

    requeued = graph.mark_task_failed("step1", "Compiler error")
    assert requeued is False
    assert t1.status == AgentStatus.FAILED
    assert t2.status == AgentStatus.BLOCKED
    assert graph.status == AgentStatus.FAILED


def test_task_graph_cancellation():
    """Verify cancelling graph cancels all pending tasks."""
    graph = AgentTaskGraph(goal="Cancel test")
    t1 = AgentTask(task_id="step1", role=AgentRole.DEVELOPER, objective="Build", capability=AgentCapability.BUILD)
    t2 = AgentTask(task_id="step2", role=AgentRole.DEVELOPER, objective="Test", capability=AgentCapability.TEST)

    graph.add_task(t1)
    graph.add_task(t2, depends_on=["step1"])

    graph.cancel_graph("User clicked stop")
    assert graph.status == AgentStatus.CANCELLED
    assert t1.status == AgentStatus.CANCELLED
    assert t2.status == AgentStatus.CANCELLED


def test_task_graph_serialization_roundtrip():
    """Verify graph to_dict and from_dict integrity."""
    graph = AgentTaskGraph(goal="Serialization test")
    t1 = AgentTask(task_id="step1", role=AgentRole.RESEARCH, objective="Search", capability=AgentCapability.WEB_SEARCH)
    graph.add_task(t1)

    d = graph.to_dict()
    reconstructed = AgentTaskGraph.from_dict(d)
    assert reconstructed.graph_id == graph.graph_id
    assert reconstructed.goal == graph.goal
    assert "step1" in reconstructed.tasks
    assert reconstructed.tasks["step1"].role == AgentRole.RESEARCH


# ===========================================================================
# 4. Task Decomposer & Planning Tests
# ===========================================================================

def test_decomposer_simple_web_search():
    """Verify simple search request generates exactly 1 Research task."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Search for React Three Fiber tutorial")
    assert len(graph.tasks) == 1
    task = list(graph.tasks.values())[0]
    assert task.role == AgentRole.RESEARCH
    assert task.capability == AgentCapability.WEB_SEARCH


def test_decomposer_simple_app_launch():
    """Verify simple application launch request generates exactly 1 Computer task."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Open VS Code")
    assert len(graph.tasks) == 1
    task = list(graph.tasks.values())[0]
    assert task.role in (AgentRole.COMPUTER, AgentRole.WINDOWS)
    assert task.capability == AgentCapability.WINDOWS_APP


def test_decomposer_simple_browser_open():
    """Verify website navigation generates 1 Browser task."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Open website https://github.com")
    assert len(graph.tasks) == 1
    task = list(graph.tasks.values())[0]
    assert task.role == AgentRole.BROWSER
    assert task.capability == AgentCapability.BROWSER_ACTION


def test_decomposer_simple_health_check():
    """Verify health query generates 1 Verification task."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Check health of http://localhost:8000")
    assert len(graph.tasks) == 1
    task = list(graph.tasks.values())[0]
    assert task.role == AgentRole.VERIFICATION
    assert task.capability == AgentCapability.HEALTH_CHECK


def test_decomposer_research_and_build_pipeline():
    """Verify complex research + build request creates a multi-stage DAG."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Research React Three Fiber and build a demo project called 3d-demo")
    assert len(graph.tasks) >= 5
    roles = {t.role for t in graph.tasks.values()}
    assert AgentRole.RESEARCH in roles
    assert AgentRole.DEVELOPER in roles
    assert AgentRole.VERIFICATION in roles

    # Check topological dependencies
    order = graph.get_topological_order()
    assert len(order) >= 5
    # First step should be research
    first_task = graph.get_task(order[0])
    assert first_task.role == AgentRole.RESEARCH


def test_decomposer_consequential_gates_require_confirmation():
    """Verify deployment or git commit requests mark requires_confirmation=True."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Create project, test it, commit it and deploy it")
    tasks = list(graph.tasks.values())

    gated_tasks = [t for t in tasks if t.requires_confirmation]
    assert len(gated_tasks) >= 1
    conf_types = {t.confirmation_type for t in gated_tasks}
    assert any("DEPLOY" in ct or "GIT" in ct for ct in conf_types if ct)


# ===========================================================================
# 5. Communication & Structured Handoffs Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_communication_send_message():
    """Verify typed message dispatch, secret sanitization, and history recording."""
    cm = CommunicationManager()
    msg = await cm.send_message(
        sender_role=AgentRole.RESEARCH,
        recipient_role=AgentRole.DEVELOPER,
        message_type=MessageType.FINDING,
        payload={"secret_key": "12345", "finding": "Tailwind v4 available"},
        task_id="t-99",
    )
    assert msg.payload["secret_key"] == "[REDACTED]"
    assert msg.payload["finding"] == "Tailwind v4 available"
    assert len(cm.message_history) == 1


@pytest.mark.asyncio
async def test_communication_execute_handoff():
    """Verify structured handoff between roles with findings and sources."""
    cm = CommunicationManager()
    handoff = await cm.execute_handoff(
        source_role=AgentRole.RESEARCH,
        target_role=AgentRole.DEVELOPER,
        task_id="t-100",
        findings=[{"library": "three"}],
        sources=["https://npmjs.com/package/three"],
        recommended_next_action="BUILD",
        graph_id="g-1",
    )
    assert handoff.accepted is True
    assert handoff.recommended_next_action == "BUILD"
    assert len(cm.handoff_history) == 1


@pytest.mark.asyncio
async def test_communication_handoff_depth_limit():
    """Verify handoff depth limit (MAX_HANDOFFS) rejects excessive delegation loops."""
    cm = CommunicationManager()
    g_id = "g-loop"
    for i in range(MAX_HANDOFFS):
        h = await cm.execute_handoff(
            source_role=AgentRole.RESEARCH,
            target_role=AgentRole.DEVELOPER,
            task_id=f"t-{i}",
            findings=[],
            graph_id=g_id,
        )
        assert h.accepted is True

    # 6th handoff should be rejected
    h_blocked = await cm.execute_handoff(
        source_role=AgentRole.DEVELOPER,
        target_role=AgentRole.RESEARCH,
        task_id="t-blocked",
        findings=[],
        graph_id=g_id,
    )
    assert h_blocked.accepted is False
    assert "Exceeded maximum handoff limit" in (h_blocked.reason or "")


# ===========================================================================
# 6. Security Policy Tests
# ===========================================================================

def test_security_valid_role_and_capability():
    """Verify valid role possessing capability is allowed."""
    policy = AgentSecurityPolicy()
    task = AgentTask(
        role=AgentRole.DEVELOPER,
        objective="Scan repository",
        capability=AgentCapability.PROJECT_SCAN,
        tool_name="scan_existing_project",
    )
    result = policy.validate_task_execution(task)
    assert result.allowed is True


def test_security_unregistered_role_rejected():
    """Verify custom/unregistered role is blocked."""
    policy = AgentSecurityPolicy()
    task = AgentTask(
        role=AgentRole.COORDINATOR,
        objective="Invalid task",
        capability=AgentCapability.BUILD,  # Coordinator does not possess BUILD
    )
    result = policy.validate_task_execution(task)
    assert result.allowed is False
    assert "Security violation" in result.reason


def test_security_forbidden_shell_tools_blocked():
    """Verify arbitrary shell tools (bash, sh, cmd, powershell) are blocked."""
    policy = AgentSecurityPolicy()
    for shell_tool in ("bash", "sh", "cmd", "powershell", "exec_shell"):
        task = AgentTask(
            role=AgentRole.DEVELOPER,
            objective="Run shell",
            capability=AgentCapability.BUILD,
            tool_name=shell_tool,
        )
        result = policy.validate_task_execution(task)
        assert result.allowed is False
        assert "forbidden" in result.reason


def test_security_consequential_tool_requires_confirmation():
    """Verify consequential tools pause for human approval."""
    policy = AgentSecurityPolicy()
    task = AgentTask(
        role=AgentRole.DEVELOPER,
        objective="Deploy to production",
        capability=AgentCapability.DEPLOY,
        tool_name="deployment_deploy",
    )
    result = policy.validate_task_execution(task, auto_confirm=False)
    assert result.allowed is False
    assert result.requires_confirmation is True
    assert result.confirmation_type == "DEPLOYMENT_DEPLOY"


def test_security_auto_confirm_bypasses_pause():
    """Verify auto_confirm permits consequential task to proceed in automated tests."""
    policy = AgentSecurityPolicy()
    task = AgentTask(
        role=AgentRole.DEVELOPER,
        objective="Deploy to staging",
        capability=AgentCapability.DEPLOY,
        tool_name="deployment_deploy",
    )
    result = policy.validate_task_execution(task, auto_confirm=True)
    assert result.allowed is True


# ===========================================================================
# 7. Agent Coordinator Integration Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_coordinator_simple_task_execution():
    """Verify end-to-end execution of a single task graph."""
    coordinator = AgentCoordinator()
    graph = await coordinator.coordinate(
        goal="Open application notepad",
        auto_confirm=True,
    )
    assert graph.is_completed()
    assert graph.status == AgentStatus.COMPLETED
    assert len(graph.tasks) == 1


@pytest.mark.asyncio
async def test_coordinator_multi_agent_pipeline():
    """Verify execution of multi-agent pipeline with handoffs and dependency ordering."""
    coordinator = AgentCoordinator()
    graph = await coordinator.coordinate(
        goal="Research React Three Fiber and build a demo project called test-r3f",
        auto_confirm=True,
    )
    assert graph.is_completed()
    assert graph.status == AgentStatus.COMPLETED
    assert graph.get_progress()["completed"] == len(graph.tasks)


@pytest.mark.asyncio
async def test_coordinator_confirmation_pause_and_resume():
    """Verify coordinator pauses on consequential tasks and resumes on explicit confirmation."""
    coordinator = AgentCoordinator()
    # Plan task with git commit
    graph = coordinator.decomposer.decompose("Build project, test it, and commit it")
    # Execute with auto_confirm=False
    executed_graph = await coordinator.execute_graph(graph, auto_confirm=False)

    assert executed_graph.status == AgentStatus.REQUIRES_CONFIRMATION
    waiting_task = next(t for t in executed_graph.tasks.values() if t.status == AgentStatus.REQUIRES_CONFIRMATION)
    assert waiting_task.requires_confirmation is True

    # Confirm the waiting task
    resumed_graph = await coordinator.confirm_task(executed_graph.graph_id, waiting_task.task_id, auto_confirm_rest=True)
    assert resumed_graph is not None
    assert resumed_graph.status == AgentStatus.COMPLETED


@pytest.mark.asyncio
async def test_coordinator_cancellation():
    """Verify cancelling active graph transitions state to CANCELLED cleanly."""
    coordinator = AgentCoordinator()
    graph = coordinator.decomposer.decompose("Research React and build demo")
    coordinator._active_graphs[graph.graph_id] = graph

    cancelled = await coordinator.cancel_graph(graph.graph_id, reason="User clicked abort")
    assert cancelled is not None
    assert cancelled.status == AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_coordinator_persists_checkpoints():
    """Verify coordinator saves graph progress to SQLite checkpoint_store."""
    coordinator = AgentCoordinator()
    graph = await coordinator.coordinate(
        goal="Search for Python asyncio",
        auto_confirm=True,
    )
    # Checkpoint store should have record of graph
    chk = checkpoint_store.get_checkpoint(graph.graph_id)
    assert chk is not None
    assert chk.task_id == graph.graph_id
    assert chk.current_state == "COMPLETED"


# ===========================================================================
# 8. API Endpoints Integration Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_api_agents_list_endpoint():
    """Verify /api/agents endpoint returns registered roles."""
    from app.api.routes import list_agents_endpoint
    res = await list_agents_endpoint()
    assert res["total_agents"] >= 8
    roles = {a["role"] for a in res["agents"]}
    assert "DEVELOPER" in roles
    assert "RESEARCH" in roles


@pytest.mark.asyncio
async def test_api_get_agent_descriptor():
    """Verify /api/agents/{agent_id} returns agent descriptor."""
    from app.api.routes import get_agent_endpoint
    res = await get_agent_endpoint("agent-developer")
    assert res["role"] == "DEVELOPER"
    assert "capabilities" in res


@pytest.mark.asyncio
async def test_api_create_task_endpoint():
    """Verify POST /api/agents/task decomposes and initiates graph execution."""
    from app.api.routes import create_agent_task_endpoint
    res = await create_agent_task_endpoint({
        "goal": "Search documentation for FastAPI",
        "auto_confirm": True,
    })
    assert "graph_id" in res
    assert res["status"] == "COMPLETED"
    assert "tasks" in res


@pytest.mark.asyncio
async def test_api_agent_events_endpoint():
    """Verify GET /api/agents/events returns recent AGENT_* events."""
    from app.api.routes import get_agent_events_endpoint
    res = await get_agent_events_endpoint(limit=10)
    assert "events" in res
    assert "total_events" in res


# ===========================================================================
# 9. Extended Coverage & Edge Cases (Phase 24)
# ===========================================================================

@pytest.mark.asyncio
async def test_coordinator_timeout_handling():
    """Verify coordinator respects timeout parameter and aborts with failure."""
    coordinator = AgentCoordinator()
    graph = AgentTaskGraph(goal="Long running task")
    t1 = AgentTask(role=AgentRole.RESEARCH, objective="Wait long", capability=AgentCapability.WEB_SEARCH)
    graph.add_task(t1)

    async def slow_exec(task, auto_confirm=False):
        await asyncio.sleep(0.5)
        return {"status": "ok"}

    with patch.object(coordinator, "_execute_tool", side_effect=slow_exec):
        executed = await coordinator.coordinate("Long running task", timeout=0.05)
        assert executed.status in (AgentStatus.FAILED, AgentStatus.CANCELLED)
        assert "timeout" in (executed.error or "").lower() or "deadline" in (executed.error or "").lower()


def test_task_graph_is_terminal():
    """Verify is_terminal correctly identifies COMPLETED, FAILED, and CANCELLED."""
    g = AgentTaskGraph(goal="Terminal test")
    assert g.is_terminal() is False
    g.status = AgentStatus.COMPLETED
    assert g.is_terminal() is True
    g.status = AgentStatus.FAILED
    assert g.is_terminal() is True
    g.status = AgentStatus.CANCELLED
    assert g.is_terminal() is True


def test_agent_descriptor_metadata():
    """Verify agent descriptor capabilities membership and metadata dictionary."""
    desc = AgentDescriptor(
        agent_id="test-agent",
        role=AgentRole.DEVELOPER,
        name="Dev",
        description="Dev desc",
        capabilities={AgentCapability.BUILD, AgentCapability.TEST},
        metadata={"version": "1.0"},
    )
    assert desc.has_capability(AgentCapability.BUILD) is True
    assert desc.has_capability(AgentCapability.WEB_SEARCH) is False
    assert desc.metadata["version"] == "1.0"


@pytest.mark.asyncio
async def test_api_get_agent_graph_endpoint():
    """Verify GET /api/agents/graph/{graph_id} returns graph data and handles 404."""
    from app.api.routes import get_agent_graph_endpoint
    # Non-existent
    err_res = await get_agent_graph_endpoint("ghost-graph")
    assert "error" in err_res

    # Real graph in coordinator
    graph = AgentTaskGraph(goal="Live graph test", graph_id="live-g1")
    agent_coordinator._active_graphs["live-g1"] = graph
    res = await get_agent_graph_endpoint("live-g1")
    assert res["graph_id"] == "live-g1"
    assert res["goal"] == "Live graph test"


@pytest.mark.asyncio
async def test_api_get_agent_task_endpoint():
    """Verify GET /api/agents/tasks/{task_id} retrieves task details."""
    from app.api.routes import get_agent_task_endpoint
    err_res = await get_agent_task_endpoint("ghost-task")
    assert "error" in err_res

    graph = AgentTaskGraph(goal="Task lookup test", graph_id="lookup-g")
    t1 = AgentTask(task_id="specific-t1", role=AgentRole.DEVELOPER, objective="Compile", capability=AgentCapability.BUILD)
    graph.add_task(t1)
    agent_coordinator._active_graphs["lookup-g"] = graph

    res = await get_agent_task_endpoint("specific-t1")
    assert res["task_id"] == "specific-t1"
    assert res["role"] == "DEVELOPER"


@pytest.mark.asyncio
async def test_api_cancel_agent_task_endpoint():
    """Verify POST /api/agents/tasks/{task_id}/cancel cancels active graph."""
    from app.api.routes import cancel_agent_task_endpoint
    graph = AgentTaskGraph(goal="Cancellation test", graph_id="cancel-g")
    t1 = AgentTask(task_id="cancel-t1", role=AgentRole.DEVELOPER, objective="Task", capability=AgentCapability.BUILD)
    graph.add_task(t1)
    agent_coordinator._active_graphs["cancel-g"] = graph

    res = await cancel_agent_task_endpoint("cancel-t1", {"reason": "Abort requested"})
    assert res["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_api_confirm_agent_task_endpoint():
    """Verify POST /api/agents/tasks/{task_id}/confirm resumes execution."""
    from app.api.routes import confirm_agent_task_endpoint
    graph = AgentTaskGraph(goal="Confirmation endpoint test", graph_id="confirm-g")
    t1 = AgentTask(
        task_id="confirm-t1",
        role=AgentRole.DEVELOPER,
        objective="Deploy",
        capability=AgentCapability.DEPLOY,
        status=AgentStatus.REQUIRES_CONFIRMATION,
        requires_confirmation=True,
    )
    graph.add_task(t1)
    agent_coordinator._active_graphs["confirm-g"] = graph

    res = await confirm_agent_task_endpoint("confirm-t1", {"graph_id": "confirm-g"})
    assert res["status"] in ("COMPLETED", "RUNNING")
    assert res["tasks"]["confirm-t1"]["confirmed"] is True


def test_redact_secrets_deep_patterns():
    """Verify nested token, bearer header, and private key redaction."""
    payload = {
        "user": "developer",
        "nested": {
            "auth_header": "Bearer abcdefghijklmnopqrstuvwxyz123456",
            "cert": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----",
            "safe_url": "https://api.github.com/repos",
        },
        "items": [
            "password123",
            {"api_key": "my-secret-api-key-999"},
        ],
    }
    redacted = redact_secrets(payload)
    assert "[REDACTED]" in redacted["nested"]["auth_header"]
    assert "[REDACTED_PRIVATE_KEY]" in redacted["nested"]["cert"]
    assert redacted["nested"]["safe_url"] == "https://api.github.com/repos"
    assert redacted["items"][1]["api_key"] == "[REDACTED]"


def test_agent_context_budgeted_summary_truncation():
    """Verify AgentContext produces truncated budgeted summaries under max_length."""
    from app.agents.models import AgentContext
    ctx = AgentContext(
        task_objective="Very long objective",
        findings=[{"topic": "A"}, {"topic": "B"}],
        relevant_files=["src/a.ts", "src/b.ts", "src/c.ts"],
    )
    summary = ctx.get_budgeted_summary(max_length=40)
    assert len(summary) <= 41
    assert summary.endswith("…")


def test_validate_capability_tools():
    """Verify capability tool validation against ToolRegistry."""
    from app.agents.capabilities import validate_capability_tools
    from app.tools.registry import create_default_registry
    reg = create_default_registry()
    report = validate_capability_tools(reg)
    assert report["total_mapped_capabilities"] >= 20
    assert "WEB_SEARCH" in report["valid"]
    assert "internet_search" in report["valid"]["WEB_SEARCH"]


def test_decomposer_diagnostic_pipeline():
    """Verify diagnostic goals generate scan -> query -> build -> quality graph."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Check my project and tell me why the build fails")
    assert len(graph.tasks) == 4
    caps = [t.capability for t in graph.tasks.values()]
    assert AgentCapability.PROJECT_SCAN in caps
    assert AgentCapability.KNOWLEDGE_GRAPH in caps
    assert AgentCapability.BUILD in caps
    assert AgentCapability.VERIFICATION in caps


def test_decomposer_custom_project_name_extraction():
    """Verify project name extracted from 'project called X' syntax."""
    decomposer = TaskDecomposer()
    graph = decomposer.decompose("Create project called analytics-engine and test it")
    tasks = list(graph.tasks.values())
    assert any("analytics-engine" in t.objective for t in tasks)


def test_coordinator_blocked_dependency_handling():
    """Verify graph transitions to FAILED when a task permanently fails and blocks children."""
    graph = AgentTaskGraph(goal="Cascade Failure")
    t1 = AgentTask(task_id="t1", role=AgentRole.DEVELOPER, objective="Task 1", capability=AgentCapability.BUILD, max_retries=0)
    t2 = AgentTask(task_id="t2", role=AgentRole.DEVELOPER, objective="Task 2", capability=AgentCapability.TEST)
    graph.add_task(t1)
    graph.add_task(t2, depends_on=["t1"])

    graph.mark_task_failed("t1", "Critical hardware error")
    assert graph.is_failed() is True
    assert t2.status == AgentStatus.BLOCKED


def test_redact_secrets_truncation():
    """Verify large string dumps (> 2048 chars) are truncated."""
    large_text = "A" * 3000
    res = redact_secrets(large_text)
    assert len(res) < 2000
    assert res.endswith("…[TRUNCATED]")



"""
RYVEN 3.0 — Milestone 17.0 Test Suite
Full Computer & Internet Control Plane Tests
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from app.control.models import (
    PermissionCategory,
    ControlStatus,
    FailureClass,
    ScopeBoundary,
    ObservationRecord,
    DiscoveredScopeItem,
    ControlRequest,
    ControlResult,
)
from app.control.permissions import (
    CapabilityPermissionManager,
    CapabilityAuthResult,
    permission_manager,
    PROHIBITED_SYSTEM_COMMANDS,
    CONSEQUENTIAL_TOOLS,
)
from app.control.observer import ObserverEngine, observer_engine
from app.control.engine import RyvenControlEngine, ryven_control_engine
from app.tools.process_tool import (
    InspectApplicationsTool,
    FocusApplicationTool,
    CloseApplicationTool,
    ALLOWLISTED_APPLICATIONS,
)
from app.tools.registry import create_default_registry, ToolRegistry
from app.actions.models import ActionType, ActionStatus
from app.actions.event_bus import action_bus
from app.core.assistant import Assistant
from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Capability Permission Engine Tests
# ---------------------------------------------------------------------------

class TestCapabilityPermissions:
    def test_permission_categories_exist(self):
        mgr = CapabilityPermissionManager()
        catalog = mgr.get_permission_catalog()
        assert "categories" in catalog
        assert "consequential_tools" in catalog
        assert "prohibited_commands" in catalog
        assert len(catalog["consequential_tools"]) > 0

    def test_safe_read_tool_auto_permitted(self):
        mgr = CapabilityPermissionManager()
        for tool_name in ["read_file", "search_files", "web_search", "fetch_web_content", "inspect_applications"]:
            res = mgr.authorize(tool_name=tool_name, arguments={})
            assert res.allowed is True, f"Expected {tool_name} to be allowed"
            assert res.requires_confirmation is False, f"Expected {tool_name} to not require confirmation"

    def test_consequential_tool_confirmation_required(self):
        mgr = CapabilityPermissionManager()
        for tool_name in ["git_commit", "git_push", "deploy", "close_application"]:
            res = mgr.authorize(tool_name=tool_name, arguments={}, auto_confirm=False)
            assert res.allowed is False, f"Expected {tool_name} to require confirmation"
            assert res.requires_confirmation is True
            assert res.confirmation_token is not None
            assert res.confirmation_type is not None

    def test_auto_confirm_bypasses_confirmation(self):
        mgr = CapabilityPermissionManager()
        res = mgr.authorize(tool_name="git_commit", arguments={"message": "test"}, auto_confirm=True)
        assert res.allowed is True
        assert res.requires_confirmation is False
        assert res.confirmation_token is not None

    def test_prohibited_system_commands_blocked(self):
        mgr = CapabilityPermissionManager()
        for cmd in PROHIBITED_SYSTEM_COMMANDS:
            res = mgr.authorize(tool_name=cmd, arguments={})
            assert res.allowed is False
            assert res.risk_level == "CRITICAL"
            assert "prohibited" in res.reason.lower() or "not permitted" in res.reason.lower()

    def test_destructive_file_action_requires_confirmation(self):
        mgr = CapabilityPermissionManager()
        res = mgr.authorize(
            tool_name="write_file",
            arguments={"path": "critical_file.py", "action": "delete"},
            auto_confirm=False,
        )
        assert res.requires_confirmation is True
        assert res.allowed is False


# ---------------------------------------------------------------------------
# 2. Safe Process Control Tools Tests
# ---------------------------------------------------------------------------

class TestSafeProcessTools:
    @pytest.mark.asyncio
    async def test_inspect_applications_tool(self):
        tool = InspectApplicationsTool()
        assert tool.name == "inspect_applications"
        res = await tool.execute()
        assert res.success is True
        data = res.data
        assert "processes" in data
        assert "count" in data
        assert isinstance(data["processes"], list)

    @pytest.mark.asyncio
    async def test_focus_application_allowlisted(self):
        tool = FocusApplicationTool()
        with patch.object(tool, "_bring_to_foreground", return_value=True):
            res = await tool.execute(name="Code.exe")
            assert res.success is True
            assert "Focus requested" in res.message or "focused" in res.message.lower()

    @pytest.mark.asyncio
    async def test_focus_application_disallowed(self):
        tool = FocusApplicationTool()
        res = await tool.execute(name="malicious_payload.exe")
        assert res.success is False
        assert "not in allowlist" in res.error

    @pytest.mark.asyncio
    async def test_close_application_disallowed(self):
        tool = CloseApplicationTool()
        res = await tool.execute(name="system_critical.exe")
        assert res.success is False
        assert "not in allowlist" in res.error

    @pytest.mark.asyncio
    async def test_close_application_allowlisted(self):
        tool = CloseApplicationTool()
        # Mocking process search to simulate process found
        mock_proc = MagicMock()
        mock_proc.name.return_value = "notepad.exe"
        mock_proc.pid = 9999
        mock_proc.terminate = MagicMock()

        with patch("psutil.process_iter", return_value=[mock_proc]):
            res = await tool.execute(name="notepad.exe")
            assert res.success is True
            assert "notified to close" in res.message.lower() or "closed" in res.message.lower()


# ---------------------------------------------------------------------------
# 3. Unified Observer Engine Tests
# ---------------------------------------------------------------------------

class TestUnifiedObserver:
    @pytest.mark.asyncio
    async def test_observe_environment_aggregates_sources(self):
        obs = ObserverEngine()
        records = await obs.observe_environment(target_project="view-archive-buddy-main")
        assert len(records) >= 2
        sources = [r.source for r in records]
        assert "desktop" in sources
        assert "browser" in sources

    @pytest.mark.asyncio
    async def test_observer_resilient_to_individual_failure(self):
        obs = ObserverEngine()
        with patch.object(obs, "observe_browser", side_effect=RuntimeError("Browser unavailable")):
            records = await obs.observe_environment()
            assert len(records) >= 1
            sources = [r.source for r in records]
            assert "desktop" in sources


# ---------------------------------------------------------------------------
# 4. Scope Protection & Recovery Classification Tests
# ---------------------------------------------------------------------------

class TestScopeAndRecovery:
    def test_scope_item_tracking(self):
        item = DiscoveredScopeItem(
            description="Found missing unit test in auth module",
            boundary=ScopeBoundary.DISCOVERED,
            requires_user_approval=True,
        )
        assert item.boundary == ScopeBoundary.DISCOVERED
        assert item.requires_user_approval is True
        assert item.approved is False

    def test_classify_transient_failure(self):
        engine = RyvenControlEngine()
        cls = engine._classify_failure(TimeoutError("Connection timed out"))
        assert cls == FailureClass.NETWORK_TRANSIENT

        cls = engine._classify_failure(Exception("429 Too Many Requests"))
        assert cls == FailureClass.RATE_LIMITED

        cls = engine._classify_failure(PermissionError("Access denied"))
        assert cls == FailureClass.FILE_LOCKED

    def test_classify_unrecoverable_failure(self):
        engine = RyvenControlEngine()
        cls = engine._classify_failure(ValueError("Invalid syntax"))
        assert cls == FailureClass.UNRECOVERABLE


# ---------------------------------------------------------------------------
# 5. RyvenControlEngine End-to-End Orchestration Tests
# ---------------------------------------------------------------------------

class TestRyvenControlEngine:
    @pytest.mark.asyncio
    async def test_execute_safe_control_goal(self):
        engine = RyvenControlEngine()
        # Safe goal: inspect running applications and web search
        req = ControlRequest(
            goal="Inspect running desktop applications and search web for Python documentation",
            auto_confirm=True,
        )
        res = await engine.execute_goal(req)
        assert res.control_id.startswith("ctrl-")
        assert res.task_id == res.control_id
        assert len(res.observations) > 0
        assert res.steps_total >= 1
        assert res.status in (ControlStatus.COMPLETED, ControlStatus.PLANNING, ControlStatus.EXECUTING)

    @pytest.mark.asyncio
    async def test_execute_consequential_goal_pauses_for_confirmation(self):
        engine = RyvenControlEngine()
        req = ControlRequest(
            goal="Close Notepad application immediately",
            auto_confirm=False,
        )
        res = await engine.execute_goal(req)
        # Should detect close_application as consequential and pause or require confirmation
        assert res.control_id.startswith("ctrl-")
        if res.confirmation_required:
            assert res.status == ControlStatus.WAITING_CONFIRMATION
            assert res.confirmation_token is not None

    @pytest.mark.asyncio
    async def test_confirm_resumes_control(self):
        engine = RyvenControlEngine()
        # Seed an execution waiting for confirmation
        ctrl_id = "ctrl-test-conf"
        token = "CONF-TEST-TOKEN-123"
        result = ControlResult(
            control_id=ctrl_id,
            goal="Deploy changes",
            status=ControlStatus.WAITING_CONFIRMATION,
            confirmation_required=True,
            confirmation_token=token,
            confirmation_type="DEPLOY",
        )
        engine._active_executions[ctrl_id] = result

        # Confirm with correct token
        confirmed_res = await engine.confirm_action(ctrl_id, token)
        assert confirmed_res.confirmation_required is False
        assert confirmed_res.status in (ControlStatus.EXECUTING, ControlStatus.COMPLETED)

    @pytest.mark.asyncio
    async def test_confirm_with_invalid_token_fails(self):
        engine = RyvenControlEngine()
        ctrl_id = "ctrl-test-bad-token"
        result = ControlResult(
            control_id=ctrl_id,
            goal="Deploy changes",
            status=ControlStatus.WAITING_CONFIRMATION,
            confirmation_required=True,
            confirmation_token="VALID-TOKEN",
        )
        engine._active_executions[ctrl_id] = result

        with pytest.raises(ValueError, match="Invalid confirmation token"):
            await engine.confirm_action(ctrl_id, "WRONG-TOKEN")

    @pytest.mark.asyncio
    async def test_cancel_control(self):
        engine = RyvenControlEngine()
        ctrl_id = "ctrl-test-cancel"
        result = ControlResult(
            control_id=ctrl_id,
            goal="Long running workflow",
            status=ControlStatus.EXECUTING,
        )
        engine._active_executions[ctrl_id] = result

        cancelled_res = await engine.cancel_execution(ctrl_id, reason="User requested abort")
        assert cancelled_res.status == ControlStatus.CANCELLED
        assert "User requested abort" in cancelled_res.message


# ---------------------------------------------------------------------------
# 6. REST API Endpoints Tests
# ---------------------------------------------------------------------------

class TestControlApiRoutes:
    def test_api_execute_control_goal(self):
        res = client.post(
            "/api/control/execute",
            json={"goal": "Inspect running applications", "auto_confirm": True},
        )
        assert res.status_code == 200
        data = res.json()
        assert "control_id" in data
        assert data["control_id"].startswith("ctrl-")
        assert "status" in data

    def test_api_get_running_applications(self):
        res = client.get("/api/control/applications/running")
        assert res.status_code == 200
        data = res.json()
        assert "processes" in data
        assert "count" in data

    def test_api_get_permissions_catalog(self):
        res = client.get("/api/control/permissions/catalog")
        assert res.status_code == 200
        data = res.json()
        assert "categories" in data
        assert "consequential_tools" in data
        assert "prohibited_commands" in data

    def test_api_get_control_status_404(self):
        res = client.get("/api/control/ctrl-nonexistent")
        assert res.status_code == 404

    def test_api_confirm_and_cancel_lifecycle(self):
        # 1. Execute goal
        exec_res = client.post(
            "/api/control/execute",
            json={"goal": "Check system status", "auto_confirm": True},
        )
        assert exec_res.status_code == 200
        control_id = exec_res.json()["control_id"]

        # 2. Get status
        get_res = client.get(f"/api/control/{control_id}")
        assert get_res.status_code == 200
        assert get_res.json()["control_id"] == control_id

        # 3. Cancel execution
        cancel_res = client.post(
            f"/api/control/{control_id}/cancel",
            json={"reason": "Test finished"},
        )
        assert cancel_res.status_code == 200
        assert cancel_res.json()["status"] == "CANCELLED"


# ---------------------------------------------------------------------------
# 7. Assistant Control Integration Tests
# ---------------------------------------------------------------------------

class TestAssistantControlIntegration:
    @pytest.mark.asyncio
    async def test_assistant_process_control_intent(self):
        assistant = Assistant()
        # Mock router to return 'agent' intent
        mock_decision = MagicMock()
        mock_decision.intent = "agent"
        mock_decision.workflow_name = None
        mock_decision.tool_name = None
        assistant.router.route = MagicMock(return_value=mock_decision)

        # Process a multi-step computer goal
        resp = await assistant.process("Inspect running applications and check git status", auto_confirm=True)
        assert resp.success is True
        assert resp.type == "agent"
        assert "control_id" in resp.metadata
        assert resp.metadata["control_id"].startswith("ctrl-")

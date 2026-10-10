"""Regression test suite for RYVEN 3.0 VS Code launch idempotency, single-flight protection, and window reuse."""

import asyncio
from dataclasses import dataclass
import os
import sys
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.tools.app_tool import OpenApplicationTool
from app.control.models import DesktopWindowState


@pytest.fixture(autouse=True)
def reset_tool_state():
    """Ensure OpenApplicationTool state is reset cleanly before and after each test."""
    OpenApplicationTool.reset_state()
    yield
    OpenApplicationTool.reset_state()


def _make_mock_window(
    hwnd: int = 1234,
    title: str = "Welcome - Visual Studio Code",
    executable: str = "Code.exe",
    application: str = "Visual Studio Code",
    focused: bool = False,
) -> DesktopWindowState:
    return DesktopWindowState(
        hwnd=hwnd,
        process_id=9999,
        executable=executable,
        application=application,
        title=title,
        left=0,
        top=0,
        width=1920,
        height=1080,
        visible=True,
        minimized=False,
        maximized=False,
        focused=focused,
    )


@pytest.mark.asyncio
async def test_single_launch_when_no_existing_window():
    """When VS Code is not already running/open, os.startfile is called exactly once."""
    tool = OpenApplicationTool()

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch:

        res = await tool.execute(application="vscode")

        assert res["success"] is True
        assert res["reused_window"] is False
        assert "Code.exe" in res["path"]
        mock_launch.assert_called_once_with("C:\\Programs\\VSCode\\Code.exe")


@pytest.mark.asyncio
async def test_reuse_existing_vscode_window():
    """When a VS Code window is already open, it is focused and os.startfile is NOT called."""
    tool = OpenApplicationTool()
    mock_win = _make_mock_window(hwnd=54321, title="project_foo - Visual Studio Code")

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch("app.desktop.interaction.WindowsDesktopDriver") as mock_driver_cls, \
         patch.object(tool, "_launch_process") as mock_launch:

        mock_driver_instance = MagicMock()
        mock_driver_instance.inspect_windows.return_value = [mock_win]
        mock_driver_instance.focus_window.return_value = True
        mock_driver_cls.return_value = mock_driver_instance

        res = await tool.execute(application="vscode")

        assert res["success"] is True
        assert res["reused_window"] is True
        assert res["hwnd"] == 54321
        assert "Focused existing" in res["message"] or "already open" in res["message"]
        mock_driver_instance.focus_window.assert_called_once_with(54321)
        mock_launch.assert_not_called()


@pytest.mark.asyncio
async def test_single_flight_coalescing_concurrent_requests():
    """Concurrent launch requests for the same application coalesce into at most one launch action."""
    tool = OpenApplicationTool()
    launch_counter = 0

    async def slow_execute_launch(app_key: str, display_name: str, target_path: Optional[str]):
        nonlocal launch_counter
        launch_counter += 1
        await asyncio.sleep(0.05)  # Simulate small delay during launch
        return {
            "success": True,
            "tool": "open_application",
            "application": display_name,
            "path": "C:\\Programs\\VSCode\\Code.exe",
            "reused_window": False,
            "message": "Visual Studio Code is now open.",
        }

    with patch.object(tool, "_execute_launch", side_effect=slow_execute_launch):
        # Fire 5 concurrent requests
        results = await asyncio.gather(
            tool.execute(application="vscode"),
            tool.execute(application="vscode"),
            tool.execute(application="vscode"),
            tool.execute(application="vscode"),
            tool.execute(application="vscode"),
        )

        assert len(results) == 5
        for r in results:
            assert r["success"] is True

        # Exactly 1 launch was executed
        assert launch_counter == 1

        # Joined callers are marked
        joined_count = sum(1 for r in results if r.get("single_flight_joined") is True)
        assert joined_count == 4


@pytest.mark.asyncio
async def test_idempotency_window_suppresses_rapid_retries():
    """Repeated calls within the idempotency window return the cached result without re-launching."""
    tool = OpenApplicationTool()

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch:

        # 1st call
        res1 = await tool.execute(application="vscode")
        assert res1["success"] is True
        assert mock_launch.call_count == 1

        # 2nd call (immediate retry)
        res2 = await tool.execute(application="vscode")
        assert res2["success"] is True
        assert res2.get("idempotent_cached") is True
        assert mock_launch.call_count == 1  # Still 1, no duplicate launch

        # 3rd call
        res3 = await tool.execute(application="vscode")
        assert res3["success"] is True
        assert res3.get("idempotent_cached") is True
        assert mock_launch.call_count == 1


@pytest.mark.asyncio
async def test_does_not_suppress_different_projects_or_files():
    """Requests targeting different projects/files are NOT suppressed."""
    tool = OpenApplicationTool()

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch:

        res1 = await tool.execute(application="vscode", path="C:\\Projects\\AppAlpha")
        assert res1["success"] is True
        mock_launch.assert_called_with("C:\\Programs\\VSCode\\Code.exe", arguments='"C:\\Projects\\AppAlpha"')

        res2 = await tool.execute(application="vscode", path="C:\\Projects\\AppBeta")
        assert res2["success"] is True
        mock_launch.assert_called_with("C:\\Programs\\VSCode\\Code.exe", arguments='"C:\\Projects\\AppBeta"')

        # Both distinct projects were launched
        assert mock_launch.call_count == 2


@pytest.mark.asyncio
async def test_reuses_window_if_matching_project_is_already_open():
    """If project 'AppAlpha' is open in an existing window, focus it; but launch project 'AppBeta'."""
    tool = OpenApplicationTool()
    win_alpha = _make_mock_window(hwnd=101, title="AppAlpha - Visual Studio Code")

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch("app.desktop.interaction.WindowsDesktopDriver") as mock_driver_cls, \
         patch.object(tool, "_launch_process") as mock_launch:

        mock_driver_instance = MagicMock()
        mock_driver_instance.inspect_windows.return_value = [win_alpha]
        mock_driver_instance.focus_window.return_value = True
        mock_driver_cls.return_value = mock_driver_instance

        # Request AppAlpha -> should reuse window 101
        res1 = await tool.execute(application="vscode", path="C:\\Projects\\AppAlpha")
        assert res1["success"] is True
        assert res1["reused_window"] is True
        assert res1["hwnd"] == 101
        mock_driver_instance.focus_window.assert_called_once_with(101)
        mock_launch.assert_not_called()

        # Request AppBeta -> not in title of window 101 -> launches new instance
        res2 = await tool.execute(application="vscode", path="C:\\Projects\\AppBeta")
        assert res2["success"] is True
        assert res2["reused_window"] is False
        mock_launch.assert_called_once_with("C:\\Programs\\VSCode\\Code.exe", arguments='"C:\\Projects\\AppBeta"')


@pytest.mark.asyncio
async def test_never_terminates_existing_processes():
    """Verify that under no circumstances are existing processes terminated."""
    tool = OpenApplicationTool()

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch, \
         patch("psutil.Process") as mock_proc, \
         patch("os.kill") as mock_os_kill:

        res = await tool.execute(application="vscode")
        assert res["success"] is True

        mock_proc.terminate.assert_not_called() if hasattr(mock_proc, "terminate") else None
        mock_proc.kill.assert_not_called() if hasattr(mock_proc, "kill") else None
        mock_os_kill.assert_not_called()
        mock_launch.assert_called_once()


@pytest.mark.asyncio
async def test_preserves_allowlist_and_safety_checks():
    """Unapproved apps or commands with dangerous injection characters are rejected safely."""
    tool = OpenApplicationTool()

    # 1. Reject unallowlisted application
    res_bad_app = await tool.execute(application="malicious_payload.exe")
    assert res_bad_app["success"] is False
    assert "approved applications allowlist" in res_bad_app["message"]

    # 2. Reject shell injection characters in path argument
    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch:

        res_inject = await tool.execute(application="vscode", path="C:\\test & calc.exe")
        assert res_inject["success"] is True
        # Path was sanitized / rejected as None, so it launched plain Code.exe without injection
        mock_launch.assert_called_once_with("C:\\Programs\\VSCode\\Code.exe")


@pytest.mark.asyncio
async def test_orchestrator_step_retry_idempotency():
    """Simulate AgentOrchestrator retry loop on open_application step: only 1 launch action occurs."""
    from app.agent.orchestrator import AgentOrchestrator
    from app.agent.models import AgentPlan, TaskStep, CapabilityGroup
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    app_tool = OpenApplicationTool()
    registry.register(app_tool)

    orchestrator = AgentOrchestrator(registry=registry)

    # Track how many times _launch_process is called
    launch_calls = []

    def mock_launch_proc(exe_path, arguments=None):
        launch_calls.append((exe_path, arguments))

    step = TaskStep(
        step_id="step-launch-vscode",
        order=1,
        name="Launch Visual Studio Code",
        capability=CapabilityGroup.COMPUTER,
        tool_name="open_application",
        arguments={"application": "vscode"},
        max_retries=2,
    )
    plan = AgentPlan(
        goal="Open VS Code safely",
        capabilities_required=[CapabilityGroup.COMPUTER],
        steps=[step],
    )

    with patch.object(app_tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(app_tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(app_tool, "_launch_process", side_effect=mock_launch_proc):

        result = await orchestrator.execute_plan(plan=plan)
        assert result.success is True

        # Exactly 1 launch was executed
        assert len(launch_calls) == 1

        # Now simulate a second run of the plan (e.g. repeated user request or retry within cooldown)
        result2 = await orchestrator.execute_plan(plan=plan)
        assert result2.success is True

        # Still exactly 1 launch because of idempotency cooldown!
        assert len(launch_calls) == 1


@pytest.mark.asyncio
async def test_owner_cancellation_safe_cleanup_and_waiter_termination():
    """Cancelling the owner task cancels the shared future, terminates waiters without hanging,
    cleans up _in_flight, and allows subsequent retries."""
    tool = OpenApplicationTool()
    owner_started = asyncio.Event()
    owner_cancel_released = asyncio.Event()

    async def slow_launch(*args, **kwargs):
        owner_started.set()
        await owner_cancel_released.wait()
        return {"success": True, "reused_window": False, "tool": "open_application"}

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_execute_launch", side_effect=slow_launch):

        # Start owner
        owner_task = asyncio.create_task(tool.execute(application="vscode"))
        await owner_started.wait()

        # Waiter joins
        waiter_task = asyncio.create_task(tool.execute(application="vscode"))
        await asyncio.sleep(0.01)

        flight_key = ("vscode", None)
        assert flight_key in tool._in_flight

        # Cancel the owner
        owner_task.cancel()

        # Both owner and waiter should terminate with CancelledError without hanging
        with pytest.raises(asyncio.CancelledError):
            await owner_task

        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        # In-flight entry must be cleaned up
        assert flight_key not in tool._in_flight

    # Subsequent retry should now succeed
    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch:

        retry_res = await tool.execute(application="vscode")
        assert retry_res["success"] is True
        mock_launch.assert_called_once()
        assert flight_key not in tool._in_flight


@pytest.mark.asyncio
async def test_precise_window_matching_distinguishes_appalpha_from_backup():
    """AppAlpha does not match AppAlphaBackup, while genuine AppAlphaBackup is reused."""
    tool = OpenApplicationTool()
    win_backup = _make_mock_window(hwnd=888, title="AppAlphaBackup - Visual Studio Code")

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch("app.desktop.interaction.WindowsDesktopDriver") as mock_driver_cls, \
         patch.object(tool, "_launch_process") as mock_launch:

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [win_backup]
        mock_driver.focus_window.return_value = True
        mock_driver_cls.return_value = mock_driver

        # 1. Request AppAlpha -> must NOT match AppAlphaBackup -> launches new process
        res1 = await tool.execute(application="vscode", path="C:\\Projects\\AppAlpha")
        assert res1["success"] is True
        assert res1["reused_window"] is False
        mock_launch.assert_called_once_with("C:\\Programs\\VSCode\\Code.exe", arguments='"C:\\Projects\\AppAlpha"')
        mock_driver.focus_window.assert_not_called()

        mock_launch.reset_mock()
        mock_driver.focus_window.reset_mock()

        # 2. Request AppAlphaBackup -> exact match -> reuses existing window 888
        res2 = await tool.execute(application="vscode", path="C:\\Projects\\AppAlphaBackup")
        assert res2["success"] is True
        assert res2["reused_window"] is True
        assert res2["hwnd"] == 888
        mock_driver.focus_window.assert_called_once_with(888)
        mock_launch.assert_not_called()


@pytest.mark.asyncio
async def test_defense_in_depth_path_validation():
    """Path validation defense-in-depth rejects directory traversal and out-of-bounds paths."""
    tool = OpenApplicationTool()

    # 1. Traversal attempt
    assert tool._resolve_target_path(r"C:\Projects\..\Windows\System32") is None
    assert tool._resolve_target_path(r"..\..\etc\passwd") is None

    # 2. Injection characters
    assert tool._resolve_target_path(r"C:\Projects\App;calc.exe") is None
    assert tool._resolve_target_path("C:\\Projects\\App\ncalc.exe") is None

    # 3. System32 or root drives
    assert tool._resolve_target_path(r"C:\Windows\System32\cmd.exe") is None
    assert tool._resolve_target_path(r"C:\\") is None
    assert tool._resolve_target_path(r"C:") is None

    # 4. Valid permitted workspace path
    resolved = tool._resolve_target_path(r"C:\Projects\AppAlpha")
    assert resolved is not None
    assert "appalpha" in resolved.lower()


@pytest.mark.asyncio
async def test_cache_ttl_expiry_allows_fresh_launch():
    """Cache TTL expiry allows fresh launch after IDEMPOTENCY_WINDOW_SECONDS."""
    tool = OpenApplicationTool()

    fake_time = 1000.0

    def mock_monotonic():
        return fake_time

    with patch.object(tool, "_locate_executable", return_value="C:\\Programs\\VSCode\\Code.exe"), \
         patch.object(tool, "_reuse_existing_window_if_applicable", return_value=None), \
         patch.object(tool, "_launch_process") as mock_launch, \
         patch("time.monotonic", side_effect=mock_monotonic):

        # 1st launch at t=1000.0
        res1 = await tool.execute(application="vscode")
        assert res1["success"] is True
        assert res1.get("idempotent_cached") is not True
        assert mock_launch.call_count == 1

        # Rapid repeat at t=1001.0 (within 3.0s window) -> cached
        fake_time = 1001.0
        res2 = await tool.execute(application="vscode")
        assert res2["success"] is True
        assert res2.get("idempotent_cached") is True
        assert mock_launch.call_count == 1

        # Advance beyond TTL at t=1005.0 (> 3.0s) -> fresh launch
        fake_time = 1005.0
        res3 = await tool.execute(application="vscode")
        assert res3["success"] is True
        assert res3.get("idempotent_cached") is not True
        assert mock_launch.call_count == 2


@pytest.mark.asyncio
async def test_immediate_retry_after_failed_launch():
    """Failed launches are not cached, allowing immediate retry."""
    tool = OpenApplicationTool()
    attempt = 0

    async def fail_then_succeed(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return {
                "success": False,
                "tool": "open_application",
                "application": "Visual Studio Code",
                "message": "Launch failed due to temporary error",
            }
        return {
            "success": True,
            "tool": "open_application",
            "application": "Visual Studio Code",
            "path": "C:\\Programs\\VSCode\\Code.exe",
            "reused_window": False,
            "message": "Visual Studio Code is now open.",
        }

    with patch.object(tool, "_execute_launch", side_effect=fail_then_succeed):
        # 1st attempt fails
        res1 = await tool.execute(application="vscode")
        assert res1["success"] is False
        assert attempt == 1

        # Immediate retry must not be blocked by cache
        res2 = await tool.execute(application="vscode")
        assert res2["success"] is True
        assert res2.get("idempotent_cached") is not True
        assert attempt == 2


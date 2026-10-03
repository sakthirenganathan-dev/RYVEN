"""
RYVEN 3.0 — Milestone 17.1 Phase 1 Test Suite
Desktop Domain Models & Failure Taxonomy Verification
"""

import pytest
from pydantic import ValidationError

from app.control.models import (
    FailureClass,
    DesktopActionType,
    DesktopTargetSource,
    DesktopWindowState,
    DesktopUIElement,
    DesktopActionRequest,
    ALLOWED_DESKTOP_KEYS,
)


class TestDesktopDomainModelsPhase1:
    """Phase 1 verification: strict Pydantic desktop models and failure taxonomy."""

    def test_1_desktop_window_state_valid(self):
        """1. DesktopWindowState valid construction."""
        win = DesktopWindowState(
            hwnd=123456,
            process_id=4567,
            executable="Code.exe",
            application="Visual Studio Code",
            title="main.py - RYVEN",
            left=100,
            top=100,
            width=1200,
            height=800,
            visible=True,
            focused=True,
        )
        assert win.hwnd == 123456
        assert win.process_id == 4567
        assert win.application == "Visual Studio Code"
        assert win.width == 1200
        assert win.focused is True

    def test_2_desktop_window_state_invalid_bounds(self):
        """2. DesktopWindowState invalid bounds rejection."""
        with pytest.raises(ValidationError):
            DesktopWindowState(
                hwnd=123,
                executable="calc.exe",
                application="Calculator",
                title="Calculator",
                left=0,
                top=0,
                width=-50,  # Negative width disallowed
                height=400,
            )

    def test_3_desktop_window_state_invalid_process_id(self):
        """3. DesktopWindowState invalid process ID rejection."""
        with pytest.raises(ValidationError):
            DesktopWindowState(
                hwnd=123,
                process_id=0,  # PID must be > 0
                executable="notepad.exe",
                application="Notepad",
                title="Untitled - Notepad",
                left=0,
                top=0,
                width=800,
                height=600,
            )

    def test_4_desktop_ui_element_valid(self):
        """4. DesktopUIElement valid construction."""
        elem = DesktopUIElement(
            role="button",
            text="Save File",
            bounds={"left": 150, "top": 200, "width": 80, "height": 30},
            confidence=0.98,
            source=DesktopTargetSource.OCR,
            actionable=True,
        )
        assert elem.role == "button"
        assert elem.text == "Save File"
        assert elem.confidence == 0.98
        assert elem.source == DesktopTargetSource.OCR

    def test_5_confidence_lower_bound(self):
        """5. Confidence score lower bound (0.0 allowed)."""
        elem = DesktopUIElement(
            role="icon",
            text="Menu",
            bounds={"left": 10, "top": 10, "width": 20, "height": 20},
            confidence=0.0,
            source=DesktopTargetSource.VISION,
        )
        assert elem.confidence == 0.0

    def test_6_confidence_upper_bound(self):
        """6. Confidence score upper bound (1.0 allowed)."""
        elem = DesktopUIElement(
            role="button",
            text="Submit",
            bounds={"left": 10, "top": 10, "width": 20, "height": 20},
            confidence=1.0,
            source=DesktopTargetSource.SEMANTIC,
        )
        assert elem.confidence == 1.0

    def test_7_invalid_confidence_rejection(self):
        """7. Confidence score out of bounds rejection (<0.0 or >1.0)."""
        with pytest.raises(ValidationError):
            DesktopUIElement(
                bounds={"left": 0, "top": 0, "width": 10, "height": 10},
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            DesktopUIElement(
                bounds={"left": 0, "top": 0, "width": 10, "height": 10},
                confidence=-0.1,
            )

    def test_8_desktop_action_request_valid_actions(self):
        """8. DesktopActionRequest valid actions across all supported primitives."""
        for action_type in DesktopActionType:
            req = DesktopActionRequest(
                action=action_type,
                application="Visual Studio Code",
                hwnd=1001,
            )
            assert req.action == action_type

    def test_9_invalid_action_rejection(self):
        """9. Rejection of invalid / arbitrary action strings."""
        with pytest.raises(ValidationError):
            DesktopActionRequest(action="EXECUTE_SHELL_COMMAND")

    def test_10_bounded_text_validation(self):
        """10. Text must be bounded in length and not exceed 1000 characters."""
        req = DesktopActionRequest(
            action=DesktopActionType.TYPE,
            text="Hello world",
        )
        assert req.text == "Hello world"

        with pytest.raises(ValidationError):
            DesktopActionRequest(
                action=DesktopActionType.TYPE,
                text="A" * 1001,  # Exceeds max_length 1000
            )

    def test_11_bounded_coordinate_validation(self):
        """11. Coordinates must be bounded integers."""
        req = DesktopActionRequest(
            action=DesktopActionType.CLICK,
            x=500,
            y=300,
        )
        assert req.x == 500
        assert req.y == 300

        with pytest.raises(ValidationError):
            DesktopActionRequest(
                action=DesktopActionType.CLICK,
                x=-5,  # Coordinates cannot be negative
                y=100,
            )

        with pytest.raises(ValidationError):
            DesktopActionRequest(
                action=DesktopActionType.CLICK,
                x=60000,  # Exceeds bounded max 50000
                y=100,
            )

    def test_12_invalid_key_validation(self):
        """12. Invalid or disallowed keys are rejected."""
        valid_req = DesktopActionRequest(
            action=DesktopActionType.KEY,
            key="enter",
        )
        assert valid_req.key == "enter"

        with pytest.raises(ValidationError):
            DesktopActionRequest(
                action=DesktopActionType.KEY,
                key="INVALID_SYSTEM_KEY_SEQUENCE_123",
            )

    def test_13_failure_class_existence(self):
        """13. Seven M17.1 failure classes exist as strict enum members."""
        expected_classes = [
            "WINDOW_NOT_FOUND",
            "APPLICATION_NOT_RUNNING",
            "TARGET_NOT_FOUND",
            "UI_CHANGED",
            "FOCUS_FAILED",
            "ACTION_TIMEOUT",
            "VISION_UNCERTAIN",
        ]
        for name in expected_classes:
            assert hasattr(FailureClass, name)
            assert getattr(FailureClass, name).value == name

    def test_14_failure_class_backward_compatibility(self):
        """14. Existing M17.0 failure classes remain intact."""
        assert FailureClass.TRANSIENT.value == "TRANSIENT"
        assert FailureClass.SECURITY.value == "SECURITY"
        assert FailureClass.PERMANENT.value == "PERMANENT"
        assert FailureClass.ENVIRONMENT.value == "ENVIRONMENT"
        assert FailureClass.USER_ACTION_REQUIRED.value == "USER_ACTION_REQUIRED"

    def test_15_model_serialization(self):
        """15. Models serialize cleanly to JSON and dictionary format."""
        state = DesktopWindowState(
            hwnd=5555,
            process_id=1234,
            executable="chrome.exe",
            application="Google Chrome",
            title="Google - Google Chrome",
            left=0,
            top=0,
            width=1920,
            height=1080,
            visible=True,
            focused=True,
        )
        dump = state.model_dump()
        assert dump["hwnd"] == 5555
        assert dump["executable"] == "chrome.exe"
        json_str = state.model_dump_json()
        assert "Google Chrome" in json_str

    def test_16_secret_credential_field_absence(self):
        """16. Verification that models expose zero credential / token / shell fields."""
        req_fields = set(DesktopActionRequest.model_fields.keys())
        win_fields = set(DesktopWindowState.model_fields.keys())
        ui_fields = set(DesktopUIElement.model_fields.keys())

        forbidden = {"password", "token", "cookie", "secret", "private_key", "shell", "cmd", "exec"}
        for f in req_fields.union(win_fields).union(ui_fields):
            assert f.lower() not in forbidden

        # Test credential payload rejection in DesktopActionRequest text
        with pytest.raises(ValidationError):
            DesktopActionRequest(
                action=DesktopActionType.TYPE,
                text="bearer eyJhbGciOi...",
            )

    @pytest.mark.asyncio
    async def test_17_existing_m17_0_control_integration(self):
        """17. Existing M17.0 control integration test remains passing."""
        from unittest.mock import MagicMock
        from app.core.assistant import Assistant

        assistant = Assistant()
        mock_decision = MagicMock()
        mock_decision.intent = "agent"
        mock_decision.workflow_name = None
        mock_decision.tool_name = None
        assistant.router.route = MagicMock(return_value=mock_decision)

        resp = await assistant.process("Inspect running applications and check git status", auto_confirm=True)
        assert resp.success is True
        assert resp.type == "agent"
        assert "control_id" in resp.metadata
        assert resp.metadata["control_id"].startswith("ctrl-")


class TestWindowsDesktopDriverPhase2:
    """Phase 2 verification: WindowsDesktopDriver native interaction & safety boundaries."""

    def test_2_1_executable_normalization(self):
        """1. Normalizes executables to lowercase basenames."""
        from app.desktop.interaction import desktop_driver
        assert desktop_driver.normalize_executable("Code.EXE") == "code.exe"
        assert desktop_driver.normalize_executable("C:\\Windows\\System32\\calc.exe") == "calc.exe"
        assert desktop_driver.normalize_executable("wt") == "wt.exe"
        assert desktop_driver.normalize_executable("chrome") == "chrome.exe"

    def test_2_2_allowlist_coverage(self):
        """2. Strict application allowlist contains exactly the approved tools."""
        from app.desktop.interaction import ALLOWED_EXECUTABLE_TO_APP
        expected_exes = {"code.exe", "chrome.exe", "wt.exe", "windowsterminal.exe", "notepad.exe", "calc.exe", "calculatorapp.exe", "calculator.exe", "explorer.exe"}
        for exe in expected_exes:
            assert exe in ALLOWED_EXECUTABLE_TO_APP

    def test_2_3_unknown_executable_rejection(self):
        """3. Rejects non-allowlisted executables with PERMISSION_DENIED."""
        from unittest.mock import patch
        from app.desktop.interaction import desktop_driver

        with patch.object(desktop_driver, "_user32") as mock_user32, \
             patch.object(desktop_driver, "_resolve_executable_for_pid", return_value="powershell.exe"):
            mock_user32.IsWindow.return_value = True
            def fake_pid(h, pref):
                pref._obj.value = 1234
                return 1
            mock_user32.GetWindowThreadProcessId.side_effect = fake_pid

            with pytest.raises(PermissionError) as exc_info:
                desktop_driver.validate_window_application(9999)
            assert "PERMISSION_DENIED" in str(exc_info.value)

    def test_2_4_invalid_hwnd_handling(self):
        """4. Invalid or non-existent HWND raises WINDOW_NOT_FOUND."""
        from app.desktop.interaction import desktop_driver
        with pytest.raises(ValueError) as exc_info:
            desktop_driver.validate_window_application(0)
        assert "WINDOW_NOT_FOUND" in str(exc_info.value)

        with pytest.raises(ValueError) as exc_info:
            desktop_driver.validate_window_application(-1)
        assert "WINDOW_NOT_FOUND" in str(exc_info.value)

    def test_2_5_coordinate_bounds_validation(self):
        """5. Coordinates must be bounded non-negative integers <= 50000."""
        from app.desktop.interaction import desktop_driver
        # Valid coordinates
        x, y = desktop_driver._validate_and_resolve_coords(500, 400)
        assert x == 500 and y == 400

        # Negative X
        with pytest.raises(ValueError):
            desktop_driver._validate_and_resolve_coords(-1, 100)

        # Out-of-bounds Y
        with pytest.raises(ValueError):
            desktop_driver._validate_and_resolve_coords(100, 60000)

    def test_2_6_coordinate_inside_window_validation(self):
        """6. Rejects coordinates outside target window bounds with TARGET_NOT_FOUND."""
        from unittest.mock import patch
        from app.desktop.interaction import desktop_driver, RECT

        with patch.object(desktop_driver, "validate_window_application", return_value=(True, "Google Chrome", "chrome.exe", 100)), \
             patch.object(desktop_driver, "_user32") as mock_user32:
            def fake_rect(hwnd, pref):
                pref._obj.left = 100
                pref._obj.top = 100
                pref._obj.right = 500
                pref._obj.bottom = 500
                return 1
            mock_user32.GetWindowRect.side_effect = fake_rect

            # Inside window bounds (300, 300) -> valid
            cx, cy = desktop_driver._validate_and_resolve_coords(300, 300, hwnd=1234)
            assert cx == 300 and cy == 300

            # Outside window bounds (50, 50) -> raises TARGET_NOT_FOUND
            with pytest.raises(ValueError) as exc_info:
                desktop_driver._validate_and_resolve_coords(50, 50, hwnd=1234)
            assert "TARGET_NOT_FOUND" in str(exc_info.value)

    def test_2_7_screenshot_bounded_dimensions(self):
        """7. Captured desktop screenshots respect bounded dimensions."""
        from app.desktop.interaction import desktop_driver
        img = desktop_driver.capture_desktop(max_width=400, max_height=300)
        assert img is not None
        assert img.width <= 400
        assert img.height <= 300

    def test_2_8_screenshot_memory_only(self):
        """8. Verification that screen capture is purely in-memory and writes no disk files."""
        import tempfile
        import os
        from app.desktop.interaction import desktop_driver

        temp_dir = tempfile.gettempdir()
        initial_files = set(os.listdir(temp_dir))

        img = desktop_driver.capture_desktop(max_width=200, max_height=200)
        assert img is not None
        assert hasattr(img, "tobytes")

        final_files = set(os.listdir(temp_dir))
        # Ensure no new image files written
        new_pngs = [f for f in final_files - initial_files if f.endswith(".png")]
        assert len(new_pngs) == 0

    def test_2_9_key_mapping(self):
        """9. Standard keys map to valid virtual keys."""
        from app.desktop.interaction import desktop_driver, VK_MAP
        assert VK_MAP["enter"] == 0x0D
        assert VK_MAP["esc"] == 0x1B
        assert VK_MAP["tab"] == 0x09
        assert VK_MAP["f5"] == 0x74
        res = desktop_driver.press_key("tab")
        assert res["success"] is True
        assert res["key"] == "tab"

    def test_2_10_invalid_key_rejection(self):
        """10. Rejects invalid keyboard keys."""
        from app.desktop.interaction import desktop_driver
        with pytest.raises(ValueError):
            desktop_driver.press_key("INVALID_KEY_CODE_ABC")

    def test_2_11_hotkey_bounds_and_rejection(self):
        """11. Hotkey length must be between 1 and 4 keys with valid key names."""
        from app.desktop.interaction import desktop_driver
        res = desktop_driver.hotkey(["ctrl", "c"])
        assert res["success"] is True
        assert res["keys"] == ["ctrl", "c"]

        # Rejects empty hotkey
        with pytest.raises(ValueError):
            desktop_driver.hotkey([])

        # Rejects > 4 keys
        with pytest.raises(ValueError):
            desktop_driver.hotkey(["ctrl", "alt", "shift", "win", "x"])

        # Rejects invalid key
        with pytest.raises(ValueError):
            desktop_driver.hotkey(["ctrl", "NOT_A_KEY"])

    def test_2_12_typing_text_length_limit(self):
        """12. Rejects typing text longer than 1000 characters."""
        from app.desktop.interaction import desktop_driver
        with pytest.raises(ValueError):
            desktop_driver.type_text("A" * 1001)

    def test_2_13_typing_credential_rejection(self):
        """13. Rejects typing text containing credential secret patterns."""
        from app.desktop.interaction import desktop_driver
        with pytest.raises(PermissionError) as exc_info:
            desktop_driver.type_text("password=MySecretPassword123")
        assert "sensitive pattern" in str(exc_info.value)

        with pytest.raises(PermissionError):
            desktop_driver.type_text("Authorization: Bearer secret_token")

    def test_2_14_shell_subprocess_absence(self):
        """14. Verifies interaction driver does not import or use subprocess/cmd/powershell."""
        import ast
        import inspect
        from app.desktop import interaction

        src = inspect.getsource(interaction)
        tree = ast.parse(src)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imported_modules.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module.split(".")[0])

        forbidden = {"subprocess", "shutil", "wscript"}
        assert not imported_modules.intersection(forbidden)
        assert "subprocess." not in src
        assert "os.system(" not in src
        assert "os.popen(" not in src

    def test_2_15_live_window_enumeration(self):
        """15. Live enumeration discovers running allowlisted windows safely."""
        from app.desktop.interaction import desktop_driver
        windows = desktop_driver.inspect_windows()
        assert isinstance(windows, list)
        for w in windows:
            assert w.application in {"Visual Studio Code", "Google Chrome", "Windows Terminal", "Notepad", "Calculator", "File Explorer"}
            assert w.hwnd > 0
            assert w.width >= 0
            assert w.height >= 0


class TestDesktopObserverPhase3:
    """Phase 3 verification: ObserverEngine desktop observation & event telemetry."""

    @pytest.mark.asyncio
    async def test_3_1_observer_engine_exposes_observe_desktop(self):
        """1. ObserverEngine exposes observe_desktop() coroutine."""
        from app.control.observer import observer_engine
        assert hasattr(observer_engine, "observe_desktop")
        import inspect
        assert inspect.iscoroutinefunction(observer_engine.observe_desktop)

    @pytest.mark.asyncio
    async def test_3_2_driver_reuse_verified(self):
        """2. ObserverEngine delegates window inspection directly to desktop_driver."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [
            DesktopWindowState(
                hwnd=101,
                process_id=202,
                executable="code.exe",
                application="Visual Studio Code",
                title="Editor - RYVEN",
                left=0,
                top=0,
                width=1920,
                height=1080,
                focused=True,
            )
        ]

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop()

        assert mock_driver.inspect_windows.called
        assert res.observation_success is True
        assert res.active_application == "Visual Studio Code"
        assert res.active_window is not None
        assert res.active_window.hwnd == 101
        assert len(res.windows) == 1

    def test_3_3_no_direct_win32_in_observer(self):
        """3. Verifies observer.py does NOT directly import ctypes.windll.user32, win32gui, or subprocess."""
        import ast
        import inspect
        from app.control import observer

        src = inspect.getsource(observer)
        tree = ast.parse(src)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imported_modules.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module.split(".")[0])

        forbidden = {"win32gui", "win32process", "win32api", "subprocess"}
        assert not imported_modules.intersection(forbidden)
        assert "windll.user32" not in src
        assert "ctypes." not in src

    @pytest.mark.asyncio
    async def test_3_4_active_allowlisted_window_detection(self):
        """4. Active allowlisted window with focused=True is correctly populated."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [
            DesktopWindowState(
                hwnd=10,
                executable="notepad.exe",
                application="Notepad",
                title="Notes.txt",
                left=0,
                top=0,
                width=500,
                height=400,
                focused=False,
            ),
            DesktopWindowState(
                hwnd=20,
                executable="chrome.exe",
                application="Google Chrome",
                title="Google Search",
                left=100,
                top=100,
                width=1200,
                height=800,
                focused=True,
            ),
        ]

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop()
        assert res.active_application == "Google Chrome"
        assert res.active_window.hwnd == 20
        assert len(res.windows) == 2

    @pytest.mark.asyncio
    async def test_3_5_unapproved_foreground_app_safely_handled(self):
        """5. Unapproved foreground application results in active_application=None, active_window=None."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        mock_driver = MagicMock()
        # All allowlisted windows are unfocused because an unapproved window has foreground
        mock_driver.inspect_windows.return_value = [
            DesktopWindowState(
                hwnd=10,
                executable="calc.exe",
                application="Calculator",
                title="Calculator",
                left=0,
                top=0,
                width=300,
                height=400,
                focused=False,
            ),
        ]

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop()
        assert res.active_application is None
        assert res.active_window is None
        assert len(res.windows) == 1

    @pytest.mark.asyncio
    async def test_3_6_target_app_filtering(self):
        """6. target_app filters returned windows while preserving safe observation."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [
            DesktopWindowState(
                hwnd=1,
                executable="code.exe",
                application="Visual Studio Code",
                title="App.tsx",
                left=0,
                top=0,
                width=800,
                height=600,
            ),
            DesktopWindowState(
                hwnd=2,
                executable="chrome.exe",
                application="Google Chrome",
                title="Google",
                left=0,
                top=0,
                width=800,
                height=600,
            ),
        ]

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop(target_app="vscode")
        assert len(res.windows) == 1
        assert res.windows[0].application == "Visual Studio Code"

    @pytest.mark.asyncio
    async def test_3_7_window_state_fields_preserved(self):
        """7. Window state fields (geometry, flags, title) are strictly preserved."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        win = DesktopWindowState(
            hwnd=777,
            process_id=888,
            executable="wt.exe",
            application="Windows Terminal",
            title="PowerShell",
            left=15,
            top=25,
            width=1000,
            height=700,
            visible=True,
            minimized=False,
            maximized=True,
            focused=True,
        )
        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [win]

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop()
        w = res.windows[0]
        assert w.hwnd == 777
        assert w.process_id == 888
        assert w.left == 15
        assert w.maximized is True
        assert w.focused is True

    @pytest.mark.asyncio
    async def test_3_8_partial_failure_isolation(self):
        """8. If driver raises an exception, observe_desktop returns structured failure without crashing."""
        from unittest.mock import MagicMock
        from app.control.observer import ObserverEngine

        mock_driver = MagicMock()
        mock_driver.inspect_windows.side_effect = RuntimeError("Low level driver timeout")

        obs_engine = ObserverEngine(desktop_driver=mock_driver)
        res = await obs_engine.observe_desktop()

        assert res.observation_success is False
        assert "driver timeout" in (res.error or "")
        assert res.active_application is None
        assert res.windows == []

    @pytest.mark.asyncio
    async def test_3_9_telemetry_events_published(self):
        """9. DESKTOP_OBSERVATION_STARTED and COMPLETED events are published to ActionEventBus."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.control.observer import ObserverEngine
        from app.actions.models import ActionType

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = []

        with patch("app.control.observer.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            obs_engine = ObserverEngine(desktop_driver=mock_driver)
            await obs_engine.observe_desktop(task_id="task-test-telemetry")

            types_published = [call.args[0].action_type for call in mock_pub.call_args_list]
            assert ActionType.DESKTOP_OBSERVATION_STARTED in types_published
            assert ActionType.DESKTOP_OBSERVATION_COMPLETED in types_published

    @pytest.mark.asyncio
    async def test_3_10_telemetry_contains_zero_secrets_or_screenshots(self):
        """10. Event metadata contains zero raw credentials, tokens, or base64 screenshots."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.control.observer import ObserverEngine
        from app.control.models import DesktopWindowState

        mock_driver = MagicMock()
        mock_driver.inspect_windows.return_value = [
            DesktopWindowState(
                hwnd=1,
                executable="code.exe",
                application="Visual Studio Code",
                title="SuperSecretProject - Code",
                left=0,
                top=0,
                width=800,
                height=600,
            )
        ]

        with patch("app.control.observer.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            obs_engine = ObserverEngine(desktop_driver=mock_driver)
            await obs_engine.observe_desktop()

            forbidden_keys = {"password", "secret", "token", "cookie", "screenshot", "base64", "private_key"}
            for call in mock_pub.call_args_list:
                event = call.args[0]
                meta = event.safe_metadata or {}
                for k in meta.keys():
                    assert k.lower() not in forbidden_keys

    def test_3_11_no_background_threads_or_polling(self):
        """11. ObserverEngine creates zero background threads or persistent polling loops."""
        import threading
        from app.control.observer import ObserverEngine

        threads_before = threading.active_count()
        engine = ObserverEngine()
        threads_after = threading.active_count()
        assert threads_after == threads_before

    @pytest.mark.asyncio
    async def test_3_12_existing_observer_environment_intact(self):
        """12. Existing observe_environment() integrates desktop observations without regression."""
        from app.control.observer import observer_engine
        records = await observer_engine.observe_environment()
        assert isinstance(records, list)
        sources = {r.source for r in records}
        assert "desktop" in sources  # Existing application process observation

    @pytest.mark.asyncio
    async def test_3_13_live_desktop_observation(self):
        """13. Live desktop observation inspects running windows in a strictly read-only manner."""
        from app.control.observer import observer_engine
        res = await observer_engine.observe_desktop()
        assert res.observation_success is True
        assert isinstance(res.windows, list)
        for w in res.windows:
            assert w.application in {
                "Visual Studio Code",
                "Google Chrome",
                "Windows Terminal",
                "Notepad",
                "Calculator",
                "File Explorer",
            }
            assert w.hwnd > 0
            assert w.width >= 0
            assert w.height >= 0

    @pytest.mark.asyncio
    async def test_3_14_existing_m17_0_invariants_intact(self):
        """14. M17.0 unified control engine continues to function alongside desktop observer."""
        from app.control.engine import ryven_control_engine
        assert hasattr(ryven_control_engine, "execute_goal")
        assert hasattr(ryven_control_engine, "confirm_control")
        assert hasattr(ryven_control_engine, "cancel_control")
        assert ryven_control_engine.observer is not None


class TestDesktopTargetResolverPhase4:
    """Phase 4 verification: DesktopTargetResolver perception & semantic target resolution."""

    def test_4_1_resolver_construction(self):
        """1. DesktopTargetResolver instantiates cleanly with default or injected dependencies."""
        from app.desktop.resolver import DesktopTargetResolver, desktop_target_resolver
        from unittest.mock import MagicMock

        assert desktop_target_resolver is not None
        mock_driver = MagicMock()
        mock_perception = MagicMock()
        res = DesktopTargetResolver(driver=mock_driver, perception=mock_perception)
        assert res.driver is mock_driver
        assert res.perception_agent is mock_perception

    @pytest.mark.asyncio
    async def test_4_2_driver_reuse(self):
        """2. Resolver reuses WindowsDesktopDriver for window inspection and screenshot capture."""
        from unittest.mock import MagicMock, AsyncMock
        from PIL import Image
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState
        from app.ai.vision import PerceptionResult

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Visual Studio Code", "code.exe", 100)
        mock_driver.capture_window.return_value = Image.new("RGB", (800, 600))
        mock_perception = MagicMock()
        mock_perception.analyze_screenshot = AsyncMock(return_value=PerceptionResult(success=True))

        win = DesktopWindowState(
            hwnd=1234,
            process_id=100,
            executable="code.exe",
            application="Visual Studio Code",
            title="Editor",
            left=0,
            top=0,
            width=800,
            height=600,
        )
        resolver = DesktopTargetResolver(driver=mock_driver, perception=mock_perception)
        await resolver.resolve_target("File", window_state=win)

        assert mock_driver.capture_window.called
        assert mock_driver.capture_window.call_args[0][0] == 1234

    @pytest.mark.asyncio
    async def test_4_3_perception_agent_reuse(self):
        """3. Resolver passes transient image to PerceptionAgent.analyze_screenshot."""
        from unittest.mock import MagicMock, AsyncMock
        from PIL import Image
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState
        from app.ai.vision import PerceptionResult

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 200)
        mock_driver.capture_window.return_value = Image.new("RGB", (400, 300))
        mock_perception = MagicMock()
        mock_perception.analyze_screenshot = AsyncMock(return_value=PerceptionResult(success=True))

        win = DesktopWindowState(
            hwnd=5678,
            process_id=200,
            executable="notepad.exe",
            application="Notepad",
            title="Notes",
            left=0,
            top=0,
            width=400,
            height=300,
        )
        resolver = DesktopTargetResolver(driver=mock_driver, perception=mock_perception)
        await resolver.resolve_target("Save", window_state=win)

        assert mock_perception.analyze_screenshot.called
        call_args, call_kwargs = mock_perception.analyze_screenshot.call_args
        assert len(call_args) > 0  # image_b64 string passed
        assert isinstance(call_args[0], str)
        assert call_kwargs.get("is_visual_sensitive") is True

    @pytest.mark.asyncio
    async def test_4_4_screenshot_is_transient(self):
        """4. Verification that screen capture during resolution is purely in-memory."""
        import tempfile
        import os
        from unittest.mock import MagicMock, AsyncMock
        from PIL import Image
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState
        from app.ai.vision import PerceptionResult

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Calculator", "calc.exe", 300)
        mock_driver.capture_window.return_value = Image.new("RGB", (300, 400))
        mock_perception = MagicMock()
        mock_perception.analyze_screenshot = AsyncMock(return_value=PerceptionResult(success=True))

        temp_dir = tempfile.gettempdir()
        initial_pngs = {f for f in os.listdir(temp_dir) if f.endswith(".png")}

        win = DesktopWindowState(
            hwnd=9999,
            process_id=300,
            executable="calc.exe",
            application="Calculator",
            title="Calculator",
            left=0,
            top=0,
            width=300,
            height=400,
        )
        resolver = DesktopTargetResolver(driver=mock_driver, perception=mock_perception)
        await resolver.resolve_target("Equals", window_state=win)

        final_pngs = {f for f in os.listdir(temp_dir) if f.endswith(".png")}
        assert len(final_pngs - initial_pngs) == 0

    @pytest.mark.asyncio
    async def test_4_5_exact_text_match(self):
        """5. Exact text match resolves with high confidence (1.0)."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource

        elements = [
            DesktopUIElement(
                role="button",
                text="Save",
                bounds={"left": 10, "top": 10, "width": 80, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
            DesktopUIElement(
                role="button",
                text="Cancel",
                bounds={"left": 100, "top": 10, "width": 80, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
        ]
        resolver = DesktopTargetResolver()
        elem, conf, err, fail = resolver.match_elements("Save", elements)
        assert elem is not None
        assert elem.text == "Save"
        assert conf == 1.0
        assert err is None

    @pytest.mark.asyncio
    async def test_4_6_normalized_text_match(self):
        """6. Normalized text match handles case and whitespace cleanly."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource

        elements = [
            DesktopUIElement(
                role="button",
                text="  Open  Project  ",
                bounds={"left": 20, "top": 50, "width": 120, "height": 40},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
        ]
        resolver = DesktopTargetResolver()
        elem, conf, err, fail = resolver.match_elements("open project", elements)
        assert elem is not None
        assert conf >= 0.95

    @pytest.mark.asyncio
    async def test_4_7_role_plus_text_match(self):
        """7. Role + text matching (e.g. 'search button') correctly resolves."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource

        elements = [
            DesktopUIElement(
                role="input",
                text="Search",
                bounds={"left": 10, "top": 10, "width": 200, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
            DesktopUIElement(
                role="button",
                text="Search",
                bounds={"left": 220, "top": 10, "width": 80, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
        ]
        resolver = DesktopTargetResolver()
        elem, conf, err, fail = resolver.match_elements("search button", elements)
        assert elem is not None
        assert elem.role == "button"
        assert conf >= 0.90

    @pytest.mark.asyncio
    async def test_4_8_ocr_source_handling(self):
        """8. OCR target source is handled gracefully."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource

        elements = [
            DesktopUIElement(
                role="text",
                text="Unsaved changes in file",
                bounds={"left": 50, "top": 100, "width": 300, "height": 25},
                confidence=0.9,
                source=DesktopTargetSource.OCR,
            )
        ]
        resolver = DesktopTargetResolver()
        elem, conf, err, fail = resolver.match_elements("Unsaved changes", elements)
        assert elem is not None
        assert elem.source == DesktopTargetSource.OCR
        assert conf >= 0.70

    @pytest.mark.asyncio
    async def test_4_9_confidence_calculation(self):
        """9. Confidence scores are clamped between 0.0 and 1.0."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource

        resolver = DesktopTargetResolver()
        elem = DesktopUIElement(
            role="button",
            text="Submit",
            bounds={"left": 0, "top": 0, "width": 100, "height": 50},
            confidence=0.85,
            source=DesktopTargetSource.VISION,
        )
        _, conf, _, _ = resolver.match_elements("Submit", [elem])
        assert 0.0 <= conf <= 1.0

    @pytest.mark.asyncio
    async def test_4_10_confidence_threshold_rejection(self):
        """10. Low-confidence matches below threshold produce VISION_UNCERTAIN."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource, FailureClass

        elements = [
            DesktopUIElement(
                role="icon",
                text="Settings gear icon",
                bounds={"left": 10, "top": 10, "width": 30, "height": 30},
                confidence=0.5,
                source=DesktopTargetSource.VISION,
            )
        ]
        resolver = DesktopTargetResolver()
        elem, conf, err, fail = resolver.match_elements("preferences configuration", elements, confidence_threshold=0.80)
        assert elem is None
        assert fail in (FailureClass.VISION_UNCERTAIN, FailureClass.TARGET_NOT_FOUND)

    @pytest.mark.asyncio
    async def test_4_11_ambiguous_target_rejection(self):
        """11. Ambiguous targets with multiple equal matches return TARGET_NOT_FOUND error."""
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopUIElement, DesktopTargetSource, FailureClass

        elements = [
            DesktopUIElement(
                role="button",
                text="Save File",
                bounds={"left": 10, "top": 10, "width": 100, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
            DesktopUIElement(
                role="button",
                text="Save All",
                bounds={"left": 120, "top": 10, "width": 100, "height": 30},
                confidence=1.0,
                source=DesktopTargetSource.VISION,
            ),
        ]
        resolver = DesktopTargetResolver()
        # Query 'Save' matches both 'Save File' and 'Save All' with similar word containment scores
        elem, conf, err, fail = resolver.match_elements("Save", elements)
        assert elem is None
        assert fail == FailureClass.TARGET_NOT_FOUND
        assert "Ambiguous" in (err or "")

    def test_4_12_malformed_bounds_rejection(self):
        """12. Negative bounds are rejected by validator."""
        from app.desktop.resolver import DesktopTargetResolver

        resolver = DesktopTargetResolver()
        valid, reason = resolver.validate_element_bounds({"left": -10, "top": 0, "width": 100, "height": 50}, 1920, 1080)
        assert valid is False
        assert "Negative" in (reason or "")

    def test_4_13_out_of_window_bounds_rejection(self):
        """13. Bounds exceeding window dimensions are rejected."""
        from app.desktop.resolver import DesktopTargetResolver

        resolver = DesktopTargetResolver()
        valid, reason = resolver.validate_element_bounds({"left": 1850, "top": 0, "width": 100, "height": 50}, 1920, 1080)
        assert valid is False
        assert "exceeds" in (reason or "")

    def test_4_14_zero_area_target_rejection(self):
        """14. Zero width or zero height elements are rejected."""
        from app.desktop.resolver import DesktopTargetResolver

        resolver = DesktopTargetResolver()
        valid, reason = resolver.validate_element_bounds({"left": 10, "top": 10, "width": 0, "height": 50}, 1920, 1080)
        assert valid is False
        assert "Zero-area" in (reason or "")

    @pytest.mark.asyncio
    async def test_4_15_successful_target_result(self):
        """15. Successful target resolution returns complete DesktopTargetResolutionResult."""
        from unittest.mock import MagicMock
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, DesktopUIElement, DesktopTargetSource

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Google Chrome", "chrome.exe", 500)
        win = DesktopWindowState(
            hwnd=5000,
            process_id=500,
            executable="chrome.exe",
            application="Google Chrome",
            title="Google Chrome",
            left=0,
            top=0,
            width=1280,
            height=720,
        )
        elem = DesktopUIElement(
            role="input",
            text="Address Bar",
            bounds={"left": 100, "top": 40, "width": 800, "height": 36},
            confidence=1.0,
            source=DesktopTargetSource.VISION,
        )

        resolver = DesktopTargetResolver(driver=mock_driver)
        res = await resolver.resolve_target("Address Bar", window_state=win, candidate_elements=[elem])

        assert res.success is True
        assert res.element is not None
        assert res.center_x == 100 + 400
        assert res.center_y == 40 + 18
        assert res.role == "input"
        assert res.confidence >= 0.95

    @pytest.mark.asyncio
    async def test_4_16_target_not_found_result(self):
        """16. Unmatched target returns failure with TARGET_NOT_FOUND."""
        from unittest.mock import MagicMock
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, FailureClass

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Visual Studio Code", "code.exe", 600)
        win = DesktopWindowState(
            hwnd=6000,
            process_id=600,
            executable="code.exe",
            application="Visual Studio Code",
            title="VS Code",
            left=0,
            top=0,
            width=1000,
            height=800,
        )

        resolver = DesktopTargetResolver(driver=mock_driver)
        res = await resolver.resolve_target("NonExistentSpecialButtonXYZ", window_state=win, candidate_elements=[])

        assert res.success is False
        assert res.element is None
        assert res.failure_class == FailureClass.TARGET_NOT_FOUND

    @pytest.mark.asyncio
    async def test_4_17_telemetry_on_success(self):
        """17. DESKTOP_TARGET_RESOLVED event emitted on successful match."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, DesktopUIElement, DesktopTargetSource
        from app.actions.models import ActionType

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 700)
        win = DesktopWindowState(
            hwnd=7000,
            process_id=700,
            executable="notepad.exe",
            application="Notepad",
            title="Notes",
            left=0,
            top=0,
            width=800,
            height=600,
        )
        elem = DesktopUIElement(
            role="button",
            text="File",
            bounds={"left": 10, "top": 10, "width": 50, "height": 25},
            confidence=1.0,
            source=DesktopTargetSource.VISION,
        )

        with patch("app.desktop.resolver.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            resolver = DesktopTargetResolver(driver=mock_driver)
            res = await resolver.resolve_target("File", window_state=win, candidate_elements=[elem])
            assert res.success is True

            types = [call.args[0].action_type for call in mock_pub.call_args_list]
            assert ActionType.DESKTOP_TARGET_RESOLVED in types

    @pytest.mark.asyncio
    async def test_4_18_telemetry_on_failure(self):
        """18. DESKTOP_TARGET_NOT_FOUND event emitted on failed match."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState
        from app.actions.models import ActionType

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 700)
        win = DesktopWindowState(
            hwnd=7000,
            process_id=700,
            executable="notepad.exe",
            application="Notepad",
            title="Notes",
            left=0,
            top=0,
            width=800,
            height=600,
        )

        with patch("app.desktop.resolver.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            resolver = DesktopTargetResolver(driver=mock_driver)
            res = await resolver.resolve_target("MissingTab", window_state=win, candidate_elements=[])
            assert res.success is False

            types = [call.args[0].action_type for call in mock_pub.call_args_list]
            assert ActionType.DESKTOP_TARGET_NOT_FOUND in types

    @pytest.mark.asyncio
    async def test_4_19_no_screenshot_in_telemetry(self):
        """19. Verification that telemetry events contain zero image bytes or base64."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, DesktopUIElement, DesktopTargetSource

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 700)
        win = DesktopWindowState(
            hwnd=7000,
            process_id=700,
            executable="notepad.exe",
            application="Notepad",
            title="Notes",
            left=0,
            top=0,
            width=800,
            height=600,
        )
        elem = DesktopUIElement(
            role="button",
            text="File",
            bounds={"left": 10, "top": 10, "width": 50, "height": 25},
            confidence=1.0,
            source=DesktopTargetSource.VISION,
        )

        with patch("app.desktop.resolver.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            resolver = DesktopTargetResolver(driver=mock_driver)
            await resolver.resolve_target("File", window_state=win, candidate_elements=[elem])

            for call in mock_pub.call_args_list:
                meta = call.args[0].safe_metadata or {}
                for k, v in meta.items():
                    assert "image" not in k.lower()
                    assert "base64" not in k.lower()
                    assert "screenshot" not in k.lower()
                    if isinstance(v, str):
                        assert not v.startswith("data:image/")

    @pytest.mark.asyncio
    async def test_4_20_no_credential_data_in_telemetry(self):
        """20. Verification that telemetry metadata rejects credentials and tokens."""
        from unittest.mock import MagicMock, AsyncMock, patch
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, FailureClass

        mock_driver = MagicMock()
        win = DesktopWindowState(
            hwnd=7000,
            process_id=700,
            executable="notepad.exe",
            application="Notepad",
            title="Notes",
            left=0,
            top=0,
            width=800,
            height=600,
        )

        with patch("app.desktop.resolver.action_bus.publish", new_callable=AsyncMock) as mock_pub:
            resolver = DesktopTargetResolver(driver=mock_driver)
            # Passing a credential in the target query is blocked
            res = await resolver.resolve_target("password=SecretPassword123", window_state=win)
            assert res.success is False
            assert res.failure_class == FailureClass.SECURITY

    def test_4_21_no_subprocess_imports(self):
        """21. Resolver does not import or use subprocess, cmd, or powershell."""
        import ast
        import inspect
        from app.desktop import resolver

        src = inspect.getsource(resolver)
        tree = ast.parse(src)
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imported_modules.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module.split(".")[0])

        forbidden = {"subprocess", "shutil", "wscript"}
        assert not imported_modules.intersection(forbidden)
        assert "os.system(" not in src
        assert "os.popen(" not in src

    @pytest.mark.asyncio
    async def test_4_22_no_action_execution(self):
        """22. Target resolution performs perception only and executes zero mouse/keyboard actions."""
        from unittest.mock import MagicMock
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, DesktopUIElement, DesktopTargetSource

        mock_driver = MagicMock()
        mock_driver.validate_window_application.return_value = (True, "Calculator", "calc.exe", 100)
        win = DesktopWindowState(
            hwnd=1000,
            process_id=100,
            executable="calc.exe",
            application="Calculator",
            title="Calculator",
            left=0,
            top=0,
            width=400,
            height=500,
        )
        elem = DesktopUIElement(
            role="button",
            text="7",
            bounds={"left": 20, "top": 100, "width": 50, "height": 50},
            confidence=1.0,
            source=DesktopTargetSource.VISION,
        )
        resolver = DesktopTargetResolver(driver=mock_driver)
        res = await resolver.resolve_target("7", window_state=win, candidate_elements=[elem])
        assert res.success is True

        # Assert no action methods were invoked
        assert not mock_driver.click.called
        assert not mock_driver.type_text.called
        assert not mock_driver.press_key.called
        assert not mock_driver.hotkey.called
        assert not mock_driver.scroll.called

    @pytest.mark.asyncio
    async def test_4_23_unknown_application_rejection(self):
        """23. Reject targets belonging to unapproved applications."""
        from unittest.mock import MagicMock
        from app.desktop.resolver import DesktopTargetResolver
        from app.control.models import DesktopWindowState, FailureClass

        mock_driver = MagicMock()
        mock_driver.validate_window_application.side_effect = PermissionError("PERMISSION_DENIED")

        win = DesktopWindowState(
            hwnd=9999,
            process_id=999,
            executable="malicious.exe",
            application="Malicious App",
            title="Malicious Window",
            left=0,
            top=0,
            width=500,
            height=500,
        )
        resolver = DesktopTargetResolver(driver=mock_driver)
        res = await resolver.resolve_target("Submit", window_state=win)
        assert res.success is False
        assert res.failure_class == FailureClass.SECURITY

    def test_4_24_existing_phase_1_models_compatible(self):
        """24. Phase 1 DesktopUIElement and DesktopWindowState remain backward-compatible."""
        from app.control.models import DesktopUIElement, DesktopWindowState, DesktopTargetResolutionResult

        elem = DesktopUIElement(
            role="button",
            text="OK",
            bounds={"left": 10, "top": 20, "width": 100, "height": 40},
        )
        assert elem.center_x == 60
        assert elem.center_y == 40

        res = DesktopTargetResolutionResult(
            success=True,
            target="OK",
            element=elem,
            confidence=0.95,
        )
        assert res.center_x == 60
        assert res.center_y == 40
        assert res.bounds == {"left": 10, "top": 20, "width": 100, "height": 40}

    @pytest.mark.asyncio
    async def test_4_25_live_read_only_verification(self):
        """25. Minimal live observation and target resolution verification."""
        from app.desktop.resolver import desktop_target_resolver
        from app.desktop.interaction import desktop_driver

        windows = desktop_driver.inspect_windows()
        if windows:
            target_win = windows[0]
            res = await desktop_target_resolver.resolve_target("File", window_state=target_win)
            assert hasattr(res, "success")
            assert hasattr(res, "confidence")
            assert res.confidence >= 0.0







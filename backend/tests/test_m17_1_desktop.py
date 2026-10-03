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


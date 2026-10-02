"""Comprehensive test suite for RYVEN Milestone 14.3 — Controlled Computer & Browser Control Engine."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.browser.models import (
    BrowserActionRecord,
    BrowserActionType,
    BrowserSnapshot,
    BrowserState,
    BrowserStatus,
    ElementInfo,
)
from app.browser.security import BrowserSecurityValidator
from app.browser.engine import BrowserEngine, browser_engine
from app.browser.tools import (
    OpenBrowserTool,
    NavigateBrowserTool,
    GetCurrentPageTool,
    ReadPageTool,
    FindElementTool,
    ClickElementTool,
    TypeTextTool,
    PressKeyTool,
    ScrollPageTool,
    GoBackTool,
    GoForwardTool,
    RefreshPageTool,
    TakeBrowserSnapshotTool,
    CloseBrowserTool,
)
from app.core.permissions import safety_guard
from app.workflows.confirmation import ConfirmationManager
from app.workflows.models import WorkflowStep
from app.tools.registry import create_default_registry


# --------------------------------------------------------------------------
# 1. BROWSER SECURITY & URL VALIDATION
# --------------------------------------------------------------------------


def test_url_validation_safe_https():
    """1. Valid HTTPS URLs pass security validation."""
    is_safe, sanitized, err = BrowserSecurityValidator.validate_url("https://example.com")
    assert is_safe is True
    assert sanitized.startswith("https://example.com")
    assert err == ""


def test_url_validation_converts_search_query():
    """2. Plain text query converts to safe Google search URL."""
    is_safe, sanitized, err = BrowserSecurityValidator.validate_url("React 19 hooks tutorial", allow_search=True)
    assert is_safe is True
    assert "google.com/search?q=" in sanitized
    assert "React+19" in sanitized or "React%2019" in sanitized


def test_url_validation_blocks_disallowed_schemes():
    """3. Prohibits file://, javascript:, data:, vbscript:, ftp:."""
    for bad_url in [
        "file:///C:/Windows/System32/cmd.exe",
        "javascript:alert('xss')",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "ftp://example.com/files",
    ]:
        is_safe, _, err = BrowserSecurityValidator.validate_url(bad_url)
        assert is_safe is False
        assert "prohibited protocol scheme" in err.lower()


def test_url_validation_blocks_ssrf_and_private_ips():
    """4. Blocks localhost, 127.0.0.1, 169.254.169.254, and RFC 1918 private IPs."""
    for ssrf_target in [
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/router",
        "http://metadata.google.internal/computeMetadata/v1/",
    ]:
        is_safe, _, err = BrowserSecurityValidator.validate_url(ssrf_target)
        assert is_safe is False
        assert "ssrf" in err.lower() or "blocked" in err.lower()


def test_url_validation_rejects_control_characters():
    """5. Rejects shell injection and control characters in URLs."""
    for bad_input in [
        "https://example.com; rm -rf /",
        "https://example.com | netstat",
        "https://example.com`calc.exe`",
    ]:
        is_safe, _, err = BrowserSecurityValidator.validate_url(bad_input)
        assert is_safe is False
        assert "illegal control characters" in err.lower() or "ssrf" in err.lower()


# --------------------------------------------------------------------------
# 2. CONFIRMATION BOUNDARIES & CREDENTIAL REDACTION
# --------------------------------------------------------------------------


def test_confirmation_boundary_detection():
    """6. Action confirmation triggers for destructive or impactful element clicks."""
    assert BrowserSecurityValidator.is_confirmation_required("submit_form") is True
    assert BrowserSecurityValidator.is_confirmation_required("download_file") is True
    assert BrowserSecurityValidator.is_confirmation_required("delete_content") is True

    # Button text with impactful keywords
    assert BrowserSecurityValidator.is_confirmation_required("click_element", element_text="Submit Order") is True
    assert BrowserSecurityValidator.is_confirmation_required("click_element", element_text="Delete Account") is True
    assert BrowserSecurityValidator.is_confirmation_required("click_element", element_text="Pay $50.00") is True

    # Ordinary links/buttons do not require confirmation
    assert BrowserSecurityValidator.is_confirmation_required("click_element", element_text="Next Page") is False
    assert BrowserSecurityValidator.is_confirmation_required("click_element", element_text="Documentation") is False


def test_credential_redaction():
    """7. Sensitive keys (passwords, tokens, API keys) are masked."""
    payload = {
        "username": "developer",
        "password": "SuperSecretPassword123!",
        "api_key": "sk-1234567890abcdef1234567890",
        "profile": {"token": "ghp_xxxxxxxxxxxxxxxxxxxxxx", "name": "Alice"},
    }
    redacted = BrowserSecurityValidator.redact_credentials(payload)
    assert redacted["username"] == "developer"
    assert redacted["password"] == "[REDACTED_CREDENTIAL]"
    assert redacted["api_key"] == "[REDACTED_CREDENTIAL]"
    assert redacted["profile"]["token"] == "[REDACTED_CREDENTIAL]"
    assert redacted["profile"]["name"] == "Alice"


# --------------------------------------------------------------------------
# 3. BROWSER ENGINE SESSION LIFECYCLE
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_session_creation_and_open():
    """8. open_browser initializes an active session."""
    engine = BrowserEngine()
    result = await engine.open_browser(session_id="test-session-1")
    assert result["success"] is True
    assert result["session_id"] == "test-session-1"
    assert result["browser_status"] == BrowserStatus.ACTIVE

    state = engine.get_session("test-session-1")
    assert state is not None
    assert state.session_id == "test-session-1"


@pytest.mark.asyncio
async def test_browser_navigation_with_mock_html():
    """9. navigate_browser parses HTML, extracts title, headings, links, and text."""
    engine = BrowserEngine()
    sample_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Test Documentation Page</title></head>
      <body>
        <h1>Getting Started with RYVEN</h1>
        <h2>Overview</h2>
        <p>RYVEN is an autonomous developer orchestrator.</p>
        <a href="https://example.com/docs">Documentation Link</a>
        <button id="next-btn">Next Step</button>
      </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.text = sample_html
    mock_resp.status_code = 200

    with patch.object(engine, "_get_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        res = await engine.navigate_browser("https://example.com/docs", session_id="nav-session")
        assert res["success"] is True
        assert res["title"] == "Test Documentation Page"
        assert len(res["headings"]) >= 2
        assert "Getting Started with RYVEN" in res["headings"]

        state = engine.get_session("nav-session")
        assert state is not None
        assert state.current_url == "https://example.com/docs"
        assert state.page_title == "Test Documentation Page"
        assert state.last_snapshot is not None
        assert "RYVEN is an autonomous developer orchestrator." in state.last_snapshot.text_content


@pytest.mark.asyncio
async def test_browser_read_page():
    """10. read_page returns clean, visible text without HTML tags."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("read-sess")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Clean body content without scripts.",
        headings=["Heading 1"],
        links=[{"href": "https://example.com/a", "text": "Link A"}],
    )

    result = await engine.read_page(session_id="read-sess")
    assert result["success"] is True
    assert result["title"] == "Example"
    assert "Clean body content" in result["text"]


@pytest.mark.asyncio
async def test_browser_find_element():
    """11. find_element discovers elements by ID or text content."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("find-sess")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Page content",
        raw_html_truncated='<div><button id="submit-btn">Submit Order</button><a href="/docs">Docs</a></div>',
    )

    # Find by ID
    res_id = await engine.find_element("submit-btn", session_id="find-sess")
    assert res_id["success"] is True
    assert res_id["found"] is True

    # Find by text
    res_text = await engine.find_element("Docs", session_id="find-sess")
    assert res_text["success"] is True
    assert res_text["found"] is True


@pytest.mark.asyncio
async def test_browser_click_ordinary_element():
    """12. click_element on safe element executes without confirmation."""
    engine = BrowserEngine()
    res = await engine.click_element("Documentation Tab", session_id="click-sess")
    assert res["success"] is True
    assert "Successfully clicked" in res["message"]


@pytest.mark.asyncio
async def test_browser_click_sensitive_element_requires_confirmation():
    """13. click_element on submit/payment button pauses for user confirmation."""
    engine = BrowserEngine()
    res = await engine.click_element("Submit Payment", session_id="click-sens")
    assert res["success"] is False
    assert res["requires_confirmation"] is True
    assert "confirmation_token" in res

    state = engine.get_session("click-sens")
    assert state.browser_status == BrowserStatus.WAITING_CONFIRMATION

    # Re-executing with confirmed=True passes
    res_confirmed = await engine.click_element("Submit Payment", session_id="click-sens", confirmed=True)
    assert res_confirmed["success"] is True


@pytest.mark.asyncio
async def test_browser_type_text_and_press_key():
    """14. type_text and press_key record actions properly."""
    engine = BrowserEngine()
    type_res = await engine.type_text("search-input", "FastAPI tutorial", session_id="type-sess")
    assert type_res["success"] is True
    assert type_res["typed_length"] == len("FastAPI tutorial")

    key_res = await engine.press_key("Enter", session_id="type-sess")
    assert key_res["success"] is True
    assert key_res["key"] == "Enter"


@pytest.mark.asyncio
async def test_browser_scroll_and_history():
    """15. scroll_page and go_back / go_forward update state."""
    engine = BrowserEngine()
    scroll_res = await engine.scroll_page(direction="down", amount=300, session_id="scroll-sess")
    assert scroll_res["success"] is True
    assert scroll_res["direction"] == "down"

    # History navigation
    state = engine._get_or_create_session("hist-sess")
    state.history = ["https://example.com/1", "https://example.com/2"]
    state.history_index = 1

    with patch.object(engine, "navigate_browser", new_callable=AsyncMock) as mock_nav:
        mock_nav.return_value = {"success": True}
        back_res = await engine.go_back(session_id="hist-sess")
        assert back_res["success"] is True
        assert state.history_index == 0


@pytest.mark.asyncio
async def test_browser_snapshot_and_close():
    """16. take_browser_snapshot returns structured preview and close_browser marks CLOSED."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("snap-sess")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com",
        title="Snap Page",
        text_content="Snapshot preview text",
        headings=["H1", "H2"],
        links=[{"href": "/a", "text": "A"}],
    )

    snap_res = await engine.take_browser_snapshot(session_id="snap-sess")
    assert snap_res["success"] is True
    assert snap_res["title"] == "Snap Page"
    assert snap_res["headings"] == ["H1", "H2"]

    close_res = await engine.close_browser(session_id="snap-sess")
    assert close_res["success"] is True
    assert state.browser_status == BrowserStatus.CLOSED


# --------------------------------------------------------------------------
# 4. TOOL REGISTRY & SAFETY GUARD INTEGRATION
# --------------------------------------------------------------------------


def test_all_14_browser_tools_registered_in_registry():
    """17. All 14 M14.3 browser tools are present in default registry."""
    registry = create_default_registry()
    expected_tools = [
        "open_browser",
        "navigate_browser",
        "get_current_page",
        "read_page",
        "find_element",
        "click_element",
        "type_text",
        "press_key",
        "scroll_page",
        "go_back",
        "go_forward",
        "refresh_page",
        "take_browser_snapshot",
        "close_browser",
    ]
    for t_name in expected_tools:
        assert registry.has_tool(t_name) is True, f"Missing tool: {t_name}"


def test_safety_guard_permits_browser_tools():
    """18. SafetyGuard authorizes browser tools under controlled policy."""
    for tool_name in [
        "open_browser",
        "navigate_browser",
        "read_page",
        "take_browser_snapshot",
        "close_browser",
    ]:
        perm = safety_guard.validate_action(tool_name=tool_name, arguments={})
        assert perm.allowed is True
        assert perm.risk_level == "safe"


def test_safety_guard_blocks_shell_injections_in_browser_arguments():
    """19. SafetyGuard detects and blocks dangerous shell commands inside browser arguments."""
    perm = safety_guard.validate_action(
        tool_name="navigate_browser",
        arguments={"url": "https://example.com; powershell.exe -c calc"},
    )
    assert perm.allowed is False
    assert perm.risk_level == "blocked"


def test_confirmation_manager_evaluates_browser_steps():
    """20. ConfirmationManager tags safe vs impactful browser workflow steps."""
    cm = ConfirmationManager()

    safe_step = WorkflowStep(name="Read Docs", tool_name="read_page", arguments={})
    assert cm.requires_confirmation(safe_step) is False

    impactful_step = WorkflowStep(name="Submit Form", tool_name="browser_submit_form", arguments={})
    assert cm.requires_confirmation(impactful_step) is True


def test_open_application_allowlist_includes_explorer():
    """21. File Explorer is registered and located in OpenApplicationTool."""
    from app.tools.app_tool import OpenApplicationTool
    tool = OpenApplicationTool()
    assert "explorer" in tool.ALLOWLIST
    resolved = tool._resolve_application_key("file explorer")
    assert resolved == "explorer"
    assert tool.ALLOWLIST["explorer"]["display_name"] == "File Explorer"


# --------------------------------------------------------------------------
# 5. ACTION ENGINE & REPLAY INTEGRATION
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_tool_execution_emits_action_events():
    """22. Executing a browser tool emits STARTED and COMPLETED events into action_bus."""
    from app.actions.event_bus import action_bus
    from app.actions.models import ActionStatus
    registry = create_default_registry()

    task_id = "browser-test-action-task"
    res = await registry.execute_tool("get_current_page", arguments={}, task_id=task_id)
    assert res["success"] is True

    events = action_bus.get_task_events(task_id)
    assert len(events) == 2
    assert events[0].status == ActionStatus.STARTED
    assert events[1].status == ActionStatus.COMPLETED
    assert events[1].safe_metadata.get("tool") == "get_current_page"


@pytest.mark.asyncio
async def test_replay_safety_with_browser_events():
    """23. ReplaySession produces replay frames without invoking browser commands."""
    from app.actions.event_bus import action_bus
    from app.actions.models import ActionEvent, ActionStatus, ActionType
    from app.actions.service import action_service

    # Create dummy browser action events for a task
    task_id = "replay-browser-task"
    e1 = ActionEvent(
        task_id=task_id,
        action_type=ActionType.TOOL_EXECUTE,
        status=ActionStatus.STARTED,
        title="Tool: navigate_browser",
        safe_metadata={"tool": "navigate_browser"},
    )
    e2 = ActionEvent(
        task_id=task_id,
        action_type=ActionType.TOOL_EXECUTE,
        status=ActionStatus.COMPLETED,
        title="Tool: navigate_browser",
        safe_metadata={"tool": "navigate_browser"},
        result_summary={"title": "Test Page"},
    )
    await action_bus.publish(e1)
    await action_bus.publish(e2)

    session = action_service.create_replay(task_id)
    assert session is not None
    assert session.total_frames >= 2
    assert session.current_frame is not None

    frame1 = action_service.replay_next(task_id)
    assert frame1 is not None
    assert "navigate_browser" in frame1["event"]["title"]



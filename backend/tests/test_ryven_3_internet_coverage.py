"""RYVEN 3.0 — M15 Internet Agent Coverage Test Matrix (A through X).

Verifies the 24 explicit requirements specified in Section 17:
A. InternetAgent creation
B. Search
C. Google search
D. YouTube search
E. Page reading
F. Navigation
G. Element discovery
H. Click
I. Type
J. Verification
K. Failed verification
L. Retry
M. Authentication detection
N. Authentication pause
O. Authentication resume
P. Secret redaction
Q. SSRF blocking
R. Confirmation boundary
S. ActionEvent emission
T. SSE compatibility
U. Orchestrator integration
V. Existing browser regression
W. Composite browser regression
X. Backward compatibility of open_website
"""

import json
from unittest.mock import patch
import pytest

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.agent.capability_router import CapabilityRouter
from app.agent.models import CapabilityGroup
from app.agent.planner import AgentPlanner
from app.browser.models import BrowserSnapshot
from app.internet.agent import InternetAgent, internet_agent
from app.internet.auth_manager import AuthenticationSessionManager
from app.internet.models import AuthStatus, SearchResult, WebTaskStatus
from app.internet.navigation_service import NavigationService
from app.internet.page_reader import PageReader
from app.internet.search_service import SearchService
from app.internet.security import InternetSecurityPolicy
from app.internet.task_service import WebTaskService
from app.internet.verification_service import WebVerificationService
from app.tools.registry import create_default_registry
from app.tools.website_tool import OpenWebsiteTool


# A. InternetAgent creation
def test_m15_a_internet_agent_creation():
    agent = InternetAgent()
    assert agent.browser_engine is not None
    assert agent.search_service is not None
    assert agent.page_reader is not None
    assert agent.navigation_service is not None
    assert agent.element_service is not None
    assert agent.auth_manager is not None
    assert agent.verification_service is not None
    assert agent.task_service is not None
    st = agent.get_state()
    assert st.status == WebTaskStatus.IDLE


# B. Search
@pytest.mark.asyncio
async def test_m15_b_search():
    res = await internet_agent.search("FastAPI documentation", max_results=3)
    assert res["success"] is True
    assert res["count"] >= 1
    item = res["results"][0]
    assert "title" in item
    assert "url" in item
    assert "snippet" in item
    assert "source_info" in item
    assert "timestamp" in item
    assert "sanitized_content" in item


# C. Google search URL generation
def test_m15_c_google_search():
    url = SearchService.build_google_url("React tutorials")
    assert url.startswith("https://www.google.com/search?q=")
    assert "React" in url


# D. YouTube search URL generation
def test_m15_d_youtube_search():
    url = SearchService.build_youtube_url("self")
    assert url.startswith("https://www.youtube.com/results?search_query=")
    assert "self" in url


# E. Page reading
@pytest.mark.asyncio
async def test_m15_e_page_reading():
    await internet_agent.navigate("https://react.dev")
    page = await internet_agent.read_page()
    assert page["success"] is True
    assert "text" in page
    assert "title" in page


# F. Navigation
@pytest.mark.asyncio
async def test_m15_f_navigation():
    nav_res = await internet_agent.navigate("https://github.com")
    assert nav_res["success"] is True
    target = nav_res.get("url") or nav_res.get("current_url") or ""
    assert "github.com" in target


# G. Element discovery
@pytest.mark.asyncio
async def test_m15_g_element_discovery():
    found = await internet_agent.find_element("button")
    assert "elements" in found or "found" in found


# H. Click
@pytest.mark.asyncio
async def test_m15_h_click():
    res = await internet_agent.click("button")
    assert "success" in res


# I. Type
@pytest.mark.asyncio
async def test_m15_i_type():
    res = await internet_agent.type("input[type=search]", "test query")
    assert "success" in res


# J. Verification
def test_m15_j_verification():
    ver = WebVerificationService.verify_navigation(
        expected_url_or_pattern="github.com",
        current_url="https://github.com/trending",
        page_title="GitHub Trending",
    )
    assert ver["verified"] is True


# K. Failed verification
def test_m15_k_failed_verification():
    ver = WebVerificationService.verify_navigation(
        expected_url_or_pattern="google.com",
        current_url="https://github.com/trending",
        page_title="GitHub Trending",
    )
    assert ver["verified"] is False
    assert "Navigation target mismatch" in ver["reason"]


# L. Retry
@pytest.mark.asyncio
async def test_m15_l_retry():
    call_count = 0

    def action():
        nonlocal call_count
        call_count += 1
        return {"attempt": call_count}

    def verify(res):
        return {"verified": res["attempt"] >= 2, "reason": f"Checked attempt {res['attempt']}"}

    res = await WebVerificationService.verify_action_with_retry(
        action_func=action,
        verify_func=verify,
        max_retries=2,
        delay_s=0.01,
    )
    assert res["verified"] is True
    assert res["attempts"] == 2


# M. Authentication detection
def test_m15_m_authentication_detection():
    manager = AuthenticationSessionManager()
    is_auth, reason = manager.is_login_required("https://github.com/login", "")
    assert is_auth is True
    assert "login" in reason.lower()

    is_pw, reason = manager.is_login_required("https://site.org", '<form><input type="password" /></form>')
    assert is_pw is True
    assert "form" in reason.lower()


# N. Authentication pause
def test_m15_n_authentication_pause():
    manager = AuthenticationSessionManager()
    session = manager.pause_for_authentication(
        task_id="task-101",
        session_id="session-01",
        current_url="https://github.com/login",
        reason="Login required",
    )
    assert session.resume_token.startswith("auth-")
    assert session.auth_status in (AuthStatus.REQUIRED, AuthStatus.WAITING_FOR_USER)


# O. Authentication resume
def test_m15_o_authentication_resume():
    manager = AuthenticationSessionManager()
    session = manager.pause_for_authentication(
        task_id="task-102",
        session_id="session-02",
        current_url="https://github.com/login",
        reason="Login required",
    )
    resumed = manager.resume_authentication(session.resume_token)
    assert resumed is not None
    assert resumed.auth_status == AuthStatus.AUTHENTICATED


# P. Secret redaction
def test_m15_p_secret_redaction():
    text = "Authorization: Bearer my-secret-api-token-12345"
    sanitized = InternetSecurityPolicy.redact_secrets(text)
    assert "my-secret-api-token-12345" not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized or "[REDACTED_SECRET]" in sanitized


# Q. SSRF blocking
def test_m15_q_ssrf_blocking():
    blocked = [
        "http://127.0.0.1:8000/api",
        "http://localhost:3000",
        "http://169.254.169.254/latest",
        "file:///C:/secret.txt",
        "javascript:alert(1)",
    ]
    for url in blocked:
        is_safe, _, _ = InternetSecurityPolicy.validate_target_url(url)
        assert is_safe is False


# R. Confirmation boundary
def test_m15_r_confirmation_boundary():
    assert InternetSecurityPolicy.is_confirmation_required("delete_resource") is True
    assert InternetSecurityPolicy.is_confirmation_required("purchase") is True
    assert InternetSecurityPolicy.is_confirmation_required("search") is False
    assert InternetSecurityPolicy.is_confirmation_required("read_page") is False


# S. ActionEvent emission
@pytest.mark.asyncio
async def test_m15_s_action_event_emission():
    emitted = []

    async def async_listener(event: ActionEvent):
        emitted.append(event)

    sub_id = action_bus.subscribe(async_listener)
    try:
        await internet_agent.search("FastAPI", max_results=1)
        import asyncio
        await asyncio.sleep(0.05)
        action_types = [e.action_type for e in emitted]
        assert ActionType.SEARCH_STARTED in action_types
        assert ActionType.SEARCH_COMPLETED in action_types
    finally:
        action_bus.unsubscribe(sub_id)


# T. SSE compatibility
def test_m15_t_sse_compatibility():
    event = ActionEvent(
        task_id="task-sse-01",
        action_type=ActionType.INTERNET_TASK_STARTED,
        status=ActionStatus.STARTED,
        title="SSE Test Task",
        description="Testing SSE serialization format",
    )
    payload = f"data: {event.model_dump_json()}\n\n"
    assert payload.startswith("data: {")
    assert payload.endswith("}\n\n")
    parsed = json.loads(payload.replace("data: ", "").strip())
    assert parsed["action_type"] == "INTERNET_TASK_STARTED"


# U. Orchestrator integration
def test_m15_u_orchestrator_integration():
    planner = AgentPlanner()
    plan = planner.plan("research FastAPI documentation and summarize it")
    assert plan is not None
    assert len(plan.steps) >= 1
    assert CapabilityGroup.INTERNET in plan.capabilities_required


# V. Existing browser regression
def test_m15_v_existing_browser_regression():
    registry = create_default_registry()
    browser_tools = [
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
    for bt in browser_tools:
        assert registry.has_tool(bt), f"Missing browser tool: {bt}"


# W. Composite browser regression
def test_m15_w_composite_browser_regression():
    planner = AgentPlanner()
    plan = planner.plan("open chrome and search youtube for self")
    assert len(plan.steps) == 3
    assert plan.steps[0].tool_name == "open_application"
    assert plan.steps[1].tool_name == "navigate_browser"
    assert "youtube.com" in plan.steps[1].arguments.get("url", "")
    assert plan.steps[2].tool_name == "read_page"


# X. Backward compatibility of open_website
@pytest.mark.asyncio
async def test_m15_x_backward_compatibility_open_website():
    tool = OpenWebsiteTool()
    with patch("webbrowser.open", return_value=True) as mock_wb:
        res = await tool.execute(url="github")
        assert res["success"] is True
        assert res["tool"] == "open_website"
        assert res["url"] == "https://github.com"
        mock_wb.assert_called_once_with("https://github.com")

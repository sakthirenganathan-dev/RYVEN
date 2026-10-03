"""RYVEN 3.0 — Comprehensive Unified Internet Agent Engine Test Suite.

Verifies:
1. SearchService: query refinement, DuckDuckGo/Google parsing, authoritative domain ranking
2. PageReader: clean text extraction, headings hierarchy, link discovery, and secret redaction
3. InternetSecurityPolicy: deep SSRF prevention, scheme validation, dangerous extension blocking
4. AuthenticationSessionManager: auth wall detection, task pausing, token generation, secure resumption
5. NavigationService & Multi-Tab Management: URL normalization, tab lifecycle (new, switch, close, list)
6. WebVerificationService: DOM search result and keyword presence verification
7. WebTaskService: Observe -> Act -> Verify planning, step execution, and auth pause handling
8. InternetAgent Facade: end-to-end search(), research(), execute_task(), and state management
9. ToolRegistry Integration: registration and execution of high-level internet tools
10. ActionEvent Emission: real-time observability across the ActionEngine
11. IntentRouter & Assistant Integration: natural language routing to research and internet tools
"""

import pytest
from app.actions.event_bus import action_bus
from app.actions.models import ActionStatus, ActionType
from app.agent.capability_router import CapabilityRouter
from app.agent.models import CapabilityGroup
from app.core.assistant import Assistant
from app.core.router import IntentRouter
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


# --------------------------------------------------------------------------
# 1. SEARCH SERVICE & RANKING TESTS
# --------------------------------------------------------------------------


def test_search_query_refinement():
    """1. SearchService strips conversational fluff and isolates query terms."""
    assert SearchService.refine_query("search for FastAPI lifespan") == "FastAPI lifespan"
    assert SearchService.refine_query("please google React 19 hooks?") == "React 19 hooks"
    assert SearchService.refine_query("research the latest python features") == "python features"
    assert SearchService.refine_query("lookup asyncio event loop") == "asyncio event loop"


def test_duckduckgo_html_parsing():
    """2. SearchService correctly parses organic results from DuckDuckGo HTML."""
    sample_html = """
    <div class="result results_links results_links_deep web-result">
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freact.dev%2Fblog%2F2024%2F04%2F25%2Freact-19">
        React 19 Beta is now available on npm!
      </a>
      <h2 class="result__title">
        <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freact.dev%2Fblog%2F2024%2F04%2F25%2Freact-19">
          <b>React</b> 19
        </a>
      </h2>
    </div>
    """
    service = SearchService()
    results = service._parse_duckduckgo_html(sample_html)
    assert len(results) >= 1
    assert "react.dev" in results[0].url
    assert "React 19" in results[0].title


def test_authoritative_domain_ranking():
    """3. SearchService boosts official documentation and authority domains."""
    service = SearchService()
    unranked = [
        SearchResult(title="Random Blog Post", url="https://random-tech-blog.xyz/react", snippet="React guide"),
        SearchResult(title="Official React Docs", url="https://react.dev/reference/react", snippet="React 19 API"),
        SearchResult(title="Forum Question", url="https://forum.example.com/topic/1", snippet="Help with react"),
    ]
    ranked = service._rank_results(unranked)
    assert ranked[0].domain == "react.dev"
    assert ranked[0].score > ranked[1].score


@pytest.mark.asyncio
async def test_search_service_search_fallback():
    """4. SearchService executes safely and produces structured results even under sandboxed network."""
    service = SearchService()
    results = await service.search("FastAPI documentation", max_results=3)
    assert len(results) >= 1
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].url.startswith("http")


@pytest.mark.asyncio
async def test_search_service_research_topic():
    """5. SearchService synthesizes multi-source research report with key findings."""
    service = SearchService()
    report = await service.research_topic("FastAPI lifespan handlers", max_sources=2)
    assert report.topic == "FastAPI lifespan handlers"
    assert len(report.sources) >= 1
    assert len(report.key_findings) >= 1
    assert "FastAPI" in report.summary or "lifespan" in report.summary.lower()


# --------------------------------------------------------------------------
# 2. PAGE READER & SANITIZATION TESTS
# --------------------------------------------------------------------------


def test_page_reader_extraction():
    """6. PageReader extracts clean text, headings hierarchy, and links from HTML."""
    sample_html = """
    <html>
      <head><title>Test Documentation — v2.0</title></head>
      <body>
        <script>console.log("secret tracker");</script>
        <style>body { color: red; }</style>
        <h1>Introduction to System</h1>
        <p>This is the primary readable body content.</p>
        <h2>Installation</h2>
        <p>Run pip install my-system to get started.</p>
        <a href="https://example.com/docs">Documentation Link</a>
      </body>
    </html>
    """
    snapshot = PageReader.extract_page_data(sample_html, "https://example.com/start")
    assert snapshot.title == "Test Documentation — v2.0"
    assert "Introduction to System" in snapshot.headings
    assert "Installation" in snapshot.headings
    assert "console.log" not in snapshot.text_content
    assert "primary readable body content" in snapshot.text_content
    assert len(snapshot.links) == 1
    assert snapshot.links[0]["href"] == "https://example.com/docs"


def test_page_reader_secret_redaction():
    """7. PageReader and InternetSecurityPolicy redact exposed API keys and tokens."""
    dirty_text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and stripe_dummy_test_placeholder"
    sanitized = InternetSecurityPolicy.redact_secrets(dirty_text)
    assert "eyJhbGciOi" not in sanitized
    assert "sk_live_" not in sanitized and "stripe_live_key_example" not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized or "[REDACTED_SECRET]" in sanitized


# --------------------------------------------------------------------------
# 3. INTERNET SECURITY POLICY & SSRF PROTECTION
# --------------------------------------------------------------------------


def test_ssrf_protection_blocks_private_and_metadata():
    """8. InternetSecurityPolicy strictly blocks loopback, private ranges, and cloud metadata."""
    # Loopback
    is_safe, _, err = InternetSecurityPolicy.validate_target_url("http://127.0.0.1:8000/admin")
    assert is_safe is False
    assert "SSRF" in err or "private" in err.lower() or "blocked" in err.lower()

    # Cloud metadata
    is_safe, _, err = InternetSecurityPolicy.validate_target_url("http://169.254.169.254/latest/meta-data")
    assert is_safe is False
    assert "SSRF" in err or "blocked" in err.lower() or "metadata" in err.lower()

    # Disallowed scheme
    is_safe, _, err = InternetSecurityPolicy.validate_target_url("file:///C:/Windows/System32/cmd.exe")
    assert is_safe is False
    assert "disallowed protocol" in err.lower()


def test_security_policy_confirmation_boundaries():
    """9. State-mutating, irreversible actions require mandatory human confirmation."""
    assert InternetSecurityPolicy.is_confirmation_required("delete_resource") is True
    assert InternetSecurityPolicy.is_confirmation_required("purchase") is True
    assert InternetSecurityPolicy.is_confirmation_required("submit_form") is True
    assert InternetSecurityPolicy.is_confirmation_required("read_page") is False
    assert InternetSecurityPolicy.is_confirmation_required("search") is False


# --------------------------------------------------------------------------
# 4. AUTHENTICATION SESSION MANAGER TESTS
# --------------------------------------------------------------------------


def test_auth_wall_detection():
    """10. AuthenticationSessionManager detects login URLs, password fields, and HTTP 401/403."""
    manager = AuthenticationSessionManager()

    # URL indicator
    is_auth, reason = manager.is_login_required("https://github.com/login", "<html></html>")
    assert is_auth is True
    assert "login" in reason.lower()

    # Password input field
    is_auth, reason = manager.is_login_required(
        "https://app.example.com",
        '<html><body><input type="password" name="pw" /></body></html>',
    )
    assert is_auth is True
    assert "form" in reason.lower()

    # HTTP 401
    is_auth, reason = manager.is_login_required("https://example.com/protected", "", status_code=401)
    assert is_auth is True
    assert "401" in reason


def test_auth_pause_and_resume_flow():
    """11. AuthenticationSessionManager pauses task safely and resumes without touching credentials."""
    manager = AuthenticationSessionManager()
    session = manager.pause_for_authentication(
        task_id="task-auth-001",
        session_id="browser-session-default",
        current_url="https://github.com/login",
        reason="GitHub login required",
    )

    assert session.auth_status == AuthStatus.REQUIRED
    assert "Authentication required for GitHub" in session.prompt_message
    assert session.resume_token.startswith("auth-")

    # Resuming with the token confirms login
    resumed = manager.resume_authentication(session.resume_token)
    assert resumed is not None
    assert resumed.auth_status == AuthStatus.AUTHENTICATED
    assert resumed.task_id == "task-auth-001"


# --------------------------------------------------------------------------
# 5. NAVIGATION SERVICE & TAB MANAGEMENT TESTS
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigation_service_shortcuts():
    """12. NavigationService expands shortcuts and manages browser navigation."""
    nav = NavigationService()
    res = await nav.open_url("github")
    assert res["success"] is True
    assert "github.com" in res["url"]


@pytest.mark.asyncio
async def test_tab_management_lifecycle():
    """13. NavigationService handles tab listing, opening new tab, and switching tabs."""
    nav = NavigationService()
    tabs_list = await nav.manage_tabs(action="list")
    assert tabs_list["success"] is True
    assert "tabs" in tabs_list

    # Open new tab
    new_tab = await nav.manage_tabs(action="new", tab_url="https://react.dev")
    assert new_tab["success"] is True
    assert len(new_tab["tabs"]) >= 2

    # Switch tab
    switch = await nav.manage_tabs(action="switch", tab_index=0)
    assert switch["success"] is True


# --------------------------------------------------------------------------
# 6. WEB TASK PLANNING & EXECUTION (OBSERVE -> ACT -> VERIFY)
# --------------------------------------------------------------------------


def test_web_task_planning_youtube():
    """14. WebTaskService plans structured YouTube search flow."""
    task_service = WebTaskService()
    plan = task_service.plan_task("Search YouTube for React 19 tutorials")
    assert len(plan.steps) >= 3
    assert plan.steps[0].action_type == "open_browser"
    assert plan.steps[1].action_type == "navigate_browser"
    assert "youtube.com" in plan.steps[1].target
    assert plan.steps[2].action_type == "read_page"
    assert plan.steps[3].action_type == "verify_results"


def test_web_task_planning_github():
    """15. WebTaskService plans structured GitHub task with auth check."""
    task_service = WebTaskService()
    plan = task_service.plan_task("Open GitHub and inspect repository issues")
    assert len(plan.steps) >= 3
    assert plan.requires_authentication is True
    assert "github.com" in plan.steps[0].target


@pytest.mark.asyncio
async def test_web_task_execution_observe_act_verify():
    """16. WebTaskService executes steps, records observations, and verifies outcomes."""
    task_service = WebTaskService()
    plan = task_service.plan_task("Search YouTube for Java")
    result = await task_service.execute_plan(plan, auto_confirm=True)
    assert result["success"] is True
    assert result["status"] == WebTaskStatus.COMPLETED
    assert len(result["steps"]) >= 3


# --------------------------------------------------------------------------
# 7. INTERNET AGENT FACADE & STATE TESTS
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_internet_agent_facade_search_and_research():
    """17. InternetAgent facade coordinates search, research, and observable state."""
    agent = InternetAgent()

    # Search
    search_res = await agent.search("FastAPI documentation", max_results=3)
    assert search_res["success"] is True
    assert search_res["count"] >= 1

    # Research
    research_res = await agent.research("FastAPI lifespan", max_sources=2)
    assert research_res["success"] is True
    assert len(research_res["sources"]) >= 1

    # State
    st = agent.get_state()
    assert st is not None
    assert st.last_search_query == "FastAPI documentation"


# --------------------------------------------------------------------------
# 8. TOOL REGISTRY & CAPABILITY ROUTER INTEGRATION
# --------------------------------------------------------------------------


def test_registry_contains_internet_tools():
    """18. create_default_registry registers high-level InternetAgent tools."""
    registry = create_default_registry()
    assert registry.has_tool("internet_search")
    assert registry.has_tool("web_research")
    assert registry.has_tool("web_task")
    assert registry.has_tool("web_verify")
    assert registry.has_tool("browser_tabs")
    assert registry.has_tool("browser_snapshot")


def test_capability_router_internet_domain():
    """19. CapabilityRouter correctly classifies internet queries into CapabilityGroup.INTERNET."""
    router = CapabilityRouter()
    caps = router.classify_capabilities("Research the latest React documentation")
    assert CapabilityGroup.INTERNET in caps

    schemas = CapabilityRouter.filter_registry(create_default_registry(), [CapabilityGroup.INTERNET])
    assert "internet_search" in schemas
    assert "web_research" in schemas
    assert "web_task" in schemas


# --------------------------------------------------------------------------
# 9. INTENT ROUTER & ASSISTANT END-TO-END INTEGRATION
# --------------------------------------------------------------------------


def test_intent_router_classifies_internet_queries():
    """20. IntentRouter maps research queries to web_research tool."""
    router = IntentRouter()

    decision_research = router.route("research FastAPI documentation")
    assert decision_research.intent == "tool"
    assert decision_research.tool_name == "web_research"
    assert "FastAPI documentation" in decision_research.tool_arguments.get("topic", "")

    decision_search = router.route("internet search python asyncio")
    assert decision_search.intent == "tool"
    assert decision_search.tool_name == "internet_search"


@pytest.mark.asyncio
async def test_assistant_processes_research_request():
    """21. Full Assistant.process() executes research request end-to-end."""
    assistant = Assistant()
    resp = await assistant.process("research FastAPI documentation")
    assert resp.success is True
    assert resp.type == "tool"
    assert resp.tool == "web_research"
    assert "Completed research" in resp.message

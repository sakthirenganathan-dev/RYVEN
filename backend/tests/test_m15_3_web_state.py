"""M15.3 Phase 2 — Unified WebPageState & Form Extraction Tests.

Tests are strictly ADDITIVE.  No existing tests are modified.
All tests run without network access (fully in-memory).

Password-safety invariant (enforced throughout):
  - FormFieldInfo.is_password=True is set for password inputs.
  - No value is ever populated for password fields.
  - WebPageState.text_content is always credential-redacted.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestFormFieldInfo:
    """Unit tests for FormFieldInfo model construction and defaults."""

    def test_basic_text_field(self):
        from app.internet.models import FormFieldInfo

        f = FormFieldInfo(input_type="text", name="username", label_text="Username")
        assert f.input_type == "text"
        assert f.name == "username"
        assert f.label_text == "Username"
        assert f.is_password is False
        assert f.is_required is False
        assert f.is_readonly is False
        assert f.options == []
        assert f.tab_index is None

    def test_password_field_has_no_value(self):
        """Security: FormFieldInfo has no 'value' field at all."""
        from app.internet.models import FormFieldInfo

        f = FormFieldInfo(input_type="password", name="pwd")
        assert f.is_password is True
        assert not hasattr(f, "value"), "FormFieldInfo MUST NOT have a value field"

    def test_required_and_validation_constraints(self):
        from app.internet.models import FormFieldInfo

        f = FormFieldInfo(
            input_type="email",
            name="email",
            is_required=True,
            min_length=5,
            max_length=100,
            pattern=r"[^@]+@[^@]+\.[^@]+",
        )
        assert f.is_required is True
        assert f.min_length == 5
        assert f.max_length == 100
        assert f.pattern is not None

    def test_select_field_with_options(self):
        from app.internet.models import FormFieldInfo

        f = FormFieldInfo(
            input_type="select",
            name="country",
            options=["US", "UK", "CA"],
            label_text="Country",
        )
        assert f.input_type == "select"
        assert "US" in f.options
        assert len(f.options) == 3

    def test_serialization_roundtrip(self):
        from app.internet.models import FormFieldInfo

        f = FormFieldInfo(
            field_id="fname",
            name="first_name",
            input_type="text",
            label_text="First Name",
            is_required=True,
        )
        d = f.model_dump()
        f2 = FormFieldInfo(**d)
        assert f2.field_id == "fname"
        assert f2.label_text == "First Name"
        assert f2.is_required is True


class TestWebFormInfo:
    """Unit tests for WebFormInfo model and auto-computed fields."""

    def test_empty_form(self):
        from app.internet.models import WebFormInfo

        form = WebFormInfo()
        assert form.field_count == 0
        assert form.has_password_field is False
        assert form.fields == []

    def test_field_count_computed(self):
        from app.internet.models import FormFieldInfo, WebFormInfo

        form = WebFormInfo(
            fields=[
                FormFieldInfo(input_type="text", name="user"),
                FormFieldInfo(input_type="password", name="pass"),
            ]
        )
        assert form.field_count == 2

    def test_has_password_field_flag(self):
        from app.internet.models import FormFieldInfo, WebFormInfo

        form = WebFormInfo(
            fields=[
                FormFieldInfo(input_type="text", name="user"),
                FormFieldInfo(input_type="password", name="pass"),
            ]
        )
        assert form.has_password_field is True

    def test_no_password_field_flag(self):
        from app.internet.models import FormFieldInfo, WebFormInfo

        form = WebFormInfo(
            fields=[FormFieldInfo(input_type="text", name="search")]
        )
        assert form.has_password_field is False

    def test_form_attributes(self):
        from app.internet.models import WebFormInfo

        form = WebFormInfo(
            form_id="login-form",
            form_name="loginForm",
            action="/auth/login",
            method="post",
            submit_labels=["Sign In"],
        )
        assert form.form_id == "login-form"
        assert form.method == "post"
        assert "Sign In" in form.submit_labels

    def test_serialization_roundtrip(self):
        from app.internet.models import FormFieldInfo, WebFormInfo

        form = WebFormInfo(
            form_id="reg",
            action="/register",
            method="post",
            fields=[FormFieldInfo(input_type="email", name="email", is_required=True)],
            submit_labels=["Register"],
        )
        d = form.model_dump()
        form2 = WebFormInfo(**d)
        assert form2.form_id == "reg"
        assert form2.field_count == 1


class TestWebPageState:
    """Unit tests for WebPageState aggregation model."""

    def test_empty_page_state(self):
        from app.internet.models import WebPageState

        state = WebPageState(url="https://example.com")
        assert state.url == "https://example.com"
        assert state.forms_count == 0
        assert state.forms == []
        assert state.page_load_status == "loaded"

    def test_forms_count_computed(self):
        from app.internet.models import WebFormInfo, WebPageState

        state = WebPageState(
            url="https://example.com/login",
            forms=[WebFormInfo(form_id="f1"), WebFormInfo(form_id="f2")],
        )
        assert state.forms_count == 2

    def test_text_content_stored(self):
        from app.internet.models import WebPageState

        state = WebPageState(
            url="https://example.com",
            text_content="Welcome to RYVEN.",
            headings=["Welcome"],
        )
        assert "RYVEN" in state.text_content
        assert "Welcome" in state.headings

    def test_perception_optional(self):
        from app.internet.models import WebPageState

        state = WebPageState(url="https://example.com")
        assert state.perception_summary is None
        assert state.detected_elements_count == 0

    def test_serialization(self):
        from app.internet.models import FormFieldInfo, WebFormInfo, WebPageState

        state = WebPageState(
            url="https://example.com/login",
            title="Login Page",
            forms=[
                WebFormInfo(
                    form_id="login",
                    fields=[
                        FormFieldInfo(input_type="email", name="email"),
                        FormFieldInfo(input_type="password", name="password"),
                    ],
                    submit_labels=["Log In"],
                )
            ],
        )
        d = state.model_dump()
        assert d["url"] == "https://example.com/login"
        assert d["forms_count"] == 1
        assert d["forms"][0]["has_password_field"] is True


# ---------------------------------------------------------------------------
# BrowserEngine.extract_forms() unit tests
# ---------------------------------------------------------------------------


def _make_engine_with_html(html_content: str, url: str = "https://example.com"):
    """Helper: create a BrowserEngine with a pre-loaded snapshot."""
    from app.browser.engine import BrowserEngine
    from app.browser.models import BrowserSnapshot, BrowserState, BrowserStatus

    engine = BrowserEngine()
    sid = "test-session"
    snap = BrowserSnapshot(
        url=url,
        title="Test Page",
        text_content="Test page content",
        raw_html_truncated=html_content,
    )
    state = BrowserState(
        session_id=sid,
        current_url=url,
        page_title="Test Page",
        browser_status=BrowserStatus.ACTIVE,
        last_snapshot=snap,
    )
    engine._sessions[sid] = state
    engine._active_session_id = sid
    return engine, sid


@pytest.mark.asyncio
class TestExtractForms:
    """BrowserEngine.extract_forms() DOM parsing tests."""

    async def test_no_snapshot_returns_failure(self):
        from app.browser.engine import BrowserEngine

        engine = BrowserEngine()
        result = await engine.extract_forms()
        assert result["success"] is False
        assert result["forms_count"] == 0

    async def test_single_login_form(self):
        html = """
        <html><body>
        <form id="login" action="/login" method="post">
            <label for="user">Username</label>
            <input type="text" id="user" name="username" required>
            <label for="pwd">Password</label>
            <input type="password" id="pwd" name="password">
            <input type="submit" value="Sign In">
        </form>
        </body></html>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        assert result["success"] is True
        assert result["forms_count"] == 1

        form = result["forms"][0]
        assert form["form_id"] == "login"
        assert form["method"] == "post"
        assert form["action"] == "/login"
        assert form["has_password_field"] is True
        assert "Sign In" in form["submit_labels"]

        fields = {f["name"]: f for f in form["fields"]}
        assert "username" in fields
        assert "password" in fields

        # PASSWORD SAFETY: label is present, but no value field
        pwd_field = fields["password"]
        assert pwd_field["is_password"] is True
        assert "value" not in pwd_field

    async def test_label_resolution_via_label_for(self):
        html = """
        <form>
            <label for="em">Email Address</label>
            <input type="email" id="em" name="email">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["label_text"] == "Email Address"

    async def test_label_resolution_via_aria_label(self):
        html = """
        <form>
            <input type="search" name="q" aria-label="Search the site">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["label_text"] == "Search the site"

    async def test_label_resolution_via_placeholder_fallback(self):
        html = """
        <form>
            <input type="text" name="city" placeholder="Enter your city">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["label_text"] == "Enter your city"
        assert field["placeholder"] == "Enter your city"

    async def test_hidden_and_submit_inputs_excluded(self):
        """Hidden inputs and submit buttons must NOT appear in fields list."""
        html = """
        <form>
            <input type="hidden" name="csrf" value="secret-token">
            <input type="text" name="name" placeholder="Name">
            <input type="submit" value="Go">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        fields = result["forms"][0]["fields"]
        field_names = [f["name"] for f in fields]
        assert "csrf" not in field_names
        assert "name" in field_names
        assert len(fields) == 1

    async def test_select_options_extracted(self):
        html = """
        <form>
            <label for="lang">Language</label>
            <select id="lang" name="language">
                <option>English</option>
                <option>French</option>
                <option>Spanish</option>
            </select>
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["input_type"] == "select"
        assert "English" in field["options"]
        assert "French" in field["options"]
        assert len(field["options"]) == 3

    async def test_required_and_maxlength_extracted(self):
        html = """
        <form>
            <input type="text" name="username" required minlength="3" maxlength="20">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["is_required"] is True
        assert field["min_length"] == 3
        assert field["max_length"] == 20

    async def test_multiple_forms_on_page(self):
        html = """
        <html><body>
        <form id="search-form" method="get" action="/search">
            <input type="search" name="q" placeholder="Search">
            <input type="submit" value="Go">
        </form>
        <form id="newsletter" method="post" action="/subscribe">
            <input type="email" name="email" placeholder="Email">
            <input type="submit" value="Subscribe">
        </form>
        </body></html>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        assert result["forms_count"] == 2
        form_ids = [f["form_id"] for f in result["forms"]]
        assert "search-form" in form_ids
        assert "newsletter" in form_ids

    async def test_page_with_no_forms(self):
        html = """
        <html><body>
        <h1>Welcome</h1>
        <p>No forms here.</p>
        </body></html>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        assert result["success"] is True
        assert result["forms_count"] == 0

    async def test_textarea_extracted(self):
        html = """
        <form>
            <label for="msg">Message</label>
            <textarea id="msg" name="message" maxlength="500"></textarea>
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        field = result["forms"][0]["fields"][0]
        assert field["input_type"] == "textarea"
        assert field["label_text"] == "Message"
        assert field["max_length"] == 500

    async def test_readonly_disabled_fields(self):
        html = """
        <form>
            <input type="text" name="fixed" readonly>
            <input type="text" name="off" disabled>
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        fields = {f["name"]: f for f in result["forms"][0]["fields"]}
        assert fields["fixed"]["is_readonly"] is True
        assert fields["off"]["is_readonly"] is True


# ---------------------------------------------------------------------------
# InternetAgent.inspect_page_state() integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInspectPageState:
    """Integration tests for InternetAgent.inspect_page_state()."""

    def _make_agent_with_snapshot(self, html_content: str, url: str = "https://example.com"):
        from app.internet.agent import InternetAgent
        from app.browser.engine import BrowserEngine
        from app.browser.models import BrowserSnapshot, BrowserState, BrowserStatus

        agent = InternetAgent()
        # Use an isolated engine so the module-level singleton is never touched.
        isolated_engine = BrowserEngine()
        agent.browser_engine = isolated_engine
        # Also wire up services that hold an engine reference.
        agent.navigation_service._engine = isolated_engine
        agent.element_service._engine = isolated_engine

        sid = "ia-test"
        snap = BrowserSnapshot(
            url=url,
            title="Test Page",
            text_content="Test visible content",
            headings=["Test Page"],
            links=[{"href": "/about", "text": "About"}],
            interactive_elements_count=3,
            raw_html_truncated=html_content,
        )
        state = BrowserState(
            session_id=sid,
            current_url=url,
            page_title="Test Page",
            browser_status=BrowserStatus.ACTIVE,
            last_snapshot=snap,
        )
        isolated_engine._sessions[sid] = state
        isolated_engine._active_session_id = sid
        return agent, sid

    async def test_no_active_session_returns_failure(self):
        from app.internet.agent import InternetAgent
        from app.browser.engine import BrowserEngine

        agent = InternetAgent()
        # Use a completely fresh engine with no sessions so the test is
        # deterministic regardless of what other tests ran first.
        agent.browser_engine = BrowserEngine()
        result = await agent.inspect_page_state()
        assert result["success"] is False
        assert "Navigate" in result["error"]

    async def test_basic_page_state_returned(self):
        html = "<html><body><form><input type='text' name='q'></form></body></html>"
        agent, sid = self._make_agent_with_snapshot(html)
        result = await agent.inspect_page_state(session_id=sid)

        assert result["success"] is True
        assert result["forms_count"] == 1

        ps = result["page_state"]
        assert ps["url"] == "https://example.com"
        assert ps["title"] == "Test Page"
        assert "Test visible content" in ps["text_content"]
        assert ps["forms_count"] == 1

    async def test_password_form_flag(self):
        html = """
        <form id="login">
            <input type="text" name="user">
            <input type="password" name="pwd">
        </form>
        """
        agent, sid = self._make_agent_with_snapshot(html)
        result = await agent.inspect_page_state(session_id=sid)

        assert result["success"] is True
        assert result["has_password_form"] is True

    async def test_no_forms_on_page(self):
        html = "<html><body><h1>Hello</h1></body></html>"
        agent, sid = self._make_agent_with_snapshot(html)
        result = await agent.inspect_page_state(session_id=sid)

        assert result["success"] is True
        assert result["forms_count"] == 0
        assert result["has_password_form"] is False

    async def test_perception_skipped_by_default(self):
        """include_perception=False (default) should never call perceive_page."""
        html = "<form><input type='text' name='x'></form>"
        agent, sid = self._make_agent_with_snapshot(html)

        with patch.object(agent, "perceive_page") as mock_perceive:
            result = await agent.inspect_page_state(session_id=sid, include_perception=False)
            mock_perceive.assert_not_called()

        assert result["success"] is True

    async def test_page_state_links_preserved(self):
        html = "<html><body></body></html>"
        agent, sid = self._make_agent_with_snapshot(html)
        result = await agent.inspect_page_state(session_id=sid)

        ps = result["page_state"]
        assert any(lnk["href"] == "/about" for lnk in ps["links"])

    async def test_duration_ms_present(self):
        html = "<form><input type='email' name='e'></form>"
        agent, sid = self._make_agent_with_snapshot(html)
        result = await agent.inspect_page_state(session_id=sid)

        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], float)
        assert result["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Security invariant tests
# ---------------------------------------------------------------------------


class TestPasswordSafetyInvariants:
    """Dedicated tests ensuring password values never leak into models."""

    def test_form_field_info_has_no_value_field(self):
        from app.internet.models import FormFieldInfo

        fields = FormFieldInfo.model_fields
        assert "value" not in fields, (
            "SECURITY VIOLATION: FormFieldInfo must NOT have a 'value' field"
        )

    def test_web_page_state_no_raw_html(self):
        from app.internet.models import WebPageState

        fields = WebPageState.model_fields
        assert "raw_html" not in fields
        assert "raw_html_truncated" not in fields
        assert "screenshot_b64" not in fields

    @pytest.mark.asyncio
    async def test_extract_forms_no_password_value(self):
        html = """
        <form>
            <input type="password" name="secret" value="super-secret-123">
        </form>
        """
        engine, sid = _make_engine_with_html(html)
        result = await engine.extract_forms(session_id=sid)

        form = result["forms"][0]
        field = form["fields"][0]

        assert field["is_password"] is True

        field_str = str(field)
        assert "super-secret-123" not in field_str, (
            "SECURITY VIOLATION: password value leaked into FormFieldInfo"
        )

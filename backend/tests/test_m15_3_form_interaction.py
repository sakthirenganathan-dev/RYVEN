"""RYVEN 3.0 — M15.3 Phase 3: Advanced Form Interaction Tests.

Comprehensive tests for:
- FieldTargetResolver: deterministic priority matching (id > name > label > placeholder > substring)
- FormInteractionService: safe, validated, observable form field operations:
  * Text / email / number / url / search / textarea inputs
  * Select dropdown options by value or label
  * Checkbox toggle, check, and uncheck
  * Radio button group selection
  * Form submit confirmation gating (confirmed=False vs confirmed=True)
  * Password and credential protection (hard rejection of password fields, sensitive names)
  * Readonly and disabled field rejection
  * Length constraint validation (min_length, max_length)
  * Observable ActionEventBus emission with secret redaction
- InternetAgent form interaction facade methods
- Registered Tools in ToolRegistry (FormFieldFillTool, FormOptionSelectTool, etc.)

All tests execute in-memory with zero network dependencies.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine
from app.internet.agent import InternetAgent, internet_agent
from app.internet.form_interaction_service import (
    FieldTargetResolver,
    FormInteractionResult,
    FormInteractionService,
    _is_sensitive_name,
    _redact_for_log,
)
from app.internet.models import FormFieldInfo, WebFormInfo
from app.internet.tools import (
    FormCheckboxToggleTool,
    FormFieldFillTool,
    FormOptionSelectTool,
    FormRadioSelectTool,
    FormSubmitTool,
)
from app.tools.registry import create_default_registry


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def make_sample_form() -> WebFormInfo:
    """Create a sample multi-field form for testing."""
    return WebFormInfo(
        form_id="test-form",
        form_name="sample_form",
        action="/submit",
        method="post",
        fields=[
            FormFieldInfo(
                field_id="user-email",
                name="email",
                input_type="email",
                label_text="Email Address",
                placeholder="name@example.com",
                is_required=True,
                min_length=5,
                max_length=50,
            ),
            FormFieldInfo(
                field_id="user-name",
                name="full_name",
                input_type="text",
                label_text="Full Name",
                placeholder="John Doe",
                is_required=True,
            ),
            FormFieldInfo(
                field_id="user-bio",
                name="bio",
                input_type="textarea",
                label_text="About You",
                placeholder="Tell us about yourself",
            ),
            FormFieldInfo(
                field_id="country-select",
                name="country",
                input_type="select",
                label_text="Country",
                options=["United States", "Canada", "Germany", "Japan", "India"],
            ),
            FormFieldInfo(
                field_id="newsletter-sub",
                name="newsletter",
                input_type="checkbox",
                label_text="Subscribe to newsletter",
            ),
            FormFieldInfo(
                field_id="theme-dark",
                name="theme",
                input_type="radio",
                label_text="Dark Mode",
                options=["dark", "light", "system"],
            ),
            FormFieldInfo(
                field_id="user-pwd",
                name="password",
                input_type="password",
                label_text="Account Password",
                is_password=True,
            ),
            FormFieldInfo(
                field_id="secret-token-input",
                name="api_key",
                input_type="text",
                label_text="API Key Token",
            ),
            FormFieldInfo(
                field_id="locked-field",
                name="system_id",
                input_type="text",
                label_text="System Identifier",
                is_readonly=True,
            ),
        ],
        submit_labels=["Sign In", "Submit Application"],
    )


def make_mock_engine() -> MagicMock:
    """Create a mock BrowserEngine with successful async interaction stubs."""
    engine = MagicMock(spec=BrowserEngine)
    engine.type_text = AsyncMock(return_value={"success": True, "message": "Typed text"})
    engine.click_element = AsyncMock(return_value={"success": True, "message": "Clicked element"})
    return engine


# ---------------------------------------------------------------------------
# 1. FieldTargetResolver Tests
# ---------------------------------------------------------------------------


class TestFieldTargetResolver:
    """Tests deterministic resolution of field targets across forms."""

    def setup_method(self):
        self.form = make_sample_form()

    def test_resolve_by_exact_id(self):
        field = FieldTargetResolver.resolve("user-email", self.form.fields)
        assert field is not None
        assert field.field_id == "user-email"
        assert field.name == "email"

    def test_resolve_by_exact_name(self):
        field = FieldTargetResolver.resolve("full_name", self.form.fields)
        assert field is not None
        assert field.name == "full_name"
        assert field.label_text == "Full Name"

    def test_resolve_by_case_insensitive_label(self):
        field = FieldTargetResolver.resolve("email address", self.form.fields)
        assert field is not None
        assert field.field_id == "user-email"

    def test_resolve_by_case_insensitive_placeholder(self):
        field = FieldTargetResolver.resolve("john doe", self.form.fields)
        assert field is not None
        assert field.name == "full_name"

    def test_resolve_by_name_substring(self):
        field = FieldTargetResolver.resolve("bio", self.form.fields)
        assert field is not None
        assert field.name == "bio"

    def test_resolve_nonexistent_returns_none(self):
        field = FieldTargetResolver.resolve("non_existent_xyz", self.form.fields)
        assert field is None

    def test_resolve_all_matches_returns_candidates(self):
        matches = FieldTargetResolver.resolve_all_matches("user", self.form.fields)
        # user-email, user-name, user-bio, user-pwd
        assert len(matches) >= 3
        ids = [m.field_id for m in matches]
        assert "user-email" in ids
        assert "user-name" in ids

    def test_sensitive_name_detection(self):
        assert _is_sensitive_name("password") is True
        assert _is_sensitive_name("user_token") is True
        assert _is_sensitive_name("client_secret") is True
        assert _is_sensitive_name("apiKey") is True
        assert _is_sensitive_name("auth_bearer") is True
        assert _is_sensitive_name("username") is False
        assert _is_sensitive_name("email") is False
        assert _is_sensitive_name("full_name") is False

    def test_redact_for_log(self):
        redacted = _redact_for_log("secret_token_123456")
        assert isinstance(redacted, str)


# ---------------------------------------------------------------------------
# 2. FormInteractionService: Text & Textarea Fields
# ---------------------------------------------------------------------------


class TestFormInteractionServiceText:
    """Tests for fill_text_field across supported text/textarea inputs."""

    @pytest.mark.asyncio
    async def test_fill_text_field_success(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.fill_text_field(
            target="Full Name",
            value="Alice Smith",
            form=form,
        )

        assert res["success"] is True
        assert res["verified"] is True
        assert res["action"] == "fill_text_field"
        assert res["field_identity"]["name"] == "full_name"
        mock_engine.type_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fill_textarea_success(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.fill_text_field(
            target="About You",
            value="Software engineer building autonomous systems.",
            form=form,
        )

        assert res["success"] is True
        assert res["field_identity"]["input_type"] == "textarea"

    @pytest.mark.asyncio
    async def test_fill_text_field_min_length_validation(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        # email min_length=5, supply 3 chars
        res = await service.fill_text_field(
            target="email",
            value="a@b",
            form=form,
        )

        assert res["success"] is False
        assert "minlength" in res["error"].lower()
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_fill_text_field_max_length_validation(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        # email max_length=50, supply 60 chars
        res = await service.fill_text_field(
            target="email",
            value="a" * 55 + "@example.com",
            form=form,
        )

        assert res["success"] is False
        assert "maxlength" in res["error"].lower()
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_fill_readonly_field_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.fill_text_field(
            target="locked-field",
            value="new-id",
            form=form,
        )

        assert res["success"] is False
        assert "disabled/readonly" in res["error"].lower()
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_fill_empty_target_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)

        res = await service.fill_text_field(
            target="",
            value="Some Value",
        )

        assert res["success"] is False
        assert "empty" in res["error"].lower()

    @pytest.mark.asyncio
    async def test_fill_field_not_in_form_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.fill_text_field(
            target="nonexistent_field_123",
            value="Some Value",
            form=form,
        )

        assert res["success"] is False
        assert "not found" in res["error"].lower()


# ---------------------------------------------------------------------------
# 3. Security: Password & Sensitive Field Hard Rejection
# ---------------------------------------------------------------------------


class TestPasswordAndCredentialProtection:
    """Security tests: password and credential values are strictly rejected."""

    @pytest.mark.asyncio
    async def test_password_field_hard_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.fill_text_field(
            target="Account Password",
            value="SuperSecretP@ssword123",
            form=form,
        )

        assert res["success"] is False
        assert "security" in res["error"].lower()
        assert "password" in res["error"].lower()
        # Ensure BrowserEngine was NEVER invoked
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_sensitive_name_field_hard_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        # field with name="api_key"
        res = await service.fill_text_field(
            target="api_key",
            value="sk-ant-123456789",
            form=form,
        )

        assert res["success"] is False
        assert "security" in res["error"].lower()
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_event_never_contains_password_in_metadata(self):
        events_emitted = []

        def capture(ev: ActionEvent):
            events_emitted.append(ev)

        sid = action_bus.subscribe(capture)
        try:
            mock_engine = make_mock_engine()
            service = FormInteractionService(engine=mock_engine)
            form = make_sample_form()

            await service.fill_text_field(
                target="Account Password",
                value="P@ss123456",
                form=form,
            )

            # Check that no emitted event contains the password value
            for ev in events_emitted:
                ev_str = str(ev.model_dump())
                assert "P@ss123456" not in ev_str
        finally:
            action_bus.unsubscribe(sid)


# ---------------------------------------------------------------------------
# 4. FormInteractionService: Select Elements
# ---------------------------------------------------------------------------


class TestFormInteractionServiceSelect:
    """Tests for select_option dropdown interactions."""

    @pytest.mark.asyncio
    async def test_select_valid_option_exact_match(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.select_option(
            target="country",
            option="Canada",
            form=form,
        )

        assert res["success"] is True
        assert res["verified"] is True
        assert res["safe_metadata"]["selected_option"] == "Canada"
        mock_engine.click_element.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_option_case_insensitive(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.select_option(
            target="country",
            option="germany",
            form=form,
        )

        assert res["success"] is True
        assert res["safe_metadata"]["selected_option"] == "Germany"

    @pytest.mark.asyncio
    async def test_select_invalid_option_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.select_option(
            target="country",
            option="Australia",
            form=form,
        )

        assert res["success"] is False
        assert "not in the allowed values" in res["error"].lower()
        mock_engine.click_element.assert_not_called()


# ---------------------------------------------------------------------------
# 5. FormInteractionService: Checkbox Elements
# ---------------------------------------------------------------------------


class TestFormInteractionServiceCheckbox:
    """Tests for toggle_checkbox interactions."""

    @pytest.mark.asyncio
    async def test_toggle_checkbox_default(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.toggle_checkbox(
            target="Subscribe to newsletter",
            checked=None,
            form=form,
        )

        assert res["success"] is True
        assert res["safe_metadata"]["desired_state"] == "toggle"
        mock_engine.click_element.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_checkbox_explicit(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.toggle_checkbox(
            target="newsletter",
            checked=True,
            form=form,
        )

        assert res["success"] is True
        assert res["safe_metadata"]["desired_state"] == "check"

    @pytest.mark.asyncio
    async def test_uncheck_checkbox_explicit(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.toggle_checkbox(
            target="newsletter",
            checked=False,
            form=form,
        )

        assert res["success"] is True
        assert res["safe_metadata"]["desired_state"] == "uncheck"


# ---------------------------------------------------------------------------
# 6. FormInteractionService: Radio Elements
# ---------------------------------------------------------------------------


class TestFormInteractionServiceRadio:
    """Tests for select_radio option selection."""

    @pytest.mark.asyncio
    async def test_select_valid_radio(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.select_radio(
            target="Dark Mode",
            value="dark",
            form=form,
        )

        assert res["success"] is True
        assert res["verified"] is True
        assert res["safe_metadata"]["radio_value"] == "dark"
        mock_engine.click_element.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_select_invalid_radio_rejected(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.select_radio(
            target="theme",
            value="neon_purple",
            form=form,
        )

        assert res["success"] is False
        assert "not in known options" in res["error"].lower()
        mock_engine.click_element.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Form Submission: Confirmation Gating
# ---------------------------------------------------------------------------


class TestFormSubmissionConfirmation:
    """Tests strict confirmation gating for form submission."""

    @pytest.mark.asyncio
    async def test_submit_without_confirmation_returns_pending_token(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.submit_form(
            form=form,
            confirmed=False,
        )

        assert res["success"] is False
        assert res["requires_confirmation"] is True
        assert res["confirmation_token"] is not None
        assert res["confirmation_token"].startswith("submit-")
        assert "CONFIRMATION REQUIRED" in res["message"]
        # Must NOT have submitted via engine
        mock_engine.click_element.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_with_confirmation_proceeds(self):
        mock_engine = make_mock_engine()
        service = FormInteractionService(engine=mock_engine)
        form = make_sample_form()

        res = await service.submit_form(
            form=form,
            confirmed=True,
            submit_label="input[type='submit']",
        )

        assert res["success"] is True
        assert res["verified"] is True
        assert "submitted successfully" in res["message"].lower()
        mock_engine.click_element.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submit_tool_requires_confirmation_flag(self):
        tool = FormSubmitTool()
        assert tool.requires_confirmation is True
        assert tool.name == "form_submit"


# ---------------------------------------------------------------------------
# 8. Ambiguous Target Resolution
# ---------------------------------------------------------------------------


class TestTargetDisambiguation:
    """Tests resolve_target candidate listing and ambiguity detection."""

    def test_disambiguation_multi_match(self):
        service = FormInteractionService()
        form = make_sample_form()

        res = service.resolve_target("user", form)
        assert res["ambiguous"] is True
        assert res["count"] >= 3
        assert len(res["matches"]) == res["count"]

    def test_disambiguation_single_match(self):
        service = FormInteractionService()
        form = make_sample_form()

        res = service.resolve_target("newsletter", form)
        assert res["ambiguous"] is False
        assert res["count"] == 1


# ---------------------------------------------------------------------------
# 9. InternetAgent Integration
# ---------------------------------------------------------------------------


class TestInternetAgentFormIntegration:
    """Tests InternetAgent delegation to FormInteractionService."""

    @pytest.mark.asyncio
    async def test_agent_has_form_service(self):
        assert hasattr(internet_agent, "form_service")
        assert isinstance(internet_agent.form_service, FormInteractionService)

    @pytest.mark.asyncio
    async def test_agent_fill_form_field(self):
        form = make_sample_form()
        with patch.object(internet_agent.form_service, "fill_text_field", new_callable=AsyncMock) as mock_fill:
            mock_fill.return_value = {"success": True, "action": "fill_text_field"}
            res = await internet_agent.fill_form_field("Full Name", "Bob", form=form)
            assert res["success"] is True
            mock_fill.assert_awaited_once_with(
                target="Full Name",
                value="Bob",
                form=form,
                session_id=None,
            )

    @pytest.mark.asyncio
    async def test_agent_select_form_option(self):
        form = make_sample_form()
        with patch.object(internet_agent.form_service, "select_option", new_callable=AsyncMock) as mock_sel:
            mock_sel.return_value = {"success": True, "action": "select_option"}
            res = await internet_agent.select_form_option("country", "Canada", form=form)
            assert res["success"] is True
            mock_sel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_toggle_form_checkbox(self):
        form = make_sample_form()
        with patch.object(internet_agent.form_service, "toggle_checkbox", new_callable=AsyncMock) as mock_chk:
            mock_chk.return_value = {"success": True, "action": "toggle_checkbox"}
            res = await internet_agent.toggle_form_checkbox("newsletter", checked=True, form=form)
            assert res["success"] is True
            mock_chk.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_select_form_radio(self):
        form = make_sample_form()
        with patch.object(internet_agent.form_service, "select_radio", new_callable=AsyncMock) as mock_rad:
            mock_rad.return_value = {"success": True, "action": "select_radio"}
            res = await internet_agent.select_form_radio("theme", "dark", form=form)
            assert res["success"] is True
            mock_rad.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_submit_form(self):
        form = make_sample_form()
        with patch.object(internet_agent.form_service, "submit_form", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = {"success": True, "action": "submit_form"}
            res = await internet_agent.submit_form(form=form, confirmed=True)
            assert res["success"] is True
            mock_sub.assert_awaited_once()

    def test_agent_resolve_form_target(self):
        form = make_sample_form()
        res = internet_agent.resolve_form_target("email", form)
        assert res["count"] >= 1


# ---------------------------------------------------------------------------
# 10. Registered Tools in ToolRegistry
# ---------------------------------------------------------------------------


class TestRegisteredFormTools:
    """Verifies that all 5 form tools are registered in create_default_registry."""

    def test_all_form_tools_in_registry(self):
        registry = create_default_registry()
        tool_names = registry.list_tools()

        assert "form_field_fill" in tool_names
        assert "form_option_select" in tool_names
        assert "form_checkbox_toggle" in tool_names
        assert "form_radio_select" in tool_names
        assert "form_submit" in tool_names

    @pytest.mark.asyncio
    async def test_form_field_fill_tool_execute(self):
        tool = FormFieldFillTool()
        with patch.object(internet_agent, "fill_form_field", new_callable=AsyncMock) as mock_fill:
            mock_fill.return_value = {"success": True, "action": "fill_text_field"}
            res = await tool.execute(target="Name", value="Jane Doe")
            assert res["success"] is True
            mock_fill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_form_option_select_tool_execute(self):
        tool = FormOptionSelectTool()
        with patch.object(internet_agent, "select_form_option", new_callable=AsyncMock) as mock_sel:
            mock_sel.return_value = {"success": True, "action": "select_option"}
            res = await tool.execute(target="country", option="Japan")
            assert res["success"] is True
            mock_sel.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_form_checkbox_toggle_tool_execute(self):
        tool = FormCheckboxToggleTool()
        with patch.object(internet_agent, "toggle_form_checkbox", new_callable=AsyncMock) as mock_chk:
            mock_chk.return_value = {"success": True, "action": "toggle_checkbox"}
            res = await tool.execute(target="terms", checked=True)
            assert res["success"] is True
            mock_chk.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_form_radio_select_tool_execute(self):
        tool = FormRadioSelectTool()
        with patch.object(internet_agent, "select_form_radio", new_callable=AsyncMock) as mock_rad:
            mock_rad.return_value = {"success": True, "action": "select_radio"}
            res = await tool.execute(target="shipping", value="express")
            assert res["success"] is True
            mock_rad.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_form_submit_tool_execute(self):
        tool = FormSubmitTool()
        with patch.object(internet_agent, "submit_form", new_callable=AsyncMock) as mock_sub:
            mock_sub.return_value = {"success": True, "action": "submit_form"}
            res = await tool.execute(form_id="checkout", confirmed=True)
            assert res["success"] is True
            mock_sub.assert_awaited_once_with(
                form_id="checkout",
                submit_label=None,
                confirmed=True,
                session_id=None,
            )

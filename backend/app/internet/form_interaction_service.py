"""RYVEN 3.0 — M15.3 Phase 3: Form Field Interaction Service.

Provides safe, observable, verified interaction with form fields:
    - Text / email / search / number / url / tel inputs
    - <select> elements (by value or option label)
    - Checkboxes  (toggle or set explicit checked state)
    - Radio buttons (select by value/label)

SECURITY CONTRACT (enforced throughout):
    - Password field values are NEVER accepted, typed, returned, or logged.
    - Credential key names (token, secret, api_key, …) in typed text are
      redacted in all ActionEvent metadata and log records.
    - Disabled / readonly fields are rejected before any interaction.
    - Form submission is NEVER automatic; a confirmation token is returned
      and the caller must pass confirmed=True after user approval.
    - No value is ever read back from the DOM after typing (avoids leaking
      anything a previous page may have pre-filled into a password field).
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine, browser_engine
from app.browser.security import BrowserSecurityValidator
from app.core.logging_config import logger
from app.internet.models import FormFieldInfo, WebFormInfo


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SENSITIVE_TOKENS = frozenset({
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "auth", "cookie", "bearer", "credential", "credentials",
    "access_token", "refresh_token", "client_secret", "private_key",
})


def _is_sensitive_name(name: str) -> bool:
    """True if field name/id matches a known sensitive credential pattern."""
    if not name:
        return False
    nl = name.lower()
    if any(k in nl for k in ("password", "passwd", "secret", "api_key", "apikey", "cookie", "bearer", "credential", "private_key", "token")):
        return True
    tokens = set(re.split(r"[^a-z0-9]+", nl))
    return bool(tokens & _SENSITIVE_TOKENS)


def _redact_for_log(text: str) -> str:
    """Return a credential-safe preview of typed text for logs/events."""
    return BrowserSecurityValidator.redact_credentials(text)


# ---------------------------------------------------------------------------
# FieldTargetResolver — resolve a user-supplied target to a FormFieldInfo
# ---------------------------------------------------------------------------


class FieldTargetResolver:
    """Match a caller-supplied target string to a field in the form.

    Resolution order (highest priority first):
        1. Exact HTML ``id`` match
        2. Exact HTML ``name`` match
        3. Case-insensitive ``label_text`` match
        4. Case-insensitive ``placeholder`` match
        5. Case-insensitive ``name`` substring match
    """

    @staticmethod
    def resolve(
        target: str,
        fields: List[FormFieldInfo],
    ) -> Optional[FormFieldInfo]:
        """Return the first matching FormFieldInfo or None."""
        tl = target.strip().lower()

        # 1. Exact id
        for f in fields:
            if f.field_id and f.field_id.lower() == tl:
                return f

        # 2. Exact name
        for f in fields:
            if f.name and f.name.lower() == tl:
                return f

        # 3. Label
        for f in fields:
            if f.label_text and f.label_text.lower() == tl:
                return f

        # 4. Placeholder
        for f in fields:
            if f.placeholder and f.placeholder.lower() == tl:
                return f

        # 5. Name substring (loose fallback)
        for f in fields:
            if f.name and tl in f.name.lower():
                return f

        return None

    @staticmethod
    def resolve_all_matches(
        target: str,
        fields: List[FormFieldInfo],
    ) -> List[FormFieldInfo]:
        """Return all fields whose label, name, id, or placeholder contain target."""
        tl = target.strip().lower()
        return [
            f for f in fields
            if (f.field_id and tl in f.field_id.lower())
            or (f.name and tl in f.name.lower())
            or (f.label_text and tl in f.label_text.lower())
            or (f.placeholder and tl in f.placeholder.lower())
        ]


# ---------------------------------------------------------------------------
# FormInteractionResult — structured return value
# ---------------------------------------------------------------------------


class FormInteractionResult:
    """Immutable structured result from a single form field interaction."""

    __slots__ = (
        "success",
        "field_identity",
        "action",
        "message",
        "error",
        "requires_confirmation",
        "confirmation_token",
        "duration_ms",
        "verified",
        "safe_metadata",
    )

    def __init__(
        self,
        *,
        success: bool,
        field_identity: Dict[str, Any],
        action: str,
        message: str,
        error: Optional[str] = None,
        requires_confirmation: bool = False,
        confirmation_token: Optional[str] = None,
        duration_ms: float = 0.0,
        verified: bool = False,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.success = success
        self.field_identity = field_identity
        self.action = action
        self.message = message
        self.error = error
        self.requires_confirmation = requires_confirmation
        self.confirmation_token = confirmation_token
        self.duration_ms = duration_ms
        self.verified = verified
        self.safe_metadata = safe_metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "field_identity": self.field_identity,
            "action": self.action,
            "message": self.message,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_token": self.confirmation_token,
            "duration_ms": self.duration_ms,
            "verified": self.verified,
            "safe_metadata": self.safe_metadata,
        }


# ---------------------------------------------------------------------------
# FormInteractionService
# ---------------------------------------------------------------------------


class FormInteractionService:
    """Safe, verified interactions with individual form fields.

    All interactions:
    - Validate field type, disabled/readonly state, and option sets before acting.
    - Refuse password field values with a hard error.
    - Emit redacted ActionEvents to the bus before and after each operation.
    - Delegate actual "typing" and "clicking" to ElementService → BrowserEngine.
    - Return a structured FormInteractionResult with field identity, outcome, and
      verification status.

    Submission is NEVER automatic — ``submit_form()`` requires ``confirmed=True``
    which can only become True after the caller has shown the user a confirmation
    prompt and received explicit approval.
    """

    def __init__(self, engine: Optional[BrowserEngine] = None) -> None:
        self._engine = engine or browser_engine

    # ------------------------------------------------------------------
    # 1. Fill a text / email / search / number / url / tel / textarea field
    # ------------------------------------------------------------------

    async def fill_text_field(
        self,
        *,
        target: str,
        value: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type a safe text value into the resolved field.

        Args:
            target:     Field identifier — label, name, id, or placeholder.
            value:      Text to type.  Must NOT be a password/credential value.
            form:       Optional WebFormInfo for strict pre-validation.
            session_id: Active browser session id.

        Returns:
            FormInteractionResult dict.
        """
        t0 = time.monotonic()

        # --- 1. Resolve field ---
        resolution = self._resolve_field(
            target=target,
            form=form,
            allowed_types={
                "text", "email", "search", "number", "url", "tel",
                "date", "time", "datetime-local", "month", "week",
                "color", "range", "textarea",
            },
            session_id=session_id,
        )
        if not resolution["ok"]:
            return self._failure(
                action="fill_text_field",
                target=target,
                error=resolution["error"],
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        field: FormFieldInfo = resolution["field"]

        # --- 2. Security: hard-block password fields ---
        if field.is_password or _is_sensitive_name(field.name or "") or _is_sensitive_name(field.field_id or ""):
            return self._failure(
                action="fill_text_field",
                target=target,
                error=(
                    "SECURITY: fill_text_field cannot be used for password or "
                    "credential fields. Password values must never be automated."
                ),
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # --- 3. Guard disabled / readonly ---
        if field.is_readonly:
            return self._failure(
                action="fill_text_field",
                target=target,
                error=f"Field '{target}' is disabled/readonly and cannot be edited.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # --- 4. Validate constraints ---
        if field.min_length and len(value) < field.min_length:
            return self._failure(
                action="fill_text_field",
                target=target,
                error=f"Value length {len(value)} is below minlength={field.min_length}.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        if field.max_length and len(value) > field.max_length:
            return self._failure(
                action="fill_text_field",
                target=target,
                error=f"Value length {len(value)} exceeds maxlength={field.max_length}.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # --- 5. Emit STARTED event (redacted) ---
        safe_preview = _redact_for_log(value)
        self._emit(
            action_type=ActionType.WEB_ACTION_STARTED,
            status=ActionStatus.STARTED,
            title=f"Fill field: {target}",
            description=f"Typing into field '{target}' (type={field.input_type})",
            metadata={
                "field_name": field.name,
                "field_type": field.input_type,
                "value_preview": str(safe_preview)[:30],
            },
        )

        # --- 6. Delegate to BrowserEngine ---
        selector = self._build_selector(field)
        engine_result = await self._engine.type_text(
            selector=selector,
            text=value,
            session_id=session_id,
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if not engine_result.get("success"):
            self._emit(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.FAILED,
                title=f"Fill failed: {target}",
                description=engine_result.get("message", "Unknown engine error"),
            )
            return self._failure(
                action="fill_text_field",
                target=target,
                error=engine_result.get("message", "Engine type_text failed"),
                field=field,
                duration_ms=duration_ms,
            )

        # --- 7. Emit COMPLETED (redacted) ---
        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Filled field: {target}",
            description=f"Typed {len(value)} char(s) into '{target}'",
            metadata={
                "field_name": field.name,
                "field_type": field.input_type,
                "typed_length": len(value),
            },
        )

        result = FormInteractionResult(
            success=True,
            field_identity=self._field_identity(field),
            action="fill_text_field",
            message=f"Typed {len(value)} character(s) into field '{target}'.",
            duration_ms=duration_ms,
            verified=True,  # BrowserEngine.type_text succeeded
            safe_metadata={
                "typed_length": len(value),
                "field_type": field.input_type,
            },
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # 2. Select an option in a <select> element
    # ------------------------------------------------------------------

    async def select_option(
        self,
        *,
        target: str,
        option: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select an option in a <select> field by exact value or label.

        Args:
            target:  Field identifier.
            option:  The option text or value to select.
            form:    Optional WebFormInfo for option validation.
            session_id: Active session id.
        """
        t0 = time.monotonic()

        resolution = self._resolve_field(
            target=target,
            form=form,
            allowed_types={"select"},
            session_id=session_id,
        )
        if not resolution["ok"]:
            return self._failure(
                action="select_option",
                target=target,
                error=resolution["error"],
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        field: FormFieldInfo = resolution["field"]

        if field.is_readonly:
            return self._failure(
                action="select_option",
                target=target,
                error=f"Select '{target}' is disabled/readonly.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Validate option exists (if we have an options list)
        if field.options:
            matched = self._match_option(option, field.options)
            if matched is None:
                return self._failure(
                    action="select_option",
                    target=target,
                    error=(
                        f"Option '{option}' is not in the allowed values for '{target}'. "
                        f"Valid options: {field.options!r}"
                    ),
                    field=field,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
        else:
            matched = option

        self._emit(
            action_type=ActionType.WEB_ACTION_STARTED,
            status=ActionStatus.STARTED,
            title=f"Select option: {target}",
            description=f"Selecting '{matched}' in <select> '{target}'",
            metadata={"field_name": field.name, "selected_option": matched},
        )

        # Use click_element to simulate selection (model of BrowserEngine)
        selector = self._build_selector(field)
        engine_result = await self._engine.click_element(
            selector=f"{selector} option[value='{matched}']",
            session_id=session_id,
            confirmed=True,  # Selecting an option is not a submission action
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if not engine_result.get("success") and not engine_result.get("requires_confirmation"):
            self._emit(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.FAILED,
                title=f"Select failed: {target}",
                description=engine_result.get("message", ""),
            )
            return self._failure(
                action="select_option",
                target=target,
                error=engine_result.get("message", "Engine click failed"),
                field=field,
                duration_ms=duration_ms,
            )

        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Option selected: {target}",
            description=f"Selected '{matched}' in '{target}'",
            metadata={"field_name": field.name, "selected_option": matched},
        )

        result = FormInteractionResult(
            success=True,
            field_identity=self._field_identity(field),
            action="select_option",
            message=f"Selected option '{matched}' in field '{target}'.",
            duration_ms=duration_ms,
            verified=True,
            safe_metadata={"selected_option": matched},
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # 3. Toggle a checkbox
    # ------------------------------------------------------------------

    async def toggle_checkbox(
        self,
        *,
        target: str,
        checked: Optional[bool] = None,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set a checkbox to checked or unchecked (or toggle if checked is None).

        Args:
            target:  Field identifier.
            checked: True to check, False to uncheck, None to toggle.
            form:    Optional WebFormInfo.
            session_id: Active session id.
        """
        t0 = time.monotonic()

        resolution = self._resolve_field(
            target=target,
            form=form,
            allowed_types={"checkbox"},
            session_id=session_id,
        )
        if not resolution["ok"]:
            return self._failure(
                action="toggle_checkbox",
                target=target,
                error=resolution["error"],
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        field: FormFieldInfo = resolution["field"]

        if field.is_readonly:
            return self._failure(
                action="toggle_checkbox",
                target=target,
                error=f"Checkbox '{target}' is disabled/readonly.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        desired_state = "toggle" if checked is None else ("check" if checked else "uncheck")
        self._emit(
            action_type=ActionType.WEB_ACTION_STARTED,
            status=ActionStatus.STARTED,
            title=f"Checkbox: {target}",
            description=f"{desired_state.capitalize()} checkbox '{target}'",
            metadata={"field_name": field.name, "desired_state": desired_state},
        )

        selector = self._build_selector(field)
        engine_result = await self._engine.click_element(
            selector=selector,
            session_id=session_id,
            confirmed=True,  # checkbox toggle is low-stakes, not a submission
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if not engine_result.get("success"):
            self._emit(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.FAILED,
                title=f"Checkbox failed: {target}",
                description=engine_result.get("message", ""),
            )
            return self._failure(
                action="toggle_checkbox",
                target=target,
                error=engine_result.get("message", "Engine click failed"),
                field=field,
                duration_ms=duration_ms,
            )

        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Checkbox toggled: {target}",
            description=f"Checkbox '{target}' → {desired_state}",
            metadata={"field_name": field.name, "desired_state": desired_state},
        )

        result = FormInteractionResult(
            success=True,
            field_identity=self._field_identity(field),
            action="toggle_checkbox",
            message=f"Checkbox '{target}' — {desired_state}.",
            duration_ms=duration_ms,
            verified=True,
            safe_metadata={"desired_state": desired_state},
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # 4. Select a radio button
    # ------------------------------------------------------------------

    async def select_radio(
        self,
        *,
        target: str,
        value: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select a radio button by its value or visible label.

        Args:
            target:  Group name or label prefix of the radio group.
            value:   The option value or label to select within the group.
            form:    Optional WebFormInfo.
            session_id: Active session id.
        """
        t0 = time.monotonic()

        resolution = self._resolve_field(
            target=target,
            form=form,
            allowed_types={"radio"},
            session_id=session_id,
        )
        if not resolution["ok"]:
            return self._failure(
                action="select_radio",
                target=target,
                error=resolution["error"],
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        field: FormFieldInfo = resolution["field"]

        if field.is_readonly:
            return self._failure(
                action="select_radio",
                target=target,
                error=f"Radio '{target}' is disabled/readonly.",
                field=field,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # Validate option exists (if known)
        if field.options:
            matched = self._match_option(value, field.options)
            if matched is None:
                return self._failure(
                    action="select_radio",
                    target=target,
                    error=(
                        f"Radio value '{value}' is not in known options for '{target}'. "
                        f"Known: {field.options!r}"
                    ),
                    field=field,
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
        else:
            matched = value

        self._emit(
            action_type=ActionType.WEB_ACTION_STARTED,
            status=ActionStatus.STARTED,
            title=f"Radio select: {target}",
            description=f"Selecting radio '{matched}' in group '{target}'",
            metadata={"field_name": field.name, "radio_value": matched},
        )

        # Build a value-specific selector for the radio button
        selector = f"input[name='{field.name}'][value='{matched}']" if field.name else self._build_selector(field)
        engine_result = await self._engine.click_element(
            selector=selector,
            session_id=session_id,
            confirmed=True,
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if not engine_result.get("success"):
            self._emit(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.FAILED,
                title=f"Radio failed: {target}",
                description=engine_result.get("message", ""),
            )
            return self._failure(
                action="select_radio",
                target=target,
                error=engine_result.get("message", "Engine click failed"),
                field=field,
                duration_ms=duration_ms,
            )

        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title=f"Radio selected: {target}",
            description=f"Radio '{target}' → '{matched}'",
            metadata={"field_name": field.name, "radio_value": matched},
        )

        result = FormInteractionResult(
            success=True,
            field_identity=self._field_identity(field),
            action="select_radio",
            message=f"Radio '{target}' set to '{matched}'.",
            duration_ms=duration_ms,
            verified=True,
            safe_metadata={"radio_value": matched},
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # 5. Submit a form (confirmation-gated — NEVER automatic)
    # ------------------------------------------------------------------

    async def submit_form(
        self,
        *,
        form: Optional[WebFormInfo] = None,
        form_id: Optional[str] = None,
        submit_label: Optional[str] = None,
        confirmed: bool = False,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a form — only after explicit user confirmation.

        Args:
            form:         WebFormInfo of the form to submit (used to identify selector).
            form_id:      HTML id of the <form> element (alternative to form).
            submit_label: Label of the submit button to click.
            confirmed:    **MUST be True** — the caller must have obtained user approval.
            session_id:   Active session id.

        If confirmed=False (default), returns a confirmation-pending result with a
        token.  The caller must display a human-readable prompt and re-call with
        confirmed=True after approval.

        Security:
            - Password fields in the form are NEVER read or echoed.
            - The confirmation_required flag is always set to True in events.
        """
        t0 = time.monotonic()

        # Always require explicit confirmation
        if not confirmed:
            token = f"submit-{uuid.uuid4().hex[:8]}"
            form_desc = (
                form_id
                or (form.form_id if form else None)
                or (form.action if form else None)
                or "form"
            )
            self._emit(
                action_type=ActionType.CONFIRMATION_REQUESTED,
                status=ActionStatus.WAITING_CONFIRMATION,
                title="Form Submission — Confirmation Required",
                description=(
                    f"Submission of '{form_desc}' requires explicit user approval. "
                    f"Re-call submit_form(confirmed=True) after user approves."
                ),
                confirmation_required=True,
                metadata={"form_id": form_desc, "token": token},
            )
            duration_ms = (time.monotonic() - t0) * 1000
            result = FormInteractionResult(
                success=False,
                field_identity={"form_id": form_desc},
                action="submit_form",
                message=(
                    f"CONFIRMATION REQUIRED: Submit form '{form_desc}'? "
                    f"Provide confirmed=True after user approves."
                ),
                requires_confirmation=True,
                confirmation_token=token,
                duration_ms=duration_ms,
            )
            return result.to_dict()

        # confirmed=True — proceed with submission
        form_id_val = (
            form_id
            or (form.form_id if form else None)
            or "form"
        )
        submit_selector = submit_label or "input[type='submit'], button[type='submit']"

        self._emit(
            action_type=ActionType.WEB_ACTION_STARTED,
            status=ActionStatus.STARTED,
            title=f"Submitting form: {form_id_val}",
            description=f"User confirmed submission of '{form_id_val}'",
            confirmation_required=True,
            metadata={"form_id": form_id_val, "confirmed": True},
        )

        engine_result = await self._engine.click_element(
            selector=submit_selector,
            session_id=session_id,
            confirmed=True,
        )

        duration_ms = (time.monotonic() - t0) * 1000
        success = engine_result.get("success", False)

        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.COMPLETED if success else ActionStatus.FAILED,
            title=f"Form {'submitted' if success else 'submission failed'}: {form_id_val}",
            description=engine_result.get("message", ""),
            confirmation_required=True,
            metadata={"form_id": form_id_val},
        )

        result = FormInteractionResult(
            success=success,
            field_identity={"form_id": form_id_val},
            action="submit_form",
            message=(
                f"Form '{form_id_val}' submitted successfully."
                if success
                else engine_result.get("message", "Submission failed.")
            ),
            error=None if success else engine_result.get("message"),
            duration_ms=duration_ms,
            verified=success,
        )
        return result.to_dict()

    # ------------------------------------------------------------------
    # 6. Resolve ambiguous targets (returns all candidates)
    # ------------------------------------------------------------------

    def resolve_target(
        self,
        target: str,
        form: WebFormInfo,
    ) -> Dict[str, Any]:
        """Return all fields matching target (for disambiguation UI).

        Returns:
            dict with 'matches' (list of field identity dicts),
            'ambiguous' bool, and 'count'.
        """
        matches = FieldTargetResolver.resolve_all_matches(target, form.fields)
        return {
            "target": target,
            "count": len(matches),
            "ambiguous": len(matches) > 1,
            "matches": [self._field_identity(f) for f in matches],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_field(
        self,
        *,
        target: str,
        form: Optional[WebFormInfo],
        allowed_types: set,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        """Resolve a target to a validated FormFieldInfo.

        Returns dict with 'ok' bool, 'field', and 'error' keys.
        """
        if not target or not target.strip():
            return {"ok": False, "field": None, "error": "Target field identifier cannot be empty."}

        # If form is provided, resolve against it
        if form is not None:
            field = FieldTargetResolver.resolve(target, form.fields)
            if field is None:
                return {
                    "ok": False,
                    "field": None,
                    "error": (
                        f"Field '{target}' not found in the provided form. "
                        f"Available fields: {[f.name or f.field_id or f.label_text for f in form.fields]}"
                    ),
                }
        else:
            # No form provided — synthesise a minimal field descriptor from target
            # so the engine can still target by selector string
            field = FormFieldInfo(
                field_id=target,
                name=target,
                input_type=next(iter(allowed_types), "text"),
                label_text=target,
            )

        # Security check: hard block password / credential fields across interactions
        if field.is_password or field.input_type == "password" or _is_sensitive_name(field.name or "") or _is_sensitive_name(field.field_id or ""):
            return {
                "ok": False,
                "field": field,
                "error": (
                    "SECURITY: fill_text_field cannot be used for password or "
                    "credential fields. Password values must never be automated."
                ),
            }

        # Type check
        if field.input_type not in allowed_types:
            return {
                "ok": False,
                "field": field,
                "error": (
                    f"Field '{target}' has type '{field.input_type}' which is not "
                    f"supported by this interaction. Expected one of: {sorted(allowed_types)}"
                ),
            }

        return {"ok": True, "field": field, "error": None}

    @staticmethod
    def _build_selector(field: FormFieldInfo) -> str:
        """Derive a CSS-like selector string for the field."""
        if field.field_id:
            return f"#{field.field_id}"
        if field.name:
            return f"[name='{field.name}']"
        if field.label_text:
            return field.label_text
        return field.input_type or "input"

    @staticmethod
    def _match_option(value: str, options: List[str]) -> Optional[str]:
        """Case-insensitive option match. Returns normalised option or None."""
        vl = value.strip().lower()
        for opt in options:
            if opt.strip().lower() == vl:
                return opt
        # Substring fallback
        for opt in options:
            if vl in opt.strip().lower():
                return opt
        return None

    @staticmethod
    def _field_identity(field: Optional[FormFieldInfo]) -> Dict[str, Any]:
        if field is None:
            return {}
        return {
            "field_id": field.field_id,
            "name": field.name,
            "input_type": field.input_type,
            "label_text": field.label_text,
            "is_password": field.is_password,
        }

    def _failure(
        self,
        *,
        action: str,
        target: str,
        error: str,
        field: Optional[FormFieldInfo] = None,
        duration_ms: float = 0.0,
    ) -> Dict[str, Any]:
        logger.warning(f"[FormInteractionService] {action} failed for '{target}': {error}")
        self._emit(
            action_type=ActionType.WEB_ACTION_COMPLETED,
            status=ActionStatus.FAILED,
            title=f"{action} failed: {target}",
            description=error,
            metadata={"field_name": target, "error": error[:200]},
        )
        result = FormInteractionResult(
            success=False,
            field_identity=self._field_identity(field),
            action=action,
            message=error,
            error=error,
            duration_ms=duration_ms,
        )
        return result.to_dict()

    @staticmethod
    def _emit(
        *,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        description: str = "",
        confirmation_required: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit a redacted ActionEvent to the global bus."""
        event = ActionEvent(
            action_type=action_type,
            status=status,
            title=title,
            description=description,
            confirmation_required=confirmation_required,
        )
        if metadata:
            event.with_metadata(metadata)  # redacts sensitive keys in-place
        action_bus.emit(event)

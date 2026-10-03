"""
RYVEN 3.0 — Milestone 17.1 Desktop Action Engine.
Semantic desktop action execution pipeline.

Pipeline Flow:
    USER ACTION REQUEST
        ↓
    SECURITY & APPLICATION VALIDATION
        ↓
    DESKTOP TARGET RESOLUTION (DesktopTargetResolver)
        ↓
    TARGET BOUNDS & CONFIDENCE VERIFICATION
        ↓
    STALE TARGET PROTECTION (Window & UI Freshness)
        ↓
    CONFIRMATION GATE (ConfirmationManager for Consequential Actions)
        ↓
    WINDOW FOCUS (WindowsDesktopDriver.focus_window)
        ↓
    ACTION EXECUTION (WindowsDesktopDriver)
        ↓
    ACTION EVENT BUS TELEMETRY (Safe Metadata Only)
        ↓
    BOUNDED POST-ACTION OBSERVATION (ObserverEngine.observe_desktop)
        ↓
    STRUCTURED RESULT (DesktopActionResult)

Security Invariants:
- NEVER executes raw coordinates directly supplied by LLM for click actions.
- Target resolution via DesktopTargetResolver is mandatory.
- Win32 native WindowsDesktopDriver execution only (zero shell / cmd / powershell / subprocess).
- Strict approved application allowlist enforcement.
- Credential, password, token, and private key rejection.
- Zero secret, token, password, or base64 screenshot leakage in telemetry or logs.
- Fails closed on any ambiguity, stale target, out-of-bounds, or permission violation.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.models import (
    DesktopActionRequest,
    DesktopActionResult,
    DesktopActionType,
    DesktopTargetResolutionResult,
    DesktopUIElement,
    DesktopWindowState,
    FailureClass,
    ALLOWED_DESKTOP_KEYS,
)
# Observer and other dependencies will be lazy-loaded in __init__ if not provided
from app.control.permissions import (
    CapabilityPermissionManager,
    permission_manager,
)
from app.core.logging_config import logger
from app.desktop.interaction import (
    ALLOWED_EXECUTABLE_TO_APP,
    CANONICAL_APP_NAMES,
    WindowsDesktopDriver,
    desktop_driver,
)
from app.desktop.resolver import (
    DEFAULT_TARGET_CONFIDENCE,
    DesktopTargetResolver,
    desktop_target_resolver,
)
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.tools.process_tool import APPROVED_PROCESS_NAMES
from app.workflows.confirmation import ConfirmationManager

# ---------------------------------------------------------------------------
# Consequential Keywords for Safety & Confirmation Gates
# ---------------------------------------------------------------------------

CONSEQUENTIAL_TARGET_KEYWORDS: Set[str] = {
    "delete",
    "remove",
    "drop",
    "terminate",
    "kill",
    "format",
    "purchase",
    "buy",
    "pay",
    "payment",
    "checkout",
    "submit",
    "publish",
    "send",
    "transfer",
    "destroy",
    "wipe",
    "reset",
    "discard",
    "overwrite",
    "uninstall",
    "close",
}

FORBIDDEN_CREDENTIAL_PATTERNS = (
    "password=",
    "bearer ",
    "ssh-rsa",
    "-----begin ",
    "api_key",
    "secret=",
    "ghp_",
    "sk_live",
)


class DesktopActionEngine:
    """Safely executes semantic desktop actions against validated UI targets."""

    def __init__(
        self,
        driver: Optional[WindowsDesktopDriver] = None,
        resolver: Optional[DesktopTargetResolver] = None,
        observer: Optional[Any] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
        permission_mgr: Optional[CapabilityPermissionManager] = None,
        checkpoints: Optional[CheckpointStore] = None,
    ) -> None:
        self.driver = driver or desktop_driver
        self.resolver = resolver or desktop_target_resolver
        if observer is not None:
            self.observer = observer
        else:
            try:
                from app.control.observer import observer_engine
                self.observer = observer_engine
            except Exception:
                self.observer = None
        self.permission_mgr = permission_mgr or permission_manager
        self.confirmation_mgr = confirmation_mgr or (
            self.permission_mgr.confirmation_mgr
            if self.permission_mgr and hasattr(self.permission_mgr, "confirmation_mgr")
            else ConfirmationManager()
        )
        self.checkpoints = checkpoints or checkpoint_store

    # -----------------------------------------------------------------------
    # Helper / Validation Methods
    # -----------------------------------------------------------------------

    def _check_credentials(self, text: Optional[str]) -> None:
        """Reject text containing sensitive credentials or private key patterns."""
        if not text:
            return
        lowered = text.lower()
        for pat in FORBIDDEN_CREDENTIAL_PATTERNS:
            if pat in lowered:
                raise PermissionError(f"Desktop action rejected: Contains sensitive pattern '{pat}'")

    def _is_consequential_action(
        self,
        action: DesktopActionType,
        target: Optional[str] = None,
        text: Optional[str] = None,
    ) -> bool:
        """Evaluate whether a semantic action can cause consequential or irreversible mutation."""
        check_str = f"{target or ''} {text or ''}".lower()
        for kw in CONSEQUENTIAL_TARGET_KEYWORDS:
            if re.search(r"\b" + re.escape(kw) + r"\b", check_str):
                return True
        return False

    def _validate_application_name(self, application: Optional[str]) -> Optional[str]:
        """Validate application name against approved allowlist."""
        if not application:
            return None
        norm = application.strip().lower()
        for k, v in CANONICAL_APP_NAMES.items():
            if norm == k or norm == v.lower():
                return v
        for k, exes in APPROVED_PROCESS_NAMES.items():
            if norm == k or norm in [e.lower() for e in exes]:
                return CANONICAL_APP_NAMES.get(k, k.title())
        for exe_key, canonical in ALLOWED_EXECUTABLE_TO_APP.items():
            if norm == exe_key or norm == exe_key.replace(".exe", ""):
                return canonical
        raise PermissionError(f"Application '{application}' is not in approved application allowlist")

    async def _emit_telemetry(
        self,
        event_type: ActionType,
        status: ActionStatus,
        title: str,
        task_id: str,
        duration_ms: float = 0.0,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish safe, sanitized telemetry event without sensitive text or base64 images."""
        meta = safe_metadata or {}
        # Ensure secret scrubbing on all event payloads
        scrubbed = {}
        for k, v in meta.items():
            if k in ("text", "password", "token", "key", "secret", "image_base64", "raw_image"):
                continue
            scrubbed[k] = v

        try:
            await action_bus.publish(
                ActionEvent(
                    action_type=event_type,
                    status=status,
                    title=title,
                    task_id=task_id,
                    duration_ms=duration_ms,
                    safe_metadata=scrubbed,
                )
            )
        except Exception as exc:
            logger.debug(f"[DESKTOP_ACTION] Telemetry emission notice: {exc}")

    async def _emit_failure(
        self,
        task_id: str,
        action: DesktopActionType,
        error: str,
        failure_class: FailureClass,
        duration_ms: float,
        application: Optional[str] = None,
    ) -> None:
        """Publish safe failure event to ActionEventBus."""
        await self._emit_telemetry(
            event_type=ActionType.DESKTOP_ACTION_FAILED,
            status=ActionStatus.FAILED,
            title=f"Desktop action failed: {action.value}",
            task_id=task_id,
            duration_ms=duration_ms,
            safe_metadata={
                "action": action.value,
                "error": error,
                "failure_class": failure_class.value,
                "application": application,
                "success": False,
            },
        )

    # -----------------------------------------------------------------------
    # Core Semantic Desktop Action Execution
    # -----------------------------------------------------------------------

    async def execute_action(
        self,
        request: DesktopActionRequest,
        auto_confirm: bool = False,
        task_id: Optional[str] = None,
    ) -> DesktopActionResult:
        """Execute a controlled desktop action through the safe authority chain.

        Strict Invariants:
        1. Validate requested application against strict approved allowlist.
        2. Resolve semantic target through DesktopTargetResolver (raw LLM coords rejected).
        3. Validate target bounds (finite, positive area, inside window).
        4. Validate confidence score >= threshold (default 0.80).
        5. Verify window & target freshness before consequential actions (stale target protection).
        6. Enforce ConfirmationManager on consequential actions.
        7. Focus target window if necessary using WindowsDesktopDriver.focus_window().
        8. Execute native input strictly via WindowsDesktopDriver.
        9. Emit safe ActionEventBus telemetry events.
        10. Bounded post-action observation using ObserverEngine.
        11. Persist checkpoint where applicable.
        """
        t0 = time.monotonic()
        task_id = task_id or f"desk-act-{uuid.uuid4().hex[:8]}"

        # 1. Telemetry: Action Requested
        await self._emit_telemetry(
            event_type=ActionType.DESKTOP_ACTION_REQUESTED,
            status=ActionStatus.PENDING,
            title=f"Desktop action requested: {request.action.value}",
            task_id=task_id,
            safe_metadata={
                "action": request.action.value,
                "application": request.application,
                "target": request.target,
                "hwnd": request.hwnd,
            },
        )

        # 2. Credential & Secret Scrubbing
        try:
            self._check_credentials(request.text)
            self._check_credentials(request.target)
        except PermissionError as pe:
            duration_ms = (time.monotonic() - t0) * 1000
            await self._emit_failure(task_id, request.action, str(pe), FailureClass.SECURITY, duration_ms, request.application)
            return DesktopActionResult(
                action=request.action,
                success=False,
                application=request.application,
                failure_class=FailureClass.SECURITY,
                error=str(pe),
                duration_ms=duration_ms,
            )

        # 3. Application & Window Allowlist Validation
        canonical_app = None
        if request.application:
            try:
                canonical_app = self._validate_application_name(request.application)
            except PermissionError as pe:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, str(pe), FailureClass.SECURITY, duration_ms, request.application)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    application=request.application,
                    failure_class=FailureClass.SECURITY,
                    error=str(pe),
                    duration_ms=duration_ms,
                )

        if request.hwnd is not None and request.hwnd > 0:
            try:
                _, win_app, _, _ = self.driver.validate_window_application(request.hwnd)
                if not canonical_app:
                    canonical_app = win_app
            except PermissionError as pe:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, str(pe), FailureClass.SECURITY, duration_ms, request.application)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    hwnd=request.hwnd,
                    failure_class=FailureClass.SECURITY,
                    error=str(pe),
                    duration_ms=duration_ms,
                )
            except ValueError as ve:
                duration_ms = (time.monotonic() - t0) * 1000
                fc = FailureClass.WINDOW_NOT_FOUND if "WINDOW_NOT_FOUND" in str(ve) else FailureClass.APPLICATION_NOT_RUNNING
                await self._emit_failure(task_id, request.action, str(ve), fc, duration_ms, request.application)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    hwnd=request.hwnd,
                    failure_class=fc,
                    error=str(ve),
                    duration_ms=duration_ms,
                )

        # 4. Action Argument Validation
        if request.action == DesktopActionType.KEY:
            if not request.key:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "Key parameter is required for KEY action", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="Key parameter is required for KEY action", duration_ms=duration_ms)
            norm_k = request.key.strip().lower()
            if norm_k not in ALLOWED_DESKTOP_KEYS and len(norm_k) != 1:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, f"Key '{request.key}' is disallowed or invalid", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error=f"Key '{request.key}' is disallowed or invalid", duration_ms=duration_ms)

        elif request.action == DesktopActionType.HOTKEY:
            if not request.hotkey or len(request.hotkey) < 1 or len(request.hotkey) > 4:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "Hotkey must contain between 1 and 4 keys", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="Hotkey must contain between 1 and 4 keys", duration_ms=duration_ms)
            for k in request.hotkey:
                norm_k = k.strip().lower()
                if norm_k not in ALLOWED_DESKTOP_KEYS and len(norm_k) != 1:
                    duration_ms = (time.monotonic() - t0) * 1000
                    await self._emit_failure(task_id, request.action, f"Hotkey key '{k}' is disallowed or invalid", FailureClass.VALIDATION, duration_ms)
                    return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error=f"Hotkey key '{k}' is disallowed or invalid", duration_ms=duration_ms)

        elif request.action == DesktopActionType.SCROLL:
            amt = request.scroll_amount
            if amt is None:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "scroll_amount is required for SCROLL action", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="scroll_amount is required for SCROLL action", duration_ms=duration_ms)
            if amt < -10000 or amt > 10000:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "Scroll amount out of bounded range [-10000, 10000]", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="Scroll amount out of bounded range [-10000, 10000]", duration_ms=duration_ms)

        elif request.action == DesktopActionType.TYPE:
            if request.text is None:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "Text parameter required for TYPE action", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="Text parameter required for TYPE action", duration_ms=duration_ms)
            if len(request.text) > 1000:
                duration_ms = (time.monotonic() - t0) * 1000
                await self._emit_failure(task_id, request.action, "Text length exceeds maximum allowed bound of 1000 characters", FailureClass.VALIDATION, duration_ms)
                return DesktopActionResult(action=request.action, success=False, failure_class=FailureClass.VALIDATION, error="Text length exceeds maximum allowed bound of 1000 characters", duration_ms=duration_ms)

        # 5. Semantic Target Resolution (Mandatory for click-like actions)
        resolved_elem: Optional[DesktopUIElement] = None
        target_win: Optional[DesktopWindowState] = None

        if request.action in (DesktopActionType.CLICK, DesktopActionType.DOUBLE_CLICK, DesktopActionType.RIGHT_CLICK):
            if not request.target or not request.target.strip():
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = "Target description required for click actions. Direct raw coordinates are strictly prohibited."
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.TARGET_NOT_FOUND, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    failure_class=FailureClass.TARGET_NOT_FOUND,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

            # Resolve semantic target through DesktopTargetResolver
            res: DesktopTargetResolutionResult = await self.resolver.resolve_target(
                target=request.target,
                hwnd=request.hwnd,
                application=canonical_app or request.application,
                confidence_threshold=DEFAULT_TARGET_CONFIDENCE,
                task_id=task_id,
            )

            if not res.success or not res.element:
                duration_ms = (time.monotonic() - t0) * 1000
                fc = res.failure_class or FailureClass.TARGET_NOT_FOUND
                err = res.error or f"Target '{request.target}' could not be resolved"
                await self._emit_failure(task_id, request.action, err, fc, duration_ms, canonical_app or request.application)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    target=request.target,
                    application=canonical_app or request.application,
                    hwnd=request.hwnd,
                    confidence=res.confidence,
                    failure_class=fc,
                    error=err,
                    duration_ms=duration_ms,
                )

            resolved_elem = res.element
            target_win = res.window or (self.driver.get_window_info(request.hwnd) if request.hwnd else None)

            # Target bounds verification
            bounds = resolved_elem.bounds or {}
            left = bounds.get("left", 0)
            top = bounds.get("top", 0)
            w = bounds.get("width", 0)
            h = bounds.get("height", 0)

            if w <= 0 or h <= 0 or left < 0 or top < 0:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = f"Zero-area or negative target bounds rejected (w={w}, h={h}, left={left}, top={top})"
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.TARGET_NOT_FOUND, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    target=request.target,
                    resolved_element=resolved_elem,
                    confidence=resolved_elem.confidence,
                    failure_class=FailureClass.TARGET_NOT_FOUND,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

            if target_win:
                if (left + w) > target_win.width or (top + h) > target_win.height:
                    duration_ms = (time.monotonic() - t0) * 1000
                    err_msg = f"Target bounds extend outside target window ({left + w} > {target_win.width} or {top + h} > {target_win.height})"
                    await self._emit_failure(task_id, request.action, err_msg, FailureClass.TARGET_NOT_FOUND, duration_ms)
                    return DesktopActionResult(
                        action=request.action,
                        success=False,
                        target=request.target,
                        resolved_element=resolved_elem,
                        confidence=resolved_elem.confidence,
                        failure_class=FailureClass.TARGET_NOT_FOUND,
                        error=err_msg,
                        duration_ms=duration_ms,
                    )

            if resolved_elem.confidence < DEFAULT_TARGET_CONFIDENCE:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = f"Target confidence {resolved_elem.confidence:.2f} is below required threshold {DEFAULT_TARGET_CONFIDENCE:.2f}"
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.VISION_UNCERTAIN, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    target=request.target,
                    resolved_element=resolved_elem,
                    confidence=resolved_elem.confidence,
                    failure_class=FailureClass.VISION_UNCERTAIN,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

            if not resolved_elem.actionable:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = f"Target element '{request.target}' is marked non-actionable"
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.TARGET_NOT_FOUND, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    target=request.target,
                    resolved_element=resolved_elem,
                    confidence=resolved_elem.confidence,
                    failure_class=FailureClass.TARGET_NOT_FOUND,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

        elif request.action == DesktopActionType.TYPE and request.target:
            # Resolve target input field if specified
            res = await self.resolver.resolve_target(
                target=request.target,
                hwnd=request.hwnd,
                application=canonical_app or request.application,
                confidence_threshold=DEFAULT_TARGET_CONFIDENCE,
                task_id=task_id,
            )
            if not res.success or not res.element:
                duration_ms = (time.monotonic() - t0) * 1000
                fc = res.failure_class or FailureClass.TARGET_NOT_FOUND
                err = res.error or f"Target '{request.target}' could not be resolved for typing"
                await self._emit_failure(task_id, request.action, err, fc, duration_ms, canonical_app or request.application)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    target=request.target,
                    confidence=res.confidence,
                    failure_class=fc,
                    error=err,
                    duration_ms=duration_ms,
                )
            resolved_elem = res.element
            target_win = res.window or (self.driver.get_window_info(request.hwnd) if request.hwnd else None)

        else:
            # Discover target window if HWND or application specified
            if request.hwnd is not None and request.hwnd > 0:
                try:
                    target_win = self.driver.get_window_info(request.hwnd)
                except Exception:
                    pass
            elif canonical_app or request.application:
                app_lookup = (canonical_app or request.application).lower()
                running = self.driver.inspect_windows()
                for w in running:
                    if app_lookup in w.application.lower() or app_lookup in w.executable.lower():
                        target_win = w
                        break

        # 6. Stale Target & UI Freshness Protection
        if target_win is not None:
            try:
                fresh_win = self.driver.get_window_info(target_win.hwnd)
                if not fresh_win.visible or fresh_win.width <= 0 or fresh_win.height <= 0:
                    duration_ms = (time.monotonic() - t0) * 1000
                    err_msg = f"Target window (HWND {target_win.hwnd}) has become stale, minimized, or closed"
                    await self._emit_failure(task_id, request.action, err_msg, FailureClass.UI_CHANGED, duration_ms)
                    return DesktopActionResult(
                        action=request.action,
                        success=False,
                        hwnd=target_win.hwnd,
                        failure_class=FailureClass.UI_CHANGED,
                        error=err_msg,
                        duration_ms=duration_ms,
                    )
            except Exception as e:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = f"Target window validation failed (stale target): {e}"
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.WINDOW_NOT_FOUND, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    hwnd=target_win.hwnd,
                    failure_class=FailureClass.WINDOW_NOT_FOUND,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

        # 7. Telemetry: Action Validated
        await self._emit_telemetry(
            event_type=ActionType.DESKTOP_ACTION_VALIDATED,
            status=ActionStatus.PROGRESS,
            title=f"Desktop action validated: {request.action.value}",
            task_id=task_id,
            safe_metadata={
                "action": request.action.value,
                "application": canonical_app or (target_win.application if target_win else request.application),
                "hwnd": target_win.hwnd if target_win else request.hwnd,
                "target": request.target,
                "target_role": resolved_elem.role if resolved_elem else None,
                "confidence": resolved_elem.confidence if resolved_elem else None,
            },
        )

        # 8. Confirmation Gate for Consequential Actions
        is_consequential = self._is_consequential_action(
            request.action,
            target=request.target,
            text=request.text,
        )

        if is_consequential and not request.confirmed and not auto_confirm:
            action_name = f"desktop_{request.action.value.lower()}"
            token = self.confirmation_mgr.request_confirmation(
                action_name=action_name,
                parameters={
                    "target": request.target,
                    "application": canonical_app or request.application,
                    "action": request.action.value,
                },
            )
            duration_ms = (time.monotonic() - t0) * 1000
            await self._emit_telemetry(
                event_type=ActionType.DESKTOP_CONFIRMATION_REQUIRED,
                status=ActionStatus.WAITING_CONFIRMATION,
                title=f"Confirmation required: {request.action.value} on '{request.target or request.application}'",
                task_id=task_id,
                duration_ms=duration_ms,
                safe_metadata={
                    "action": request.action.value,
                    "target": request.target,
                    "application": canonical_app or request.application,
                    "confirmation_token": token,
                    "confirmation_required": True,
                },
            )
            return DesktopActionResult(
                action=request.action,
                success=False,
                target=request.target,
                application=canonical_app or request.application,
                hwnd=target_win.hwnd if target_win else request.hwnd,
                confirmation_required=True,
                confirmation_token=token,
                message=f"Action '{request.action.value}' on '{request.target or request.application}' requires user confirmation before execution.",
                failure_class=FailureClass.USER_ACTION_REQUIRED,
                duration_ms=duration_ms,
            )

        if is_consequential and (request.confirmed or auto_confirm):
            await self._emit_telemetry(
                event_type=ActionType.DESKTOP_ACTION_CONFIRMED,
                status=ActionStatus.PROGRESS,
                title=f"Desktop action confirmed: {request.action.value}",
                task_id=task_id,
                safe_metadata={
                    "action": request.action.value,
                    "target": request.target,
                    "application": canonical_app or request.application,
                },
            )

        # 9. Window Focus
        active_hwnd = target_win.hwnd if target_win else request.hwnd
        if active_hwnd is not None and active_hwnd > 0:
            try:
                self.driver.focus_window(active_hwnd)
            except Exception as fe:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = f"Failed to focus window HWND {active_hwnd}: {fe}"
                await self._emit_failure(task_id, request.action, err_msg, FailureClass.FOCUS_FAILED, duration_ms)
                return DesktopActionResult(
                    action=request.action,
                    success=False,
                    hwnd=active_hwnd,
                    failure_class=FailureClass.FOCUS_FAILED,
                    error=err_msg,
                    duration_ms=duration_ms,
                )

        # 10. Native Action Execution via WindowsDesktopDriver
        exec_x: Optional[int] = None
        exec_y: Optional[int] = None

        if resolved_elem and target_win:
            exec_x = target_win.left + resolved_elem.center_x
            exec_y = target_win.top + resolved_elem.center_y

        res_dict: Dict[str, Any] = {}
        try:
            if request.action == DesktopActionType.CLICK:
                res_dict = self.driver.click(exec_x, exec_y, active_hwnd)
            elif request.action == DesktopActionType.DOUBLE_CLICK:
                res_dict = self.driver.double_click(exec_x, exec_y, active_hwnd)
            elif request.action == DesktopActionType.RIGHT_CLICK:
                res_dict = self.driver.right_click(exec_x, exec_y, active_hwnd)
            elif request.action == DesktopActionType.TYPE:
                if exec_x is not None and exec_y is not None:
                    # Focus control with single click before typing
                    self.driver.click(exec_x, exec_y, active_hwnd)
                    time.sleep(0.02)
                res_dict = self.driver.type_text(request.text or "")
                # Cleanse details of typed characters (zero secret leakage)
                res_dict["characters"] = len(request.text or "")
                res_dict.pop("text", None)
            elif request.action == DesktopActionType.KEY:
                res_dict = self.driver.press_key(request.key or "")
            elif request.action == DesktopActionType.HOTKEY:
                res_dict = self.driver.hotkey(request.hotkey or [])
            elif request.action == DesktopActionType.SCROLL:
                res_dict = self.driver.scroll(request.scroll_amount or 0, active_hwnd)
            elif request.action == DesktopActionType.FOCUS:
                res_dict = {"success": True, "action": "focus", "hwnd": active_hwnd}
            elif request.action == DesktopActionType.INSPECT:
                windows = self.driver.inspect_windows()
                res_dict = {"success": True, "action": "inspect", "window_count": len(windows)}
            elif request.action == DesktopActionType.WINDOW_INFO:
                w_info = self.driver.get_window_info(active_hwnd)
                res_dict = {
                    "success": True,
                    "action": "window_info",
                    "title": w_info.title,
                    "executable": w_info.executable,
                    "application": w_info.application,
                }
            elif request.action == DesktopActionType.SCREENSHOT:
                img = self.driver.capture_window(active_hwnd) if active_hwnd else self.driver.capture_desktop()
                res_dict = {
                    "success": True,
                    "action": "screenshot",
                    "width": img.width,
                    "height": img.height,
                }
            else:
                raise ValueError(f"Unsupported desktop action type: {request.action}")

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            err_msg = str(exc)
            fc = FailureClass.PERMANENT
            if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                fc = FailureClass.ACTION_TIMEOUT
            elif "target_not_found" in err_msg.lower():
                fc = FailureClass.TARGET_NOT_FOUND
            elif "permission" in err_msg.lower() or "denied" in err_msg.lower():
                fc = FailureClass.SECURITY
            elif "focus" in err_msg.lower():
                fc = FailureClass.FOCUS_FAILED

            await self._emit_failure(task_id, request.action, err_msg, fc, duration_ms, canonical_app or request.application)
            return DesktopActionResult(
                action=request.action,
                success=False,
                target=request.target,
                application=canonical_app or request.application,
                hwnd=active_hwnd,
                failure_class=fc,
                error=err_msg,
                duration_ms=duration_ms,
            )

        # 11. Bounded Post-Action Observation
        post_obs: Optional[Any] = None
        if request.action in (
            DesktopActionType.CLICK,
            DesktopActionType.DOUBLE_CLICK,
            DesktopActionType.RIGHT_CLICK,
            DesktopActionType.TYPE,
            DesktopActionType.KEY,
            DesktopActionType.HOTKEY,
            DesktopActionType.SCROLL,
        ):
            try:
                obs_target = canonical_app or (target_win.application if target_win else request.application)
                post_obs = await self.observer.observe_desktop(target_app=obs_target, task_id=task_id)
            except Exception as oe:
                logger.debug(f"[DESKTOP_ACTION] Post-action observation skipped: {oe}")

        # 12. Telemetry: Action Executed
        duration_ms = (time.monotonic() - t0) * 1000
        safe_meta = {
            "action": request.action.value,
            "application": canonical_app or (target_win.application if target_win else request.application),
            "hwnd": active_hwnd,
            "target_role": resolved_elem.role if resolved_elem else None,
            "target_source": resolved_elem.source.value if resolved_elem and resolved_elem.source else None,
            "confidence": resolved_elem.confidence if resolved_elem else None,
            "duration_ms": duration_ms,
            "success": True,
        }
        await self._emit_telemetry(
            event_type=ActionType.DESKTOP_ACTION_EXECUTED,
            status=ActionStatus.COMPLETED,
            title=f"Desktop action executed: {request.action.value}",
            task_id=task_id,
            duration_ms=duration_ms,
            safe_metadata=safe_meta,
        )

        # 13. Checkpoint Recording
        if self.checkpoints and task_id:
            try:
                self.checkpoints.save_checkpoint(
                    task_id=task_id,
                    user_goal=f"Desktop action: {request.action.value}",
                    task_type="desktop_action",
                    current_state="COMPLETED",
                    current_step_id=request.action.value,
                    completed_steps=[request.action.value],
                    pending_steps=[],
                    step_details=[safe_meta],
                    metadata=safe_meta,
                )
            except Exception as ce:
                logger.debug(f"[DESKTOP_ACTION] Checkpoint save notice: {ce}")

        # 14. Return Structured Result
        return DesktopActionResult(
            action=request.action,
            success=True,
            application=canonical_app or (target_win.application if target_win else request.application),
            hwnd=active_hwnd,
            target=request.target,
            resolved_element=resolved_elem,
            confidence=resolved_elem.confidence if resolved_elem else None,
            x=exec_x,
            y=exec_y,
            duration_ms=duration_ms,
            message=f"Desktop action '{request.action.value}' successfully executed.",
            post_observation=post_obs,
            details=res_dict if isinstance(res_dict, dict) else {},
        )

    async def execute_semantic_action(
        self,
        action: Union[DesktopActionType, str],
        target: Optional[str] = None,
        application: Optional[str] = None,
        hwnd: Optional[int] = None,
        text: Optional[str] = None,
        key: Optional[str] = None,
        hotkey: Optional[List[str]] = None,
        scroll_amount: Optional[int] = None,
        confirmed: bool = False,
        auto_confirm: bool = False,
        task_id: Optional[str] = None,
    ) -> DesktopActionResult:
        """Convenience wrapper for semantic desktop action execution."""
        act_enum = DesktopActionType(action) if isinstance(action, str) else action
        req = DesktopActionRequest(
            action=act_enum,
            target=target,
            application=application,
            hwnd=hwnd,
            text=text,
            key=key,
            hotkey=hotkey,
            scroll_amount=scroll_amount,
            confirmed=confirmed,
        )
        return await self.execute_action(req, auto_confirm=auto_confirm, task_id=task_id)


# Module-level default singleton
desktop_action_engine = DesktopActionEngine()

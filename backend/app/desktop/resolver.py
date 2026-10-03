"""
RYVEN 3.0 — Milestone 17.1 Desktop Target Resolver.
Semantic desktop target resolution and perception pipeline.

Pipeline Flow:
    USER TARGET
        ↓
    DESKTOP OBSERVATION / WINDOW LOOKUP
        ↓
    TRANSIENT IN-MEMORY SCREENSHOT
        ↓
    PerceptionAgent (Local-only Vision & OCR)
        ↓
    DETECTED UI ELEMENTS (Normalized DesktopUIElement)
        ↓
    SEMANTIC MATCHING & SCORING
        ↓
    CONFIDENCE ASSESSMENT & BOUNDS VALIDATION
        ↓
    RESOLVED TARGET RESULT

Security Invariants:
- STRICTLY READ-ONLY: Never clicks, types, scrolls, hotkeys, launches, or closes applications.
- Zero subprocess / cmd / powershell / shell executions.
- Zero disk persistence for screenshots; in-memory transient PIL images only.
- Zero credentials, tokens, or base64 images published to ActionEventBus telemetry.
- Rejects out-of-window, zero-area, or unapproved application targets.
"""

from __future__ import annotations

import base64
import difflib
import io
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.vision import DetectedElement, PerceptionAgent, PerceptionResult, perception_agent
from app.control.models import (
    DesktopTargetResolutionResult,
    DesktopTargetSource,
    DesktopUIElement,
    DesktopWindowState,
    FailureClass,
)
from app.core.logging_config import logger
from app.desktop.interaction import WindowsDesktopDriver, desktop_driver

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TARGET_CONFIDENCE = 0.80

_ROLE_KEYWORDS = {
    "button": "button",
    "btn": "button",
    "input": "input",
    "textbox": "input",
    "textfield": "input",
    "box": "input",
    "tab": "tab",
    "menu": "menu",
    "menuitem": "menuitem",
    "link": "link",
    "checkbox": "checkbox",
    "radio": "radio",
    "icon": "icon",
    "bar": "bar",
}

_FORBIDDEN_CREDENTIAL_PATTERNS = (
    "password=",
    "bearer ",
    "ssh-rsa",
    "-----begin ",
    "api_key",
    "secret=",
)


class DesktopTargetResolver:
    """Resolves high-level semantic target descriptions into actionable UI element coordinates.

    Strictly read-only; performs perception and analysis only, never execution.
    """

    def __init__(
        self,
        driver: Optional[WindowsDesktopDriver] = None,
        perception: Optional[PerceptionAgent] = None,
    ) -> None:
        self.driver = driver or desktop_driver
        self.perception_agent = perception or perception_agent

    # -----------------------------------------------------------------------
    # Text Normalization & Sanitization
    # -----------------------------------------------------------------------

    def normalize_text(self, text: Optional[str]) -> str:
        """Collapse whitespace, trim, and lowercase text safely."""
        if not text or not isinstance(text, str):
            return ""
        collapsed = re.sub(r"\s+", " ", text).strip().lower()
        return collapsed

    def _check_credentials(self, text: str) -> None:
        """Reject text containing potential credential strings."""
        lowered = text.lower()
        for pattern in _FORBIDDEN_CREDENTIAL_PATTERNS:
            if pattern in lowered:
                raise PermissionError(f"Target contains forbidden credential pattern: {pattern}")

    # -----------------------------------------------------------------------
    # Bounds & Element Validation
    # -----------------------------------------------------------------------

    def validate_element_bounds(
        self,
        bounds: Dict[str, int],
        window_width: int,
        window_height: int,
    ) -> Tuple[bool, Optional[str]]:
        """Validate element bounding rectangle against geometry constraints.

        Invariants:
        - width > 0, height > 0 (zero-area elements rejected).
        - left >= 0, top >= 0 (no negative coordinates).
        - left + width <= window_width, top + height <= window_height (no out-of-window elements).
        """
        required = {"left", "top", "width", "height"}
        if not required.issubset(bounds.keys()):
            return False, f"Missing required bounds keys: {sorted(required)}"

        left = bounds["left"]
        top = bounds["top"]
        width = bounds["width"]
        height = bounds["height"]

        if width <= 0 or height <= 0:
            return False, f"Zero-area or negative dimensions (width={width}, height={height})"

        if left < 0 or top < 0:
            return False, f"Negative coordinate bounds (left={left}, top={top})"

        if window_width > 0 and (left + width) > window_width:
            return False, f"Element exceeds window width ({left + width} > {window_width})"

        if window_height > 0 and (top + height) > window_height:
            return False, f"Element exceeds window height ({top + height} > {window_height})"

        return True, None

    # -----------------------------------------------------------------------
    # Perception Extraction & Normalization
    # -----------------------------------------------------------------------

    def extract_elements_from_perception(
        self,
        perception_res: PerceptionResult,
        window_width: int,
        window_height: int,
    ) -> List[DesktopUIElement]:
        """Convert raw perception output into validated DesktopUIElement models."""
        elements: List[DesktopUIElement] = []

        for raw in perception_res.elements_detected:
            label = getattr(raw, "label", "") or ""
            text = getattr(raw, "text", "") or label
            conf = getattr(raw, "confidence", 1.0)
            loc = getattr(raw, "location", {}) or {}

            # Convert location to pixel bounds
            # Handle normalized 0.0-1.0 floats vs absolute integer coordinates
            w_val = float(loc.get("width", loc.get("w", 0)))
            h_val = float(loc.get("height", loc.get("h", 0)))
            x_val = float(loc.get("x", loc.get("left", 0)))
            y_val = float(loc.get("y", loc.get("top", 0)))

            if (w_val <= 1.0 and h_val <= 1.0 and (window_width > 1 or window_height > 1)):
                left = int(round(x_val * window_width))
                top = int(round(y_val * window_height))
                width = int(round(w_val * window_width))
                height = int(round(h_val * window_height))
            else:
                left = int(round(x_val))
                top = int(round(y_val))
                width = int(round(w_val))
                height = int(round(h_val))

            bounds = {"left": left, "top": top, "width": width, "height": height}
            valid, reason = self.validate_element_bounds(bounds, window_width, window_height)
            if not valid:
                logger.debug(f"[TARGET_RESOLVER] Discarding element '{label}' due to invalid bounds: {reason}")
                continue

            # Infer role
            role = "control"
            label_norm = self.normalize_text(label)
            for kw, r_name in _ROLE_KEYWORDS.items():
                if kw in label_norm:
                    role = r_name
                    break

            try:
                elem = DesktopUIElement(
                    element_id=f"elem-{uuid.uuid4().hex[:8]}",
                    role=role,
                    text=text[:500],
                    bounds=bounds,
                    confidence=max(0.0, min(1.0, float(conf))),
                    source=DesktopTargetSource.VISION,
                    actionable=True,
                )
                elements.append(elem)
            except Exception as e:
                logger.debug(f"[TARGET_RESOLVER] Element construction error: {e}")

        # Ingest text regions as OCR elements if location bounds are provided
        for region in perception_res.text_regions:
            if not isinstance(region, str) or not region.strip():
                continue
            # Text regions with synthesized bounds if not already covered
            # Typically text regions complement OCR detection
            pass

        return elements

    # -----------------------------------------------------------------------
    # Semantic Matching Strategy
    # -----------------------------------------------------------------------

    def calculate_match_score(
        self,
        target_norm: str,
        target_role: Optional[str],
        target_words: List[str],
        elem: DesktopUIElement,
    ) -> float:
        """Calculate semantic similarity score between requested target and UI element.

        Priority:
        1. Exact semantic identifier / role match: 1.0
        2. Exact visible text match: 1.0
        3. Normalized text match: 0.95
        4. Role + text match: 0.92
        5. Word containment / accessible label: 0.85 - 0.88
        6. OCR text match: 0.82
        7. Controlled fuzzy text match: difflib.ratio * 0.90
        """
        elem_text_norm = self.normalize_text(elem.text)
        elem_role_norm = self.normalize_text(elem.role)

        # 1. Exact identifier or role match
        if elem.element_id.lower() == target_norm:
            return 1.0

        # 2. Exact visible text match
        if elem.text == target_norm or (elem.text and elem.text.strip().lower() == target_norm):
            return 1.0

        # 3. Normalized text match
        if elem_text_norm and elem_text_norm == target_norm:
            return 0.98

        # 4. Role + text match (e.g. "search button" -> role="button", text="search")
        if target_role and target_role == elem_role_norm:
            matched = [w for w in target_words if w in elem_text_norm]
            if matched:
                return 0.95

        # 5. Full phrase containment or prefix
        if target_norm and target_norm in elem_text_norm:
            return 0.94 if elem_text_norm.startswith(target_norm) else 0.90

        # 6. OCR specific match
        if elem.source == DesktopTargetSource.OCR and any(w in elem_text_norm for w in target_words):
            return 0.90

        # Word containment
        if target_words:
            matched_words = [w for w in target_words if w in elem_text_norm]
            if len(matched_words) == len(target_words):
                return 0.86
            elif len(matched_words) > 0 and len(matched_words) >= len(target_words) / 2:
                return 0.75 * (len(matched_words) / len(target_words))

        # 7. Controlled fuzzy similarity
        if elem_text_norm:
            ratio = difflib.SequenceMatcher(None, target_norm, elem_text_norm).ratio()
            if ratio >= 0.75:
                return ratio * 0.90

        return 0.0

    def match_elements(
        self,
        target: str,
        elements: List[DesktopUIElement],
        confidence_threshold: float = DEFAULT_TARGET_CONFIDENCE,
    ) -> Tuple[Optional[DesktopUIElement], float, Optional[str], Optional[FailureClass]]:
        """Match candidate elements against target query and detect ambiguities.

        Returns:
            (matched_element, final_confidence, error_message, failure_class)
        """
        target_norm = self.normalize_text(target)
        if not target_norm:
            return None, 0.0, "Empty target specification", FailureClass.TARGET_NOT_FOUND

        # Parse potential role keyword from target
        all_words = target_norm.split()
        target_role: Optional[str] = None
        target_words: List[str] = []
        for word in all_words:
            if word in _ROLE_KEYWORDS and target_role is None:
                target_role = _ROLE_KEYWORDS[word]
            else:
                target_words.append(word)

        if not target_words:
            target_words = all_words

        scored_candidates: List[Tuple[DesktopUIElement, float, float]] = []

        for elem in elements:
            score = self.calculate_match_score(target_norm, target_role, target_words, elem)
            if score <= 0.0:
                continue

            # Modulate by detection confidence
            final_conf = max(0.0, min(1.0, round(score * elem.confidence, 3)))
            scored_candidates.append((elem, final_conf, score))

        if not scored_candidates:
            return None, 0.0, f"No UI element matched target '{target}'", FailureClass.TARGET_NOT_FOUND

        # Sort descending by final confidence
        scored_candidates.sort(key=lambda item: item[1], reverse=True)
        best_elem, best_conf, best_score = scored_candidates[0]

        # Check confidence threshold
        if best_conf < confidence_threshold:
            return (
                None,
                best_conf,
                f"Candidate match confidence {best_conf:.2f} below threshold {confidence_threshold:.2f}",
                FailureClass.VISION_UNCERTAIN,
            )

        # Ambiguity detection: check if multiple top candidates have indistinguishable confidence
        if len(scored_candidates) > 1:
            second_elem, second_conf, second_score = scored_candidates[1]
            if second_conf >= confidence_threshold:
                # If top two candidates are within 0.05 confidence and neither is an exact match
                is_exact_1 = (best_elem.text.strip().lower() == target_norm)
                is_exact_2 = (second_elem.text.strip().lower() == target_norm)

                if is_exact_1 and not is_exact_2:
                    pass  # exact match wins unambiguously
                elif abs(best_conf - second_conf) < 0.05:
                    logger.info(
                        f"[TARGET_RESOLVER] Ambiguous target detected: multiple elements matched '{target}' "
                        f"('{best_elem.text}' vs '{second_elem.text}')"
                    )
                    return (
                        None,
                        best_conf,
                        f"Ambiguous target: multiple UI elements matched '{target}' with similar confidence ({best_conf:.2f} vs {second_conf:.2f})",
                        FailureClass.TARGET_NOT_FOUND,
                    )

        return best_elem, best_conf, None, None

    # -----------------------------------------------------------------------
    # Core Resolution Entry Point
    # -----------------------------------------------------------------------

    async def resolve_target(
        self,
        target: str,
        hwnd: Optional[int] = None,
        application: Optional[str] = None,
        window_state: Optional[DesktopWindowState] = None,
        candidate_elements: Optional[List[DesktopUIElement]] = None,
        confidence_threshold: float = DEFAULT_TARGET_CONFIDENCE,
        task_id: Optional[str] = None,
    ) -> DesktopTargetResolutionResult:
        """Resolve a high-level UI target query to a specific, validated DesktopUIElement.

        Strict invariants:
        - READ-ONLY: Never executes mouse clicks, keyboard input, or shell processes.
        - Memory-only transient screenshots.
        - Emits safe telemetry without credentials, tokens, or base64 data.
        """
        t0 = time.monotonic()

        # 1. Validate target query
        if not target or not isinstance(target, str) or not target.strip():
            return DesktopTargetResolutionResult(
                success=False,
                target=str(target),
                confidence=0.0,
                failure_class=FailureClass.TARGET_NOT_FOUND,
                error="Target query cannot be empty",
            )

        try:
            self._check_credentials(target)
        except PermissionError as e:
            return DesktopTargetResolutionResult(
                success=False,
                target="[REDACTED_CREDENTIAL_QUERY]",
                confidence=0.0,
                failure_class=FailureClass.SECURITY,
                error=str(e),
            )

        target_clean = target.strip()
        win: Optional[DesktopWindowState] = None

        # 2. Window state resolution
        try:
            if window_state is not None:
                # Validate window application
                self.driver.validate_window_application(window_state.hwnd)
                win = window_state
            elif hwnd is not None:
                win = self.driver.get_window_info(hwnd)
            elif application is not None:
                app_norm = application.strip().lower()
                running = self.driver.inspect_windows()
                for w in running:
                    if app_norm in w.application.lower() or app_norm in w.executable.lower():
                        win = w
                        break
                if win is None:
                    duration_ms = (time.monotonic() - t0) * 1000
                    await self._emit_telemetry(
                        ActionType.DESKTOP_TARGET_NOT_FOUND,
                        ActionStatus.FAILED,
                        application or "unknown",
                        target_clean,
                        None,
                        0.0,
                        "APPLICATION_NOT_RUNNING",
                        duration_ms,
                        task_id,
                    )
                    return DesktopTargetResolutionResult(
                        success=False,
                        target=target_clean,
                        confidence=0.0,
                        failure_class=FailureClass.APPLICATION_NOT_RUNNING,
                        error=f"No running allowlisted window found for application: {application}",
                    )
            else:
                # Use currently active or first available allowlisted window
                running = self.driver.inspect_windows()
                for w in running:
                    if w.focused:
                        win = w
                        break
                if win is None and running:
                    win = running[0]

                if win is None:
                    duration_ms = (time.monotonic() - t0) * 1000
                    await self._emit_telemetry(
                        ActionType.DESKTOP_TARGET_NOT_FOUND,
                        ActionStatus.FAILED,
                        "desktop",
                        target_clean,
                        None,
                        0.0,
                        "WINDOW_NOT_FOUND",
                        duration_ms,
                        task_id,
                    )
                    return DesktopTargetResolutionResult(
                        success=False,
                        target=target_clean,
                        confidence=0.0,
                        failure_class=FailureClass.WINDOW_NOT_FOUND,
                        error="No running allowlisted window available for perception",
                    )
        except PermissionError as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            err_msg = str(exc)
            await self._emit_telemetry(
                ActionType.DESKTOP_TARGET_NOT_FOUND,
                ActionStatus.FAILED,
                "unknown",
                target_clean,
                None,
                0.0,
                err_msg,
                duration_ms,
                task_id,
            )
            return DesktopTargetResolutionResult(
                success=False,
                target=target_clean,
                confidence=0.0,
                failure_class=FailureClass.SECURITY,
                error=f"Target window belongs to an unapproved application: {err_msg}",
            )
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            err_msg = str(exc)
            return DesktopTargetResolutionResult(
                success=False,
                target=target_clean,
                confidence=0.0,
                failure_class=FailureClass.WINDOW_NOT_FOUND,
                error=f"Failed to inspect target window: {err_msg}",
            )

        # 3. Obtain candidate elements
        elements: List[DesktopUIElement] = []
        if candidate_elements is not None:
            # Caller provided mocked or existing element candidates
            for elem in candidate_elements:
                valid, _ = self.validate_element_bounds(elem.bounds, win.width, win.height)
                if valid:
                    elements.append(elem)
        else:
            # 4. In-memory transient screenshot capture
            try:
                img = self.driver.capture_window(win.hwnd)
                if img is None:
                    raise RuntimeError("Capture window returned None")

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                err_msg = str(exc)
                logger.warning(f"[TARGET_RESOLVER] Screenshot capture failed: {err_msg}")
                return DesktopTargetResolutionResult(
                    success=False,
                    target=target_clean,
                    confidence=0.0,
                    failure_class=FailureClass.WINDOW_NOT_FOUND,
                    error=f"Window screenshot capture failed: {err_msg}",
                )

            # 5. Local PerceptionAgent Analysis
            try:
                perception_res = await self.perception_agent.analyze_screenshot(
                    img_b64,
                    prompt=f"Identify interactive UI controls, buttons, text matching: {target_clean}",
                    is_visual_sensitive=True,
                )
                elements = self.extract_elements_from_perception(perception_res, win.width, win.height)
            except Exception as exc:
                duration_ms = (time.monotonic() - t0) * 1000
                logger.warning(f"[TARGET_RESOLVER] Perception agent analysis error: {exc}")
                return DesktopTargetResolutionResult(
                    success=False,
                    target=target_clean,
                    confidence=0.0,
                    failure_class=FailureClass.VISION_UNCERTAIN,
                    error=f"Perception agent analysis error: {exc}",
                )

        # 6. Semantic Matching & Confidence
        matched_elem, confidence, error, failure_class = self.match_elements(
            target_clean,
            elements,
            confidence_threshold=confidence_threshold,
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if matched_elem is None:
            await self._emit_telemetry(
                ActionType.DESKTOP_TARGET_NOT_FOUND,
                ActionStatus.FAILED,
                win.application,
                target_clean,
                None,
                confidence,
                error or "Not found",
                duration_ms,
                task_id,
            )
            return DesktopTargetResolutionResult(
                success=False,
                target=target_clean,
                confidence=confidence,
                failure_class=failure_class or FailureClass.TARGET_NOT_FOUND,
                error=error,
                details={
                    "application": win.application,
                    "hwnd": win.hwnd,
                    "candidates_evaluated": len(elements),
                    "duration_ms": duration_ms,
                },
            )

        # 7. Success Result
        await self._emit_telemetry(
            ActionType.DESKTOP_TARGET_RESOLVED,
            ActionStatus.COMPLETED,
            win.application,
            target_clean,
            matched_elem.role,
            confidence,
            "Resolved successfully",
            duration_ms,
            task_id,
        )

        return DesktopTargetResolutionResult(
            success=True,
            target=target_clean,
            element=matched_elem,
            confidence=confidence,
            details={
                "application": win.application,
                "hwnd": win.hwnd,
                "role": matched_elem.role,
                "center_x": matched_elem.center_x,
                "center_y": matched_elem.center_y,
                "duration_ms": duration_ms,
            },
        )

    # -----------------------------------------------------------------------
    # Safe Telemetry
    # -----------------------------------------------------------------------

    async def _emit_telemetry(
        self,
        action_type: ActionType,
        status: ActionStatus,
        application: str,
        target: str,
        role: Optional[str],
        confidence: float,
        reason: str,
        duration_ms: float,
        task_id: Optional[str],
    ) -> None:
        """Publish safe event telemetry to ActionEventBus without sensitive data."""
        safe_meta = {
            "application": application[:64] if application else "unknown",
            "target": target[:60],
            "role": role[:32] if role else None,
            "confidence": round(confidence, 3),
            "reason": reason[:80] if reason else "",
            "duration_ms": round(duration_ms, 1),
        }
        await action_bus.publish(
            ActionEvent(
                action_type=action_type,
                status=status,
                title=f"Desktop target resolution: {target[:40]} ({status.value})",
                task_id=task_id,
                duration_ms=duration_ms,
                safe_metadata=safe_meta,
            )
        )


# Module-level singleton
desktop_target_resolver = DesktopTargetResolver()

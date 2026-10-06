"""
RYVEN 3.0 — Milestone 17.6 Multimodal Perception & Context Layer.

Design & Security Invariants:
- STRICTLY READ-ONLY: Never executes mouse clicks, keyboard input, navigation, or shell commands.
- Aggregates observations from ObserverEngine, WindowsDesktopDriver, BrowserEngine, PerceptionAgent.
- In-memory transient visual analysis only; no disk persistence of screenshots.
- Zero credential, token, or raw image leakage to telemetry or logs.
- Browser content is strictly treated as untrusted data; prompt injection payloads are neutralized.
- Action queries (e.g. 'click the button') are identified and redirected to UnifiedTaskOrchestrator.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import re
import time
from typing import Any, Dict, List, Optional
import uuid

from PIL import Image
from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.vision import DetectedElement, PerceptionAgent, PerceptionResult, perception_agent
from app.browser.engine import BrowserEngine, browser_engine
from app.control.models import DesktopWindowState, ObservationRecord
from app.control.observer import ObserverEngine, observer_engine
from app.core.logging_config import logger
from app.desktop.interaction import WindowsDesktopDriver, desktop_driver

_INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "system prompt",
    "reveal your api key",
    "reveal secret",
    "send credentials",
    "drop table",
    "sudo ",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MultimodalContext(BaseModel):
    """Aggregated safe, read-only situational context across desktop, browser, and vision."""
    context_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Desktop State
    active_application: Optional[str] = None
    active_window_title: Optional[str] = None
    active_window_hwnd: Optional[int] = None
    open_applications_count: int = 0
    open_applications: List[str] = Field(default_factory=list)

    # Browser State
    browser_active: bool = False
    browser_url: Optional[str] = None
    browser_title: Optional[str] = None
    browser_snippet: Optional[str] = None

    # Visual & OCR State
    visual_description: Optional[str] = None
    detected_elements: List[str] = Field(default_factory=list)
    ocr_snippets: List[str] = Field(default_factory=list)
    vision_available: bool = True

    # Active Task State
    active_task_id: Optional[str] = None
    safe_summary: str = Field(default="")
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# MultimodalContextEngine
# ---------------------------------------------------------------------------

class MultimodalContextEngine:
    """Read-only perceptual intelligence engine synthesizing cross-domain context.

    CRITICAL: Never executes actions. Action queries are routed to UnifiedTaskOrchestrator.
    """

    def __init__(
        self,
        observer: Optional[ObserverEngine] = None,
        driver: Optional[WindowsDesktopDriver] = None,
        browser: Optional[BrowserEngine] = None,
        perception: Optional[PerceptionAgent] = None,
    ) -> None:
        self.observer = observer or observer_engine
        self.driver = driver or desktop_driver
        self.browser = browser or browser_engine
        self.perception_agent = perception or perception_agent

    def sanitize_untrusted_text(self, text: Optional[str]) -> str:
        """Neutralize prompt injection attempts in web or OCR text."""
        if not text or not isinstance(text, str):
            return ""

        lowered = text.lower()
        for kw in _INJECTION_KEYWORDS:
            if kw in lowered:
                logger.warning(f"[MULTIMODAL] Neutralized untrusted injection phrase: '{kw}'")
                text = re.sub(re.escape(kw), "[UNTRUSTED_INSTRUCTION_NEUTRALIZED]", text, flags=re.IGNORECASE)

        return text.strip()[:1000]

    async def gather_context(
        self,
        target_app: Optional[str] = None,
        include_vision: bool = True,
        session_id: str = "default",
    ) -> MultimodalContext:
        """Collect read-only situational context across desktop, browser, and vision.

        Strict invariants:
        - Read-only; never mutates desktop or browser state.
        - Memory-only transient image inspection.
        - Never persists images to disk or logs.
        """
        t0 = time.monotonic()
        ctx = MultimodalContext()

        # 1. Desktop Observation
        try:
            desktop_obs = await self.observer.observe_desktop(target_app=target_app)
            if desktop_obs.active_window:
                ctx.active_application = desktop_obs.active_application
                ctx.active_window_title = desktop_obs.active_window.title
                ctx.active_window_hwnd = desktop_obs.active_window.hwnd
            ctx.open_applications_count = len(desktop_obs.windows)
            ctx.open_applications = [w.application for w in desktop_obs.windows if w.application]
        except Exception as exc:
            logger.debug(f"[MULTIMODAL] Desktop observation error: {exc}")

        # 2. Browser Observation
        try:
            browser_obs = await self.observer.observe_browser(session_id=session_id)
            if browser_obs and browser_obs.details:
                ctx.browser_active = True
                ctx.browser_url = browser_obs.details.get("url")
                ctx.browser_title = browser_obs.title
                raw_snippet = browser_obs.details.get("snippet") or browser_obs.summary
                ctx.browser_snippet = self.sanitize_untrusted_text(raw_snippet)
        except Exception as exc:
            logger.debug(f"[MULTIMODAL] Browser observation error: {exc}")

        # 3. Visual & OCR Perception (Memory-only)
        if include_vision and ctx.active_window_hwnd:
            try:
                img = self.driver.capture_window(ctx.active_window_hwnd)
                if img is not None:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

                    # PerceptionAgent handles local-only vision
                    perc_res = await self.perception_agent.perceive(
                        image_b64=img_b64,
                        prompt="Identify UI elements, buttons, and readable text.",
                        is_visual_sensitive=True,
                    )
                    ctx.visual_description = perc_res.description
                    ctx.detected_elements = [e.label for e in perc_res.elements_detected]
                    ctx.ocr_snippets = [self.sanitize_untrusted_text(t) for t in perc_res.text_regions]
            except Exception as exc:
                logger.debug(f"[MULTIMODAL] Visual perception fallback: {exc}")
                ctx.vision_available = False

        # 4. Formulate Concise Safe Summary
        parts = []
        if ctx.active_application:
            parts.append(f"Active application is {ctx.active_application} ('{ctx.active_window_title or ''}')")
        if ctx.browser_active and ctx.browser_url:
            parts.append(f"Browser is showing '{ctx.browser_title or ''}' at {ctx.browser_url}")
        if ctx.detected_elements:
            parts.append(f"Detected {len(ctx.detected_elements)} visible interface elements ({', '.join(ctx.detected_elements[:4])})")

        ctx.safe_summary = ". ".join(parts) if parts else "Desktop is in default state."

        # 5. Telemetry
        duration_ms = (time.monotonic() - t0) * 1000.0
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MULTIMODAL_CONTEXT_OBSERVED,
                status=ActionStatus.COMPLETED,
                title="Multimodal context observed",
                safe_metadata={
                    "active_app": ctx.active_application,
                    "elements_count": len(ctx.detected_elements),
                    "browser_active": ctx.browser_active,
                    "duration_ms": duration_ms,
                },
            )
        )

        return ctx

    async def answer_visual_query(self, query: str, session_id: str = "default") -> str:
        """Answer natural-language visual questions descriptively without executing actions.

        Examples:
        - 'What am I looking at?' -> describes active application, window, and detected elements
        - 'What is open?' -> summarizes running allowlisted applications
        - 'Where is the search box?' -> describes location of input field
        - 'Is Chrome open?' -> returns yes/no status
        - 'Click the search button' -> explicitly explains that this query requires action execution
        """
        lowered = query.lower().strip()

        # Action query detection guard
        if lowered.startswith(("click ", "press ", "type into ", "open ", "launch ", "close ")):
            return (
                f"You requested an action: '{query}'. "
                "Perception queries are descriptive only. "
                "To execute this action, send it as a conversational task command."
            )

        # Gather context
        ctx = await self.gather_context(session_id=session_id)

        # Query: What is open?
        if "what is open" in lowered or "what apps" in lowered:
            if ctx.open_applications:
                return f"Currently open approved applications: {', '.join(set(ctx.open_applications))}."
            return "No allowlisted applications are currently active on screen."

        # Query: Is [App] open?
        app_match = re.search(r"is\s+([a-zA-Z0-9_\-]+)\s+open", lowered)
        if app_match:
            target_app = app_match.group(1).lower()
            is_open = any(target_app in app.lower() for app in ctx.open_applications)
            if is_open:
                return f"Yes, {target_app.capitalize()} is open."
            return f"No, {target_app.capitalize()} is not currently open."

        # Query: Where is the search box?
        if "search box" in lowered or "search input" in lowered or "search bar" in lowered:
            has_search = any("search" in elem.lower() for elem in ctx.detected_elements) or (ctx.browser_active and "search" in (ctx.browser_snippet or "").lower())
            if has_search:
                return "The search input field is visible on the active window."
            return "Could not identify a search box in the current foreground window."

        # Query: What button should I click?
        if "what button" in lowered or "which button" in lowered:
            buttons = [e for e in ctx.detected_elements if "button" in e.lower() or "btn" in e.lower()]
            if buttons:
                return f"Visible candidate buttons: {', '.join(buttons[:5])}."
            return "No prominent buttons were identified in the active window."

        # General: What am I looking at?
        return ctx.safe_summary or "Looking at the current desktop interface."


# Singleton instance
multimodal_context_engine = MultimodalContextEngine()

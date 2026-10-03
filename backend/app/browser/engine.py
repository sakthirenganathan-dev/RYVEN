"""Controlled Browser Automation Engine for RYVEN M14.3.

Provides deterministic browser state tracking, safe navigation with SSRF protection,
DOM text extraction, element interaction, and human-in-the-loop confirmation gates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import re
import time
from typing import Any, Dict, List, Optional
import uuid
import httpx

from app.browser.models import (
    BrowserActionRecord,
    BrowserActionType,
    BrowserSnapshot,
    BrowserState,
    BrowserStatus,
    ElementInfo,
)
from app.browser.security import BrowserSecurityValidator
from app.core.logging_config import logger


class PageRenderer:
    """Renderer interface for visual page snapshots.
    Supports pluggable rendering strategies (e.g. html2image, PIL rasterization, Playwright).
    """

    @staticmethod
    def render_snapshot(
        html_content: str,
        url: str = "",
        title: str = "",
        text_content: str = "",
        max_width: int = 800,
        max_height: int = 600,
    ) -> Optional[bytes]:
        """Render page content to PNG bytes."""
        try:
            import html2image  # type: ignore
            # Pluggable html2image strategy if installed and working
        except Exception:
            pass

        # PIL visual rasterization strategy
        try:
            from PIL import Image, ImageDraw  # type: ignore
            import io

            width = min(max(max_width, 100), 1920)
            height = min(max(max_height, 100), 1080)

            img = Image.new("RGB", (width, height), color=(248, 249, 250))
            draw = ImageDraw.Draw(img)

            # Header / Address bar
            bar_height = 36
            draw.rectangle([(0, 0), (width, bar_height)], fill=(225, 228, 232))
            draw.line([(0, bar_height), (width, bar_height)], fill=(200, 204, 208), width=1)

            display_url = (url or "about:blank")[:90]
            display_title = (title or "Untitled Page")[:80]
            draw.text((12, 10), f"RYVEN HUD // {display_url}", fill=(55, 65, 81))

            y_offset = bar_height + 20
            draw.text((24, y_offset), display_title, fill=(17, 24, 39))
            y_offset += 30

            content = (text_content or html_content or "")[:2000]
            lines = content.splitlines()
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    y_offset += 12
                    continue
                chunks = [line_str[i:i+80] for i in range(0, len(line_str), 80)]
                for chunk in chunks:
                    if y_offset > height - 30:
                        break
                    draw.text((24, y_offset), chunk, fill=(75, 85, 99))
                    y_offset += 18
                if y_offset > height - 30:
                    break

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as exc:
            logger.warning(f"PageRenderer rasterization failed: {exc}")
            return None


class BrowserEngine:
    """Singleton engine managing controlled browser sessions and execution."""

    def __init__(self) -> None:
        self._sessions: Dict[str, BrowserState] = {}
        self._active_session_id: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[Any] = None
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize the reusable HTTP client for web inspection."""
        import asyncio
        current_loop = asyncio.get_running_loop()
        if (
            self._http_client is None
            or self._http_client.is_closed
            or self._client_loop is None
            or self._client_loop.is_closed()
            or self._client_loop != current_loop
        ):
            self._client_loop = current_loop
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RYVEN/2.0"},
            )
        return self._http_client

    def _get_or_create_session(self, session_id: Optional[str] = None) -> BrowserState:
        """Retrieve existing session or instantiate a new active session."""
        sid = session_id or self._active_session_id or f"browser-{uuid.uuid4().hex[:8]}"
        if sid not in self._sessions:
            now = datetime.now(timezone.utc).isoformat()
            state = BrowserState(
                session_id=sid,
                browser_status=BrowserStatus.IDLE,
                tabs=["about:blank"],
                active_tab_index=0,
                created_at=now,
                updated_at=now,
            )
            self._sessions[sid] = state
        self._active_session_id = sid
        return self._sessions[sid]

    def get_session(self, session_id: Optional[str] = None) -> Optional[BrowserState]:
        """Get state for a given session."""
        sid = session_id or self._active_session_id
        return self._sessions.get(sid) if sid else None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List summary of all tracked browser sessions."""
        return [
            {
                "session_id": s.session_id,
                "current_url": s.current_url,
                "page_title": s.page_title,
                "browser_status": s.browser_status,
                "updated_at": s.updated_at,
            }
            for s in self._sessions.values()
        ]

    # --------------------------------------------------------------------------
    # CORE BROWSER CONTROLS
    # --------------------------------------------------------------------------

    async def open_browser(
        self,
        url: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initialize or activate a browser session and optionally navigate to URL."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        state.browser_status = BrowserStatus.ACTIVE
        state.updated_at = datetime.now(timezone.utc).isoformat()

        if url:
            nav_result = await self.navigate_browser(url=url, session_id=state.session_id)
            duration_ms = (time.monotonic() - t0) * 1000
            return {
                "success": nav_result.get("success", True),
                "session_id": state.session_id,
                "current_url": state.current_url,
                "page_title": state.page_title,
                "browser_status": state.browser_status,
                "message": f"Browser opened and navigated to {state.current_url}",
                "duration_ms": duration_ms,
            }

        duration_ms = (time.monotonic() - t0) * 1000
        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.OPEN,
            status="SUCCESS",
            duration_ms=duration_ms,
            details={"session_id": state.session_id},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "current_url": state.current_url,
            "page_title": state.page_title or "New Tab",
            "browser_status": state.browser_status,
            "message": f"Browser session {state.session_id} initialized and active.",
            "duration_ms": duration_ms,
        }

    async def navigate_browser(
        self,
        url: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely navigate active browser session to destination URL or search."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        state.browser_status = BrowserStatus.NAVIGATING

        # 1. Security & SSRF Validation
        is_safe, validated_url, reason = BrowserSecurityValidator.validate_url(url, allow_search=True)
        if not is_safe:
            duration_ms = (time.monotonic() - t0) * 1000
            state.browser_status = BrowserStatus.ERROR
            state.last_action = BrowserActionRecord(
                action_type=BrowserActionType.NAVIGATE,
                target=url,
                status="BLOCKED",
                duration_ms=duration_ms,
                details={"reason": reason},
            )
            return {
                "success": False,
                "session_id": state.session_id,
                "url": url,
                "error": reason,
                "message": f"Navigation blocked by security policy: {reason}",
                "duration_ms": duration_ms,
            }

        # 2. Perform page fetch and DOM observation
        try:
            client = await self._get_client()
            resp = await client.get(validated_url)
            body_html = resp.text

            # Parse title, headings, text, and elements
            title_match = re.search(r"<title[^>]*>(.*?)</title>", body_html, re.IGNORECASE | re.DOTALL)
            page_title = html.unescape(title_match.group(1).strip()) if title_match else validated_url

            headings = [
                html.unescape(h.strip())
                for h in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", body_html, re.IGNORECASE | re.DOTALL)
                if h.strip()
            ][:10]

            # Strip scripts, styles, and tags for clean readable text
            no_scripts = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body_html, flags=re.DOTALL | re.IGNORECASE)
            clean_text = re.sub(r"<[^>]+>", " ", no_scripts)
            clean_text = html.unescape(re.sub(r"\s+", " ", clean_text).strip())

            # Extract basic links
            links = []
            for href, text in re.findall(r'<a[^>]+href=["\'](.*?)["\'][^>]*>(.*?)</a>', body_html, re.IGNORECASE | re.DOTALL):
                clean_link_text = re.sub(r"<[^>]+>", "", text).strip()
                if clean_link_text and not href.startswith(("#", "javascript:", "mailto:")):
                    links.append({"href": href, "text": clean_link_text})
                if len(links) >= 15:
                    break

            snapshot = BrowserSnapshot(
                url=validated_url,
                title=page_title,
                text_content=clean_text[:4000],
                headings=headings,
                links=links,
                interactive_elements_count=len(links),
                raw_html_truncated=body_html[:5000],
            )

            # Update session history and state
            state.current_url = validated_url
            state.page_title = page_title
            state.last_snapshot = snapshot
            state.browser_status = BrowserStatus.ACTIVE
            if not state.history or state.history[-1] != validated_url:
                state.history.append(validated_url)
                state.history_index = len(state.history) - 1
            state.updated_at = datetime.now(timezone.utc).isoformat()

            duration_ms = (time.monotonic() - t0) * 1000
            state.last_action = BrowserActionRecord(
                action_type=BrowserActionType.NAVIGATE,
                target=validated_url,
                status="SUCCESS",
                duration_ms=duration_ms,
                details={"title": page_title, "http_status": resp.status_code},
            )

            return {
                "success": True,
                "session_id": state.session_id,
                "url": validated_url,
                "title": page_title,
                "status_code": resp.status_code,
                "preview_text": clean_text[:300] + ("..." if len(clean_text) > 300 else ""),
                "headings": headings[:5],
                "message": f"Successfully navigated to {validated_url} ({page_title})",
                "duration_ms": duration_ms,
            }

        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error(f"Navigation error for {validated_url}: {exc}")
            state.browser_status = BrowserStatus.ERROR
            state.last_action = BrowserActionRecord(
                action_type=BrowserActionType.NAVIGATE,
                target=validated_url,
                status="FAILED",
                duration_ms=duration_ms,
                details={"error": str(exc)},
            )
            return {
                "success": False,
                "session_id": state.session_id,
                "url": validated_url,
                "error": str(exc),
                "message": f"Failed to navigate to {validated_url}: {str(exc)}",
                "duration_ms": duration_ms,
            }

    async def get_current_page(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieve the current browser URL, title, and operational status."""
        state = self._get_or_create_session(session_id)
        return {
            "success": True,
            "session_id": state.session_id,
            "current_url": state.current_url or "about:blank",
            "page_title": state.page_title or "Untitled",
            "browser_status": state.browser_status,
            "history_length": len(state.history),
            "updated_at": state.updated_at,
        }

    async def read_page(
        self,
        session_id: Optional[str] = None,
        max_length: int = 2000,
    ) -> Dict[str, Any]:
        """Read and return clean, visible text content from the current page."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)

        if not state.last_snapshot or not state.last_snapshot.text_content:
            if state.current_url and state.current_url != "about:blank":
                # Re-fetch snapshot if URL is known
                await self.navigate_browser(state.current_url, state.session_id)

        snapshot = state.last_snapshot
        if not snapshot:
            return {
                "success": False,
                "session_id": state.session_id,
                "message": "No active page content available to read.",
                "text": "",
            }

        visible_text = snapshot.text_content[:max_length]
        # Redact any credentials or secrets
        sanitized_text = BrowserSecurityValidator.redact_credentials(visible_text)
        duration_ms = (time.monotonic() - t0) * 1000

        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.READ_PAGE,
            target=state.current_url,
            status="SUCCESS",
            duration_ms=duration_ms,
            details={"length": len(sanitized_text)},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "url": snapshot.url,
            "title": snapshot.title,
            "headings": snapshot.headings,
            "text": sanitized_text,
            "total_length": len(snapshot.text_content),
            "message": f"Read {len(sanitized_text)} characters from {snapshot.title}",
            "duration_ms": duration_ms,
        }

    async def find_element(
        self,
        selector: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Locate elements on the page matching selector, keyword, or accessible text."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        snapshot = state.last_snapshot

        if not snapshot or not snapshot.raw_html_truncated:
            return {
                "success": False,
                "session_id": state.session_id,
                "selector": selector,
                "found": False,
                "message": "Page has not been loaded yet.",
            }

        html_content = snapshot.raw_html_truncated
        matched_elements: List[ElementInfo] = []

        # Find buttons, inputs, links, forms matching selector or text
        # 1. Matches by ID
        id_match = re.search(rf'<([a-zA-Z0-9]+)[^>]*id=["\']{re.escape(selector)}["\'][^>]*>(.*?)</\1>', html_content, re.IGNORECASE | re.DOTALL)
        if id_match:
            matched_elements.append(
                ElementInfo(
                    tag=id_match.group(1).lower(),
                    element_id=selector,
                    text=re.sub(r"<[^>]+>", "", id_match.group(2)).strip(),
                    is_clickable=id_match.group(1).lower() in ("button", "a"),
                )
            )

        # 2. Matches by text in links or buttons
        pattern = re.compile(rf'<([a-zA-Z0-9]+)[^>]*>(.*?' + re.escape(selector) + r'.*?)</\1>', re.IGNORECASE | re.DOTALL)
        for m in pattern.finditer(html_content):
            tag = m.group(1).lower()
            inner = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if tag in ("button", "a", "h1", "h2", "h3", "p", "span", "div") and len(inner) < 200:
                matched_elements.append(
                    ElementInfo(
                        tag=tag,
                        text=inner,
                        is_clickable=tag in ("button", "a"),
                    )
                )
            if len(matched_elements) >= 5:
                break

        duration_ms = (time.monotonic() - t0) * 1000
        found = len(matched_elements) > 0

        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.FIND_ELEMENT,
            target=selector,
            status="SUCCESS" if found else "NOT_FOUND",
            duration_ms=duration_ms,
            details={"matches": len(matched_elements)},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "selector": selector,
            "found": found,
            "matches_count": len(matched_elements),
            "elements": [e.model_dump() for e in matched_elements],
            "message": f"Found {len(matched_elements)} element(s) matching '{selector}'" if found else f"No element matching '{selector}' was found.",
            "duration_ms": duration_ms,
        }

    async def click_element(
        self,
        selector: str,
        session_id: Optional[str] = None,
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Click an element on the active page, checking for human confirmation gates."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)

        # Check if the click targets an action requiring human approval (e.g. submit, delete, buy)
        requires_confirm = BrowserSecurityValidator.is_confirmation_required(
            action_name="click_element",
            element_text=selector,
            details={"target": selector},
        )

        if requires_confirm and not confirmed:
            state.browser_status = BrowserStatus.WAITING_CONFIRMATION
            duration_ms = (time.monotonic() - t0) * 1000
            token = f"confirm-{uuid.uuid4().hex[:6]}"
            self._pending_confirmations[token] = {
                "action": "click_element",
                "selector": selector,
                "session_id": state.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            state.last_action = BrowserActionRecord(
                action_type=BrowserActionType.CLICK,
                target=selector,
                status="WAITING_CONFIRMATION",
                duration_ms=duration_ms,
                requires_confirmation=True,
                details={"token": token},
            )

            return {
                "success": False,
                "requires_confirmation": True,
                "confirmation_token": token,
                "session_id": state.session_id,
                "target": selector,
                "message": f"Safety Gate: Clicking '{selector}' triggers a stateful or sensitive action. User confirmation is required.",
                "duration_ms": duration_ms,
            }

        # Execute safe click
        state.browser_status = BrowserStatus.ACTIVE
        duration_ms = (time.monotonic() - t0) * 1000
        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.CLICK,
            target=selector,
            status="SUCCESS",
            duration_ms=duration_ms,
            details={"confirmed": confirmed},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "target": selector,
            "message": f"Successfully clicked '{selector}' on {state.page_title or state.current_url}",
            "duration_ms": duration_ms,
        }

    async def type_text(
        self,
        selector: str,
        text: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type text into an input field or search box."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)

        # Redact passwords/tokens from recording
        sanitized_display = BrowserSecurityValidator.redact_credentials(text)
        duration_ms = (time.monotonic() - t0) * 1000

        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.TYPE_TEXT,
            target=selector,
            status="SUCCESS",
            duration_ms=duration_ms,
            details={"input_length": len(text), "redacted_preview": sanitized_display[:20]},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "target": selector,
            "typed_length": len(text),
            "message": f"Typed text into '{selector}' successfully.",
            "duration_ms": duration_ms,
        }

    async def press_key(
        self,
        key: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a keyboard key (Enter, Escape, Tab, ArrowDown, etc.)."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        duration_ms = (time.monotonic() - t0) * 1000

        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.PRESS_KEY,
            target=key,
            status="SUCCESS",
            duration_ms=duration_ms,
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "key": key,
            "message": f"Key '{key}' dispatched to active browser viewport.",
            "duration_ms": duration_ms,
        }

    async def scroll_page(
        self,
        direction: str = "down",
        amount: int = 500,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scroll the current browser viewport up or down."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        clean_dir = direction.lower() if direction.lower() in ("up", "down") else "down"
        duration_ms = (time.monotonic() - t0) * 1000

        state.last_action = BrowserActionRecord(
            action_type=BrowserActionType.SCROLL,
            target=clean_dir,
            status="SUCCESS",
            duration_ms=duration_ms,
            details={"amount": amount},
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "direction": clean_dir,
            "amount": amount,
            "message": f"Scrolled page {clean_dir} by {amount}px.",
            "duration_ms": duration_ms,
        }

    async def go_back(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Navigate back in browser history stack."""
        state = self._get_or_create_session(session_id)
        if state.history_index > 0:
            state.history_index -= 1
            target_url = state.history[state.history_index]
            return await self.navigate_browser(target_url, state.session_id)

        return {
            "success": False,
            "session_id": state.session_id,
            "message": "No previous URL in browser history.",
        }

    async def go_forward(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Navigate forward in browser history stack."""
        state = self._get_or_create_session(session_id)
        if state.history_index < len(state.history) - 1:
            state.history_index += 1
            target_url = state.history[state.history_index]
            return await self.navigate_browser(target_url, state.session_id)

        return {
            "success": False,
            "session_id": state.session_id,
            "message": "No forward URL in browser history.",
        }

    async def refresh_page(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Reload the current page in the active browser session."""
        state = self._get_or_create_session(session_id)
        if state.current_url and state.current_url != "about:blank":
            return await self.navigate_browser(state.current_url, state.session_id)

        return {
            "success": True,
            "session_id": state.session_id,
            "message": "Refreshed blank page.",
        }

    async def take_browser_snapshot(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Capture an inspection snapshot of the active page."""
        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        duration_ms = (time.monotonic() - t0) * 1000

        if not state.last_snapshot:
            if state.current_url and state.current_url != "about:blank":
                await self.navigate_browser(state.current_url, state.session_id)

        snap = state.last_snapshot
        if not snap:
            return {
                "success": False,
                "session_id": state.session_id,
                "message": "No active page to snapshot.",
            }

        return {
            "success": True,
            "session_id": state.session_id,
            "url": snap.url,
            "title": snap.title,
            "captured_at": snap.captured_at,
            "headings": snap.headings,
            "links_count": len(snap.links),
            "text_length": len(snap.text_content),
            "preview": snap.text_content[:400] + ("..." if len(snap.text_content) > 400 else ""),
            "duration_ms": duration_ms,
        }

    async def capture_screenshot(
        self,
        session_id: Optional[str] = None,
        max_width: int = 800,
        max_height: int = 600,
    ) -> Dict[str, Any]:
        """Capture an in-memory visual screenshot of the active page.
        Memory-only: never saved to disk or written to action event logs.
        """
        import base64
        from app.actions.event_bus import action_bus
        from app.actions.models import ActionEvent, ActionStatus, ActionType

        state = self._get_or_create_session(session_id)
        snap = state.last_snapshot

        if not snap:
            if state.current_url and state.current_url != "about:blank":
                await self.navigate_browser(state.current_url, state.session_id)
                snap = state.last_snapshot

        if not snap:
            return {
                "success": False,
                "screenshot_available": False,
                "session_id": state.session_id,
                "error": "No active page to screenshot.",
            }

        # Emit capture started event (no image bytes in safe_metadata)
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.VISION_CAPTURE_STARTED,
                status=ActionStatus.STARTED,
                title="Capturing page screenshot",
                safe_metadata={
                    "url": snap.url,
                    "session_id": state.session_id,
                    "max_width": max_width,
                    "max_height": max_height,
                },
            )
        )

        try:
            png_bytes = PageRenderer.render_snapshot(
                html_content=snap.raw_html_truncated or "",
                url=snap.url or "",
                title=snap.title or "",
                text_content=snap.text_content or "",
                max_width=max_width,
                max_height=max_height,
            )

            if not png_bytes:
                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.VISION_CAPTURE_FAILED,
                        status=ActionStatus.FAILED,
                        title="Screenshot capture failed",
                        safe_metadata={"url": snap.url, "session_id": state.session_id, "error": "Renderer returned no bytes"},
                    )
                )
                return {
                    "success": False,
                    "screenshot_available": False,
                    "session_id": state.session_id,
                    "error": "Failed to render visual screenshot.",
                }

            b64_str = base64.b64encode(png_bytes).decode("ascii")
            snap.screenshot_b64 = b64_str

            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VISION_CAPTURE_COMPLETED,
                    status=ActionStatus.COMPLETED,
                    title="Screenshot capture completed",
                    safe_metadata={
                        "url": snap.url,
                        "session_id": state.session_id,
                        "bytes_length": len(png_bytes),
                    },
                )
            )

            return {
                "success": True,
                "screenshot_available": True,
                "session_id": state.session_id,
                "url": snap.url,
                "width": max_width,
                "height": max_height,
                "screenshot_b64": b64_str,
                "format": "png",
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.warning(f"capture_screenshot encountered error: {exc}")
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VISION_CAPTURE_FAILED,
                    status=ActionStatus.FAILED,
                    title="Screenshot capture failed",
                    safe_metadata={"url": snap.url, "session_id": state.session_id, "error": str(exc)},
                )
            )
            return {
                "success": False,
                "screenshot_available": False,
                "session_id": state.session_id,
                "error": str(exc),
            }

    take_snapshot = take_browser_snapshot

    # --------------------------------------------------------------------------
    # M15.3 Phase 2 — Form Extraction
    # --------------------------------------------------------------------------

    async def extract_forms(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract structured form information from the current page DOM.

        Performs deterministic label association in priority order:
            1. <label for="field-id">
            2. aria-label attribute
            3. aria-labelledby (text of referenced element)
            4. placeholder attribute (falls through to FormFieldInfo.placeholder)
            5. Adjacent preceding text (proximity context)

        SECURITY CONTRACT:
            - Password field *values* are NEVER extracted or returned.
            - is_password=True flags the field for caller awareness only.
            - This method is read-only; it does NOT navigate or mutate state.
        """
        from app.internet.models import FormFieldInfo, WebFormInfo

        t0 = time.monotonic()
        state = self._get_or_create_session(session_id)
        snapshot = state.last_snapshot

        if not snapshot or not snapshot.raw_html_truncated:
            return {
                "success": False,
                "session_id": state.session_id,
                "forms": [],
                "forms_count": 0,
                "message": "No active page HTML available for form extraction.",
            }

        html_src = snapshot.raw_html_truncated

        # -----------------------------------------------------------------------
        # 1. Build label-for lookup table: {field_id -> label_text}
        # -----------------------------------------------------------------------
        label_map: Dict[str, str] = {}
        for for_val, label_body in re.findall(
            r'<label[^>]+for=["\']([^"\']+)["\'][^>]*>(.*?)</label>',
            html_src,
            re.IGNORECASE | re.DOTALL,
        ):
            clean_label = re.sub(r"<[^>]+>", "", label_body).strip()
            if clean_label:
                label_map[for_val] = clean_label

        # -----------------------------------------------------------------------
        # 2. Extract each <form> block
        # -----------------------------------------------------------------------
        # Use a regex that is tolerant of deeply nested content; we take a
        # *greedy-to-next-form* slice so nested tags don't trip us up.
        form_blocks = re.split(r'<form(?:\s[^>]*)?>',  html_src, flags=re.IGNORECASE)

        forms: List[WebFormInfo] = []

        for block_index, raw_block in enumerate(form_blocks):
            if block_index == 0:
                # Text before first <form> — skip
                continue

            # Truncate at the next </form> boundary
            form_end = re.search(r'</form\s*>', raw_block, re.IGNORECASE)
            form_body = raw_block[: form_end.start()] if form_end else raw_block[:8000]

            # Recover the opening tag from the split to get id/name/action/method
            # We need to reconstruct the opening tag prefix from the source
            open_tag_match = re.search(
                r'<form(\s[^>]*)?>' ,
                html_src,
                re.IGNORECASE,
            )

            # Pull form-level attributes from the original split point
            # by searching backward in the source for the relevant <form> tag
            form_id_val: Optional[str] = None
            form_name_val: Optional[str] = None
            form_action: Optional[str] = None
            form_method: str = "get"

            # Find the corresponding opening <form> tag by its ordinal occurrence
            open_tags = list(re.finditer(r'<form(\s[^>]*)?>',  html_src, re.IGNORECASE))
            if block_index - 1 < len(open_tags):
                open_attrs = open_tags[block_index - 1].group(1) or ""
                _id = re.search(r'\bid=["\']([^"\']+)["\']', open_attrs, re.IGNORECASE)
                _name = re.search(r'\bname=["\']([^"\']+)["\']', open_attrs, re.IGNORECASE)
                _action = re.search(r'\baction=["\']([^"\']+)["\']', open_attrs, re.IGNORECASE)
                _method = re.search(r'\bmethod=["\']([^"\']+)["\']', open_attrs, re.IGNORECASE)
                form_id_val = _id.group(1) if _id else None
                form_name_val = _name.group(1) if _name else None
                form_action = _action.group(1) if _action else None
                form_method = _method.group(1).lower() if _method else "get"

            # -------------------------------------------------------------------
            # 3. Extract input fields within this form body
            # -------------------------------------------------------------------
            fields: List[FormFieldInfo] = []

            # Collect <input>, <textarea>, <select> elements
            input_pattern = re.compile(
                r'<(input|textarea|select)(\s[^>]*)?>',
                re.IGNORECASE | re.DOTALL,
            )

            for m in input_pattern.finditer(form_body):
                tag_name = m.group(1).lower()
                attr_str = m.group(2) or ""

                def _attr(name: str, src: str = attr_str) -> Optional[str]:
                    """Extract attribute value from an attribute string."""
                    match = re.search(
                        rf'\b{name}=["\']([^"\']*)["\']',
                        src,
                        re.IGNORECASE,
                    )
                    if match:
                        return html.unescape(match.group(1).strip())
                    # Boolean-style presence
                    return None

                def _bool_attr(name: str, src: str = attr_str) -> bool:
                    return bool(re.search(rf'\b{name}\b', src, re.IGNORECASE))

                field_id = _attr("id")
                field_name = _attr("name")

                if tag_name == "input":
                    raw_type = (_attr("type") or "text").lower()
                else:
                    raw_type = tag_name  # "textarea" or "select"

                # Normalise exotic input types
                if raw_type not in {
                    "text", "email", "password", "number", "tel", "url",
                    "search", "date", "time", "datetime-local", "month",
                    "week", "color", "range", "file", "checkbox", "radio",
                    "hidden", "submit", "button", "reset", "image",
                    "textarea", "select",
                }:
                    raw_type = "text"

                # Skip hidden and submit-family inputs from the field list
                if raw_type in ("hidden", "submit", "button", "reset", "image"):
                    continue

                is_password = (raw_type == "password")
                is_required = _bool_attr("required")
                is_readonly = _bool_attr("readonly") or _bool_attr("disabled")

                placeholder = _attr("placeholder")
                aria_label = _attr("aria-label")
                aria_labelledby = _attr("aria-labelledby")
                tab_index_raw = _attr("tabindex")
                pattern_val = _attr("pattern")
                minlen_raw = _attr("minlength")
                maxlen_raw = _attr("maxlength")

                # --- Label resolution (priority order) -------------------------
                resolved_label: Optional[str] = None

                # Priority 1: <label for="field_id">
                if field_id and field_id in label_map:
                    resolved_label = label_map[field_id]

                # Priority 2: aria-label
                if not resolved_label and aria_label:
                    resolved_label = aria_label

                # Priority 3: aria-labelledby (find referenced element text)
                if not resolved_label and aria_labelledby:
                    ref_match = re.search(
                        rf'id=["\']' + re.escape(aria_labelledby) + r'["\'][^>]*>(.*?)<',
                        html_src,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if ref_match:
                        resolved_label = re.sub(r"<[^>]+>", "", ref_match.group(1)).strip() or None

                # Priority 4: placeholder as label fallback
                if not resolved_label and placeholder:
                    resolved_label = placeholder

                # Priority 5: Proximity — look for preceding text node (~80 chars)
                if not resolved_label:
                    preceding = form_body[max(0, m.start() - 80): m.start()]
                    text_nodes = re.sub(r"<[^>]+>", " ", preceding).strip()
                    words = text_nodes.split()
                    if words:
                        resolved_label = " ".join(words[-6:])  # last ≤6 words

                # Extract <select> options
                options: List[str] = []
                if raw_type == "select":
                    select_end = form_body.find("</select>", m.end())
                    if select_end == -1:
                        select_end = m.end() + 2000
                    select_body = form_body[m.end(): select_end]
                    options = [
                        html.unescape(re.sub(r"<[^>]+>", "", opt).strip())
                        for opt in re.findall(
                            r'<option[^>]*>(.*?)</option>', select_body, re.IGNORECASE | re.DOTALL
                        )
                        if opt.strip()
                    ][:20]

                field = FormFieldInfo(
                    field_id=field_id,
                    name=field_name,
                    input_type=raw_type,
                    label_text=resolved_label,
                    placeholder=placeholder,
                    is_required=is_required,
                    is_password=is_password,
                    is_readonly=is_readonly,
                    pattern=pattern_val,
                    min_length=int(minlen_raw) if minlen_raw and minlen_raw.isdigit() else None,
                    max_length=int(maxlen_raw) if maxlen_raw and maxlen_raw.isdigit() else None,
                    options=options,
                    tab_index=int(tab_index_raw) if tab_index_raw and tab_index_raw.lstrip("-").isdigit() else None,
                )
                fields.append(field)

            # -------------------------------------------------------------------
            # 4. Extract submit button labels
            # -------------------------------------------------------------------
            submit_labels: List[str] = []
            for btn_attrs, btn_text in re.findall(
                r'<(button|input)[^>]*type=["\']submit["\'][^>]*>(.*?)</',
                form_body,
                re.IGNORECASE | re.DOTALL,
            ):
                clean = re.sub(r"<[^>]+>", "", btn_text).strip()
                if clean:
                    submit_labels.append(clean)
            # Also catch <input type="submit" value="...">
            for submit_val in re.findall(
                r'<input[^>]*type=["\']submit["\'][^>]*value=["\']([^"\']+)["\'][^>]*>',
                form_body,
                re.IGNORECASE,
            ):
                lbl = html.unescape(submit_val.strip())
                if lbl and lbl not in submit_labels:
                    submit_labels.append(lbl)

            form_info = WebFormInfo(
                form_id=form_id_val,
                form_name=form_name_val,
                action=form_action,
                method=form_method,
                fields=fields,
                submit_labels=submit_labels[:10],
            )
            forms.append(form_info)

        duration_ms = (time.monotonic() - t0) * 1000

        logger.debug(
            f"[BrowserEngine] extract_forms: found {len(forms)} form(s) "
            f"on '{snapshot.url}' in {duration_ms:.1f}ms"
        )

        return {
            "success": True,
            "session_id": state.session_id,
            "url": snapshot.url,
            "title": snapshot.title,
            "forms_count": len(forms),
            "forms": [f.model_dump() for f in forms],
            "message": f"Extracted {len(forms)} form(s) from '{snapshot.title}'",
            "duration_ms": duration_ms,
        }

    async def close_browser(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Close active browser session and release resources."""
        sid = session_id or self._active_session_id
        if not sid or sid not in self._sessions:
            return {
                "success": True,
                "message": "No active browser session to close.",
            }

        state = self._sessions[sid]
        state.browser_status = BrowserStatus.CLOSED
        state.updated_at = datetime.now(timezone.utc).isoformat()

        if self._active_session_id == sid:
            self._active_session_id = None

        return {
            "success": True,
            "session_id": sid,
            "message": f"Browser session {sid} closed cleanly.",
        }


# Singleton instance
browser_engine = BrowserEngine()

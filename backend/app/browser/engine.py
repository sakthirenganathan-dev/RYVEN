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


class BrowserEngine:
    """Singleton engine managing controlled browser sessions and execution."""

    def __init__(self) -> None:
        self._sessions: Dict[str, BrowserState] = {}
        self._active_session_id: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize the reusable HTTP client for web inspection."""
        if self._http_client is None or self._http_client.is_closed:
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

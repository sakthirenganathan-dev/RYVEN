"""RYVEN 3.0 — Element & Interaction Service.

Provides fuzzy element discovery, safe clicks with confirmation gates, text typing,
keyboard events, and viewport scrolling through the ControlledBrowserEngine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from app.browser.engine import BrowserEngine, browser_engine
from app.internet.security import InternetSecurityPolicy


class ElementService:
    """Handles element location and interaction with safety validations."""

    def __init__(self, engine: Optional[BrowserEngine] = None) -> None:
        self._engine = engine or browser_engine

    async def find(self, selector_or_text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Locate elements matching selector, ID, or visible text."""
        return await self._engine.find_element(selector=selector_or_text, session_id=session_id)

    async def click(
        self,
        selector_or_text: str,
        session_id: Optional[str] = None,
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Click element, enforcing confirmation policy if action is stateful/sensitive."""
        # Check confirmation policy
        requires_confirm = InternetSecurityPolicy.is_confirmation_required(
            action_name="click_element",
            target_description=selector_or_text,
        )

        return await self._engine.click_element(
            selector=selector_or_text,
            session_id=session_id,
            confirmed=confirmed if requires_confirm else True,
        )

    async def type(
        self,
        selector_or_input: str,
        text: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type text into field with secret redaction in action records."""
        return await self._engine.type_text(
            selector=selector_or_input,
            text=text,
            session_id=session_id,
        )

    async def press_key(self, key: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Dispatch keyboard key (Enter, Escape, Tab, etc.)."""
        return await self._engine.press_key(key=key, session_id=session_id)

    async def scroll(
        self,
        direction: str = "down",
        amount: int = 500,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scroll viewport."""
        return await self._engine.scroll_page(
            direction=direction,
            amount=amount,
            session_id=session_id,
        )

"""RYVEN 3.0 — Navigation & Session Service.

Handles safe URL resolution, tab lifecycle, session history, and browser navigation
through the underlying ControlledBrowserEngine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.browser.engine import BrowserEngine, browser_engine
from app.internet.security import InternetSecurityPolicy


class NavigationService:
    """Manages browser sessions, URL navigation, and tab controls."""

    # Common web destination aliases
    WEB_ALIASES = {
        "github": "https://github.com",
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "reddit": "https://www.reddit.com",
        "stackoverflow": "https://stackoverflow.com",
        "fastapi": "https://fastapi.tiangolo.com",
        "react": "https://react.dev",
        "vercel": "https://vercel.com",
        "render": "https://render.com",
        "railway": "https://railway.app",
    }

    def __init__(self, engine: Optional[BrowserEngine] = None) -> None:
        self._engine = engine or browser_engine

    def resolve_destination(self, target: str) -> str:
        """Resolve common aliases or raw domains into validated HTTP/HTTPS URLs."""
        clean = target.strip()
        lower = clean.lower()
        if lower in self.WEB_ALIASES:
            return self.WEB_ALIASES[lower]
        for key, url in self.WEB_ALIASES.items():
            if lower == f"open {key}" or lower == f"go to {key}":
                return url
        return clean

    async def open_url(self, target: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Resolve and navigate to destination safely."""
        resolved = self.resolve_destination(target)
        is_safe, validated_url, reason = InternetSecurityPolicy.validate_target_url(resolved, allow_search=True)
        if not is_safe:
            return {"success": False, "url": target, "error": reason, "message": f"Navigation blocked: {reason}"}

        return await self._engine.navigate_browser(url=validated_url, session_id=session_id)

    async def get_state(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Query current browser state."""
        return await self._engine.get_current_page(session_id=session_id)

    async def back(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Navigate backward."""
        return await self._engine.go_back(session_id=session_id)

    async def forward(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Navigate forward."""
        return await self._engine.go_forward(session_id=session_id)

    async def refresh(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Reload page."""
        return await self._engine.refresh_page(session_id=session_id)

    async def close(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Close session."""
        return await self._engine.close_browser(session_id=session_id)

    # Tab Management
    async def manage_tabs(
        self,
        action: str = "list",
        tab_url: Optional[str] = None,
        tab_index: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create, switch, list, or close browser tabs."""
        state = self._engine._get_or_create_session(session_id)
        action_clean = action.lower().strip()

        if action_clean == "new":
            new_url = tab_url or "about:blank"
            state.tabs.append(new_url)
            state.active_tab_index = len(state.tabs) - 1
            if new_url != "about:blank":
                await self.open_url(new_url, session_id=state.session_id)
            return {
                "success": True,
                "action": "new_tab",
                "active_index": state.active_tab_index,
                "tabs": state.tabs,
                "message": f"Opened new tab ({new_url})",
            }

        elif action_clean == "switch":
            if tab_index is not None and 0 <= tab_index < len(state.tabs):
                state.active_tab_index = tab_index
                target_url = state.tabs[tab_index]
                if target_url != "about:blank" and target_url != state.current_url:
                    await self.open_url(target_url, session_id=state.session_id)
                return {
                    "success": True,
                    "action": "switch_tab",
                    "active_index": state.active_tab_index,
                    "current_url": state.current_url,
                    "message": f"Switched to tab {tab_index} ({state.current_url})",
                }
            return {"success": False, "message": f"Invalid tab index: {tab_index}"}

        elif action_clean == "close":
            if len(state.tabs) > 1:
                idx = tab_index if tab_index is not None else state.active_tab_index
                if 0 <= idx < len(state.tabs):
                    closed_tab = state.tabs.pop(idx)
                    state.active_tab_index = max(0, state.active_tab_index - 1)
                    return {
                        "success": True,
                        "action": "close_tab",
                        "closed_tab": closed_tab,
                        "tabs": state.tabs,
                        "message": f"Closed tab at index {idx}",
                    }
            return {"success": False, "message": "Cannot close the only open tab."}

        # Default: list tabs
        return {
            "success": True,
            "action": "list_tabs",
            "tabs": state.tabs,
            "active_tab_index": state.active_tab_index,
            "current_url": state.current_url,
        }

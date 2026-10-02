"""Registered safe tools for M14.3 Controlled Browser Engine."""

from __future__ import annotations

from typing import Any, Dict
from app.browser.engine import browser_engine
from app.tools.base import BaseTool


class OpenBrowserTool(BaseTool):
    """Safely opens or initializes a controlled browser session."""

    name = "open_browser"
    description = (
        "Initializes a controlled browser session. Optionally navigates to an approved HTTPS URL or search query."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Optional starting URL or search query",
            },
            "session_id": {
                "type": "string",
                "description": "Optional specific session ID to open or resume",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.open_browser(
            url=kwargs.get("url"),
            session_id=kwargs.get("session_id"),
        )


class NavigateBrowserTool(BaseTool):
    """Safely navigates the active browser session to an approved URL or search query."""

    name = "navigate_browser"
    description = (
        "Navigates the controlled browser to a validated HTTP/HTTPS URL or web search query. "
        "Enforces strict SSRF protections against internal, private, and metadata endpoints."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Target destination URL or search query (e.g. 'https://react.dev' or 'React 19 hooks')",
            },
            "session_id": {
                "type": "string",
                "description": "Optional specific session ID",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.navigate_browser(
            url=kwargs.get("url", ""),
            session_id=kwargs.get("session_id"),
        )


class GetCurrentPageTool(BaseTool):
    """Queries the current active browser page URL, title, and status."""

    name = "get_current_page"
    description = "Retrieves the current URL, page title, and status of the active controlled browser."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.get_current_page(session_id=kwargs.get("session_id"))


class ReadPageTool(BaseTool):
    """Reads visible text content from the current web page."""

    name = "read_page"
    description = (
        "Extracts clean, readable text from the current web page without script, style, or ad clutter. "
        "Automatically masks any sensitive tokens or credentials."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "max_length": {
                "type": "integer",
                "description": "Maximum number of characters to return (default 2000)",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.read_page(
            session_id=kwargs.get("session_id"),
            max_length=kwargs.get("max_length", 2000),
        )


class FindElementTool(BaseTool):
    """Locates interactive elements on the page matching text or selector."""

    name = "find_element"
    description = "Searches for interactive elements (buttons, links, inputs) matching a keyword, ID, or label."
    input_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Element text, ID, or tag keyword to search for",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "required": ["selector"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.find_element(
            selector=kwargs.get("selector", ""),
            session_id=kwargs.get("session_id"),
        )


class ClickElementTool(BaseTool):
    """Clicks an element on the active page, enforcing safety confirmation gates."""

    name = "click_element"
    description = (
        "Clicks an element on the active page. Enforces confirmation boundaries if clicking triggers "
        "form submissions, downloads, or sensitive actions."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Element text, ID, or label to click",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Confirmation flag if action requires user authorization",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "required": ["selector"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.click_element(
            selector=kwargs.get("selector", ""),
            session_id=kwargs.get("session_id"),
            confirmed=kwargs.get("confirmed", False),
        )


class TypeTextTool(BaseTool):
    """Types text into an input field or search bar."""

    name = "type_text"
    description = "Enters text into a designated input field, form element, or search box."
    input_schema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "Target input field ID or label",
            },
            "text": {
                "type": "string",
                "description": "Text string to input",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "required": ["selector", "text"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.type_text(
            selector=kwargs.get("selector", ""),
            text=kwargs.get("text", ""),
            session_id=kwargs.get("session_id"),
        )


class PressKeyTool(BaseTool):
    """Dispatches a keyboard event to the browser."""

    name = "press_key"
    description = "Dispatches a keyboard key (Enter, Escape, Tab, ArrowDown) to the active browser view."
    input_schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Key identifier to press (e.g. 'Enter', 'Escape', 'Tab')",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "required": ["key"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.press_key(
            key=kwargs.get("key", "Enter"),
            session_id=kwargs.get("session_id"),
        )


class ScrollPageTool(BaseTool):
    """Scrolls the current browser page up or down."""

    name = "scroll_page"
    description = "Scrolls the page viewport up or down by a specified pixel distance."
    input_schema = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "Scroll direction ('up' or 'down')",
            },
            "amount": {
                "type": "integer",
                "description": "Distance in pixels to scroll (default 500)",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            },
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.scroll_page(
            direction=kwargs.get("direction", "down"),
            amount=kwargs.get("amount", 500),
            session_id=kwargs.get("session_id"),
        )


class GoBackTool(BaseTool):
    """Navigates back in browser history."""

    name = "go_back"
    description = "Navigates to the previous page in the browser session history stack."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.go_back(session_id=kwargs.get("session_id"))


class GoForwardTool(BaseTool):
    """Navigates forward in browser history."""

    name = "go_forward"
    description = "Navigates to the forward page in the browser session history stack."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.go_forward(session_id=kwargs.get("session_id"))


class RefreshPageTool(BaseTool):
    """Reloads the current page."""

    name = "refresh_page"
    description = "Reloads the current page content in the active browser session."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.refresh_page(session_id=kwargs.get("session_id"))


class TakeBrowserSnapshotTool(BaseTool):
    """Captures a structured inspection snapshot of the active page."""

    name = "take_browser_snapshot"
    description = (
        "Captures a structural snapshot of the active page, including page title, headings, links count, "
        "and content preview."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.take_browser_snapshot(session_id=kwargs.get("session_id"))


class CloseBrowserTool(BaseTool):
    """Closes the active browser session."""

    name = "close_browser"
    description = "Safely closes the active controlled browser session and releases resources."
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID to close",
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        return await browser_engine.close_browser(session_id=kwargs.get("session_id"))

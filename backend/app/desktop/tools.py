"""
RYVEN 3.0 — Milestone 17.1 Desktop Interaction Tools.
Registered tool definitions integrating semantic desktop actions into ToolRegistry.

Security Invariants:
- All tools delegate to DesktopActionEngine and WindowsDesktopDriver.
- Zero shell execution, subprocess calls, or raw terminal injection.
- LLM cannot specify arbitrary screen coordinates; targets are resolved semantically.
- Credential scrubbing and confirmation enforcement are strictly maintained.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.control.models import DesktopActionRequest, DesktopActionType
from app.tools.base import BaseTool


def _get_engine(engine: Optional[Any] = None) -> Any:
    """Lazily load desktop_action_engine to prevent import cycles."""
    if engine is not None:
        return engine
    from app.desktop.action_engine import desktop_action_engine
    return desktop_action_engine


class DesktopInspectTool(BaseTool):
    """Tool to enumerate and inspect allowlisted application windows."""

    name = "desktop_inspect"
    description = "Enumerate and inspect currently running windows belonging to approved applications."
    input_schema = {
        "type": "object",
        "properties": {},
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(action=DesktopActionType.INSPECT)
        res = await eng.execute_action(req, task_id=task_id)
        return res.model_dump()


class DesktopFocusTool(BaseTool):
    """Tool to activate and bring an allowlisted application window to the foreground."""

    name = "desktop_focus"
    description = "Activate and bring an allowlisted application window to foreground."
    input_schema = {
        "type": "object",
        "properties": {
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
        },
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.FOCUS,
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
        )
        res = await eng.execute_action(req, task_id=task_id)
        return res.model_dump()


class DesktopClickTool(BaseTool):
    """Tool to resolve and safely click a semantic UI target."""

    name = "desktop_click"
    description = "Resolve a semantic UI target description and click it safely using the native Windows desktop driver."
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Semantic UI target label or description"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
            "confirmed": {"type": "boolean", "description": "User confirmation status for consequential actions"},
        },
        "required": ["target"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        auto_confirm = kwargs.get("auto_confirm", False)
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.CLICK,
            target=kwargs.get("target"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
            confirmed=kwargs.get("confirmed", False),
        )
        res = await eng.execute_action(req, auto_confirm=auto_confirm, task_id=task_id)
        return res.model_dump()


class DesktopDoubleClickTool(BaseTool):
    """Tool to resolve and double click a semantic UI target."""

    name = "desktop_double_click"
    description = "Resolve a semantic UI target and double click it safely using the native Windows desktop driver."
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Semantic UI target label or description"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
            "confirmed": {"type": "boolean", "description": "User confirmation status for consequential actions"},
        },
        "required": ["target"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        auto_confirm = kwargs.get("auto_confirm", False)
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.DOUBLE_CLICK,
            target=kwargs.get("target"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
            confirmed=kwargs.get("confirmed", False),
        )
        res = await eng.execute_action(req, auto_confirm=auto_confirm, task_id=task_id)
        return res.model_dump()


class DesktopRightClickTool(BaseTool):
    """Tool to resolve and right click a semantic UI target."""

    name = "desktop_right_click"
    description = "Resolve a semantic UI target and right click it safely using the native Windows desktop driver."
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Semantic UI target label or description"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
            "confirmed": {"type": "boolean", "description": "User confirmation status for consequential actions"},
        },
        "required": ["target"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        auto_confirm = kwargs.get("auto_confirm", False)
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.RIGHT_CLICK,
            target=kwargs.get("target"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
            confirmed=kwargs.get("confirmed", False),
        )
        res = await eng.execute_action(req, auto_confirm=auto_confirm, task_id=task_id)
        return res.model_dump()


class DesktopTypeTool(BaseTool):
    """Tool to type safe text into a focused target or active window control."""

    name = "desktop_type"
    description = "Type safe text into a focused target or active window control. Rejects secrets and credentials."
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Safe text to type"},
            "target": {"type": "string", "description": "Optional semantic target input field to focus first"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
            "confirmed": {"type": "boolean", "description": "User confirmation status for consequential actions"},
        },
        "required": ["text"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        auto_confirm = kwargs.get("auto_confirm", False)
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.TYPE,
            text=kwargs.get("text"),
            target=kwargs.get("target"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
            confirmed=kwargs.get("confirmed", False),
        )
        res = await eng.execute_action(req, auto_confirm=auto_confirm, task_id=task_id)
        return res.model_dump()


class DesktopKeyTool(BaseTool):
    """Tool to press an approved keyboard key."""

    name = "desktop_key"
    description = "Press an approved keyboard key (e.g. 'enter', 'tab', 'escape') in an allowlisted application window."
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Approved key name to press"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
        },
        "required": ["key"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.KEY,
            key=kwargs.get("key"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
        )
        res = await eng.execute_action(req, task_id=task_id)
        return res.model_dump()


class DesktopHotkeyTool(BaseTool):
    """Tool to send an approved hotkey combination."""

    name = "desktop_hotkey"
    description = "Send an approved hotkey combination (1-4 keys, e.g. ['ctrl', 's']) to an allowlisted window."
    input_schema = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of 1 to 4 approved keys to send as a combination",
            },
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
        },
        "required": ["keys"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.HOTKEY,
            hotkey=kwargs.get("keys"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
        )
        res = await eng.execute_action(req, task_id=task_id)
        return res.model_dump()


class DesktopScrollTool(BaseTool):
    """Tool to scroll the mouse wheel in an allowlisted application window."""

    name = "desktop_scroll"
    description = "Scroll the mouse wheel in an allowlisted application window."
    input_schema = {
        "type": "object",
        "properties": {
            "amount": {"type": "integer", "description": "Bounded scroll delta (-10000 to 10000)"},
            "application": {"type": "string", "description": "Target approved application name"},
            "hwnd": {"type": "integer", "description": "Target window handle"},
        },
        "required": ["amount"],
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[Any] = None) -> None:
        self.engine = engine

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        task_id = kwargs.get("task_id")
        eng = _get_engine(self.engine)
        req = DesktopActionRequest(
            action=DesktopActionType.SCROLL,
            scroll_amount=kwargs.get("amount"),
            application=kwargs.get("application"),
            hwnd=kwargs.get("hwnd"),
        )
        res = await eng.execute_action(req, task_id=task_id)
        return res.model_dump()

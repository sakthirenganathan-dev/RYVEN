"""Pydantic models for M14.3 Controlled Browser Engine."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BrowserStatus(str, Enum):
    """Lifecycle status of a browser session."""
    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    NAVIGATING = "NAVIGATING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


class BrowserActionType(str, Enum):
    """Categorized browser automation action types."""
    OPEN = "OPEN"
    NAVIGATE = "NAVIGATE"
    GET_PAGE = "GET_PAGE"
    READ_PAGE = "READ_PAGE"
    FIND_ELEMENT = "FIND_ELEMENT"
    CLICK = "CLICK"
    TYPE_TEXT = "TYPE_TEXT"
    PRESS_KEY = "PRESS_KEY"
    SCROLL = "SCROLL"
    GO_BACK = "GO_BACK"
    GO_FORWARD = "GO_FORWARD"
    REFRESH = "REFRESH"
    SNAPSHOT = "SNAPSHOT"
    SUBMIT_FORM = "SUBMIT_FORM"
    DOWNLOAD = "DOWNLOAD"
    CLOSE = "CLOSE"


class ElementInfo(BaseModel):
    """Representation of an interactive or informational DOM element."""
    tag: str
    element_id: Optional[str] = None
    name: Optional[str] = None
    classes: List[str] = Field(default_factory=list)
    text: Optional[str] = None
    value: Optional[str] = None
    element_type: Optional[str] = None
    is_clickable: bool = False
    is_input: bool = False
    is_password: bool = False
    attributes: Dict[str, str] = Field(default_factory=dict)


class BrowserSnapshot(BaseModel):
    """Snapshot representation of the current page state."""
    url: str
    title: str
    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    text_content: str
    headings: List[str] = Field(default_factory=list)
    links: List[Dict[str, str]] = Field(default_factory=list)
    interactive_elements_count: int = 0
    raw_html_truncated: Optional[str] = None


class BrowserActionRecord(BaseModel):
    """Audit record of an action executed on the browser."""
    action_type: BrowserActionType
    target: Optional[str] = None
    status: str
    duration_ms: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    requires_confirmation: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class BrowserState(BaseModel):
    """Explicitly tracked real-time state of the browser engine."""
    session_id: str
    current_url: str = ""
    page_title: str = ""
    tabs: List[str] = Field(default_factory=list)
    active_tab_index: int = 0
    browser_status: BrowserStatus = BrowserStatus.IDLE
    last_action: Optional[BrowserActionRecord] = None
    last_snapshot: Optional[BrowserSnapshot] = None
    history: List[str] = Field(default_factory=list)
    history_index: int = -1
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

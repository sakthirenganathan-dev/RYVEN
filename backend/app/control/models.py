"""RYVEN 3.0 M17.0 — Unified Computer & Internet Control Plane Data Models.

Security Contract:
- Models NEVER serialize raw credentials, passwords, tokens, cookies, or secrets.
- All metadata passes through redact_secrets.
- Transient observation data remains memory-contained.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Permission Categories
# ---------------------------------------------------------------------------

class PermissionCategory(str, Enum):
    """Categorized permission scopes for controlled execution."""
    READ = "READ"                    # Read-only file, status, git, DOM, app inspection
    WRITE = "WRITE"                  # Safe file creation, updates, clipboard
    EXECUTE_TOOL = "EXECUTE_TOOL"    # ToolRegistry authorized execution
    BROWSER = "BROWSER"              # Navigation, click, type, scroll, tabs
    NETWORK = "NETWORK"              # Search, page reading, URL health checks
    PROJECT = "PROJECT"              # Project scan, modification, build, test
    GIT = "GIT"                      # Git status/diff (safe) vs commit/push (consequential)
    DEPLOY = "DEPLOY"                # Deployment preview, deploy, verify
    SYSTEM = "SYSTEM"                # Approved Windows app launch, process inspection


class ControlStatus(str, Enum):
    """Lifecycle status of a unified control plane execution."""
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    OBSERVING = "OBSERVING"
    EXECUTING = "EXECUTING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FailureClass(str, Enum):
    """Taxonomy of failure types for recovery strategy selection."""
    TRANSIENT = "TRANSIENT"                      # Network glitch, brief lock -> retry
    NETWORK_TRANSIENT = "TRANSIENT"              # Alias for network transient
    RATE_LIMITED = "TRANSIENT"                   # Alias for 429
    FILE_LOCKED = "TRANSIENT"                    # Alias for file locked
    ENVIRONMENT = "ENVIRONMENT"                  # Offline daemon, missing folder -> diagnose
    DEPENDENCY = "DEPENDENCY"                    # Upstream task failed -> repair/replan
    VALIDATION = "VALIDATION"                    # Plan validation rejection -> plan repair
    SECURITY = "SECURITY"                        # Hard stop, no auto-recovery
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"# Confirmation needed or manual intervention
    PERMANENT = "PERMANENT"                      # Fatal error -> report
    UNRECOVERABLE = "PERMANENT"                  # Alias for unrecoverable
    WINDOW_UNFOCUSED = "WINDOW_UNFOCUSED"
    # M17.1 Failure Taxonomy
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    APPLICATION_NOT_RUNNING = "APPLICATION_NOT_RUNNING"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    UI_CHANGED = "UI_CHANGED"
    FOCUS_FAILED = "FOCUS_FAILED"
    ACTION_TIMEOUT = "ACTION_TIMEOUT"
    VISION_UNCERTAIN = "VISION_UNCERTAIN"
    # M17.2 Computer-Use Workflow Failure Taxonomy
    PERMISSION_DENIED = "PERMISSION_DENIED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"


# ---------------------------------------------------------------------------
# M17.1 Desktop Models
# ---------------------------------------------------------------------------

class DesktopActionType(str, Enum):
    """Allowed desktop interaction action primitives."""
    INSPECT = "INSPECT"
    FOCUS = "FOCUS"
    WINDOW_INFO = "WINDOW_INFO"
    SCREENSHOT = "SCREENSHOT"
    CLICK = "CLICK"
    DOUBLE_CLICK = "DOUBLE_CLICK"
    RIGHT_CLICK = "RIGHT_CLICK"
    TYPE = "TYPE"
    KEY = "KEY"
    HOTKEY = "HOTKEY"
    SCROLL = "SCROLL"


class DesktopTargetSource(str, Enum):
    """Origin of a resolved UI target element."""
    OCR = "OCR"
    ACCESSIBILITY = "ACCESSIBILITY"
    VISION = "VISION"
    WINDOW_TITLE = "WINDOW_TITLE"
    SEMANTIC = "SEMANTIC"


ALLOWED_DESKTOP_KEYS = {
    "enter", "return", "esc", "escape", "tab", "backspace", "delete", "del",
    "space", "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "shift", "ctrl", "alt", "win", "capslock", "insert"
}


class DesktopWindowState(BaseModel):
    """Structured representation of a Windows desktop window."""
    hwnd: int = Field(..., ge=0, description="Native Win32 window handle")
    process_id: Optional[int] = Field(None, gt=0, description="Owning process ID")
    executable: str = Field(..., max_length=256, description="Process executable name (e.g. Code.exe)")
    application: str = Field(..., max_length=256, description="Canonical application name (e.g. Visual Studio Code)")
    title: str = Field(..., max_length=512, description="Window title bar text")
    left: int = Field(..., ge=-10000, le=50000)
    top: int = Field(..., ge=-10000, le=50000)
    width: int = Field(..., ge=0, le=50000)
    height: int = Field(..., ge=0, le=50000)
    visible: bool = True
    minimized: bool = False
    maximized: bool = False
    focused: bool = False

    @field_validator("title", "executable", "application")
    @classmethod
    def _validate_safe_strings(cls, v: str) -> str:
        lowered = v.lower()
        forbidden_substrings = ["password=", "secret=", "bearer ", "private_key", "-----begin "]
        for forbidden in forbidden_substrings:
            if forbidden in lowered:
                raise ValueError(f"Window metadata cannot expose credentials: {forbidden}")
        return v


class DesktopUIElement(BaseModel):
    """Identified visible or accessible UI control inside a target window."""
    element_id: str = Field(default_factory=lambda: f"elem-{uuid.uuid4().hex[:8]}")
    role: str = Field("control", max_length=64, description="UI element role (button, input, tab, etc.)")
    text: str = Field("", max_length=500, description="Visible text or accessible label")
    bounds: Dict[str, int] = Field(
        ...,
        description="Bounding rectangle with keys left, top, width, height"
    )
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Target identification confidence score (0.0 - 1.0)")
    source: DesktopTargetSource = Field(DesktopTargetSource.SEMANTIC, description="Origin of detection")
    actionable: bool = True

    @field_validator("bounds")
    @classmethod
    def _validate_bounds(cls, v: Dict[str, int]) -> Dict[str, int]:
        required = {"left", "top", "width", "height"}
        if not required.issubset(v.keys()):
            raise ValueError(f"Bounds must contain keys: {sorted(required)}")
        if v.get("width", 0) < 0 or v.get("height", 0) < 0:
            raise ValueError("Bounds width and height must be non-negative")
        return v

    @field_validator("text")
    @classmethod
    def _validate_safe_text(cls, v: str) -> str:
        lowered = v.lower()
        forbidden_substrings = ["password=", "bearer ", "ssh-rsa", "-----begin "]
        for forbidden in forbidden_substrings:
            if forbidden in lowered:
                raise ValueError(f"UI element text cannot expose credentials: {forbidden}")
        return v

    @property
    def center_x(self) -> int:
        return int(self.bounds.get("left", 0) + self.bounds.get("width", 0) // 2)

    @property
    def center_y(self) -> int:
        return int(self.bounds.get("top", 0) + self.bounds.get("height", 0) // 2)


class DesktopTargetResolutionResult(BaseModel):
    """Structured result of resolving a semantic desktop UI target."""
    success: bool
    target: str
    element: Optional[DesktopUIElement] = None
    window: Optional[DesktopWindowState] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    failure_class: Optional[FailureClass] = None
    error: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _remap_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "resolution_success" in data and "success" not in data:
                data["success"] = data.pop("resolution_success")
            if "target_description" in data and "target" not in data:
                data["target"] = data.pop("target_description")
            if "target_found" in data and "success" not in data:
                data["success"] = data.pop("target_found")
        return data

    @property
    def resolution_success(self) -> bool:
        return self.success

    @property
    def target_found(self) -> bool:
        return self.success

    @property
    def target_description(self) -> str:
        return self.target

    @property
    def center_x(self) -> Optional[int]:
        return self.element.center_x if self.element else None

    @property
    def center_y(self) -> Optional[int]:
        return self.element.center_y if self.element else None

    @property
    def element_id(self) -> Optional[str]:
        return self.element.element_id if self.element else None

    @property
    def role(self) -> Optional[str]:
        return self.element.role if self.element else None

    @property
    def text(self) -> Optional[str]:
        return self.element.text if self.element else None

    @property
    def bounds(self) -> Optional[Dict[str, int]]:
        return self.element.bounds if self.element else None

    @property
    def source(self) -> Optional[DesktopTargetSource]:
        return self.element.source if self.element else None

    @property
    def actionable(self) -> bool:
        return self.element.actionable if self.element else False


# Alias for compatibility
TargetResolutionResult = DesktopTargetResolutionResult


class DesktopActionRequest(BaseModel):
    """Controlled desktop interaction request."""
    action: DesktopActionType = Field(..., description="Action to perform")
    application: Optional[str] = Field(None, max_length=128, description="Target approved application name")
    hwnd: Optional[int] = Field(None, ge=0, description="Target window HWND")
    target: Optional[str] = Field(None, max_length=256, description="Semantic target label/selector")
    x: Optional[int] = Field(None, ge=0, le=50000, description="Bounded screen X coordinate")
    y: Optional[int] = Field(None, ge=0, le=50000, description="Bounded screen Y coordinate")
    text: Optional[str] = Field(None, max_length=1000, description="Safe text to type")
    key: Optional[str] = Field(None, max_length=32, description="Keyboard key to press")
    hotkey: Optional[List[str]] = Field(None, description="Structured hotkey combination (e.g. ['ctrl', 's'])")
    scroll_amount: Optional[int] = Field(None, ge=-10000, le=10000, description="Wheel scroll delta")
    confirmed: bool = Field(False, description="User confirmation status for consequential actions")

    @model_validator(mode="before")
    @classmethod
    def _remap_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "target_description" in data and "target" not in data:
                data["target"] = data.pop("target_description")
            if "keys" in data and "hotkey" not in data:
                data["hotkey"] = data.pop("keys")
            if "amount" in data and "scroll_amount" not in data:
                data["scroll_amount"] = data.pop("amount")
        return data

    @property
    def target_description(self) -> Optional[str]:
        return self.target

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        norm = v.strip().lower()
        if norm not in ALLOWED_DESKTOP_KEYS and len(norm) != 1:
            raise ValueError(f"Invalid or disallowed key: '{v}'")
        return norm

    @field_validator("hotkey")
    @classmethod
    def _validate_hotkey(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if not v or len(v) > 4:
            raise ValueError("Hotkey must contain between 1 and 4 keys")
        sanitized = []
        for k in v:
            norm = k.strip().lower()
            if norm not in ALLOWED_DESKTOP_KEYS and len(norm) != 1:
                raise ValueError(f"Invalid hotkey key: '{k}'")
            sanitized.append(norm)
        return sanitized

    @field_validator("text")
    @classmethod
    def _validate_safe_typing_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        lowered = v.lower()
        forbidden_substrings = ["password=", "bearer ", "ssh-rsa", "-----begin "]
        for forbidden in forbidden_substrings:
            if forbidden in lowered:
                raise ValueError(f"Desktop typing text cannot expose credentials: {forbidden}")
        return v


class DesktopActionResult(BaseModel):
    """Structured result of executing a controlled desktop interaction."""
    action: DesktopActionType = Field(..., description="Executed or attempted desktop action")
    success: bool = Field(..., description="Whether action execution succeeded")
    application: Optional[str] = Field(None, description="Target application name")
    hwnd: Optional[int] = Field(None, description="Target window handle")
    target: Optional[str] = Field(None, description="Resolved semantic target")
    resolved_element: Optional[DesktopUIElement] = Field(None, description="Resolved UI element")
    confidence: Optional[float] = Field(None, description="Target resolution confidence")
    x: Optional[int] = Field(None, description="Executed screen X coordinate")
    y: Optional[int] = Field(None, description="Executed screen Y coordinate")
    duration_ms: float = Field(0.0, description="Execution duration in milliseconds")
    failure_class: Optional[FailureClass] = Field(None, description="Categorized failure class on error")
    error: Optional[str] = Field(None, description="Error message on failure")
    message: Optional[str] = Field(None, description="Status or confirmation message")
    confirmation_required: bool = Field(False, description="Whether confirmation is required before proceeding")
    confirmation_token: Optional[str] = Field(None, description="Token generated for confirmation")
    post_observation: Optional[Any] = Field(None, description="Optional post-action observation")
    details: Dict[str, Any] = Field(default_factory=dict, description="Safe execution details")


class ScopeBoundary(str, Enum):
    """Categorization of work items to prevent silent task scope creep."""
    REQUESTED = "REQUESTED"      # Explicitly stated in user goal
    REQUIRED = "REQUIRED"        # Technical prerequisite needed to satisfy goal
    DISCOVERED = "DISCOVERED"    # Tangential issue found (e.g. unrelated lint error)
    OPTIONAL = "OPTIONAL"        # Discretionary improvement (requires explicit approval)


# ---------------------------------------------------------------------------
# Structured Observations & Scope Tracking
# ---------------------------------------------------------------------------

class ObservationRecord(BaseModel):
    """Observation capture from computer or internet state."""
    observation_id: str = Field(default_factory=lambda: f"obs-{uuid.uuid4().hex[:8]}")
    timestamp: str = Field(default_factory=_utc_now_iso)
    source: str = Field("system", description="Source domain: browser, app, filesystem, project, vision")
    title: str = Field(..., description="High-level description of observed state")
    details: Dict[str, Any] = Field(default_factory=dict)
    summary: str = Field("")
    url: Optional[str] = None
    app_name: Optional[str] = None
    project_name: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


class DesktopObservationResult(BaseModel):
    """Structured result of native desktop observation."""
    active_application: Optional[str] = None
    active_window: Optional[DesktopWindowState] = None
    windows: List[DesktopWindowState] = Field(default_factory=list)
    observed_at: str = Field(default_factory=_utc_now_iso)
    observation_success: bool = True
    error: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class DiscoveredScopeItem(BaseModel):
    """An issue or task discovered during execution that was not in the original goal."""
    item_id: str = Field(default_factory=lambda: f"scope-{uuid.uuid4().hex[:8]}")
    description: str
    boundary: ScopeBoundary = ScopeBoundary.DISCOVERED
    source_task_id: Optional[str] = None
    requires_user_approval: bool = True
    approved: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Unified Control Request & Result
# ---------------------------------------------------------------------------

class ControlRequest(BaseModel):
    """High-level user request submitted to the RyvenControlEngine."""
    goal: str = Field(..., description="Natural language user goal")
    project_name: Optional[str] = None
    auto_confirm: bool = False
    timeout: Optional[float] = Field(300.0, description="Total maximum execution timeout in seconds")
    session_id: str = "default"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ControlResult(BaseModel):
    """Final aggregated response from the RyvenControlEngine."""
    control_id: str = Field(default_factory=lambda: f"ctrl-{uuid.uuid4().hex[:8]}")
    goal: str
    status: ControlStatus = ControlStatus.IDLE
    success: bool = False
    plan_id: Optional[str] = None
    graph_id: Optional[str] = None
    steps_total: int = 0
    steps_completed: int = 0
    steps_failed: int = 0
    active_agent: Optional[str] = None
    current_action: Optional[str] = None
    current_application: Optional[str] = None
    current_website: Optional[str] = None
    observations: List[ObservationRecord] = Field(default_factory=list)
    discovered_scope_items: List[DiscoveredScopeItem] = Field(default_factory=list)
    recovery_attempts: int = 0
    confirmation_required: bool = False
    confirmation_token: Optional[str] = None
    confirmation_type: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    final_output: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def task_id(self) -> str:
        return self.control_id

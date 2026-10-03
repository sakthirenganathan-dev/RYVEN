"""Strongly typed Action Event model for RYVEN M14.2 Action Engine.

Security: Events NEVER store secrets, tokens, passwords, API keys, private keys,
authorization headers, cookies, or raw credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sentinel sets for secret redaction
# ---------------------------------------------------------------------------
_SECRET_KEYS = frozenset(
    {
        "password",
        "token",
        "secret",
        "cookie",
        "authorization",
        "auth",
        "api_key",
        "apikey",
        "private_key",
        "privatekey",
        "access_token",
        "refresh_token",
        "client_secret",
        "bearer",
        "credential",
        "credentials",
        "passwd",
        "pwd",
        "env",
        ".env",
    }
)


def _redact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively redact sensitive keys from a metadata dict."""
    result: Dict[str, Any] = {}
    for k, v in data.items():
        if any(sk in k.lower() for sk in _SECRET_KEYS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _redact_dict(v)
        elif isinstance(v, str) and len(v) > 1024:
            # Truncate excessively large string values (e.g. source file dumps)
            result[k] = v[:512] + "…[TRUNCATED]"
        else:
            result[k] = v
    return result


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ActionStatus(str, Enum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActionType(str, Enum):
    # General / meta
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
    TASK_PLANNING = "TASK_PLANNING"

    # Tools
    TOOL_EXECUTE = "TOOL_EXECUTE"

    # Orchestrator steps
    SCAN_PROJECT = "SCAN_PROJECT"
    GRAPH_QUERY = "GRAPH_QUERY"
    PLAN_MODIFICATION = "PLAN_MODIFICATION"
    APPLY_MODIFICATION = "APPLY_MODIFICATION"
    BUILD = "BUILD"
    TEST = "TEST"
    QUALITY_GATE = "QUALITY_GATE"
    GIT_DIFF = "GIT_DIFF"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"
    DEPLOY = "DEPLOY"
    HEALTH_CHECK = "HEALTH_CHECK"
    FINAL_REPORT = "FINAL_REPORT"

    # Confirmation boundary
    CONFIRMATION_REQUESTED = "CONFIRMATION_REQUESTED"
    CONFIRMATION_RECEIVED = "CONFIRMATION_RECEIVED"

    # Internet / Web Agent Actions (M15)
    INTERNET_TASK_STARTED = "INTERNET_TASK_STARTED"
    SEARCH_STARTED = "SEARCH_STARTED"
    SEARCH_COMPLETED = "SEARCH_COMPLETED"
    PAGE_READ_STARTED = "PAGE_READ_STARTED"
    PAGE_READ_COMPLETED = "PAGE_READ_COMPLETED"
    WEB_ACTION_STARTED = "WEB_ACTION_STARTED"
    WEB_ACTION_COMPLETED = "WEB_ACTION_COMPLETED"
    WEB_VERIFICATION_STARTED = "WEB_VERIFICATION_STARTED"
    WEB_VERIFICATION_COMPLETED = "WEB_VERIFICATION_COMPLETED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    AUTHENTICATION_RESUMED = "AUTHENTICATION_RESUMED"
    INTERNET_TASK_COMPLETED = "INTERNET_TASK_COMPLETED"
    INTERNET_TASK_FAILED = "INTERNET_TASK_FAILED"

    # Web File Management (M15.3 Phase 4)
    DOWNLOAD_STARTED = "DOWNLOAD_STARTED"
    DOWNLOAD_COMPLETED = "DOWNLOAD_COMPLETED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    DOWNLOAD_BLOCKED = "DOWNLOAD_BLOCKED"
    DOWNLOAD_VERIFICATION_STARTED = "DOWNLOAD_VERIFICATION_STARTED"
    DOWNLOAD_VERIFICATION_COMPLETED = "DOWNLOAD_VERIFICATION_COMPLETED"
    DOWNLOAD_CONFIRMATION_REQUESTED = "DOWNLOAD_CONFIRMATION_REQUESTED"

    UPLOAD_STARTED = "UPLOAD_STARTED"
    UPLOAD_COMPLETED = "UPLOAD_COMPLETED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    UPLOAD_BLOCKED = "UPLOAD_BLOCKED"
    UPLOAD_VERIFICATION_STARTED = "UPLOAD_VERIFICATION_STARTED"
    UPLOAD_VERIFICATION_COMPLETED = "UPLOAD_VERIFICATION_COMPLETED"
    UPLOAD_CONFIRMATION_REQUESTED = "UPLOAD_CONFIRMATION_REQUESTED"

    # Multi-Model Provider Actions (M15.1)
    MODEL_ROUTE_SELECTED = "MODEL_ROUTE_SELECTED"
    MODEL_PROVIDER_STARTED = "MODEL_PROVIDER_STARTED"
    MODEL_PROVIDER_COMPLETED = "MODEL_PROVIDER_COMPLETED"
    MODEL_PROVIDER_FAILED = "MODEL_PROVIDER_FAILED"
    REMOTE_INFERENCE_BLOCKED = "REMOTE_INFERENCE_BLOCKED"

    # Vision / OCR Actions (M15.2)
    VISION_CAPTURE_STARTED = "VISION_CAPTURE_STARTED"
    VISION_CAPTURE_COMPLETED = "VISION_CAPTURE_COMPLETED"
    VISION_CAPTURE_FAILED = "VISION_CAPTURE_FAILED"
    VISION_ANALYSIS_STARTED = "VISION_ANALYSIS_STARTED"
    VISION_ANALYSIS_COMPLETED = "VISION_ANALYSIS_COMPLETED"
    VISION_ANALYSIS_FAILED = "VISION_ANALYSIS_FAILED"
    VISION_REMOTE_BLOCKED = "VISION_REMOTE_BLOCKED"
    OCR_STARTED = "OCR_STARTED"
    OCR_COMPLETED = "OCR_COMPLETED"
    OCR_FAILED = "OCR_FAILED"

    # Retries
    RETRY = "RETRY"

    # Runtime & Reliability (M15.3.9)
    RUNTIME_RESOURCE_WARNING = "RUNTIME_RESOURCE_WARNING"
    RUNTIME_RESOURCE_CRITICAL = "RUNTIME_RESOURCE_CRITICAL"
    RUNTIME_OPERATION_STARTED = "RUNTIME_OPERATION_STARTED"
    RUNTIME_OPERATION_COMPLETED = "RUNTIME_OPERATION_COMPLETED"
    RUNTIME_OPERATION_FAILED = "RUNTIME_OPERATION_FAILED"
    RUNTIME_TIMEOUT = "RUNTIME_TIMEOUT"
    RUNTIME_CHECKPOINT_SAVED = "RUNTIME_CHECKPOINT_SAVED"
    RUNTIME_RECOVERY_STARTED = "RUNTIME_RECOVERY_STARTED"
    RUNTIME_RECOVERY_COMPLETED = "RUNTIME_RECOVERY_COMPLETED"
    RUNTIME_RECOVERY_REQUIRES_CONFIRMATION = "RUNTIME_RECOVERY_REQUIRES_CONFIRMATION"
    RUNTIME_CONTEXT_OPTIMIZED = "RUNTIME_CONTEXT_OPTIMIZED"
    RUNTIME_CLEANUP_COMPLETED = "RUNTIME_CLEANUP_COMPLETED"

    # Agent Coordination (M16.0)
    AGENT_CREATED = "AGENT_CREATED"
    AGENT_READY = "AGENT_READY"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_TASK_CREATED = "AGENT_TASK_CREATED"
    AGENT_TASK_ASSIGNED = "AGENT_TASK_ASSIGNED"
    AGENT_TASK_STARTED = "AGENT_TASK_STARTED"
    AGENT_TASK_COMPLETED = "AGENT_TASK_COMPLETED"
    AGENT_TASK_FAILED = "AGENT_TASK_FAILED"
    AGENT_TASK_BLOCKED = "AGENT_TASK_BLOCKED"
    AGENT_HANDOFF = "AGENT_HANDOFF"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    AGENT_RETRY = "AGENT_RETRY"
    AGENT_CANCELLED = "AGENT_CANCELLED"
    AGENT_RECOVERED = "AGENT_RECOVERED"
    AGENT_CONFIRMATION_REQUIRED = "AGENT_CONFIRMATION_REQUIRED"
    AGENT_GRAPH_STARTED = "AGENT_GRAPH_STARTED"
    AGENT_GRAPH_COMPLETED = "AGENT_GRAPH_COMPLETED"
    AGENT_GRAPH_FAILED = "AGENT_GRAPH_FAILED"

    # Unknown / fallback
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Core Event Model
# ---------------------------------------------------------------------------

class ActionEvent(BaseModel):
    """A single observable action event in the RYVEN execution timeline.

    IMPORTANT: No credentials, tokens, or secrets are ever stored here.
    All metadata is passed through _redact_dict before storage.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: Optional[str] = None
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_action_id: Optional[str] = None

    action_type: ActionType = ActionType.UNKNOWN
    status: ActionStatus = ActionStatus.PENDING

    title: str = ""
    description: str = ""

    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None

    progress: Optional[float] = None  # 0.0 – 1.0
    step_index: Optional[int] = None
    total_steps: Optional[int] = None

    confirmation_required: bool = False
    confirmation_status: Optional[str] = None  # "PENDING" | "APPROVED" | "DENIED"

    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None

    def with_metadata(self, raw: Dict[str, Any]) -> "ActionEvent":
        """Return a copy of this event with safe (redacted) metadata."""
        self.safe_metadata = _redact_dict(raw)
        return self

    def model_post_init(self, __context: Any) -> None:
        """Ensure safe_metadata is always redacted on construction."""
        if self.safe_metadata:
            self.safe_metadata = _redact_dict(self.safe_metadata)


# ---------------------------------------------------------------------------
# Task Summary Model
# ---------------------------------------------------------------------------

class TaskSummary(BaseModel):
    """Human-readable summary of a completed orchestration task's action events."""

    task_id: str
    goal: str = ""
    status: ActionStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None

    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    cancelled_actions: int = 0
    confirmations_required: int = 0

    # Actual results from events (None = not performed / data unavailable)
    build_result: Optional[str] = None
    test_result: Optional[str] = None
    quality_gate_result: Optional[str] = None
    deployment_result: Optional[str] = None
    health_result: Optional[str] = None

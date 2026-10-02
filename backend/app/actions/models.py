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

    # Retries
    RETRY = "RETRY"

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

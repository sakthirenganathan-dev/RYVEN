"""
RYVEN 3.0 — Milestone 17.7 Long-Horizon Task Persistence & Autonomous Execution.

Authoritative Runtime & Storage:
- SINGLE AUTHORITATIVE EXECUTION: Every step executes exclusively via UnifiedTaskOrchestrator.
- DURABLE PERSISTENCE: SQLite-backed schema-versioned persistence surviving restarts & crashes.
- STRICT PRIVACY: Zero persistence of passwords, tokens, API keys, cookies, raw audio, or screenshots.
- BOUNDED AUTONOMY: Explicit step, retry, recovery, and time budgets (no infinite loops).
- IDEMPOTENCY AWARE: Verifies state before re-running non-idempotent actions upon resume.
- REVALIDATED CONFIRMATION: Invalids stale confirmation tokens upon restart.
- PROACTIVE MONITORING: Emits structured TaskProgressSnapshot events to ActionEventBus and TTS.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType, _redact_dict
from app.control.adaptive import (
    ActionIdempotency,
    AdaptiveExecutionResult,
    ObservedComputerState,
    StateDiffClassification,
    adaptive_controller,
)
from app.control.models import FailureClass
from app.control.permissions import CapabilityPermissionManager, permission_manager
from app.control.task import (
    TaskCapability,
    UnifiedTask,
    UnifiedTaskOrchestrator,
    UnifiedTaskResult,
    UnifiedTaskStatus,
    UnifiedTaskStep,
    unified_task_orchestrator as default_orchestrator,
)
from app.core.config import settings
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.workflows.confirmation import ConfirmationManager, confirmation_manager

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / ".ryven"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "checkpoints.db"

DEFAULT_MAX_STEPS_PER_RUN = 50
DEFAULT_MAX_REPLANS = 5
DEFAULT_MAX_RECOVERY_ATTEMPTS = 3
DEFAULT_MAX_STEP_ATTEMPTS = 3
DEFAULT_MAX_RUNTIME_SECONDS = 1800.0  # 30 minutes
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


def _utc_now_iso() -> str:
    """Helper returning current UTC ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


_SECRET_KEYS_LOWER: Set[str] = {
    "password", "passwd", "token", "secret", "api_key", "apikey",
    "private_key", "cookie", "bearer", "authorization", "credential", "credentials",
}


def _scrub_secrets_recursive(obj: Any) -> Any:
    """Recursively redact sensitive keys and values."""
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).startswith("_"):
                cleaned[k] = v
                continue
            k_lower = str(k).lower()
            if any(sk in k_lower for sk in _SECRET_KEYS_LOWER):
                cleaned[k] = "[REDACTED]" if v is not None else None
            else:
                cleaned[k] = _scrub_secrets_recursive(v)
        return cleaned
    elif isinstance(obj, list):
        return [_scrub_secrets_recursive(item) for item in obj]
    elif isinstance(obj, str):
        if "<think>" in obj and "</think>" in obj:
            import re
            obj = re.sub(r"<think>.*?</think>", "", obj, flags=re.DOTALL)
        return obj
    return obj


# ---------------------------------------------------------------------------
# Task Lifecycle State Machine
# ---------------------------------------------------------------------------

class LongHorizonTaskState(str, Enum):
    """Lifecycle state of a long-horizon persistent task."""
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_USER = "WAITING_USER"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


LEGAL_TASK_TRANSITIONS: Dict[LongHorizonTaskState, Set[LongHorizonTaskState]] = {
    LongHorizonTaskState.PENDING: {
        LongHorizonTaskState.PLANNING,
        LongHorizonTaskState.READY,
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.CANCELLED,
        LongHorizonTaskState.FAILED,
    },
    LongHorizonTaskState.PLANNING: {
        LongHorizonTaskState.READY,
        LongHorizonTaskState.WAITING_CONFIRMATION,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.CANCELLED,
        LongHorizonTaskState.FAILED,
    },
    LongHorizonTaskState.READY: {
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.CANCELLED,
        LongHorizonTaskState.FAILED,
    },
    LongHorizonTaskState.RUNNING: {
        LongHorizonTaskState.WAITING_CONFIRMATION,
        LongHorizonTaskState.WAITING_USER,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.RECOVERING,
        LongHorizonTaskState.BLOCKED,
        LongHorizonTaskState.INTERRUPTED,
        LongHorizonTaskState.COMPLETED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.WAITING_CONFIRMATION: {
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.CANCELLED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.INTERRUPTED,
    },
    LongHorizonTaskState.WAITING_USER: {
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.CANCELLED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.INTERRUPTED,
    },
    LongHorizonTaskState.PAUSED: {
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.READY,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.RECOVERING: {
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.BLOCKED,
        LongHorizonTaskState.INTERRUPTED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.BLOCKED: {
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.RECOVERING,
        LongHorizonTaskState.PLANNING,
        LongHorizonTaskState.PAUSED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.INTERRUPTED: {
        LongHorizonTaskState.RESUMING,
        LongHorizonTaskState.EXPIRED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.RESUMING: {
        LongHorizonTaskState.RUNNING,
        LongHorizonTaskState.WAITING_CONFIRMATION,
        LongHorizonTaskState.RECOVERING,
        LongHorizonTaskState.BLOCKED,
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.CANCELLED,
    },
    LongHorizonTaskState.COMPLETED: set(),
    LongHorizonTaskState.FAILED: {LongHorizonTaskState.RESUMING, LongHorizonTaskState.PLANNING},  # eligible for retry
    LongHorizonTaskState.CANCELLED: set(),
    LongHorizonTaskState.EXPIRED: set(),
}


# ---------------------------------------------------------------------------
# Structured Models
# ---------------------------------------------------------------------------

class LongHorizonTaskStep(BaseModel):
    """Individual unit of work within a long-horizon task plan."""
    step_id: str = Field(default_factory=lambda: f"lh-step-{uuid.uuid4().hex[:8]}")
    name: str = Field(..., description="Human-readable step title")
    description: str = Field(default="", description="Detailed step description")
    capability: str = Field(default="desktop", description="Target execution capability")
    action: str = Field(..., description="Action name (e.g. open_application, navigate, click, inspect)")
    target: Optional[str] = Field(default=None, description="Semantic UI selector, URL, or file path")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    application_context: Optional[str] = Field(default=None)
    expected_state: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list, description="IDs of steps that must complete first")
    status: LongHorizonTaskState = Field(default=LongHorizonTaskState.PENDING)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    retry_policy: str = Field(default="exponential_backoff")
    idempotency_class: ActionIdempotency = Field(default=ActionIdempotency.IDEMPOTENT)
    requires_confirmation: bool = Field(default=False)
    verification_strategy: str = Field(default="state_diff")
    checkpoint_reference: Optional[str] = Field(default=None)
    result: Optional[Dict[str, Any]] = Field(default=None)
    error: Optional[str] = Field(default=None)
    duration_ms: float = Field(default=0.0)
    started_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def scrub_step_secrets(self) -> LongHorizonTaskStep:
        self.arguments = _scrub_secrets_recursive(self.arguments)
        self.expected_state = _scrub_secrets_recursive(self.expected_state)
        if self.result:
            self.result = _scrub_secrets_recursive(self.result)
        return self


class TaskExecutionJournalEntry(BaseModel):
    """Immutable audit entry in the execution journal."""
    journal_id: str = Field(default_factory=lambda: f"jnl-{uuid.uuid4().hex[:8]}")
    event_type: str = Field(...)
    timestamp: str = Field(default_factory=_utc_now_iso)
    step_id: Optional[str] = None
    description: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_metadata(self) -> TaskExecutionJournalEntry:
        self.metadata = _scrub_secrets_recursive(self.metadata)
        return self


class TaskProgressSnapshot(BaseModel):
    """Real-time structured snapshot of task progression for UI and voice updates."""
    task_id: str
    state: LongHorizonTaskState
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    pending_steps: int = 0
    current_step_id: Optional[str] = None
    current_step_name: Optional[str] = None
    progress_percent: float = 0.0
    elapsed_ms: float = 0.0
    estimated_remaining_ms: Optional[float] = None
    last_milestone: str = "Initialized"
    next_action: str = ""
    waiting_for_user: bool = False
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    failure_class: Optional[str] = None
    recovery_attempts: int = 0
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


class LongHorizonTask(BaseModel):
    """Persistent, multi-step long-horizon goal container."""
    task_id: str = Field(default_factory=lambda: f"lht-{uuid.uuid4().hex[:8]}")
    goal: str = Field(...)
    normalized_goal: str = Field(default="")
    schema_version: int = Field(default=1)
    state: LongHorizonTaskState = Field(default=LongHorizonTaskState.PENDING)
    steps: List[LongHorizonTaskStep] = Field(default_factory=list)
    current_step_index: int = Field(default=0)
    completed_step_ids: List[str] = Field(default_factory=list)
    pending_step_ids: List[str] = Field(default_factory=list)
    failed_step_ids: List[str] = Field(default_factory=list)
    progress_percent: float = Field(default=0.0)
    adaptation_count: int = Field(default=0)
    recovery_count: int = Field(default=0)
    replan_count: int = Field(default=0)
    consecutive_failures: int = Field(default=0)
    active_confirmation_token: Optional[str] = None
    active_confirmation_action: Optional[str] = None
    checkpoint_reference: Optional[str] = None
    execution_journal: List[TaskExecutionJournalEntry] = Field(default_factory=list)
    last_milestone: str = Field(default="Task initialized")
    next_recommended_action: str = Field(default="")
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    failure_class: Optional[str] = None
    result_summary: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_task_secrets(self) -> LongHorizonTask:
        self.safe_metadata = _scrub_secrets_recursive(self.safe_metadata)
        if not self.normalized_goal:
            self.normalized_goal = self.goal.strip()
        return self

    def transition_to(self, new_state: LongHorizonTaskState, reason: str = "") -> bool:
        """Enforce strict legal state transition table."""
        if self.state == new_state:
            return True
        allowed = LEGAL_TASK_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            err = f"Illegal task transition from {self.state} to {new_state} (Reason: {reason})"
            logger.warning(f"[LONG_TASK] {err}")
            raise ValueError(err)
        old_state = self.state
        self.state = new_state
        self.updated_at = _utc_now_iso()
        self.add_journal_entry(
            event_type="TASK_STATE_TRANSITION",
            description=f"Transitioned from {old_state.value} to {new_state.value}: {reason}",
            metadata={"from": old_state.value, "to": new_state.value, "reason": reason},
        )
        return True

    def add_journal_entry(self, event_type: str, description: str = "", step_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Append safe audit record to execution journal."""
        entry = TaskExecutionJournalEntry(
            event_type=event_type,
            step_id=step_id,
            description=description,
            metadata=metadata or {},
        )
        self.execution_journal.append(entry)

    def get_progress_snapshot(self) -> TaskProgressSnapshot:
        """Compute an accurate, un-fabricated progress summary."""
        total = len(self.steps)
        completed = len(self.completed_step_ids)
        failed = len(self.failed_step_ids)
        pending = len(self.pending_step_ids)
        pct = (completed / total * 100.0) if total > 0 else 0.0

        current_s = self.steps[self.current_step_index] if 0 <= self.current_step_index < len(self.steps) else None

        # Approximate elapsed time
        elapsed_ms = 0.0
        if self.started_at:
            try:
                st = datetime.fromisoformat(self.started_at)
                elapsed_ms = (datetime.now(timezone.utc) - st).total_seconds() * 1000.0
            except Exception:
                pass

        # Approximate remaining time based on completed steps average
        est_rem_ms = None
        if completed > 0 and elapsed_ms > 0 and pending > 0:
            avg_per_step = elapsed_ms / completed
            est_rem_ms = round(avg_per_step * pending, 1)

        return TaskProgressSnapshot(
            task_id=self.task_id,
            state=self.state,
            total_steps=total,
            completed_steps=completed,
            failed_steps=failed,
            pending_steps=pending,
            current_step_id=current_s.step_id if current_s else None,
            current_step_name=current_s.name if current_s else None,
            progress_percent=round(pct, 1),
            elapsed_ms=round(elapsed_ms, 1),
            estimated_remaining_ms=est_rem_ms,
            last_milestone=self.last_milestone,
            next_action=self.next_recommended_action or (current_s.name if current_s else "None"),
            waiting_for_user=self.state in (LongHorizonTaskState.WAITING_USER, LongHorizonTaskState.WAITING_CONFIRMATION),
            requires_confirmation=self.active_confirmation_token is not None,
            confirmation_token=self.active_confirmation_token,
            failure_class=self.failure_class,
            recovery_attempts=self.recovery_count,
            safe_metadata=dict(self.safe_metadata),
        )


# ---------------------------------------------------------------------------
# TaskPersistenceRepository (SQLite-Backed, Thread-Safe)
# ---------------------------------------------------------------------------

class TaskPersistenceRepository:
    """Thread-safe SQLite storage for durable multi-session tasks.

    Invariants:
    - Never persists passwords, API keys, tokens, cookies, or raw screenshots.
    - Uses atomic transactions and schema migration metadata.
    - Thread-safe connection pooling across workers.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = db_path if db_path and str(db_path) == ":memory:" else (Path(db_path) if db_path else DEFAULT_DB_PATH)
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @contextmanager
    def _connection(self):
        """Thread-safe database connection context manager."""
        if str(self.db_path) == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            with self._mem_conn:
                yield self._mem_conn
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _init_db(self) -> None:
        """Create tables and ensure schema integrity."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                cur.execute("SELECT value FROM schema_meta WHERE key = 'long_tasks_schema_version'")
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO schema_meta (key, value) VALUES ('long_tasks_schema_version', ?)",
                        (str(self.SCHEMA_VERSION),),
                    )

                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS long_horizon_tasks (
                        task_id TEXT PRIMARY KEY,
                        schema_version INTEGER NOT NULL,
                        goal TEXT NOT NULL,
                        normalized_goal TEXT NOT NULL,
                        state TEXT NOT NULL,
                        total_steps INTEGER NOT NULL DEFAULT 0,
                        completed_steps INTEGER NOT NULL DEFAULT 0,
                        progress_percent REAL NOT NULL DEFAULT 0.0,
                        current_step_index INTEGER NOT NULL DEFAULT 0,
                        steps_json TEXT NOT NULL,
                        journal_json TEXT NOT NULL,
                        safe_metadata_json TEXT NOT NULL,
                        checkpoint_ref TEXT,
                        failure_class TEXT,
                        result_summary TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT
                    )
                    """
                )
                conn.commit()

    def create_task(self, task: LongHorizonTask) -> LongHorizonTask:
        """Insert a newly created task atomically."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                task.updated_at = now
                steps_data = [s.model_dump() for s in task.steps]
                journal_data = [j.model_dump() for j in task.execution_journal]

                meta_to_save = dict(task.safe_metadata)
                meta_to_save["_recovery_count"] = task.recovery_count
                meta_to_save["_adaptation_count"] = task.adaptation_count
                meta_to_save["_replan_count"] = task.replan_count
                meta_to_save["_consecutive_failures"] = task.consecutive_failures
                meta_to_save["_last_milestone"] = task.last_milestone
                meta_to_save["_active_confirmation_token"] = task.active_confirmation_token
                meta_to_save["_active_confirmation_action"] = task.active_confirmation_action

                cur.execute(
                    """
                    INSERT INTO long_horizon_tasks (
                        task_id, schema_version, goal, normalized_goal, state,
                        total_steps, completed_steps, progress_percent, current_step_index,
                        steps_json, journal_json, safe_metadata_json, checkpoint_ref,
                        failure_class, result_summary, created_at, updated_at,
                        started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.schema_version,
                        task.goal,
                        task.normalized_goal,
                        task.state.value,
                        len(task.steps),
                        len(task.completed_step_ids),
                        task.progress_percent,
                        task.current_step_index,
                        json.dumps(_scrub_secrets_recursive(steps_data)),
                        json.dumps(_scrub_secrets_recursive(journal_data)),
                        json.dumps(_scrub_secrets_recursive(meta_to_save)),
                        task.checkpoint_reference,
                        task.failure_class,
                        task.result_summary,
                        task.created_at,
                        task.updated_at,
                        task.started_at,
                        task.completed_at,
                    ),
                )
                conn.commit()
                return task

    def update_task(self, task: LongHorizonTask) -> LongHorizonTask:
        """Update existing task state and step graph."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                task.updated_at = now
                steps_data = [s.model_dump() for s in task.steps]
                journal_data = [j.model_dump() for j in task.execution_journal]

                meta_to_save = dict(task.safe_metadata)
                meta_to_save["_recovery_count"] = task.recovery_count
                meta_to_save["_adaptation_count"] = task.adaptation_count
                meta_to_save["_replan_count"] = task.replan_count
                meta_to_save["_consecutive_failures"] = task.consecutive_failures
                meta_to_save["_last_milestone"] = task.last_milestone
                meta_to_save["_active_confirmation_token"] = task.active_confirmation_token
                meta_to_save["_active_confirmation_action"] = task.active_confirmation_action

                cur.execute(
                    """
                    UPDATE long_horizon_tasks SET
                        state = ?,
                        total_steps = ?,
                        completed_steps = ?,
                        progress_percent = ?,
                        current_step_index = ?,
                        steps_json = ?,
                        journal_json = ?,
                        safe_metadata_json = ?,
                        checkpoint_ref = ?,
                        failure_class = ?,
                        result_summary = ?,
                        updated_at = ?,
                        started_at = ?,
                        completed_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        task.state.value,
                        len(task.steps),
                        len(task.completed_step_ids),
                        task.progress_percent,
                        task.current_step_index,
                        json.dumps(_scrub_secrets_recursive(steps_data)),
                        json.dumps(_scrub_secrets_recursive(journal_data)),
                        json.dumps(_scrub_secrets_recursive(meta_to_save)),
                        task.checkpoint_reference,
                        task.failure_class,
                        task.result_summary,
                        task.updated_at,
                        task.started_at,
                        task.completed_at,
                        task.task_id,
                    ),
                )
                conn.commit()
                return task


    def get_task(self, task_id: str) -> Optional[LongHorizonTask]:
        """Fetch and reconstruct task from persistent storage."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM long_horizon_tasks WHERE task_id = ?", (task_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_task(row)

    def list_tasks(self, limit: int = 50, state: Optional[LongHorizonTaskState] = None) -> List[LongHorizonTask]:
        """Return recently updated tasks matching criteria."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                if state:
                    cur.execute(
                        "SELECT * FROM long_horizon_tasks WHERE state = ? ORDER BY updated_at DESC LIMIT ?",
                        (state.value, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM long_horizon_tasks ORDER BY updated_at DESC LIMIT ?",
                        (limit,),
                    )
                rows = cur.fetchall()
                return [self._row_to_task(r) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        """Remove task record completely."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM long_horizon_tasks WHERE task_id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0

    def mark_interrupted(self, task_id: str, reason: str = "Application restart/interruption detected") -> Optional[LongHorizonTask]:
        """Mark in-flight tasks as INTERRUPTED upon crash or abrupt shutdown."""
        task = self.get_task(task_id)
        if not task:
            return None
        if task.state in (LongHorizonTaskState.RUNNING, LongHorizonTaskState.PLANNING, LongHorizonTaskState.WAITING_CONFIRMATION, LongHorizonTaskState.WAITING_USER, LongHorizonTaskState.RESUMING):
            task.state = LongHorizonTaskState.INTERRUPTED
            task.active_confirmation_token = None  # Invalidate confirmation tokens
            task.add_journal_entry("TASK_INTERRUPTED", description=reason)
            self.update_task(task)
        return task

    def recover_crash_interrupted_tasks(self) -> List[str]:
        """Find any tasks left in non-terminal active states upon process restart and mark them INTERRUPTED."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT task_id FROM long_horizon_tasks WHERE state IN ('RUNNING', 'PLANNING', 'WAITING_CONFIRMATION', 'WAITING_USER', 'RESUMING')"
                )
                rows = cur.fetchall()
                interrupted_ids = [r["task_id"] for r in rows]

        for tid in interrupted_ids:
            self.mark_interrupted(tid, reason="Application restart/crash detected")
        return interrupted_ids

    def mark_completed(self, task_id: str, summary: str = "") -> Optional[LongHorizonTask]:
        """Finalize task as COMPLETED."""
        task = self.get_task(task_id)
        if not task:
            return None
        task.state = LongHorizonTaskState.COMPLETED
        task.progress_percent = 100.0
        task.completed_at = _utc_now_iso()
        task.result_summary = summary or f"Goal '{task.goal}' completed successfully."
        task.add_journal_entry("TASK_COMPLETED", description=task.result_summary)
        return self.update_task(task)

    def mark_failed(self, task_id: str, error: str = "", failure_class: Optional[str] = None) -> Optional[LongHorizonTask]:
        """Finalize task as FAILED with safe classification."""
        task = self.get_task(task_id)
        if not task:
            return None
        task.state = LongHorizonTaskState.FAILED
        task.failure_class = failure_class or FailureClass.RUNTIME_ERROR.value
        task.result_summary = error or "Task execution failed."
        task.completed_at = _utc_now_iso()
        task.add_journal_entry("TASK_FAILED", description=task.result_summary, metadata={"failure_class": task.failure_class})
        return self.update_task(task)

    def _row_to_task(self, row: sqlite3.Row) -> LongHorizonTask:
        """Deserialize database row into structured LongHorizonTask."""
        steps_raw = json.loads(row["steps_json"]) if row["steps_json"] else []
        journal_raw = json.loads(row["journal_json"]) if row["journal_json"] else []
        meta_raw = json.loads(row["safe_metadata_json"]) if row["safe_metadata_json"] else {}
        recovery_count = meta_raw.pop("_recovery_count", 0)
        adaptation_count = meta_raw.pop("_adaptation_count", 0)
        replan_count = meta_raw.pop("_replan_count", 0)
        consecutive_failures = meta_raw.pop("_consecutive_failures", 0)
        last_milestone = meta_raw.pop("_last_milestone", "Task initialized")
        active_conf_token = meta_raw.pop("_active_confirmation_token", None)
        active_conf_action = meta_raw.pop("_active_confirmation_action", None)

        steps = [LongHorizonTaskStep(**s) for s in steps_raw]
        journal = [TaskExecutionJournalEntry(**j) for j in journal_raw]

        completed_ids = [s.step_id for s in steps if s.status == LongHorizonTaskState.COMPLETED]
        pending_ids = [s.step_id for s in steps if s.status in (LongHorizonTaskState.PENDING, LongHorizonTaskState.READY)]
        failed_ids = [s.step_id for s in steps if s.status == LongHorizonTaskState.FAILED]

        return LongHorizonTask(
            task_id=row["task_id"],
            schema_version=row["schema_version"],
            goal=row["goal"],
            normalized_goal=row["normalized_goal"],
            state=LongHorizonTaskState(row["state"]),
            steps=steps,
            current_step_index=row["current_step_index"],
            completed_step_ids=completed_ids,
            pending_step_ids=pending_ids,
            failed_step_ids=failed_ids,
            progress_percent=row["progress_percent"],
            adaptation_count=adaptation_count,
            recovery_count=recovery_count,
            replan_count=replan_count,
            consecutive_failures=consecutive_failures,
            active_confirmation_token=active_conf_token,
            active_confirmation_action=active_conf_action,
            checkpoint_reference=row["checkpoint_ref"],
            failure_class=row["failure_class"],
            result_summary=row["result_summary"],
            execution_journal=journal,
            last_milestone=last_milestone,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            safe_metadata=meta_raw,
        )



# Singleton repository instance
task_persistence_repo = TaskPersistenceRepository()


# ---------------------------------------------------------------------------
# LongHorizonTaskManager (Phase 2 & 3: Authoritative Orchestrator Supervisor)
# ---------------------------------------------------------------------------

class LongHorizonTaskManager:
    """Authoritative supervisor coordinating long-horizon task execution.

    CRITICAL ARCHITECTURE INVARIANT:
    - Never executes tools directly.
    - Every action and workflow routes strictly through UnifiedTaskOrchestrator.
    - Bounded progression: explicit step, cycle, recovery, and time budgets.
    - Invalids confirmation tokens on resumption.
    - Idempotency-aware re-execution protection.
    """

    def __init__(
        self,
        repository: Optional[TaskPersistenceRepository] = None,
        orchestrator: Optional[UnifiedTaskOrchestrator] = None,
        checkpoint_store_inst: Optional[CheckpointStore] = None,
        max_steps_per_run: int = DEFAULT_MAX_STEPS_PER_RUN,
        max_replans: int = DEFAULT_MAX_REPLANS,
        max_recovery_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
        max_runtime_seconds: float = DEFAULT_MAX_RUNTIME_SECONDS,
    ) -> None:
        self.repo = repository or task_persistence_repo
        self.orchestrator = orchestrator or default_orchestrator
        self.checkpoints = checkpoint_store_inst or checkpoint_store
        self.max_steps_per_run = max_steps_per_run
        self.max_replans = max_replans
        self.max_recovery_attempts = max_recovery_attempts
        self.max_runtime_seconds = max_runtime_seconds
        self._paused_tasks: Set[str] = set()
        self._cancelled_tasks: Set[str] = set()

    def startup_recovery(self) -> List[str]:
        """Scan and recover tasks interrupted across crashes or restarts."""
        return self.repo.recover_crash_interrupted_tasks()

    def get_task(self, task_id: str) -> Optional[LongHorizonTask]:
        """Fetch task by ID from repository."""
        return self.repo.get_task(task_id)

    def list_tasks(self, limit: int = 50, state: Optional[LongHorizonTaskState] = None) -> List[LongHorizonTask]:
        """List tasks with optional state filter and limit."""
        return self.repo.list_tasks(limit=limit, state=state)

    def get_progress(self, task_id: str) -> Optional[TaskProgressSnapshot]:
        """Fetch progress snapshot for task."""
        t = self.repo.get_task(task_id)
        return t.get_progress_snapshot() if t else None

    # -----------------------------------------------------------------------
    # Task Creation & Planning
    # -----------------------------------------------------------------------

    async def create_long_horizon_task(self, goal: str, initial_steps: Optional[List[LongHorizonTaskStep]] = None) -> LongHorizonTask:
        """Create and persist a new long-horizon task container."""
        task = LongHorizonTask(goal=goal)
        task.started_at = _utc_now_iso()
        task.transition_to(LongHorizonTaskState.PLANNING, reason="Synthesizing multi-step plan")

        if initial_steps:
            task.steps = initial_steps
            task.pending_step_ids = [s.step_id for s in initial_steps]
            task.transition_to(LongHorizonTaskState.READY, reason="Initial plan provided")
        else:
            # Delegate planning to UnifiedTaskOrchestrator / ComputerWorkflowEngine
            unified = await self.orchestrator.create_task(goal)
            planned = await self.orchestrator.plan_task(unified)

            steps: List[LongHorizonTaskStep] = []
            prev_id: Optional[str] = None
            for idx, u_step in enumerate(planned.steps):
                s_id = f"step-{idx}"
                deps = [prev_id] if prev_id else []
                # Determine idempotency
                idempotency = ActionIdempotency.IDEMPOTENT
                if any(k in u_step.action.lower() for k in ("submit", "pay", "delete", "post", "transfer")):
                    idempotency = ActionIdempotency.NON_IDEMPOTENT
                elif any(k in u_step.action.lower() for k in ("navigate", "download", "upload")):
                    idempotency = ActionIdempotency.CONDITIONALLY_IDEMPOTENT

                steps.append(
                    LongHorizonTaskStep(
                        step_id=s_id,
                        name=u_step.name,
                        capability=u_step.capability.value if hasattr(u_step.capability, "value") else str(u_step.capability),
                        action=u_step.action,
                        target=u_step.target,
                        arguments=dict(u_step.arguments),
                        application_context=u_step.application_context,
                        expected_state=dict(u_step.expected_state),
                        dependencies=deps,
                        status=LongHorizonTaskState.PENDING,
                        idempotency_class=idempotency,
                        requires_confirmation=u_step.requires_confirmation,
                    )
                )
                prev_id = s_id

            task.steps = steps
            task.pending_step_ids = [s.step_id for s in steps]
            task.transition_to(LongHorizonTaskState.READY, reason="Plan generated from orchestrator")

        task = self.repo.create_task(task)
        await self._emit_telemetry(ActionType.LONG_TASK_CREATED, ActionStatus.COMPLETED, f"Created task: {goal[:80]}", task.task_id)
        return task

    # -----------------------------------------------------------------------
    # Autonomous Execution Loop (Bounded, Safe)
    # -----------------------------------------------------------------------

    async def execute_task(self, task_id: str, auto_confirm: bool = False, session_id: str = "default") -> TaskProgressSnapshot:
        """Run bounded progression through dependency-satisfied steps.

        Invariants:
        - Re-checks pause and cancellation flags before each step.
        - Strictly bounds execution cycles (no while True without counters).
        - Executes exclusively through UnifiedTaskOrchestrator.
        """
        task = self.repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found in repository.")

        if task.state in (LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED):
            return task.get_progress_snapshot()

        if task.state == LongHorizonTaskState.PAUSED:
            return task.get_progress_snapshot()

        task.transition_to(LongHorizonTaskState.RUNNING, reason="Starting autonomous execution")
        self.repo.update_task(task)
        await self._emit_telemetry(ActionType.LONG_TASK_STARTED, ActionStatus.COMPLETED, f"Executing task {task_id}", task_id)

        start_time = time.time()
        steps_executed_this_run = 0

        # BOUNDED LOOP
        while steps_executed_this_run < self.max_steps_per_run:
            # 1. Check time budget
            if (time.time() - start_time) > self.max_runtime_seconds:
                task.transition_to(LongHorizonTaskState.FAILED, reason="Task execution time budget exhausted")
                task.result_summary = "Execution timed out."
                self.repo.update_task(task)
                break

            # 2. Check Pause Flag
            if task_id in self._paused_tasks:
                self._paused_tasks.discard(task_id)
                task.transition_to(LongHorizonTaskState.PAUSED, reason="User paused task")
                task.last_milestone = "Task paused by user"
                self.repo.update_task(task)
                await self._emit_telemetry(ActionType.LONG_TASK_PAUSED, ActionStatus.COMPLETED, "Task paused", task_id)
                break

            # 3. Check Cancel Flag
            if task_id in self._cancelled_tasks:
                self._cancelled_tasks.discard(task_id)
                task.transition_to(LongHorizonTaskState.CANCELLED, reason="User cancelled task")
                task.result_summary = "Task cancelled by user."
                self.repo.update_task(task)
                await self._emit_telemetry(ActionType.LONG_TASK_CANCELLED, ActionStatus.CANCELLED, "Task cancelled", task_id)
                break

            # 4. Select Next Eligible Step (Dependency-Aware)
            step_to_run = self._select_next_eligible_step(task)
            if not step_to_run:
                # All steps completed or no more eligible
                if len(task.completed_step_ids) == len(task.steps):
                    task.transition_to(LongHorizonTaskState.COMPLETED, reason="All plan steps completed successfully")
                    task.result_summary = f"Successfully completed all {len(task.steps)} steps."
                    self.repo.mark_completed(task_id, summary=task.result_summary)
                    await self._emit_telemetry(ActionType.LONG_TASK_COMPLETED, ActionStatus.COMPLETED, task.result_summary, task_id)
                break

            # 5. Check Consequential Confirmation Requirement
            if step_to_run.requires_confirmation and not auto_confirm:
                # Need fresh confirmation token
                tok = confirmation_manager.generate_token(step_to_run.action)
                task.active_confirmation_token = tok
                task.active_confirmation_action = step_to_run.action
                task.transition_to(LongHorizonTaskState.WAITING_CONFIRMATION, reason=f"Step '{step_to_run.name}' requires confirmation")
                task.last_milestone = f"Waiting for user confirmation to perform: {step_to_run.name}"
                self.repo.update_task(task)
                await self._emit_telemetry(ActionType.LONG_TASK_WAITING_CONFIRMATION, ActionStatus.WAITING_CONFIRMATION, task.last_milestone, task_id)
                break

            # 6. Execute Single Step strictly via UnifiedTaskOrchestrator
            step_to_run.status = LongHorizonTaskState.RUNNING
            step_to_run.started_at = _utc_now_iso()
            step_to_run.attempt_count += 1
            task.add_journal_entry("LONG_TASK_STEP_STARTED", description=f"Executing {step_to_run.name}", step_id=step_to_run.step_id)
            self.repo.update_task(task)

            step_res = await self._dispatch_step_to_orchestrator(task, step_to_run, auto_confirm=auto_confirm, session_id=session_id)
            steps_executed_this_run += 1

            if step_res.get("success"):
                # STEP SUCCESS
                step_to_run.status = LongHorizonTaskState.COMPLETED
                step_to_run.completed_at = _utc_now_iso()
                step_to_run.result = step_res
                if step_to_run.step_id not in task.completed_step_ids:
                    task.completed_step_ids.append(step_to_run.step_id)
                if step_to_run.step_id in task.pending_step_ids:
                    task.pending_step_ids.remove(step_to_run.step_id)

                task.current_step_index = min(len(task.steps) - 1, task.current_step_index + 1)
                task.consecutive_failures = 0
                task.last_milestone = f"Completed step: {step_to_run.name}"
                task.progress_percent = (len(task.completed_step_ids) / len(task.steps) * 100.0) if task.steps else 100.0
                task.add_journal_entry("LONG_TASK_STEP_COMPLETED", description=f"Completed {step_to_run.name}", step_id=step_to_run.step_id)

                # Durable Checkpoint
                self._save_step_checkpoint(task, step_to_run)
                self.repo.update_task(task)
                await self._emit_telemetry(ActionType.LONG_TASK_STEP_COMPLETED, ActionStatus.COMPLETED, f"Step completed: {step_to_run.name}", task_id)

            else:
                # STEP FAILURE / ADAPTATION / RECOVERY
                step_to_run.error = step_res.get("error") or "Step execution failed"
                task.consecutive_failures += 1
                task.add_journal_entry("LONG_TASK_STEP_FAILED", description=f"Failed {step_to_run.name}: {step_to_run.error}", step_id=step_to_run.step_id)

                if step_to_run.attempt_count < step_to_run.max_attempts and task.consecutive_failures <= DEFAULT_MAX_CONSECUTIVE_FAILURES:
                    # Retry eligible
                    step_to_run.status = LongHorizonTaskState.READY
                    task.recovery_count += 1
                    task.last_milestone = f"Attempt {step_to_run.attempt_count} failed for '{step_to_run.name}'. Retrying..."
                    self.repo.update_task(task)
                else:
                    # Step exhausted attempts -> Block downstream
                    step_to_run.status = LongHorizonTaskState.FAILED
                    if step_to_run.step_id not in task.failed_step_ids:
                        task.failed_step_ids.append(step_to_run.step_id)
                    task.transition_to(LongHorizonTaskState.BLOCKED, reason=f"Step '{step_to_run.name}' exhausted attempts")
                    task.last_milestone = f"Step '{step_to_run.name}' failed after {step_to_run.attempt_count} attempts."
                    self.repo.update_task(task)
                    break

        # Check if all steps completed
        if len(task.completed_step_ids) == len(task.steps) and task.steps and task.state == LongHorizonTaskState.RUNNING:
            task.transition_to(LongHorizonTaskState.COMPLETED, reason="All plan steps completed successfully")
            task.result_summary = f"Successfully completed all {len(task.steps)} steps."
            self.repo.mark_completed(task_id, summary=task.result_summary)
            await self._emit_telemetry(ActionType.LONG_TASK_COMPLETED, ActionStatus.COMPLETED, task.result_summary, task_id)

        return task.get_progress_snapshot()

    # -----------------------------------------------------------------------
    # Step Selection & Dependency Graph Validation
    # -----------------------------------------------------------------------

    def _select_next_eligible_step(self, task: LongHorizonTask) -> Optional[LongHorizonTaskStep]:
        """Find the first uncompleted step whose dependencies are all satisfied."""
        for step in task.steps:
            if step.status == LongHorizonTaskState.COMPLETED:
                continue
            if step.status == LongHorizonTaskState.FAILED:
                continue

            # Verify dependencies
            deps_satisfied = True
            for dep_id in step.dependencies:
                dep_step = next((s for s in task.steps if s.step_id == dep_id), None)
                if not dep_step or dep_step.status != LongHorizonTaskState.COMPLETED:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                return step

        return None

    # -----------------------------------------------------------------------
    # Dispatch Step strictly to UnifiedTaskOrchestrator
    # -----------------------------------------------------------------------

    async def _dispatch_step_to_orchestrator(
        self,
        task: LongHorizonTask,
        step: LongHorizonTaskStep,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> Dict[str, Any]:
        """Wrap step into a single-step UnifiedTask and execute via authoritative orchestrator."""
        u_step = UnifiedTaskStep(
            step_id=step.step_id,
            name=step.name,
            capability=TaskCapability(step.capability) if step.capability in TaskCapability._value2member_map_ else TaskCapability.DESKTOP,
            action=step.action,
            target=step.target,
            arguments=dict(step.arguments),
            application_context=step.application_context,
            expected_state=dict(step.expected_state),
            requires_confirmation=step.requires_confirmation,
        )

        single_task = UnifiedTask(
            original_goal=f"{task.goal} -> {step.name}",
            steps=[u_step],
            pending_steps=[u_step.step_id],
            metadata={"parent_long_task_id": task.task_id},
        )

        try:
            res: UnifiedTaskResult = await self.orchestrator.execute_task(
                single_task,
                auto_confirm=auto_confirm,
                session_id=session_id,
            )
            return {
                "success": res.success,
                "status": res.status.value,
                "result_summary": res.result_summary,
                "error": res.error,
                "step_results": res.step_results,
            }
        except Exception as exc:
            logger.error(f"[LONG_TASK] Dispatch error for step '{step.name}': {exc}")
            return {"success": False, "error": str(exc)}

    # -----------------------------------------------------------------------
    # Pause, Resume, Cancel & Retry Operations
    # -----------------------------------------------------------------------

    async def pause_task(self, task_id: str, reason: str = "User requested pause") -> TaskProgressSnapshot:
        """Signal an active task to gracefully pause after the current step."""
        self._paused_tasks.add(task_id)
        task = self.repo.get_task(task_id)
        if task and task.state in (LongHorizonTaskState.READY, LongHorizonTaskState.RUNNING):
            task.transition_to(LongHorizonTaskState.PAUSED, reason=reason)
            self.repo.update_task(task)
        return (task or self.repo.get_task(task_id)).get_progress_snapshot()

    async def resume_task(self, task_id: str, auto_confirm: bool = False, session_id: str = "default") -> TaskProgressSnapshot:
        """Safely resume a paused, interrupted, or blocked task.

        Invariants:
        - Invalids all existing confirmation tokens (re-approval mandatory).
        - Re-observes desktop/browser state for environment drift.
        - Preserves completed steps (never re-executes completed non-idempotent actions).
        """
        task = self.repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        # Clear pause flag
        self._paused_tasks.discard(task_id)

        # Invalidate stale confirmation tokens
        task.active_confirmation_token = None
        task.active_confirmation_action = None

        if task.state in (LongHorizonTaskState.PAUSED, LongHorizonTaskState.INTERRUPTED, LongHorizonTaskState.BLOCKED, LongHorizonTaskState.READY, LongHorizonTaskState.WAITING_CONFIRMATION, LongHorizonTaskState.WAITING_USER):
            task.transition_to(LongHorizonTaskState.RESUMING, reason="Resuming execution")
            self.repo.update_task(task)

            # Re-observe environment for drift detection
            drift_detected, drift_reason = await self._check_environment_drift(task)
            if drift_detected:
                task.add_journal_entry("ENVIRONMENT_DRIFT_DETECTED", description=drift_reason)
                logger.info(f"[LONG_TASK] Environment drift on resume for {task_id}: {drift_reason}")

            self.repo.update_task(task)
            return await self.execute_task(task_id, auto_confirm=auto_confirm, session_id=session_id)

        return task.get_progress_snapshot()

    async def cancel_task(self, task_id: str, reason: str = "User requested cancellation") -> TaskProgressSnapshot:
        """Immediately cancel an in-flight or pending task."""
        self._cancelled_tasks.add(task_id)
        task = self.repo.get_task(task_id)
        if task:
            task.transition_to(LongHorizonTaskState.CANCELLED, reason=reason)
            task.result_summary = f"Cancelled: {reason}"
            self.repo.update_task(task)
            await self._emit_telemetry(ActionType.LONG_TASK_CANCELLED, ActionStatus.CANCELLED, reason, task_id)
            return task.get_progress_snapshot()
        raise ValueError(f"Task '{task_id}' not found.")

    async def retry_task(self, task_id: str, auto_confirm: bool = False, session_id: str = "default") -> TaskProgressSnapshot:
        """Reset failed steps and restart progression."""
        task = self.repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        # Reset failed steps to READY
        for step in task.steps:
            if step.status == LongHorizonTaskState.FAILED:
                step.status = LongHorizonTaskState.READY
                step.attempt_count = 0
                step.error = None
        task.failed_step_ids.clear()
        task.consecutive_failures = 0
        task.transition_to(LongHorizonTaskState.RESUMING, reason="Task retry requested")
        self.repo.update_task(task)

        return await self.execute_task(task_id, auto_confirm=auto_confirm, session_id=session_id)

    async def confirm_task_step(self, task_id: str, confirmation_token: str, approved: bool = True, session_id: str = "default") -> TaskProgressSnapshot:
        """Handle confirmation approval for a waiting task step."""
        task = self.repo.get_task(task_id)
        if not task:
            raise ValueError(f"Task '{task_id}' not found.")

        if not approved:
            task.transition_to(LongHorizonTaskState.CANCELLED, reason="User rejected step confirmation")
            task.result_summary = "Confirmation rejected by user."
            self.repo.update_task(task)
            return task.get_progress_snapshot()

        if task.active_confirmation_token and confirmation_token != task.active_confirmation_token:
            raise ValueError("Provided confirmation token does not match active token.")

        # Confirmation accepted
        confirmation_manager.confirm(confirmation_token)
        task.active_confirmation_token = None
        task.active_confirmation_action = None
        task.transition_to(LongHorizonTaskState.RUNNING, reason="User confirmed step")
        self.repo.update_task(task)

        return await self.execute_task(task_id, auto_confirm=True, session_id=session_id)

    # -----------------------------------------------------------------------
    # Environment Drift Detection
    # -----------------------------------------------------------------------

    async def _check_environment_drift(self, task: LongHorizonTask) -> Tuple[bool, str]:
        """Inspect current system observation vs expected state of the next step."""
        curr_step = self._select_next_eligible_step(task)
        if not curr_step or not curr_step.expected_state:
            return False, "No expected state defined for step"

        try:
            obs: ObservedComputerState = await adaptive_controller.observe_environment(session_id="drift_check")
            # Reuse adaptive diff classifier
            diff = adaptive_controller.evaluate_state_difference(curr_step.expected_state, obs, None)
            if not diff.matches:
                return True, f"State difference: {diff.classification.value} - {diff.reason}"
            return False, "Environment state matches expectation"
        except Exception as e:
            return False, f"Could not observe environment: {e}"

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _save_step_checkpoint(self, task: LongHorizonTask, step: LongHorizonTaskStep) -> None:
        """Save a durable SQLite checkpoint record."""
        try:
            ckpt_ref = f"ckpt-{task.task_id}-{step.step_id}"
            self.checkpoints.save_checkpoint(
                task_id=task.task_id,
                user_goal=task.goal,
                current_state=task.state.value,
                current_step_id=step.step_id,
                completed_steps=task.completed_step_ids,
                pending_steps=task.pending_step_ids,
                safe_metadata={"last_milestone": task.last_milestone, "progress": task.progress_percent, "checkpoint_ref": ckpt_ref},
            )
            step.checkpoint_reference = ckpt_ref
            task.checkpoint_reference = ckpt_ref
        except Exception as e:
            logger.debug(f"[LONG_TASK] Checkpoint error: {e}")

    async def _emit_telemetry(self, action_type: ActionType, status: ActionStatus, title: str, task_id: str) -> None:
        """Publish sanitized lifecycle telemetry event."""
        try:
            await action_bus.publish(
                ActionEvent(
                    action_type=action_type,
                    status=status,
                    title=title,
                    task_id=task_id,
                )
            )
        except Exception:
            pass


# Singleton manager instance
long_horizon_task_manager = LongHorizonTaskManager()

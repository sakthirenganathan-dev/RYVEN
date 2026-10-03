"""RYVEN 3.0 — SQLite-Backed Persistent Checkpoint Store (M15.3.9).

Provides durable, schema-versioned checkpoint persistence across process restarts and crashes.
Enforces strict secret redaction on all metadata before serialization.

NEVER persists passwords, API keys, cookies, tokens, or screenshots.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType, _redact_dict
from app.core.config import settings
from app.core.logging_config import logger
from app.runtime.models import PersistedTaskCheckpoint, utc_now_iso


# Path for durable SQLite DB
DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / ".ryven"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "checkpoints.db"


class CheckpointStore:
    """Thread-safe SQLite storage for durable task checkpoints."""

    CURRENT_SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = db_path if db_path and str(db_path) == ":memory:" else (Path(db_path) if db_path else DEFAULT_DB_PATH)
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._recover_if_corrupt_db()
        self._init_db()

    def _recover_if_corrupt_db(self) -> None:
        """Replace invalid SQLite files with a fresh database to avoid import-time crashes."""
        if str(self.db_path) == ":memory:":
            return

        db_path = Path(self.db_path)
        if not db_path.exists() or not db_path.is_file():
            return

        try:
            probe = sqlite3.connect(str(db_path), timeout=10.0)
            try:
                probe.execute("SELECT name FROM sqlite_master LIMIT 1")
                return
            finally:
                probe.close()
        except sqlite3.DatabaseError:
            backup_path = db_path.with_name(f"{db_path.name}.corrupt-{threading.get_ident()}-{int(os.times().elapsed * 1000)}")
            counter = 1
            while backup_path.exists():
                backup_path = db_path.with_name(f"{db_path.name}.corrupt-{threading.get_ident()}-{counter}")
                counter += 1

            try:
                db_path.replace(backup_path)
                logger.warning("[CHECKPOINT_STORE] Invalid SQLite checkpoint database detected; replaced %s with backup %s", db_path, backup_path)
            except Exception as exc:
                logger.warning("[CHECKPOINT_STORE] Could not rotate invalid database %s: %s", db_path, exc)
                try:
                    db_path.unlink()
                except Exception:
                    pass

    @contextmanager
    def _connection(self):
        """Create a connection with timeout, ensuring it is closed or kept for in-memory DB."""
        if str(self.db_path) == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            with self._mem_conn:
                yield self._mem_conn
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
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
        """Initialize tables and verify schema version."""
    def close(self) -> None:
        """Release any in-memory SQLite connection so temp DB files can be cleaned up on Windows."""
        with self._lock:
            if self._mem_conn is not None:
                try:
                    self._mem_conn.close()
                except Exception:
                    pass
                self._mem_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _init_db(self) -> None:
        """Initialize tables and verify schema version."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                # Meta table for migrations
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                cursor.execute("SELECT value FROM schema_meta WHERE key = 'version'")
                row = cursor.fetchone()
                if not row:
                    cursor.execute(
                        "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
                        (str(self.CURRENT_SCHEMA_VERSION),),
                    )

                # Checkpoints table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_checkpoints (
                        task_id TEXT PRIMARY KEY,
                        workflow_id TEXT,
                        project_name TEXT,
                        task_type TEXT,
                        user_goal TEXT,
                        current_state TEXT,
                        current_step_id TEXT,
                        completed_steps TEXT,
                        pending_steps TEXT,
                        step_details TEXT,
                        retry_counts TEXT,
                        recovery_status TEXT,
                        safe_metadata TEXT,
                        schema_version INTEGER,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )
                conn.commit()

    def save_checkpoint(
        self,
        task_id: str,
        user_goal: str = "",
        project_name: str = "",
        task_type: str = "orchestration",
        current_state: str = "READY",
        current_step_id: Optional[str] = None,
        completed_steps: Optional[List[str]] = None,
        pending_steps: Optional[List[str]] = None,
        step_details: Optional[List[Dict[str, Any]]] = None,
        retry_counts: Optional[Dict[str, int]] = None,
        recovery_status: str = "PENDING",
        metadata: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
    ) -> PersistedTaskCheckpoint:
        """Persist a task checkpoint with recursive secret redaction."""
        now = utc_now_iso()
        safe_meta = _redact_dict(metadata or safe_metadata or {})
        safe_step_details = [_redact_dict(s) for s in (step_details or [])]

        checkpoint = PersistedTaskCheckpoint(
            schema_version=self.CURRENT_SCHEMA_VERSION,
            task_id=task_id,
            workflow_id=workflow_id,
            project_name=project_name,
            task_type=task_type,
            user_goal=user_goal,
            current_state=current_state,
            current_step_id=current_step_id,
            completed_steps=completed_steps or [],
            pending_steps=pending_steps or [],
            step_details=safe_step_details,
            retry_counts=retry_counts or {},
            recovery_status=recovery_status,
            safe_metadata=safe_meta,
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO task_checkpoints (
                        task_id, workflow_id, project_name, task_type, user_goal,
                        current_state, current_step_id, completed_steps, pending_steps,
                        step_details, retry_counts, recovery_status, safe_metadata,
                        schema_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        workflow_id = excluded.workflow_id,
                        project_name = excluded.project_name,
                        task_type = excluded.task_type,
                        user_goal = excluded.user_goal,
                        current_state = excluded.current_state,
                        current_step_id = excluded.current_step_id,
                        completed_steps = excluded.completed_steps,
                        pending_steps = excluded.pending_steps,
                        step_details = excluded.step_details,
                        retry_counts = excluded.retry_counts,
                        recovery_status = excluded.recovery_status,
                        safe_metadata = excluded.safe_metadata,
                        schema_version = excluded.schema_version,
                        updated_at = excluded.updated_at
                    """,
                    (
                        checkpoint.task_id,
                        checkpoint.workflow_id,
                        checkpoint.project_name,
                        checkpoint.task_type,
                        checkpoint.user_goal,
                        checkpoint.current_state,
                        checkpoint.current_step_id,
                        json.dumps(checkpoint.completed_steps),
                        json.dumps(checkpoint.pending_steps),
                        json.dumps(checkpoint.step_details),
                        json.dumps(checkpoint.retry_counts),
                        checkpoint.recovery_status,
                        json.dumps(checkpoint.safe_metadata),
                        checkpoint.schema_version,
                        checkpoint.created_at,
                        checkpoint.updated_at,
                    ),
                )
                conn.commit()

        # Emit runtime checkpoint event
        try:
            action_bus.emit(
                ActionEvent(
                    action_type=ActionType.RUNTIME_CHECKPOINT_SAVED,
                    status=ActionStatus.COMPLETED,
                    title=f"Checkpoint Saved: {task_id}",
                    task_id=task_id,
                    description=f"Task '{task_id}' state persisted at step {current_step_id or 'none'}.",
                    safe_metadata={
                        "task_id": task_id,
                        "current_state": current_state,
                        "current_step_id": current_step_id,
                        "completed_count": len(checkpoint.completed_steps),
                    },
                )
            )
        except Exception as exc:
            logger.debug(f"[CHECKPOINT_STORE] Failed to emit checkpoint event: {exc}")

        return checkpoint

    def get_checkpoint(self, task_id: str) -> Optional[PersistedTaskCheckpoint]:
        """Retrieve a persisted checkpoint by task ID."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM task_checkpoints WHERE task_id = ?",
                    (task_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_model(row)

    def list_checkpoints(self, limit: int = 50) -> List[PersistedTaskCheckpoint]:
        """List most recent persisted checkpoints."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM task_checkpoints ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
                return [self._row_to_model(r) for r in rows]

    def list_incomplete_tasks(self) -> List[PersistedTaskCheckpoint]:
        """Fetch tasks interrupted in RUNNING, PLANNING, WAITING_CONFIRMATION, or RETRYING states."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM task_checkpoints 
                    WHERE current_state IN ('RUNNING', 'PLANNING', 'WAITING_CONFIRMATION', 'RETRYING')
                    ORDER BY updated_at DESC
                    """
                )
                rows = cursor.fetchall()
                return [self._row_to_model(r) for r in rows]

    def update_recovery_status(self, task_id: str, status: str, current_state: Optional[str] = None) -> None:
        """Update recovery flag and optionally task state."""
        now = utc_now_iso()
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                if current_state:
                    cursor.execute(
                        "UPDATE task_checkpoints SET recovery_status = ?, current_state = ?, updated_at = ? WHERE task_id = ?",
                        (status, current_state, now, task_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE task_checkpoints SET recovery_status = ?, updated_at = ? WHERE task_id = ?",
                        (status, now, task_id),
                    )
                conn.commit()

    def delete_checkpoint(self, task_id: str) -> bool:
        """Delete checkpoint by task ID."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM task_checkpoints WHERE task_id = ?", (task_id,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted

    def count_checkpoints(self) -> int:
        """Return total count of persisted checkpoints."""
        with self._lock:
            with self._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM task_checkpoints")
                row = cursor.fetchone()
                return row[0] if row else 0

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> PersistedTaskCheckpoint:
        """Convert SQLite row to PersistedTaskCheckpoint model with safe fallback for corrupted payloads."""
        def safe_json_loads(val: Any, default: Any) -> Any:
            if not val:
                return default
            try:
                return json.loads(val)
            except Exception:
                return default

        return PersistedTaskCheckpoint(
            schema_version=row["schema_version"],
            task_id=row["task_id"],
            workflow_id=row["workflow_id"],
            project_name=row["project_name"],
            task_type=row["task_type"],
            user_goal=row["user_goal"],
            current_state=row["current_state"],
            current_step_id=row["current_step_id"],
            completed_steps=safe_json_loads(row["completed_steps"], []),
            pending_steps=safe_json_loads(row["pending_steps"], []),
            step_details=safe_json_loads(row["step_details"], []),
            retry_counts=safe_json_loads(row["retry_counts"], {}),
            recovery_status=row["recovery_status"],
            safe_metadata=safe_json_loads(row["safe_metadata"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Global default singleton
checkpoint_store = CheckpointStore()

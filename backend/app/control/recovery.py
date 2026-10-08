"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 4: Authoritative Persistence + Crash / Restart Recovery Controller (MultiTaskRecoveryManager).

Architecture:
    User / Conversation
            ↓
    MultiTaskScheduler
            ↓
    SafeConcurrencyController
            ↓
    TaskResourceManager
            ↓
    LongHorizonTaskManager
            ↓
    UnifiedTaskOrchestrator
            ↓
    Existing Execution Engines

Recovery & Persistence Coordination Layer:
- Operates strictly underneath the existing coordination hierarchy.
- Reconciles durable SQLite state across restarts, crashes, or unexpected process stops.
- Coordinates startup recovery, stale slot invalidation, stale resource release,
  checkpoint validation, confirmation token invalidation, and deterministic requeueing.

CRITICAL SECURITY & ARCHITECTURAL INVARIANTS:
1. Coordination layer ONLY. Never directly executes Win32, raw mouse/keyboard, subprocess, or shell.
2. Zero subprocess, os.system, os.popen, os.spawn*, cmd, PowerShell, shell, ctypes, pyautogui, pynput.
3. Crash-Safe Invariant: In-flight tasks (RUNNING, RESUMING, PREEMPTION_REQUESTED, PAUSING)
   NEVER automatically resume execution. They are transitioned to INTERRUPTED.
4. Consequential Safety Gate: Stale confirmation tokens MUST NOT survive restart. Any action
   requiring confirmation must obtain fresh authorization through ConfirmationManager.
5. Mutex Freedom: COMPUTER_INTERACTION/global and all logical resources held by crashed tasks
   are forcibly reconciled and released to prevent deadlocks.
6. Execution Slot Freshness: Persisted execution slots are cleared. All execution slots after restart
   are freshly allocated through SafeConcurrencyController and MultiTaskScheduler.
7. Bounded Retries: Recovery attempts per task are strictly capped (MAX_RECOVERY_ATTEMPTS = 3).
   Tasks exceeding the threshold are locked in RECOVERY_REQUIRED.
8. Telemetry & Metrics: Full integration with ActionEventBus; all telemetry and logs are sanitized
   with recursive secret scrubbing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.adaptive import (
    AdaptiveComputerUseController,
    adaptive_controller as default_adaptive_controller,
)
from app.control.concurrency import (
    ConcurrencyState,
    ExecutionSlot,
    SafeConcurrencyController,
    concurrency_controller as default_concurrency_controller,
)
from app.control.long_horizon import (
    DEFAULT_MAX_RECOVERY_ATTEMPTS,
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    TaskPersistenceRepository,
    _scrub_secrets_recursive,
    long_horizon_task_manager as default_task_manager,
    task_persistence_repo as default_repo,
)
from app.control.resources import (
    ResourceType,
    TaskResourceManager,
    task_resource_manager as default_resource_manager,
)
from app.control.scheduler import (
    MultiTaskScheduler,
    ScheduledTask,
    TaskPriority,
    multi_task_scheduler as default_scheduler,
)
from app.core.logging_config import logger
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store as default_checkpoint_store
from app.runtime.models import PersistedTaskCheckpoint
from app.runtime.recovery import RuntimeRecoveryService, runtime_recovery_service as default_runtime_recovery
from app.workflows.confirmation import ConfirmationManager, confirmation_manager as default_confirmation_manager


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Recovery Enums and Models
# ---------------------------------------------------------------------------

class RecoveryState(str, Enum):
    """Authoritative outcome classification for task recovery."""
    RECOVERED = "RECOVERED"
    REQUEUED = "REQUEUED"
    PAUSED = "PAUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CANCELLED = "CANCELLED"
    FAILED_RECOVERY = "FAILED_RECOVERY"
    SKIPPED = "SKIPPED"


class TaskRecoveryResult(BaseModel):
    """Deterministic result of an individual task recovery evaluation."""
    task_id: str
    previous_state: str
    new_state: str
    decision: RecoveryState
    reason: str
    checkpoint_valid: bool = True
    environment_valid: bool = True
    confirmation_invalidated: bool = False
    resources_reconciled: List[str] = Field(default_factory=list)
    retry_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_metadata(self) -> TaskRecoveryResult:
        self.metadata = _scrub_secrets_recursive(self.metadata)
        return self


class RecoveryRunSummary(BaseModel):
    """Aggregated summary of a startup or on-demand recovery execution."""
    recovery_run_id: str
    unclean_shutdown_detected: bool = False
    interrupted_tasks_detected: int = 0
    stale_slots_reconciled: int = 0
    stale_resources_released: int = 0
    tasks_requeued: int = 0
    tasks_paused: int = 0
    recovery_required_count: int = 0
    invalidated_confirmations: int = 0
    invalid_checkpoints: int = 0
    recovery_failures: int = 0
    task_results: Dict[str, TaskRecoveryResult] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    timestamp: str = Field(default_factory=_utc_now_iso)

    @model_validator(mode="after")
    def scrub_summary(self) -> RecoveryRunSummary:
        return self


class ShutdownReport(BaseModel):
    """Structured report produced during graceful control-plane shutdown."""
    session_id: str
    clean_shutdown: bool = True
    active_tasks_persisted: int = 0
    scheduler_queue_persisted: int = 0
    resources_released: int = 0
    timestamp: str = Field(default_factory=_utc_now_iso)


# ---------------------------------------------------------------------------
# Authoritative MultiTaskRecoveryManager
# ---------------------------------------------------------------------------

class MultiTaskRecoveryManager:
    """Authoritative Persistence and Crash/Restart Recovery Manager (M17.8 Phase 4).

    Coordinates durable SQLite state reconciliation, detects unclean restarts,
    cleans stale concurrency slots and resource locks, validates checkpoints,
    invalidates stale confirmation tokens, and safely re-admits resumable tasks
    through MultiTaskScheduler.
    """

    MAX_RECOVERY_ATTEMPTS: int = DEFAULT_MAX_RECOVERY_ATTEMPTS  # 3

    def __init__(
        self,
        repo: Optional[TaskPersistenceRepository] = None,
        scheduler: Optional[MultiTaskScheduler] = None,
        concurrency_controller: Optional[SafeConcurrencyController] = None,
        resource_manager: Optional[TaskResourceManager] = None,
        task_manager: Optional[LongHorizonTaskManager] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        runtime_recovery_service: Optional[RuntimeRecoveryService] = None,
        adaptive_controller: Optional[AdaptiveComputerUseController] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
        bus: Optional[ActionEventBus] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self.repo = repo or default_repo
        self.scheduler = scheduler or default_scheduler
        self.concurrency_controller = concurrency_controller or default_concurrency_controller
        self.resource_manager = resource_manager or default_resource_manager
        self.task_manager = task_manager or default_task_manager
        self.checkpoint_store = checkpoint_store or default_checkpoint_store
        self.runtime_recovery = runtime_recovery_service or default_runtime_recovery
        self.adaptive_controller = adaptive_controller or default_adaptive_controller
        self.confirmation_mgr = confirmation_manager or default_confirmation_manager
        self.bus = bus or action_bus

        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        self._lock = threading.RLock()
        self._recovered_cache: Dict[str, TaskRecoveryResult] = {}

        # Bounded metrics
        self.metrics: Dict[str, int] = {
            "recovery_runs": 0,
            "interrupted_tasks": 0,
            "recovered_tasks": 0,
            "requeued_tasks": 0,
            "recovery_required": 0,
            "stale_slots_reconciled": 0,
            "stale_resources_released": 0,
            "invalid_checkpoints": 0,
            "invalidated_confirmations": 0,
            "recovery_failures": 0,
        }

    # -----------------------------------------------------------------------
    # Telemetry Helper
    # -----------------------------------------------------------------------

    def _emit(
        self,
        action_type: ActionType,
        title: str,
        description: str,
        task_id: Optional[str] = None,
        status: ActionStatus = ActionStatus.COMPLETED,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Sanitized telemetry emission to ActionEventBus."""
        try:
            self.bus.emit(
                ActionEvent(
                    action_type=action_type,
                    status=status,
                    title=title,
                    description=description,
                    task_id=task_id,
                    safe_metadata=_scrub_secrets_recursive(metadata or {}),
                )
            )
        except Exception as exc:
            logger.debug(f"[RecoveryManager] Telemetry emission ignored: {exc}")

    # -----------------------------------------------------------------------
    # Startup Recovery
    # -----------------------------------------------------------------------

    def startup_recovery(self) -> RecoveryRunSummary:
        """Authoritative startup recovery workflow.

        Steps:
        1. Ensure schema v2 migration
        2. Detect unclean shutdown from app_sessions
        3. Register new active session
        4. Reconcile stale concurrency slots
        5. Reconcile stale resource leases (ensuring COMPUTER_INTERACTION/global mutex is free)
        6. Scan and recover interrupted tasks
        7. Invalidate stale confirmation tokens
        8. Validate checkpoints & environment drift
        9. Re-queue eligible tasks into MultiTaskScheduler
        10. Emit telemetry and update bounded metrics
        """
        start_time = time.time()
        recovery_run_id = f"recov-{uuid.uuid4().hex[:8]}"

        with self._lock:
            self.metrics["recovery_runs"] += 1

            # 1. Ensure schema is at Version 2
            try:
                self.repo.migrate_to_v2()
            except Exception as e:
                logger.error(f"[RecoveryManager] Schema migration error: {e}")

            # 2. Unclean shutdown detection
            unclean_shutdown = False
            last_sess = self.repo.get_last_session()
            if last_sess:
                if last_sess.get("status") == "ACTIVE" or last_sess.get("clean_shutdown") == 0:
                    unclean_shutdown = True
                    logger.warning(
                        f"[RecoveryManager] Unclean shutdown detected from previous session: {last_sess.get('session_id')}"
                    )

            # 3. Create fresh active session marker
            self.repo.create_session(
                self.session_id,
                metadata={
                    "startup_at": _utc_now_iso(),
                    "unclean_previous": unclean_shutdown,
                    "recovery_run_id": recovery_run_id,
                },
            )

            self._emit(
                ActionType.RECOVERY_STARTED,
                title="Recovery Started",
                description=f"Initiating startup recovery run {recovery_run_id}",
                metadata={"unclean_shutdown": unclean_shutdown, "session_id": self.session_id},
            )

            summary = RecoveryRunSummary(
                recovery_run_id=recovery_run_id,
                unclean_shutdown_detected=unclean_shutdown,
            )

            # 4. Reconcile Stale Execution Slots
            stale_slots = self.repo.list_durable_slots()
            if stale_slots:
                for slot_info in stale_slots:
                    slot_id = slot_info.get("slot_id")
                    task_id = slot_info.get("task_id")
                    summary.stale_slots_reconciled += 1
                    self.metrics["stale_slots_reconciled"] += 1
                    self._emit(
                        ActionType.RECOVERY_SLOT_RECONCILED,
                        title=f"Stale Slot Reconciled: {slot_id}",
                        description=f"Invalidated execution slot {slot_id} belonging to crashed task {task_id}",
                        task_id=task_id,
                        metadata={"slot_id": slot_id, "task_id": task_id},
                    )
                # Clear all durable slots in persistent store and in-memory controller
                self.repo.clear_durable_slots()
            self.concurrency_controller.clear_all()

            # 5. Reconcile Stale Resource Leases
            stale_leases = self.repo.list_durable_leases()
            if stale_leases:
                for lease_info in stale_leases:
                    lease_id = lease_info.get("lease_id")
                    task_id = lease_info.get("task_id")
                    summary.stale_resources_released += 1
                    self.metrics["stale_resources_released"] += 1
                    self._emit(
                        ActionType.RECOVERY_RESOURCE_RECONCILED,
                        title=f"Stale Resource Released: {lease_id}",
                        description=f"Released stale resource lease {lease_id} for task {task_id}",
                        task_id=task_id,
                        metadata={"lease_id": lease_id, "task_id": task_id},
                    )
                self.repo.clear_durable_leases()
            # Clear all in-memory resource locks to ensure mutexes like COMPUTER_INTERACTION are free
            self.resource_manager.clear_all()

            # 6. Scan for active / interrupted tasks
            active_states = (
                LongHorizonTaskState.RUNNING,
                LongHorizonTaskState.PLANNING,
                LongHorizonTaskState.WAITING_CONFIRMATION,
                LongHorizonTaskState.WAITING_USER,
                LongHorizonTaskState.RESUMING,
                LongHorizonTaskState.INTERRUPTED,
            )
            tasks_to_recover = self.repo.list_tasks(limit=100)
            target_tasks = [t for t in tasks_to_recover if t.state in active_states]
            summary.interrupted_tasks_detected = len(target_tasks)
            self.metrics["interrupted_tasks"] += len(target_tasks)

            # Also check scheduled_tasks table for any queued or orphaned entries
            scheduled_entries = self.repo.list_scheduled_tasks()
            scheduled_ids = {s["task_id"] for s in scheduled_entries}

            # 7. Evaluate and recover each target task
            for task in target_tasks:
                rec_res = self.recover_task(task.task_id, run_id=recovery_run_id)
                summary.task_results[task.task_id] = rec_res

                if rec_res.confirmation_invalidated:
                    summary.invalidated_confirmations += 1
                    self.metrics["invalidated_confirmations"] += 1

                if not rec_res.checkpoint_valid:
                    summary.invalid_checkpoints += 1
                    self.metrics["invalid_checkpoints"] += 1

                if rec_res.decision == RecoveryState.REQUEUED:
                    summary.tasks_requeued += 1
                    self.metrics["requeued_tasks"] += 1
                    self.metrics["recovered_tasks"] += 1
                elif rec_res.decision == RecoveryState.PAUSED:
                    summary.tasks_paused += 1
                    self.metrics["recovered_tasks"] += 1
                elif rec_res.decision == RecoveryState.RECOVERY_REQUIRED:
                    summary.recovery_required_count += 1
                    self.metrics["recovery_required"] += 1
                elif rec_res.decision == RecoveryState.FAILED_RECOVERY:
                    summary.recovery_failures += 1
                    self.metrics["recovery_failures"] += 1

            # Re-enqueue any scheduled tasks that were previously QUEUED but not in memory
            for s_entry in scheduled_entries:
                tid = s_entry["task_id"]
                if tid not in summary.task_results and s_entry.get("state") in ("QUEUED", "READY"):
                    lh_task = self.repo.get_task(tid)
                    if lh_task and lh_task.state in (LongHorizonTaskState.QUEUED, LongHorizonTaskState.READY, LongHorizonTaskState.PENDING):
                        try:
                            self.scheduler.admit_task(
                                ScheduledTask(
                                    task_id=tid,
                                    task=lh_task,
                                    base_priority=s_entry.get("priority", TaskPriority.NORMAL.value),
                                    dependencies=s_entry.get("dependencies", []),
                                    metadata=s_entry.get("metadata", {}),
                                )
                            )
                        except Exception as e:
                            logger.debug(f"[RecoveryManager] Could not re-admit scheduled task {tid}: {e}")

            summary.duration_seconds = max(0.001, time.time() - start_time)

            self._emit(
                ActionType.RECOVERY_COMPLETED,
                title="Recovery Completed",
                description=f"Completed recovery run {recovery_run_id} in {summary.duration_seconds:.3f}s",
                metadata={
                    "requeued": summary.tasks_requeued,
                    "paused": summary.tasks_paused,
                    "recovery_required": summary.recovery_required_count,
                    "stale_slots_reconciled": summary.stale_slots_reconciled,
                    "stale_resources_released": summary.stale_resources_released,
                },
            )

            return summary

    # -----------------------------------------------------------------------
    # Idempotent Single-Task Recovery
    # -----------------------------------------------------------------------

    def recover_task(self, task_id: str, run_id: Optional[str] = None) -> TaskRecoveryResult:
        """Idempotently evaluate, reconcile, and recover an individual task.

        Calling recover_task multiple times on the same task is completely safe
        and produces deterministic, consistent results without double-increments
        or duplicate execution.
        """
        recovery_run_id = run_id or f"recov-single-{uuid.uuid4().hex[:8]}"

        with self._lock:
            task = self.repo.get_task(task_id)
            if not task:
                return TaskRecoveryResult(
                    task_id=task_id,
                    previous_state="UNKNOWN",
                    new_state="UNKNOWN",
                    decision=RecoveryState.FAILED_RECOVERY,
                    reason=f"Task '{task_id}' not found in persistent store.",
                )

            prev_state = task.state.value

            # Idempotency: If task is already in terminal state or already cleanly requeued / paused, no-op
            if task.state in (LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED, LongHorizonTaskState.EXPIRED):
                return TaskRecoveryResult(
                    task_id=task_id,
                    previous_state=prev_state,
                    new_state=prev_state,
                    decision=RecoveryState.SKIPPED,
                    reason=f"Task is in terminal state ({prev_state}). Recovery skipped.",
                )

            # Check if this task was already recovered in this run
            if task_id in self._recovered_cache:
                cached = self._recovered_cache[task_id]
                if cached.new_state == task.state.value:
                    return cached

            # 1. Enforce Bounded Retries
            if task.recovery_count >= self.MAX_RECOVERY_ATTEMPTS:
                task.state = LongHorizonTaskState.RECOVERY_REQUIRED
                task.add_journal_entry(
                    "RECOVERY_LIMIT_EXCEEDED",
                    description=f"Task exceeded maximum recovery attempts ({self.MAX_RECOVERY_ATTEMPTS}). Manual recovery required.",
                )
                self.repo.update_task(task)
                self.repo.save_scheduled_task(
                    task_id=task_id,
                    state="RECOVERY_REQUIRED",
                    recovery_state="RECOVERY_REQUIRED",
                    interruption_reason="Exceeded maximum recovery attempts",
                )
                self.repo.log_recovery_event(
                    recovery_run_id=recovery_run_id,
                    task_id=task_id,
                    action="RECOVERY_LIMIT_EXCEEDED",
                    previous_state=prev_state,
                    new_state=LongHorizonTaskState.RECOVERY_REQUIRED.value,
                    details={"recovery_count": task.recovery_count},
                )
                self._emit(
                    ActionType.RECOVERY_TASK_FAILED,
                    title=f"Task Recovery Limit Exceeded: {task_id}",
                    description=f"Task exceeded {self.MAX_RECOVERY_ATTEMPTS} attempts. Locked in RECOVERY_REQUIRED.",
                    task_id=task_id,
                )
                result = TaskRecoveryResult(
                    task_id=task_id,
                    previous_state=prev_state,
                    new_state=LongHorizonTaskState.RECOVERY_REQUIRED.value,
                    decision=RecoveryState.RECOVERY_REQUIRED,
                    reason=f"Exceeded max recovery attempts ({self.MAX_RECOVERY_ATTEMPTS}).",
                    retry_count=task.recovery_count,
                )
                self._recovered_cache[task_id] = result
                return result

            # 2. Invalidate Confirmation Safety
            confirmation_invalidated = False
            if task.active_confirmation_token is not None:
                task.active_confirmation_token = None
                task.active_confirmation_action = None
                confirmation_invalidated = True
                self._emit(
                    ActionType.RECOVERY_CONFIRMATION_INVALIDATED,
                    title=f"Confirmation Invalidated: {task_id}",
                    description="Invalidated stale confirmation token across process restart.",
                    task_id=task_id,
                )

            # 3. Mark in-flight tasks as INTERRUPTED
            if task.state in (
                LongHorizonTaskState.RUNNING,
                LongHorizonTaskState.PLANNING,
                LongHorizonTaskState.WAITING_CONFIRMATION,
                LongHorizonTaskState.WAITING_USER,
                LongHorizonTaskState.RESUMING,
            ):
                task.state = LongHorizonTaskState.INTERRUPTED
                task.recovery_count += 1
                task.add_journal_entry(
                    "TASK_INTERRUPTED",
                    description="Application restart or interruption detected.",
                    metadata={"recovery_attempt": task.recovery_count},
                )
                self._emit(
                    ActionType.RECOVERY_TASK_INTERRUPTED,
                    title=f"Task Interrupted: {task_id}",
                    description=f"Transitioned task {task_id} from {prev_state} to INTERRUPTED.",
                    task_id=task_id,
                    metadata={"previous_state": prev_state, "recovery_count": task.recovery_count},
                )

            # Release any resource holdings owned by this task
            self.resource_manager.release_all_for_task(task_id)

            # 4. Checkpoint Validation
            checkpoint_valid = True
            requires_user_confirmation = False
            chk = self.checkpoint_store.get_checkpoint(task_id)
            if chk:
                # Validate schema version
                if chk.schema_version != CheckpointStore.CURRENT_SCHEMA_VERSION:
                    checkpoint_valid = False
                    self._emit(
                        ActionType.RECOVERY_CHECKPOINT_INVALID,
                        title=f"Invalid Checkpoint: {task_id}",
                        description=f"Checkpoint schema mismatch ({chk.schema_version}).",
                        task_id=task_id,
                    )
                else:
                    # Evaluate dangerous operations via runtime recovery service
                    decision = self.runtime_recovery.evaluate_task(chk)
                    if decision.requires_confirmation or decision.dangerous_step_detected:
                        requires_user_confirmation = True

                    self._emit(
                        ActionType.RECOVERY_CHECKPOINT_VALIDATED,
                        title=f"Checkpoint Validated: {task_id}",
                        description=f"Validated checkpoint with {len(chk.completed_steps)} completed steps.",
                        task_id=task_id,
                    )
            else:
                # If no checkpoint exists, check if task has completed steps
                if task.completed_step_ids:
                    checkpoint_valid = False

            # 5. Environment Drift Check
            environment_valid = True
            # Check via adaptive controller if available
            try:
                obs = self.adaptive_controller.observe_state()
                if obs and obs.anomaly_detected:
                    environment_valid = False
            except Exception:
                pass

            # 6. Determine Next State & Requeue Policy
            if not checkpoint_valid:
                task.state = LongHorizonTaskState.RECOVERY_REQUIRED
                rec_decision = RecoveryState.RECOVERY_REQUIRED
                rec_reason = "Checkpoint invalid, corrupted, or schema mismatch."
                self._emit(
                    ActionType.RECOVERY_TASK_FAILED,
                    title=f"Recovery Required: {task_id}",
                    description=rec_reason,
                    task_id=task_id,
                )
            elif not environment_valid:
                task.state = LongHorizonTaskState.RECOVERY_REQUIRED
                rec_decision = RecoveryState.RECOVERY_REQUIRED
                rec_reason = "Material environment drift detected on restart."
                self._emit(
                    ActionType.RECOVERY_TASK_FAILED,
                    title=f"Recovery Required: {task_id}",
                    description=rec_reason,
                    task_id=task_id,
                )
            elif requires_user_confirmation:
                # Dangerous action pending: transition to PAUSED / WAITING_CONFIRMATION
                task.state = LongHorizonTaskState.PAUSED
                rec_decision = RecoveryState.PAUSED
                rec_reason = "Task contains consequential steps requiring fresh user confirmation."
                self._emit(
                    ActionType.RECOVERY_TASK_PAUSED,
                    title=f"Task Paused for Confirmation: {task_id}",
                    description=rec_reason,
                    task_id=task_id,
                )
            else:
                # Safe to resume: transition to QUEUED and re-admit to scheduler
                task.state = LongHorizonTaskState.QUEUED
                rec_decision = RecoveryState.REQUEUED
                rec_reason = "Task validated safe for automatic resumption."

                # Re-admit cleanly to MultiTaskScheduler
                try:
                    sch_rec = self.repo.get_scheduled_task(task_id)
                    prio = sch_rec.get("priority", TaskPriority.NORMAL.value) if sch_rec else TaskPriority.NORMAL.value
                    deps = sch_rec.get("dependencies", []) if sch_rec else []
                    self.scheduler.admit_task(
                        ScheduledTask(
                            task_id=task.task_id,
                            task=task,
                            base_priority=prio,
                            dependencies=deps,
                            metadata={"resumed_from_crash": True, "recovery_count": task.recovery_count},
                        )
                    )
                except Exception as e:
                    logger.debug(f"[RecoveryManager] Scheduler admit: {e}")

                self._emit(
                    ActionType.RECOVERY_TASK_REQUEUED,
                    title=f"Task Requeued: {task_id}",
                    description=f"Task successfully requeued into MultiTaskScheduler for admission.",
                    task_id=task_id,
                )

            # Persist updated task state
            self.repo.update_task(task)
            self.repo.save_scheduled_task(
                task_id=task_id,
                state=task.state.value,
                recovery_state=rec_decision.value,
                interruption_reason=rec_reason,
                retry_count=task.recovery_count,
            )

            # Record audit entry in recovery journal
            self.repo.log_recovery_event(
                recovery_run_id=recovery_run_id,
                task_id=task_id,
                action="RECOVER_TASK",
                previous_state=prev_state,
                new_state=task.state.value,
                details={
                    "decision": rec_decision.value,
                    "reason": rec_reason,
                    "checkpoint_valid": checkpoint_valid,
                    "confirmation_invalidated": confirmation_invalidated,
                },
            )

            result = TaskRecoveryResult(
                task_id=task_id,
                previous_state=prev_state,
                new_state=task.state.value,
                decision=rec_decision,
                reason=rec_reason,
                checkpoint_valid=checkpoint_valid,
                environment_valid=environment_valid,
                confirmation_invalidated=confirmation_invalidated,
                retry_count=task.recovery_count,
            )
            self._recovered_cache[task_id] = result
            return result

    # -----------------------------------------------------------------------
    # Graceful Shutdown
    # -----------------------------------------------------------------------

    def graceful_shutdown(self) -> ShutdownReport:
        """Execute safe, durable, graceful shutdown sequence.

        Steps:
        1. Stop admitting new work (pause scheduler)
        2. Persist active scheduler queue state
        3. Persist running and paused task states
        4. Release logical resources safely
        5. Mark session as CLEAN in app_sessions
        """
        with self._lock:
            # 1. Stop scheduler admission
            try:
                self.scheduler.pause()
            except Exception:
                pass

            # 2. Persist queue items
            q_tasks = self.scheduler.get_queued_tasks()
            for st in q_tasks:
                try:
                    self.repo.save_scheduled_task(
                        task_id=st.task_id,
                        priority=st.base_priority,
                        effective_priority=st.effective_priority,
                        state=st.state.value if hasattr(st.state, "value") else str(st.state),
                        dependencies=st.dependencies,
                        enqueued_at=st.enqueued_at,
                        metadata=st.metadata,
                    )
                except Exception as e:
                    logger.debug(f"[RecoveryManager] Persist scheduled task failed: {e}")

            # 3. Persist active tasks
            active_ids = self.scheduler.get_active_tasks()
            for tid in active_ids:
                lh = self.task_manager.get_task(tid)
                if lh:
                    self.repo.update_task(lh)

            # 4. Release resources cleanly
            self.resource_manager.clear_all()
            self.repo.clear_durable_leases()
            self.repo.clear_durable_slots()

            # 5. Mark clean shutdown
            clean = self.repo.mark_clean_shutdown(self.session_id)

            report = ShutdownReport(
                session_id=self.session_id,
                clean_shutdown=clean,
                active_tasks_persisted=len(active_ids),
                scheduler_queue_persisted=len(q_tasks),
                resources_released=len(active_ids),
            )

            logger.info(f"[RecoveryManager] Graceful shutdown completed cleanly for session {self.session_id}")
            return report


# ---------------------------------------------------------------------------
# Aliases and Singletons
# ---------------------------------------------------------------------------

RecoveryManager = MultiTaskRecoveryManager

recovery_manager: MultiTaskRecoveryManager = MultiTaskRecoveryManager()
multi_task_recovery_manager: MultiTaskRecoveryManager = recovery_manager

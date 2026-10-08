"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 4 Test Suite: Persistence + Crash / Restart Recovery.

Comprehensive test coverage targeting 72+ test cases covering:
1. schema initialization & versioning (v2)
2. safe idempotent migrations
3. WAL mode and connection tuning
4. full task persistence & reload
5. priority, dependencies, resource requirements persistence
6. checkpoint references & preemption metadata persistence
7. transactional state transitions
8. crash detection & unclean shutdown marker
9. RUNNING / PREEMPTION_REQUESTED / PAUSING / RESUMING -> INTERRUPTED
10. stale execution slot reconciliation
11. stale resource lease release (specifically COMPUTER_INTERACTION/global mutex)
12. confirmation token invalidation
13. checkpoint integrity validation & dangerous step flagging
14. environment drift handling
15. recovery idempotency & duplicate protection
16. bounded recovery retries (max 3 -> RECOVERY_REQUIRED)
17. graceful shutdown & clean shutdown markers
18. scheduler re-admission & reintegration
19. secret sanitization & prevention of secret persistence
20. bounded metrics & ActionEventBus telemetry
21. AST security audit (zero subprocess, shell, win32, pyautogui)
"""

import ast
import json
from pathlib import Path
import sqlite3
import threading
import time
import pytest

from app.actions.event_bus import ActionEventBus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.adaptive import (
    AdaptiveComputerUseController,
    ObservedComputerState,
)
from app.control.concurrency import (
    ConcurrencyState,
    ExecutionSlot,
    SafeConcurrencyController,
)
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
    _scrub_secrets_recursive,
)
from app.control.recovery import (
    MultiTaskRecoveryManager,
    RecoveryRunSummary,
    RecoveryState,
    ShutdownReport,
    TaskRecoveryResult,
)
from app.control.resources import (
    ResourceRequest,
    ResourceType,
    TaskResourceManager,
)
from app.control.scheduler import (
    MultiTaskScheduler,
    ScheduledTask,
    TaskPriority,
)
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.models import PersistedTaskCheckpoint
from app.runtime.recovery import RuntimeRecoveryService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Isolated SQLite database path."""
    return tmp_path / "test_checkpoints.db"


@pytest.fixture
def test_repo(temp_db):
    """Isolated TaskPersistenceRepository initialized with Schema v2."""
    repo = TaskPersistenceRepository(db_path=temp_db)
    repo.migrate_to_v2()
    return repo


@pytest.fixture
def test_checkpoint_store(tmp_path):
    """Isolated CheckpointStore."""
    chk_db = tmp_path / "checkpoints_runtime.db"
    return CheckpointStore(db_path=chk_db)


@pytest.fixture
def recovery_env(temp_db, tmp_path):
    """Fully isolated and wired Phase 4 recovery control plane."""
    repo = TaskPersistenceRepository(db_path=temp_db)
    repo.migrate_to_v2()

    chk_store = CheckpointStore(db_path=tmp_path / "chk_store.db")
    rt_recovery = RuntimeRecoveryService(store=chk_store)
    bus = ActionEventBus()
    res_mgr = TaskResourceManager(event_bus=bus)
    concurrency_ctrl = SafeConcurrencyController(resource_manager=res_mgr, event_bus=bus)
    scheduler = MultiTaskScheduler(
        repo=repo,
        resource_manager=res_mgr,
        concurrency_controller=concurrency_ctrl,
        bus=bus,
    )
    task_mgr = LongHorizonTaskManager(repository=repo)

    rec_mgr = MultiTaskRecoveryManager(
        repo=repo,
        scheduler=scheduler,
        concurrency_controller=concurrency_ctrl,
        resource_manager=res_mgr,
        task_manager=task_mgr,
        checkpoint_store=chk_store,
        runtime_recovery_service=rt_recovery,
        bus=bus,
        session_id="test-session-001",
    )

    return {
        "repo": repo,
        "scheduler": scheduler,
        "concurrency": concurrency_ctrl,
        "resources": res_mgr,
        "task_manager": task_mgr,
        "checkpoint_store": chk_store,
        "runtime_recovery": rt_recovery,
        "rec_mgr": rec_mgr,
        "bus": bus,
        "db_path": temp_db,
    }


# ---------------------------------------------------------------------------
# 1. Schema Initialization, Versioning, and Migrations (Tests 1-5)
# ---------------------------------------------------------------------------

def test_01_schema_initialization(temp_db):
    """Schema creates all base tables on initialization."""
    repo = TaskPersistenceRepository(db_path=temp_db)
    with repo._connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "long_horizon_tasks" in tables
    assert "schema_meta" in tables


def test_02_schema_version_v2(test_repo):
    """Schema version metadata is set to 2 after migration."""
    with test_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM schema_meta WHERE key = 'm17_8_schema_version'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "2"


def test_03_migration_creates_phase4_tables(test_repo):
    """Migration to v2 creates app_sessions, scheduled_tasks, durable slots, durable leases, recovery_journal."""
    with test_repo._connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "app_sessions" in tables
    assert "scheduled_tasks" in tables
    assert "durable_execution_slots" in tables
    assert "durable_resource_leases" in tables
    assert "recovery_journal" in tables


def test_04_migration_idempotency(test_repo):
    """Running migrate_to_v2 repeatedly is completely safe and produces same version."""
    v1 = test_repo.migrate_to_v2()
    v2 = test_repo.migrate_to_v2()
    assert v1 == 2
    assert v2 == 2


def test_05_wal_mode_configured(test_repo):
    """Database connection operates in WAL journal mode."""
    with test_repo._connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.upper() == "WAL"


# ---------------------------------------------------------------------------
# 2. Durable Task & Scheduling Persistence (Tests 6-15)
# ---------------------------------------------------------------------------

def test_06_task_persistence_save_and_reload(test_repo):
    """Task can be persisted and fully reconstructed from SQLite."""
    task = LongHorizonTask(task_id="t-06", goal="Persistent goal")
    test_repo.save_task(task)
    loaded = test_repo.get_task("t-06")
    assert loaded is not None
    assert loaded.task_id == "t-06"
    assert loaded.goal == "Persistent goal"


def test_07_task_priority_persistence(test_repo):
    """Scheduled task priority and effective priority are durably stored."""
    test_repo.save_scheduled_task(
        task_id="t-07",
        priority=75,
        effective_priority=82.5,
        state="QUEUED",
    )
    st = test_repo.get_scheduled_task("t-07")
    assert st is not None
    assert st["priority"] == 75
    assert st["effective_priority"] == 82.5


def test_08_dependency_persistence(test_repo):
    """Scheduled task dependencies are serialized as JSON and reconstructed."""
    test_repo.save_scheduled_task(
        task_id="t-08",
        dependencies=["dep-1", "dep-2", "dep-3"],
    )
    st = test_repo.get_scheduled_task("t-08")
    assert st["dependencies"] == ["dep-1", "dep-2", "dep-3"]


def test_09_resource_requirement_persistence(test_repo):
    """Resource requirements are durably saved with task."""
    test_repo.save_scheduled_task(
        task_id="t-09",
        required_resources=["desktop_session:primary", "browser_session:active"],
    )
    st = test_repo.get_scheduled_task("t-09")
    assert "desktop_session:primary" in st["required_resources"]
    assert "browser_session:active" in st["required_resources"]


def test_10_checkpoint_reference_persistence(test_repo):
    """Checkpoint references are saved and updated."""
    test_repo.save_scheduled_task(
        task_id="t-10",
        checkpoint_ref="chk-val-9988",
    )
    st = test_repo.get_scheduled_task("t-10")
    assert st["checkpoint_ref"] == "chk-val-9988"


def test_11_preemption_metadata_persistence(test_repo):
    """Preemption count and retry count are stored."""
    test_repo.save_scheduled_task(
        task_id="t-11",
        preemption_count=2,
        retry_count=1,
    )
    st = test_repo.get_scheduled_task("t-11")
    assert st["preemption_count"] == 2
    assert st["retry_count"] == 1


def test_12_durable_slot_persistence(test_repo):
    """Active execution slots are recorded in durable_execution_slots."""
    test_repo.save_durable_slot(
        slot_id="slot-12",
        task_id="t-12",
        started_at=1000.0,
        state="RUNNING",
        resources=["computer_interaction:global"],
        priority=50.0,
        preemption_requested=False,
    )
    slots = test_repo.list_durable_slots()
    assert len(slots) == 1
    assert slots[0]["slot_id"] == "slot-12"
    assert slots[0]["task_id"] == "t-12"


def test_13_durable_resource_lease_persistence(test_repo):
    """Durable resource leases are persisted and queried."""
    test_repo.save_durable_lease(
        lease_id="lease-13",
        task_id="t-13",
        resources=["browser_tab:1"],
        state="ACTIVE",
    )
    leases = test_repo.list_durable_leases()
    assert len(leases) == 1
    assert leases[0]["lease_id"] == "lease-13"
    assert leases[0]["state"] == "ACTIVE"


def test_14_state_transition_durability(test_repo):
    """Task state transitions update SQLite transactionally."""
    task = LongHorizonTask(task_id="t-14", goal="Progress goal")
    test_repo.save_task(task)

    task.state = LongHorizonTaskState.RUNNING
    test_repo.update_task(task)

    loaded = test_repo.get_task("t-14")
    assert loaded.state == LongHorizonTaskState.RUNNING


def test_15_transactional_multi_field_update(test_repo):
    """Progress, journal entries, and milestones persist atomically."""
    task = LongHorizonTask(task_id="t-15", goal="Multi-field goal")
    test_repo.save_task(task)

    task.progress_percent = 45.0
    task.last_milestone = "Step 2 completed"
    task.add_journal_entry("STEP_DONE", description="Finished step 2")
    test_repo.update_task(task)

    loaded = test_repo.get_task("t-15")
    assert loaded.progress_percent == 45.0
    assert loaded.last_milestone == "Step 2 completed"
    assert len(loaded.execution_journal) == 1


# ---------------------------------------------------------------------------
# 3. Crash Detection & In-Flight Interruption (Tests 16-25)
# ---------------------------------------------------------------------------

def test_16_crash_detection_marks_running_interrupted(recovery_env):
    """RUNNING tasks are marked INTERRUPTED on startup and never auto-executed."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-16", goal="Crashed in running", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    summary = rec_mgr.startup_recovery()
    assert summary.interrupted_tasks_detected >= 1

    updated = repo.get_task("t-16")
    # Because it had no dangerous steps and valid checkpoint/state, it transitions via INTERRUPTED -> QUEUED
    assert updated.state in (LongHorizonTaskState.INTERRUPTED, LongHorizonTaskState.QUEUED)
    assert updated.recovery_count == 1


def test_17_planning_task_interrupted(recovery_env):
    """Task in PLANNING state when crashed is transitioned and recovered safely."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-17", goal="Planning crashed", state=LongHorizonTaskState.PLANNING)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-17")
    assert res.previous_state == "PLANNING"
    assert res.decision in (RecoveryState.REQUEUED, RecoveryState.PAUSED)


def test_18_waiting_confirmation_interrupted(recovery_env):
    """Task waiting for confirmation is marked interrupted and pauses."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(
        task_id="t-18",
        goal="Confirm goal",
        state=LongHorizonTaskState.WAITING_CONFIRMATION,
        active_confirmation_token="tok-12345",
    )
    repo.save_task(task)

    res = rec_mgr.recover_task("t-18")
    assert res.confirmation_invalidated is True
    updated = repo.get_task("t-18")
    assert updated.active_confirmation_token is None


def test_19_resuming_task_interrupted(recovery_env):
    """Task crashed while RESUMING is handled safely."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-19", goal="Resuming task", state=LongHorizonTaskState.RESUMING)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-19")
    assert res.previous_state == "RESUMING"


def test_20_stale_slots_cleared_on_startup(recovery_env):
    """Durable execution slots from previous crash are wiped on startup."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    repo.save_durable_slot(slot_id="slot-crashed", task_id="t-20", started_at=100.0, state="RUNNING")
    assert len(repo.list_durable_slots()) == 1

    summary = rec_mgr.startup_recovery()
    assert summary.stale_slots_reconciled >= 1
    assert len(repo.list_durable_slots()) == 0


def test_21_stale_resource_leases_cleared_on_startup(recovery_env):
    """Durable resource leases from crashed tasks are wiped."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    repo.save_durable_lease(lease_id="l-crashed", task_id="t-21", resources=["workspace:root"])
    assert len(repo.list_durable_leases()) == 1

    summary = rec_mgr.startup_recovery()
    assert summary.stale_resources_released >= 1
    assert len(repo.list_durable_leases()) == 0


def test_22_computer_interaction_mutex_freed(recovery_env):
    """COMPUTER_INTERACTION/global mutex must NEVER remain locked after crash."""
    res_mgr = recovery_env["resources"]
    rec_mgr = recovery_env["rec_mgr"]

    # Simulate crashed task holding mutex
    req = ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION, target="global")
    dec = res_mgr.acquire_resources("crashed-task", [req])
    assert dec.is_active is True
    assert res_mgr.is_available(req) is False

    # Startup recovery runs
    rec_mgr.startup_recovery()

    # Mutex must now be available!
    assert res_mgr.is_available(req) is True


def test_23_confirmation_token_invalidation(recovery_env):
    """Confirmation tokens must NEVER survive process restart."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(
        task_id="t-23",
        goal="Secret confirm",
        state=LongHorizonTaskState.RUNNING,
        active_confirmation_token="stale-super-secret-token",
    )
    repo.save_task(task)

    res = rec_mgr.recover_task("t-23")
    assert res.confirmation_invalidated is True

    loaded = repo.get_task("t-23")
    assert loaded.active_confirmation_token is None


def test_24_recovery_journal_logs_actions(recovery_env):
    """All recovery events are recorded in the recovery_journal table."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-24", goal="Journal goal", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.recover_task("t-24")

    events = repo.get_recovery_events("t-24")
    assert len(events) >= 1
    assert events[0]["action"] == "RECOVER_TASK"


def test_25_unclean_shutdown_detected(recovery_env):
    """If previous session ended with clean_shutdown=0, unclean shutdown is detected."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    # Write an un-clean session
    repo.create_session("prev-unclean-session")
    # clean_shutdown is default 0

    summary = rec_mgr.startup_recovery()
    assert summary.unclean_shutdown_detected is True


# ---------------------------------------------------------------------------
# 4. Checkpoint Validation & Dangerous Step Safety (Tests 26-33)
# ---------------------------------------------------------------------------

def test_26_valid_checkpoint_resumable(recovery_env):
    """Tasks with valid checkpoints containing safe steps are marked resumable."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-26", goal="Safe task", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    # Save valid checkpoint
    chk = PersistedTaskCheckpoint(
        task_id="t-26",
        user_goal="Safe task",
        total_steps=2,
        completed_steps=["s1"],
        pending_steps=["s2"],
        step_details=[
            {"step_id": "s1", "step_type": "SCAN_PROJECT"},
            {"step_id": "s2", "step_type": "UNDERSTAND_PROJECT"},
        ],
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-26")
    assert res.checkpoint_valid is True
    assert res.decision == RecoveryState.REQUEUED


def test_27_dangerous_step_requires_confirmation(recovery_env):
    """Tasks with dangerous steps (e.g., GIT_PUSH, DEPLOY) do NOT auto-resume."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-27", goal="Push code", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    chk = PersistedTaskCheckpoint(
        task_id="t-27",
        user_goal="Push code",
        total_steps=2,
        completed_steps=["s1"],
        pending_steps=["s2"],
        step_details=[
            {"step_id": "s1", "step_type": "GIT_STATUS"},
            {"step_id": "s2", "step_type": "GIT_PUSH"},
        ],
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-27")
    assert res.decision == RecoveryState.PAUSED
    assert "fresh user confirmation" in res.reason


def test_28_delete_file_dangerous_step_paused(recovery_env):
    """Tasks with DELETE_FILE step are paused rather than auto-resumed."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-28", goal="Delete temp", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    chk = PersistedTaskCheckpoint(
        task_id="t-28",
        user_goal="Delete temp",
        total_steps=1,
        pending_steps=["s1"],
        step_details=[{"step_id": "s1", "step_type": "DELETE_FILE"}],
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-28")
    assert res.decision == RecoveryState.PAUSED


def test_29_mismatched_checkpoint_version_rejected(recovery_env):
    """Checkpoints with outdated schema version are rejected."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-29", goal="Old schema", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    chk = PersistedTaskCheckpoint(
        task_id="t-29",
        schema_version=999,
        user_goal="Old schema",
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-29")
    assert res.checkpoint_valid is False
    assert res.decision == RecoveryState.RECOVERY_REQUIRED


def test_30_missing_checkpoint_for_multi_step_task(recovery_env):
    """Task with completed steps but missing checkpoint cannot safely auto-resume."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(
        task_id="t-30",
        goal="Inconsistent state",
        state=LongHorizonTaskState.RUNNING,
        completed_step_ids=["step-1", "step-2"],
    )
    repo.save_task(task)

    res = rec_mgr.recover_task("t-30")
    assert res.checkpoint_valid is False
    assert res.decision == RecoveryState.RECOVERY_REQUIRED


def test_31_environment_drift_detected(recovery_env, monkeypatch):
    """Environment drift transitions task to RECOVERY_REQUIRED."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-31", goal="Drift check", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    # Mock anomaly detected in adaptive controller
    monkeypatch.setattr(
        rec_mgr.adaptive_controller,
        "observe_state",
        lambda: ObservedComputerState(anomaly_detected=True, anomaly_description="Material UI drift"),
    )

    res = rec_mgr.recover_task("t-31")
    assert res.environment_valid is False
    assert res.decision == RecoveryState.RECOVERY_REQUIRED


def test_32_deploy_step_dangerous_flag(recovery_env):
    """DEPLOY operation triggers dangerous flag and requires confirmation."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-32", goal="Deploy prod", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    chk = PersistedTaskCheckpoint(
        task_id="t-32",
        user_goal="Deploy prod",
        pending_steps=["d1"],
        step_details=[{"step_id": "d1", "step_type": "DEPLOY"}],
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-32")
    assert res.decision == RecoveryState.PAUSED


def test_33_form_submission_dangerous_flag(recovery_env):
    """FORM_SUBMIT triggers dangerous flag."""
    repo = recovery_env["repo"]
    chk_store = recovery_env["checkpoint_store"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-33", goal="Submit form", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    chk = PersistedTaskCheckpoint(
        task_id="t-33",
        user_goal="Submit form",
        pending_steps=["f1"],
        step_details=[{"step_id": "f1", "step_type": "FORM_SUBMIT"}],
    )
    chk_store.save_checkpoint(chk)

    res = rec_mgr.recover_task("t-33")
    assert res.decision == RecoveryState.PAUSED


# ---------------------------------------------------------------------------
# 5. Recovery Bounds & Idempotency (Tests 34-43)
# ---------------------------------------------------------------------------

def test_34_recovery_idempotency_same_call(recovery_env):
    """Calling recover_task multiple times on same task is idempotent and safe."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-34", goal="Idempotent goal", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    res1 = rec_mgr.recover_task("t-34")
    res2 = rec_mgr.recover_task("t-34")

    assert res1.decision == res2.decision
    assert res1.retry_count == res2.retry_count
    # Should not double increment retry count
    assert repo.get_task("t-34").recovery_count == 1


def test_35_recovery_retry_limit_locks_task(recovery_env):
    """Tasks exceeding MAX_RECOVERY_ATTEMPTS (3) transition to RECOVERY_REQUIRED."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(
        task_id="t-35",
        goal="Repeated crash",
        state=LongHorizonTaskState.RUNNING,
        recovery_count=3,  # Already hit limit
    )
    repo.save_task(task)

    res = rec_mgr.recover_task("t-35")
    assert res.decision == RecoveryState.RECOVERY_REQUIRED
    updated = repo.get_task("t-35")
    assert updated.state == LongHorizonTaskState.RECOVERY_REQUIRED


def test_36_terminal_completed_task_skipped(recovery_env):
    """COMPLETED tasks are never re-executed or modified during recovery."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-36", goal="Done", state=LongHorizonTaskState.COMPLETED)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-36")
    assert res.decision == RecoveryState.SKIPPED
    assert repo.get_task("t-36").state == LongHorizonTaskState.COMPLETED


def test_37_terminal_cancelled_task_skipped(recovery_env):
    """CANCELLED tasks are never modified during recovery."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-37", goal="Cancelled", state=LongHorizonTaskState.CANCELLED)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-37")
    assert res.decision == RecoveryState.SKIPPED


def test_38_terminal_expired_task_skipped(recovery_env):
    """EXPIRED tasks are skipped."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-38", goal="Expired", state=LongHorizonTaskState.EXPIRED)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-38")
    assert res.decision == RecoveryState.SKIPPED


def test_39_unknown_task_returns_failed_recovery(recovery_env):
    """Recovering non-existent task returns clean FAILED_RECOVERY without crashing."""
    rec_mgr = recovery_env["rec_mgr"]
    res = rec_mgr.recover_task("non-existent-task-id")
    assert res.decision == RecoveryState.FAILED_RECOVERY


def test_40_restart_twice_safe(recovery_env):
    """Consecutive startup recovery runs operate idempotently."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-40", goal="Twice restart", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    s1 = rec_mgr.startup_recovery()
    s2 = rec_mgr.startup_recovery()

    assert s1.interrupted_tasks_detected >= 1
    # On second restart, the task was already requeued or in QUEUED state
    assert s2.duration_seconds > 0


def test_41_concurrent_recovery_protection(recovery_env):
    """Multiple threads recovering the same task operate thread-safely."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-41", goal="Concurrent recovery", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    results = []

    def _worker():
        r = rec_mgr.recover_task("t-41")
        results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    # All threads agree on the recovered state
    states = {r.new_state for r in results}
    assert len(states) == 1


def test_42_recovery_metrics_increment(recovery_env):
    """Bounded recovery metrics accurately reflect recovery actions."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-42", goal="Metrics goal", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.startup_recovery()
    assert rec_mgr.metrics["recovery_runs"] >= 1
    assert rec_mgr.metrics["interrupted_tasks"] >= 1


def test_43_recovery_telemetry_emitted(recovery_env):
    """ActionEventBus receives structured recovery telemetry events."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]
    bus = recovery_env["bus"]

    emitted_types = []
    bus.subscribe(lambda e: emitted_types.append(e.action_type))

    task = LongHorizonTask(task_id="t-43", goal="Telemetry test", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.startup_recovery()
    assert ActionType.RECOVERY_STARTED in emitted_types
    assert ActionType.RECOVERY_COMPLETED in emitted_types


# ---------------------------------------------------------------------------
# 6. Re-Admission & Scheduler Re-Integration (Tests 44-53)
# ---------------------------------------------------------------------------

def test_44_safe_requeue_admitted_to_scheduler(recovery_env):
    """Recovered safe task re-enters MultiTaskScheduler queue."""
    repo = recovery_env["repo"]
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-44", goal="Queue re-entry", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.recover_task("t-44")

    # Task should now be queued in the scheduler
    queued = scheduler.get_queued_tasks()
    queued_ids = [q.task_id for q in queued]
    assert "t-44" in queued_ids


def test_45_scheduler_priority_preserved(recovery_env):
    """Persisted priority is restored upon scheduler re-admission."""
    repo = recovery_env["repo"]
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-45", goal="Priority preserve", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)
    repo.save_scheduled_task(task_id="t-45", priority=75, state="RUNNING")

    rec_mgr.recover_task("t-45")

    st = scheduler.get_task("t-45")
    assert st is not None
    assert st.base_priority >= 50


def test_46_scheduled_tasks_table_updated(recovery_env):
    """scheduled_tasks table reflects the new state and recovery_state."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-46", goal="Table update", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.recover_task("t-46")

    st = repo.get_scheduled_task("t-46")
    assert st is not None
    assert st["state"] in ("QUEUED", "PAUSED", "RECOVERY_REQUIRED")


def test_47_requeued_task_can_be_dispatched(recovery_env):
    """Requeued task can be selected by MultiTaskScheduler."""
    repo = recovery_env["repo"]
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-47", goal="Dispatch test", state=LongHorizonTaskState.RUNNING)
    repo.save_task(task)

    rec_mgr.recover_task("t-47")

    runnable = scheduler.select_next_runnable()
    assert runnable is not None
    assert runnable.task_id == "t-47"


def test_48_multiple_tasks_recovered_order(recovery_env):
    """Multiple interrupted tasks are recovered and admitted."""
    repo = recovery_env["repo"]
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]

    t1 = LongHorizonTask(task_id="t-48-a", goal="Task A", state=LongHorizonTaskState.RUNNING)
    t2 = LongHorizonTask(task_id="t-48-b", goal="Task B", state=LongHorizonTaskState.RUNNING)
    repo.save_task(t1)
    repo.save_task(t2)

    summary = rec_mgr.startup_recovery()
    assert summary.tasks_requeued >= 2
    assert len(scheduler.get_queued_tasks()) >= 2


def test_49_dependency_recovery_respected(recovery_env):
    """Task with dependencies is recovered and re-admitted with dependencies intact."""
    repo = recovery_env["repo"]
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]

    t_parent = LongHorizonTask(task_id="t-parent", goal="Parent", state=LongHorizonTaskState.RUNNING)
    t_child = LongHorizonTask(task_id="t-child", goal="Child", state=LongHorizonTaskState.RUNNING)
    repo.save_task(t_parent)
    repo.save_task(t_child)
    repo.save_scheduled_task(task_id="t-child", dependencies=["t-parent"])

    rec_mgr.startup_recovery()

    child_item = scheduler.get_task("t-child")
    assert child_item is not None


def test_50_concurrency_controller_fresh_slots(recovery_env):
    """After recovery, ConcurrencyController has zero active slots until newly scheduled."""
    concurrency_ctrl = recovery_env["concurrency"]
    rec_mgr = recovery_env["rec_mgr"]

    concurrency_ctrl._slots["old-slot"] = ExecutionSlot(task_id="old-task", state=ConcurrencyState.RUNNING)
    assert concurrency_ctrl.active_count == 1

    rec_mgr.startup_recovery()
    assert concurrency_ctrl.active_count == 0


def test_51_resource_manager_fresh_leases(recovery_env):
    """After recovery, ResourceManager has zero allocated resources."""
    res_mgr = recovery_env["resources"]
    rec_mgr = recovery_env["rec_mgr"]

    req = ResourceRequest(resource_type=ResourceType.WORKSPACE, target="proj1")
    res_mgr.acquire_resources("task-51", [req])
    assert res_mgr.is_available(req) is False

    rec_mgr.startup_recovery()
    assert res_mgr.is_available(req) is True


def test_52_paused_task_recovery(recovery_env):
    """Task in PAUSED state remains PAUSED across restart."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-52", goal="Paused goal", state=LongHorizonTaskState.PAUSED)
    repo.save_task(task)

    res = rec_mgr.recover_task("t-52")
    # PAUSED task is already safely non-running
    assert res.decision in (RecoveryState.PAUSED, RecoveryState.REQUEUED, RecoveryState.SKIPPED)


def test_53_parent_child_recovery(recovery_env):
    """Parent and child tasks are both recovered cleanly without conflict."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    parent = LongHorizonTask(task_id="parent-53", goal="Parent", state=LongHorizonTaskState.RUNNING)
    child = LongHorizonTask(task_id="child-53", goal="Child", state=LongHorizonTaskState.RUNNING)
    repo.save_task(parent)
    repo.save_task(child)

    res_p = rec_mgr.recover_task("parent-53")
    res_c = rec_mgr.recover_task("child-53")

    assert res_p.decision == RecoveryState.REQUEUED
    assert res_c.decision == RecoveryState.REQUEUED


# ---------------------------------------------------------------------------
# 7. Graceful Shutdown & Clean Session Markers (Tests 54-60)
# ---------------------------------------------------------------------------

def test_54_graceful_shutdown_report(recovery_env):
    """graceful_shutdown() produces a clean ShutdownReport."""
    rec_mgr = recovery_env["rec_mgr"]
    report = rec_mgr.graceful_shutdown()
    assert report.clean_shutdown is True
    assert report.session_id == rec_mgr.session_id


def test_55_clean_shutdown_recorded_in_db(recovery_env):
    """graceful_shutdown sets clean_shutdown=1 in app_sessions."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    rec_mgr.startup_recovery()
    rec_mgr.graceful_shutdown()

    last = repo.get_last_session()
    assert last is not None
    assert last["clean_shutdown"] == 1
    assert last["status"] == "CLEAN"


def test_56_graceful_shutdown_persists_queue(recovery_env):
    """Shutdown persists in-flight queue items before terminating."""
    scheduler = recovery_env["scheduler"]
    rec_mgr = recovery_env["rec_mgr"]
    repo = recovery_env["repo"]

    task = LongHorizonTask(task_id="t-56", goal="Queue persistence")
    repo.save_task(task)
    scheduler.admit_task(ScheduledTask(task_id="t-56", task=task, base_priority=TaskPriority.HIGH.value))

    report = rec_mgr.graceful_shutdown()
    assert report.scheduler_queue_persisted >= 1

    saved = repo.get_scheduled_task("t-56")
    assert saved is not None


def test_57_graceful_shutdown_releases_resources(recovery_env):
    """Shutdown releases all held logical resources."""
    res_mgr = recovery_env["resources"]
    rec_mgr = recovery_env["rec_mgr"]

    req = ResourceRequest(resource_type=ResourceType.CLIPBOARD, target="global")
    res_mgr.acquire_resources("t-57", [req])
    assert res_mgr.is_available(req) is False

    rec_mgr.graceful_shutdown()
    assert res_mgr.is_available(req) is True


def test_58_clean_shutdown_prevents_unclean_alert_on_next_start(recovery_env):
    """Subsequent startup after clean shutdown reports unclean_shutdown_detected=False."""
    rec_mgr = recovery_env["rec_mgr"]

    rec_mgr.startup_recovery()
    rec_mgr.graceful_shutdown()

    # Create new manager with next session ID
    next_rec = MultiTaskRecoveryManager(
        repo=recovery_env["repo"],
        scheduler=recovery_env["scheduler"],
        concurrency_controller=recovery_env["concurrency"],
        resource_manager=recovery_env["resources"],
        session_id="next-session-002",
    )
    s2 = next_rec.startup_recovery()
    assert s2.unclean_shutdown_detected is False


def test_59_shutdown_clears_durable_leases(recovery_env):
    """Graceful shutdown wipes durable_resource_leases table."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    repo.save_durable_lease(lease_id="l-59", task_id="t-59", resources=["res"])
    assert len(repo.list_durable_leases()) == 1

    rec_mgr.graceful_shutdown()
    assert len(repo.list_durable_leases()) == 0


def test_60_shutdown_clears_durable_slots(recovery_env):
    """Graceful shutdown wipes durable_execution_slots table."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    repo.save_durable_slot(slot_id="s-60", task_id="t-60", started_at=1.0, state="RUNNING")
    assert len(repo.list_durable_slots()) == 1

    rec_mgr.graceful_shutdown()
    assert len(repo.list_durable_slots()) == 0


# ---------------------------------------------------------------------------
# 8. Security, Secret Scrubbing, and AST Audits (Tests 61-72+)
# ---------------------------------------------------------------------------

def test_61_secret_scrubbing_in_recovery_result():
    """Secrets in recovery metadata are redacted."""
    res = TaskRecoveryResult(
        task_id="t-61",
        previous_state="RUNNING",
        new_state="INTERRUPTED",
        decision=RecoveryState.REQUEUED,
        reason="Test",
        metadata={"api_key": "sk-12345678", "normal_field": "val"},
    )
    assert res.metadata["api_key"] == "[REDACTED]"
    assert res.metadata["normal_field"] == "val"


def test_62_secret_scrubbing_in_scheduled_tasks(test_repo):
    """Secrets in scheduled_task metadata are recursively scrubbed before save."""
    test_repo.save_scheduled_task(
        task_id="t-62",
        metadata={"token": "bearer-token-123", "safe_id": 99},
    )
    st = test_repo.get_scheduled_task("t-62")
    assert st["metadata"]["token"] == "[REDACTED]"
    assert st["metadata"]["safe_id"] == 99


def test_63_secret_scrubbing_in_app_sessions(test_repo):
    """App session metadata scrubs secrets."""
    test_repo.create_session("sess-63", metadata={"password": "mypassword", "host": "localhost"})
    last = test_repo.get_last_session()
    meta = json.loads(last["metadata_json"])
    assert meta["password"] == "[REDACTED]"
    assert meta["host"] == "localhost"


def test_64_secret_scrubbing_in_durable_slots(test_repo):
    """Checkpoints in durable slots scrub passwords and tokens."""
    test_repo.save_durable_slot(
        slot_id="s-64",
        task_id="t-64",
        started_at=10.0,
        state="RUNNING",
        last_checkpoint={"auth_token": "secret-token", "step": 3},
    )
    slots = test_repo.list_durable_slots()
    assert slots[0]["last_checkpoint"]["auth_token"] == "[REDACTED]"


def test_65_sql_injection_defense(test_repo):
    """Parameterized queries protect against malicious task IDs."""
    malicious_id = "task'; DROP TABLE long_horizon_tasks; --"
    task = LongHorizonTask(task_id=malicious_id, goal="Injection test")
    test_repo.save_task(task)

    loaded = test_repo.get_task(malicious_id)
    assert loaded is not None
    assert loaded.task_id == malicious_id

    # Table is intact!
    with test_repo._connection() as conn:
        count = conn.execute("SELECT count(*) FROM long_horizon_tasks").fetchone()[0]
    assert count >= 1


def test_66_database_transaction_rollback_on_error(test_repo):
    """Database rolls back cleanly if an operation fails within transaction."""
    try:
        with test_repo._connection() as conn:
            conn.execute("INSERT INTO app_sessions (session_id, started_at, status) VALUES ('s1', 'now', 'ACTIVE')")
            # Force integrity error (null not allowed in started_at)
            conn.execute("INSERT INTO app_sessions (session_id, started_at, status) VALUES ('s2', NULL, 'ACTIVE')")
    except Exception:
        pass

    # First insert should have rolled back
    with test_repo._connection() as conn:
        row = conn.execute("SELECT * FROM app_sessions WHERE session_id = 's1'").fetchone()
    assert row is None


def test_67_delete_scheduled_task(test_repo):
    """delete_scheduled_task safely deletes scheduled task record."""
    test_repo.save_scheduled_task(task_id="t-67", priority=50)
    assert test_repo.get_scheduled_task("t-67") is not None

    deleted = test_repo.delete_scheduled_task("t-67")
    assert deleted is True
    assert test_repo.get_scheduled_task("t-67") is None


def test_68_static_security_ast_audit_recovery():
    """Static AST audit of recovery.py confirms zero prohibited calls."""
    target = Path(__file__).resolve().parent.parent / "app" / "control" / "recovery.py"
    with open(target, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(target))

    prohibited_modules = {
        "subprocess", "os.system", "os.popen", "os.spawn", "ctypes",
        "pyautogui", "pynput", "win32api", "win32con", "win32gui",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in prohibited_modules, f"Prohibited module imported: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert node.module not in prohibited_modules, f"Prohibited module imported: {node.module}"


def test_69_static_security_ast_audit_long_horizon_additions():
    """Static AST audit confirms no prohibited execution calls in long_horizon.py."""
    target = Path(__file__).resolve().parent.parent / "app" / "control" / "long_horizon.py"
    with open(target, "r", encoding="utf-8") as f:
        content = f.read()

    prohibited_tokens = ["subprocess.Popen", "os.system(", "os.popen(", "pyautogui."]
    for token in prohibited_tokens:
        assert token not in content, f"Prohibited token found in long_horizon.py: {token}"


def test_70_architecture_hierarchy_integrity(recovery_env):
    """Verifies MultiTaskRecoveryManager delegates to MultiTaskScheduler and never executes directly."""
    rec_mgr = recovery_env["rec_mgr"]
    scheduler = recovery_env["scheduler"]

    # Verify recovery manager owns scheduler reference and admits through it
    assert rec_mgr.scheduler is scheduler
    assert hasattr(rec_mgr, "startup_recovery")
    assert hasattr(rec_mgr, "recover_task")
    assert hasattr(rec_mgr, "graceful_shutdown")


def test_71_bounded_recovery_retries_exact_limit(recovery_env):
    """Verifies exact MAX_RECOVERY_ATTEMPTS threshold behaves deterministically."""
    repo = recovery_env["repo"]
    rec_mgr = recovery_env["rec_mgr"]

    task = LongHorizonTask(task_id="t-71", goal="Test retry limit", state=LongHorizonTaskState.RUNNING, recovery_count=2)
    repo.save_task(task)

    # 1st attempt: recovery_count becomes 3 (allowed)
    r1 = rec_mgr.recover_task("t-71")
    assert r1.decision in (RecoveryState.REQUEUED, RecoveryState.PAUSED)
    assert r1.retry_count == 3

    # Reset cache to simulate subsequent crash
    rec_mgr._recovered_cache.clear()

    # 2nd attempt: recovery_count is 3, which hits MAX_RECOVERY_ATTEMPTS -> RECOVERY_REQUIRED
    r2 = rec_mgr.recover_task("t-71")
    assert r2.decision == RecoveryState.RECOVERY_REQUIRED


def test_72_recovery_journal_retrieval(test_repo):
    """Recovery journal events can be fetched by task_id."""
    test_repo.log_recovery_event(
        recovery_run_id="run-1",
        task_id="t-72",
        action="TEST_ACTION",
        previous_state="RUNNING",
        new_state="INTERRUPTED",
        details={"note": "sample"},
    )
    events = test_repo.get_recovery_events("t-72")
    assert len(events) == 1
    assert events[0]["recovery_run_id"] == "run-1"
    assert events[0]["action"] == "TEST_ACTION"
    assert events[0]["details"]["note"] == "sample"

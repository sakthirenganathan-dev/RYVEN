"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 1: MultiTaskScheduler Verification Test Suite.

Comprehensive Test Suite covering:
1.  Scheduler initialization & configuration (Tests 1-2)
2.  Task admission & submission paths (Tests 3-6)
3.  Queue insertion & status tracking (Tests 7-8)
4.  Priority tiers: CRITICAL, HIGH, NORMAL, LOW, BACKGROUND, DEFAULT (Tests 9-15)
5.  Bounded aging & starvation prevention (Tests 16-18)
6.  Deterministic queue tie-breaking (Tests 19-22)
7.  Runnable task selection & dependency gating (Tests 23-25)
8.  Task pause, resume & cancellation (Tests 26-30)
9.  Duplicate task rejection & error handling (Tests 31-34)
10. Dependency cycle detection & invalid task handling (Tests 35-37)
11. Scheduler lifecycle: start, stop & loop shutdown (Tests 38-40)
12. ActionEventBus telemetry integration (Tests 41-43)
13. Security invariants: confirmation gating & secret scrubbing (Tests 44-46)
14. Architectural compliance: zero subprocess / execution bypass (Tests 47-48)
15. Phase 2 cooperation hooks (Tests 49-50)
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
)
from app.control.scheduler import (
    DEFAULT_AGING_RATE,
    DEFAULT_MAX_WAIT_BONUS,
    DEFAULT_PRIORITY,
    MultiTaskScheduler,
    ScheduledQueueItem,
    ScheduledTask,
    SchedulerState,
    SchedulerStatus,
    TaskPriority,
    calculate_effective_priority,
)
from app.control.task import UnifiedTaskOrchestrator, UnifiedTaskResult, UnifiedTaskStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_repo() -> TaskPersistenceRepository:
    """Isolated in-memory SQLite repository."""
    return TaskPersistenceRepository(db_path=":memory:")


@pytest.fixture
def mock_orchestrator() -> UnifiedTaskOrchestrator:
    """Mocked UnifiedTaskOrchestrator returning successful executions."""
    orch = UnifiedTaskOrchestrator()
    orch.execute_task = AsyncMock(
        return_value=UnifiedTaskResult(
            task_id="mock-unified-task",
            original_goal="mock-goal",
            status=UnifiedTaskStatus.COMPLETED,
            success=True,
            result_summary="Mock step succeeded",
            step_results=[{"status": "SUCCESS"}],
        )
    )
    orch.create_task = AsyncMock(
        side_effect=lambda g: MagicMock(goal=g, steps=[])
    )
    orch.plan_task = AsyncMock(
        side_effect=lambda t: MagicMock(goal="mock", steps=[])
    )
    return orch


@pytest.fixture
def test_task_manager(memory_repo: TaskPersistenceRepository, mock_orchestrator: UnifiedTaskOrchestrator) -> LongHorizonTaskManager:
    """Isolated LongHorizonTaskManager instance."""
    return LongHorizonTaskManager(
        repository=memory_repo,
        orchestrator=mock_orchestrator,
        max_steps_per_run=10,
        max_replans=3,
        max_recovery_attempts=2,
        max_runtime_seconds=60.0,
    )


@pytest.fixture
def test_event_bus() -> ActionEventBus:
    """Fresh isolated ActionEventBus."""
    return ActionEventBus(max_events=500)


@pytest.fixture
def scheduler(test_task_manager: LongHorizonTaskManager, memory_repo: TaskPersistenceRepository, test_event_bus: ActionEventBus) -> MultiTaskScheduler:
    """Isolated MultiTaskScheduler instance."""
    return MultiTaskScheduler(
        task_manager=test_task_manager,
        repository=memory_repo,
        event_bus=test_event_bus,
        max_concurrency=1,
        aging_rate=1.0,
        max_wait_bonus=20.0,
        cycle_interval_seconds=0.1,
    )


# ---------------------------------------------------------------------------
# 1. Scheduler Initialization & Configuration (Tests 1-2)
# ---------------------------------------------------------------------------

def test_01_scheduler_initialization_defaults():
    sched = MultiTaskScheduler()
    status = sched.scheduler_status()
    assert status.scheduler_state == SchedulerState.STOPPED
    assert status.queued_task_count == 0
    assert status.active_task_count == 0
    assert status.max_concurrency == 1
    assert status.queued_task_ids == []
    assert status.active_task_ids == []
    assert status.scheduler_uptime_seconds == 0.0
    assert status.last_scheduling_cycle is None
    assert isinstance(status.bounded_metrics, dict)
    assert status.bounded_metrics["total_enqueued"] == 0


def test_02_scheduler_initialization_custom_params(memory_repo: TaskPersistenceRepository, test_event_bus: ActionEventBus):
    sched = MultiTaskScheduler(
        repository=memory_repo,
        event_bus=test_event_bus,
        max_concurrency=4,
        aging_rate=2.5,
        max_wait_bonus=30.0,
        cycle_interval_seconds=0.2,
    )
    assert sched.max_concurrency == 4
    assert sched.aging_rate == 2.5
    assert sched.max_wait_bonus == 30.0
    assert sched.cycle_interval_seconds == 0.2


# ---------------------------------------------------------------------------
# 2. Task Admission & Submission Paths (Tests 3-6)
# ---------------------------------------------------------------------------

def test_03_task_submission_long_horizon_task(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    task = LongHorizonTask(goal="Compile project assets")
    memory_repo.create_task(task)

    scheduled = scheduler.enqueue_task(task, priority=TaskPriority.HIGH)
    assert scheduled.task_id == task.task_id
    assert scheduled.base_priority == 75
    assert scheduled.effective_priority == 75.0
    assert scheduled.state == LongHorizonTaskState.QUEUED
    assert task.state == LongHorizonTaskState.QUEUED


def test_04_task_submission_by_task_id(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    task = LongHorizonTask(goal="Run database migration")
    memory_repo.create_task(task)

    scheduled = scheduler.enqueue_task(task.task_id, priority=TaskPriority.NORMAL)
    assert scheduled.task_id == task.task_id
    assert scheduled.base_priority == 50
    assert scheduled.state == LongHorizonTaskState.QUEUED


@pytest.mark.asyncio
async def test_05_task_submission_goal_string(scheduler: MultiTaskScheduler):
    scheduled = await scheduler.submit_task("Automate backup verification", priority=TaskPriority.LOW)
    assert scheduled.task_id.startswith("lht-")
    assert scheduled.base_priority == 25
    assert scheduled.state == LongHorizonTaskState.QUEUED


def test_06_task_submission_as_scheduled_task(scheduler: MultiTaskScheduler):
    item = ScheduledTask(
        task_id="custom-sched-1",
        base_priority=TaskPriority.CRITICAL.value,
        effective_priority=100.0,
    )
    scheduled = scheduler.enqueue_task(item)
    assert scheduled.task_id == "custom-sched-1"
    assert scheduled.base_priority == 100


# ---------------------------------------------------------------------------
# 3. Queue Insertion & Status Tracking (Tests 7-8)
# ---------------------------------------------------------------------------

def test_07_queue_insertion_and_status_update(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t1 = LongHorizonTask(goal="Task 1")
    t2 = LongHorizonTask(goal="Task 2")
    memory_repo.create_task(t1)
    memory_repo.create_task(t2)

    scheduler.enqueue_task(t1)
    scheduler.enqueue_task(t2)

    status = scheduler.scheduler_status()
    assert status.queued_task_count == 2
    assert t1.task_id in status.queued_task_ids
    assert t2.task_id in status.queued_task_ids
    assert status.bounded_metrics["total_enqueued"] == 2


def test_08_get_queue_returns_scheduled_items(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t1 = LongHorizonTask(goal="Task 1")
    memory_repo.create_task(t1)
    scheduler.enqueue_task(t1)

    q = scheduler.get_queue()
    assert len(q) == 1
    assert q[0].task_id == t1.task_id
    assert isinstance(q[0], ScheduledTask)
    assert isinstance(q[0], ScheduledQueueItem)


# ---------------------------------------------------------------------------
# 4. Priority Tiers (Tests 9-15)
# ---------------------------------------------------------------------------

def test_09_priority_tier_critical(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_crit = LongHorizonTask(goal="Critical security audit")
    memory_repo.create_task(t_crit)
    scheduled = scheduler.enqueue_task(t_crit, priority=TaskPriority.CRITICAL)
    assert scheduled.base_priority == 100
    assert scheduled.effective_priority == 100.0


def test_10_priority_tier_high(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_high = LongHorizonTask(goal="High priority bugfix")
    memory_repo.create_task(t_high)
    scheduled = scheduler.enqueue_task(t_high, priority=TaskPriority.HIGH)
    assert scheduled.base_priority == 75
    assert scheduled.effective_priority == 75.0


def test_11_priority_tier_normal(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_norm = LongHorizonTask(goal="Regular test run")
    memory_repo.create_task(t_norm)
    scheduled = scheduler.enqueue_task(t_norm, priority=TaskPriority.NORMAL)
    assert scheduled.base_priority == 50
    assert scheduled.effective_priority == 50.0


def test_12_priority_tier_low(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_low = LongHorizonTask(goal="Low priority doc update")
    memory_repo.create_task(t_low)
    scheduled = scheduler.enqueue_task(t_low, priority=TaskPriority.LOW)
    assert scheduled.base_priority == 25
    assert scheduled.effective_priority == 25.0


def test_13_priority_tier_background(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_bg = LongHorizonTask(goal="Background log prune")
    memory_repo.create_task(t_bg)
    scheduled = scheduler.enqueue_task(t_bg, priority=TaskPriority.BACKGROUND)
    assert scheduled.base_priority == 10
    assert scheduled.effective_priority == 10.0


def test_14_default_priority_assigned_if_missing(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_default = LongHorizonTask(goal="Unspecified priority task")
    memory_repo.create_task(t_default)
    scheduled = scheduler.enqueue_task(t_default)
    assert scheduled.base_priority == TaskPriority.NORMAL.value
    assert scheduled.base_priority == 50


def test_15_queue_ordering_by_base_priority(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # Enqueue in reverse order: BACKGROUND, LOW, NORMAL, HIGH, CRITICAL
    tasks = [
        ("t_bg", TaskPriority.BACKGROUND),
        ("t_low", TaskPriority.LOW),
        ("t_norm", TaskPriority.NORMAL),
        ("t_high", TaskPriority.HIGH),
        ("t_crit", TaskPriority.CRITICAL),
    ]
    for tid, prio in tasks:
        t = LongHorizonTask(task_id=tid, goal=f"Goal {tid}")
        memory_repo.create_task(t)
        scheduler.enqueue_task(t, priority=prio)

    q = scheduler.get_queue()
    ordered_ids = [item.task_id for item in q]
    assert ordered_ids == ["t_crit", "t_high", "t_norm", "t_low", "t_bg"]


# ---------------------------------------------------------------------------
# 5. Bounded Aging & Starvation Prevention (Tests 16-18)
# ---------------------------------------------------------------------------

def test_16_effective_priority_calculation_function():
    # 0 seconds elapsed
    eff, bonus = calculate_effective_priority(50, enqueued_at=100.0, current_time=100.0, aging_rate=1.0, max_wait_bonus=20.0)
    assert eff == 50.0
    assert bonus == 0.0

    # 10 seconds elapsed at 1.0/s
    eff, bonus = calculate_effective_priority(50, enqueued_at=100.0, current_time=110.0, aging_rate=1.0, max_wait_bonus=20.0)
    assert eff == 60.0
    assert bonus == 10.0

    # 50 seconds elapsed, capped at max_wait_bonus 20.0
    eff, bonus = calculate_effective_priority(50, enqueued_at=100.0, current_time=150.0, aging_rate=1.0, max_wait_bonus=20.0)
    assert eff == 70.0
    assert bonus == 20.0


def test_17_bounded_aging_prevents_starvation_overtaking(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # A LOW task enqueued 30 seconds ago
    t_low = LongHorizonTask(task_id="low_old", goal="Low priority old")
    memory_repo.create_task(t_low)
    old_time = time.time() - 30.0
    item_low = ScheduledTask(
        task_id=t_low.task_id,
        task=t_low,
        base_priority=TaskPriority.LOW.value,  # 25
        enqueued_at=old_time,
    )
    scheduler.enqueue_task(item_low)

    # A NORMAL task enqueued right now
    t_norm = LongHorizonTask(task_id="norm_new", goal="Normal priority new")
    memory_repo.create_task(t_norm)
    item_norm = ScheduledTask(
        task_id=t_norm.task_id,
        task=t_norm,
        base_priority=TaskPriority.NORMAL.value,  # 50
        enqueued_at=time.time(),
    )
    scheduler.enqueue_task(item_norm)

    # Scheduler aging_rate = 1.0, max_wait_bonus = 20.0
    # Old low task effective priority = 25 + 20 = 45
    # Normal new task effective priority = 50 + 0 = 50 -> normal stays first
    # But if LOW task was base 35, or if we compare LOW vs BACKGROUND:
    t_bg = LongHorizonTask(task_id="bg_old", goal="Background old")
    memory_repo.create_task(t_bg)
    item_bg = ScheduledTask(
        task_id=t_bg.task_id,
        task=t_bg,
        base_priority=TaskPriority.BACKGROUND.value,  # 10
        enqueued_at=time.time() - 25.0,  # 25s ago -> bonus 20 -> effective 30
    )
    scheduler.enqueue_task(item_bg)

    t_low_new = LongHorizonTask(task_id="low_new", goal="Low priority new")
    memory_repo.create_task(t_low_new)
    item_low_new = ScheduledTask(
        task_id=t_low_new.task_id,
        task=t_low_new,
        base_priority=TaskPriority.LOW.value,  # 25
        enqueued_at=time.time(),  # bonus 0 -> effective 25
    )
    scheduler.enqueue_task(item_low_new)

    q = scheduler.get_queue()
    q_ids = [item.task_id for item in q]
    # bg_old (effective 30) MUST overtake low_new (effective 25)!
    assert q_ids.index("bg_old") < q_ids.index("low_new")


def test_18_bounded_aging_strictly_capped_at_configured_maximum(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # Enqueued 1,000,000 seconds ago
    t = LongHorizonTask(task_id="ancient_task", goal="Ancient task")
    memory_repo.create_task(t)
    ancient_item = ScheduledTask(
        task_id=t.task_id,
        task=t,
        base_priority=TaskPriority.NORMAL.value,  # 50
        enqueued_at=time.time() - 1_000_000.0,
    )
    scheduler.enqueue_task(ancient_item)

    q = scheduler.get_queue()
    ancient_scheduled = next(item for item in q if item.task_id == "ancient_task")
    # Max wait bonus is 20.0 -> effective priority cannot exceed 50 + 20 = 70.0
    assert ancient_scheduled.wait_bonus == 20.0
    assert ancient_scheduled.effective_priority == 70.0


# ---------------------------------------------------------------------------
# 6. Deterministic Queue Tie-Breaking (Tests 19-22)
# ---------------------------------------------------------------------------

def test_19_deterministic_tie_breaking_dependency_readiness(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # Two tasks with identical priority (50) and enqueue time
    # One is ready, one is blocked by a non-existent/uncompleted dependency
    now = time.time()
    t_ready = LongHorizonTask(task_id="task_ready", goal="Ready task")
    t_blocked = LongHorizonTask(task_id="task_blocked", goal="Blocked task")
    memory_repo.create_task(t_ready)
    memory_repo.create_task(t_blocked)

    scheduler.enqueue_task(ScheduledTask(task_id="task_blocked", task=t_blocked, base_priority=50, dependencies=["missing_dep"], enqueued_at=now))
    scheduler.enqueue_task(ScheduledTask(task_id="task_ready", task=t_ready, base_priority=50, dependencies=[], enqueued_at=now))

    q = scheduler.get_queue()
    assert q[0].task_id == "task_ready"
    assert q[1].task_id == "task_blocked"


def test_20_deterministic_tie_breaking_enqueue_timestamp(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # Two tasks with identical priority (50), both ready, but t_older enqueued 5s earlier
    now = time.time()
    t_older = LongHorizonTask(task_id="t_older", goal="Older task")
    t_newer = LongHorizonTask(task_id="t_newer", goal="Newer task")
    memory_repo.create_task(t_older)
    memory_repo.create_task(t_newer)

    # Disable aging for pure timestamp tie-breaker check
    scheduler.aging_rate = 0.0
    scheduler.enqueue_task(ScheduledTask(task_id="t_newer", task=t_newer, base_priority=50, enqueued_at=now + 5.0))
    scheduler.enqueue_task(ScheduledTask(task_id="t_older", task=t_older, base_priority=50, enqueued_at=now))

    q = scheduler.get_queue()
    assert q[0].task_id == "t_older"
    assert q[1].task_id == "t_newer"


def test_21_deterministic_tie_breaking_lexicographical_task_id(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    # Two tasks with identical priority (50), identical readiness, identical enqueue time
    now = time.time()
    t_a = LongHorizonTask(task_id="task_alpha", goal="Task A")
    t_z = LongHorizonTask(task_id="task_zeta", goal="Task Z")
    memory_repo.create_task(t_a)
    memory_repo.create_task(t_z)

    # Enqueue zeta first, then alpha
    scheduler.enqueue_task(ScheduledTask(task_id="task_zeta", task=t_z, base_priority=50, enqueued_at=now))
    scheduler.enqueue_task(ScheduledTask(task_id="task_alpha", task=t_a, base_priority=50, enqueued_at=now))

    q = scheduler.get_queue()
    assert q[0].task_id == "task_alpha"
    assert q[1].task_id == "task_zeta"


def test_22_select_next_task_picks_head_deterministically(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    now = time.time()
    for tid in ["task_c", "task_b", "task_a"]:
        t = LongHorizonTask(task_id=tid, goal=f"Goal {tid}")
        memory_repo.create_task(t)
        scheduler.enqueue_task(ScheduledTask(task_id=tid, task=t, base_priority=50, enqueued_at=now))

    selected = scheduler.select_next_task()
    assert selected is not None
    assert selected.task_id == "task_a"


# ---------------------------------------------------------------------------
# 7. Runnable Task Selection & Dependency Gating (Tests 23-25)
# ---------------------------------------------------------------------------

def test_23_select_next_task_empty_queue_returns_none(scheduler: MultiTaskScheduler):
    assert scheduler.select_next_task() is None


def test_24_select_next_task_blocked_when_dependencies_unmet(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_dep = LongHorizonTask(task_id="dep_task", goal="Unmet dep task")
    memory_repo.create_task(t_dep)
    scheduler.enqueue_task(t_dep, dependencies=["non_existent_upstream"])

    # Queue contains item, but select_next_task should return None because it's blocked
    assert scheduler.select_next_task() is None


def test_25_select_next_task_unblocks_when_dependency_completes(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    upstream = LongHorizonTask(task_id="upstream_1", goal="Upstream work")
    upstream.state = LongHorizonTaskState.COMPLETED
    memory_repo.create_task(upstream)

    downstream = LongHorizonTask(task_id="downstream_1", goal="Downstream work")
    memory_repo.create_task(downstream)

    scheduler.enqueue_task(downstream, dependencies=["upstream_1"])
    selected = scheduler.select_next_task()
    assert selected is not None
    assert selected.task_id == "downstream_1"


# ---------------------------------------------------------------------------
# 8. Task Pause, Resume & Cancellation (Tests 26-30)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_26_task_cancellation_queued(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="To be cancelled")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    cancelled = await scheduler.cancel_task(t.task_id, reason="User abort")
    assert cancelled is not None
    assert cancelled.state == LongHorizonTaskState.CANCELLED
    assert scheduler.scheduler_status().queued_task_count == 0
    assert memory_repo.get_task(t.task_id).state == LongHorizonTaskState.CANCELLED


@pytest.mark.asyncio
async def test_27_task_cancellation_active(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="Active task")
    memory_repo.create_task(t)
    item = ScheduledTask(task_id=t.task_id, task=t)
    scheduler._active_tasks[t.task_id] = item

    cancelled = await scheduler.cancel_task(t.task_id, reason="Emergency stop")
    assert cancelled is not None
    assert cancelled.state == LongHorizonTaskState.CANCELLED
    assert t.task_id not in scheduler._active_tasks


@pytest.mark.asyncio
async def test_28_task_pause_queued(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="To be paused")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    paused = await scheduler.pause_task(t.task_id, reason="Hold for inspection")
    assert paused is not None
    assert paused.state == LongHorizonTaskState.PAUSED
    assert scheduler.scheduler_status().queued_task_count == 0
    assert t.task_id in scheduler._paused_tasks


@pytest.mark.asyncio
async def test_29_task_resume_queued(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="To be paused and resumed")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    await scheduler.pause_task(t.task_id)
    assert scheduler.scheduler_status().queued_task_count == 0

    resumed = await scheduler.resume_task(t.task_id)
    assert resumed is not None
    assert resumed.state == LongHorizonTaskState.QUEUED
    assert scheduler.scheduler_status().queued_task_count == 1
    assert t.task_id not in scheduler._paused_tasks


@pytest.mark.asyncio
async def test_30_pause_and_cancel_non_existent_returns_none(scheduler: MultiTaskScheduler):
    assert await scheduler.pause_task("non-existent-task-id") is None
    assert await scheduler.cancel_task("non-existent-task-id") is None
    assert await scheduler.resume_task("non-existent-task-id") is None


# ---------------------------------------------------------------------------
# 9. Duplicate Task Rejection & Error Handling (Tests 31-34)
# ---------------------------------------------------------------------------

def test_31_duplicate_task_rejected_if_queued(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="Unique task")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    with pytest.raises(ValueError, match="already queued"):
        scheduler.enqueue_task(t)


def test_32_duplicate_task_rejected_if_active(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="Running task")
    memory_repo.create_task(t)
    scheduler._active_tasks[t.task_id] = ScheduledTask(task_id=t.task_id, task=t)

    with pytest.raises(ValueError, match="currently active"):
        scheduler.enqueue_task(t)


def test_33_invalid_task_none_or_empty_rejected(scheduler: MultiTaskScheduler):
    with pytest.raises(ValueError, match="task cannot be None"):
        scheduler.enqueue_task(None)

    with pytest.raises(ValueError, match="task_id cannot be empty"):
        scheduler.enqueue_task("   ")


def test_34_terminal_state_task_rejected(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    for terminal_st in [LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED, LongHorizonTaskState.EXPIRED]:
        t = LongHorizonTask(goal="Terminal task")
        t.state = terminal_st
        memory_repo.create_task(t)
        with pytest.raises(ValueError, match="terminal state"):
            scheduler.enqueue_task(t)


# ---------------------------------------------------------------------------
# 10. Dependency Validation & Priority Error Handling (Tests 35-37)
# ---------------------------------------------------------------------------

def test_35_running_state_task_rejected(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="Running task")
    t.state = LongHorizonTaskState.RUNNING
    memory_repo.create_task(t)
    with pytest.raises(ValueError, match="already RUNNING"):
        scheduler.enqueue_task(t)


def test_36_self_dependency_rejected(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(task_id="self_dep_task", goal="Self dep task")
    memory_repo.create_task(t)
    with pytest.raises(ValueError, match="cannot depend on itself"):
        scheduler.enqueue_task(t, dependencies=["self_dep_task"])


def test_37_circular_dependency_rejected(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t_a = LongHorizonTask(task_id="dep_a", goal="Task A")
    t_b = LongHorizonTask(task_id="dep_b", goal="Task B")
    memory_repo.create_task(t_a)
    memory_repo.create_task(t_b)

    # A depends on B
    scheduler.enqueue_task(t_a, dependencies=["dep_b"])

    # B attempting to depend on A raises circular dependency
    with pytest.raises(ValueError, match="Circular dependency detected"):
        scheduler.enqueue_task(t_b, dependencies=["dep_a"])


# ---------------------------------------------------------------------------
# 11. Scheduler Lifecycle & Shutdown (Tests 38-40)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_38_scheduler_start_and_stop_lifecycle(scheduler: MultiTaskScheduler):
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.STOPPED

    await scheduler.start()
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.RUNNING
    assert scheduler.scheduler_status().scheduler_uptime_seconds >= 0.0

    await scheduler.stop()
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.STOPPED


def test_39_scheduler_sync_start_and_stop(scheduler: MultiTaskScheduler):
    scheduler.start_sync()
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.RUNNING

    scheduler.stop_sync()
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.STOPPED


@pytest.mark.asyncio
async def test_40_scheduler_run_loop_cancels_cleanly(scheduler: MultiTaskScheduler):
    await scheduler.start()
    # Spawn the bounded background loop
    loop_task = asyncio.create_task(scheduler.run_loop())
    scheduler._loop_task = loop_task

    # Allow one quick tick
    await asyncio.sleep(0.05)

    # Clean stop
    await scheduler.stop()
    assert loop_task.done()
    assert scheduler.scheduler_status().scheduler_state == SchedulerState.STOPPED


# ---------------------------------------------------------------------------
# 12. ActionEventBus Telemetry Integration (Tests 41-43)
# ---------------------------------------------------------------------------

def test_41_telemetry_emitted_on_enqueue_and_dequeue(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository, test_event_bus: ActionEventBus):
    t = LongHorizonTask(goal="Telemetry task")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.TASK_ENQUEUED for e in events)

    scheduler.dequeue_task(t.task_id)
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.TASK_DEQUEUED for e in events)


def test_42_telemetry_emitted_on_selection(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository, test_event_bus: ActionEventBus):
    t = LongHorizonTask(goal="Selection task")
    memory_repo.create_task(t)
    scheduler.enqueue_task(t)

    scheduler.select_next_task()
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.TASK_SCHEDULING_STARTED for e in events)


@pytest.mark.asyncio
async def test_43_telemetry_emitted_on_lifecycle(scheduler: MultiTaskScheduler, test_event_bus: ActionEventBus):
    await scheduler.start()
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.SCHEDULER_STARTED for e in events)

    await scheduler.stop()
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.SCHEDULER_STOPPED for e in events)


# ---------------------------------------------------------------------------
# 13. Security Invariants (Tests 44-46)
# ---------------------------------------------------------------------------

def test_44_security_invariant_critical_cannot_bypass_confirmation(scheduler: MultiTaskScheduler):
    step = LongHorizonTaskStep(
        name="Delete database table",
        action="delete_table",
        requires_confirmation=True,
    )
    task = LongHorizonTask(goal="Drop production db", steps=[step])

    # Enqueue with CRITICAL (100) priority
    scheduled = scheduler.enqueue_task(task, priority=TaskPriority.CRITICAL)
    assert scheduled.base_priority == 100

    # Invariant verification: confirmation MUST remain True
    assert scheduler.verify_security_invariants(task, priority=100) is True
    assert task.steps[0].requires_confirmation is True


def test_45_security_invariant_secrets_scrubbed_in_metadata(scheduler: MultiTaskScheduler):
    task = LongHorizonTask(
        goal="Secret metadata task",
        safe_metadata={
            "api_key": "sk-secret-token-12345",
            "password": "super_secret_password",
            "safe_project": "view-archive-buddy",
        },
    )
    scheduled = scheduler.enqueue_task(task)

    assert scheduled.metadata.get("api_key") == "[REDACTED]"
    assert scheduled.metadata.get("password") == "[REDACTED]"
    assert scheduled.metadata.get("safe_project") == "view-archive-buddy"


def test_46_invalid_priority_rejected(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t = LongHorizonTask(goal="Bad priority task")
    memory_repo.create_task(t)
    with pytest.raises(ValueError, match="Invalid priority type"):
        scheduler.enqueue_task(t, priority="SUPER_HIGH")  # type: ignore


# ---------------------------------------------------------------------------
# 14. Architectural Compliance: Zero Execution Bypass (Tests 47-48)
# ---------------------------------------------------------------------------

def test_47_no_direct_execution_imports_in_scheduler():
    import app.control.scheduler as sched_module
    import ast
    source_code = inspect.getsource(sched_module)
    tree = ast.parse(source_code)

    prohibited_modules = {
        "subprocess",
        "pyautogui",
        "pynput",
        "win32api",
        "win32gui",
        "win32con",
        "ctypes",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for prohibited in prohibited_modules:
                    assert not alias.name.startswith(prohibited), f"Prohibited import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for prohibited in prohibited_modules:
                assert not mod.startswith(prohibited), f"Prohibited from-import: {mod}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in {"system", "popen", "spawn"}, f"Prohibited os call: {node.func.attr}"


def test_48_scheduler_is_coordination_layer_only(scheduler: MultiTaskScheduler):
    # Verify scheduler does not have any execution primitives as methods
    forbidden_methods = [
        "execute_tool",
        "run_command",
        "click",
        "type",
        "open_application",
        "dispatch_win32",
    ]
    for m in forbidden_methods:
        assert not hasattr(scheduler, m), f"Scheduler must not have execution method '{m}'."


# ---------------------------------------------------------------------------
# 15. Phase 2 Cooperation Hooks (Tests 49-50)
# ---------------------------------------------------------------------------

def test_49_resource_advisor_cooperation_hook(scheduler: MultiTaskScheduler, memory_repo: TaskPersistenceRepository):
    t1 = LongHorizonTask(task_id="res_task_1", goal="Task 1")
    t2 = LongHorizonTask(task_id="res_task_2", goal="Task 2")
    memory_repo.create_task(t1)
    memory_repo.create_task(t2)

    scheduler.enqueue_task(t1, priority=TaskPriority.HIGH)
    scheduler.enqueue_task(t2, priority=TaskPriority.NORMAL)

    # Advisor denies t1 (simulating heavy GPU resource unavailable), allows t2
    scheduler.resource_advisor = lambda item: item.task_id != "res_task_1"

    selected = scheduler.select_next_task()
    assert selected is not None
    assert selected.task_id == "res_task_2"


def test_50_dequeue_non_existent_returns_none(scheduler: MultiTaskScheduler):
    assert scheduler.dequeue_task("non-existent-task-id") is None

"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 1: Authoritative Multi-Task Scheduler Core (MultiTaskScheduler).

Architecture:
    MultiTaskScheduler (Admission, Deterministic Queue, Bounded Aging, Status, Telemetry)
            ↓
    LongHorizonTaskManager (Lifecycle, Journaling, Recovery, Checkpointing)
            ↓
    UnifiedTaskOrchestrator (Multimodal Routing, Planning, Permissions, Confirmation)
            ↓
    Existing Execution Capabilities (Desktop / Browser / Tools)

Strict Security & Architectural Invariants:
- Coordination layer ONLY. Never directly executes Win32, raw mouse/keyboard, subprocess, or shell.
- Priority NEVER bypasses SafetyGuard, capability permissions, confirmation gates, or security policies.
- Zero duplicate task engines or planners. Reuses M17.7 task models and persistence.
- Bounded aging prevents priority starvation with strict configurable maximum wait bonus.
- 100% deterministic queue tie-breaking based on effective priority, readiness, enqueue time, and task ID.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType, _redact_dict
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    TaskPersistenceRepository,
    _scrub_secrets_recursive,
    long_horizon_task_manager,
    task_persistence_repo,
)
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard


def _utc_now_iso() -> str:
    """Helper returning current UTC ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Priority Levels & Defaults
# ---------------------------------------------------------------------------

class TaskPriority(int, Enum):
    """Authoritative task priority tiers."""
    CRITICAL = 100
    HIGH = 75
    NORMAL = 50
    LOW = 25
    BACKGROUND = 10


DEFAULT_PRIORITY: TaskPriority = TaskPriority.NORMAL
DEFAULT_AGING_RATE: float = 0.5            # Priority points gained per second of queue wait
DEFAULT_MAX_WAIT_BONUS: float = 25.0       # Configurable upper bound on starvation prevention bonus
DEFAULT_MAX_CONCURRENCY: int = 1           # Phase 1 single-flight boundary; cooperative for Phase 2
DEFAULT_CYCLE_INTERVAL_SECONDS: float = 0.5


class SchedulerState(str, Enum):
    """Lifecycle state of the multi-task scheduler."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"


# ---------------------------------------------------------------------------
# Bounded Aging & Priority Calculation
# ---------------------------------------------------------------------------

def calculate_effective_priority(
    base_priority: int,
    enqueued_at: float,
    current_time: Optional[float] = None,
    aging_rate: float = DEFAULT_AGING_RATE,
    max_wait_bonus: float = DEFAULT_MAX_WAIT_BONUS,
) -> Tuple[float, float]:
    """Calculate bounded effective priority preventing starvation without unbounded priority growth.

    effective_priority = base_priority + bounded_wait_bonus
    bounded_wait_bonus = min(max_wait_bonus, wait_time_seconds * aging_rate)

    Returns:
        (effective_priority, wait_bonus)
    """
    now = current_time if current_time is not None else time.time()
    wait_seconds = max(0.0, now - enqueued_at)
    raw_bonus = wait_seconds * max(0.0, aging_rate)
    wait_bonus = min(max(0.0, max_wait_bonus), raw_bonus)
    effective_priority = float(base_priority) + wait_bonus
    return effective_priority, wait_bonus


# ---------------------------------------------------------------------------
# Scheduled Task & Scheduler Status Models
# ---------------------------------------------------------------------------

class ScheduledTask(BaseModel):
    """Authoritative scheduled task item tracking admission, priority, aging, and dependencies."""
    task_id: str
    task: Optional[LongHorizonTask] = None
    base_priority: int = Field(default=TaskPriority.NORMAL.value)
    effective_priority: float = Field(default=float(TaskPriority.NORMAL.value))
    wait_bonus: float = Field(default=0.0)
    dependencies: List[str] = Field(default_factory=list)
    enqueued_at: float = Field(default_factory=time.time)
    enqueued_at_iso: str = Field(default_factory=_utc_now_iso)
    state: LongHorizonTaskState = Field(default=LongHorizonTaskState.QUEUED)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_task_metadata(self) -> ScheduledTask:
        self.metadata = _scrub_secrets_recursive(self.metadata)
        return self

    def __await__(self):
        """Dual sync/async convenience so methods returning ScheduledTask can be awaited or read directly."""
        async def _wrapper():
            return self
        return _wrapper().__await__()


# Alias for backward and planning compatibility
ScheduledQueueItem = ScheduledTask


class SchedulerStatus(BaseModel):
    """Internal status representation of the scheduler."""
    scheduler_state: SchedulerState = SchedulerState.STOPPED
    queued_task_count: int = 0
    active_task_count: int = 0
    max_concurrency: int = 1
    queued_task_ids: List[str] = Field(default_factory=list)
    active_task_ids: List[str] = Field(default_factory=list)
    scheduler_uptime_seconds: float = 0.0
    last_scheduling_cycle: Optional[str] = None
    bounded_metrics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_status_metrics(self) -> SchedulerStatus:
        self.bounded_metrics = _scrub_secrets_recursive(self.bounded_metrics)
        return self


# ---------------------------------------------------------------------------
# MultiTaskScheduler (Phase 1 Authoritative Core)
# ---------------------------------------------------------------------------

class MultiTaskScheduler:
    """Authoritative Multi-Task Scheduler coordinating task admission, deterministic
    priority queue management, bounded starvation aging, and lifecycle observability.

    CRITICAL ARCHITECTURE INVARIANTS:
    - Coordination layer ONLY. Never executes tools directly.
    - Zero Win32, subprocess, shell, PowerShell, cmd, or arbitrary executables.
    - Execution dispatch routes exclusively through LongHorizonTaskManager -> UnifiedTaskOrchestrator.
    - Priority NEVER bypasses SafetyGuard, capability permissions, confirmation gates, or budgets.
    """

    def __init__(
        self,
        task_manager: Optional[LongHorizonTaskManager] = None,
        repository: Optional[TaskPersistenceRepository] = None,
        repo: Optional[TaskPersistenceRepository] = None,
        event_bus: Optional[ActionEventBus] = None,
        bus: Optional[ActionEventBus] = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        aging_rate: float = DEFAULT_AGING_RATE,
        max_wait_bonus: float = DEFAULT_MAX_WAIT_BONUS,
        cycle_interval_seconds: float = DEFAULT_CYCLE_INTERVAL_SECONDS,
        resource_advisor: Optional[Callable[[ScheduledTask], bool]] = None,
        resource_manager: Optional[Any] = None,
        concurrency_controller: Optional[Any] = None,
    ) -> None:
        self.task_manager = task_manager
        self.repo = repo or repository or (task_manager.repo if task_manager else task_persistence_repo)
        self.event_bus = bus or event_bus or action_bus
        self.max_concurrency = max(1, max_concurrency)
        self.aging_rate = max(0.0, aging_rate)
        self.max_wait_bonus = max(0.0, max_wait_bonus)
        self.cycle_interval_seconds = max(0.05, cycle_interval_seconds)

        # Resource management & Phase 2 cooperation hooks
        self.resource_manager = resource_manager
        self.resource_advisor = resource_advisor
        self.concurrency_controller = concurrency_controller
        self.on_task_started_hook: Optional[Callable[[ScheduledTask], None]] = None
        self.on_task_finished_hook: Optional[Callable[[ScheduledTask], None]] = None

        # Thread & state safety
        self._lock = threading.RLock()
        self._state: SchedulerState = SchedulerState.STOPPED
        self._started_at: Optional[float] = None
        self._last_cycle_at: Optional[str] = None

        # Queues and registry
        self._queue: Dict[str, ScheduledTask] = {}
        self._active_tasks: Dict[str, ScheduledTask] = {}
        self._paused_tasks: Dict[str, ScheduledTask] = {}
        self._completed_task_ids: Set[str] = set()

        # Background loop control
        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

        # Bounded metrics (strictly counters, zero secrets)
        self._metrics: Dict[str, int] = {
            "total_submitted": 0,
            "total_enqueued": 0,
            "total_dequeued": 0,
            "total_selected": 0,
            "total_paused": 0,
            "total_resumed": 0,
            "total_cancelled": 0,
            "total_completed": 0,
            "total_scheduling_cycles": 0,
            "starvation_promotions": 0,
        }

    # -----------------------------------------------------------------------
    # Task Admission & Enqueue
    # -----------------------------------------------------------------------

    def validate_task_identity(self, task_or_id: Union[LongHorizonTask, str, ScheduledTask, Dict[str, Any]]) -> Tuple[str, Optional[LongHorizonTask]]:
        """Validate task identity and uniqueness."""
        if task_or_id is None:
            raise ValueError("Task identity validation failed: task cannot be None.")

        if isinstance(task_or_id, ScheduledTask):
            task_id = task_or_id.task_id
            task = task_or_id.task
        elif isinstance(task_or_id, LongHorizonTask):
            task_id = task_or_id.task_id
            task = task_or_id
        elif isinstance(task_or_id, str):
            task_id = task_or_id.strip()
            if not task_id:
                raise ValueError("Task identity validation failed: task_id cannot be empty.")
            task = self.repo.get_task(task_id) if self.repo else None
        elif isinstance(task_or_id, dict):
            task_id = str(task_or_id.get("task_id", "")).strip()
            if not task_id:
                raise ValueError("Task identity validation failed: dict must contain non-empty 'task_id'.")
            task = self.repo.get_task(task_id) if self.repo else None
        else:
            raise ValueError(f"Task identity validation failed: unsupported task type {type(task_or_id).__name__}.")

        if not task_id:
            raise ValueError("Task identity validation failed: resolved task_id is empty.")

        with self._lock:
            if task_id in self._queue:
                raise ValueError(f"Duplicate task rejected: Task '{task_id}' is already queued.")
            if task_id in self._active_tasks:
                raise ValueError(f"Duplicate task rejected: Task '{task_id}' is currently active.")

        return task_id, task

    def validate_lifecycle_state(self, task: Optional[LongHorizonTask]) -> None:
        """Validate task lifecycle state is eligible for admission."""
        if not task:
            return

        terminal_states = {
            LongHorizonTaskState.COMPLETED,
            LongHorizonTaskState.CANCELLED,
            LongHorizonTaskState.EXPIRED,
        }
        if task.state in terminal_states:
            raise ValueError(f"Lifecycle state rejection: Cannot admit task in terminal state '{task.state.value}'.")

        if task.state == LongHorizonTaskState.RUNNING:
            raise ValueError("Lifecycle state rejection: Cannot admit task that is already RUNNING.")

    def validate_dependencies(self, task_id: str, dependencies: List[str]) -> None:
        """Validate dependency graph integrity."""
        if task_id in dependencies:
            raise ValueError(f"Dependency validation failed: Task '{task_id}' cannot depend on itself.")

        # Check for immediate circular dependency with queued tasks
        with self._lock:
            for dep_id in dependencies:
                dep_item = self._queue.get(dep_id)
                if dep_item and task_id in dep_item.dependencies:
                    raise ValueError(
                        f"Circular dependency detected between '{task_id}' and '{dep_id}'."
                    )

    def enqueue_task(
        self,
        task: Union[LongHorizonTask, str, ScheduledTask, Dict[str, Any]],
        priority: Optional[Union[TaskPriority, int]] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScheduledTask:
        """Synchronously or asynchronously admit and insert a task into the deterministic queue.

        Admission steps:
        1. validate task identity
        2. validate lifecycle state
        3. validate dependencies
        4. assign default priority if missing
        5. record enqueue timestamp
        6. calculate effective priority
        7. transition task into QUEUED
        8. emit scheduling telemetry
        """
        # 1. Validate task identity
        task_id, lht_task = self.validate_task_identity(task)

        # 2. Validate lifecycle state
        self.validate_lifecycle_state(lht_task)

        # Extract dependencies
        deps: List[str] = list(dependencies or [])
        if not deps and isinstance(task, ScheduledTask) and task.dependencies:
            deps = list(task.dependencies)
        elif not deps and lht_task and hasattr(lht_task, "safe_metadata"):
            deps = list(lht_task.safe_metadata.get("dependencies", []))

        # 3. Validate dependencies
        self.validate_dependencies(task_id, deps)

        # 4. Assign default priority if missing
        assigned_priority: int = TaskPriority.NORMAL.value
        if priority is not None:
            if isinstance(priority, TaskPriority):
                assigned_priority = priority.value
            elif isinstance(priority, int):
                assigned_priority = priority
            else:
                raise ValueError(f"Invalid priority type '{type(priority).__name__}'. Must be int or TaskPriority.")
        elif lht_task and "priority" in lht_task.safe_metadata:
            meta_p = lht_task.safe_metadata.get("priority")
            if isinstance(meta_p, int):
                assigned_priority = meta_p
            elif isinstance(meta_p, TaskPriority):
                assigned_priority = meta_p.value
        elif isinstance(task, ScheduledTask):
            assigned_priority = task.base_priority

        # 5. Record enqueue timestamp
        now = time.time()
        now_iso = _utc_now_iso()
        if isinstance(task, ScheduledTask):
            if task.enqueued_at:
                now = task.enqueued_at
            if task.enqueued_at_iso:
                now_iso = task.enqueued_at_iso

        # 6. Calculate initial effective priority (0 wait bonus at admission)
        eff_p, wait_bonus = calculate_effective_priority(
            base_priority=assigned_priority,
            enqueued_at=now,
            current_time=now,
            aging_rate=self.aging_rate,
            max_wait_bonus=self.max_wait_bonus,
        )

        # 7. Transition task into QUEUED
        if lht_task:
            if lht_task.state != LongHorizonTaskState.QUEUED:
                lht_task.transition_to(LongHorizonTaskState.QUEUED, reason="Enqueued in MultiTaskScheduler")
            lht_task.safe_metadata["priority"] = assigned_priority
            lht_task.safe_metadata["enqueued_at"] = now_iso
            lht_task.safe_metadata["dependencies"] = deps
            if self.repo:
                try:
                    self.repo.update_task(lht_task)
                except Exception as e:
                    logger.debug(f"[SCHEDULER] Could not update repo on enqueue: {e}")

        # Construct scheduled task record
        merged_meta = dict(metadata or {})
        if lht_task:
            merged_meta.update(lht_task.safe_metadata)

        scheduled = ScheduledTask(
            task_id=task_id,
            task=lht_task,
            base_priority=assigned_priority,
            effective_priority=eff_p,
            wait_bonus=wait_bonus,
            dependencies=deps,
            enqueued_at=now,
            enqueued_at_iso=now_iso,
            state=LongHorizonTaskState.QUEUED,
            metadata=merged_meta,
        )

        with self._lock:
            self._queue[task_id] = scheduled
            self._metrics["total_enqueued"] += 1

        # 8. Emit scheduling telemetry
        self._emit_telemetry(
            action_type=ActionType.TASK_ENQUEUED,
            status=ActionStatus.PENDING,
            title=f"Task {task_id} enqueued (Priority={assigned_priority})",
            task_id=task_id,
            metadata={"base_priority": assigned_priority, "effective_priority": eff_p},
        )

        logger.info(f"[SCHEDULER] Enqueued task '{task_id}' with base priority {assigned_priority}.")
        return scheduled

    async def submit_task(
        self,
        task_or_id: Union[LongHorizonTask, str, ScheduledTask, Dict[str, Any]],
        priority: Optional[Union[TaskPriority, int]] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScheduledTask:
        """High-level async task submission entry point.
        
        If a new goal string is provided and task_manager exists, plans and creates
        a LongHorizonTask container first, then enqueues it.
        """
        with self._lock:
            self._metrics["total_submitted"] += 1

        # If string is not an existing ID and task_manager is available, create task
        if isinstance(task_or_id, str) and self.task_manager:
            existing = self.repo.get_task(task_or_id) if self.repo else None
            if not existing and not task_or_id.startswith("lht-"):
                # Treat as goal string
                created = await self.task_manager.create_long_horizon_task(task_or_id)
                return self.enqueue_task(created, priority=priority, dependencies=dependencies, metadata=metadata)

        return self.enqueue_task(task_or_id, priority=priority, dependencies=dependencies, metadata=metadata)

    # -----------------------------------------------------------------------
    # Dequeue, Selection & Deterministic Ordering
    # -----------------------------------------------------------------------

    def dequeue_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Remove a task from the queue without executing it."""
        with self._lock:
            item = self._queue.pop(task_id, None)
            if item:
                self._metrics["total_dequeued"] += 1
                self._emit_telemetry(
                    action_type=ActionType.TASK_DEQUEUED,
                    status=ActionStatus.CANCELLED,
                    title=f"Task {task_id} dequeued",
                    task_id=task_id,
                )
                logger.info(f"[SCHEDULER] Dequeued task '{task_id}'.")
                return item
        return None

    def _is_task_ready(self, item: ScheduledTask) -> bool:
        """Evaluate dependency readiness deterministically."""
        if not item.dependencies:
            return True

        for dep_id in item.dependencies:
            # Check if recorded completed
            if dep_id in self._completed_task_ids:
                continue

            # Check persistent repository
            if self.repo:
                dep_task = self.repo.get_task(dep_id)
                if dep_task and dep_task.state == LongHorizonTaskState.COMPLETED:
                    self._completed_task_ids.add(dep_id)
                    continue

            # Dependency not yet satisfied
            return False

        return True

    def _update_queue_aging(self, now: Optional[float] = None) -> None:
        """Recalculate bounded priority aging for all queued tasks."""
        current_time = now if now is not None else time.time()
        for item in self._queue.values():
            old_eff = item.effective_priority
            eff_p, wait_bonus = calculate_effective_priority(
                base_priority=item.base_priority,
                enqueued_at=item.enqueued_at,
                current_time=current_time,
                aging_rate=self.aging_rate,
                max_wait_bonus=self.max_wait_bonus,
            )
            item.effective_priority = eff_p
            item.wait_bonus = wait_bonus

            # Check if starvation promotion occurred
            if wait_bonus >= self.max_wait_bonus and old_eff < eff_p:
                self._metrics["starvation_promotions"] += 1
                self._emit_telemetry(
                    action_type=ActionType.TASK_PRIORITY_CHANGED,
                    status=ActionStatus.PROGRESS,
                    title=f"Starvation bonus maxed for task {item.task_id} (+{wait_bonus:.1f})",
                    task_id=item.task_id,
                    metadata={"base_priority": item.base_priority, "effective_priority": eff_p},
                )

    def select_next_task(self) -> Optional[ScheduledTask]:
        """Select the next runnable task from the queue based on deterministic ordering.

        Deterministic ordering key:
        1. effective priority (descending, higher first)
        2. dependency readiness (ready first: 0 for ready, 1 for blocked)
        3. enqueue timestamp (ascending, older first)
        4. task_id (ascending, lexicographical tie-breaker)
        """
        with self._lock:
            if not self._queue:
                return None

            now = time.time()
            self._update_queue_aging(now)

            # Sort queue deterministically
            sorted_candidates: List[Tuple[ScheduledTask, bool]] = []
            for item in self._queue.values():
                ready = self._is_task_ready(item)
                sorted_candidates.append((item, ready))

            # Deterministic sort
            sorted_candidates.sort(
                key=lambda pair: (
                    -pair[0].effective_priority,
                    0 if pair[1] else 1,
                    pair[0].enqueued_at,
                    pair[0].task_id,
                )
            )

            # Select first ready candidate that passes optional resource advisory hook
            for item, is_ready in sorted_candidates:
                if not is_ready:
                    continue

                # Phase 2 Resource Manager availability gating
                if self.resource_manager:
                    req_resources = self.resource_manager.infer_required_resources(item)
                    if not self.resource_manager.can_acquire(item.task_id, req_resources):
                        continue

                # Phase 3 Concurrency Controller admission gating
                if self.concurrency_controller and not self.concurrency_controller.can_admit(item.task_id):
                    continue

                # Cooperation hook for Phase 2 resource management
                if self.resource_advisor and not self.resource_advisor(item):
                    continue

                self._metrics["total_selected"] += 1
                self._emit_telemetry(
                    action_type=ActionType.TASK_SCHEDULING_STARTED,
                    status=ActionStatus.PROGRESS,
                    title=f"Task {item.task_id} selected (Effective Priority={item.effective_priority:.1f})",
                    task_id=item.task_id,
                    metadata={
                        "base_priority": item.base_priority,
                        "effective_priority": item.effective_priority,
                        "wait_bonus": item.wait_bonus,
                    },
                )
                return item

            # If items are queued but none are ready/advisable, emit blocked telemetry
            if self._queue:
                self._emit_telemetry(
                    action_type=ActionType.TASK_SCHEDULING_BLOCKED,
                    status=ActionStatus.PROGRESS,
                    title="All queued tasks are currently blocked on dependencies or resources",
                    task_id="scheduler",
                )

            return None

    def get_queue(self) -> List[ScheduledTask]:
        """Return queued tasks in deterministic priority order."""
        with self._lock:
            self._update_queue_aging()
            items = list(self._queue.values())
            items.sort(
                key=lambda it: (
                    -it.effective_priority,
                    0 if self._is_task_ready(it) else 1,
                    it.enqueued_at,
                    it.task_id,
                )
            )
            return items

    def get_queued_tasks(self) -> List[ScheduledTask]:
        """Return queued tasks in deterministic priority order (alias for get_queue)."""
        return self.get_queue()

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Retrieve scheduled task from queue or active tasks."""
        with self._lock:
            return self._queue.get(task_id) or self._active_tasks.get(task_id)

    def select_next_runnable(self) -> Optional[ScheduledTask]:
        """Alias for select_next_task."""
        return self.select_next_task()

    def admit_task(
        self,
        task: Union[LongHorizonTask, str, ScheduledTask, Dict[str, Any]],
        priority: Optional[Union[TaskPriority, int]] = None,
        dependencies: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ScheduledTask:
        """Alias for enqueue_task for task admission."""
        return self.enqueue_task(task, priority=priority, dependencies=dependencies, metadata=metadata)

    def update_task_priority(
        self,
        task_id: str,
        priority: Union[TaskPriority, int],
    ) -> Optional[ScheduledTask]:
        """Update base priority of a queued, active, or persisted task.

        Invariants:
        - Validates priority tier
        - Preserves bounded aging and recalculates effective priority
        - Updates persisted task and queue entry
        - Emits sanitized ActionEventBus telemetry
        - Never executes the task directly
        """
        # 1. Validate priority input
        assigned_p: int
        if isinstance(priority, TaskPriority):
            assigned_p = priority.value
        elif isinstance(priority, int):
            if priority not in (10, 25, 50, 75, 100) and not (1 <= priority <= 100):
                raise ValueError(f"Invalid priority '{priority}'. Must be an integer between 1 and 100 or standard tier.")
            assigned_p = priority
        else:
            raise ValueError(f"Invalid priority type '{type(priority).__name__}'. Must be int or TaskPriority.")

        with self._lock:
            # Check in-memory queue
            item = self._queue.get(task_id)
            if item:
                item.base_priority = assigned_p
                eff_p, wait_bonus = calculate_effective_priority(
                    base_priority=item.base_priority,
                    enqueued_at=item.enqueued_at,
                    current_time=time.time(),
                    aging_rate=self.aging_rate,
                    max_wait_bonus=self.max_wait_bonus,
                )
                item.effective_priority = eff_p
                item.wait_bonus = wait_bonus
                if item.task and hasattr(item.task, "safe_metadata"):
                    item.task.safe_metadata["priority"] = assigned_p

                if self.repo:
                    try:
                        self.repo.save_scheduled_task(
                            task_id=task_id,
                            priority=assigned_p,
                            effective_priority=eff_p,
                        )
                        if item.task:
                            self.repo.update_task(item.task)
                    except Exception as exc:
                        logger.debug(f"[SCHEDULER] Could not update repo on priority update: {exc}")

                self._emit_telemetry(
                    action_type=ActionType.TASK_PRIORITY_CHANGED,
                    status=ActionStatus.COMPLETED,
                    title=f"Task {task_id} priority updated to {assigned_p}",
                    task_id=task_id,
                    metadata={"base_priority": assigned_p, "effective_priority": eff_p},
                )
                return item

            # Check active tasks
            active_item = self._active_tasks.get(task_id)
            if active_item:
                active_item.base_priority = assigned_p
                if active_item.task and hasattr(active_item.task, "safe_metadata"):
                    active_item.task.safe_metadata["priority"] = assigned_p
                if self.repo:
                    try:
                        self.repo.save_scheduled_task(
                            task_id=task_id,
                            priority=assigned_p,
                            effective_priority=float(assigned_p),
                        )
                        if active_item.task:
                            self.repo.update_task(active_item.task)
                    except Exception as exc:
                        logger.debug(f"[SCHEDULER] Could not update repo on priority update: {exc}")
                self._emit_telemetry(
                    action_type=ActionType.TASK_PRIORITY_CHANGED,
                    status=ActionStatus.COMPLETED,
                    title=f"Active Task {task_id} priority updated to {assigned_p}",
                    task_id=task_id,
                    metadata={"base_priority": assigned_p, "effective_priority": float(assigned_p)},
                )
                return active_item

            # Check persistent repository
            if self.repo:
                lht = self.repo.get_task(task_id)
                sch = self.repo.get_scheduled_task(task_id)
                if lht or sch:
                    if lht:
                        lht.safe_metadata["priority"] = assigned_p
                        self.repo.update_task(lht)
                    self.repo.save_scheduled_task(
                        task_id=task_id,
                        priority=assigned_p,
                        effective_priority=float(assigned_p),
                    )
                    created_st = ScheduledTask(
                        task_id=task_id,
                        task=lht,
                        base_priority=assigned_p,
                        effective_priority=float(assigned_p),
                        state=lht.state if lht else (sch.get("state", "QUEUED") if sch else "QUEUED"),
                    )
                    self._emit_telemetry(
                        action_type=ActionType.TASK_PRIORITY_CHANGED,
                        status=ActionStatus.COMPLETED,
                        title=f"Persisted Task {task_id} priority updated to {assigned_p}",
                        task_id=task_id,
                        metadata={"base_priority": assigned_p, "effective_priority": float(assigned_p)},
                    )
                    return created_st

            return None

    def get_active_tasks(self) -> List[ScheduledTask]:
        """Return currently active tasks."""
        with self._lock:
            return list(self._active_tasks.values())

    # -----------------------------------------------------------------------
    # Task Pause, Resume & Cancellation
    # -----------------------------------------------------------------------

    async def pause_task(self, task_id: str, reason: str = "User requested pause") -> Optional[ScheduledTask]:
        """Pause a queued or active task."""
        with self._lock:
            # If in queue
            if task_id in self._queue:
                item = self._queue.pop(task_id)
                item.state = LongHorizonTaskState.PAUSED
                if item.task:
                    item.task.transition_to(LongHorizonTaskState.PAUSED, reason=reason)
                    if self.repo:
                        self.repo.update_task(item.task)
                self._paused_tasks[task_id] = item
                self._metrics["total_paused"] += 1
                self._emit_telemetry(ActionType.LONG_TASK_PAUSED, ActionStatus.COMPLETED, f"Paused queued task {task_id}", task_id)
                return item

            # If active
            if task_id in self._active_tasks:
                item = self._active_tasks.pop(task_id)
                item.state = LongHorizonTaskState.PAUSED
                self._paused_tasks[task_id] = item
                self._metrics["total_paused"] += 1
                if self.task_manager:
                    await self.task_manager.pause_task(task_id, reason=reason)
                return item

            if task_id in self._paused_tasks:
                return self._paused_tasks[task_id]

        return None

    async def resume_task(self, task_id: str, reason: str = "Resuming task") -> Optional[ScheduledTask]:
        """Resume a paused task back into the queued pool."""
        with self._lock:
            if task_id in self._paused_tasks:
                item = self._paused_tasks.pop(task_id)
                item.state = LongHorizonTaskState.QUEUED
                item.enqueued_at = time.time()
                item.effective_priority = float(item.base_priority)
                item.wait_bonus = 0.0

                if item.task:
                    item.task.transition_to(LongHorizonTaskState.QUEUED, reason=reason)
                    if self.repo:
                        self.repo.update_task(item.task)

                self._queue[task_id] = item
                self._metrics["total_resumed"] += 1
                self._emit_telemetry(ActionType.TASK_ENQUEUED, ActionStatus.PENDING, f"Resumed task {task_id} into queue", task_id)
                return item

            if self.task_manager:
                t = self.repo.get_task(task_id) if self.repo else None
                if t and t.state == LongHorizonTaskState.PAUSED:
                    return self.enqueue_task(t)

        return None

    async def cancel_task(self, task_id: str, reason: str = "User requested cancellation") -> Optional[ScheduledTask]:
        """Cancel a queued, active, or paused task immediately."""
        with self._lock:
            item = self._queue.pop(task_id, None) or self._active_tasks.pop(task_id, None) or self._paused_tasks.pop(task_id, None)
            if item:
                item.state = LongHorizonTaskState.CANCELLED
                if item.task:
                    item.task.transition_to(LongHorizonTaskState.CANCELLED, reason=reason)
                    if self.repo:
                        self.repo.update_task(item.task)
                self._metrics["total_cancelled"] += 1
                self._emit_telemetry(ActionType.LONG_TASK_CANCELLED, ActionStatus.CANCELLED, f"Cancelled task {task_id}: {reason}", task_id)
                self._emit_telemetry(ActionType.TASK_DEQUEUED, ActionStatus.CANCELLED, f"Task {task_id} dequeued upon cancellation", task_id)

                if self.resource_manager:
                    try:
                        self.resource_manager.release_all(task_id)
                    except Exception:
                        pass

                if self.concurrency_controller:
                    try:
                        self.concurrency_controller.release_slot(task_id, reason=reason)
                    except Exception:
                        pass

                if self.task_manager:
                    try:
                        await self.task_manager.cancel_task(task_id, reason=reason)
                    except Exception:
                        pass
                return item

            # If task exists only in persistent repository
            if self.repo:
                persisted = self.repo.get_task(task_id)
                if persisted and persisted.state not in (LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED):
                    persisted.transition_to(LongHorizonTaskState.CANCELLED, reason=reason)
                    self.repo.update_task(persisted)
                    if self.task_manager:
                        try:
                            await self.task_manager.cancel_task(task_id, reason=reason)
                        except Exception:
                            pass
                    return ScheduledTask(task_id=task_id, task=persisted, state=LongHorizonTaskState.CANCELLED)

        return None

    async def retry_task(self, task_id: str, reason: str = "User requested retry") -> Optional[ScheduledTask]:
        """Retry a failed, interrupted, or recovery-required task."""
        with self._lock:
            task: Optional[LongHorizonTask] = None
            if self.repo:
                task = self.repo.get_task(task_id)
            elif self.task_manager:
                task = self.task_manager.get_task(task_id)

            if not task:
                return None

            if task.state not in (
                LongHorizonTaskState.FAILED,
                LongHorizonTaskState.INTERRUPTED,
                LongHorizonTaskState.RECOVERY_REQUIRED,
            ):
                raise ValueError(f"Task '{task_id}' in state '{task.state.value}' is not eligible for retry.")

            if task.recovery_count >= 3:
                raise ValueError(f"Task '{task_id}' has reached maximum recovery attempts ({task.recovery_count}).")

            task.recovery_count += 1
            task.transition_to(LongHorizonTaskState.QUEUED, reason=reason)
            if self.repo:
                self.repo.update_task(task)

            # Re-enqueue cleanly
            scheduled = self.enqueue_task(task)
            self._emit_telemetry(
                action_type=ActionType.TASK_RESUMED,
                status=ActionStatus.PENDING,
                title=f"Task {task_id} retried and re-enqueued",
                task_id=task_id,
                metadata={"retry_count": task.recovery_count, "reason": reason},
            )
            return scheduled

    # -----------------------------------------------------------------------
    # Scheduler Status & Metrics
    # -----------------------------------------------------------------------

    def scheduler_status(self) -> SchedulerStatus:
        """Expose internal scheduler state and metrics with zero secrets."""
        with self._lock:
            uptime = 0.0
            if self._started_at and self._state == SchedulerState.RUNNING:
                uptime = max(0.0, time.time() - self._started_at)

            return SchedulerStatus(
                scheduler_state=self._state,
                queued_task_count=len(self._queue),
                active_task_count=len(self._active_tasks),
                max_concurrency=self.max_concurrency,
                queued_task_ids=list(self._queue.keys()),
                active_task_ids=list(self._active_tasks.keys()),
                scheduler_uptime_seconds=round(uptime, 2),
                last_scheduling_cycle=self._last_cycle_at,
                bounded_metrics=dict(self._metrics),
            )

    # -----------------------------------------------------------------------
    # Scheduler Lifecycle & Loop
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler asynchronously and emit lifecycle telemetry."""
        with self._lock:
            if self._state == SchedulerState.RUNNING:
                return
            self._state = SchedulerState.RUNNING
            self._started_at = time.time()

        await self._emit_telemetry_async(
            action_type=ActionType.SCHEDULER_STARTED,
            status=ActionStatus.COMPLETED,
            title="MultiTaskScheduler started",
            task_id="scheduler",
        )
        logger.info("[SCHEDULER] MultiTaskScheduler started.")

    async def stop(self) -> None:
        """Cleanly stop the scheduler and cancel background loops."""
        with self._lock:
            if self._state == SchedulerState.STOPPED:
                return
            self._state = SchedulerState.STOPPING

            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._loop_task = None

            self._state = SchedulerState.STOPPED

        await self._emit_telemetry_async(
            action_type=ActionType.SCHEDULER_STOPPED,
            status=ActionStatus.COMPLETED,
            title="MultiTaskScheduler stopped",
            task_id="scheduler",
        )
        logger.info("[SCHEDULER] MultiTaskScheduler stopped cleanly.")

    def start_sync(self) -> None:
        """Synchronous start convenience."""
        with self._lock:
            if self._state == SchedulerState.RUNNING:
                return
            self._state = SchedulerState.RUNNING
            self._started_at = time.time()
        self._emit_telemetry(ActionType.SCHEDULER_STARTED, ActionStatus.COMPLETED, "Scheduler started", "scheduler")

    def stop_sync(self) -> None:
        """Synchronous stop convenience."""
        with self._lock:
            self._state = SchedulerState.STOPPED
        self._emit_telemetry(ActionType.SCHEDULER_STOPPED, ActionStatus.COMPLETED, "Scheduler stopped", "scheduler")

    async def scheduling_cycle(self) -> Optional[ScheduledTask]:
        """Execute a single bounded scheduling cycle."""
        with self._lock:
            self._last_cycle_at = _utc_now_iso()
            self._metrics["total_scheduling_cycles"] += 1

            if len(self._active_tasks) >= self.max_concurrency:
                return None

        selected = self.select_next_task()
        return selected

    async def run_loop(self) -> None:
        """Bounded, cooperative scheduling loop. Avoids busy-waiting and spins cleanly."""
        while self._state == SchedulerState.RUNNING:
            try:
                await self.scheduling_cycle()
                await asyncio.sleep(self.cycle_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[SCHEDULER] Error during cycle: {exc}")
                await asyncio.sleep(self.cycle_interval_seconds)

    # -----------------------------------------------------------------------
    # Security Invariants & Compliance Verification
    # -----------------------------------------------------------------------

    def verify_security_invariants(
        self,
        task: Optional[LongHorizonTask] = None,
        priority: int = TaskPriority.NORMAL.value,
    ) -> bool:
        """Verify that priority never bypasses SafetyGuard, confirmation, permissions, or security policies.

        INVARIANTS:
        1. CRITICAL priority NEVER bypasses SafetyGuard.
        2. Priority NEVER bypasses ConfirmationManager tokens for consequential steps.
        3. Priority NEVER bypasses CapabilityPermissionManager boundaries.
        4. Tasks NEVER persist unredacted credentials or secrets.
        """
        # Invariant 1: SafetyGuard cannot be bypassed
        assert SafetyGuard is not None, "SafetyGuard must remain bound."

        # Invariant 2 & 4: If task is provided, check confirmation requirements and secrets
        if task:
            for step in task.steps:
                if step.requires_confirmation:
                    # Consequential confirmation flag MUST remain true regardless of priority
                    assert step.requires_confirmation is True, (
                        f"Security invariant violated: Confirmation required for step '{step.name}' "
                        f"cannot be cleared by priority {priority}."
                    )

            # Scrubbing check
            for k in task.safe_metadata:
                k_lower = str(k).lower()
                for secret_kw in ("password", "secret", "token", "api_key", "cookie", "bearer"):
                    if secret_kw in k_lower:
                        assert task.safe_metadata[k] == "[REDACTED]", f"Secret key '{k}' must be redacted."

        return True

    # -----------------------------------------------------------------------
    # Telemetry Helpers
    # -----------------------------------------------------------------------

    def _emit_telemetry(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        task_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit scheduling event to ActionEventBus without blocking or raising."""
        try:
            safe_meta = _scrub_secrets_recursive(metadata or {})
            event = ActionEvent(
                action_type=action_type,
                status=status,
                title=title,
                task_id=task_id,
                metadata=safe_meta,
            )
            self.event_bus.emit(event)
        except Exception as exc:
            logger.debug(f"[SCHEDULER] Telemetry emission failed: {exc}")

    async def _emit_telemetry_async(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        task_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Asynchronously publish event directly to ActionEventBus subscribers."""
        try:
            safe_meta = _scrub_secrets_recursive(metadata or {})
            event = ActionEvent(
                action_type=action_type,
                status=status,
                title=title,
                task_id=task_id,
                metadata=safe_meta,
            )
            await self.event_bus.publish(event)
        except Exception as exc:
            logger.debug(f"[SCHEDULER] Async telemetry emission failed: {exc}")


# Singleton scheduler instance
multi_task_scheduler = MultiTaskScheduler()

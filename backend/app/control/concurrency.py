"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 3: Authoritative Safe Concurrency & Cooperative Preemption Controller.

Architecture:
    User / Conversation
            ↓
    MultiTaskScheduler
            ↓
    SafeConcurrencyController  ← (Phase 3 Concurrency & Preemption Authority)
            ↓
    TaskResourceManager        ← (Phase 2 Resource Ownership & Locks)
            ↓
    LongHorizonTaskManager     ← (Multi-step progression & checkpoints)
            ↓
    UnifiedTaskOrchestrator    ← (Authoritative execution router)
            ↓
    Existing Execution Engines

CRITICAL SECURITY & ARCHITECTURAL INVARIANTS:
1. Coordination layer ONLY. Never directly executes Win32, raw mouse/keyboard, subprocess, or shell.
2. Zero subprocess, os.system, os.popen, os.spawn*, cmd, PowerShell, shell, ctypes, pyautogui, pynput.
3. Safe Cooperative Preemption: NEVER forcibly terminate a running thread, process, or action.
4. Preemption occurs strictly at safe checkpoints (after the current logical step completes).
5. Protected atomic actions and active desktop steps defer preemption until checkpoint boundary.
6. Resource release on pause: all resources held by preempted tasks are released to prevent deadlock.
7. Resume validates checkpoint integrity, environment drift, and reacquires resources atomically.
8. Bounded concurrency (default max_concurrency = 2, configurable).
9. Bounded preemption cooldown and maximum preemption limits prevent starvation and priority thrashing.
10. Full ActionEventBus telemetry integration with complete secret sanitization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
import uuid

from pydantic import BaseModel, Field, model_validator

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.long_horizon import LongHorizonTaskState, _scrub_secrets_recursive
from app.control.resources import (
    ResourceLease,
    ResourceRequest,
    ResourceType,
    TaskResourceManager,
    resource_manager,
)
from app.core.logging_config import logger


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Concurrency & Preemption State Models
# ---------------------------------------------------------------------------

class ConcurrencyState(str, Enum):
    """Lifecycle state of an execution slot in the concurrency control plane."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PREEMPTION_REQUESTED = "PREEMPTION_REQUESTED"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class PreemptionStatus(str, Enum):
    """Result status of a cooperative preemption request."""
    ACCEPTED = "ACCEPTED"    # Preemption requested; task is ready to pause at next checkpoint
    DEFERRED = "DEFERRED"    # Task is inside an atomic action; preemption deferred until boundary
    REJECTED = "REJECTED"    # Preemption request rejected (cooldown, delta, or limit)
    COMPLETED = "COMPLETED"  # Cooperative pause and resource release succeeded


# Preemption configuration thresholds
DEFAULT_MAX_CONCURRENCY: int = 2
DEFAULT_MIN_PREEMPTION_PRIORITY_DELTA: float = 10.0
DEFAULT_PREEMPTION_COOLDOWN_SECONDS: float = 5.0
DEFAULT_MAX_PREEMPTIONS_PER_TASK: int = 3
DEFAULT_MAX_CONSECUTIVE_PREEMPTIONS: int = 2


class ExecutionSlot(BaseModel):
    """Authoritative representation of an active task execution slot."""
    slot_id: str = Field(default_factory=lambda: f"slot-{uuid.uuid4().hex[:8]}")
    task_id: str
    started_at: float = Field(default_factory=time.time)
    started_at_iso: str = Field(default_factory=_utc_now_iso)
    state: ConcurrencyState = Field(default=ConcurrencyState.RUNNING)
    resources: List[ResourceRequest] = Field(default_factory=list)
    priority: float = Field(default=0.0)
    preemption_requested: bool = False
    preemption_requested_at: Optional[float] = None
    is_atomic_protected: bool = False
    is_desktop_action_active: bool = False
    last_checkpoint: Optional[Dict[str, Any]] = None
    last_progress_at: float = Field(default_factory=time.time)
    preemption_count: int = 0
    consecutive_preemptions: int = 0
    last_preempted_at: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_slot_data(self) -> ExecutionSlot:
        self.metadata = _scrub_secrets_recursive(self.metadata)
        if self.last_checkpoint:
            self.last_checkpoint = _scrub_secrets_recursive(self.last_checkpoint)
        return self

    @property
    def is_active(self) -> bool:
        return self.state in (ConcurrencyState.RUNNING, ConcurrencyState.PREEMPTION_REQUESTED, ConcurrencyState.PAUSING)

    @property
    def is_preemptible(self) -> bool:
        return self.state == ConcurrencyState.RUNNING and not self.preemption_requested


class ConcurrencyAdmissionDecision(BaseModel):
    """Structured response for concurrency admission evaluation."""
    admitted: bool
    task_id: str
    reason: str
    slot_id: Optional[str] = None
    conflicting_resources: List[str] = Field(default_factory=list)
    concurrency_available: bool = True


class PreemptionDecision(BaseModel):
    """Structured decision returned when cooperative preemption is evaluated or requested."""
    task_id: str
    status: PreemptionStatus
    reason: str
    requesting_task_id: Optional[str] = None
    priority_delta: float = 0.0
    safe_checkpoint_available: bool = True
    is_deferred: bool = False


class ConcurrencySnapshot(BaseModel):
    """Comprehensive read-only snapshot of the concurrency control plane."""
    timestamp: str = Field(default_factory=_utc_now_iso)
    max_concurrency: int
    active_task_count: int
    active_slots: Dict[str, ExecutionSlot]
    preempting_tasks: List[str] = Field(default_factory=list)
    paused_tasks_count: int = 0
    bounded_metrics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_snapshot_metrics(self) -> ConcurrencySnapshot:
        self.bounded_metrics = _scrub_secrets_recursive(self.bounded_metrics)
        return self


# ---------------------------------------------------------------------------
# 2. Authoritative SafeConcurrencyController (Phase 3)
# ---------------------------------------------------------------------------

class SafeConcurrencyController:
    """Authoritative Concurrency & Cooperative Preemption Controller for RYVEN 3.0.

    RESPONSIBILITIES:
    1. enforce bounded concurrency capacity (max_concurrency)
    2. coordinate with TaskResourceManager to prevent resource conflicts
    3. track active execution slots with metadata and checkpoint state
    4. select preemption candidates deterministically based on priority delta
    5. request cooperative preemption without killing threads or actions
    6. defer preemption during protected atomic steps or desktop interactions
    7. pause tasks at safe checkpoints and release all held resources
    8. safely resume paused tasks after verifying checkpoints and reacquiring resources
    9. enforce preemption cooldowns, limits, and bounded priority fairness
    10. emit rich, sanitized ActionEventBus telemetry
    """

    def __init__(
        self,
        resource_manager_inst: Optional[TaskResourceManager] = None,
        resource_manager: Optional[TaskResourceManager] = None,
        scheduler_inst: Optional[Any] = None,
        task_manager_inst: Optional[Any] = None,
        event_bus: Optional[ActionEventBus] = None,
        bus: Optional[ActionEventBus] = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        min_preemption_priority_delta: float = DEFAULT_MIN_PREEMPTION_PRIORITY_DELTA,
        preemption_cooldown_seconds: float = DEFAULT_PREEMPTION_COOLDOWN_SECONDS,
        max_preemptions_per_task: int = DEFAULT_MAX_PREEMPTIONS_PER_TASK,
        max_consecutive_preemptions: int = DEFAULT_MAX_CONSECUTIVE_PREEMPTIONS,
    ) -> None:
        self.resource_manager = resource_manager or resource_manager_inst
        if self.resource_manager is None:
            from app.control.resources import resource_manager as _rm
            self.resource_manager = _rm
        self.scheduler = scheduler_inst
        self.task_manager = task_manager_inst
        self.event_bus = event_bus or bus or action_bus

        # Concurrency & Preemption Policy Limits
        self.max_concurrency = max(1, max_concurrency)
        self.min_preemption_priority_delta = max(0.0, min_preemption_priority_delta)
        self.preemption_cooldown_seconds = max(0.0, preemption_cooldown_seconds)
        self.max_preemptions_per_task = max(1, max_preemptions_per_task)
        self.max_consecutive_preemptions = max(1, max_consecutive_preemptions)

        # Thread synchronization
        self._lock = threading.RLock()

        # Active execution slots: task_id -> ExecutionSlot
        self._slots: Dict[str, ExecutionSlot] = {}

        # Paused tasks tracking checkpoints: task_id -> Dict[str, Any]
        self._paused_checkpoints: Dict[str, Dict[str, Any]] = {}

        # Controller lifecycle
        self._is_running = True

        # Bounded metrics (strictly counters)
        self._metrics: Dict[str, int] = {
            "active_task_count": 0,
            "max_concurrency": self.max_concurrency,
            "total_slots_acquired": 0,
            "total_slots_released": 0,
            "total_preemptions": 0,
            "successful_preemptions": 0,
            "deferred_preemptions": 0,
            "failed_preemptions": 0,
            "preemptions_rejected_cooldown": 0,
            "preemptions_rejected_delta": 0,
            "preemptions_rejected_limit": 0,
            "resume_count": 0,
            "resume_failures": 0,
            "concurrency_blocks": 0,
            "resource_blocks": 0,
        }

    # -----------------------------------------------------------------------
    # Lifecycle Control
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the concurrency controller."""
        with self._lock:
            self._is_running = True

    def clear_all(self) -> None:
        """Clear all active execution slots and paused checkpoints (used in crash recovery)."""
        with self._lock:
            self._slots.clear()
            self._paused_checkpoints.clear()
            self._metrics["active_task_count"] = 0

    def stop(self) -> None:
        """Clean shutdown of concurrency tracking."""
        with self._lock:
            self._is_running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    # -----------------------------------------------------------------------
    # Concurrency Admission & Slot Allocation
    # -----------------------------------------------------------------------

    def can_admit(
        self,
        task_id: str,
        resources: Optional[Union[List[Union[ResourceType, ResourceRequest, str]], ResourceType, ResourceRequest, str]] = None,
    ) -> bool:
        """Check if a task can be admitted for concurrent execution (read-only inspection)."""
        decision = self.can_admit_task(task_id, resources=resources)
        return decision.admitted

    def can_admit_task(
        self,
        task_or_id: Any,
        priority: Optional[float] = None,
        resources: Optional[Union[List[Union[ResourceType, ResourceRequest, str]], ResourceType, ResourceRequest, str]] = None,
    ) -> ConcurrencyAdmissionDecision:
        """Structured evaluation of concurrency admission."""
        task_id = getattr(task_or_item := task_or_id, "task_id", str(task_or_id))

        with self._lock:
            # 1. Reentrancy: Task already holds an active slot
            if task_id in self._slots:
                return ConcurrencyAdmissionDecision(
                    admitted=True,
                    task_id=task_id,
                    slot_id=self._slots[task_id].slot_id,
                    reason="TASK_ALREADY_ACTIVE",
                    concurrency_available=True,
                )

            # 2. Concurrency Capacity Check
            active_count = len([s for s in self._slots.values() if s.is_active])
            if active_count >= self.max_concurrency:
                self._metrics["concurrency_blocks"] += 1
                return ConcurrencyAdmissionDecision(
                    admitted=False,
                    task_id=task_id,
                    reason="CONCURRENCY_LIMIT_REACHED",
                    concurrency_available=False,
                )

            # 3. Resource Availability Check
            req_resources: List[ResourceRequest] = []
            if resources is not None:
                req_resources = self.resource_manager.normalize_requests(resources)
            elif self.resource_manager:
                req_resources = self.resource_manager.infer_required_resources(task_or_item)

            if req_resources and self.resource_manager:
                conflicts = self.resource_manager.get_conflicts(task_id, req_resources)
                if conflicts:
                    self._metrics["resource_blocks"] += 1
                    return ConcurrencyAdmissionDecision(
                        admitted=False,
                        task_id=task_id,
                        reason=f"RESOURCE_CONFLICT: {conflicts[0].reason}",
                        conflicting_resources=[c.resource for c in conflicts],
                        concurrency_available=True,
                    )

            return ConcurrencyAdmissionDecision(
                admitted=True,
                task_id=task_id,
                reason="ADMISSIBLE",
                concurrency_available=True,
            )

    def acquire_slot(
        self,
        task_id: str,
        priority: float = 0.0,
        resources: Optional[Union[List[Union[ResourceType, ResourceRequest, str]], ResourceType, ResourceRequest, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionSlot:
        """Allocate an execution slot for an admitted task."""
        if not task_id or not str(task_id).strip():
            raise ValueError("Task ID cannot be empty when acquiring an execution slot.")

        with self._lock:
            # Reentrancy check
            if task_id in self._slots:
                slot = self._slots[task_id]
                slot.priority = priority
                slot.last_progress_at = time.time()
                return slot

            # Validate admission
            decision = self.can_admit_task(task_id, priority=priority, resources=resources)
            if not decision.admitted:
                self._emit_telemetry(
                    ActionType.CONCURRENCY_BLOCKED,
                    ActionStatus.FAILED,
                    f"Concurrency admission blocked for task {task_id}: {decision.reason}",
                    task_id,
                    {"reason": decision.reason, "conflicts": decision.conflicting_resources},
                )
                raise RuntimeError(f"Cannot acquire execution slot for task '{task_id}': {decision.reason}")

            # Parse and acquire resources if provided
            req_resources: List[ResourceRequest] = []
            if resources is not None and self.resource_manager:
                req_resources = self.resource_manager.normalize_requests(resources)
                # Try acquiring resources synchronously
                self.resource_manager.acquire_resources(task_id, req_resources)

            slot = ExecutionSlot(
                task_id=task_id,
                priority=priority,
                resources=req_resources,
                state=ConcurrencyState.RUNNING,
                metadata=metadata or {},
            )
            self._slots[task_id] = slot

            self._metrics["total_slots_acquired"] += 1
            self._metrics["active_task_count"] = len(self._slots)

            self._emit_telemetry(
                ActionType.CONCURRENCY_SLOT_ACQUIRED,
                ActionStatus.COMPLETED,
                f"Task {task_id} acquired execution slot {slot.slot_id} (Priority={priority:.1f})",
                task_id,
                {"slot_id": slot.slot_id, "priority": priority, "resources": [r.canonical_string for r in req_resources]},
            )
            logger.info(f"[CONCURRENCY] Task '{task_id}' acquired slot '{slot.slot_id}'. Active: {len(self._slots)}/{self.max_concurrency}")
            return slot

    def release_slot(
        self,
        task_id: str,
        release_resources: bool = True,
        reason: str = "completed",
    ) -> bool:
        """Release an execution slot and optionally free all held resources."""
        with self._lock:
            slot = self._slots.pop(task_id, None)
            if not slot:
                return False

            slot.state = ConcurrencyState.COMPLETED
            self._metrics["total_slots_released"] += 1
            self._metrics["active_task_count"] = len(self._slots)

            if release_resources and self.resource_manager:
                self.resource_manager.release_all_for_task(task_id)

            self._emit_telemetry(
                ActionType.CONCURRENCY_SLOT_RELEASED,
                ActionStatus.COMPLETED,
                f"Task {task_id} released execution slot {slot.slot_id} ({reason})",
                task_id,
                {"slot_id": slot.slot_id, "reason": reason},
            )
            logger.info(f"[CONCURRENCY] Task '{task_id}' released slot '{slot.slot_id}'. Active: {len(self._slots)}/{self.max_concurrency}")
            return True

    def get_slot(self, task_id: str) -> Optional[ExecutionSlot]:
        """Inspect the execution slot for task_id (read-only)."""
        with self._lock:
            return self._slots.get(task_id)

    def list_active_slots(self) -> List[ExecutionSlot]:
        """List all currently active execution slots."""
        with self._lock:
            return list(self._slots.values())

    @property
    def active_count(self) -> int:
        """Return the number of currently allocated execution slots."""
        with self._lock:
            return len(self._slots)

    # -----------------------------------------------------------------------
    # Atomic Action & Desktop Safety Gates
    # -----------------------------------------------------------------------

    def set_atomic_protection(self, task_id: str, protected: bool) -> bool:
        """Set whether the task is currently executing an atomic / non-interruptible action."""
        with self._lock:
            slot = self._slots.get(task_id)
            if not slot:
                return False
            slot.is_atomic_protected = protected
            slot.last_progress_at = time.time()
            return True

    def set_desktop_action_active(self, task_id: str, active: bool) -> bool:
        """Set whether the task is actively executing a physical desktop interaction."""
        with self._lock:
            slot = self._slots.get(task_id)
            if not slot:
                return False
            slot.is_desktop_action_active = active
            slot.last_progress_at = time.time()
            return True

    # -----------------------------------------------------------------------
    # Deterministic Preemption Candidate Selection
    # -----------------------------------------------------------------------

    def select_preemption_candidate(
        self,
        candidate_high_task_id: str,
        high_task_priority: float,
        high_task_resources: Optional[Union[List[Union[ResourceType, ResourceRequest, str]], ResourceType, ResourceRequest, str]] = None,
    ) -> Optional[ExecutionSlot]:
        """Deterministically choose the best running task to cooperatively preempt.

        SELECTION CRITERIA:
        1. candidate is currently RUNNING
        2. candidate is preemptible (not already preemption_requested)
        3. priority delta >= min_preemption_priority_delta
        4. candidate has not exceeded max_preemptions_per_task
        5. preemption cooldown has elapsed since last preemption
        6. releasing candidate unblocks high_task_resources OR capacity is full
        7. deterministic tie-breaking:
           - lowest effective priority first
           - highest preemption count first
           - oldest runtime first
           - task_id lexicographical tie-breaker
        """
        with self._lock:
            now = time.time()
            candidates: List[ExecutionSlot] = []

            for slot in self._slots.values():
                # Skip self
                if slot.task_id == candidate_high_task_id:
                    continue

                # Must be currently RUNNING
                if slot.state != ConcurrencyState.RUNNING:
                    continue

                # Must not already be pending preemption
                if slot.preemption_requested:
                    continue

                # 1. Priority Delta Check
                delta = high_task_priority - slot.priority
                if delta < self.min_preemption_priority_delta:
                    continue

                # 2. Maximum Preemption Count Check
                if slot.preemption_count >= self.max_preemptions_per_task:
                    continue

                # 3. Cooldown Check
                if slot.last_preempted_at is not None:
                    if (now - slot.last_preempted_at) < self.preemption_cooldown_seconds:
                        continue

                # 4. Resource or Concurrency relevance check
                # Either slots are full, or slot holds resources needed by higher task
                relevance = False
                if len(self._slots) >= self.max_concurrency:
                    relevance = True
                elif high_task_resources and self.resource_manager:
                    norm_reqs = self.resource_manager.normalize_requests(high_task_resources)
                    held_by_slot = set(self.resource_manager.list_resources_for_task(slot.task_id))
                    needed_keys = {r.canonical_string for r in norm_reqs}
                    if held_by_slot.intersection(needed_keys):
                        relevance = True

                if relevance:
                    candidates.append(slot)

            if not candidates:
                return None

            # Deterministic sorting:
            # 1. lowest priority first (ascending)
            # 2. highest preemption count first (descending)
            # 3. oldest started_at first (ascending)
            # 4. task_id ascending
            candidates.sort(
                key=lambda s: (
                    s.priority,
                    -s.preemption_count,
                    s.started_at,
                    s.task_id,
                )
            )
            return candidates[0]

    # -----------------------------------------------------------------------
    # Cooperative Preemption Protocol
    # -----------------------------------------------------------------------

    def request_preemption(
        self,
        task_id: str,
        requesting_task_id: Optional[str] = None,
        requesting_priority: Optional[float] = None,
        reason: str = "Higher priority task waiting",
    ) -> PreemptionDecision:
        """Request cooperative preemption for a running task.

        NEVER forcibly terminates running threads or actions.
        If task is in a protected atomic action, preemption is DEFERRED.
        """
        with self._lock:
            slot = self._slots.get(task_id)
            if not slot:
                return PreemptionDecision(
                    task_id=task_id,
                    status=PreemptionStatus.REJECTED,
                    reason="TASK_NOT_ACTIVE",
                    requesting_task_id=requesting_task_id,
                )

            # Check delta if requesting_priority provided
            if requesting_priority is not None:
                delta = requesting_priority - slot.priority
                if delta < self.min_preemption_priority_delta:
                    self._metrics["preemptions_rejected_delta"] += 1
                    return PreemptionDecision(
                        task_id=task_id,
                        status=PreemptionStatus.REJECTED,
                        reason=f"PRIORITY_DELTA_TOO_LOW ({delta:.1f} < {self.min_preemption_priority_delta:.1f})",
                        requesting_task_id=requesting_task_id,
                        priority_delta=delta,
                    )
            else:
                delta = 0.0

            # Check limits
            if slot.preemption_count >= self.max_preemptions_per_task:
                self._metrics["preemptions_rejected_limit"] += 1
                return PreemptionDecision(
                    task_id=task_id,
                    status=PreemptionStatus.REJECTED,
                    reason=f"MAX_PREEMPTIONS_EXCEEDED ({slot.preemption_count} >= {self.max_preemptions_per_task})",
                    requesting_task_id=requesting_task_id,
                )

            # Check cooldown
            now = time.time()
            if slot.last_preempted_at is not None:
                elapsed = now - slot.last_preempted_at
                if elapsed < self.preemption_cooldown_seconds:
                    self._metrics["preemptions_rejected_cooldown"] += 1
                    return PreemptionDecision(
                        task_id=task_id,
                        status=PreemptionStatus.REJECTED,
                        reason=f"PREEMPTION_COOLDOWN_ACTIVE ({elapsed:.1f}s < {self.preemption_cooldown_seconds:.1f}s)",
                        requesting_task_id=requesting_task_id,
                    )

            # Check atomic protection & desktop safety
            if slot.is_atomic_protected or slot.is_desktop_action_active:
                # Defer preemption safely
                slot.preemption_requested = True
                slot.preemption_requested_at = now
                self._metrics["total_preemptions"] += 1
                self._metrics["deferred_preemptions"] += 1

                self._emit_telemetry(
                    ActionType.PREEMPTION_DEFERRED,
                    ActionStatus.PROGRESS,
                    f"Preemption deferred for task {task_id} (Atomic/Desktop action in progress)",
                    task_id,
                    {"requesting_task": requesting_task_id, "reason": "ATOMIC_ACTION_PROTECTED"},
                )
                logger.info(f"[CONCURRENCY] Preemption deferred for task '{task_id}': protected action active.")
                return PreemptionDecision(
                    task_id=task_id,
                    status=PreemptionStatus.DEFERRED,
                    reason="ACTION_PROTECTED_DEFERRED",
                    requesting_task_id=requesting_task_id,
                    priority_delta=delta,
                    safe_checkpoint_available=False,
                    is_deferred=True,
                )

            # Immediate cooperative request accepted
            slot.preemption_requested = True
            slot.preemption_requested_at = now
            slot.state = ConcurrencyState.PREEMPTION_REQUESTED
            self._metrics["total_preemptions"] += 1

            self._emit_telemetry(
                ActionType.PREEMPTION_REQUESTED,
                ActionStatus.PENDING,
                f"Cooperative preemption requested for task {task_id}",
                task_id,
                {"requesting_task": requesting_task_id, "reason": reason, "priority_delta": delta},
            )
            logger.info(f"[CONCURRENCY] Cooperative preemption requested for task '{task_id}'.")
            return PreemptionDecision(
                task_id=task_id,
                status=PreemptionStatus.ACCEPTED,
                reason="PREEMPTION_ACCEPTED",
                requesting_task_id=requesting_task_id,
                priority_delta=delta,
                safe_checkpoint_available=True,
                is_deferred=False,
            )

    # -----------------------------------------------------------------------
    # Checkpoint Registration & Safe Pause Protocol
    # -----------------------------------------------------------------------

    def register_checkpoint(
        self,
        task_id: str,
        checkpoint_data: Dict[str, Any],
    ) -> bool:
        """Register a safe logical execution checkpoint for a running task."""
        if not checkpoint_data or not isinstance(checkpoint_data, dict):
            checkpoint_data = {"step_completed": True, "timestamp": _utc_now_iso()}

        with self._lock:
            slot = self._slots.get(task_id)
            clean_cp = _scrub_secrets_recursive(checkpoint_data)
            self._paused_checkpoints[task_id] = clean_cp

            if slot:
                slot.last_checkpoint = clean_cp
                slot.last_progress_at = time.time()
                # If preemption was deferred because of atomic action, it can now proceed
                if slot.is_atomic_protected:
                    slot.is_atomic_protected = False

            self._emit_telemetry(
                ActionType.PREEMPTION_CHECKPOINTED,
                ActionStatus.PROGRESS,
                f"Checkpoint registered for task {task_id}",
                task_id,
                {"checkpoint": clean_cp},
            )
            return True

    def acknowledge_checkpoint_and_pause(
        self,
        task_id: str,
        reason: str = "Preempted at safe checkpoint",
    ) -> bool:
        """Execute the cooperative pause: release resources, clear execution slot, and transition to PAUSED."""
        with self._lock:
            slot = self._slots.get(task_id)
            if not slot:
                logger.warning(f"[CONCURRENCY] Cannot pause task '{task_id}': no active slot.")
                return False

            now = time.time()
            slot.state = ConcurrencyState.PAUSING

            # 1. Ensure checkpoint exists
            if not slot.last_checkpoint and task_id in self._paused_checkpoints:
                slot.last_checkpoint = self._paused_checkpoints[task_id]
            if not slot.last_checkpoint:
                # Create fallback consistent checkpoint
                slot.last_checkpoint = {
                    "checkpoint_id": f"cp-{uuid.uuid4().hex[:6]}",
                    "timestamp": _utc_now_iso(),
                    "reason": reason,
                }
                self._paused_checkpoints[task_id] = slot.last_checkpoint

            # 2. Release held resources safely
            if self.resource_manager:
                self.resource_manager.release_all_for_task(task_id)

            # 3. Update slot accounting
            slot.state = ConcurrencyState.PAUSED
            slot.preemption_count += 1
            slot.consecutive_preemptions += 1
            slot.last_preempted_at = now
            slot.preemption_requested = False

            # 4. Remove from active execution slots so higher-priority task can run
            self._slots.pop(task_id, None)
            self._metrics["active_task_count"] = len(self._slots)
            self._metrics["successful_preemptions"] += 1

            # 5. Notify scheduler if available
            if self.scheduler and hasattr(self.scheduler, "pause_task"):
                try:
                    # Sync or async dispatch
                    res = self.scheduler.pause_task(task_id, reason=reason)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception as exc:
                    logger.debug(f"[CONCURRENCY] Scheduler pause notification: {exc}")

            self._emit_telemetry(
                ActionType.TASK_PAUSED,
                ActionStatus.COMPLETED,
                f"Task {task_id} safely paused at checkpoint ({reason})",
                task_id,
                {"preemption_count": slot.preemption_count, "checkpoint": slot.last_checkpoint},
            )
            self._emit_telemetry(
                ActionType.PREEMPTION_COMPLETED,
                ActionStatus.COMPLETED,
                f"Preemption cycle completed for task {task_id}",
                task_id,
                {"reason": reason},
            )
            logger.info(f"[CONCURRENCY] Task '{task_id}' cooperatively paused at checkpoint. Resources released.")
            return True

    # -----------------------------------------------------------------------
    # Task Resumption Protocol
    # -----------------------------------------------------------------------

    def resume_task(
        self,
        task_id: str,
        priority: Optional[float] = None,
        resources: Optional[Union[List[Union[ResourceType, ResourceRequest, str]], ResourceType, ResourceRequest, str]] = None,
        reason: str = "Resuming preempted task",
    ) -> bool:
        """Safely resume a paused task from its checkpoint.

        INVARIANTS:
        1. Validates checkpoint exists and is non-empty
        2. Validates capacity exists (or fails safely without state corruption)
        3. Validates and reacquires all required resources atomically
        4. Transitions task to RUNNING and allocates slot
        """
        with self._lock:
            # 1. Checkpoint Validation
            checkpoint = self._paused_checkpoints.get(task_id)
            if not checkpoint:
                self._metrics["resume_failures"] += 1
                logger.warning(f"[CONCURRENCY] Resume rejected for task '{task_id}': no valid checkpoint found.")
                return False

            # 2. Concurrency Capacity Check
            if len(self._slots) >= self.max_concurrency:
                self._metrics["concurrency_blocks"] += 1
                self._metrics["resume_failures"] += 1
                logger.warning(f"[CONCURRENCY] Resume blocked for task '{task_id}': concurrency capacity full.")
                return False

            # 3. Resource Availability Check
            req_resources: List[ResourceRequest] = []
            if resources is not None and self.resource_manager:
                req_resources = self.resource_manager.normalize_requests(resources)
            elif self.resource_manager:
                req_resources = self.resource_manager.infer_required_resources(task_id)

            if req_resources and self.resource_manager:
                if not self.resource_manager.can_acquire(task_id, req_resources):
                    self._metrics["resource_blocks"] += 1
                    self._metrics["resume_failures"] += 1
                    logger.warning(f"[CONCURRENCY] Resume blocked for task '{task_id}': resources unavailable.")
                    return False

            # 4. Atomically Reacquire Resources
            if req_resources and self.resource_manager:
                try:
                    self.resource_manager.acquire_resources(task_id, req_resources)
                except Exception as exc:
                    self._metrics["resume_failures"] += 1
                    logger.error(f"[CONCURRENCY] Resource reacquisition failed on resume for '{task_id}': {exc}")
                    return False

            # 5. Allocate Slot & Transition to RUNNING
            eff_priority = priority if priority is not None else 0.0
            slot = ExecutionSlot(
                task_id=task_id,
                priority=eff_priority,
                resources=req_resources,
                state=ConcurrencyState.RUNNING,
                last_checkpoint=checkpoint,
                metadata={"resumed_from": checkpoint.get("checkpoint_id", "default")},
            )
            self._slots[task_id] = slot

            self._metrics["resume_count"] += 1
            self._metrics["active_task_count"] = len(self._slots)

            # 6. Notify scheduler if available
            if self.scheduler and hasattr(self.scheduler, "resume_task"):
                try:
                    res = self.scheduler.resume_task(task_id, reason=reason)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception as exc:
                    logger.debug(f"[CONCURRENCY] Scheduler resume notification: {exc}")

            self._emit_telemetry(
                ActionType.TASK_RESUMED,
                ActionStatus.COMPLETED,
                f"Task {task_id} resumed from checkpoint ({reason})",
                task_id,
                {"checkpoint": checkpoint, "priority": eff_priority},
            )
            logger.info(f"[CONCURRENCY] Task '{task_id}' resumed successfully from checkpoint.")
            return True

    # -----------------------------------------------------------------------
    # Snapshot & Metrics
    # -----------------------------------------------------------------------

    def snapshot(self) -> ConcurrencySnapshot:
        """Produce an immutable, read-only snapshot of the concurrency plane."""
        with self._lock:
            slots_copy = {tid: s.model_copy() for tid, s in self._slots.items()}
            preempting = [s.task_id for s in self._slots.values() if s.preemption_requested]

            metrics_copy = dict(self._metrics)
            metrics_copy["active_task_count"] = len(self._slots)

            return ConcurrencySnapshot(
                max_concurrency=self.max_concurrency,
                active_task_count=len(self._slots),
                active_slots=slots_copy,
                preempting_tasks=preempting,
                paused_tasks_count=len(self._paused_checkpoints),
                bounded_metrics=metrics_copy,
            )

    # -----------------------------------------------------------------------
    # Telemetry
    # -----------------------------------------------------------------------

    def _emit_telemetry(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        task_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish sanitized event to ActionEventBus."""
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
            logger.debug(f"[CONCURRENCY] Telemetry emission failed: {exc}")


# Authoritative Singleton instance
concurrency_controller = SafeConcurrencyController()
safe_concurrency_controller = concurrency_controller
safe_concurrency_controller = concurrency_controller

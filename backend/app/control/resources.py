"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 2: Authoritative Resource Manager (ResourceManager).

Architecture:
    User / Conversation
            ↓
    MultiTaskScheduler
            ↓
     ResourceManager
            ↓
    ┌─────────────────────────┐
    │ 1. COMPUTER_INTERACTION │ ← strict global mutex
    │ 2. DESKTOP_SESSION      │
    │ 3. FOREGROUND_WINDOW    │
    │ 4. BROWSER_SESSION      │
    │ 5. BROWSER_TAB          │
    │ 6. WORKSPACE            │
    │ 7. PROJECT              │
    │ 8. NETWORK_CHANNEL      │
    │ 9. CLIPBOARD            │ ← strict global mutex
    └─────────────────────────┘
            ↓
    LongHorizonTaskManager
            ↓
    UnifiedTaskOrchestrator
            ↓
    Existing Execution Engines

Strict Security & Architectural Invariants:
- Coordination layer ONLY. Never directly executes Win32, raw mouse/keyboard, subprocess, or shell.
- Zero subprocess, os.system, os.popen, os.spawn*, cmd, PowerShell, shell, ctypes, pyautogui, pynput.
- COMPUTER_INTERACTION is a globally exclusive mutex (only one task may hold it).
- Atomic multi-resource acquisition (all-or-nothing commit/rollback).
- Deterministic canonical resource ordering eliminates circular wait deadlocks.
- Task-scoped ownership: wrong-owner release is strictly rejected.
- Bounded wait with structured timeout diagnostics (no unbounded loops).
- Full ActionEventBus telemetry integration with complete secret sanitization.
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
from app.actions.models import ActionEvent, ActionStatus, ActionType, _redact_dict
from app.control.long_horizon import _scrub_secrets_recursive
from app.core.logging_config import logger


def _utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Resource Types & Canonical Order
# ---------------------------------------------------------------------------

class ResourceType(str, Enum):
    """The 9 authoritative logical resource types for M17.8 Phase 2."""
    COMPUTER_INTERACTION = "COMPUTER_INTERACTION"  # Globally exclusive mutex: physical mouse/keyboard/screen
    DESKTOP_SESSION = "DESKTOP_SESSION"            # Desktop app session focus (session:<safe-id>)
    FOREGROUND_WINDOW = "FOREGROUND_WINDOW"        # Foreground window lock (window:<safe-id>)
    BROWSER_SESSION = "BROWSER_SESSION"            # Browser daemon instance (browser:<safe-id>)
    BROWSER_TAB = "BROWSER_TAB"                    # Browser tab allocation (browser-tab:<safe-id>)
    WORKSPACE = "WORKSPACE"                        # Filesystem workspace tree (workspace:<safe-id>)
    PROJECT = "PROJECT"                            # Project build/git repository (project:<safe-id>)
    NETWORK_CHANNEL = "NETWORK_CHANNEL"            # Network request / rate-limit slot (channel:<safe-id>)
    CLIPBOARD = "CLIPBOARD"                        # Globally exclusive mutex: OS clipboard read/write


class LockMode(str, Enum):
    """Lock acquisition semantics."""
    EXCLUSIVE = "EXCLUSIVE"  # Exclusive / Mutex lock
    SHARED = "SHARED"        # Shared read lock (for workspaces, projects)


# Deterministic canonical ordering to prevent circular-wait deadlocks
CANONICAL_RESOURCE_ORDER: List[ResourceType] = [
    ResourceType.COMPUTER_INTERACTION,
    ResourceType.DESKTOP_SESSION,
    ResourceType.FOREGROUND_WINDOW,
    ResourceType.BROWSER_SESSION,
    ResourceType.BROWSER_TAB,
    ResourceType.WORKSPACE,
    ResourceType.PROJECT,
    ResourceType.NETWORK_CHANNEL,
    ResourceType.CLIPBOARD,
]

# Default capacities for shared / pooled resources
DEFAULT_RESOURCE_CAPACITIES: Dict[ResourceType, int] = {
    ResourceType.COMPUTER_INTERACTION: 1,  # Strict Global Mutex
    ResourceType.CLIPBOARD: 1,             # Strict Global Mutex
    ResourceType.FOREGROUND_WINDOW: 1,     # Single active foreground window
    ResourceType.DESKTOP_SESSION: 2,       # Concurrent distinct app sessions
    ResourceType.BROWSER_SESSION: 2,       # Concurrent browser instances
    ResourceType.BROWSER_TAB: 8,           # Pool of concurrent browser tabs
    ResourceType.WORKSPACE: 4,             # Concurrent shared workspace readers
    ResourceType.PROJECT: 4,               # Concurrent shared project readers
    ResourceType.NETWORK_CHANNEL: 6,       # Concurrent network channels
}

DEFAULT_MAX_ACQUISITION_WAIT: float = 5.0  # 5 seconds default bounded timeout


# ---------------------------------------------------------------------------
# 2. Resource Models
# ---------------------------------------------------------------------------

class ResourceState(str, Enum):
    """Lifecycle state of an individual resource lock."""
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    LOCKED = "LOCKED"


class LeaseState(str, Enum):
    """Lifecycle state of an issued resource lease."""
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ResourceKey(BaseModel):
    """Structured representation of a logical resource identifier."""
    resource_type: ResourceType
    scope: str = Field(default="global", description="Scope or safe identifier")

    @property
    def canonical_string(self) -> str:
        """Formatted canonical key, e.g. COMPUTER_INTERACTION/global or DESKTOP_SESSION/session:1."""
        return f"{self.resource_type.value}/{self.scope}"

    @property
    def canonical_key(self) -> str:
        return self.canonical_string

    @classmethod
    def from_string(cls, raw: str) -> ResourceKey:
        """Parse from raw string (e.g. 'COMPUTER_INTERACTION/global', 'desktop_session:session:1', or 'computer_interaction')."""
        raw_s = raw.strip()
        if "/" in raw_s:
            parts = raw_s.split("/", 1)
            rtype_str = parts[0].strip().upper()
            scope = parts[1].strip()
        elif ":" in raw_s:
            parts = raw_s.split(":", 1)
            rtype_str = parts[0].strip().upper()
            scope = parts[1].strip()
        else:
            rtype_str = raw_s.upper()
            scope = "global"

        # Resolve enum matching
        for member in ResourceType:
            if member.value == rtype_str:
                return cls(resource_type=member, scope=scope)
        raise ValueError(f"Unknown resource type identifier: '{rtype_str}'")

    def __str__(self) -> str:
        return self.canonical_string


class ResourceRequest(BaseModel):
    """Individual resource requirement within an acquisition request."""
    resource_type: ResourceType
    scope: str = Field(default="global")
    lock_mode: LockMode = Field(default=LockMode.EXCLUSIVE)

    @property
    def key(self) -> ResourceKey:
        return ResourceKey(resource_type=self.resource_type, scope=self.scope)

    @property
    def canonical_string(self) -> str:
        return self.key.canonical_string

    @property
    def canonical_key(self) -> str:
        return self.canonical_string

    @model_validator(mode="after")
    def validate_mutex_modes(self) -> ResourceRequest:
        # COMPUTER_INTERACTION and CLIPBOARD are strictly exclusive mutexes
        if self.resource_type in (ResourceType.COMPUTER_INTERACTION, ResourceType.CLIPBOARD, ResourceType.FOREGROUND_WINDOW):
            self.lock_mode = LockMode.EXCLUSIVE
        # Enforce 'global' scope for COMPUTER_INTERACTION and CLIPBOARD
        if self.resource_type in (ResourceType.COMPUTER_INTERACTION, ResourceType.CLIPBOARD):
            self.scope = "global"
        return self


class ResourceOwner(BaseModel):
    """Metadata tracking ownership of an occupied resource."""
    task_id: str
    lease_id: str
    acquired_at: float = Field(default_factory=time.time)
    acquired_at_iso: str = Field(default_factory=_utc_now_iso)
    lock_mode: LockMode = LockMode.EXCLUSIVE


class ResourceLease(BaseModel):
    """Authoritative lease granting a task access to designated resources."""
    lease_id: str = Field(default_factory=lambda: f"lease-{uuid.uuid4().hex[:8]}")
    task_id: str
    resources: List[ResourceRequest] = Field(default_factory=list)
    acquired_at: float = Field(default_factory=time.time)
    acquired_at_iso: str = Field(default_factory=_utc_now_iso)
    state: LeaseState = Field(default=LeaseState.ACTIVE)
    ttl_seconds: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def scrub_lease_metadata(self) -> ResourceLease:
        self.metadata = _scrub_secrets_recursive(self.metadata)
        return self

    @property
    def is_active(self) -> bool:
        return self.state == LeaseState.ACTIVE

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        if self.state != LeaseState.ACTIVE or self.ttl_seconds is None:
            return False
        now = current_time if current_time is not None else time.time()
        return (now - self.acquired_at) > self.ttl_seconds


class ResourceConflict(BaseModel):
    """Structured conflict information when resource acquisition is rejected or blocked."""
    resource: str
    requested_by: str
    owned_by: str
    reason: str = "RESOURCE_BUSY"


class ResourceDecision(BaseModel):
    """Structured response for scheduler cooperation."""
    allowed: bool
    blocked: bool
    conflicts: List[ResourceConflict] = Field(default_factory=list)
    reason: str = "AVAILABLE"


class ResourceSnapshotItem(BaseModel):
    """Snapshot entry for an individual resource."""
    resource_key: str
    resource_type: ResourceType
    state: ResourceState
    exclusive_owner: Optional[str] = None
    shared_owners: List[str] = Field(default_factory=list)
    capacity: int
    used_capacity: int

    @property
    def exclusive_holder(self) -> Optional[str]:
        return self.exclusive_owner

    @property
    def is_locked(self) -> bool:
        return self.state == ResourceState.LOCKED


class ResourceSnapshot(BaseModel):
    """Comprehensive read-only state snapshot of the resource plane."""
    timestamp: str = Field(default_factory=_utc_now_iso)
    active_leases_count: int = 0
    occupied_resources_count: int = 0
    available_resources_count: int = 0
    resources: Dict[str, ResourceSnapshotItem] = Field(default_factory=dict)
    waiting_tasks_count: int = 0
    bounded_metrics: Dict[str, Any] = Field(default_factory=dict)

    @property
    def active_lease_count(self) -> int:
        return self.active_leases_count

    @property
    def total_held_resources(self) -> int:
        return self.occupied_resources_count

    @property
    def allocations(self) -> Dict[str, ResourceSnapshotItem]:
        return self.resources

    @model_validator(mode="after")
    def scrub_snapshot_metrics(self) -> ResourceSnapshot:
        self.bounded_metrics = _scrub_secrets_recursive(self.bounded_metrics)
        return self


# Backward and architectural compatibility aliases
ResourceDescriptor = ResourceRequest
ResourceAllocationStatus = ResourceSnapshotItem
ResourceManagerStatus = ResourceSnapshot


class ResourceAcquisitionTimeoutError(TimeoutError):
    """Structured exception raised when bounded acquisition wait expires."""
    def __init__(
        self,
        task_id: str,
        requested_resources: List[str],
        conflicts: List[ResourceConflict],
        elapsed_seconds: float,
    ) -> None:
        self.task_id = task_id
        self.requested_resources = requested_resources
        self.conflicts = conflicts
        self.elapsed_seconds = elapsed_seconds
        msg = (
            f"Resource acquisition timed out after {elapsed_seconds:.1f}s for task '{task_id}'. "
            f"Requested: {requested_resources}. Conflicts: {[c.model_dump() for c in conflicts]}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# 3. Authoritative TaskResourceManager (Phase 2)
# ---------------------------------------------------------------------------

class TaskResourceManager:
    """Authoritative Resource Manager coordinating physical and logical resources.

    RESPONSIBILITIES:
    1. determine whether a task can acquire required resources
    2. track which resources are currently occupied
    3. track which task owns each resource
    4. detect resource conflicts
    5. atomic acquisition of multiple resources (all-or-nothing)
    6. safe release of resources (task-scoped, wrong-owner rejected)
    7. bounded waiting for unavailable resources
    8. cleanup of stale ownership
    9. deterministic resource ordering (deadlock elimination)
    10. safe cooperation with MultiTaskScheduler
    """

    def __init__(
        self,
        event_bus: Optional[ActionEventBus] = None,
        capacities: Optional[Dict[ResourceType, int]] = None,
        default_timeout_seconds: float = DEFAULT_MAX_ACQUISITION_WAIT,
        default_ttl_seconds: Optional[float] = 300.0,
        bus: Optional[ActionEventBus] = None,
    ) -> None:
        self.event_bus = event_bus or bus or action_bus
        self.capacities: Dict[ResourceType, int] = dict(DEFAULT_RESOURCE_CAPACITIES)
        if capacities:
            self.capacities.update(capacities)
        self.default_timeout_seconds = max(0.0, default_timeout_seconds)
        self.default_ttl_seconds = default_ttl_seconds

        # Thread synchronization
        self._lock = threading.RLock()

        # State storage: canonical_key -> ownership
        # Exclusive: canonical_key -> ResourceOwner
        self._exclusive_owners: Dict[str, ResourceOwner] = {}
        # Shared: canonical_key -> Dict[task_id, ResourceOwner]
        self._shared_owners: Dict[str, Dict[str, ResourceOwner]] = {}

        # Leases: lease_id -> ResourceLease
        self._leases: Dict[str, ResourceLease] = {}
        # Task mapping: task_id -> Set of lease_ids
        self._task_leases: Dict[str, Set[str]] = {}

        self._waiting_tasks_count = 0

        # Bounded metrics (strictly counters)
        self._metrics: Dict[str, int] = {
            "total_acquisition_attempts": 0,
            "successful_acquisitions": 0,
            "failed_acquisitions": 0,
            "timeout_count": 0,
            "release_count": 0,
            "conflict_count": 0,
            "wrong_owner_release_rejections": 0,
            "reentrant_acquisitions": 0,
            "atomic_rollbacks": 0,
        }

    # -----------------------------------------------------------------------
    # Resource Normalization & Canonical Ordering
    # -----------------------------------------------------------------------

    def normalize_requests(
        self,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
    ) -> List[ResourceRequest]:
        """Convert, validate, deduplicate, and deterministically sort resource requests."""
        if not isinstance(resources, list):
            resources = [resources]

        parsed: List[ResourceRequest] = []
        for r in resources:
            if isinstance(r, ResourceRequest):
                parsed.append(r)
            elif isinstance(r, ResourceKey):
                parsed.append(ResourceRequest(resource_type=r.resource_type, scope=r.scope))
            elif isinstance(r, ResourceType):
                parsed.append(ResourceRequest(resource_type=r, scope="global"))
            elif isinstance(r, str):
                rk = ResourceKey.from_string(r)
                parsed.append(ResourceRequest(resource_type=rk.resource_type, scope=rk.scope))
            else:
                raise ValueError(f"Invalid resource request type: '{type(r).__name__}'")

        # Deduplicate by canonical key string, preferring EXCLUSIVE if mixed
        dedup_map: Dict[str, ResourceRequest] = {}
        for req in parsed:
            key_str = req.canonical_string
            if key_str in dedup_map:
                if req.lock_mode == LockMode.EXCLUSIVE:
                    dedup_map[key_str] = req
            else:
                dedup_map[key_str] = req

        # Sort deterministically based on canonical order hierarchy, then scope string
        def _sort_key(req: ResourceRequest) -> Tuple[int, str]:
            try:
                type_idx = CANONICAL_RESOURCE_ORDER.index(req.resource_type)
            except ValueError:
                type_idx = len(CANONICAL_RESOURCE_ORDER)
            return (type_idx, req.canonical_string)

        return sorted(dedup_map.values(), key=_sort_key)

    # -----------------------------------------------------------------------
    # Availability, Conflict Detection & Scheduler Cooperation
    # -----------------------------------------------------------------------

    def get_owner(self, resource_key: Union[ResourceKey, str]) -> Optional[str]:
        """Inspect the current owner task ID of a resource key (read-only)."""
        key_str = resource_key.canonical_string if isinstance(resource_key, ResourceKey) else str(resource_key)
        with self._lock:
            if key_str in self._exclusive_owners:
                return self._exclusive_owners[key_str].task_id
            shared = self._shared_owners.get(key_str, {})
            if shared:
                return next(iter(shared.keys()))
            return None

    def get_resource_state(self, resource_key: Union[ResourceKey, str]) -> ResourceState:
        """Inspect the current state of a resource (read-only)."""
        key_str = resource_key.canonical_string if isinstance(resource_key, ResourceKey) else str(resource_key)
        with self._lock:
            if key_str in self._exclusive_owners:
                return ResourceState.LOCKED
            if self._shared_owners.get(key_str):
                return ResourceState.OCCUPIED
            return ResourceState.AVAILABLE

    def is_available(self, resource: Union[ResourceKey, ResourceRequest, ResourceType, str]) -> bool:
        """Check if resource is currently unallocated and available (read-only)."""
        if isinstance(resource, ResourceType):
            key_str = f"{resource.value}/global"
        elif hasattr(resource, "canonical_string"):
            key_str = resource.canonical_string
        elif isinstance(resource, str) and "/" not in resource:
            key_str = f"{resource.strip().upper()}/global"
        else:
            key_str = str(resource)
        return self.get_resource_state(key_str) == ResourceState.AVAILABLE

    def get_conflicts(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
    ) -> List[ResourceConflict]:
        """Detect and return any resource conflicts for task_id (read-only, zero mutation)."""
        with self._lock:
            self._cleanup_expired_leases_internal()
            normalized = self.normalize_requests(resources)
            conflicts: List[ResourceConflict] = []

            for req in normalized:
                key_str = req.canonical_string
                capacity = self.capacities.get(req.resource_type, 1)

                # Reentrancy check: task_id already holds this resource
                if key_str in self._exclusive_owners and self._exclusive_owners[key_str].task_id == task_id:
                    continue
                if task_id in self._shared_owners.get(key_str, {}):
                    if req.lock_mode == LockMode.SHARED:
                        continue

                # 1. Exclusive owner conflict
                if key_str in self._exclusive_owners:
                    owner_id = self._exclusive_owners[key_str].task_id
                    conflicts.append(
                        ResourceConflict(
                            resource=key_str,
                            requested_by=task_id,
                            owned_by=owner_id,
                            reason="RESOURCE_BUSY",
                        )
                    )
                    continue

                # 2. Shared holders conflict when exclusive requested
                shared_map = self._shared_owners.get(key_str, {})
                if req.lock_mode == LockMode.EXCLUSIVE and shared_map:
                    other_owners = [tid for tid in shared_map if tid != task_id]
                    if other_owners:
                        conflicts.append(
                            ResourceConflict(
                                resource=key_str,
                                requested_by=task_id,
                                owned_by=other_owners[0],
                                reason="SHARED_LOCK_HELD",
                            )
                        )
                        continue

                # 3. Capacity pool exhaustion for shared locks
                if req.lock_mode == LockMode.SHARED and len(shared_map) >= capacity:
                    if task_id not in shared_map:
                        conflicts.append(
                            ResourceConflict(
                                resource=key_str,
                                requested_by=task_id,
                                owned_by=next(iter(shared_map.keys()), "pool"),
                                reason="CAPACITY_EXHAUSTED",
                            )
                        )

            return conflicts

    def can_acquire(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
    ) -> bool:
        """Check if all requested resources are available for task_id (read-only inspection)."""
        return len(self.get_conflicts(task_id, resources)) == 0

    def can_acquire_resources(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
    ) -> ResourceDecision:
        """Structured scheduler cooperation response."""
        conflicts = self.get_conflicts(task_id, resources)
        if not conflicts:
            return ResourceDecision(
                allowed=True,
                blocked=False,
                conflicts=[],
                reason="AVAILABLE",
            )
        return ResourceDecision(
            allowed=False,
            blocked=True,
            conflicts=conflicts,
            reason=f"BLOCKED: {conflicts[0].reason} on {conflicts[0].resource}",
        )

    # -----------------------------------------------------------------------
    # Atomic Multi-Resource Acquisition (All-or-Nothing)
    # -----------------------------------------------------------------------

    def acquire_resources(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
        timeout_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResourceLease:
        """Synchronously acquire requested resources with bounded wait.
        
        Guarantees all-or-nothing atomicity and deterministic canonical ordering.
        Raises ResourceAcquisitionTimeoutError on expiration.
        """
        if not task_id or not str(task_id).strip():
            raise ValueError("Task ID cannot be empty for resource acquisition.")

        normalized = self.normalize_requests(resources)
        if not normalized:
            return ResourceLease(task_id=task_id, state=LeaseState.ACTIVE)

        timeout = self.default_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        start_time = time.time()

        with self._lock:
            self._metrics["total_acquisition_attempts"] += 1
            self._waiting_tasks_count += 1

        try:
            while True:
                with self._lock:
                    self._cleanup_expired_leases_internal()
                    conflicts = self.get_conflicts(task_id, normalized)

                    if not conflicts:
                        lease = self._commit_atomic_acquisition(task_id, normalized, ttl, metadata)
                        self._metrics["successful_acquisitions"] += 1
                        return lease

                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    with self._lock:
                        self._metrics["timeout_count"] += 1
                        self._metrics["failed_acquisitions"] += 1
                        self._metrics["conflict_count"] += len(conflicts)
                        self._metrics["atomic_rollbacks"] += 1

                    keys = [r.canonical_string for r in normalized]
                    self._emit_telemetry(
                        ActionType.RESOURCE_TIMEOUT,
                        ActionStatus.FAILED,
                        f"Resource acquisition timed out for task {task_id}",
                        task_id,
                        {"requested": keys, "conflicts": [c.model_dump() for c in conflicts]},
                    )
                    raise ResourceAcquisitionTimeoutError(
                        task_id=task_id,
                        requested_resources=keys,
                        conflicts=conflicts,
                        elapsed_seconds=elapsed,
                    )

                time.sleep(min(0.05, max(0.005, (timeout - elapsed) / 10)))

        finally:
            with self._lock:
                self._waiting_tasks_count = max(0, self._waiting_tasks_count - 1)

    async def acquire_resources_async(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
        timeout_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResourceLease:
        """Asynchronously acquire requested resources with bounded wait."""
        if not task_id or not str(task_id).strip():
            raise ValueError("Task ID cannot be empty for resource acquisition.")

        normalized = self.normalize_requests(resources)
        if not normalized:
            return ResourceLease(task_id=task_id, state=LeaseState.ACTIVE)

        timeout = self.default_timeout_seconds if timeout_seconds is None else max(0.0, timeout_seconds)
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        start_time = time.time()

        with self._lock:
            self._metrics["total_acquisition_attempts"] += 1
            self._waiting_tasks_count += 1

        try:
            while True:
                with self._lock:
                    self._cleanup_expired_leases_internal()
                    conflicts = self.get_conflicts(task_id, normalized)

                    if not conflicts:
                        lease = self._commit_atomic_acquisition(task_id, normalized, ttl, metadata)
                        self._metrics["successful_acquisitions"] += 1
                        return lease

                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    with self._lock:
                        self._metrics["timeout_count"] += 1
                        self._metrics["failed_acquisitions"] += 1
                        self._metrics["conflict_count"] += len(conflicts)
                        self._metrics["atomic_rollbacks"] += 1

                    keys = [r.canonical_string for r in normalized]
                    self._emit_telemetry(
                        ActionType.RESOURCE_TIMEOUT,
                        ActionStatus.FAILED,
                        f"Resource acquisition timed out for task {task_id}",
                        task_id,
                        {"requested": keys, "conflicts": [c.model_dump() for c in conflicts]},
                    )
                    raise ResourceAcquisitionTimeoutError(
                        task_id=task_id,
                        requested_resources=keys,
                        conflicts=conflicts,
                        elapsed_seconds=elapsed,
                    )

                if elapsed > 0.05:
                    self._emit_telemetry(
                        ActionType.RESOURCE_BLOCKED,
                        ActionStatus.PROGRESS,
                        f"Task {task_id} waiting on resources",
                        task_id,
                        {"conflicts": [c.model_dump() for c in conflicts]},
                    )

                await asyncio.sleep(min(0.05, max(0.01, (timeout - elapsed) / 10)))

        finally:
            with self._lock:
                self._waiting_tasks_count = max(0, self._waiting_tasks_count - 1)

    def _commit_atomic_acquisition(
        self,
        task_id: str,
        resources: List[ResourceRequest],
        ttl_seconds: Optional[float],
        metadata: Optional[Dict[str, Any]],
    ) -> ResourceLease:
        """Atomically commit all resources in deterministic order under lock."""
        lease = ResourceLease(
            task_id=task_id,
            resources=resources,
            ttl_seconds=ttl_seconds,
            state=LeaseState.ACTIVE,
            metadata=metadata or {},
        )

        for req in resources:
            key_str = req.canonical_string
            owner_entry = ResourceOwner(
                task_id=task_id,
                lease_id=lease.lease_id,
                lock_mode=req.lock_mode,
            )

            if req.lock_mode == LockMode.EXCLUSIVE:
                if key_str in self._exclusive_owners and self._exclusive_owners[key_str].task_id == task_id:
                    self._metrics["reentrant_acquisitions"] += 1
                self._exclusive_owners[key_str] = owner_entry
            else:
                if key_str not in self._shared_owners:
                    self._shared_owners[key_str] = {}
                if task_id in self._shared_owners[key_str]:
                    self._metrics["reentrant_acquisitions"] += 1
                self._shared_owners[key_str][task_id] = owner_entry

        self._leases[lease.lease_id] = lease
        if task_id not in self._task_leases:
            self._task_leases[task_id] = set()
        self._task_leases[task_id].add(lease.lease_id)

        keys = [r.canonical_string for r in resources]
        self._emit_telemetry(
            ActionType.RESOURCE_ACQUIRED,
            ActionStatus.COMPLETED,
            f"Task {task_id} acquired lease {lease.lease_id} on {keys}",
            task_id,
            {"lease_id": lease.lease_id, "resources": keys},
        )
        logger.info(f"[RESOURCE] Task '{task_id}' acquired {keys} under lease '{lease.lease_id}'.")
        return lease

    # -----------------------------------------------------------------------
    # Task-Scoped Release Operations
    # -----------------------------------------------------------------------

    def release_resource(self, resource: Union[ResourceKey, ResourceRequest, str], owner_task_id: str) -> bool:
        """Release a single resource lock. Rejects wrong-owner release safely."""
        key_str = resource.canonical_string if hasattr(resource, "canonical_string") else str(resource)

        with self._lock:
            # 1. Exclusive check
            if key_str in self._exclusive_owners:
                owner = self._exclusive_owners[key_str]
                if owner.task_id != owner_task_id:
                    self._metrics["wrong_owner_release_rejections"] += 1
                    logger.warning(
                        f"[RESOURCE] Rejected wrong-owner release of '{key_str}' by '{owner_task_id}' "
                        f"(actual owner: '{owner.task_id}')."
                    )
                    return False
                del self._exclusive_owners[key_str]
                self._metrics["release_count"] += 1
                self._emit_telemetry(
                    ActionType.RESOURCE_RELEASED,
                    ActionStatus.COMPLETED,
                    f"Released resource {key_str}",
                    owner_task_id,
                    {"resource": key_str},
                )
                return True

            # 2. Shared check
            if key_str in self._shared_owners:
                shared_map = self._shared_owners[key_str]
                if owner_task_id not in shared_map:
                    self._metrics["wrong_owner_release_rejections"] += 1
                    logger.warning(
                        f"[RESOURCE] Rejected wrong-owner shared release of '{key_str}' by '{owner_task_id}'."
                    )
                    return False
                del shared_map[owner_task_id]
                if not shared_map:
                    del self._shared_owners[key_str]
                self._metrics["release_count"] += 1
                self._emit_telemetry(
                    ActionType.RESOURCE_RELEASED,
                    ActionStatus.COMPLETED,
                    f"Released shared resource {key_str}",
                    owner_task_id,
                    {"resource": key_str},
                )
                return True

            # Resource is not currently held: idempotent safe return
            return True

    def release_resources(
        self,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
        owner_task_id: str,
    ) -> bool:
        """Release a list of resources. Rejects if any resource is owned by a different task."""
        normalized = self.normalize_requests(resources)
        with self._lock:
            for req in normalized:
                key_str = req.canonical_string
                if key_str in self._exclusive_owners and self._exclusive_owners[key_str].task_id != owner_task_id:
                    self._metrics["wrong_owner_release_rejections"] += 1
                    return False
                if key_str in self._shared_owners and owner_task_id not in self._shared_owners[key_str]:
                    self._metrics["wrong_owner_release_rejections"] += 1
                    return False

            success = True
            for req in normalized:
                if not self.release_resource(req.canonical_string, owner_task_id):
                    success = False
            return success

    def release_lease(self, lease_or_id: Union[ResourceLease, str], owner_task_id: Optional[str] = None) -> bool:
        """Release an entire lease by lease ID."""
        lease_id = lease_or_id.lease_id if isinstance(lease_or_id, ResourceLease) else str(lease_or_id)

        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                return False

            if owner_task_id and lease.task_id != owner_task_id:
                self._metrics["wrong_owner_release_rejections"] += 1
                logger.warning(f"[RESOURCE] Wrong-owner release for lease '{lease_id}' by '{owner_task_id}'.")
                return False

            task_id = lease.task_id
            lease.state = LeaseState.RELEASED
            self._leases.pop(lease_id, None)

            if task_id in self._task_leases:
                self._task_leases[task_id].discard(lease_id)

            # Check if any other active lease for this task still claims each resource
            still_claimed_keys: Set[str] = set()
            for other_lid in self._task_leases.get(task_id, set()):
                other_lease = self._leases.get(other_lid)
                if other_lease and other_lease.is_active:
                    for req in other_lease.resources:
                        still_claimed_keys.add(req.canonical_string)

            # Only release resources that are not claimed by any other active lease of this task
            for req in lease.resources:
                k_str = req.canonical_string
                if k_str not in still_claimed_keys:
                    self.release_resource(k_str, task_id)

            return True

    def release_all_for_task(self, task_id: str) -> int:
        """Release ALL resources and active leases held by a specific task.
        
        Guaranteed idempotent, exception-safe cleanup for cancelled or finished tasks.
        """
        with self._lock:
            # Pre-calculate held resources so we accurately report count of cleared allocations
            held_resources = set(self.list_resources_for_task(task_id))
            lease_ids = list(self._task_leases.get(task_id, set()))

            # 1. Clear leases
            for lid in lease_ids:
                self.release_lease(lid, owner_task_id=task_id)
            self._task_leases.pop(task_id, None)

            # 2. Sweep exclusive locks
            for k in list(self._exclusive_owners.keys()):
                if self._exclusive_owners[k].task_id == task_id:
                    del self._exclusive_owners[k]
                    self._metrics["release_count"] += 1

            # 3. Sweep shared locks
            for k in list(self._shared_owners.keys()):
                if task_id in self._shared_owners[k]:
                    del self._shared_owners[k][task_id]
                    self._metrics["release_count"] += 1
                    if not self._shared_owners[k]:
                        del self._shared_owners[k]

            total_cleared = max(len(held_resources), len(lease_ids))
            logger.info(f"[RESOURCE] release_all_for_task('{task_id}') cleared {total_cleared} resource allocations.")
            return total_cleared

    def clear_all(self) -> None:
        """Clear all active leases and resource locks across all tasks (used in crash recovery)."""
        with self._lock:
            self._exclusive_owners.clear()
            self._shared_owners.clear()
            self._leases.clear()
            self._task_leases.clear()
            self._waiting_tasks_count = 0
            logger.info("[RESOURCE] clear_all() released all resource holdings.")

    # -----------------------------------------------------------------------
    # Stale Lease Cleanup
    # -----------------------------------------------------------------------

    def cleanup_stale_leases(self) -> int:
        """Scan and reclaim expired leases across all tasks."""
        with self._lock:
            return self._cleanup_expired_leases_internal()

    def _cleanup_expired_leases_internal(self) -> int:
        """Internal cleanup under lock."""
        now = time.time()
        expired_ids: List[str] = []

        for lid, lease in self._leases.items():
            if lease.is_expired(now):
                expired_ids.append(lid)

        for lid in expired_ids:
            lease = self._leases[lid]
            logger.warning(f"[RESOURCE] Lease '{lid}' for task '{lease.task_id}' expired TTL. Reclaiming.")
            self.release_lease(lid, owner_task_id=lease.task_id)
            lease.state = LeaseState.EXPIRED

        return len(expired_ids)

    # -----------------------------------------------------------------------
    # Inspection & Snapshot APIs (Read-Only)
    # -----------------------------------------------------------------------

    def list_resources(self) -> List[str]:
        """List all currently occupied resource canonical keys (read-only)."""
        with self._lock:
            keys: Set[str] = set(self._exclusive_owners.keys())
            keys.update(self._shared_owners.keys())
            return sorted(list(keys))

    def list_resources_for_task(self, task_id: str) -> List[str]:
        """List all resource keys currently held by a specific task (read-only)."""
        with self._lock:
            held: Set[str] = set()
            for k, owner in self._exclusive_owners.items():
                if owner.task_id == task_id:
                    held.add(k)
            for k, shared_map in self._shared_owners.items():
                if task_id in shared_map:
                    held.add(k)
            return sorted(list(held))

    def snapshot(self) -> ResourceSnapshot:
        """Produce an immutable, read-only snapshot of the resource plane with zero secrets."""
        with self._lock:
            self._cleanup_expired_leases_internal()

            items: Dict[str, ResourceSnapshotItem] = {}

            # All active exclusive entries
            for k, owner in self._exclusive_owners.items():
                rk = ResourceKey.from_string(k)
                cap = self.capacities.get(rk.resource_type, 1)
                items[k] = ResourceSnapshotItem(
                    resource_key=k,
                    resource_type=rk.resource_type,
                    state=ResourceState.LOCKED,
                    exclusive_owner=owner.task_id,
                    shared_owners=[],
                    capacity=cap,
                    used_capacity=1,
                )

            # All active shared entries
            for k, shared_map in self._shared_owners.items():
                rk = ResourceKey.from_string(k)
                cap = self.capacities.get(rk.resource_type, 1)
                owners_list = list(shared_map.keys())
                used = len(owners_list)
                items[k] = ResourceSnapshotItem(
                    resource_key=k,
                    resource_type=rk.resource_type,
                    state=ResourceState.OCCUPIED if used < cap else ResourceState.LOCKED,
                    exclusive_owner=None,
                    shared_owners=owners_list,
                    capacity=cap,
                    used_capacity=used,
                )

            occupied_count = len(items)
            active_leases_count = len([l for l in self._leases.values() if l.is_active])

            return ResourceSnapshot(
                active_leases_count=active_leases_count,
                occupied_resources_count=occupied_count,
                available_resources_count=max(0, len(self.capacities) - occupied_count),
                resources=items,
                waiting_tasks_count=self._waiting_tasks_count,
                bounded_metrics=dict(self._metrics),
            )

    # -----------------------------------------------------------------------
    # Requirement Inference Helper
    # -----------------------------------------------------------------------

    def infer_required_resources(self, task_or_step: Any) -> List[ResourceRequest]:
        """Automatically infer resource requirements from task or step intent."""
        meta = getattr(task_or_step, "metadata", None) or getattr(task_or_step, "safe_metadata", None)
        if not meta and isinstance(getattr(task_or_step, "arguments", None), dict):
            meta = getattr(task_or_step, "arguments", {}).get("safe_metadata") or getattr(task_or_step, "arguments", {})

        if isinstance(meta, dict) and "required_resources" in meta:
            return self.normalize_requests(meta["required_resources"])

        cap = str(getattr(task_or_step, "capability", "")).lower()
        action = str(getattr(task_or_step, "action", "")).lower()
        goal = str(getattr(task_or_step, "goal", "")).lower()
        app_ctx = getattr(task_or_step, "application_context", None) or "default"

        reqs: List[ResourceRequest] = []

        if "desktop" in cap or "click" in action or "mouse" in action or "screen" in action:
            reqs.append(ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION, scope="global"))
            reqs.append(ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope=f"session:{app_ctx}"))
            reqs.append(ResourceRequest(resource_type=ResourceType.FOREGROUND_WINDOW, scope=f"window:{app_ctx}"))

        if "browser" in cap or "web" in cap or "navigate" in action:
            reqs.append(ResourceRequest(resource_type=ResourceType.BROWSER_SESSION, scope="browser:default"))
            reqs.append(ResourceRequest(resource_type=ResourceType.BROWSER_TAB, scope="browser-tab:active"))
            if "click" in action or "type" in action:
                reqs.append(ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION, scope="global"))

        if "clipboard" in action or "copy" in action or "paste" in action:
            reqs.append(ResourceRequest(resource_type=ResourceType.CLIPBOARD, scope="global"))

        if "build" in action or "git" in action or "project" in goal:
            reqs.append(ResourceRequest(resource_type=ResourceType.PROJECT, scope="project:main", lock_mode=LockMode.EXCLUSIVE))

        if "file" in action or "workspace" in goal:
            reqs.append(ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="workspace:main", lock_mode=LockMode.SHARED))

        if not reqs:
            reqs.append(ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION, scope="global"))

        return self.normalize_requests(reqs)

    # -----------------------------------------------------------------------
    # Convenience Methods & Aliases
    # -----------------------------------------------------------------------

    def release_all(self, task_id: str) -> int:
        """Alias for release_all_for_task."""
        return self.release_all_for_task(task_id)

    def acquire_sync(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
        timeout_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResourceLease:
        """Convenience alias for acquire_resources."""
        return self.acquire_resources(task_id, resources, timeout_seconds=timeout_seconds, ttl_seconds=ttl_seconds, metadata=metadata)

    async def acquire(
        self,
        task_id: str,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
        timeout_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ResourceLease:
        """Convenience alias for acquire_resources_async."""
        return await self.acquire_resources_async(task_id, resources, timeout_seconds=timeout_seconds, ttl_seconds=ttl_seconds, metadata=metadata)

    def release(self, lease_or_resource: Any, owner_task_id: Optional[str] = None) -> bool:
        """Convenience polymorphic release method accepting ResourceLease, lease_id, or resource key."""
        if isinstance(lease_or_resource, ResourceLease):
            return self.release_lease(lease_or_resource, owner_task_id=owner_task_id)
        if isinstance(lease_or_resource, str) and lease_or_resource in self._leases:
            return self.release_lease(lease_or_resource, owner_task_id=owner_task_id)
        if owner_task_id:
            return self.release_resource(lease_or_resource, owner_task_id=owner_task_id)
        return self.release_lease(lease_or_resource, owner_task_id=owner_task_id)

    def normalize_resources(
        self,
        resources: Union[List[Union[ResourceType, ResourceRequest, ResourceKey, str]], ResourceType, ResourceRequest, ResourceKey, str],
    ) -> List[ResourceRequest]:
        """Alias for normalize_requests."""
        return self.normalize_requests(resources)

    def get_status(self) -> ResourceSnapshot:
        """Alias for snapshot."""
        return self.snapshot()

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
            logger.debug(f"[RESOURCE] Telemetry emission failed: {exc}")


# Type Aliases for backward and planning compatibility
ResourceDescriptor = ResourceRequest
ResourceAllocationStatus = ResourceState
ResourceManagerStatus = ResourceSnapshot

# Alias
ResourceManager = TaskResourceManager

# Singleton instance
resource_manager = TaskResourceManager()
task_resource_manager = resource_manager

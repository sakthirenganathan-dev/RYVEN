"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 2: Authoritative TaskResourceManager Verification Suite.

Complete Coverage Matrix (Tests 1 - 55):
1.  Initialization (Test 1)
2.  Resource Registration (Test 2)
3.  Resource Identity (Test 3)
4.  Resource Availability (Test 4)
5.  Single-Resource Acquisition (Test 5)
6.  Single-Resource Release (Test 6)
7.  Multiple-Resource Acquisition (Test 7)
8.  Atomic Acquisition Success (Test 8)
9.  Atomic Acquisition Rollback (Test 9)
10. COMPUTER_INTERACTION Mutex (Test 10)
11. Wrong-Owner Release (Test 11)
12. Duplicate / Reentrant Acquisition (Test 12)
13. Idempotent Release (Test 13)
14. Task-Wide Release (Test 14)
15. Conflict Detection (Test 15)
16. Timeout Behavior (Test 16)
17. Deterministic Resource Ordering (Test 17)
18. Concurrent Acquisition (Test 18)
19. Concurrent Mutex Protection (Test 19)
20. Snapshot Inspection (Test 20)
21. Task Resource Listing (Test 21)
22. Bounded Metrics (Test 22)
23. Stale Cleanup / TTL Expiration (Test 23)
24. Invalid Resource Rejection (Test 24)
25. Empty Resource Request (Test 25)
26. Duplicate Resource Request (Test 26)
27. Secret Metadata Sanitization (Test 27)
28. Security Imports Audit (Test 28)
29. Scheduler Cooperation (Test 29)
30. Resource Advisor Cooperation (Test 30)
31. Exception Safety (Test 31)
32. Lease Lifecycle (Test 32)
33. Resource State Transitions (Test 33)
34. Unavailable Resource Reporting (Test 34)
35. Atomicity Under Contention (Test 35)
36. Release After Failed Acquisition (Test 36)
37. release_all_for_task (Test 37)
38. Multiple Task Isolation (Test 38)
39. No Direct Execution Imports (Test 39)
40. Coordination-Layer Architecture Test (Test 40)
41. CLIPBOARD Mutex (Test 41)
42. FOREGROUND_WINDOW Resource (Test 42)
43. BROWSER_TAB Capacity Pool (Test 43)
44. WORKSPACE Shared Concurrency vs Exclusive (Test 44)
45. PROJECT Scope Isolation (Test 45)
46. NETWORK_CHANNEL Capacity Pool (Test 46)
47. DESKTOP_SESSION Scoping (Test 47)
48. Async Acquisition & Release (Test 48)
49. Telemetry Event Emission (Test 49)
50. Requirement Inference (Test 50)
51. Scheduler Gating on Busy Resources (Test 51)
52. Scheduler Parallel Non-Conflicting Tasks (Test 52)
53. Scheduler Cancel Releases Resources (Test 53)
54. Reentrant Partial Lease Release (Test 54)
55. Singleton Instance Verification (Test 55)
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from typing import Any, Dict, List
import pytest

from app.actions.event_bus import ActionEventBus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
)
from app.control.resources import (
    CANONICAL_RESOURCE_ORDER,
    DEFAULT_RESOURCE_CAPACITIES,
    LeaseState,
    LockMode,
    ResourceAcquisitionTimeoutError,
    ResourceConflict,
    ResourceDecision,
    ResourceDescriptor,
    ResourceKey,
    ResourceLease,
    ResourceManager,
    ResourceOwner,
    ResourceRequest,
    ResourceSnapshot,
    ResourceState,
    ResourceType,
    TaskResourceManager,
    resource_manager,
)
from app.control.scheduler import (
    MultiTaskScheduler,
    ScheduledTask,
    TaskPriority,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_event_bus() -> ActionEventBus:
    """Isolated ActionEventBus."""
    return ActionEventBus(max_events=500)


@pytest.fixture
def rm(test_event_bus: ActionEventBus) -> TaskResourceManager:
    """Isolated TaskResourceManager."""
    return TaskResourceManager(
        event_bus=test_event_bus,
        default_timeout_seconds=0.2,
        default_ttl_seconds=10.0,
    )


# ---------------------------------------------------------------------------
# Core Tests 1 - 40
# ---------------------------------------------------------------------------

def test_01_initialization(rm: TaskResourceManager):
    """1. Initialization: Verify initial state, default timeouts, and zero held locks."""
    assert rm.default_timeout_seconds == 0.2
    assert rm.default_ttl_seconds == 10.0
    assert rm.list_resources() == []
    snapshot = rm.snapshot()
    assert snapshot.occupied_resources_count == 0
    assert snapshot.active_leases_count == 0


def test_02_resource_registration(rm: TaskResourceManager):
    """2. Resource Registration: All 9 logical resource types are registered with default capacities."""
    expected_types = {
        ResourceType.COMPUTER_INTERACTION,
        ResourceType.DESKTOP_SESSION,
        ResourceType.FOREGROUND_WINDOW,
        ResourceType.BROWSER_SESSION,
        ResourceType.BROWSER_TAB,
        ResourceType.WORKSPACE,
        ResourceType.PROJECT,
        ResourceType.NETWORK_CHANNEL,
        ResourceType.CLIPBOARD,
    }
    assert len(expected_types) == 9
    assert set(rm.capacities.keys()) == expected_types
    assert rm.capacities[ResourceType.COMPUTER_INTERACTION] == 1
    assert rm.capacities[ResourceType.CLIPBOARD] == 1
    assert rm.capacities[ResourceType.FOREGROUND_WINDOW] == 1
    assert rm.capacities[ResourceType.BROWSER_TAB] == 8


def test_03_resource_identity():
    """3. Resource Identity: Key formatting, canonical strings, and parsing."""
    k1 = ResourceKey(resource_type=ResourceType.COMPUTER_INTERACTION, scope="global")
    assert k1.canonical_string == "COMPUTER_INTERACTION/global"
    assert k1.canonical_key == "COMPUTER_INTERACTION/global"

    k2 = ResourceKey.from_string("DESKTOP_SESSION/session:1")
    assert k2.resource_type == ResourceType.DESKTOP_SESSION
    assert k2.scope == "session:1"

    k3 = ResourceKey.from_string("workspace:workspace:repo")
    assert k3.resource_type == ResourceType.WORKSPACE
    assert k3.scope == "workspace:repo"

    k4 = ResourceKey.from_string("computer_interaction")
    assert k4.resource_type == ResourceType.COMPUTER_INTERACTION
    assert k4.scope == "global"


def test_04_resource_availability(rm: TaskResourceManager):
    """4. Resource Availability: can_acquire and get_resource_state for available resources."""
    assert rm.can_acquire("task_1", ResourceType.COMPUTER_INTERACTION) is True
    assert rm.get_resource_state("COMPUTER_INTERACTION/global") == ResourceState.AVAILABLE
    assert rm.get_owner("COMPUTER_INTERACTION/global") is None


def test_05_single_resource_acquisition(rm: TaskResourceManager):
    """5. Single-Resource Acquisition: Acquire single resource and inspect lease."""
    lease = rm.acquire_resources("task_1", ResourceType.COMPUTER_INTERACTION)
    assert lease.is_active is True
    assert lease.task_id == "task_1"
    assert len(lease.resources) == 1
    assert rm.get_owner("COMPUTER_INTERACTION/global") == "task_1"
    assert rm.get_resource_state("COMPUTER_INTERACTION/global") == ResourceState.LOCKED


def test_06_single_resource_release(rm: TaskResourceManager):
    """6. Single-Resource Release: Release resource by owner restores availability."""
    rm.acquire_resources("task_1", ResourceType.COMPUTER_INTERACTION)
    assert rm.can_acquire("task_2", ResourceType.COMPUTER_INTERACTION) is False

    success = rm.release_resource("COMPUTER_INTERACTION/global", owner_task_id="task_1")
    assert success is True
    assert rm.can_acquire("task_2", ResourceType.COMPUTER_INTERACTION) is True
    assert rm.get_owner("COMPUTER_INTERACTION/global") is None


def test_07_multiple_resource_acquisition(rm: TaskResourceManager):
    """7. Multiple-Resource Acquisition: Acquire multiple resources in a single call."""
    reqs = [
        ResourceType.DESKTOP_SESSION,
        ResourceType.FOREGROUND_WINDOW,
        ResourceType.WORKSPACE,
    ]
    lease = rm.acquire_resources("task_multi", reqs)
    assert lease.is_active is True
    assert len(lease.resources) == 3
    assert len(rm.list_resources_for_task("task_multi")) == 3


def test_08_atomic_acquisition_success(rm: TaskResourceManager):
    """8. Atomic Acquisition Success: All requested resources committed atomically."""
    reqs = [
        ResourceType.COMPUTER_INTERACTION,
        ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="workspace:main", lock_mode=LockMode.EXCLUSIVE),
    ]
    lease = rm.acquire_resources("task_atomic", reqs)
    assert lease.is_active is True
    assert rm.get_owner("COMPUTER_INTERACTION/global") == "task_atomic"
    assert rm.get_owner("WORKSPACE/workspace:main") == "task_atomic"


def test_09_atomic_acquisition_rollback(rm: TaskResourceManager):
    """9. Atomic Acquisition Rollback: If one resource is unavailable, none are acquired."""
    # Task A holds COMPUTER_INTERACTION
    rm.acquire_resources("task_a", ResourceType.COMPUTER_INTERACTION)

    # Task B requests [WORKSPACE, COMPUTER_INTERACTION]
    # Since COMPUTER_INTERACTION is held, Task B MUST NOT acquire WORKSPACE
    with pytest.raises(ResourceAcquisitionTimeoutError) as exc_info:
        rm.acquire_resources(
            "task_b",
            [
                ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="workspace:target"),
                ResourceType.COMPUTER_INTERACTION,
            ],
            timeout_seconds=0.05,
        )

    assert "COMPUTER_INTERACTION/global" in [c.resource for c in exc_info.value.conflicts]
    # WORKSPACE must remain completely free
    assert rm.can_acquire("task_c", ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="workspace:target")) is True
    assert rm.get_owner("WORKSPACE/workspace:target") is None


def test_10_computer_interaction_mutex(rm: TaskResourceManager):
    """10. COMPUTER_INTERACTION Mutex: Hard safety requirement - globally exclusive."""
    rm.acquire_resources("task_holder", ResourceType.COMPUTER_INTERACTION)

    assert rm.can_acquire("task_requester", ResourceType.COMPUTER_INTERACTION) is False
    with pytest.raises(ResourceAcquisitionTimeoutError):
        rm.acquire_resources("task_requester", ResourceType.COMPUTER_INTERACTION, timeout_seconds=0.05)


def test_11_wrong_owner_release(rm: TaskResourceManager):
    """11. Wrong-Owner Release: Non-owner cannot release another task's resource."""
    lease = rm.acquire_resources("task_owner", ResourceType.COMPUTER_INTERACTION)

    # Task rogue tries to release resource
    released = rm.release_resource("COMPUTER_INTERACTION/global", owner_task_id="task_rogue")
    assert released is False

    # Task rogue tries to release lease
    released_lease = rm.release_lease(lease.lease_id, owner_task_id="task_rogue")
    assert released_lease is False

    # Owner still holds it
    assert rm.get_owner("COMPUTER_INTERACTION/global") == "task_owner"
    assert rm._metrics["wrong_owner_release_rejections"] >= 2


def test_12_duplicate_acquisition(rm: TaskResourceManager):
    """12. Duplicate / Reentrant Acquisition: Same task re-acquiring succeeds safely."""
    l1 = rm.acquire_resources("task_reentrant", ResourceType.COMPUTER_INTERACTION)
    assert l1.is_active is True

    l2 = rm.acquire_resources("task_reentrant", ResourceType.COMPUTER_INTERACTION)
    assert l2.is_active is True
    assert rm._metrics["reentrant_acquisitions"] >= 1


def test_13_idempotent_release(rm: TaskResourceManager):
    """13. Idempotent Release: Releasing already released or nonexistent resource is safe."""
    assert rm.release_resource("NON_EXISTENT/global", owner_task_id="any_task") is True
    assert rm.release_lease("non-existent-lease", owner_task_id="any_task") is False


def test_14_task_wide_release(rm: TaskResourceManager):
    """14. Task-Wide Release: release_all_for_task frees all task resources."""
    rm.acquire_resources("task_all", [ResourceType.COMPUTER_INTERACTION, ResourceType.CLIPBOARD])
    assert len(rm.list_resources_for_task("task_all")) == 2

    cleared = rm.release_all_for_task("task_all")
    assert cleared >= 2
    assert len(rm.list_resources_for_task("task_all")) == 0
    assert rm.can_acquire("other_task", ResourceType.COMPUTER_INTERACTION) is True
    assert rm.can_acquire("other_task", ResourceType.CLIPBOARD) is True


def test_15_conflict_detection(rm: TaskResourceManager):
    """15. Conflict Detection: get_conflicts returns structured ResourceConflict."""
    rm.acquire_resources("task_owner", ResourceType.COMPUTER_INTERACTION)

    conflicts = rm.get_conflicts("task_waiter", ResourceType.COMPUTER_INTERACTION)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert isinstance(conflict, ResourceConflict)
    assert conflict.resource == "COMPUTER_INTERACTION/global"
    assert conflict.requested_by == "task_waiter"
    assert conflict.owned_by == "task_owner"
    assert conflict.reason == "RESOURCE_BUSY"


def test_16_timeout_behavior(rm: TaskResourceManager):
    """16. Timeout Behavior: Bounded wait expires and raises structured exception."""
    rm.acquire_resources("task_busy", ResourceType.CLIPBOARD)

    t0 = time.time()
    with pytest.raises(ResourceAcquisitionTimeoutError) as exc_info:
        rm.acquire_resources("task_hungry", ResourceType.CLIPBOARD, timeout_seconds=0.1)
    elapsed = time.time() - t0

    assert 0.08 <= elapsed <= 0.5
    assert exc_info.value.task_id == "task_hungry"
    assert "CLIPBOARD/global" in exc_info.value.requested_resources
    assert rm._metrics["timeout_count"] == 1


def test_17_deterministic_resource_ordering(rm: TaskResourceManager):
    """17. Deterministic Resource Ordering: Sorted canonical order prevents deadlocks."""
    # Pass resources scrambled
    scrambled = [
        ResourceType.CLIPBOARD,
        ResourceType.WORKSPACE,
        ResourceType.COMPUTER_INTERACTION,
        ResourceType.DESKTOP_SESSION,
    ]
    normalized = rm.normalize_requests(scrambled)
    types_in_order = [r.resource_type for r in normalized]

    assert types_in_order == [
        ResourceType.COMPUTER_INTERACTION,
        ResourceType.DESKTOP_SESSION,
        ResourceType.WORKSPACE,
        ResourceType.CLIPBOARD,
    ]


def test_18_concurrent_acquisition(rm: TaskResourceManager):
    """18. Concurrent Acquisition: Non-conflicting acquisitions run in parallel."""
    results = {}

    def worker(task_id: str, scope: str):
        lease = rm.acquire_resources(
            task_id,
            ResourceRequest(resource_type=ResourceType.WORKSPACE, scope=f"workspace:{scope}"),
            timeout_seconds=0.5,
        )
        results[task_id] = lease.is_active

    threads = [
        threading.Thread(target=worker, args=(f"task_{i}", f"scope_{i}"))
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    assert all(results.values())


def test_19_concurrent_mutex_protection(rm: TaskResourceManager):
    """19. Concurrent Mutex Protection: Exactly one thread acquires COMPUTER_INTERACTION."""
    winners = []
    failures = []

    def compete(task_id: str):
        try:
            lease = rm.acquire_resources(task_id, ResourceType.COMPUTER_INTERACTION, timeout_seconds=0.08)
            winners.append((task_id, lease))
        except ResourceAcquisitionTimeoutError:
            failures.append(task_id)

    threads = [threading.Thread(target=compete, args=(f"comp_{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one acquired the mutex
    assert len(winners) == 1
    assert len(failures) == 4


def test_20_snapshot_inspection(rm: TaskResourceManager):
    """20. Snapshot Inspection: snapshot() is read-only and returns comprehensive state."""
    rm.acquire_resources("task_snap", [ResourceType.COMPUTER_INTERACTION, ResourceType.FOREGROUND_WINDOW])

    snap = rm.snapshot()
    assert isinstance(snap, ResourceSnapshot)
    assert snap.occupied_resources_count == 2
    assert snap.active_leases_count == 1
    assert "COMPUTER_INTERACTION/global" in snap.resources
    assert "FOREGROUND_WINDOW/global" in snap.resources
    assert snap.resources["COMPUTER_INTERACTION/global"].exclusive_owner == "task_snap"


def test_21_task_resource_listing(rm: TaskResourceManager):
    """21. Task Resource Listing: list_resources_for_task returns held keys."""
    rm.acquire_resources("task_list", [ResourceType.CLIPBOARD, ResourceType.FOREGROUND_WINDOW])
    held = rm.list_resources_for_task("task_list")
    assert "CLIPBOARD/global" in held
    assert "FOREGROUND_WINDOW/global" in held


def test_22_bounded_metrics(rm: TaskResourceManager):
    """22. Bounded Metrics: Metric counters remain strictly bounded integers."""
    rm.acquire_resources("task_m1", ResourceType.COMPUTER_INTERACTION)
    rm.release_all_for_task("task_m1")

    snap = rm.snapshot()
    for key, val in snap.bounded_metrics.items():
        assert isinstance(val, int)
        assert val >= 0


def test_23_stale_cleanup(rm: TaskResourceManager):
    """23. Stale Cleanup: cleanup_stale_leases reclaims expired TTL leases."""
    # Lease with very short TTL
    rm.acquire_resources("task_stale", ResourceType.COMPUTER_INTERACTION, ttl_seconds=0.05)
    assert rm.can_acquire("task_fresh", ResourceType.COMPUTER_INTERACTION) is False

    time.sleep(0.08)
    reclaimed = rm.cleanup_stale_leases()
    assert reclaimed >= 1
    assert rm.can_acquire("task_fresh", ResourceType.COMPUTER_INTERACTION) is True


def test_24_invalid_resource_rejection(rm: TaskResourceManager):
    """24. Invalid Resource Rejection: Invalid identifiers raise ValueError."""
    with pytest.raises(ValueError):
        ResourceKey.from_string("UNKNOWN_RESOURCE_TYPE/some_scope")

    with pytest.raises(ValueError):
        rm.normalize_requests([12345])  # type: ignore


def test_25_empty_resource_request(rm: TaskResourceManager):
    """25. Empty Resource Request: Requesting empty list returns empty active lease."""
    lease = rm.acquire_resources("task_empty", [])
    assert lease.is_active is True
    assert len(lease.resources) == 0


def test_26_duplicate_resource_request(rm: TaskResourceManager):
    """26. Duplicate Resource Request: Multiple identical requests deduplicate safely."""
    reqs = [
        ResourceType.COMPUTER_INTERACTION,
        ResourceType.COMPUTER_INTERACTION,
        ResourceKey(resource_type=ResourceType.COMPUTER_INTERACTION, scope="global"),
    ]
    normalized = rm.normalize_requests(reqs)
    assert len(normalized) == 1
    assert normalized[0].resource_type == ResourceType.COMPUTER_INTERACTION


def test_27_secret_metadata_sanitization():
    """27. Secret Metadata Sanitization: Passwords, tokens, api_keys are redacted."""
    raw_meta = {
        "password": "supersecretpassword",
        "api_key": "sk-1234567890",
        "access_token": "token-abc",
        "safe_label": "desktop_automation",
    }
    lease = ResourceLease(task_id="task_meta", metadata=raw_meta)
    assert lease.metadata["password"] == "[REDACTED]"
    assert lease.metadata["api_key"] == "[REDACTED]"
    assert lease.metadata["access_token"] == "[REDACTED]"
    assert lease.metadata["safe_label"] == "desktop_automation"


def test_28_security_imports_audit():
    """28. Security Imports Audit: Static AST scan confirms zero execution imports."""
    import app.control.resources as res_mod
    import ast
    source = inspect.getsource(res_mod)
    tree = ast.parse(source)

    prohibited = {"subprocess", "pyautogui", "pynput", "win32api", "win32gui", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert not n.name.startswith(tuple(prohibited)), f"Prohibited import: {n.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(tuple(prohibited)), f"Prohibited import: {mod}"


def test_29_scheduler_cooperation(rm: TaskResourceManager):
    """29. Scheduler Cooperation: can_acquire_resources returns structured ResourceDecision."""
    decision_ok = rm.can_acquire_resources("task_sched", ResourceType.COMPUTER_INTERACTION)
    assert isinstance(decision_ok, ResourceDecision)
    assert decision_ok.allowed is True
    assert decision_ok.blocked is False

    rm.acquire_resources("task_sched", ResourceType.COMPUTER_INTERACTION)

    decision_blocked = rm.can_acquire_resources("task_other", ResourceType.COMPUTER_INTERACTION)
    assert decision_blocked.allowed is False
    assert decision_blocked.blocked is True
    assert len(decision_blocked.conflicts) == 1


def test_30_resource_advisor_cooperation(rm: TaskResourceManager):
    """30. Resource Advisor Cooperation: scheduler advisor hook integrates seamlessly."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    advised_tasks = []

    def sample_advisor(item: ScheduledTask) -> bool:
        advised_tasks.append(item.task_id)
        return True

    scheduler = MultiTaskScheduler(
        repository=repo,
        resource_manager=rm,
        resource_advisor=sample_advisor,
    )
    t = LongHorizonTask(task_id="adv_task", goal="Sample")
    repo.create_task(t)
    scheduler.enqueue_task(t)

    selected = scheduler.select_next_task()
    assert selected is not None
    assert "adv_task" in advised_tasks


def test_31_exception_safety(rm: TaskResourceManager):
    """31. Exception Safety: Empty task ID raises ValueError without corrupting internal locks."""
    with pytest.raises(ValueError):
        rm.acquire_resources("", ResourceType.COMPUTER_INTERACTION)

    # Next legitimate call works perfectly
    lease = rm.acquire_resources("valid_task", ResourceType.COMPUTER_INTERACTION)
    assert lease.is_active is True


def test_32_lease_lifecycle(rm: TaskResourceManager):
    """32. Lease Lifecycle: Transition through ACTIVE -> RELEASED."""
    lease = rm.acquire_resources("task_life", ResourceType.COMPUTER_INTERACTION)
    assert lease.state == LeaseState.ACTIVE
    assert lease.is_active is True

    rm.release_lease(lease.lease_id, owner_task_id="task_life")
    assert lease.state == LeaseState.RELEASED
    assert lease.is_active is False


def test_33_resource_state_transitions(rm: TaskResourceManager):
    """33. Resource State Transitions: AVAILABLE -> LOCKED -> AVAILABLE."""
    key = "COMPUTER_INTERACTION/global"
    assert rm.get_resource_state(key) == ResourceState.AVAILABLE

    lease = rm.acquire_resources("t_state", ResourceType.COMPUTER_INTERACTION)
    assert rm.get_resource_state(key) == ResourceState.LOCKED

    rm.release_lease(lease.lease_id, owner_task_id="t_state")
    assert rm.get_resource_state(key) == ResourceState.AVAILABLE


def test_34_unavailable_resource_reporting(rm: TaskResourceManager):
    """34. Unavailable Resource Reporting: Details exact blocked key and owner."""
    rm.acquire_resources("task_boss", ResourceType.CLIPBOARD)

    conflicts = rm.get_conflicts("task_worker", ResourceType.CLIPBOARD)
    assert len(conflicts) == 1
    assert conflicts[0].owned_by == "task_boss"
    assert conflicts[0].resource == "CLIPBOARD/global"


def test_35_atomicity_under_contention(rm: TaskResourceManager):
    """35. Atomicity Under Contention: Multiple competing atomic requests never leave partial locks."""
    rm.acquire_resources("seed_task", ResourceType.CLIPBOARD)

    # Request requiring both COMPUTER_INTERACTION and CLIPBOARD
    with pytest.raises(ResourceAcquisitionTimeoutError):
        rm.acquire_resources(
            "contender",
            [ResourceType.COMPUTER_INTERACTION, ResourceType.CLIPBOARD],
            timeout_seconds=0.05,
        )

    # COMPUTER_INTERACTION must NOT be locked by contender
    assert rm.get_owner("COMPUTER_INTERACTION/global") is None
    assert rm.can_acquire("third_party", ResourceType.COMPUTER_INTERACTION) is True


def test_36_release_after_failed_acquisition(rm: TaskResourceManager):
    """36. Release After Failed Acquisition: Initial blocker releases, unblocking follower."""
    l_block = rm.acquire_resources("blocker", ResourceType.CLIPBOARD)

    # Follower attempts and fails with short timeout
    with pytest.raises(ResourceAcquisitionTimeoutError):
        rm.acquire_resources("follower", ResourceType.CLIPBOARD, timeout_seconds=0.02)

    # Blocker releases
    rm.release_lease(l_block.lease_id, owner_task_id="blocker")

    # Follower now succeeds
    l_fol = rm.acquire_resources("follower", ResourceType.CLIPBOARD, timeout_seconds=0.1)
    assert l_fol.is_active is True


def test_37_release_all_for_task_multiple_types(rm: TaskResourceManager):
    """37. release_all_for_task: Cleans up multiple different resource types."""
    rm.acquire_resources(
        "task_heavy",
        [
            ResourceType.COMPUTER_INTERACTION,
            ResourceType.DESKTOP_SESSION,
            ResourceType.CLIPBOARD,
            ResourceType.FOREGROUND_WINDOW,
        ],
    )
    assert len(rm.list_resources_for_task("task_heavy")) == 4

    cleared = rm.release_all_for_task("task_heavy")
    assert cleared >= 4
    assert len(rm.list_resources_for_task("task_heavy")) == 0


def test_38_multiple_task_isolation(rm: TaskResourceManager):
    """38. Multiple Task Isolation: Tasks holding distinct scopes do not interfere."""
    l1 = rm.acquire_resources("t1", ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope="session:1"))
    l2 = rm.acquire_resources("t2", ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope="session:2"))

    assert l1.is_active and l2.is_active
    assert rm.get_owner("DESKTOP_SESSION/session:1") == "t1"
    assert rm.get_owner("DESKTOP_SESSION/session:2") == "t2"


def test_39_no_direct_execution_imports():
    """39. No Direct Execution Imports: AST verification against system execution libraries."""
    import app.control.resources as res_mod
    import ast
    tree = ast.parse(inspect.getsource(res_mod))

    banned = {"os.system", "os.popen", "subprocess", "ctypes", "pyautogui"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            val = getattr(node.value, "id", "")
            attr = f"{val}.{node.attr}" if val else node.attr
            assert attr not in banned, f"Banned attribute call: {attr}"


def test_40_coordination_layer_architecture_test(rm: TaskResourceManager):
    """40. Coordination-Layer Architecture Test: ResourceManager manages only logical state."""
    # Must only contain pure Python state management, no OS handles or sockets
    assert hasattr(rm, "_exclusive_owners")
    assert hasattr(rm, "_shared_owners")
    assert hasattr(rm, "_leases")
    assert hasattr(rm, "_metrics")
    assert not hasattr(rm, "execute")
    assert not hasattr(rm, "run_command")


# ---------------------------------------------------------------------------
# Additional Invariant & Integration Tests 41 - 55
# ---------------------------------------------------------------------------

def test_41_clipboard_mutex_globally_exclusive(rm: TaskResourceManager):
    """41. CLIPBOARD Mutex: Capacity 1 global exclusive lock."""
    rm.acquire_resources("clip_1", ResourceType.CLIPBOARD)
    assert rm.can_acquire("clip_2", ResourceType.CLIPBOARD) is False

    rm.release_resource("CLIPBOARD/global", owner_task_id="clip_1")
    assert rm.can_acquire("clip_2", ResourceType.CLIPBOARD) is True


def test_42_foreground_window_resource(rm: TaskResourceManager):
    """42. FOREGROUND_WINDOW: Exclusive lock for active window."""
    rm.acquire_resources("win_1", ResourceType.FOREGROUND_WINDOW)
    assert rm.can_acquire("win_2", ResourceType.FOREGROUND_WINDOW) is False

    rm.release_resource("FOREGROUND_WINDOW/global", owner_task_id="win_1")
    assert rm.can_acquire("win_2", ResourceType.FOREGROUND_WINDOW) is True


def test_43_browser_tab_capacity_pool(rm: TaskResourceManager):
    """43. BROWSER_TAB Capacity Pool: Pooled capacity limit."""
    rm.capacities[ResourceType.BROWSER_TAB] = 3

    l1 = rm.acquire_resources("t1", ResourceRequest(resource_type=ResourceType.BROWSER_TAB, lock_mode=LockMode.SHARED))
    l2 = rm.acquire_resources("t2", ResourceRequest(resource_type=ResourceType.BROWSER_TAB, lock_mode=LockMode.SHARED))
    l3 = rm.acquire_resources("t3", ResourceRequest(resource_type=ResourceType.BROWSER_TAB, lock_mode=LockMode.SHARED))

    # At capacity 3
    assert rm.can_acquire("t4", ResourceRequest(resource_type=ResourceType.BROWSER_TAB, lock_mode=LockMode.SHARED)) is False

    rm.release_resource("BROWSER_TAB/global", owner_task_id="t1")
    assert rm.can_acquire("t4", ResourceRequest(resource_type=ResourceType.BROWSER_TAB, lock_mode=LockMode.SHARED)) is True


def test_44_workspace_shared_concurrency_vs_exclusive(rm: TaskResourceManager):
    """44. WORKSPACE: Multiple shared readers coexist, exclusive write blocks."""
    r1 = rm.acquire_resources("reader1", ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.SHARED))
    r2 = rm.acquire_resources("reader2", ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.SHARED))

    assert r1.is_active and r2.is_active
    # Exclusive writer blocked
    assert rm.can_acquire("writer", ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.EXCLUSIVE)) is False

    rm.release_all_for_task("reader1")
    rm.release_all_for_task("reader2")
    # Writer can now acquire
    assert rm.can_acquire("writer", ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.EXCLUSIVE)) is True


def test_45_project_scope_isolation(rm: TaskResourceManager):
    """45. PROJECT: Distinct project scopes are isolated."""
    rm.acquire_resources("t1", ResourceRequest(resource_type=ResourceType.PROJECT, scope="project:backend"))
    assert rm.can_acquire("t2", ResourceRequest(resource_type=ResourceType.PROJECT, scope="project:backend")) is False
    assert rm.can_acquire("t2", ResourceRequest(resource_type=ResourceType.PROJECT, scope="project:frontend")) is True


def test_46_network_channel_capacity_pool(rm: TaskResourceManager):
    """46. NETWORK_CHANNEL: Rate-limit slot pooling."""
    rm.capacities[ResourceType.NETWORK_CHANNEL] = 2

    l1 = rm.acquire_resources("net1", ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, lock_mode=LockMode.SHARED))
    l2 = rm.acquire_resources("net2", ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, lock_mode=LockMode.SHARED))

    assert rm.can_acquire("net3", ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, lock_mode=LockMode.SHARED)) is False
    rm.release_resource("NETWORK_CHANNEL/global", owner_task_id="net1")
    assert rm.can_acquire("net3", ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, lock_mode=LockMode.SHARED)) is True


def test_47_desktop_session_scoping(rm: TaskResourceManager):
    """47. DESKTOP_SESSION: Application scoping."""
    rm.acquire_resources("t_calc", ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope="session:calc"))
    assert rm.can_acquire("t_notes", ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope="session:notepad")) is True
    assert rm.can_acquire("t_calc2", ResourceRequest(resource_type=ResourceType.DESKTOP_SESSION, scope="session:calc")) is False


@pytest.mark.asyncio
async def test_48_async_acquisition_and_release(rm: TaskResourceManager):
    """48. Async Acquisition & Release: acquire_resources_async unblocks on delayed release."""
    l_a = await rm.acquire_resources_async("task_async_a", ResourceType.COMPUTER_INTERACTION)

    async def delayed_release():
        await asyncio.sleep(0.04)
        rm.release_resource("COMPUTER_INTERACTION/global", owner_task_id="task_async_a")

    asyncio.create_task(delayed_release())

    l_b = await rm.acquire_resources_async("task_async_b", ResourceType.COMPUTER_INTERACTION, timeout_seconds=0.3)
    assert l_b.is_active is True
    assert l_b.task_id == "task_async_b"


def test_49_telemetry_event_emission(rm: TaskResourceManager, test_event_bus: ActionEventBus):
    """49. Telemetry Event Emission: Events published for acquire, release, and timeout."""
    lease = rm.acquire_resources("tele_task", ResourceType.COMPUTER_INTERACTION)
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.RESOURCE_ACQUIRED for e in events)

    rm.release_lease(lease.lease_id, owner_task_id="tele_task")
    events = test_event_bus.get_recent_events()
    assert any(e.action_type == ActionType.RESOURCE_RELEASED for e in events)


def test_50_requirement_inference(rm: TaskResourceManager):
    """50. Requirement Inference: Auto-detect resources for desktop, browser, and metadata."""
    step_desktop = LongHorizonTaskStep(name="Click", capability="desktop", action="click", application_context="calc")
    inferred_d = rm.infer_required_resources(step_desktop)
    keys_d = [r.canonical_string for r in inferred_d]
    assert "COMPUTER_INTERACTION/global" in keys_d
    assert "DESKTOP_SESSION/session:calc" in keys_d

    step_browser = LongHorizonTaskStep(name="Nav", capability="browser", action="navigate")
    inferred_b = rm.infer_required_resources(step_browser)
    keys_b = [r.canonical_string for r in inferred_b]
    assert "BROWSER_SESSION/browser:default" in keys_b

    step_custom = LongHorizonTaskStep(
        name="Custom",
        action="custom",
        arguments={"safe_metadata": {"required_resources": ["CLIPBOARD/global"]}},
    )
    inferred_c = rm.infer_required_resources(step_custom)
    assert inferred_c[0].resource_type == ResourceType.CLIPBOARD


def test_51_scheduler_gating_on_busy_resources(rm: TaskResourceManager):
    """51. Scheduler Gating on Busy Resources: Scheduler blocks competing GUI tasks."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    scheduler = MultiTaskScheduler(repository=repo, resource_manager=rm)

    t1 = LongHorizonTask(task_id="gui_1", goal="GUI Task 1")
    t1.safe_metadata["required_resources"] = ["COMPUTER_INTERACTION/global"]
    repo.create_task(t1)

    t2 = LongHorizonTask(task_id="gui_2", goal="GUI Task 2")
    t2.safe_metadata["required_resources"] = ["COMPUTER_INTERACTION/global"]
    repo.create_task(t2)

    scheduler.enqueue_task(t1, priority=TaskPriority.HIGH)
    scheduler.enqueue_task(t2, priority=TaskPriority.NORMAL)

    # First task selected
    sel_1 = scheduler.select_next_task()
    assert sel_1 is not None and sel_1.task_id == "gui_1"

    # Task 1 acquires resource
    rm.acquire_resources("gui_1", ResourceType.COMPUTER_INTERACTION)
    scheduler._active_tasks["gui_1"] = sel_1
    scheduler._queue.pop("gui_1", None)

    # Task 2 cannot run while Task 1 holds COMPUTER_INTERACTION
    sel_2 = scheduler.select_next_task()
    assert sel_2 is None

    # Task 1 finishes and releases
    rm.release_all_for_task("gui_1")
    scheduler._active_tasks.pop("gui_1", None)

    # Now Task 2 is selected
    sel_2 = scheduler.select_next_task()
    assert sel_2 is not None and sel_2.task_id == "gui_2"


def test_52_scheduler_parallel_non_conflicting_tasks(rm: TaskResourceManager):
    """52. Scheduler Parallel Non-Conflicting Tasks: Data task runs while GUI task is blocked."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    scheduler = MultiTaskScheduler(repository=repo, resource_manager=rm)

    t_gui = LongHorizonTask(task_id="t_gui_busy", goal="GUI Task")
    t_gui.safe_metadata["required_resources"] = ["COMPUTER_INTERACTION/global"]
    repo.create_task(t_gui)

    t_data = LongHorizonTask(task_id="t_data_ready", goal="Data Task")
    t_data.safe_metadata["required_resources"] = ["NETWORK_CHANNEL/global"]
    repo.create_task(t_data)

    scheduler.enqueue_task(t_gui, priority=TaskPriority.HIGH)
    scheduler.enqueue_task(t_data, priority=TaskPriority.NORMAL)

    # Simulate an active task currently holding COMPUTER_INTERACTION
    rm.acquire_resources("t_active_desktop", ResourceType.COMPUTER_INTERACTION)

    # Scheduler skips t_gui because resource is busy, and selects t_data
    selected = scheduler.select_next_task()
    assert selected is not None
    assert selected.task_id == "t_data_ready"


@pytest.mark.asyncio
async def test_53_scheduler_cancel_releases_resources(rm: TaskResourceManager):
    """53. Scheduler Cancel Releases Resources: Cancelling a task frees its resource allocations."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    scheduler = MultiTaskScheduler(repository=repo, resource_manager=rm)

    t = LongHorizonTask(task_id="t_cancel_me", goal="Task")
    repo.create_task(t)
    scheduler.enqueue_task(t)

    rm.acquire_resources("t_cancel_me", ResourceType.COMPUTER_INTERACTION)
    assert rm.can_acquire("other", ResourceType.COMPUTER_INTERACTION) is False

    await scheduler.cancel_task("t_cancel_me")
    assert rm.can_acquire("other", ResourceType.COMPUTER_INTERACTION) is True


def test_54_reentrant_partial_lease_release(rm: TaskResourceManager):
    """54. Reentrant Partial Lease Release: Releasing 1 of 2 leases for same task keeps resource locked."""
    l1 = rm.acquire_resources("t_reent", ResourceType.COMPUTER_INTERACTION)
    l2 = rm.acquire_resources("t_reent", ResourceType.COMPUTER_INTERACTION)

    # Releasing l2 should still keep COMPUTER_INTERACTION held under l1
    rm.release_lease(l2.lease_id, owner_task_id="t_reent")
    assert rm.can_acquire("other_task", ResourceType.COMPUTER_INTERACTION) is False

    # Releasing l1 frees the resource
    rm.release_lease(l1.lease_id, owner_task_id="t_reent")
    assert rm.can_acquire("other_task", ResourceType.COMPUTER_INTERACTION) is True


def test_55_singleton_instance_verification():
    """55. Singleton Instance Verification: Global resource_manager instance exists and is valid."""
    assert resource_manager is not None
    assert isinstance(resource_manager, TaskResourceManager)
    assert isinstance(resource_manager, ResourceManager)

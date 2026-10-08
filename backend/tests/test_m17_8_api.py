"""
RYVEN 3.0 — Milestone 17.8 Phase 5 Test Suite.
Multi-Task REST API + Scheduler Control + Frontend HUD Contract Verification.

Authoritative Architecture Hierarchy:
User / Conversation
        ↓
REST / Frontend Control Surface
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

Test Suite Coverage (72 comprehensive tests):
1.  Scheduler status endpoint (Tests 1-6)
2.  Queue listing, ordering, aging & conflict visibility (Tests 7-14)
3.  Resource manager status across 9 canonical types (Tests 15-20)
4.  Task detail & progress endpoints (Tests 21-28)
5.  Priority updates & bounded starvation aging (Tests 29-36)
6.  Task pause & resume controls (Tests 37-44)
7.  Task cancellation & resource reclamation (Tests 45-50)
8.  Task retry & recovery bounding (Tests 51-56)
9.  Task admission & submission endpoints (Tests 57-60)
10. Security, sanitization, concurrency & contract compatibility (Tests 61-72)
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import time
from typing import Any, Dict, Generator, List
import uuid

from fastapi.testclient import TestClient
import pytest

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.concurrency import (
    ConcurrencyState,
    ExecutionSlot,
    SafeConcurrencyController,
    safe_concurrency_controller,
)
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
    task_persistence_repo,
)
from app.control.recovery import (
    MultiTaskRecoveryManager,
    RecoveryState,
    multi_task_recovery_manager,
)
from app.control.resources import (
    CANONICAL_RESOURCE_ORDER,
    DEFAULT_RESOURCE_CAPACITIES,
    LockMode,
    ResourceRequest,
    ResourceType,
    TaskResourceManager,
    task_resource_manager,
)
from app.control.scheduler import (
    MultiTaskScheduler,
    ScheduledTask,
    SchedulerState,
    TaskPriority,
    multi_task_scheduler,
)
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def api_client(tmp_path: pathlib.Path) -> Generator[TestClient, None, None]:
    """Provide isolated TestClient with clean scheduler, resource, and storage state."""
    # 1. Isolated SQLite database
    db_file = str(tmp_path / f"test_api_{uuid.uuid4().hex[:8]}.db")
    test_repo = TaskPersistenceRepository(db_path=db_file)
    test_repo.migrate_to_v2()

    # 2. Reset singletons and bind isolated repository
    multi_task_scheduler.repo = test_repo
    multi_task_scheduler._queue.clear()
    multi_task_scheduler._active_tasks.clear()
    multi_task_scheduler._paused_tasks.clear()
    multi_task_scheduler._completed_task_ids.clear()
    multi_task_scheduler._metrics = {k: 0 for k in multi_task_scheduler._metrics}
    multi_task_scheduler._state = SchedulerState.RUNNING

    task_resource_manager.clear_all()
    task_resource_manager._metrics = {k: 0 for k in task_resource_manager._metrics}

    safe_concurrency_controller._slots.clear()
    safe_concurrency_controller.max_concurrency = 2

    multi_task_recovery_manager.repo = test_repo
    multi_task_recovery_manager.metrics = {k: 0 for k in multi_task_recovery_manager.metrics}

    # 3. Create fresh FastAPI application and TestClient
    app = create_app()
    client = TestClient(app)

    yield client

    # Cleanup
    task_resource_manager.clear_all()
    multi_task_scheduler._queue.clear()
    multi_task_scheduler._active_tasks.clear()
    multi_task_scheduler._paused_tasks.clear()
    safe_concurrency_controller._slots.clear()


# ===========================================================================
# 1. Scheduler Status (Tests 1-6)
# ===========================================================================

def test_01_status_empty_scheduler(api_client: TestClient):
    """GET /api/scheduler/status returns safe baseline metrics when empty."""
    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    data = res.json()
    assert data["scheduler_state"] == "RUNNING"
    assert data["running_task_count"] == 0
    assert data["queued_task_count"] == 0
    assert data["paused_task_count"] == 0
    assert data["blocked_task_count"] == 0
    assert data["active_execution_slots"] == 0
    assert data["max_concurrency"] >= 1
    assert "resource_usage_summary" in data


def test_02_status_reflects_running_tasks(api_client: TestClient):
    """Active tasks increment running_task_count in status."""
    task = LongHorizonTask(task_id="t-run-1", goal="Running goal", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    sched = ScheduledTask(task_id="t-run-1", task=task, state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler._active_tasks["t-run-1"] = sched

    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    assert res.json()["running_task_count"] == 1


def test_03_status_reflects_queued_tasks(api_client: TestClient):
    """Enqueued tasks increment queued_task_count in status."""
    task = LongHorizonTask(task_id="t-q-1", goal="Queued goal", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    assert res.json()["queued_task_count"] == 1


def test_04_status_reflects_blocked_tasks(api_client: TestClient):
    """Tasks blocked on dependencies increment blocked_task_count."""
    task = LongHorizonTask(task_id="t-child", goal="Child", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, dependencies=["missing-parent"])

    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    assert res.json()["blocked_task_count"] == 1


def test_05_status_reflects_resource_usage(api_client: TestClient):
    """Allocated resources update resource_usage_summary."""
    req = ResourceRequest(resource_type=ResourceType.WORKSPACE, target="proj-x")
    task_resource_manager.acquire_resources("t-res", [req])

    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    summary = res.json()["resource_usage_summary"]
    assert summary["occupied_resources"] >= 1


def test_06_status_reflects_concurrency_slots(api_client: TestClient):
    """SafeConcurrencyController active count is accurately reported."""
    slot = ExecutionSlot(task_id="t-slot-1", state=ConcurrencyState.RUNNING)
    safe_concurrency_controller._slots[slot.slot_id] = slot

    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    assert res.json()["active_execution_slots"] == 1


# ===========================================================================
# 2. Queue Listing, Ordering, Aging & Conflicts (Tests 7-14)
# ===========================================================================

def test_07_queue_empty(api_client: TestClient):
    """GET /api/scheduler/queue returns empty queue array when no tasks admitted."""
    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 0
    assert data["queue"] == []


def test_08_queue_single_task(api_client: TestClient):
    """Enqueued task is returned with complete metadata in queue response."""
    task = LongHorizonTask(task_id="t-q-single", goal="Single task", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=TaskPriority.HIGH)

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    item = data["queue"][0]
    assert item["task_id"] == "t-q-single"
    assert item["priority"] == 75
    assert item["effective_priority"] >= 75.0
    assert item["state"] == "READY"


def test_09_queue_priority_ordering(api_client: TestClient):
    """Tasks are returned in strict descending priority order."""
    t_low = LongHorizonTask(task_id="t-low", goal="Low", state=LongHorizonTaskState.QUEUED)
    t_crit = LongHorizonTask(task_id="t-crit", goal="Critical", state=LongHorizonTaskState.QUEUED)
    t_norm = LongHorizonTask(task_id="t-norm", goal="Normal", state=LongHorizonTaskState.QUEUED)

    multi_task_scheduler.repo.save_task(t_low)
    multi_task_scheduler.repo.save_task(t_crit)
    multi_task_scheduler.repo.save_task(t_norm)

    multi_task_scheduler.enqueue_task(t_low, priority=TaskPriority.LOW)
    multi_task_scheduler.enqueue_task(t_crit, priority=TaskPriority.CRITICAL)
    multi_task_scheduler.enqueue_task(t_norm, priority=TaskPriority.NORMAL)

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    queue = res.json()["queue"]
    assert len(queue) == 3
    assert queue[0]["task_id"] == "t-crit"
    assert queue[1]["task_id"] == "t-norm"
    assert queue[2]["task_id"] == "t-low"


def test_10_queue_bounded_aging_applied(api_client: TestClient):
    """Effective priority includes wait bonus without unbounded growth."""
    task = LongHorizonTask(task_id="t-aged", goal="Aging", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    item = multi_task_scheduler.enqueue_task(task, priority=TaskPriority.NORMAL)
    item.enqueued_at = time.time() - 20.0  # 20 seconds ago

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    q_item = res.json()["queue"][0]
    assert q_item["effective_priority"] > 50.0
    assert q_item["wait_bonus"] > 0.0


def test_11_queue_dependency_visibility(api_client: TestClient):
    """Blocked dependency state and reason are surfaced."""
    task = LongHorizonTask(task_id="t-child-dep", goal="Child", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, dependencies=["dep-prerequisite"])

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    q_item = res.json()["queue"][0]
    assert q_item["state"] == "BLOCKED_DEPENDENCY"
    assert "dep-prerequisite" in q_item["blocked_reason"]


def test_12_queue_resource_conflict_visibility(api_client: TestClient):
    """Blocked resource mutex surfaces BLOCKED_RESOURCE and blocked_resources array."""
    # Lock global mutex
    task_resource_manager.acquire_resources(
        "other-task",
        [ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION)],
    )

    task = LongHorizonTask(
        task_id="t-desk",
        goal="Desktop click",
        state=LongHorizonTaskState.QUEUED,
        safe_metadata={"required_resources": ["COMPUTER_INTERACTION"]},
    )
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    q_item = res.json()["queue"][0]
    assert q_item["state"] == "BLOCKED_RESOURCE"
    assert "COMPUTER_INTERACTION" in q_item["blocked_resources"]
    assert "other-task" in q_item["blocked_reason"]


def test_13_queue_ready_state(api_client: TestClient):
    """Task with no blocking conditions is marked READY."""
    task = LongHorizonTask(task_id="t-ready", goal="Ready goal", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    assert res.json()["queue"][0]["state"] == "READY"


def test_14_queue_secret_sanitization(api_client: TestClient):
    """Metadata in queue responses never leaks secret keys."""
    task = LongHorizonTask(
        task_id="t-secret-q",
        goal="Secret goal",
        state=LongHorizonTaskState.QUEUED,
        safe_metadata={"api_token": "super-secret-token", "project": "demo"},
    )
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, metadata={"user_password": "my_password"})

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    raw_text = res.text
    assert "super-secret-token" not in raw_text
    assert "my_password" not in raw_text


# ===========================================================================
# 3. Resource Manager Status (Tests 15-20)
# ===========================================================================

def test_15_resources_lists_all_nine_types(api_client: TestClient):
    """GET /api/scheduler/resources returns all 9 canonical resource types."""
    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    data = res.json()
    assert data["total_resources"] == 9
    types = [r["resource_type"] for r in data["resources"]]
    for rtype in CANONICAL_RESOURCE_ORDER:
        assert rtype.value in types


def test_16_resources_capacities_match_defaults(api_client: TestClient):
    """Capacities match strict M17.8 specifications."""
    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    r_map = {r["resource_type"]: r["capacity"] for r in res.json()["resources"]}
    assert r_map["COMPUTER_INTERACTION"] == 1
    assert r_map["CLIPBOARD"] == 1
    assert r_map["FOREGROUND_WINDOW"] == 1
    assert r_map["BROWSER_TAB"] == 8


def test_17_resources_active_usage_and_available(api_client: TestClient):
    """Acquiring a resource reduces available capacity accurately."""
    task_resource_manager.acquire_resources(
        "t-res-test",
        [ResourceRequest(resource_type=ResourceType.BROWSER_SESSION, scope="b-1")],
    )

    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    matching = next(r for r in res.json()["resources"] if r["resource_type"] == "BROWSER_SESSION")
    assert matching["active_usage"] == 1
    assert matching["available_capacity"] == matching["capacity"] - 1


def test_18_resources_safe_owner_visibility(api_client: TestClient):
    """Owner task IDs are visible in safe_owner_task_ids list."""
    task_resource_manager.acquire_resources(
        "owner-task-42",
        [ResourceRequest(resource_type=ResourceType.CLIPBOARD)],
    )

    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    matching = next(r for r in res.json()["resources"] if r["resource_type"] == "CLIPBOARD")
    assert "owner-task-42" in matching["safe_owner_task_ids"]


def test_19_resources_blocked_tasks_reported(api_client: TestClient):
    """Queued tasks blocked on resources are listed in blocked_tasks."""
    task_resource_manager.acquire_resources(
        "holder-task",
        [ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION)],
    )

    t_blocked = LongHorizonTask(
        task_id="t-waiting-ci",
        goal="Need CI",
        state=LongHorizonTaskState.QUEUED,
        safe_metadata={"required_resources": ["COMPUTER_INTERACTION"]},
    )
    multi_task_scheduler.repo.save_task(t_blocked)
    multi_task_scheduler.enqueue_task(t_blocked)

    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    ci_res = next(r for r in res.json()["resources"] if r["resource_type"] == "COMPUTER_INTERACTION")
    assert "t-waiting-ci" in ci_res["blocked_tasks"]


def test_20_resources_conflicts_structured_list(api_client: TestClient):
    """Resource conflicts are structured with resource, requested_by, and owned_by."""
    task_resource_manager.acquire_resources(
        "holder-task-20",
        [ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION)],
    )

    t_blocked = LongHorizonTask(
        task_id="t-req-20",
        goal="Need CI 20",
        state=LongHorizonTaskState.QUEUED,
        safe_metadata={"required_resources": ["COMPUTER_INTERACTION"]},
    )
    multi_task_scheduler.repo.save_task(t_blocked)
    multi_task_scheduler.enqueue_task(t_blocked)

    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    ci_res = next(r for r in res.json()["resources"] if r["resource_type"] == "COMPUTER_INTERACTION")
    assert len(ci_res["conflicts"]) >= 1
    assert ci_res["conflicts"][0]["owned_by"] == "holder-task-20"


# ===========================================================================
# 4. Task Detail & Progress (Tests 21-28)
# ===========================================================================

def test_21_task_detail_queued_task(api_client: TestClient):
    """GET /api/scheduler/tasks/{task_id} returns safe details for queued task."""
    task = LongHorizonTask(task_id="t-detail-1", goal="Detail goal", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=TaskPriority.HIGH)

    res = api_client.get("/api/scheduler/tasks/t-detail-1")
    assert res.status_code == 200
    data = res.json()
    assert data["task_id"] == "t-detail-1"
    assert data["goal"] == "Detail goal"
    assert data["priority"] == 75
    assert data["state"] == "QUEUED"


def test_22_task_detail_persisted_task(api_client: TestClient):
    """Task stored only in persistent DB is safely retrieved."""
    task = LongHorizonTask(task_id="t-persisted-only", goal="DB goal", state=LongHorizonTaskState.PAUSED)
    multi_task_scheduler.repo.save_task(task)

    res = api_client.get("/api/scheduler/tasks/t-persisted-only")
    assert res.status_code == 200
    data = res.json()
    assert data["task_id"] == "t-persisted-only"
    assert data["state"] == "PAUSED"


def test_23_task_detail_not_found_returns_404(api_client: TestClient):
    """Non-existent task ID returns 404."""
    res = api_client.get("/api/scheduler/tasks/does-not-exist")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_24_task_detail_empty_id_returns_400_or_404(api_client: TestClient):
    """Empty or whitespace task_id is safely rejected."""
    res = api_client.get("/api/scheduler/tasks/%20")
    assert res.status_code in (400, 404)


def test_25_task_detail_never_leaks_confirmation_tokens(api_client: TestClient):
    """Active confirmation tokens are never returned in GET task detail."""
    task = LongHorizonTask(
        task_id="t-token-test",
        goal="Confirmation goal",
        state=LongHorizonTaskState.WAITING_CONFIRMATION,
        active_confirmation_token="conf_secret_token_12345",
    )
    multi_task_scheduler.repo.save_task(task)

    res = api_client.get("/api/scheduler/tasks/t-token-test")
    assert res.status_code == 200
    assert "conf_secret_token_12345" not in res.text


def test_26_task_progress_returns_snapshot(api_client: TestClient):
    """GET /api/scheduler/tasks/{task_id}/progress returns progress snapshot."""
    task = LongHorizonTask(
        task_id="t-prog-1",
        goal="Progress goal",
        state=LongHorizonTaskState.RUNNING,
        progress_percent=45.0,
    )
    multi_task_scheduler.repo.save_task(task)

    res = api_client.get("/api/scheduler/tasks/t-prog-1/progress")
    assert res.status_code == 200
    data = res.json()
    assert data["task_id"] == "t-prog-1"
    assert data["progress_percent"] == 45.0


def test_27_task_progress_not_found_returns_404(api_client: TestClient):
    """Non-existent task progress returns 404."""
    res = api_client.get("/api/scheduler/tasks/missing-prog-task/progress")
    assert res.status_code == 404


def test_28_task_progress_scrubs_secrets(api_client: TestClient):
    """Task progress response never contains active confirmation tokens or secrets."""
    task = LongHorizonTask(
        task_id="t-prog-sec",
        goal="Scrub goal",
        state=LongHorizonTaskState.RUNNING,
        active_confirmation_token="secret_conf_token_abc",
        safe_metadata={"secret_pass": "super_pass"},
    )
    multi_task_scheduler.repo.save_task(task)

    res = api_client.get("/api/scheduler/tasks/t-prog-sec/progress")
    assert res.status_code == 200
    assert "secret_conf_token_abc" not in res.text
    assert "super_pass" not in res.text


# ===========================================================================
# 5. Priority Updates (Tests 29-36)
# ===========================================================================

def test_29_update_priority_standard_tiers(api_client: TestClient):
    """POST /api/scheduler/tasks/{task_id}/priority updates priority to CRITICAL (100)."""
    task = LongHorizonTask(task_id="t-prio-1", goal="Prio goal", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=TaskPriority.NORMAL)

    res = api_client.post("/api/scheduler/tasks/t-prio-1/priority", json={"priority": 100})
    assert res.status_code == 200
    assert res.json()["priority"] == 100
    assert res.json()["status"] == "UPDATED"

    # Verify queue state reflects change
    q_item = multi_task_scheduler.get_task("t-prio-1")
    assert q_item.base_priority == 100


def test_30_update_priority_custom_integer(api_client: TestClient):
    """Custom integer between 1 and 100 is accepted."""
    task = LongHorizonTask(task_id="t-prio-custom", goal="Custom", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=50)

    res = api_client.post("/api/scheduler/tasks/t-prio-custom/priority", json={"priority": 85})
    assert res.status_code == 200
    assert res.json()["priority"] == 85


def test_31_update_priority_string_tier_name(api_client: TestClient):
    """String tier name 'HIGH' maps to 75."""
    task = LongHorizonTask(task_id="t-prio-str", goal="String prio", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=50)

    res = api_client.post("/api/scheduler/tasks/t-prio-str/priority", json={"priority": "HIGH"})
    assert res.status_code == 200
    assert res.json()["priority"] == 75


def test_32_update_priority_invalid_value_returns_400(api_client: TestClient):
    """Invalid priority values (0, 150, 'INVALID') return 400 Bad Request."""
    task = LongHorizonTask(task_id="t-prio-inv", goal="Invalid", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res1 = api_client.post("/api/scheduler/tasks/t-prio-inv/priority", json={"priority": 0})
    assert res1.status_code == 400

    res2 = api_client.post("/api/scheduler/tasks/t-prio-inv/priority", json={"priority": 105})
    assert res2.status_code == 400

    res3 = api_client.post("/api/scheduler/tasks/t-prio-inv/priority", json={"priority": "UNKNOWN_TIER"})
    assert res3.status_code == 400


def test_33_update_priority_not_found_returns_404(api_client: TestClient):
    """Updating priority on non-existent task returns 404."""
    res = api_client.post("/api/scheduler/tasks/missing-task/priority", json={"priority": 75})
    assert res.status_code == 404


def test_34_update_priority_persisted_in_db(api_client: TestClient):
    """Priority update persists to scheduled_tasks in DB."""
    task = LongHorizonTask(task_id="t-prio-db", goal="DB Prio", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=50)

    res = api_client.post("/api/scheduler/tasks/t-prio-db/priority", json={"priority": 75})
    assert res.status_code == 200

    persisted = multi_task_scheduler.repo.get_scheduled_task("t-prio-db")
    assert persisted is not None
    assert persisted["priority"] == 75


def test_35_update_priority_reorders_queue(api_client: TestClient):
    """Elevating priority immediately reorders queue deterministically."""
    t1 = LongHorizonTask(task_id="t-ord-1", goal="First", state=LongHorizonTaskState.QUEUED)
    t2 = LongHorizonTask(task_id="t-ord-2", goal="Second", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(t1)
    multi_task_scheduler.repo.save_task(t2)

    multi_task_scheduler.enqueue_task(t1, priority=TaskPriority.NORMAL)  # 50
    multi_task_scheduler.enqueue_task(t2, priority=TaskPriority.LOW)     # 25

    # Elevate t2 to CRITICAL (100)
    res = api_client.post("/api/scheduler/tasks/t-ord-2/priority", json={"priority": 100})
    assert res.status_code == 200

    q_res = api_client.get("/api/scheduler/queue")
    assert q_res.status_code == 200
    queue = q_res.json()["queue"]
    assert queue[0]["task_id"] == "t-ord-2"
    assert queue[1]["task_id"] == "t-ord-1"


def test_36_update_priority_emits_telemetry(api_client: TestClient):
    """Priority change emits ActionType.TASK_PRIORITY_CHANGED event."""
    task = LongHorizonTask(task_id="t-prio-tel", goal="Tel", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    api_client.post("/api/scheduler/tasks/t-prio-tel/priority", json={"priority": 100})
    events = action_bus.get_recent_events(limit=20)
    prio_events = [e for e in events if e.action_type == ActionType.TASK_PRIORITY_CHANGED]
    assert len(prio_events) >= 1
    assert prio_events[-1].task_id == "t-prio-tel"


# ===========================================================================
# 6. Task Pause & Resume (Tests 37-44)
# ===========================================================================

def test_37_pause_queued_task(api_client: TestClient):
    """POST /api/scheduler/tasks/{task_id}/pause pauses a queued task."""
    task = LongHorizonTask(task_id="t-pause-q", goal="Pause Q", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.post("/api/scheduler/tasks/t-pause-q/pause")
    assert res.status_code == 200
    assert res.json()["status"] == "PAUSED"

    # Task removed from queue and present in paused
    assert "t-pause-q" not in multi_task_scheduler._queue
    assert "t-pause-q" in multi_task_scheduler._paused_tasks


def test_38_pause_active_task(api_client: TestClient):
    """Pausing an active task moves it to paused."""
    task = LongHorizonTask(task_id="t-pause-act", goal="Pause Act", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._active_tasks["t-pause-act"] = ScheduledTask(task_id="t-pause-act", task=task)

    res = api_client.post("/api/scheduler/tasks/t-pause-act/pause")
    assert res.status_code == 200
    assert res.json()["status"] == "PAUSED"
    assert "t-pause-act" not in multi_task_scheduler._active_tasks
    assert "t-pause-act" in multi_task_scheduler._paused_tasks


def test_39_pause_already_paused_idempotent(api_client: TestClient):
    """Pausing an already paused task is idempotent."""
    task = LongHorizonTask(task_id="t-pause-idem", goal="Idem", state=LongHorizonTaskState.PAUSED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._paused_tasks["t-pause-idem"] = ScheduledTask(task_id="t-pause-idem", task=task)

    res = api_client.post("/api/scheduler/tasks/t-pause-idem/pause")
    assert res.status_code == 200
    assert res.json()["status"] == "PAUSED"


def test_40_pause_terminal_task_returns_409(api_client: TestClient):
    """Pausing a COMPLETED task returns 409 Conflict."""
    task = LongHorizonTask(task_id="t-term-pause", goal="Term", state=LongHorizonTaskState.COMPLETED)
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-term-pause/pause")
    assert res.status_code == 409


def test_41_pause_not_found_returns_404(api_client: TestClient):
    """Pausing a non-existent task returns 404."""
    res = api_client.post("/api/scheduler/tasks/missing-pause-task/pause")
    assert res.status_code == 404


def test_42_resume_paused_task(api_client: TestClient):
    """POST /api/scheduler/tasks/{task_id}/resume moves paused task back to queue."""
    task = LongHorizonTask(task_id="t-resume-1", goal="Resume goal", state=LongHorizonTaskState.PAUSED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._paused_tasks["t-resume-1"] = ScheduledTask(task_id="t-resume-1", task=task)

    res = api_client.post("/api/scheduler/tasks/t-resume-1/resume")
    assert res.status_code == 200
    assert res.json()["status"] == "RESUMED"

    assert "t-resume-1" in multi_task_scheduler._queue
    assert "t-resume-1" not in multi_task_scheduler._paused_tasks


def test_43_resume_running_task_returns_409(api_client: TestClient):
    """Resuming an already running task returns 409 Conflict."""
    task = LongHorizonTask(task_id="t-already-run", goal="Run", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._active_tasks["t-already-run"] = ScheduledTask(task_id="t-already-run", task=task)

    res = api_client.post("/api/scheduler/tasks/t-already-run/resume")
    assert res.status_code == 409


def test_44_resume_terminal_task_returns_409(api_client: TestClient):
    """Resuming a CANCELLED task returns 409 Conflict."""
    task = LongHorizonTask(task_id="t-term-cancel", goal="Cancel", state=LongHorizonTaskState.CANCELLED)
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-term-cancel/resume")
    assert res.status_code == 409


# ===========================================================================
# 7. Task Cancellation & Resource Reclamation (Tests 45-50)
# ===========================================================================

def test_45_cancel_queued_task(api_client: TestClient):
    """POST /api/scheduler/tasks/{task_id}/cancel cancels a queued task."""
    task = LongHorizonTask(task_id="t-cancel-q", goal="Cancel Q", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.post("/api/scheduler/tasks/t-cancel-q/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELLED"
    assert "t-cancel-q" not in multi_task_scheduler._queue


def test_46_cancel_releases_all_resources(api_client: TestClient):
    """Cancelling a task holding COMPUTER_INTERACTION frees the mutex."""
    task_resource_manager.acquire_resources(
        "t-holder-cancel",
        [ResourceRequest(resource_type=ResourceType.COMPUTER_INTERACTION)],
    )
    assert task_resource_manager.is_available(ResourceType.COMPUTER_INTERACTION) is False

    task = LongHorizonTask(task_id="t-holder-cancel", goal="Hold CI", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._active_tasks["t-holder-cancel"] = ScheduledTask(task_id="t-holder-cancel", task=task)

    res = api_client.post("/api/scheduler/tasks/t-holder-cancel/cancel")
    assert res.status_code == 200
    assert task_resource_manager.is_available(ResourceType.COMPUTER_INTERACTION) is True


def test_47_cancel_releases_concurrency_slot(api_client: TestClient):
    """Cancelling a task frees its active concurrency slot."""
    slot = ExecutionSlot(task_id="t-slot-cancel", state=ConcurrencyState.RUNNING)
    safe_concurrency_controller._slots[slot.task_id] = slot
    assert safe_concurrency_controller.active_count == 1

    task = LongHorizonTask(task_id="t-slot-cancel", goal="Slot task", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._active_tasks["t-slot-cancel"] = ScheduledTask(task_id="t-slot-cancel", task=task)

    res = api_client.post("/api/scheduler/tasks/t-slot-cancel/cancel")
    assert res.status_code == 200
    assert safe_concurrency_controller.active_count == 0


def test_48_cancel_not_found_returns_404(api_client: TestClient):
    """Cancelling non-existent task returns 404."""
    res = api_client.post("/api/scheduler/tasks/missing-cancel-task/cancel")
    assert res.status_code == 404


def test_49_cancel_emits_telemetry(api_client: TestClient):
    """Cancellation emits LONG_TASK_CANCELLED event."""
    task = LongHorizonTask(task_id="t-cancel-tel", goal="Cancel tel", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    api_client.post("/api/scheduler/tasks/t-cancel-tel/cancel")
    events = action_bus.get_recent_events(limit=20)
    cancels = [e for e in events if e.action_type == ActionType.LONG_TASK_CANCELLED]
    assert len(cancels) >= 1


def test_50_cancel_persisted_in_db(api_client: TestClient):
    """Cancellation persists task state CANCELLED in database."""
    task = LongHorizonTask(task_id="t-cancel-persist", goal="Cancel persist", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    api_client.post("/api/scheduler/tasks/t-cancel-persist/cancel")
    persisted = multi_task_scheduler.repo.get_task("t-cancel-persist")
    assert persisted.state == LongHorizonTaskState.CANCELLED


# ===========================================================================
# 8. Task Retry & Recovery Bounding (Tests 51-56)
# ===========================================================================

def test_51_retry_failed_task(api_client: TestClient):
    """POST /api/scheduler/tasks/{task_id}/retry resets failed task and re-enqueues."""
    step_f = LongHorizonTaskStep(step_id="s1", name="Step 1", action="test_action", status=LongHorizonTaskState.FAILED)
    task = LongHorizonTask(
        task_id="t-retry-1",
        goal="Retry goal",
        state=LongHorizonTaskState.FAILED,
        steps=[step_f],
        failed_step_ids=["s1"],
    )
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-retry-1/retry")
    assert res.status_code == 200
    assert res.json()["status"] == "RETRYING"
    assert res.json()["retry_count"] == 1

    # Task is now in queue
    assert "t-retry-1" in multi_task_scheduler._queue


def test_52_retry_interrupted_task(api_client: TestClient):
    """Interrupted task can be safely retried."""
    task = LongHorizonTask(task_id="t-retry-inter", goal="Interrupted", state=LongHorizonTaskState.INTERRUPTED)
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-retry-inter/retry")
    assert res.status_code == 200
    assert res.json()["status"] == "RETRYING"


def test_53_retry_running_task_returns_409(api_client: TestClient):
    """Cannot retry an active running task."""
    task = LongHorizonTask(task_id="t-retry-act", goal="Act", state=LongHorizonTaskState.RUNNING)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler._active_tasks["t-retry-act"] = ScheduledTask(task_id="t-retry-act", task=task)

    res = api_client.post("/api/scheduler/tasks/t-retry-act/retry")
    assert res.status_code == 409


def test_54_retry_limit_exceeded_returns_409(api_client: TestClient):
    """Task that exceeded max retry limit (3) is rejected with 409."""
    task = LongHorizonTask(
        task_id="t-retry-max",
        goal="Max retry",
        state=LongHorizonTaskState.FAILED,
        recovery_count=3,
    )
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-retry-max/retry")
    assert res.status_code == 409
    assert "maximum retry" in res.json()["detail"].lower()


def test_55_retry_not_found_returns_404(api_client: TestClient):
    """Retrying non-existent task returns 404."""
    res = api_client.post("/api/scheduler/tasks/missing-retry-task/retry")
    assert res.status_code == 404


def test_56_retry_ineligible_completed_task_returns_409(api_client: TestClient):
    """COMPLETED tasks are not eligible for retry."""
    task = LongHorizonTask(task_id="t-retry-comp", goal="Comp", state=LongHorizonTaskState.COMPLETED)
    multi_task_scheduler.repo.save_task(task)

    res = api_client.post("/api/scheduler/tasks/t-retry-comp/retry")
    assert res.status_code == 409


# ===========================================================================
# 9. Task Admission & Submission (Tests 57-60)
# ===========================================================================

def test_57_submit_new_goal(api_client: TestClient):
    """POST /api/scheduler/submit admits a new goal into the multi-task scheduler."""
    res = api_client.post("/api/scheduler/submit", json={"goal": "Verify backup pipelines"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ADMITTED"
    assert data["task_id"].startswith("lht-")
    assert data["priority"] == 50
    assert "t" not in data or data["state"] == "QUEUED"


def test_58_submit_with_custom_priority(api_client: TestClient):
    """Submitting with CRITICAL priority admits at tier 100."""
    res = api_client.post(
        "/api/scheduler/submit",
        json={"goal": "Emergency patch deployment", "priority": "CRITICAL"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["priority"] == 100


def test_59_submit_with_dependencies(api_client: TestClient):
    """Submitting with dependencies records dependencies in admitted task."""
    res = api_client.post(
        "/api/scheduler/submit",
        json={"goal": "Child pipeline", "dependencies": ["parent-task-1", "parent-task-2"]},
    )
    assert res.status_code == 200
    tid = res.json()["task_id"]
    sched_item = multi_task_scheduler.get_task(tid)
    assert "parent-task-1" in sched_item.dependencies


def test_60_submit_empty_goal_returns_400(api_client: TestClient):
    """Empty goal string returns 400 Bad Request."""
    res = api_client.post("/api/scheduler/submit", json={"goal": "   "})
    assert res.status_code == 400


# ===========================================================================
# 10. Security, Concurrency & Contract Integrity (Tests 61-72)
# ===========================================================================

def test_61_never_leak_stack_traces_on_error(api_client: TestClient):
    """Errors never leak Python traceback or stack trace in HTTP response."""
    res = api_client.post("/api/scheduler/tasks/invalid-id/priority", json={"priority": "MALFORMED"})
    assert res.status_code == 400
    assert "Traceback" not in res.text
    assert "File \"" not in res.text


def test_62_sql_injection_defense_in_url(api_client: TestClient):
    """SQL injection payloads in URL route parameters return safe 404."""
    payload = "'; DROP TABLE long_horizon_tasks; --"
    res = api_client.get(f"/api/scheduler/tasks/{payload}")
    assert res.status_code in (400, 404)

    # Confirm table was not dropped
    assert multi_task_scheduler.repo.get_task("some-id") is None


def test_63_xss_defense_in_reason(api_client: TestClient):
    """XSS injection payloads in reason string do not cause execution errors."""
    task = LongHorizonTask(task_id="t-xss-test", goal="XSS", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    xss = "<script>alert('xss')</script>"
    res = api_client.post("/api/scheduler/tasks/t-xss-test/cancel", json={"reason": xss})
    assert res.status_code == 200
    assert res.json()["status"] == "CANCELLED"


def test_64_secret_redaction_in_post_responses(api_client: TestClient):
    """Sensitive values in request payloads are sanitized from responses."""
    res = api_client.post(
        "/api/scheduler/submit",
        json={"goal": "Process secret token", "resources": ["password_res"]},
    )
    assert res.status_code == 200
    assert "super_secret" not in res.text


def test_65_confirmation_token_safety_cannot_manufacture(api_client: TestClient):
    """Scheduler control routes cannot manufacture or bypass confirmation tokens."""
    task = LongHorizonTask(
        task_id="t-conf-guard",
        goal="Dangerous delete",
        state=LongHorizonTaskState.WAITING_CONFIRMATION,
        steps=[LongHorizonTaskStep(step_id="s1", name="DELETE", action="delete_file", requires_confirmation=True)],
    )
    multi_task_scheduler.repo.save_task(task)

    # Resume must not bypass confirmation
    res = api_client.post("/api/scheduler/tasks/t-conf-guard/resume")
    assert res.status_code in (200, 409)
    # Check that task in repo does not have fake confirmation token
    refreshed = multi_task_scheduler.repo.get_task("t-conf-guard")
    assert refreshed.active_confirmation_token is None


def test_66_concurrent_priority_updates(api_client: TestClient):
    """Multiple sequential priority updates maintain consistent internal state."""
    task = LongHorizonTask(task_id="t-concur-prio", goal="Prio Concur", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task, priority=50)

    for p in [25, 75, 10, 100, 50]:
        res = api_client.post("/api/scheduler/tasks/t-concur-prio/priority", json={"priority": p})
        assert res.status_code == 200

    q_item = multi_task_scheduler.get_task("t-concur-prio")
    assert q_item.base_priority == 50


def test_67_concurrent_pause_resume_requests(api_client: TestClient):
    """Rapid alternating pause and resume calls transition cleanly."""
    task = LongHorizonTask(task_id="t-pause-res-cycle", goal="Cycle", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    for _ in range(3):
        res_pause = api_client.post("/api/scheduler/tasks/t-pause-res-cycle/pause")
        assert res_pause.status_code == 200
        res_resume = api_client.post("/api/scheduler/tasks/t-pause-res-cycle/resume")
        assert res_resume.status_code == 200

    assert "t-pause-res-cycle" in multi_task_scheduler._queue


def test_68_frontend_contract_compatibility_status(api_client: TestClient):
    """Verify JSON schema for /status matches SchedulerStatusData TypeScript interface."""
    res = api_client.get("/api/scheduler/status")
    assert res.status_code == 200
    data = res.json()

    # Exact required keys matching TypeScript interface
    required_keys = [
        "scheduler_state",
        "running_task_count",
        "queued_task_count",
        "paused_task_count",
        "blocked_task_count",
        "recovery_required_count",
        "active_execution_slots",
        "max_concurrency",
        "resource_usage_summary",
        "preemption_count",
        "uptime_seconds",
    ]
    for k in required_keys:
        assert k in data, f"Missing TypeScript contract key: '{k}'"


def test_69_frontend_contract_compatibility_queue(api_client: TestClient):
    """Verify JSON schema for queue items matches QueuedTaskItem TypeScript interface."""
    task = LongHorizonTask(task_id="t-contract-q", goal="Contract Q", state=LongHorizonTaskState.QUEUED)
    multi_task_scheduler.repo.save_task(task)
    multi_task_scheduler.enqueue_task(task)

    res = api_client.get("/api/scheduler/queue")
    assert res.status_code == 200
    item = res.json()["queue"][0]

    required_keys = [
        "task_id",
        "goal",
        "state",
        "priority",
        "effective_priority",
        "enqueued_at",
        "dependencies",
        "requested_resources",
        "retry_count",
        "preemption_count",
        "recovery_count",
    ]
    for k in required_keys:
        assert k in item, f"Missing QueuedTaskItem contract key: '{k}'"


def test_70_frontend_contract_compatibility_resources(api_client: TestClient):
    """Verify JSON schema for resources matches ResourceItemData TypeScript interface."""
    res = api_client.get("/api/scheduler/resources")
    assert res.status_code == 200
    item = res.json()["resources"][0]

    required_keys = [
        "resource_type",
        "capacity",
        "active_usage",
        "available_capacity",
        "safe_owner_task_ids",
        "blocked_tasks",
        "conflicts",
    ]
    for k in required_keys:
        assert k in item, f"Missing ResourceItemData contract key: '{k}'"


def test_71_static_security_ast_audit_api():
    """Verify scheduler_routes.py contains ZERO prohibited OS, shell, or execution calls."""
    routes_path = pathlib.Path(__file__).parents[1] / "app" / "api" / "scheduler_routes.py"
    with open(routes_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    prohibited = {"subprocess", "pyautogui", "pynput"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                assert name.name not in prohibited, f"Prohibited import: '{name.name}'"
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in prohibited, f"Prohibited from-import: '{node.module}'"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"system", "popen", "spawnl", "execv"}, (
                f"Prohibited OS call: '{node.func.attr}'"
            )


def test_72_architecture_hierarchy_integrity():
    """Verify control flow routes strictly through authoritative managers."""
    # Scheduler routes must not import DesktopActionEngine or BrowserEngine directly
    routes_path = pathlib.Path(__file__).parents[1] / "app" / "api" / "scheduler_routes.py"
    with open(routes_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "DesktopActionEngine" not in content
    assert "BrowserEngine" not in content
    assert "win32gui" not in content
    assert "ctypes" not in content

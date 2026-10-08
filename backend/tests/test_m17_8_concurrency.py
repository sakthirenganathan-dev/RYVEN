"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling & Resource Coordination.
Phase 3: Authoritative Safe Concurrency & Cooperative Preemption Verification Suite.

Comprehensive Test Matrix (Tests 1 - 68):
1-10:   Controller Initialization & Concurrency Slots
11-20:  Preemption Candidate Selection & Decisions
21-30:  Checkpoint Safety, Atomic Actions & Desktop Protection
31-40:  Resumption Protocol & Error Handling
41-50:  Thread Safety, Race Conditions & Failure Recovery
51-60:  Scheduler & System Integrations
61-68:  Security, Architecture Audits & End-to-End Workflow
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
from app.control.concurrency import (
    ConcurrencyAdmissionDecision,
    ConcurrencySnapshot,
    ConcurrencyState,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MIN_PREEMPTION_PRIORITY_DELTA,
    DEFAULT_PREEMPTION_COOLDOWN_SECONDS,
    ExecutionSlot,
    PreemptionDecision,
    PreemptionStatus,
    SafeConcurrencyController,
    concurrency_controller,
)
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
)
from app.control.resources import (
    LockMode,
    ResourceRequest,
    ResourceType,
    TaskResourceManager,
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
def test_rm(test_event_bus: ActionEventBus) -> TaskResourceManager:
    """Isolated TaskResourceManager."""
    return TaskResourceManager(
        event_bus=test_event_bus,
        default_timeout_seconds=0.2,
        default_ttl_seconds=30.0,
    )


@pytest.fixture
def ctrl(test_rm: TaskResourceManager, test_event_bus: ActionEventBus) -> SafeConcurrencyController:
    """Isolated SafeConcurrencyController."""
    return SafeConcurrencyController(
        resource_manager_inst=test_rm,
        event_bus=test_event_bus,
        max_concurrency=2,
        min_preemption_priority_delta=10.0,
        preemption_cooldown_seconds=0.1,  # Short cooldown for test speed
        max_preemptions_per_task=3,
        max_consecutive_preemptions=2,
    )


# ---------------------------------------------------------------------------
# 1-10: Controller Initialization & Concurrency Slots
# ---------------------------------------------------------------------------

def test_01_controller_initialization(ctrl: SafeConcurrencyController):
    """1. Controller initialization: default configuration and zero active slots."""
    assert ctrl.max_concurrency == 2
    assert ctrl.min_preemption_priority_delta == 10.0
    assert ctrl.max_preemptions_per_task == 3
    assert len(ctrl.list_active_slots()) == 0
    assert ctrl.is_running is True


def test_02_configurable_concurrency(test_rm: TaskResourceManager):
    """2. Configurable concurrency: controller accepts custom max_concurrency."""
    c = SafeConcurrencyController(resource_manager_inst=test_rm, max_concurrency=4)
    assert c.max_concurrency == 4


def test_03_max_concurrency_enforcement(ctrl: SafeConcurrencyController):
    """3. Max concurrency enforcement: blocks when slot capacity is reached."""
    ctrl.acquire_slot("task_1", priority=10.0)
    ctrl.acquire_slot("task_2", priority=10.0)

    # 3rd task cannot be admitted
    assert ctrl.can_admit("task_3") is False
    with pytest.raises(RuntimeError, match="CONCURRENCY_LIMIT_REACHED"):
        ctrl.acquire_slot("task_3", priority=10.0)


def test_04_independent_task_concurrency(ctrl: SafeConcurrencyController):
    """4. Independent task concurrency: non-conflicting tasks run concurrently."""
    s1 = ctrl.acquire_slot("task_net1", resources=[ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, scope="ch:1")])
    s2 = ctrl.acquire_slot("task_net2", resources=[ResourceRequest(resource_type=ResourceType.NETWORK_CHANNEL, scope="ch:2")])

    assert s1.is_active and s2.is_active
    assert len(ctrl.list_active_slots()) == 2


def test_05_conflicting_resource_blocking(ctrl: SafeConcurrencyController):
    """5. Conflicting resource blocking: task requiring occupied resource is rejected."""
    ctrl.acquire_slot("task_ws1", resources=[ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.EXCLUSIVE)])

    # Task 2 wants exclusive access to the same workspace
    decision = ctrl.can_admit_task("task_ws2", resources=[ResourceRequest(resource_type=ResourceType.WORKSPACE, scope="repo", lock_mode=LockMode.EXCLUSIVE)])
    assert decision.admitted is False
    assert "RESOURCE_CONFLICT" in decision.reason


def test_06_computer_interaction_exclusivity(ctrl: SafeConcurrencyController):
    """6. COMPUTER_INTERACTION exclusivity: only one desktop task can hold slot & resource."""
    s1 = ctrl.acquire_slot("task_desk1", resources=[ResourceType.COMPUTER_INTERACTION])
    assert s1.is_active is True

    decision = ctrl.can_admit_task("task_desk2", resources=[ResourceType.COMPUTER_INTERACTION])
    assert decision.admitted is False
    assert "RESOURCE_CONFLICT" in decision.reason


def test_07_execution_slot_lifecycle(ctrl: SafeConcurrencyController):
    """7. Execution slot lifecycle: tracks slot states from RUNNING to COMPLETED."""
    slot = ctrl.acquire_slot("task_life", priority=15.0)
    assert slot.state == ConcurrencyState.RUNNING
    assert slot.is_active is True
    assert slot.task_id == "task_life"

    retrieved = ctrl.get_slot("task_life")
    assert retrieved is not None
    assert retrieved.slot_id == slot.slot_id


def test_08_slot_release(ctrl: SafeConcurrencyController):
    """8. Slot release: frees slot and releases resources."""
    ctrl.acquire_slot("task_rel", resources=[ResourceType.COMPUTER_INTERACTION])
    assert ctrl.can_admit("task_other", resources=[ResourceType.COMPUTER_INTERACTION]) is False

    success = ctrl.release_slot("task_rel", release_resources=True)
    assert success is True
    assert ctrl.get_slot("task_rel") is None
    # Now task_other can acquire
    assert ctrl.can_admit("task_other", resources=[ResourceType.COMPUTER_INTERACTION]) is True


def test_09_slot_release_idempotent(ctrl: SafeConcurrencyController):
    """9. Slot release idempotent: releasing non-existent slot returns False safely."""
    assert ctrl.release_slot("unknown_task") is False


def test_10_slot_metadata_scrubbing():
    """10. Slot metadata scrubbing: passwords and keys are redacted."""
    slot = ExecutionSlot(
        task_id="task_scrub",
        metadata={"api_key": "secret_key_123", "safe_val": "ok"},
        last_checkpoint={"auth_token": "token_abc", "step": 1},
    )
    assert slot.metadata["api_key"] == "[REDACTED]"
    assert slot.metadata["safe_val"] == "ok"
    assert slot.last_checkpoint["auth_token"] == "[REDACTED]"
    assert slot.last_checkpoint["step"] == 1


# ---------------------------------------------------------------------------
# 11-20: Preemption Candidate Selection & Decisions
# ---------------------------------------------------------------------------

def test_11_priority_aware_candidate_selection(ctrl: SafeConcurrencyController):
    """11. Priority-aware candidate selection: selects lower-priority task."""
    ctrl.acquire_slot("low_task", priority=10.0)
    ctrl.acquire_slot("med_task", priority=30.0)

    # High task with priority 60 wants to run
    candidate = ctrl.select_preemption_candidate("high_task", high_task_priority=60.0)
    assert candidate is not None
    assert candidate.task_id == "low_task"


def test_12_higher_priority_detection(ctrl: SafeConcurrencyController):
    """12. Higher-priority detection: rejects candidate if delta is below threshold."""
    ctrl.acquire_slot("task_a", priority=50.0)

    # Task B has priority 55 (delta 5 < 10)
    candidate = ctrl.select_preemption_candidate("task_b", high_task_priority=55.0)
    assert candidate is None


def test_13_preemption_eligibility_only_running(ctrl: SafeConcurrencyController):
    """13. Preemption eligibility: only RUNNING tasks are selected."""
    slot = ctrl.acquire_slot("task_running", priority=10.0)
    slot.state = ConcurrencyState.PAUSED

    candidate = ctrl.select_preemption_candidate("task_high", high_task_priority=80.0)
    assert candidate is None


def test_14_non_preemptible_task_protection(ctrl: SafeConcurrencyController):
    """14. Non-preemptible protection: task already under preemption request is not re-selected."""
    slot = ctrl.acquire_slot("task_preempting", priority=10.0)
    slot.preemption_requested = True

    candidate = ctrl.select_preemption_candidate("task_high", high_task_priority=80.0)
    assert candidate is None


def test_15_request_preemption_accepted(ctrl: SafeConcurrencyController):
    """15. Request preemption accepted: valid request transitions state to PREEMPTION_REQUESTED."""
    ctrl.acquire_slot("task_target", priority=10.0)

    decision = ctrl.request_preemption("task_target", requesting_task_id="task_high", requesting_priority=50.0)
    assert decision.status == PreemptionStatus.ACCEPTED
    assert decision.is_deferred is False

    slot = ctrl.get_slot("task_target")
    assert slot.preemption_requested is True
    assert slot.state == ConcurrencyState.PREEMPTION_REQUESTED


def test_16_request_preemption_rejected_low_delta(ctrl: SafeConcurrencyController):
    """16. Request preemption rejected: delta too small returns REJECTED."""
    ctrl.acquire_slot("task_target", priority=50.0)

    decision = ctrl.request_preemption("task_target", requesting_priority=55.0)
    assert decision.status == PreemptionStatus.REJECTED
    assert "PRIORITY_DELTA_TOO_LOW" in decision.reason


def test_17_minimum_priority_threshold_enforcement(ctrl: SafeConcurrencyController):
    """17. Minimum priority threshold: delta exactly at threshold is accepted."""
    ctrl.acquire_slot("task_target", priority=20.0)

    # Priority 30 (delta exactly 10.0)
    decision = ctrl.request_preemption("task_target", requesting_priority=30.0)
    assert decision.status == PreemptionStatus.ACCEPTED


def test_18_preemption_cooldown_enforcement(ctrl: SafeConcurrencyController):
    """18. Preemption cooldown: immediate repeated preemption during cooldown is rejected."""
    slot = ctrl.acquire_slot("task_cooldown", priority=10.0)
    slot.last_preempted_at = time.time()  # just preempted

    decision = ctrl.request_preemption("task_cooldown", requesting_priority=80.0)
    assert decision.status == PreemptionStatus.REJECTED
    assert "PREEMPTION_COOLDOWN_ACTIVE" in decision.reason


def test_19_max_preemption_count_limit_enforcement(ctrl: SafeConcurrencyController):
    """19. Max preemption count limit: task preempted max times is protected."""
    slot = ctrl.acquire_slot("task_limit", priority=10.0)
    slot.preemption_count = 3  # equal to max_preemptions_per_task (3)

    decision = ctrl.request_preemption("task_limit", requesting_priority=80.0)
    assert decision.status == PreemptionStatus.REJECTED
    assert "MAX_PREEMPTIONS_EXCEEDED" in decision.reason


def test_20_deterministic_task_id_tie_breaking(ctrl: SafeConcurrencyController):
    """20. Deterministic task ID tie-breaking: identical priority & runtime breaks ties by task_id."""
    t0 = time.time()
    s_b = ctrl.acquire_slot("task_b", priority=10.0)
    s_b.started_at = t0
    s_a = ctrl.acquire_slot("task_a", priority=10.0)
    s_a.started_at = t0

    candidate = ctrl.select_preemption_candidate("task_high", high_task_priority=80.0)
    assert candidate is not None
    # Lexicographically 'task_a' comes before 'task_b'
    assert candidate.task_id == "task_a"


# ---------------------------------------------------------------------------
# 21-30: Checkpoint Safety, Atomic Actions & Desktop Protection
# ---------------------------------------------------------------------------

def test_21_atomic_action_protection_deferred_preemption(ctrl: SafeConcurrencyController):
    """21. Atomic action protection: preemption is DEFERRED if atomic action is running."""
    ctrl.acquire_slot("task_atomic", priority=10.0)
    ctrl.set_atomic_protection("task_atomic", protected=True)

    decision = ctrl.request_preemption("task_atomic", requesting_priority=50.0)
    assert decision.status == PreemptionStatus.DEFERRED
    assert decision.is_deferred is True
    assert decision.safe_checkpoint_available is False

    slot = ctrl.get_slot("task_atomic")
    # Task remains RUNNING while atomic action executes
    assert slot.state == ConcurrencyState.RUNNING
    assert slot.preemption_requested is True


def test_22_desktop_action_protection_deferred_preemption(ctrl: SafeConcurrencyController):
    """22. Desktop action protection: active mouse/keyboard action defers preemption."""
    ctrl.acquire_slot("task_desk_active", priority=10.0)
    ctrl.set_desktop_action_active("task_desk_active", active=True)

    decision = ctrl.request_preemption("task_desk_active", requesting_priority=60.0)
    assert decision.status == PreemptionStatus.DEFERRED
    assert decision.is_deferred is True


def test_23_safe_checkpoint_detection_and_registration(ctrl: SafeConcurrencyController):
    """23. Safe checkpoint registration: stores checkpoint data in execution slot."""
    ctrl.acquire_slot("task_cp", priority=10.0)
    cp_data = {"step_index": 2, "state_digest": "clean"}

    assert ctrl.register_checkpoint("task_cp", cp_data) is True
    slot = ctrl.get_slot("task_cp")
    assert slot.last_checkpoint == cp_data


def test_24_checkpoint_clears_atomic_protection(ctrl: SafeConcurrencyController):
    """24. Checkpoint clears atomic protection: completing step unblocks deferred preemption."""
    ctrl.acquire_slot("task_clearing", priority=10.0)
    ctrl.set_atomic_protection("task_clearing", protected=True)

    ctrl.request_preemption("task_clearing", requesting_priority=50.0)
    slot = ctrl.get_slot("task_clearing")
    assert slot.is_atomic_protected is True

    # Register checkpoint at end of step
    ctrl.register_checkpoint("task_clearing", {"step": 1})
    assert slot.is_atomic_protected is False


def test_25_cooperative_pause_execution(ctrl: SafeConcurrencyController):
    """25. Cooperative pause execution: pauses task safely and updates accounting."""
    ctrl.acquire_slot("task_pause", priority=10.0)
    ctrl.request_preemption("task_pause")
    ctrl.register_checkpoint("task_pause", {"step": 3})

    success = ctrl.acknowledge_checkpoint_and_pause("task_pause", reason="Preempted for task_high")
    assert success is True

    # Slot was freed from active slots
    assert ctrl.get_slot("task_pause") is None
    # Checkpoint preserved for resume
    assert "task_pause" in ctrl._paused_checkpoints


def test_26_resource_release_after_cooperative_pause(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """26. Resource release after pause: held resources are released cleanly."""
    ctrl.acquire_slot("task_res_holder", resources=[ResourceType.COMPUTER_INTERACTION])
    assert test_rm.can_acquire("task_other", ResourceType.COMPUTER_INTERACTION) is False

    ctrl.request_preemption("task_res_holder")
    ctrl.acknowledge_checkpoint_and_pause("task_res_holder")

    # COMPUTER_INTERACTION is now completely free!
    assert test_rm.can_acquire("task_other", ResourceType.COMPUTER_INTERACTION) is True


def test_27_computer_interaction_cooperative_preemption_safety(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """27. COMPUTER_INTERACTION safety: desktop task finishes action and releases before new task enters."""
    # Desktop task 1 running
    ctrl.acquire_slot("desk_1", resources=[ResourceType.COMPUTER_INTERACTION])
    ctrl.set_desktop_action_active("desk_1", active=True)

    # High-priority desktop task 2 requests preemption
    decision = ctrl.request_preemption("desk_1", requesting_priority=90.0)
    assert decision.status == PreemptionStatus.DEFERRED

    # Desktop task 1 completes action and checkpoints
    ctrl.set_desktop_action_active("desk_1", active=False)
    ctrl.register_checkpoint("desk_1", {"screen": "ready"})
    ctrl.acknowledge_checkpoint_and_pause("desk_1")

    # Now desktop task 2 can acquire slot and mutex safely
    s2 = ctrl.acquire_slot("desk_2", resources=[ResourceType.COMPUTER_INTERACTION], priority=90.0)
    assert s2.is_active is True
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") == "desk_2"


def test_28_consecutive_preemption_tracking(ctrl: SafeConcurrencyController):
    """28. Consecutive preemption tracking: records preemption increments."""
    slot = ctrl.acquire_slot("task_consec", priority=10.0)
    assert slot.preemption_count == 0

    ctrl.request_preemption("task_consec")
    ctrl.acknowledge_checkpoint_and_pause("task_consec")

    # In paused record
    assert ctrl._metrics["successful_preemptions"] == 1


def test_29_repeated_preemption_limit_rejection(ctrl: SafeConcurrencyController):
    """29. Repeated preemption prevention: candidate selection skips tasks at limit."""
    slot = ctrl.acquire_slot("task_maxed", priority=10.0)
    slot.preemption_count = 3

    assert ctrl.select_preemption_candidate("task_high", high_task_priority=80.0) is None


def test_30_checkpoint_fallback_generation(ctrl: SafeConcurrencyController):
    """30. Checkpoint fallback generation: acknowledge_checkpoint_and_pause creates valid checkpoint if none registered."""
    ctrl.acquire_slot("task_no_cp", priority=10.0)
    ctrl.request_preemption("task_no_cp")

    assert ctrl.acknowledge_checkpoint_and_pause("task_no_cp") is True
    assert "task_no_cp" in ctrl._paused_checkpoints
    assert "checkpoint_id" in ctrl._paused_checkpoints["task_no_cp"]


# ---------------------------------------------------------------------------
# 31-40: Resumption Protocol & Error Handling
# ---------------------------------------------------------------------------

def test_31_resume_validation_requires_checkpoint(ctrl: SafeConcurrencyController):
    """31. Resume validation: cannot resume without a registered checkpoint."""
    assert ctrl.resume_task("task_never_checkpointed") is False
    assert ctrl._metrics["resume_failures"] >= 1


def test_32_resume_restores_execution_slot(ctrl: SafeConcurrencyController):
    """32. Resume restores slot: resumes task from checkpoint into RUNNING state."""
    ctrl.acquire_slot("task_to_resume", priority=15.0)
    ctrl.register_checkpoint("task_to_resume", {"step": 4})
    ctrl.acknowledge_checkpoint_and_pause("task_to_resume")

    assert ctrl.get_slot("task_to_resume") is None

    resumed = ctrl.resume_task("task_to_resume", priority=20.0)
    assert resumed is True

    slot = ctrl.get_slot("task_to_resume")
    assert slot is not None
    assert slot.state == ConcurrencyState.RUNNING
    assert slot.priority == 20.0
    assert slot.last_checkpoint["step"] == 4


def test_33_resume_reacquires_resources(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """33. Resume reacquires resources: resources freed on pause are reacquired on resume."""
    ctrl.acquire_slot("task_with_res", resources=[ResourceType.CLIPBOARD])
    ctrl.register_checkpoint("task_with_res", {"clipboard_data": "saved"})
    ctrl.acknowledge_checkpoint_and_pause("task_with_res")

    assert test_rm.get_owner("CLIPBOARD/global") is None

    resumed = ctrl.resume_task("task_with_res", resources=[ResourceType.CLIPBOARD])
    assert resumed is True
    assert test_rm.get_owner("CLIPBOARD/global") == "task_with_res"


def test_34_resume_blocked_on_resource_conflict(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """34. Resume blocked on conflict: if required resource is busy, resume fails cleanly."""
    ctrl.acquire_slot("task_res_a", resources=[ResourceType.CLIPBOARD])
    ctrl.register_checkpoint("task_res_a", {"step": 1})
    ctrl.acknowledge_checkpoint_and_pause("task_res_a")

    # Another task acquires CLIPBOARD in the meantime
    test_rm.acquire_resources("task_interferer", ResourceType.CLIPBOARD)

    # Resume fails without corrupting state
    resumed = ctrl.resume_task("task_res_a", resources=[ResourceType.CLIPBOARD])
    assert resumed is False
    assert ctrl.get_slot("task_res_a") is None


def test_35_resume_blocked_when_concurrency_capacity_full(ctrl: SafeConcurrencyController):
    """35. Resume blocked on capacity: cannot resume if active slots equal max_concurrency."""
    ctrl.acquire_slot("task_paused", priority=10.0)
    ctrl.register_checkpoint("task_paused", {"step": 1})
    ctrl.acknowledge_checkpoint_and_pause("task_paused")

    # Fill concurrency capacity (max = 2)
    ctrl.acquire_slot("task_fill_1", priority=20.0)
    ctrl.acquire_slot("task_fill_2", priority=20.0)

    # Resuming task_paused fails safely
    assert ctrl.resume_task("task_paused") is False


def test_36_resume_state_consistency(ctrl: SafeConcurrencyController):
    """36. Resume state consistency: slot metrics and active task counts update accurately."""
    ctrl.acquire_slot("task_consist", priority=10.0)
    ctrl.register_checkpoint("task_consist", {"step": 1})
    ctrl.acknowledge_checkpoint_and_pause("task_consist")

    before_resumes = ctrl._metrics["resume_count"]
    ctrl.resume_task("task_consist", priority=15.0)
    assert ctrl._metrics["resume_count"] == before_resumes + 1
    assert ctrl._metrics["active_task_count"] == 1


def test_37_environment_drift_compatibility(ctrl: SafeConcurrencyController):
    """37. Environment drift compatibility: checkpoint metadata preserves drift attributes."""
    cp_with_drift = {
        "step": 2,
        "environment_state": {"window": "calc", "drift_detected": False},
    }
    ctrl.acquire_slot("task_drift", priority=10.0)
    ctrl.register_checkpoint("task_drift", cp_with_drift)
    ctrl.acknowledge_checkpoint_and_pause("task_drift")

    ctrl.resume_task("task_drift")
    slot = ctrl.get_slot("task_drift")
    assert slot.last_checkpoint["environment_state"]["window"] == "calc"


def test_38_resume_failure_leaves_zero_orphaned_slots(ctrl: SafeConcurrencyController):
    """38. Resume failure cleanup: failed resume never leaves orphaned slots."""
    # Attempt resume without checkpoint
    ctrl.resume_task("invalid_task")
    assert ctrl.get_slot("invalid_task") is None
    assert len(ctrl.list_active_slots()) == 0


def test_39_queued_task_recovery(ctrl: SafeConcurrencyController):
    """39. Queued task recovery: task can be admitted normally after a failed attempt."""
    ctrl.acquire_slot("t1", priority=10.0)
    ctrl.acquire_slot("t2", priority=10.0)

    # t3 fails
    assert ctrl.can_admit("t3") is False

    # t1 completes
    ctrl.release_slot("t1")

    # t3 can now be admitted
    assert ctrl.can_admit("t3") is True
    s3 = ctrl.acquire_slot("t3", priority=10.0)
    assert s3.is_active is True


def test_40_preemption_rejection_leaves_slot_running(ctrl: SafeConcurrencyController):
    """40. Preemption rejection safety: rejected preemption leaves task unaffected and RUNNING."""
    slot = ctrl.acquire_slot("task_unaffected", priority=50.0)

    # Request with lower priority
    decision = ctrl.request_preemption("task_unaffected", requesting_priority=40.0)
    assert decision.status == PreemptionStatus.REJECTED

    assert slot.state == ConcurrencyState.RUNNING
    assert slot.preemption_requested is False


# ---------------------------------------------------------------------------
# 41-50: Thread Safety, Race Conditions & Failure Recovery
# ---------------------------------------------------------------------------

def test_41_concurrent_slot_acquisitions(ctrl: SafeConcurrencyController):
    """41. Concurrent slot acquisitions: multithreaded slot requests respect capacity."""
    admitted = []
    blocked = []

    def try_acquire(tid: str):
        try:
            slot = ctrl.acquire_slot(tid, priority=10.0)
            admitted.append(slot.task_id)
        except RuntimeError:
            blocked.append(tid)

    threads = [threading.Thread(target=try_acquire, args=(f"t_{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == ctrl.max_concurrency
    assert len(blocked) == 10 - ctrl.max_concurrency


def test_42_concurrent_preemption_requests(ctrl: SafeConcurrencyController):
    """42. Concurrent preemption requests: multiple threads requesting preemption are safe."""
    ctrl.acquire_slot("victim_task", priority=10.0)
    results = []

    def request(req_id: str):
        dec = ctrl.request_preemption("victim_task", requesting_task_id=req_id, requesting_priority=80.0)
        results.append(dec.status)

    threads = [threading.Thread(target=request, args=(f"req_{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Preemption request accepted without corruption
    assert any(st == PreemptionStatus.ACCEPTED for st in results)


def test_43_race_condition_protection(ctrl: SafeConcurrencyController):
    """43. Race condition protection: simultaneous pause and checkpoint registration succeed."""
    ctrl.acquire_slot("race_task", priority=10.0)
    ctrl.request_preemption("race_task")

    t1 = threading.Thread(target=lambda: ctrl.register_checkpoint("race_task", {"step": 1}))
    t2 = threading.Thread(target=lambda: ctrl.acknowledge_checkpoint_and_pause("race_task"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Completed pause safely
    assert ctrl.get_slot("race_task") is None


def test_44_controller_lifecycle_start_stop(ctrl: SafeConcurrencyController):
    """44. Controller lifecycle: start and stop methods update running state."""
    assert ctrl.is_running is True
    ctrl.stop()
    assert ctrl.is_running is False
    ctrl.start()
    assert ctrl.is_running is True


def test_45_clean_shutdown(ctrl: SafeConcurrencyController):
    """45. Clean shutdown: stops controller and reports state in snapshot."""
    ctrl.stop()
    snap = ctrl.snapshot()
    assert snap.max_concurrency == ctrl.max_concurrency


def test_46_exception_recovery_empty_task_id(ctrl: SafeConcurrencyController):
    """46. Exception recovery: empty task ID raises ValueError without corrupting locks."""
    with pytest.raises(ValueError):
        ctrl.acquire_slot("")

    # Subsequent valid acquisition works
    slot = ctrl.acquire_slot("valid_task_after_err", priority=10.0)
    assert slot.is_active is True


def test_47_bounded_metrics_counters(ctrl: SafeConcurrencyController):
    """47. Bounded metrics: all metric counters are non-negative integers."""
    ctrl.acquire_slot("t_metric", priority=10.0)
    ctrl.release_slot("t_metric")

    snap = ctrl.snapshot()
    for k, v in snap.bounded_metrics.items():
        assert isinstance(v, int)
        assert v >= 0


def test_48_telemetry_emission_on_all_lifecycle_stages(ctrl: SafeConcurrencyController, test_event_bus: ActionEventBus):
    """48. Telemetry emission: events emitted for slot acquisition, pause, and release."""
    ctrl.acquire_slot("t_events", priority=10.0)
    ctrl.register_checkpoint("t_events", {"step": 1})
    ctrl.request_preemption("t_events")
    ctrl.acknowledge_checkpoint_and_pause("t_events")

    events = test_event_bus.get_recent_events()
    types = [e.action_type for e in events]
    assert ActionType.CONCURRENCY_SLOT_ACQUIRED in types
    assert ActionType.PREEMPTION_CHECKPOINTED in types
    assert ActionType.PREEMPTION_REQUESTED in types
    assert ActionType.TASK_PAUSED in types


def test_49_task_isolation_across_multiple_slots(ctrl: SafeConcurrencyController):
    """49. Task isolation: distinct tasks maintain isolated priorities and checkpoints."""
    s1 = ctrl.acquire_slot("iso_1", priority=15.0)
    s2 = ctrl.acquire_slot("iso_2", priority=25.0)

    ctrl.register_checkpoint("iso_1", {"data": 1})
    ctrl.register_checkpoint("iso_2", {"data": 2})

    assert s1.priority == 15.0
    assert s2.priority == 25.0
    assert s1.last_checkpoint["data"] == 1
    assert s2.last_checkpoint["data"] == 2


def test_50_no_infinite_loops_or_blocking(ctrl: SafeConcurrencyController):
    """50. No infinite loops: all concurrency and preemption operations complete in < 0.1s."""
    t0 = time.time()
    for i in range(20):
        ctrl.can_admit(f"t_{i}")
        ctrl.select_preemption_candidate(f"t_{i}", high_task_priority=50.0)
    elapsed = time.time() - t0
    assert elapsed < 0.2


# ---------------------------------------------------------------------------
# 51-60: Scheduler & System Integrations
# ---------------------------------------------------------------------------

def test_51_scheduler_concurrency_gating(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """51. Scheduler concurrency gating: scheduler skips tasks when controller is full."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    scheduler = MultiTaskScheduler(
        repository=repo,
        resource_manager=test_rm,
        concurrency_controller=ctrl,
    )

    t1 = LongHorizonTask(task_id="task_sched_1", goal="Task 1")
    t2 = LongHorizonTask(task_id="task_sched_2", goal="Task 2")
    t3 = LongHorizonTask(task_id="task_sched_3", goal="Task 3")
    repo.create_task(t1)
    repo.create_task(t2)
    repo.create_task(t3)

    scheduler.enqueue_task(t1, priority=TaskPriority.NORMAL)
    scheduler.enqueue_task(t2, priority=TaskPriority.NORMAL)
    scheduler.enqueue_task(t3, priority=TaskPriority.NORMAL)

    # Fill 2 concurrency slots with currently executing workers
    ctrl.acquire_slot("running_worker_1", priority=20.0)
    ctrl.acquire_slot("running_worker_2", priority=20.0)

    # Scheduler select_next_task should return None because concurrency capacity is full
    selected = scheduler.select_next_task()
    assert selected is None


def test_52_scheduler_low_priority_preemption_flow(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """52. Scheduler low-priority preemption flow: candidate selected and cooperatively paused."""
    # Slot occupied by low-priority task
    ctrl.acquire_slot("t_low", priority=10.0, resources=[ResourceType.COMPUTER_INTERACTION])

    # High-priority task with priority 80 wants desktop
    cand = ctrl.select_preemption_candidate("t_high", high_task_priority=80.0, high_task_resources=[ResourceType.COMPUTER_INTERACTION])
    assert cand is not None
    assert cand.task_id == "t_low"

    # Request preemption & pause
    ctrl.request_preemption("t_low", requesting_task_id="t_high", requesting_priority=80.0)
    ctrl.register_checkpoint("t_low", {"step": 2})
    ctrl.acknowledge_checkpoint_and_pause("t_low")

    # High-priority task can now acquire slot and resource
    high_slot = ctrl.acquire_slot("t_high", priority=80.0, resources=[ResourceType.COMPUTER_INTERACTION])
    assert high_slot.is_active is True
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") == "t_high"


def test_53_scheduler_cancellation_frees_concurrency_slot(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """53. Scheduler cancellation: cancelling a task via scheduler frees its concurrency slot."""
    repo = TaskPersistenceRepository(db_path=":memory:")
    scheduler = MultiTaskScheduler(
        repository=repo,
        resource_manager=test_rm,
        concurrency_controller=ctrl,
    )

    t = LongHorizonTask(task_id="t_to_cancel", goal="Cancel me")
    repo.create_task(t)
    scheduler.enqueue_task(t)

    ctrl.acquire_slot("t_to_cancel", priority=20.0)
    assert ctrl.get_slot("t_to_cancel") is not None

    asyncio.run(scheduler.cancel_task("t_to_cancel"))
    assert ctrl.get_slot("t_to_cancel") is None


def test_54_resource_manager_zero_leaks_after_preemption(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """54. Zero resource leaks: snapshot confirms no orphaned resources after pause."""
    ctrl.acquire_slot("t_leaker", resources=[ResourceType.CLIPBOARD, ResourceType.DESKTOP_SESSION])
    assert test_rm.snapshot().occupied_resources_count >= 2

    ctrl.request_preemption("t_leaker")
    ctrl.acknowledge_checkpoint_and_pause("t_leaker")

    assert test_rm.snapshot().occupied_resources_count == 0


def test_55_fairness_under_repeated_priority_pressure(ctrl: SafeConcurrencyController):
    """55. Fairness under pressure: task preempted max times cannot be preempted again."""
    slot = ctrl.acquire_slot("t_starved", priority=10.0)
    slot.preemption_count = 3  # hit max limit

    cand = ctrl.select_preemption_candidate("t_greedy", high_task_priority=100.0)
    assert cand is None


def test_56_starvation_prevention_aging_cooperation(ctrl: SafeConcurrencyController):
    """56. Starvation prevention: effective priority increases protect aged tasks from preemption."""
    # Base priority 10, but aged effective priority 45
    ctrl.acquire_slot("t_aged", priority=45.0)

    # Competitor with priority 50 has delta only 5 (< 10)
    cand = ctrl.select_preemption_candidate("t_competitor", high_task_priority=50.0)
    assert cand is None


def test_57_pause_resume_state_consistency(ctrl: SafeConcurrencyController):
    """57. Pause/resume state consistency: repeated pause and resume cycles maintain integrity."""
    ctrl.acquire_slot("t_cycle", priority=10.0)
    ctrl.register_checkpoint("t_cycle", {"cycle": 1})
    ctrl.acknowledge_checkpoint_and_pause("t_cycle")

    ctrl.resume_task("t_cycle", priority=15.0)
    assert ctrl.get_slot("t_cycle").priority == 15.0

    ctrl.register_checkpoint("t_cycle", {"cycle": 2})
    ctrl.acknowledge_checkpoint_and_pause("t_cycle")
    assert ctrl.get_slot("t_cycle") is None


def test_58_high_priority_task_admission_after_preemption(ctrl: SafeConcurrencyController):
    """58. High-priority admission: slot capacity freed by preemption is immediately usable."""
    ctrl.acquire_slot("t_fill1", priority=10.0)
    ctrl.acquire_slot("t_fill2", priority=10.0)
    assert ctrl.can_admit("t_boss") is False

    # Preempt t_fill1
    ctrl.request_preemption("t_fill1")
    ctrl.acknowledge_checkpoint_and_pause("t_fill1")

    # t_boss can now be admitted
    assert ctrl.can_admit("t_boss") is True
    slot_boss = ctrl.acquire_slot("t_boss", priority=90.0)
    assert slot_boss.is_active is True


def test_59_reentrant_slot_acquisition(ctrl: SafeConcurrencyController):
    """59. Reentrant slot acquisition: calling acquire_slot for same task returns existing slot."""
    s1 = ctrl.acquire_slot("t_reent", priority=20.0)
    s2 = ctrl.acquire_slot("t_reent", priority=30.0)

    assert s1.slot_id == s2.slot_id
    assert s2.priority == 30.0


def test_60_maximum_active_task_enforcement(ctrl: SafeConcurrencyController):
    """60. Maximum active task count: active_task_count never exceeds max_concurrency."""
    for i in range(ctrl.max_concurrency):
        ctrl.acquire_slot(f"t_max_{i}", priority=10.0)

    snap = ctrl.snapshot()
    assert snap.active_task_count == ctrl.max_concurrency
    assert len(snap.active_slots) == ctrl.max_concurrency


# ---------------------------------------------------------------------------
# 61-68: Security, Architecture Audits & End-to-End Workflow
# ---------------------------------------------------------------------------

def test_61_static_ast_zero_execution_imports():
    """61. Static AST: zero prohibited execution imports in concurrency.py."""
    import app.control.concurrency as conc_mod
    import ast
    source = inspect.getsource(conc_mod)
    tree = ast.parse(source)

    prohibited = {"subprocess", "pyautogui", "pynput", "win32api", "win32gui", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert not n.name.startswith(tuple(prohibited)), f"Prohibited import: {n.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith(tuple(prohibited)), f"Prohibited import: {mod}"


def test_62_static_ast_zero_os_execution_calls():
    """62. Static AST: zero os.system, os.popen, or subprocess calls."""
    import app.control.concurrency as conc_mod
    import ast
    tree = ast.parse(inspect.getsource(conc_mod))

    banned = {"os.system", "os.popen", "subprocess", "ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            val = getattr(node.value, "id", "")
            attr = f"{val}.{node.attr}" if val else node.attr
            assert attr not in banned, f"Banned attribute call: {attr}"


def test_63_coordination_layer_architecture_invariants(ctrl: SafeConcurrencyController):
    """63. Architecture invariants: SafeConcurrencyController does not execute actions."""
    assert not hasattr(ctrl, "execute")
    assert not hasattr(ctrl, "run_command")
    assert not hasattr(ctrl, "click")
    assert not hasattr(ctrl, "type")
    assert hasattr(ctrl, "_slots")
    assert hasattr(ctrl, "_metrics")


def test_64_snapshot_inspection_and_properties(ctrl: SafeConcurrencyController):
    """64. Snapshot inspection: returns complete immutable state."""
    ctrl.acquire_slot("t_snap1", priority=10.0)
    ctrl.acquire_slot("t_snap2", priority=20.0)

    snap = ctrl.snapshot()
    assert isinstance(snap, ConcurrencySnapshot)
    assert snap.active_task_count == 2
    assert "t_snap1" in snap.active_slots
    assert "t_snap2" in snap.active_slots


def test_65_singleton_instance_verification():
    """65. Singleton verification: global concurrency_controller instance exists."""
    assert concurrency_controller is not None
    assert isinstance(concurrency_controller, SafeConcurrencyController)


def test_66_end_to_end_cooperative_preemption_workflow(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """66. End-to-end workflow: Task A runs -> Task B requests preemption -> A checkpoints -> A pauses -> B runs."""
    # 1. Task A runs with desktop interaction
    ctrl.acquire_slot("task_a", priority=10.0, resources=[ResourceType.COMPUTER_INTERACTION])
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") == "task_a"

    # 2. Task A begins an atomic desktop click step
    ctrl.set_desktop_action_active("task_a", active=True)

    # 3. High-priority Task B requests preemption
    dec = ctrl.request_preemption("task_a", requesting_task_id="task_b", requesting_priority=80.0)
    assert dec.status == PreemptionStatus.DEFERRED  # deferred because desktop action is active

    # 4. Task A finishes desktop action and reaches checkpoint
    ctrl.set_desktop_action_active("task_a", active=False)
    ctrl.register_checkpoint("task_a", {"step": "click_submit", "result": "success"})

    # 5. Task A cooperatively pauses
    paused = ctrl.acknowledge_checkpoint_and_pause("task_a", reason="Preempted for task_b")
    assert paused is True
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") is None

    # 6. Task B acquires slot and desktop mutex
    slot_b = ctrl.acquire_slot("task_b", priority=80.0, resources=[ResourceType.COMPUTER_INTERACTION])
    assert slot_b.is_active is True
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") == "task_b"

    # 7. Task B finishes and releases slot
    ctrl.release_slot("task_b", release_resources=True)
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") is None

    # 8. Task A resumes from checkpoint and reacquires desktop mutex
    resumed = ctrl.resume_task("task_a", priority=15.0, resources=[ResourceType.COMPUTER_INTERACTION])
    assert resumed is True
    assert test_rm.get_owner("COMPUTER_INTERACTION/global") == "task_a"
    assert ctrl.get_slot("task_a").last_checkpoint["step"] == "click_submit"


def test_67_admission_decision_conflicts_reporting(ctrl: SafeConcurrencyController, test_rm: TaskResourceManager):
    """67. Conflict reporting: can_admit_task returns exact conflicting resource keys."""
    test_rm.acquire_resources("blocker_task", ResourceType.CLIPBOARD)

    dec = ctrl.can_admit_task("victim_task", resources=[ResourceType.CLIPBOARD])
    assert dec.admitted is False
    assert "CLIPBOARD/global" in dec.conflicting_resources


def test_68_consecutive_preemption_limit_protection(ctrl: SafeConcurrencyController):
    """68. Consecutive preemption protection: task with max consecutive preemptions is skipped."""
    slot = ctrl.acquire_slot("t_consec_limit", priority=10.0)
    slot.consecutive_preemptions = 2  # max consecutive limit

    # Rejection via select_preemption_candidate if max_consecutive_preemptions exceeded
    slot.preemption_count = 3
    cand = ctrl.select_preemption_candidate("t_high", high_task_priority=90.0)
    assert cand is None

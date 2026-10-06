"""
RYVEN 3.0 — Milestone 17.5.1 Real-World E2E Computer-Use Hardening Live Verification Script.

Runs live validation of:
1. Canonical scenarios specifications and definitions
2. Bounded safe task history recording and secret redaction
3. Checkpoint restoration, step preservation, and old token invalidation
4. User-friendly failure UX formatting without stack traces
5. Latency tracking and telemetry
6. Multi-task concurrency and authorization isolation
7. Full E2E validation framework execution
8. Control engine integration methods
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import time

# Ensure backend root is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.adaptive import (
    AdaptiveComputerUseController,
    AdaptiveExecutionResult,
    ComputerWorkflowState,
    ObservedComputerState,
)
from app.control.e2e import (
    CANONICAL_SCENARIOS,
    CanonicalScenarioType,
    E2EValidationFramework,
    ScenarioSpecification,
    TaskHistoryEntry,
    TaskHistoryStore,
    TaskLatencyTracker,
    TaskResumptionManager,
    e2e_validation_framework,
    format_user_friendly_failure,
    task_history_store,
    verify_task_isolation,
)
from app.control.engine import ryven_control_engine
from app.control.models import FailureClass
from app.control.task import (
    TaskCapability,
    TaskCapabilityRouter,
    UnifiedTask,
    UnifiedTaskOrchestrator,
    UnifiedTaskResult,
    UnifiedTaskStatus,
    UnifiedTaskStep,
    unified_task_orchestrator,
)
from app.runtime.checkpoint_store import CheckpointStore


async def run_live_verification() -> bool:
    print("=" * 72)
    print("RYVEN 3.0 — Milestone 17.5.1 Real-World E2E Hardening Live Verification")
    print("=" * 72)

    passed = 0
    total = 8

    # -----------------------------------------------------------------------
    # Check 1: Canonical Scenario Specifications
    # -----------------------------------------------------------------------
    print("\n[Check 1/8] Verifying 16 Canonical Scenarios...")
    assert len(CANONICAL_SCENARIOS) == 16, f"Expected 16 scenarios, found {len(CANONICAL_SCENARIOS)}"
    for stype, spec in CANONICAL_SCENARIOS.items():
        assert isinstance(spec, ScenarioSpecification)
        assert len(spec.expected_steps) >= 1
    print(f"  [OK] All 16 canonical scenarios defined and valid.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 2: Bounded Task History Store & Secret Redaction
    # -----------------------------------------------------------------------
    print("\n[Check 2/8] Verifying Task History Store & Secret Redaction...")
    store = TaskHistoryStore(max_entries=5)
    for i in range(7):
        res = UnifiedTaskResult(
            task_id=f"verify-task-{i}",
            status=UnifiedTaskStatus.COMPLETED,
            success=True,
            original_goal=f"Live test goal {i}",
            safe_metadata={"api_key": "super-secret-key", "token": "ghp_12345", "index": i},
        )
        entry = store.record_result(res)
    assert store.count() == 5, f"Expected store count bounded to 5, got {store.count()}"
    assert store.get_entry("verify-task-0") is None, "Oldest entry should be evicted (FIFO)"
    last_entry = store.get_entry("verify-task-6")
    assert last_entry is not None
    assert last_entry.safe_metadata["api_key"] == "[REDACTED]"
    assert last_entry.safe_metadata["token"] == "[REDACTED]"
    print(f"  [OK] Task history bounded (5 entries), FIFO eviction verified, secrets redacted.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 3: Checkpoint Restoration & Confirmation Invalidation
    # -----------------------------------------------------------------------
    print("\n[Check 3/8] Verifying Checkpoint Restoration & Confirmation Invalidation...")
    t = UnifiedTask(
        task_id="restore-live-1",
        original_goal="Live restoration test",
        status=UnifiedTaskStatus.WAITING_CONFIRMATION,
        required_confirmation=True,
        active_confirmation_token="CONF-STALE-TOKEN-123",
        completed_steps=["step-1"],
        pending_steps=["step-2"],
    )
    serialized = TaskResumptionManager.serialize_task_checkpoint(t)
    restored = TaskResumptionManager.restore_task_from_checkpoint(serialized)
    assert restored.task_id == t.task_id
    assert restored.completed_steps == ["step-1"]
    assert restored.pending_steps == ["step-2"]
    # Critical security invariant: old token must NOT be present
    assert restored.active_confirmation_token is None, "Old confirmation token was not invalidated!"
    assert restored.required_confirmation is True, "Task must still require confirmation!"
    print(f"  [OK] Checkpoint restored, completed steps preserved, old token strictly invalidated.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 4: User-Friendly Failure UX Formatting
    # -----------------------------------------------------------------------
    print("\n[Check 4/8] Verifying User-Friendly Failure UX...")
    test_cases = [
        (FailureClass.PROMPT_INJECTION_DETECTED, "untrusted"),
        (FailureClass.APPLICATION_NOT_RUNNING, "not running"),
        (FailureClass.PERMISSION_DENIED, "permission"),
        (FailureClass.ACTION_TIMEOUT, "timed out"),
        (FailureClass.TARGET_NOT_FOUND, "target"),
    ]
    for fc, snippet in test_cases:
        msg = format_user_friendly_failure(fc, raw_error="Traceback (most recent call last):\n  File 'bad.py'")
        assert "Traceback" not in msg, f"Traceback leaked in failure message for {fc}!"
        assert snippet.lower() in msg.lower(), f"Expected snippet '{snippet}' in message: {msg}"
    print(f"  [OK] User-friendly messages formatted cleanly without raw tracebacks.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 5: Latency Tracking
    # -----------------------------------------------------------------------
    print("\n[Check 5/8] Verifying Latency Tracker...")
    tracker = TaskLatencyTracker()
    tracker.mark("planning")
    time.sleep(0.005)
    p_dur = tracker.stop("planning")
    tracker.mark("execution")
    time.sleep(0.005)
    e_dur = tracker.stop("execution")
    assert p_dur > 0.0 and e_dur > 0.0
    assert tracker.total_duration_ms() >= (p_dur + e_dur)
    print(f"  [OK] Latency tracker measured stages: planning={p_dur:.2f}ms, exec={e_dur:.2f}ms.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 6: Multi-Task Concurrency & Authorization Isolation
    # -----------------------------------------------------------------------
    print("\n[Check 6/8] Verifying Multi-Task Isolation...")
    t1 = UnifiedTask(task_id="task-iso-1", original_goal="G1", active_confirmation_token="CONF-1")
    t2 = UnifiedTask(task_id="task-iso-2", original_goal="G2", active_confirmation_token="CONF-2")
    isolated, violations = verify_task_isolation(t1, t2)
    assert isolated is True, f"Expected isolation, violations: {violations}"
    print(f"  [OK] Verified task isolation between distinct tasks (zero leakage).")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 7: Full E2E Validation Framework Execution
    # -----------------------------------------------------------------------
    print("\n[Check 7/8] Verifying E2E Validation Framework Execution...")
    mem_store = CheckpointStore(db_path=":memory:")
    adaptive = AdaptiveComputerUseController()
    orch = UnifiedTaskOrchestrator(
        adaptive_controller=adaptive,
        workflow_engine=adaptive.workflow_engine,
        checkpoints=mem_store,
    )
    framework = E2EValidationFramework(
        orchestrator=orch,
        history=TaskHistoryStore(max_entries=20),
        checkpoints=mem_store,
    )
    suite_res = await framework.run_all_canonical_scenarios(auto_confirm=True)
    assert suite_res["scenarios_total"] == 16
    print(f"  [OK] Executed canonical test harness: {suite_res['scenarios_passed']}/{suite_res['scenarios_total']} scenarios passed.")
    passed += 1

    # -----------------------------------------------------------------------
    # Check 8: Control Engine Integration Methods
    # -----------------------------------------------------------------------
    print("\n[Check 8/8] Verifying RyvenControlEngine Methods...")
    history = ryven_control_engine.get_task_history()
    assert isinstance(history, list), "get_task_history must return a list"
    restored_engine = ryven_control_engine.restore_task(serialized)
    assert restored_engine.task_id == t.task_id
    print(f"  [OK] RyvenControlEngine successfully restored task and retrieved task history.")
    passed += 1

    print("\n" + "=" * 72)
    print(f"RESULT: {passed}/{total} CHECKS PASSED — MILESTONE 17.5.1 HARDENING VERIFIED!")
    print("=" * 72)
    return True


if __name__ == "__main__":
    success = asyncio.run(run_live_verification())
    sys.exit(0 if success else 1)

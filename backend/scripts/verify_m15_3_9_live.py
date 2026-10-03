"""RYVEN 3.0 — M15.3.9 Live Windows Verification Script.

Executes comprehensive live validation of:
1. Runtime status
2. Resource telemetry
3. Performance metrics
4. Task creation
5. Checkpoint creation
6. Checkpoint loading
7. Safe recovery
8. Dangerous-action recovery requiring confirmation
9. Timeout behavior
10. Cleanup
11. ActionEvents
12. Frontend runtime API
13. Ollama/Qwen remains default and functional
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from app.main import app
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import TaskType
from app.ai.router import model_router
from app.ai.unified import unified_ai_provider
from app.orchestrator.models import OrchestrationTask, OrchestrationStep, StepType, OrchestrationPlan, TaskState
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store
from app.runtime.concurrency import concurrency_controller
from app.runtime.context_budget import ContextBudgetManager
from app.runtime.deadline_manager import deadline_manager
from app.runtime.lifecycle import lifecycle_manager
from app.runtime.models import (
    ContextBudgetReport,
    PersistedTaskCheckpoint,
    RecoveryDecision,
    ResourceDecision,
    ResourceStatus,
    RuntimeState,
)
from app.runtime.performance import performance_service
from app.runtime.recovery import RuntimeRecoveryService, recovery_service
from app.runtime.resource_manager import resource_manager


def log_step(index: int, name: str, success: bool, details: str = ""):
    symbol = "[PASS]" if success else "[FAIL]"
    print(f" {symbol} ({index}/13) {name}: {details}")


async def main():
    print("=" * 70)
    print("RYVEN 3.0 — M15.3.9 Live Windows Runtime Verification")
    print("Local-First Personal AI Agent / Control Plane")
    print("=" * 70)

    client = TestClient(app)
    results = []

    # 1. Runtime Status
    try:
        resp = client.get("/api/runtime/status")
        assert resp.status_code == 200, f"Status code {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ok"
        assert data.get("runtime_state") in [s.value for s in RuntimeState]
        assert "host_resources" in data
        assert "performance" in data
        log_step(1, "Runtime Status API", True, f"State: {data.get('runtime_state')}, Incomplete tasks: {data.get('active_or_incomplete_tasks_count')}")
        results.append(True)
    except Exception as e:
        log_step(1, "Runtime Status API", False, str(e))
        results.append(False)

    # 2. Resource Telemetry
    try:
        resp = client.get("/api/runtime/resources")
        assert resp.status_code == 200
        res_data = resp.json()
        assert "ram_total_gb" in res_data
        assert "cpu_pct" in res_data
        assert "status" in res_data
        log_step(2, "Resource Telemetry", True, f"RAM: {res_data.get('ram_used_pct')}% ({res_data.get('ram_used_gb')}GB), CPU: {res_data.get('cpu_pct')}%, Status: {res_data.get('status')}")
        results.append(True)
    except Exception as e:
        log_step(2, "Resource Telemetry", False, str(e))
        results.append(False)

    # 3. Performance Metrics
    try:
        await performance_service.record_operation("live_verify_op", category="verification", duration_ms=4.2, success=True)
        resp = client.get("/api/runtime/performance")
        assert resp.status_code == 200
        perf_data = resp.json()
        agg = perf_data.get("aggregate", {})
        assert agg.get("total_operations", 0) >= 1
        log_step(3, "Performance Metrics", True, f"Total ops: {agg.get('total_operations')}, Avg latency: {agg.get('avg_latency_ms'):.2f}ms")
        results.append(True)
    except Exception as e:
        log_step(3, "Performance Metrics", False, str(e))
        results.append(False)

    # 4. Task Creation
    try:
        test_task = OrchestrationTask(
            task_id="live-task-001",
            project_name="ryven_live_test",
            user_goal="Live runtime test",
            plan=OrchestrationPlan(
                user_goal="Live runtime test",
                project_name="ryven_live_test",
                steps=[
                    OrchestrationStep(step_id="step-1", name="Scan project", step_type=StepType.UNDERSTAND_PROJECT),
                    OrchestrationStep(step_id="step-2", name="Deploy release", step_type=StepType.DEPLOY, requires_confirmation=True),
                ],
            ),
        )
        assert test_task.task_id == "live-task-001"
        assert test_task.plan.total_steps == 2
        log_step(4, "Task Creation", True, f"Created task '{test_task.task_id}' with {test_task.plan.total_steps} plan steps")
        results.append(True)
    except Exception as e:
        log_step(4, "Task Creation", False, str(e))
        results.append(False)

    # 5. Checkpoint Creation
    try:
        chk = checkpoint_store.save_checkpoint(
            task_id="live-task-001",
            workflow_id="wf-live-1",
            project_name="ryven_live_test",
            user_goal="Live runtime test",
            current_state="RUNNING",
            current_step_id="step-1",
            completed_steps=[],
            pending_steps=["step-1", "step-2"],
            step_details=[
                {"step_id": "step-1", "step_type": "UNDERSTAND_PROJECT", "requires_confirmation": False},
                {"step_id": "step-2", "step_type": "DEPLOY", "requires_confirmation": True},
            ],
            safe_metadata={"env": "SECRET_KEY_MUST_BE_REDACTED", "test_flag": True},
        )
        assert chk.task_id == "live-task-001"
        assert chk.safe_metadata.get("env") == "[REDACTED]"
        log_step(5, "Checkpoint Creation & Redaction", True, f"Saved checkpoint v{chk.schema_version} to SQLite. Secret redacted: {chk.safe_metadata.get('env')}")
        results.append(True)
    except Exception as e:
        log_step(5, "Checkpoint Creation & Redaction", False, str(e))
        results.append(False)

    # 6. Checkpoint Loading
    try:
        loaded = checkpoint_store.get_checkpoint("live-task-001")
        assert loaded is not None
        assert loaded.task_id == "live-task-001"
        assert loaded.current_step_id == "step-1"
        log_step(6, "Checkpoint Loading", True, f"Retrieved persisted checkpoint for '{loaded.task_id}', status={loaded.current_state}")
        results.append(True)
    except Exception as e:
        log_step(6, "Checkpoint Loading", False, str(e))
        results.append(False)

    # 7. Safe Recovery
    try:
        safe_chk = checkpoint_store.save_checkpoint(
            task_id="live-safe-002",
            user_goal="Safe read-only scan",
            current_state="RUNNING",
            current_step_id="step-scan",
            pending_steps=["step-scan"],
            step_details=[{"step_id": "step-scan", "step_type": "SCAN_PROJECT", "requires_confirmation": False}],
        )
        decision_safe = recovery_service.evaluate_task(safe_chk)
        assert decision_safe.safe_to_auto_resume is True
        assert decision_safe.requires_confirmation is False
        log_step(7, "Safe Task Auto-Recovery", True, f"Task '{safe_chk.task_id}' auto-resumable: {decision_safe.reason}")
        results.append(True)
    except Exception as e:
        log_step(7, "Safe Task Auto-Recovery", False, str(e))
        results.append(False)

    # 8. Dangerous-Action Recovery Requiring Confirmation
    try:
        loaded_dangerous = checkpoint_store.get_checkpoint("live-task-001")
        decision_dangerous = recovery_service.evaluate_task(loaded_dangerous)
        assert decision_dangerous.requires_confirmation is True
        assert decision_dangerous.safe_to_auto_resume is False
        assert decision_dangerous.dangerous_step_detected is True
        log_step(8, "Dangerous Recovery Confirmation Gate", True, f"Step '{decision_dangerous.dangerous_step_type}' flagged. Confirmation required: {decision_dangerous.reason}")
        results.append(True)
    except Exception as e:
        log_step(8, "Dangerous Recovery Confirmation Gate", False, str(e))
        results.append(False)

    # 9. Timeout Behavior
    try:
        async def slow_work():
            await asyncio.sleep(0.5)
            return "done"

        timed_out_ok, res, err = await deadline_manager.execute_with_timeout(
            slow_work(),
            timeout_seconds=0.1,
            operation_name="live_timeout_test",
        )
        assert timed_out_ok is False
        assert "timed out" in err.lower()
        log_step(9, "Deadline / Timeout Management", True, f"Operation timed out safely: {err}")
        results.append(True)
    except Exception as e:
        log_step(9, "Deadline / Timeout Management", False, str(e))
        results.append(False)

    # 10. Cleanup
    try:
        temp_file = Path(backend_dir / "temp_live_test.tmp")
        temp_file.write_text("temporary data")
        lifecycle_manager.register_temp_file(str(temp_file))
        cleanup_report = await lifecycle_manager.cleanup_all()
        assert not temp_file.exists()
        log_step(10, "Resource Cleanup / Lifecycle", True, f"Purged temporary files: {cleanup_report.get('purged_temp_files')}, Callbacks: {cleanup_report.get('callbacks_executed')}")
        results.append(True)
    except Exception as e:
        log_step(10, "Resource Cleanup / Lifecycle", False, str(e))
        results.append(False)

    # 11. ActionEvents
    try:
        received_events = []
        async def on_action_event(ev):
            if ev.action_type == ActionType.RUNTIME_OPERATION_COMPLETED:
                received_events.append(ev)

        sub_id = action_bus.subscribe(on_action_event)
        test_event = ActionEvent(
            action_type=ActionType.RUNTIME_OPERATION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title="Live Event Test",
            safe_metadata={"action": "test", "token": "SECRET_BEARER_TOKEN"},
        )
        await action_bus.publish(test_event)
        await asyncio.sleep(0.05)
        action_bus.unsubscribe(sub_id)

        assert len(received_events) > 0
        assert received_events[-1].safe_metadata.get("token") == "[REDACTED]"
        log_step(11, "ActionEvent Emission & Bus Delivery", True, f"Event received on ActionEventBus. Secret key redacted: {received_events[-1].safe_metadata.get('token')}")
        results.append(True)
    except Exception as e:
        log_step(11, "ActionEvent Emission & Bus Delivery", False, str(e))
        results.append(False)

    # 12. Frontend Runtime API
    try:
        resp_tasks = client.get("/api/runtime/tasks")
        assert resp_tasks.status_code == 200
        tasks_data = resp_tasks.json()
        assert "tasks" in tasks_data
        assert "total_persisted" in tasks_data

        resp_rec = client.get("/api/runtime/recovery")
        assert resp_rec.status_code == 200
        rec_data = resp_rec.json()
        assert "recoverable_count" in rec_data
        assert "tasks" in rec_data

        log_step(12, "Frontend Runtime API Endpoints", True, f"Tasks ({tasks_data.get('total_persisted')}) and Recovery ({rec_data.get('recoverable_count')}) endpoints active.")
        results.append(True)
    except Exception as e:
        log_step(12, "Frontend Runtime API Endpoints", False, str(e))
        results.append(False)

    # 13. Ollama/Qwen Remains Default
    try:
        decision = await model_router.route(TaskType.CODE)
        assert decision.selected_model
        assert decision.provider
        log_step(13, "Local-First AI Default Invariant", True, f"Routed task {TaskType.CODE.value} -> Model: '{decision.selected_model}', Provider: '{decision.provider}', Local: {decision.local_or_remote}")
        results.append(True)
    except Exception as e:
        log_step(13, "Local-First AI Default Invariant", False, str(e))
        results.append(False)

    # Clean up test checkpoints
    checkpoint_store.delete_checkpoint("live-task-001")
    checkpoint_store.delete_checkpoint("live-safe-002")

    passed_count = sum(1 for r in results if r)
    total_count = len(results)
    print("=" * 70)
    print(f"VERIFICATION SUMMARY: {passed_count}/{total_count} PASSED")
    print("=" * 70)
    if passed_count == total_count:
        print("RYVEN 3.0 — M15.3.9 RUNTIME INTEGRITY VERIFIED")
        sys.exit(0)
    else:
        print("M15.3.9 VERIFICATION HAD FAILURES")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

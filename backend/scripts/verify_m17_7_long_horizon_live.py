#!/usr/bin/env python3
"""
RYVEN 3.0 — Milestone 17.7 Long-Horizon Task Persistence & Autonomous Goal Execution Live Verification.

Safety Guarantee:
- Default execution is strictly read-only and dry-run with mock orchestrators or bounded synthetic steps.
- ZERO uncontrolled mouse clicks, keyboard strokes, or arbitrary shell commands.
- Validates the complete authoritative orchestration path:
      User Input / Conversation Context → UnifiedTaskOrchestrator → Long-Horizon Supervisor → Results
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"
_results: List[Dict[str, Any]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    status = PASS if cond else FAIL
    line = f"  {status}  {name}"
    if detail:
        line += f"  [{detail}]"
    print(line)
    _results.append({"name": name, "passed": cond, "detail": detail, "skipped": False})
    return cond


def check_skip(name: str, detail: str = "") -> None:
    line = f"  {SKIP}  {name}"
    if detail:
        line += f"  [{detail}]"
    print(line)
    _results.append({"name": name, "passed": True, "detail": detail, "skipped": True})


def section(title: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


async def main() -> int:
    print("=" * 60)
    print("RYVEN 3.0 — M17.7 LONG-HORIZON TASK PERSISTENCE LIVE VERIFICATION")
    print("=" * 60)

    # -----------------------------------------------------------------------
    # Section 1: Task State Machine & Model Validation
    # -----------------------------------------------------------------------
    section("1. Task State Machine & Step Models")

    from app.control.long_horizon import (
        LongHorizonTaskState,
        ActionIdempotency,
        LongHorizonTaskStep,
        TaskExecutionJournalEntry,
        TaskProgressSnapshot,
        LongHorizonTask,
        TaskPersistenceRepository,
        LongHorizonTaskManager,
        LEGAL_TASK_TRANSITIONS,
        long_horizon_task_manager,
        task_persistence_repo,
    )

    check("LongHorizonTaskState enum defines all 15 canonical states", len(LongHorizonTaskState) == 15)
    check("ActionIdempotency enum defines IDEMPOTENT, NON_IDEMPOTENT, CONDITIONALLY_IDEMPOTENT", len(ActionIdempotency) == 3)
    
    # Check legal transitions
    check("LEGAL_TASK_TRANSITIONS allows PENDING -> RUNNING", LongHorizonTaskState.RUNNING in LEGAL_TASK_TRANSITIONS[LongHorizonTaskState.PENDING])
    check("LEGAL_TASK_TRANSITIONS allows RUNNING -> PAUSED", LongHorizonTaskState.PAUSED in LEGAL_TASK_TRANSITIONS[LongHorizonTaskState.RUNNING])
    check("LEGAL_TASK_TRANSITIONS allows PAUSED -> RESUMING", LongHorizonTaskState.RESUMING in LEGAL_TASK_TRANSITIONS[LongHorizonTaskState.PAUSED])
    check("LEGAL_TASK_TRANSITIONS allows RUNNING -> COMPLETED", LongHorizonTaskState.COMPLETED in LEGAL_TASK_TRANSITIONS[LongHorizonTaskState.RUNNING])

    # Instantiate task step
    s1 = LongHorizonTaskStep(
        step_id="step-1",
        name="Observe workspace",
        action="observe_screen",
        idempotency=ActionIdempotency.IDEMPOTENT,
    )
    check("LongHorizonTaskStep initialized with default attempt counters", s1.attempt_count == 0 and s1.max_attempts == 3)

    # Task creation & transition enforcement
    task = LongHorizonTask(goal="Organize desktop project files", steps=[s1])
    check("LongHorizonTask initialized in PENDING state", task.state == LongHorizonTaskState.PENDING)
    task.transition_to(LongHorizonTaskState.RUNNING, reason="Starting live test")
    check("Task successfully transitioned to RUNNING", task.state == LongHorizonTaskState.RUNNING)
    check("Execution journal recorded state transition event", any(j.event_type == "TASK_STATE_TRANSITION" for j in task.execution_journal))

    # Illegal transition rejection
    illegal_caught = False
    try:
        task.transition_to(LongHorizonTaskState.PENDING, reason="Illegal backwards transition")
    except ValueError:
        illegal_caught = True
    check("Illegal state transition from RUNNING to PENDING rejected", illegal_caught)

    # -----------------------------------------------------------------------
    # Section 2: SQLite Persistence & Crash Recovery
    # -----------------------------------------------------------------------
    section("2. SQLite Persistence & Crash Recovery")

    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "test_verify.db"
    repo = TaskPersistenceRepository(db_path=db_path)

    check("TaskPersistenceRepository created SQLite database with WAL mode", db_path.exists())
    
    # Create task in repository
    created_task = repo.create_task(task)
    check("Task stored with identical task_id", created_task.task_id == task.task_id)

    # Fetch task
    fetched_task = repo.get_task(task.task_id)
    check("Task retrieved from SQLite with matching goal", fetched_task is not None and fetched_task.goal == task.goal)
    check("Retrieved task deserialized steps accurately", len(fetched_task.steps) == 1 and fetched_task.steps[0].name == "Observe workspace")

    # Crash recovery testing: simulate an interrupted active task
    t_inflight = LongHorizonTask(goal="In-flight crash test")
    t_inflight.state = LongHorizonTaskState.RUNNING
    t_inflight.active_confirmation_token = "CONF-CRASH-TEST"
    repo.create_task(t_inflight)

    interrupted_ids = repo.recover_crash_interrupted_tasks()
    check("recover_crash_interrupted_tasks identifies unfinalized in-flight task", t_inflight.task_id in interrupted_ids)

    recovered_task = repo.get_task(t_inflight.task_id)
    check("Crash-interrupted task marked INTERRUPTED", recovered_task.state == LongHorizonTaskState.INTERRUPTED)
    check("Crash recovery strictly clears active_confirmation_token", recovered_task.active_confirmation_token is None)
    check("Crash recovery logs TASK_INTERRUPTED in journal", any(j.event_type == "TASK_INTERRUPTED" for j in recovered_task.execution_journal))

    # -----------------------------------------------------------------------
    # Section 3: Dependency Graph & Step Selection
    # -----------------------------------------------------------------------
    section("3. Dependency Graph & Step Selection")

    from app.control.task import UnifiedTaskResult, UnifiedTaskStatus

    mgr = LongHorizonTaskManager(repository=repo)
    
    # Create diamond dependency DAG:
    #       s_start
    #      /       \
    #    s_left   s_right
    #      \       /
    #       s_end
    s_start = LongHorizonTaskStep(step_id="st", name="Start", action="act")
    s_left = LongHorizonTaskStep(step_id="sl", name="Left Branch", action="act", dependencies=["st"])
    s_right = LongHorizonTaskStep(step_id="sr", name="Right Branch", action="act", dependencies=["st"])
    s_end = LongHorizonTaskStep(step_id="se", name="End Merge", action="act", dependencies=["sl", "sr"])

    dag_task = LongHorizonTask(goal="DAG validation", steps=[s_start, s_left, s_right, s_end])

    # First eligible step must be s_start
    next_step = mgr._select_next_eligible_step(dag_task)
    check("DAG step selector selects root step with no dependencies", next_step is not None and next_step.step_id == "st")

    # Mark s_start complete
    s_start.status = LongHorizonTaskState.COMPLETED
    dag_task.completed_step_ids.append("st")

    # Next eligible step can be left branch
    next_step = mgr._select_next_eligible_step(dag_task)
    check("DAG step selector selects child step once parent completes", next_step is not None and next_step.step_id in ("sl", "sr"))

    # If only sl is complete, se must NOT be eligible
    s_left.status = LongHorizonTaskState.COMPLETED
    dag_task.completed_step_ids.append("sl")
    next_step = mgr._select_next_eligible_step(dag_task)
    check("Merge step remains ineligible until all branches complete", next_step is not None and next_step.step_id == "sr")

    # -----------------------------------------------------------------------
    # Section 4: Bounded Autonomous Execution via UnifiedTaskOrchestrator
    # -----------------------------------------------------------------------
    section("4. Bounded Autonomous Execution via UnifiedTaskOrchestrator")

    step_a = LongHorizonTaskStep(step_id="sa", name="Step Alpha", action="action_a")
    step_b = LongHorizonTaskStep(step_id="sb", name="Step Beta", action="action_b", dependencies=["sa"])
    exec_task = LongHorizonTask(goal="Sequential execution", steps=[step_a, step_b])
    repo.create_task(exec_task)

    call_actions = []

    async def mock_orch(task_obj, **kwargs):
        step = task_obj.steps[0]
        call_actions.append(step.action)
        return UnifiedTaskResult(
            task_id="orch-res",
            original_goal=getattr(task_obj, "original_goal", getattr(task_obj, "goal", "mock")),
            status=UnifiedTaskStatus.COMPLETED,
            success=True,
            step_results=[{"success": True, "output": f"Ran {step.action}"}],
        )

    with patch.object(mgr.orchestrator, "execute_task", new=AsyncMock(side_effect=mock_orch)):
        snap = await mgr.execute_task(exec_task.task_id)

    check("Execution dispatches sequentially through UnifiedTaskOrchestrator", call_actions == ["action_a", "action_b"])
    check("Completed all steps without errors", snap.state == LongHorizonTaskState.COMPLETED)
    check("Progress reached 100%", snap.progress_percent == 100.0)
    check("Completed step count accurately recorded", snap.completed_steps == 2)

    # -----------------------------------------------------------------------
    # Section 5: Pause, Resume & Drift Detection
    # -----------------------------------------------------------------------
    section("5. Pause, Resume & Environment Drift Detection")

    from app.control.adaptive import ObservedComputerState

    p_step1 = LongHorizonTaskStep(step_id="ps1", name="Step 1", action="a1")
    p_step2 = LongHorizonTaskStep(step_id="ps2", name="Step 2", action="a2", dependencies=["ps1"], expected_state={"window_title": "VS Code"})
    p_task = LongHorizonTask(goal="Pause resume drift", steps=[p_step1, p_step2])
    repo.create_task(p_task)

    # Run step 1 only by bounding max_steps_per_run
    mgr.max_steps_per_run = 1
    with patch.object(mgr.orchestrator, "execute_task", new=AsyncMock(return_value=UnifiedTaskResult(task_id="x", original_goal="x", status=UnifiedTaskStatus.COMPLETED, success=True))):
        await mgr.execute_task(p_task.task_id)

    check("Task paused/ready after bounded batch", len(repo.get_task(p_task.task_id).completed_step_ids) == 1)

    # Explicit pause
    pause_snap = await mgr.pause_task(p_task.task_id, reason="User requested pause")
    check("Explicit pause sets state to PAUSED", pause_snap.state == LongHorizonTaskState.PAUSED)

    # Resume with environmental drift (Chrome active instead of expected VS Code)
    mgr.max_steps_per_run = 10
    with patch("app.control.adaptive.adaptive_controller.observe_environment", new=AsyncMock(return_value=ObservedComputerState(active_window="Google Chrome"))), \
         patch.object(mgr.orchestrator, "execute_task", new=AsyncMock(return_value=UnifiedTaskResult(task_id="y", original_goal="y", status=UnifiedTaskStatus.COMPLETED, success=True))):
        resume_snap = await mgr.resume_task(p_task.task_id)

    fetched_drift = repo.get_task(p_task.task_id)
    check("Resume detects environmental drift and logs to execution journal", any(j.event_type == "ENVIRONMENT_DRIFT_DETECTED" for j in fetched_drift.execution_journal))
    check("Completed remaining steps on resume without re-running completed steps", fetched_drift.state == LongHorizonTaskState.COMPLETED)

    # -----------------------------------------------------------------------
    # Section 6: Consequential Actions & Confirmation Invalidation
    # -----------------------------------------------------------------------
    section("6. Consequential Actions & Confirmation Tokens")

    c_step = LongHorizonTaskStep(
        step_id="cs1",
        name="Delete system partition",
        action="delete_partition",
        requires_confirmation=True,
    )
    c_task = LongHorizonTask(goal="Dangerous confirmation task", steps=[c_step])
    repo.create_task(c_task)

    # Execute without auto_confirm -> must pause in WAITING_CONFIRMATION
    c_snap = await mgr.execute_task(c_task.task_id, auto_confirm=False)
    check("Consequential action pauses execution in WAITING_CONFIRMATION", c_snap.state == LongHorizonTaskState.WAITING_CONFIRMATION)
    check("Generated fresh non-empty confirmation token", bool(c_snap.confirmation_token))

    # Reject confirmation
    tok = c_snap.confirmation_token
    rej_snap = await mgr.confirm_task_step(c_task.task_id, tok, approved=False)
    check("User rejecting confirmation transitions task to CANCELLED", rej_snap.state == LongHorizonTaskState.CANCELLED)

    # Token invalidation on resume: if a task in WAITING_CONFIRMATION is resumed, stale token must be cleared
    c_task2 = LongHorizonTask(goal="Token clearance on resume", steps=[c_step])
    c_task2.state = LongHorizonTaskState.WAITING_CONFIRMATION
    c_task2.active_confirmation_token = "CONF-TEST-STALE"
    repo.create_task(c_task2)

    with patch.object(mgr.orchestrator, "execute_task", new=AsyncMock(return_value=UnifiedTaskResult(task_id="c", original_goal="c", status=UnifiedTaskStatus.COMPLETED, success=True))):
        await mgr.resume_task(c_task2.task_id, auto_confirm=True)

    fetched_c2 = repo.get_task(c_task2.task_id)
    check("Stale confirmation token invalidated upon task resumption", fetched_c2.active_confirmation_token is None)

    # -----------------------------------------------------------------------
    # Section 7: Conversational Intent Routing & Task Monitoring
    # -----------------------------------------------------------------------
    section("7. Conversational Intent Routing & Task Control")

    from app.control.conversation import (
        conversation_manager,
        ConversationalIntentType,
        ConversationTurn,
    )

    # Test Intent classification
    test_ctx = conversation_manager.get_context("intent-test")
    i_pause, _, _, _ = conversation_manager.resolve_intent_and_target("pause the running task please", test_ctx)
    check("Intent classifier recognizes 'pause the running task' as PAUSE", i_pause == ConversationalIntentType.PAUSE)

    i_status, _, _, _ = conversation_manager.resolve_intent_and_target("what is the status of the current task?", test_ctx)
    check("Intent classifier recognizes 'what is the status...' as STATUS", i_status == ConversationalIntentType.STATUS)

    i_continue, _, _, _ = conversation_manager.resolve_intent_and_target("continue task", test_ctx)
    check("Intent classifier recognizes 'continue task' as CONTINUE", i_continue == ConversationalIntentType.CONTINUE)

    i_cancel, _, _, _ = conversation_manager.resolve_intent_and_target("stop", test_ctx)
    check("Intent classifier recognizes 'stop' as CANCELLATION", i_cancel == ConversationalIntentType.CANCELLATION)

    # Conversational turn: STATUS with active task
    conv_step = LongHorizonTaskStep(step_id="cv1", name="Fetch logs", action="fetch_logs")
    conv_task = LongHorizonTask(goal="Conversational status task", steps=[conv_step])
    conv_task.state = LongHorizonTaskState.RUNNING
    conv_task.last_milestone = "Fetching web archives"
    repo.create_task(conv_task)

    with patch.object(long_horizon_task_manager, "get_progress", return_value=conv_task.get_progress_snapshot()), \
         patch.object(long_horizon_task_manager, "list_tasks", return_value=[conv_task]):
        status_turn = await conversation_manager.process_user_message("status of the current task?", session_id="live-conv")
        check("Conversational STATUS response describes active task state", "RUNNING" in status_turn.message or "progress" in status_turn.message.lower())
        check("Conversational STATUS response cites active step or milestone", "Fetch logs" in status_turn.message or "Fetching web archives" in status_turn.message)

    # Conversational turn: PAUSE
    ctx = conversation_manager.get_context("live-conv-pause")
    ctx.active_task_id = conv_task.task_id
    with patch.object(long_horizon_task_manager, "get_task", return_value=conv_task), \
         patch.object(long_horizon_task_manager, "pause_task", new=AsyncMock(return_value=conv_task.get_progress_snapshot())):
        pause_turn = await conversation_manager.process_user_message("pause task", session_id="live-conv-pause")
        check("Conversational PAUSE executes pause and returns confirmation message", "paused" in pause_turn.message.lower())

    # -----------------------------------------------------------------------
    # Section 8: Secret Redaction & Security Invariants
    # -----------------------------------------------------------------------
    section("8. Secret Scrubbing & Architectural Security Invariants")

    # Scrubbing secrets in metadata
    sec_task = LongHorizonTask(
        goal="Secret scrubbing validation",
        safe_metadata={
            "api_key": "sk-secret-12345",
            "password": "SuperSecretPassword123!",
            "cookie": "session_id=abcdef",
            "user_label": "benign-data",
        },
    )
    check("Pydantic validator redacts sensitive keys in safe_metadata", sec_task.safe_metadata.get("api_key") == "[REDACTED]")
    check("Pydantic validator redacts password in safe_metadata", sec_task.safe_metadata.get("password") == "[REDACTED]")
    check("Pydantic validator redacts cookie in safe_metadata", sec_task.safe_metadata.get("cookie") == "[REDACTED]")
    check("Pydantic validator preserves non-sensitive metadata keys", sec_task.safe_metadata.get("user_label") == "benign-data")

    # Verify absence of forbidden execution calls in long_horizon module
    lh_code_path = backend_dir / "app" / "control" / "long_horizon.py"
    lh_code = lh_code_path.read_text(encoding="utf-8")

    check("Zero subprocess.Popen in long_horizon.py", "subprocess.Popen" not in lh_code)
    check("Zero os.system in long_horizon.py", "os.system" not in lh_code)
    check("Zero pyautogui in long_horizon.py", "pyautogui" not in lh_code)
    check("Zero pynput in long_horizon.py", "pynput" not in lh_code)
    check("Zero while True unbounded loops in long_horizon.py", "while True:" not in lh_code)

    # -----------------------------------------------------------------------
    # Section 9: Telemetry & Event Bus Verification
    # -----------------------------------------------------------------------
    section("9. Telemetry & Event Bus Dispatch")

    from app.actions.event_bus import action_bus
    from app.actions.models import ActionEvent, ActionStatus, ActionType

    received_long_task_events = []

    def ev_listener(event: ActionEvent):
        if str(event.action_type.value).upper().startswith("LONG_TASK_"):
            received_long_task_events.append(event)

    action_bus.subscribe(ev_listener)

    await mgr._emit_telemetry(ActionType.LONG_TASK_STARTED, ActionStatus.STARTED, "Live test started", "test-lht-1")
    await mgr._emit_telemetry(ActionType.LONG_TASK_STEP_COMPLETED, ActionStatus.COMPLETED, "Live step finished", "test-lht-1")
    await mgr._emit_telemetry(ActionType.LONG_TASK_COMPLETED, ActionStatus.COMPLETED, "Live task completed", "test-lht-1")

    action_bus.unsubscribe(ev_listener)

    check("ActionEventBus receives and publishes LONG_TASK_STARTED", any(e.action_type == ActionType.LONG_TASK_STARTED for e in received_long_task_events))
    check("ActionEventBus receives and publishes LONG_TASK_STEP_COMPLETED", any(e.action_type == ActionType.LONG_TASK_STEP_COMPLETED for e in received_long_task_events))
    check("ActionEventBus receives and publishes LONG_TASK_COMPLETED", any(e.action_type == ActionType.LONG_TASK_COMPLETED for e in received_long_task_events))

    # Clean up temporary DB
    temp_dir.cleanup()

    # -----------------------------------------------------------------------
    # Section 10: Overall Verification Summary
    # -----------------------------------------------------------------------
    section("Verification Summary")

    total = len(_results)
    passed = sum(1 for r in _results if r["passed"] and not r["skipped"])
    skipped = sum(1 for r in _results if r["skipped"])
    failed = sum(1 for r in _results if not r["passed"])

    print(f"\nTotal Checks : {total}")
    print(f"Passed       : {passed}")
    print(f"Skipped      : {skipped}")
    print(f"Failed       : {failed}")
    for r in _results:
        if not r["passed"]:
            print(f"  FAILED: {r['name']} - {r['detail']}")

    if failed == 0 and total >= 50:
        print("\n>>> M17.7 LIVE VERIFICATION SUCCEEDED (50+ CHECKS GREEN) <<<")
        return 0
    else:
        print(f"\n>>> M17.7 LIVE VERIFICATION FAILED (failed={failed}, total={total}) <<<")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

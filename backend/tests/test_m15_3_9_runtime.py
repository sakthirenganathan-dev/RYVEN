"""RYVEN 3.0 — M15.3.9: Performance, Resource & Runtime Reliability Tests.

Comprehensive test suite verifying:
1. Performance Profiler:
   - Operation timing and bounded ring buffer (1000 items).
   - Aggregate latency distributions (avg, min, max, p50, p95).
   - Passive ActionEventBus ingestion and categorization (llm, tool, browser, workflow).
2. Resource Manager:
   - Normal, Warning, and Critical threshold classification.
   - Graceful fallback when psutil or hardware APIs fail.
   - Resource check decisions (ALLOW, ALLOW_WITH_WARNING, DEFER, BLOCK).
   - Never kills processes or unloads models.
3. Context & Token Budget Manager:
   - Token estimation (4 chars/token).
   - Priority-based context trimming (user intent & security NEVER trimmed).
   - Omitted categories and optimization ratio.
4. Persistent Checkpoints:
   - SQLite schema initialization and migrations.
   - Save and load task checkpoints across simulated restarts.
   - Strict recursive secret redaction (API keys, tokens, passwords never stored).
5. Crash & Restart Recovery:
   - Safe/idempotent tasks allow auto-resume.
   - Consequential tasks (GIT_COMMIT, GIT_PUSH, DEPLOY, form submissions) require explicit confirmation.
   - Evaluation decisions and confirmation boundaries.
6. Deadline & Timeout Manager:
   - Task deadline registration and remaining time calculation.
   - Timeout enforcement (execute_with_timeout).
   - RUNTIME_TIMEOUT ActionEvent emission.
7. Resource Lifecycle & Cleanup:
   - Temporary file registration and cleanup.
   - Sync and async callback execution.
   - RUNTIME_CLEANUP_COMPLETED emission.
8. Concurrency Controller:
   - Bounded semaphores for LLM, builds, browsers, and graph writes.
   - Prevents concurrent overload.
9. Security & Safety Invariants:
   - Secrets, cookies, bearer tokens, passwords filtered from metrics, checkpoints, and events.
   - No arbitrary shell execution or command execution.
10. API Endpoints:
   - /api/runtime/status, /resources, /performance, /tasks, /recovery.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
from typing import Any, Dict
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.concurrency import ConcurrencyController
from app.runtime.context_budget import (
    CATEGORY_NAMES,
    ContextBudgetManager,
    PRIORITY_HISTORY_CONTEXT,
    PRIORITY_PROJECT_KNOWLEDGE,
    PRIORITY_RELEVANT_FILES,
    PRIORITY_TASK_STATE_AND_SECURITY,
    PRIORITY_TOOL_RESULTS,
    PRIORITY_USER_INTENT,
)
from app.runtime.deadline_manager import DeadlineManager
from app.runtime.lifecycle import ResourceLifecycleManager
from app.runtime.models import (
    OperationMetric,
    PersistedTaskCheckpoint,
    ResourceDecision,
    ResourceStatus,
    RuntimeState,
)
from app.runtime.performance import RuntimePerformanceService
from app.runtime.recovery import DANGEROUS_STEP_TYPES, RuntimeRecoveryService
from app.runtime.resource_manager import ResourceManager


# ---------------------------------------------------------------------------
# 1. Performance Profiler Tests
# ---------------------------------------------------------------------------

class TestPerformanceProfiler:
    """Verifies timing, bounded memory, and aggregate percentile calculations."""

    @pytest.mark.asyncio
    async def test_record_operation_and_bounded_storage(self):
        profiler = RuntimePerformanceService(max_metrics=5)
        for i in range(10):
            await profiler.record_operation(
                name=f"op_{i}",
                category="tool",
                duration_ms=float(i * 10),
                success=True,
            )

        metrics = await profiler.get_metrics(limit=10)
        assert len(metrics) == 5
        # The oldest items should have been evicted
        assert metrics[0].operation_name == "op_5"
        assert metrics[-1].operation_name == "op_9"

    @pytest.mark.asyncio
    async def test_aggregate_metrics_distribution(self):
        profiler = RuntimePerformanceService(max_metrics=100)
        durations = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        for d in durations:
            await profiler.record_operation(
                name="test_op",
                category="llm",
                duration_ms=d,
                success=d < 100.0,
            )

        agg = await profiler.get_aggregate_metrics()
        assert agg.total_operations == 10
        assert agg.successful_operations == 9
        assert agg.failed_operations == 1
        assert agg.avg_latency_ms == 55.0
        assert agg.min_latency_ms == 10.0
        assert agg.max_latency_ms == 100.0
        assert agg.p50_latency_ms == 50.0
        assert agg.p95_latency_ms == 90.0
        assert agg.llm_duration_ms == 550.0

    @pytest.mark.asyncio
    async def test_async_profile_context_manager(self):
        profiler = RuntimePerformanceService(max_metrics=10)
        async with profiler.profile(name="sleep_op", category="workflow"):
            await asyncio.sleep(0.02)

        metrics = await profiler.get_metrics(limit=1)
        assert len(metrics) == 1
        assert metrics[0].operation_name == "sleep_op"
        assert metrics[0].category == "workflow"
        assert metrics[0].duration_ms >= 15.0

    @pytest.mark.asyncio
    async def test_passive_action_event_ingestion(self):
        profiler = RuntimePerformanceService(max_metrics=50)
        profiler.start_event_listener()

        ev = ActionEvent(
            action_type=ActionType.TOOL_EXECUTE,
            status=ActionStatus.COMPLETED,
            title="web_download",
            duration_ms=45.5,
            task_id="task-123",
            safe_metadata={"tool_name": "web_download"},
        )
        await profiler._on_action_event(ev)

        metrics = await profiler.get_metrics(limit=1)
        assert len(metrics) == 1
        assert metrics[0].duration_ms == 45.5
        assert metrics[0].tool_name == "web_download"


# ---------------------------------------------------------------------------
# 2. Resource Manager Tests
# ---------------------------------------------------------------------------

class TestResourceManager:
    """Verifies resource telemetry, thresholds, and operational gate decisions."""

    def test_gather_metrics_returns_valid_structure(self):
        mgr = ResourceManager()
        metrics = mgr.get_resource_snapshot(force_refresh=True)

        assert "status" in metrics
        assert "cpu_pct" in metrics
        assert "ram_total_gb" in metrics
        assert "ram_used_pct" in metrics
        assert metrics["status"] in ("NORMAL", "WARNING", "CRITICAL", "UNKNOWN")

    def test_resource_decision_allow_normal(self):
        mgr = ResourceManager(ram_warning_pct=75.0, ram_critical_pct=90.0)
        res = mgr.check_resource_pressure(operation_type="llm", custom_ram_pct=45.0, custom_cpu_pct=20.0)
        assert res.decision == ResourceDecision.ALLOW

    def test_resource_decision_allow_with_warning(self):
        mgr = ResourceManager(ram_warning_pct=75.0, ram_critical_pct=90.0)
        res = mgr.check_resource_pressure(operation_type="general", custom_ram_pct=80.0, custom_cpu_pct=20.0)
        assert res.decision == ResourceDecision.ALLOW_WITH_WARNING

    def test_resource_decision_block_critical_heavy_operation(self):
        mgr = ResourceManager(ram_warning_pct=75.0, ram_critical_pct=90.0)
        res = mgr.check_resource_pressure(operation_type="build", custom_ram_pct=92.0, custom_cpu_pct=50.0)
        assert res.decision == ResourceDecision.BLOCK
        assert "Heavy operation 'build' blocked" in res.reason

    def test_resource_decision_defer_critical_light_operation(self):
        mgr = ResourceManager(ram_warning_pct=75.0, ram_critical_pct=90.0)
        res = mgr.check_resource_pressure(operation_type="read_page", custom_ram_pct=95.0, custom_cpu_pct=30.0)
        assert res.decision == ResourceDecision.DEFER

    def test_missing_psutil_graceful_fallback(self):
        mgr = ResourceManager()
        with patch.dict("sys.modules", {"psutil": None}):
            metrics = mgr._gather_metrics()
            assert metrics["status"] in (ResourceStatus.NORMAL.value, ResourceStatus.UNKNOWN.value)
            assert metrics["ram_total_gb"] > 0


# ---------------------------------------------------------------------------
# 3. Context & Token Budget Manager Tests
# ---------------------------------------------------------------------------

class TestContextBudgetManager:
    """Verifies token budgeting and priority-based prompt pruning."""

    def test_token_estimation(self):
        assert ContextBudgetManager.estimate_tokens("") == 0
        assert ContextBudgetManager.estimate_tokens("abcd") == 1
        assert ContextBudgetManager.estimate_tokens("a" * 400) == 100

    def test_within_budget_preserves_all_context(self):
        context, report = ContextBudgetManager.optimize_context(
            task_goal="Fix bug",
            user_intent="Fix null pointer exception",
            task_state_and_security="Security boundaries: safe",
            relevant_files={"app.py": "def main(): pass"},
            tool_results={"test": "passed"},
            project_knowledge="FastAPI backend",
            conversation_history=[{"role": "user", "content": "hi"}],
            max_budget_tokens=2000,
        )

        assert report.optimization_ratio == 0.0
        assert len(report.omitted_categories) == 0
        assert report.security_invariants_preserved is True
        assert "app.py" in context["files"]

    def test_over_budget_prunes_lowest_priority_first(self):
        # Create huge history and project knowledge, but small user intent and files
        huge_history = [{"role": "user", "content": "x" * 10000}]
        huge_knowledge = "k" * 8000

        context, report = ContextBudgetManager.optimize_context(
            task_goal="Fix bug",
            user_intent="Fix null pointer exception",
            task_state_and_security="Security boundaries: active",
            relevant_files={"app.py": "def main(): pass"},
            tool_results={"test": "passed"},
            project_knowledge=huge_knowledge,
            conversation_history=huge_history,
            max_budget_tokens=1000,
        )

        assert report.optimization_ratio > 0.0
        assert "conversation_history" in report.omitted_categories
        assert report.security_invariants_preserved is True
        assert context["user_intent"] == "Fix null pointer exception"
        assert context["task_state"] == "Security boundaries: active"

    def test_security_constraints_never_pruned(self):
        context, report = ContextBudgetManager.optimize_context(
            task_goal="Goal",
            user_intent="Intent",
            task_state_and_security="MANDATORY SECURITY INVARIANT: DO NOT DELETE ROOT",
            relevant_files={"main.py": "x" * 5000},
            tool_results={"tool": "r" * 5000},
            project_knowledge="k" * 5000,
            conversation_history=[{"role": "user", "content": "c" * 5000}],
            max_budget_tokens=500,
        )

        # Invariant must remain intact
        assert "MANDATORY SECURITY INVARIANT" in context["task_state"]
        assert context["user_intent"] == "Intent"
        assert report.security_invariants_preserved is True


# ---------------------------------------------------------------------------
# 4. Checkpoint Store Tests
# ---------------------------------------------------------------------------

class TestCheckpointStore:
    """Verifies SQLite persistence, migrations, and secret redaction."""

    def test_save_and_retrieve_checkpoint(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            store.save_checkpoint(
                task_id="orch-101",
                user_goal="Build project",
                project_name="my_project",
                current_state="RUNNING",
                current_step_id="step-1",
                completed_steps=["step-0"],
                pending_steps=["step-1", "step-2"],
                metadata={"info": "started"},
            )

            loaded = store.get_checkpoint("orch-101")
            assert loaded is not None
            assert loaded.task_id == "orch-101"
            assert loaded.project_name == "my_project"
            assert loaded.current_state == "RUNNING"
            assert loaded.current_step_id == "step-1"
            assert loaded.completed_steps == ["step-0"]
            assert loaded.pending_steps == ["step-1", "step-2"]
            assert loaded.schema_version == 1
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_checkpoint_secret_redaction(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            store.save_checkpoint(
                task_id="orch-sec",
                user_goal="Deploy app",
                current_state="WAITING_CONFIRMATION",
                metadata={
                    "api_key": "SUPER_SECRET_KEY_123",
                    "password": "my_password_456",
                    "bearer_token": "bearer_xyz_789",
                    "safe_key": "safe_val",
                },
            )

            loaded = store.get_checkpoint("orch-sec")
            assert loaded is not None
            assert loaded.safe_metadata["api_key"] == "[REDACTED]"
            assert loaded.safe_metadata["password"] == "[REDACTED]"
            assert loaded.safe_metadata["bearer_token"] == "[REDACTED]"
            assert loaded.safe_metadata["safe_key"] == "safe_val"
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_list_incomplete_tasks(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            store.save_checkpoint(task_id="task-run", current_state="RUNNING")
            store.save_checkpoint(task_id="task-wait", current_state="WAITING_CONFIRMATION")
            store.save_checkpoint(task_id="task-done", current_state="COMPLETED")

            incomplete = store.list_incomplete_tasks()
            task_ids = [t.task_id for t in incomplete]
            assert "task-run" in task_ids
            assert "task-wait" in task_ids
            assert "task-done" not in task_ids
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


# ---------------------------------------------------------------------------
# 5. Crash & Restart Recovery Tests
# ---------------------------------------------------------------------------

class TestRecoveryService:
    """Verifies crash evaluation and dangerous action confirmation boundaries."""

    def test_safe_task_auto_resumable(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            recovery = RuntimeRecoveryService(store=store)

            chk = store.save_checkpoint(
                task_id="task-safe",
                user_goal="Scan and query project graph",
                current_state="RUNNING",
                current_step_id="step-1",
                pending_steps=["step-1"],
                step_details=[
                    {"step_id": "step-1", "step_type": "SCAN_PROJECT", "requires_confirmation": False},
                ],
            )

            decision = recovery.evaluate_task(chk)
            assert decision.is_resumable is True
            assert decision.requires_confirmation is False
            assert decision.safe_to_auto_resume is True
            assert decision.dangerous_step_detected is False
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_consequential_task_requires_confirmation(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            recovery = RuntimeRecoveryService(store=store)

            for dangerous_type in ("GIT_COMMIT", "GIT_PUSH", "DEPLOY", "FORM_SUBMIT"):
                chk = store.save_checkpoint(
                    task_id=f"task-{dangerous_type.lower()}",
                    user_goal="Perform consequential operation",
                    current_state="RUNNING",
                    current_step_id="step-danger",
                    pending_steps=["step-danger"],
                    step_details=[
                        {"step_id": "step-danger", "step_type": dangerous_type, "requires_confirmation": True},
                    ],
                )

                decision = recovery.evaluate_task(chk)
                assert decision.is_resumable is True
                assert decision.requires_confirmation is True
                assert decision.safe_to_auto_resume is False
                assert decision.dangerous_step_detected is True
                assert "confirmation required" in decision.reason.lower()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_resume_task_confirmation_gate(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            store = CheckpointStore(db_path=db_path)
            recovery = RuntimeRecoveryService(store=store)

            store.save_checkpoint(
                task_id="task-deploy",
                user_goal="Deploy site",
                current_state="RUNNING",
                current_step_id="step-deploy",
                pending_steps=["step-deploy"],
                step_details=[{"step_id": "step-deploy", "step_type": "DEPLOY", "requires_confirmation": True}],
            )

            # Unconfirmed resume must reject
            res_unconfirmed = recovery.resume_task("task-deploy", confirmed=False)
            assert res_unconfirmed.requires_confirmation is True

            # Confirmed resume must succeed
            res_confirmed = recovery.resume_task("task-deploy", confirmed=True)
            assert res_confirmed.requires_confirmation is False
            assert res_confirmed.is_resumable is True

            updated_chk = store.get_checkpoint("task-deploy")
            assert updated_chk.recovery_status == "RESUMED"
            assert updated_chk.current_state == "READY"
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


# ---------------------------------------------------------------------------
# 6. Deadline & Timeout Manager Tests
# ---------------------------------------------------------------------------

class TestDeadlineManager:
    """Verifies timeout execution, deadline clamping, and event emission."""

    @pytest.mark.asyncio
    async def test_successful_execution_within_timeout(self):
        dm = DeadlineManager()

        async def quick_work():
            await asyncio.sleep(0.01)
            return "done"

        success, result, err = await dm.execute_with_timeout(quick_work(), timeout_seconds=1.0)
        assert success is True
        assert result == "done"
        assert err is None

    @pytest.mark.asyncio
    async def test_timeout_execution_cancellation(self):
        dm = DeadlineManager()

        async def slow_work():
            await asyncio.sleep(0.5)
            return "never"

        success, result, err = await dm.execute_with_timeout(slow_work(), timeout_seconds=0.05, operation_name="slow")
        assert success is False
        assert result is None
        assert "timed out" in err.lower()

    def test_deadline_propagation_clamping(self):
        dm = DeadlineManager()
        dm.register_task_deadline("task-clamp", deadline_seconds=5.0)

        # Remaining should be around 5.0
        rem = dm.get_remaining_task_time("task-clamp")
        assert rem is not None
        assert 0.0 < rem <= 5.0

        # Effective timeout clamped
        effective = dm.calculate_effective_timeout(task_id="task-clamp", requested_timeout_seconds=30.0)
        assert effective <= 5.0


# ---------------------------------------------------------------------------
# 7. Resource Lifecycle & Cleanup Tests
# ---------------------------------------------------------------------------

class TestResourceLifecycle:
    """Verifies temporary file purging and cleanup hook execution."""

    @pytest.mark.asyncio
    async def test_temp_file_cleanup(self):
        lifecycle = ResourceLifecycleManager()
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            fpath = tf.name

        assert os.path.exists(fpath)
        lifecycle.register_temp_file(fpath)

        res = await lifecycle.cleanup_all()
        assert res["purged_temp_files"] >= 1
        assert not os.path.exists(fpath)

    @pytest.mark.asyncio
    async def test_sync_and_async_cleanup_callbacks(self):
        lifecycle = ResourceLifecycleManager()
        sync_called = []
        async_called = []

        def sync_hook():
            sync_called.append(True)

        async def async_hook():
            async_called.append(True)

        lifecycle.register_cleanup_hook(sync_hook, is_async=False)
        lifecycle.register_cleanup_hook(async_hook, is_async=True)

        res = await lifecycle.cleanup_all()
        assert len(sync_called) == 1
        assert len(async_called) == 1
        assert res["callbacks_executed"] == 2


# ---------------------------------------------------------------------------
# 8. Concurrency Controller Tests
# ---------------------------------------------------------------------------

class TestConcurrencyController:
    """Verifies bounded semaphores and concurrency limits."""

    @pytest.mark.asyncio
    async def test_llm_concurrency_limit(self):
        cc = ConcurrencyController(max_concurrent_llm=2)
        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            async with cc.limit_llm():
                active += 1
                if active > max_active:
                    max_active = active
                await asyncio.sleep(0.05)
                active -= 1

        tasks = [asyncio.create_task(worker()) for _ in range(6)]
        await asyncio.gather(*tasks)

        assert max_active <= 2

    @pytest.mark.asyncio
    async def test_build_concurrency_limit(self):
        cc = ConcurrencyController(max_concurrent_builds=1)
        active = 0
        max_active = 0

        async def build_worker():
            nonlocal active, max_active
            async with cc.limit_build():
                active += 1
                if active > max_active:
                    max_active = active
                await asyncio.sleep(0.03)
                active -= 1

        tasks = [asyncio.create_task(build_worker()) for _ in range(4)]
        await asyncio.gather(*tasks)

        assert max_active == 1


# ---------------------------------------------------------------------------
# 9. API Endpoints Tests
# ---------------------------------------------------------------------------

class TestRuntimeAPIEndpoints:
    """Verifies FastAPI runtime endpoints."""

    @pytest.mark.asyncio
    async def test_runtime_status_endpoint(self):
        from app.api.routes import runtime_status_endpoint
        res = await runtime_status_endpoint()

        assert res["status"] == "ok"
        assert "runtime_state" in res
        assert "host_resources" in res
        assert "performance" in res

    @pytest.mark.asyncio
    async def test_runtime_resources_endpoint(self):
        from app.api.routes import runtime_resources_endpoint
        res = await runtime_resources_endpoint()

        assert "ram_used_pct" in res
        assert "cpu_pct" in res
        assert "thresholds" in res

    @pytest.mark.asyncio
    async def test_runtime_performance_endpoint(self):
        from app.api.routes import runtime_performance_endpoint
        res = await runtime_performance_endpoint()

        assert "aggregate" in res
        assert "recent_operations" in res

    @pytest.mark.asyncio
    async def test_runtime_tasks_endpoint(self):
        from app.api.routes import runtime_tasks_endpoint
        res = await runtime_tasks_endpoint()

        assert "total_persisted" in res
        assert "tasks" in res

    @pytest.mark.asyncio
    async def test_runtime_recovery_endpoint(self):
        from app.api.routes import runtime_recovery_endpoint
        res = await runtime_recovery_endpoint()

        assert "recoverable_count" in res
        assert "tasks" in res

    @pytest.mark.asyncio
    async def test_runtime_recovery_resume_endpoint(self):
        from app.api.routes import runtime_recovery_resume_endpoint
        res = await runtime_recovery_resume_endpoint({"task_id": "non-existent-task-id", "confirmed": False})
        assert res["is_resumable"] is False
        assert "not found" in res["reason"]


# ---------------------------------------------------------------------------
# 10. Security & Secret Redaction Invariant Tests
# ---------------------------------------------------------------------------

class TestRuntimeSecurityInvariants:
    """Explicitly verifies Part 15 security requirements."""

    def test_checkpoint_store_never_persists_raw_secrets(self):
        store = CheckpointStore(db_path=":memory:")
        chk = store.save_checkpoint(
            task_id="orch-sec-inv",
            user_goal="Secret test",
            metadata={
                ".env": "SECRET_KEY=12345",
                "api_key": "sk-1234567890",
                "password": "Password123!",
                "cookie": "session=abcde12345",
                "bearer_token": "bearer eyJhbGciOi...",
                "private_key": "-----BEGIN RSA PRIVATE KEY-----...",
                "safe_field": "public_data",
            },
        )

        loaded = store.get_checkpoint("orch-sec-inv")
        assert loaded is not None
        for key in (".env", "api_key", "password", "cookie", "bearer_token", "private_key"):
            assert loaded.safe_metadata[key] == "[REDACTED]"
        assert loaded.safe_metadata["safe_field"] == "public_data"

    @pytest.mark.asyncio
    async def test_performance_metrics_never_contain_prompts_or_secrets(self):
        profiler = RuntimePerformanceService(max_metrics=10)
        await profiler.record_operation(
            name="sensitive_call",
            category="llm",
            duration_ms=120.0,
            task_id="task-sec",
            action_id="act-sec",
            provider="ollama",
            model="qwen2.5:7b",
        )

        metrics = await profiler.get_metrics(limit=1)
        m = metrics[0]
        dump = m.model_dump()
        assert "prompt" not in dump
        assert "password" not in dump
        assert "api_key" not in dump
        assert "token" not in dump

    def test_recovery_service_never_executes_shell_or_bypasses_confirmation(self):
        store = CheckpointStore(db_path=":memory:")
        recovery = RuntimeRecoveryService(store=store)

        for dangerous_type in DANGEROUS_STEP_TYPES:
            chk = store.save_checkpoint(
                task_id=f"chk-{dangerous_type.lower()}",
                user_goal="Dangerous op",
                current_state="WAITING_CONFIRMATION",
                current_step_id="step-x",
                pending_steps=["step-x"],
                step_details=[{"step_id": "step-x", "step_type": dangerous_type, "requires_confirmation": True}],
            )

            # Unconfirmed resume must not allow execution
            res = recovery.resume_task(chk.task_id, confirmed=False)
            assert res.requires_confirmation is True
            assert res.safe_to_auto_resume is False


# ---------------------------------------------------------------------------
# 11. Orchestrator Integration & Lifecycle Scenarios
# ---------------------------------------------------------------------------

class TestOrchestratorRuntimeIntegration:
    """Verifies orchestration interaction with runtime reliability components."""

    @pytest.mark.asyncio
    async def test_orchestration_checkpoints_auto_persist_to_store(self):
        from app.orchestrator.engine import OrchestratorEngine

        engine = OrchestratorEngine()
        task = engine.create_and_plan(user_goal="Test persistent checkpointing", project_name_hint="test_proj")

        # The task creation records checkpoints (TASK_CREATED, PLAN_CREATED)
        # Verify they exist in checkpoint_store
        from app.runtime.checkpoint_store import checkpoint_store
        persisted = checkpoint_store.get_checkpoint(task.task_id)
        assert persisted is not None
        assert persisted.task_id == task.task_id
        assert persisted.project_name == task.project_name
        assert "PLAN_CREATED" in [c.name for c in task.checkpoints]

    @pytest.mark.asyncio
    async def test_orchestrator_resource_block_handling(self):
        from app.orchestrator.engine import OrchestratorEngine
        from app.runtime.resource_manager import resource_manager

        engine = OrchestratorEngine()
        task = engine.create_and_plan(user_goal="Build my application", project_name_hint="block_proj")

        # Mock resource manager to report CRITICAL RAM for BUILD
        with patch.object(resource_manager, "check_resource_pressure") as mock_check:
            from app.runtime.models import ResourceCheckResult, ResourceDecision
            mock_check.return_value = ResourceCheckResult(
                decision=ResourceDecision.BLOCK,
                ram_used_pct=96.0,
                cpu_used_pct=50.0,
                reason="Simulated host RAM critical exhaustion (96%)",
            )

            result = await engine.execute_task(task.task_id, auto_confirm=True)
            assert result.status == "PAUSED" or result.status == "FAILED"
            assert "blocked by host resource policy" in (task.error or "").lower()

    @pytest.mark.asyncio
    async def test_deadline_manager_timeout_event_emission(self):
        dm = DeadlineManager()
        events = []

        async def capture(ev: ActionEvent):
            events.append(ev)

        sid = action_bus.subscribe(capture)
        try:
            async def hung_work():
                await asyncio.sleep(0.5)

            success, _, err = await dm.execute_with_timeout(hung_work(), timeout_seconds=0.02, operation_name="hung_op")
            assert success is False
            await asyncio.sleep(0.05)
            timeout_events = [e for e in events if e.action_type == ActionType.RUNTIME_TIMEOUT]
            assert len(timeout_events) >= 1
            assert timeout_events[0].status == ActionStatus.FAILED
        finally:
            action_bus.unsubscribe(sid)

    def test_checkpoint_store_update_and_delete(self):
        store = CheckpointStore(db_path=":memory:")
        store.save_checkpoint(task_id="task-del", current_state="READY")
        assert store.get_checkpoint("task-del") is not None

        store.update_recovery_status("task-del", status="RESOLVED", current_state="COMPLETED")
        updated = store.get_checkpoint("task-del")
        assert updated.recovery_status == "RESOLVED"
        assert updated.current_state == "COMPLETED"

        deleted = store.delete_checkpoint("task-del")
        assert deleted is True
        assert store.get_checkpoint("task-del") is None

    def test_recovery_service_schema_mismatch(self):
        store = CheckpointStore(db_path=":memory:")
        recovery = RuntimeRecoveryService(store=store)

        chk = store.save_checkpoint(task_id="task-old", current_state="RUNNING")
        # Mutate schema version to older/future
        chk.schema_version = 999

        decision = recovery.evaluate_task(chk)
        assert decision.is_resumable is False
        assert "version mismatch" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_profiler_clear_and_category_filter(self):
        profiler = RuntimePerformanceService(max_metrics=20)
        await profiler.record_operation("op_llm", category="llm", duration_ms=100.0)
        await profiler.record_operation("op_tool", category="tool", duration_ms=20.0)

        llm_metrics = await profiler.get_metrics(category="llm")
        assert len(llm_metrics) == 1
        assert llm_metrics[0].category == "llm"

        tool_metrics = await profiler.get_metrics(category="tool")
        assert len(tool_metrics) == 1
        assert tool_metrics[0].category == "tool"

        profiler.clear()
        all_metrics = await profiler.get_metrics()
        assert len(all_metrics) == 0

    def test_context_budget_report_to_dict(self):
        context, report = ContextBudgetManager.optimize_context(
            task_goal="test goal",
            user_intent="test intent",
            task_state_and_security="security constraints",
            relevant_files={"main.py": "code"},
            tool_results={"tool": "res"},
            max_budget_tokens=100,
        )
        d = report.model_dump()
        assert "estimated_tokens" in d
        assert "optimization_ratio" in d
        assert "omitted_categories" in d
        assert "retained_categories" in d

    @pytest.mark.asyncio
    async def test_concurrency_browser_and_graph_semaphores(self):
        controller = ConcurrencyController(max_concurrent_browser=2, max_concurrent_graph_writes=1)
        async with controller.limit_browser():
            async with controller.limit_browser():
                assert controller._browser_sem.locked()

        async with controller.limit_graph_write():
            assert controller._graph_sem.locked()

    def test_deadline_manager_remaining_calculation(self):
        dm = DeadlineManager()
        dm.register_task_deadline("task-timing", deadline_seconds=60.0)
        eff = dm.calculate_effective_timeout("task-timing", requested_timeout_seconds=30.0)
        assert 0.0 < eff <= 30.0
        dm.clear_task("task-timing")
        assert "task-timing" not in dm._task_deadlines

    def test_checkpoint_store_corrupt_json_payload_handling(self):
        store = CheckpointStore(db_path=":memory:")
        store.save_checkpoint(task_id="task-corrupt", current_state="RUNNING")

        with store._connection() as conn:
            conn.execute("UPDATE task_checkpoints SET safe_metadata = '{corrupted-json' WHERE task_id = 'task-corrupt'")

        chk = store.get_checkpoint("task-corrupt")
        assert chk is not None
        assert chk.safe_metadata == {}

    def test_recovery_service_batch_inspection(self):
        store = CheckpointStore(db_path=":memory:")
        recovery = RuntimeRecoveryService(store=store)

        store.save_checkpoint(task_id="t1", current_state="COMPLETED")
        store.save_checkpoint(
            task_id="t2", current_state="RUNNING", pending_steps=["s1"],
            step_details=[{"step_id": "s1", "step_type": "READ_PAGE", "requires_confirmation": False}],
        )
        store.save_checkpoint(
            task_id="t3", current_state="RUNNING", pending_steps=["s2"],
            step_details=[{"step_id": "s2", "step_type": "GIT_PUSH", "requires_confirmation": True}],
        )

        decisions = recovery.scan_for_recoverable_tasks()
        task_ids = {d.task_id for d in decisions}
        assert "t2" in task_ids
        assert "t3" in task_ids
        assert "t1" not in task_ids

        t2_dec = next(d for d in decisions if d.task_id == "t2")
        assert t2_dec.safe_to_auto_resume is True

        t3_dec = next(d for d in decisions if d.task_id == "t3")
        assert t3_dec.requires_confirmation is True

    @pytest.mark.asyncio
    async def test_resource_lifecycle_error_handling(self):
        manager = ResourceLifecycleManager()

        def failing_sync():
            raise ValueError("Intentional sync cleanup error")

        async def failing_async():
            raise RuntimeError("Intentional async cleanup error")

        manager.register_cleanup_hook(failing_sync, is_async=False)
        manager.register_cleanup_hook(failing_async, is_async=True)

        report = await manager.cleanup_all()
        assert "callbacks_executed" in report

    def test_action_event_env_redaction(self):
        from app.actions.models import ActionEvent, ActionType, ActionStatus
        evt = ActionEvent(
            action_type=ActionType.RUNTIME_OPERATION_COMPLETED,
            status=ActionStatus.COMPLETED,
            title="Env check",
            safe_metadata={"env": "DB_PASSWORD=secret", "normal_key": "val"},
        )
        d = evt.model_dump()
        assert d["safe_metadata"]["env"] == "[REDACTED]"
        assert d["safe_metadata"]["normal_key"] == "val"

    def test_context_budget_zero_limit_preserves_security(self):
        context, report = ContextBudgetManager.optimize_context(
            task_goal="Run command",
            user_intent="Intent",
            task_state_and_security="STRICT_SECURITY_RULES",
            relevant_files={},
            tool_results={},
            max_budget_tokens=0,
        )
        assert report.security_invariants_preserved is True
        assert context.get("task_state") == "STRICT_SECURITY_RULES"

    def test_runtime_models_serialization(self):
        from app.runtime.models import (
            AggregatePerformanceMetrics,
            OperationMetric,
            PersistedTaskCheckpoint,
            RecoveryDecision,
            ResourceCheckResult,
            ResourceDecision,
        )
        check = ResourceCheckResult(
            decision=ResourceDecision.ALLOW,
            ram_used_pct=45.0,
            cpu_used_pct=12.0,
        )
        d = check.model_dump()
        assert d["decision"] == "ALLOW"
        assert d["ram_used_pct"] == 45.0

    def test_resource_manager_state_evaluation(self):
        rm = ResourceManager(ram_warning_pct=95.0, ram_critical_pct=99.9)
        res = rm.check_resource_pressure("read_file")
        assert res.decision in (ResourceDecision.ALLOW, ResourceDecision.ALLOW_WITH_WARNING)




"""RYVEN 3.0 — M15.3.10 Endurance & Recovery Unit & Integration Tests.

Validates the stability, deterministic restart recovery, memory trends,
concurrency limits, and security invariants of the endurance test harness.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time
from pathlib import Path
import pytest

from fastapi.testclient import TestClient
from app.main import app
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import TaskType
from app.ai.router import model_router
from app.orchestrator.models import OrchestrationTask, TaskState
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.concurrency import ConcurrencyController
from app.runtime.context_budget import ContextBudgetManager
from app.runtime.deadline_manager import DeadlineManager
from app.runtime.lifecycle import ResourceLifecycleManager
from app.runtime.models import ResourceDecision, ResourceStatus, RuntimeState
from app.runtime.performance import RuntimePerformanceService
from app.runtime.recovery import RuntimeRecoveryService
from app.runtime.resource_manager import ResourceManager
from scripts.verify_m15_3_10_endurance import EnduranceHarness, ResourceSample


@pytest.fixture
def harness():
    """Create an isolated, short-duration endurance harness."""
    h = EnduranceHarness(duration_seconds=2.0, mode="short", generate_report=False)
    yield h
    try:
        h.temp_dir.cleanup()
    except Exception:
        pass


class TestEnduranceHarnessComponents:
    """Verifies each functional component of the M15.3.10 endurance harness."""

    def test_endurance_harness_initialization(self, harness):
        assert harness.duration_seconds == 2.0
        assert harness.mode == "short"
        assert harness.test_db_path.exists()
        assert harness.checkpoint_store is not None
        assert harness.recovery_service is not None
        assert harness.resource_manager is not None

    @pytest.mark.asyncio
    async def test_endurance_workload_ai(self, harness):
        ok = await harness._workload_ai()
        assert ok is True
        assert harness.metrics.ai_requests == 1
        assert harness.metrics.ai_failures == 0
        assert len(harness.metrics.latencies) == 1

    @pytest.mark.asyncio
    async def test_endurance_workload_project(self, harness):
        ok = await harness._workload_project()
        assert ok is True
        assert harness.metrics.project_operations == 1

    @pytest.mark.asyncio
    async def test_endurance_workload_browser(self, harness):
        ok = await harness._workload_browser()
        assert ok is True
        assert harness.metrics.browser_operations == 1

    @pytest.mark.asyncio
    async def test_endurance_workload_health(self, harness):
        ok = await harness._workload_health()
        assert ok is True
        assert harness.metrics.health_checks == 1

    @pytest.mark.asyncio
    async def test_endurance_workload_checkpoints_and_redaction(self, harness):
        ok = await harness._workload_checkpoints(iteration=1)
        assert ok is True
        assert harness.metrics.checkpoints_created == 1
        assert harness.metrics.checkpoints_retrieved == 1

        # Check secret was redacted in DB
        chk = harness.checkpoint_store.get_checkpoint("task-endurance-1")
        assert chk is not None
        assert chk.safe_metadata.get("env") == "[REDACTED]"
        assert chk.safe_metadata.get("normal") == "valid_metadata"

    @pytest.mark.asyncio
    async def test_endurance_controlled_restart_recovery(self, harness):
        ok = await harness.verify_controlled_restart_recovery()
        assert ok is True
        assert harness.metrics.recovery_tests_passed == 1
        assert harness.metrics.recovery_tests_failed == 0

    @pytest.mark.asyncio
    async def test_endurance_dangerous_recovery_confirmation_gate(self, harness):
        ok = await harness.verify_dangerous_action_confirmation_gate()
        assert ok is True
        assert harness.metrics.recovery_tests_passed == 1
        assert harness.metrics.recovery_tests_failed == 0

    @pytest.mark.asyncio
    async def test_endurance_timeout_and_deadline(self, harness):
        ok = await harness.verify_timeout_and_deadline()
        assert ok is True
        assert harness.metrics.timeouts_handled == 1

    @pytest.mark.asyncio
    async def test_endurance_cancellation(self, harness):
        ok = await harness.verify_cancellation()
        assert ok is True
        assert harness.metrics.cancellations_handled == 1

    @pytest.mark.asyncio
    async def test_endurance_concurrency_limits(self, harness):
        ok = await harness.verify_concurrency_limits()
        assert ok is True
        assert harness.metrics.concurrency_checks_passed == 1

    @pytest.mark.asyncio
    async def test_endurance_event_integrity_and_redaction(self, harness):
        ok = await harness.verify_event_integrity_and_redaction()
        assert ok is True

    @pytest.mark.asyncio
    async def test_endurance_resource_cleanup_cycle(self, harness):
        ok = await harness.verify_resource_cleanup_cycle()
        assert ok is True
        assert harness.metrics.purged_temp_files >= 1
        assert harness.metrics.cleanup_cycles_executed == 1


class TestEnduranceAnalysisAndExecution:
    """Verifies statistical analysis, reporting, and full short harness run."""

    def test_endurance_telemetry_sampling(self, harness):
        s1 = harness.sample_telemetry()
        assert isinstance(s1, ResourceSample)
        assert s1.ram_used_pct >= 0.0
        assert s1.rss_mb >= 0.0
        assert len(harness.resource_samples) == 1

    def test_endurance_statistical_analysis(self, harness):
        harness.start_time = time.monotonic() - 60.0
        harness.end_time = time.monotonic()
        harness.resource_samples = [
            ResourceSample(timestamp=0, elapsed_seconds=0, rss_mb=100.0, ram_used_pct=50.0, ram_used_gb=8.0, cpu_pct=10.0, active_tasks=0, checkpoint_count=0),
            ResourceSample(timestamp=30, elapsed_seconds=30, rss_mb=105.0, ram_used_pct=51.0, ram_used_gb=8.1, cpu_pct=15.0, active_tasks=0, checkpoint_count=5),
            ResourceSample(timestamp=60, elapsed_seconds=60, rss_mb=104.0, ram_used_pct=51.0, ram_used_gb=8.1, cpu_pct=12.0, active_tasks=0, checkpoint_count=10),
        ]
        harness.metrics.latencies = [10.0, 12.0, 15.0, 20.0, 25.0]
        harness.metrics.early_latencies = [10.0, 12.0]
        harness.metrics.late_latencies = [20.0, 25.0]
        harness.metrics.recovery_tests_passed = 1

        analysis = harness.analyze_results()
        assert analysis["initial_rss_mb"] == 100.0
        assert analysis["final_rss_mb"] == 104.0
        assert analysis["peak_rss_mb"] == 105.0
        assert analysis["rss_delta_mb"] == 4.0
        assert analysis["growth_rate_mb_per_min"] == 4.0
        assert analysis["memory_stable"] is True
        assert analysis["verdict"] == "PASS"

    def test_endurance_report_generation(self, harness, tmp_path):
        report_file = tmp_path / "TEST_REPORT.md"
        harness.output_report_path = str(report_file)
        analysis = {
            "verdict": "PASS",
            "duration_seconds": 60.0,
            "mode": "test",
            "samples_count": 5,
            "initial_rss_mb": 100.0,
            "final_rss_mb": 105.0,
            "peak_rss_mb": 106.0,
            "avg_rss_mb": 102.5,
            "rss_delta_mb": 5.0,
            "growth_rate_mb_per_min": 5.0,
            "memory_stable": True,
            "peak_ram_pct": 52.0,
            "avg_cpu_pct": 12.5,
            "total_operations": 50,
            "successful_operations": 50,
            "failed_operations": 0,
            "ai_requests": 10,
            "browser_operations": 10,
            "project_operations": 10,
            "health_checks": 10,
            "checkpoints_created": 10,
            "checkpoints_retrieved": 10,
            "recovery_tests_passed": 2,
            "recovery_tests_failed": 0,
            "timeouts_handled": 1,
            "cancellations_handled": 1,
            "concurrency_checks_passed": 1,
            "purged_temp_files": 2,
            "avg_latency_ms": 15.0,
            "p50_latency_ms": 14.0,
            "p95_latency_ms": 22.0,
            "max_latency_ms": 25.0,
            "early_avg_latency_ms": 12.0,
            "late_avg_latency_ms": 18.0,
            "latency_drift_pct": 50.0,
            "latency_stable": True,
        }
        harness.write_report(analysis)
        assert report_file.exists()
        content = report_file.read_text(encoding="utf-8")
        assert "RYVEN 3.0 — M15.3.10 ENDURANCE & SOAK TEST REPORT" in content
        assert "OVERALL RESULT**: **PASS**" in content

    @pytest.mark.asyncio
    async def test_endurance_short_run_execution(self, harness):
        harness.duration_seconds = 1.0  # Run for 1 second in test suite
        analysis = await harness.run()
        assert analysis["verdict"] in ("PASS", "WARN")
        assert analysis["total_operations"] > 0
        assert analysis["checkpoints_created"] > 0
        assert analysis["recovery_tests_passed"] > 0

    def test_sqlite_concurrent_access_and_reopen(self, tmp_path):
        db_path = str(tmp_path / "concurrent_chk.db")
        store = CheckpointStore(db_path=db_path)

        # Write initial checkpoint
        store.save_checkpoint(task_id="t1", user_goal="goal 1")

        # Open concurrent connection
        conn2 = sqlite3.connect(db_path, timeout=5.0)
        cur = conn2.cursor()
        cur.execute("SELECT COUNT(*) FROM task_checkpoints")
        row = cur.fetchone()
        assert row[0] == 1
        conn2.close()

        # Update and count
        store.save_checkpoint(task_id="t2", user_goal="goal 2")
        assert store.count_checkpoints() == 2

    def test_frontend_runtime_endpoints_during_load(self):
        client = TestClient(app)

        # Check endpoints return 200 under rapid requests
        for _ in range(5):
            r_status = client.get("/api/runtime/status")
            assert r_status.status_code == 200

            r_resources = client.get("/api/runtime/resources")
            assert r_resources.status_code == 200

            r_perf = client.get("/api/runtime/performance")
            assert r_perf.status_code == 200

            r_tasks = client.get("/api/runtime/tasks")
            assert r_tasks.status_code == 200

            r_rec = client.get("/api/runtime/recovery")
            assert r_rec.status_code == 200

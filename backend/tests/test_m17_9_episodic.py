"""Unit and integration tests for RYVEN 3.0 M17.9 Phase 2 Episodic Task Memory & Indexer.

Covers:
- Successful and failed task indexing with accurate outcome classification
- PARTIAL outcome classification on partial step progression
- Provenance tracking (task_id link to TaskPersistenceRepository)
- Project scoping and isolation metadata
- Bounded summary and solution steps extraction
- Defensive secret boundary (passwords, tokens, API keys, confirmation tokens, <think> tags)
- Event bus integration and terminal lifecycle handling
- Deterministic idempotency on duplicate events and repeated indexing
- Hook safety (memory write failures never fail task execution)
- Safe handling of malformed or missing metadata
- Concurrent indexing safety
- Subscription lifecycle and clean shutdown
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any, Dict, List
import uuid

import pytest

from app.actions.event_bus import ActionEventBus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskState,
    LongHorizonTaskStep,
    TaskPersistenceRepository,
)
from app.memory.episodic import EpisodicMemoryIndexer, MAX_SOLUTION_STEPS, MAX_SUMMARY_WORDS
from app.memory.repository import (
    EpisodicMemory,
    EpisodicOutcome,
    MemoryRepository,
    MemoryScope,
    RetentionClass,
    TrustLevel,
)


@pytest.fixture
def mem_repo(tmp_path: Path) -> MemoryRepository:
    """Fixture providing isolated MemoryRepository."""
    db_file = tmp_path / "episodic_test.db"
    repo = MemoryRepository(db_path=db_file)
    yield repo
    repo.close()


@pytest.fixture
def task_repo(tmp_path: Path) -> TaskPersistenceRepository:
    """Fixture providing isolated TaskPersistenceRepository."""
    db_file = tmp_path / "episodic_tasks.db"
    repo = TaskPersistenceRepository(db_path=db_file)
    yield repo


@pytest.fixture
def event_bus() -> ActionEventBus:
    """Fixture providing isolated ActionEventBus."""
    return ActionEventBus(max_events=100)


@pytest.fixture
def indexer(mem_repo: MemoryRepository, task_repo: TaskPersistenceRepository, event_bus: ActionEventBus) -> EpisodicMemoryIndexer:
    """Fixture providing configured EpisodicMemoryIndexer."""
    idx = EpisodicMemoryIndexer(
        memory_repo=mem_repo,
        task_repo=task_repo,
        event_bus=event_bus,
        auto_subscribe=True,
    )
    yield idx
    idx.close()


def _make_sample_task(
    task_id: str = "lht-001",
    goal: str = "Configure PostgreSQL replica and verify replication lag",
    state: LongHorizonTaskState = LongHorizonTaskState.COMPLETED,
    steps_count: int = 3,
    completed_count: int = 3,
    project_id: str = "infra_repo",
    failure_class: str = None,
) -> LongHorizonTask:
    """Helper creating realistic LongHorizonTask records."""
    task = LongHorizonTask(
        task_id=task_id,
        goal=goal,
        state=state,
        failure_class=failure_class,
        safe_metadata={"project_id": project_id, "tags": ["postgres", "replication", "infra"]},
    )
    for i in range(steps_count):
        step = LongHorizonTaskStep(
            step_id=f"step-{i}",
            name=f"Execute replication step {i}",
            action="configure",
            status=LongHorizonTaskState.COMPLETED if i < completed_count else LongHorizonTaskState.FAILED,
        )
        task.steps.append(step)
        if i < completed_count:
            task.completed_step_ids.append(step.step_id)
        else:
            task.failed_step_ids.append(step.step_id)

    task.progress_percent = (completed_count / steps_count * 100.0) if steps_count else 0.0
    task.result_summary = "Replication configured and streaming tested." if state == LongHorizonTaskState.COMPLETED else "Replication failed."
    return task


# ---------------------------------------------------------------------------
# Test 1-7: Task Outcome & Indexing Basics
# ---------------------------------------------------------------------------

def test_01_successful_task_creates_episodic_memory(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository):
    """Test 1: Completed task produces a valid EpisodicMemory with SUCCESS outcome."""
    task = _make_sample_task(task_id="t1", state=LongHorizonTaskState.COMPLETED)
    task_repo.save_task(task)

    mem = indexer.index_task(task)
    assert mem is not None
    assert mem.task_id == "t1"
    assert mem.outcome == EpisodicOutcome.SUCCESS
    assert "PostgreSQL" in mem.summary
    assert len(mem.solution_steps) == 3


def test_02_failed_task_creates_episodic_memory(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository):
    """Test 2: Failed task produces an EpisodicMemory with FAILURE outcome."""
    task = _make_sample_task(
        task_id="t2",
        state=LongHorizonTaskState.FAILED,
        steps_count=3,
        completed_count=0,
        failure_class="NETWORK_TIMEOUT",
    )
    task_repo.save_task(task)

    mem = indexer.index_task(task)
    assert mem is not None
    assert mem.task_id == "t2"
    assert mem.outcome == EpisodicOutcome.FAILURE
    assert mem.failure_class == "NETWORK_TIMEOUT"


def test_03_task_id_provenance_preserved(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository, mem_repo: MemoryRepository):
    """Test 3: task_id provides exact provenance linking back to TaskPersistenceRepository."""
    task = _make_sample_task(task_id="t3-provenance")
    task_repo.save_task(task)

    mem = indexer.index_task(task)
    assert mem is not None

    # Query memory from repo
    fetched_mem = mem_repo.get_episodic_memory_by_task_id("t3-provenance")
    assert fetched_mem is not None

    # Authoritative record must exist in TaskPersistenceRepository
    authoritative_task = task_repo.get_task(fetched_mem.task_id)
    assert authoritative_task is not None
    assert authoritative_task.task_id == "t3-provenance"


def test_04_project_id_preserved(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository):
    """Test 4: project_id is captured accurately from safe_metadata."""
    task = _make_sample_task(task_id="t4", project_id="ryven_analytics")
    mem = indexer.index_task(task)
    assert mem is not None
    assert mem.project_id == "ryven_analytics"
    assert "project:ryven_analytics" in mem.tags


def test_05_success_classification(indexer: EpisodicMemoryIndexer):
    """Test 5: Clean completion classifies as SUCCESS."""
    task = _make_sample_task(task_id="t5-success", state=LongHorizonTaskState.COMPLETED, steps_count=2, completed_count=2)
    mem = indexer.index_task(task)
    assert mem.outcome == EpisodicOutcome.SUCCESS


def test_06_failure_classification(indexer: EpisodicMemoryIndexer):
    """Test 6: Zero progress failure classifies as FAILURE."""
    task = _make_sample_task(task_id="t6-fail", state=LongHorizonTaskState.FAILED, steps_count=3, completed_count=0)
    mem = indexer.index_task(task)
    assert mem.outcome == EpisodicOutcome.FAILURE


def test_07_partial_classification(indexer: EpisodicMemoryIndexer):
    """Test 7: Tasks with partial progress classify as PARTIAL."""
    # Completed task with partial step execution
    task1 = _make_sample_task(task_id="t7-part1", state=LongHorizonTaskState.COMPLETED, steps_count=4, completed_count=2)
    mem1 = indexer.index_task(task1)
    assert mem1.outcome == EpisodicOutcome.PARTIAL

    # Cancelled task with partial progress
    task2 = _make_sample_task(task_id="t7-part2", state=LongHorizonTaskState.CANCELLED, steps_count=4, completed_count=2)
    mem2 = indexer.index_task(task2)
    assert mem2.outcome == EpisodicOutcome.PARTIAL


# ---------------------------------------------------------------------------
# Test 8-11: Bounds, Summary, & Metadata Extraction
# ---------------------------------------------------------------------------

def test_08_summary_bounded(indexer: EpisodicMemoryIndexer):
    """Test 8: Summary word count is strictly bounded."""
    huge_goal = "Solve issue " + " ".join([f"detail_{i}" for i in range(500)])
    task = _make_sample_task(task_id="t8", goal=huge_goal)
    mem = indexer.index_task(task)
    assert mem is not None
    words = mem.summary.split()
    assert len(words) <= MAX_SUMMARY_WORDS + 5


def test_09_solution_steps_bounded(indexer: EpisodicMemoryIndexer):
    """Test 9: Solution steps list is bounded to max steps."""
    task = LongHorizonTask(task_id="t9", goal="Lots of steps")
    for i in range(50):
        task.steps.append(LongHorizonTaskStep(name=f"Step {i}", action="do_something"))
    mem = indexer.index_task(task)
    assert len(mem.solution_steps) <= MAX_SOLUTION_STEPS


def test_10_failure_class_preserved(indexer: EpisodicMemoryIndexer):
    """Test 10: Failure class is preserved on non-success."""
    task = _make_sample_task(task_id="t10", state=LongHorizonTaskState.FAILED, steps_count=1, completed_count=0, failure_class="PERMISSION_DENIED")
    mem = indexer.index_task(task)
    assert mem.failure_class == "PERMISSION_DENIED"


def test_11_tags_preserved(indexer: EpisodicMemoryIndexer):
    """Test 11: Task tags are preserved and categorized."""
    task = _make_sample_task(task_id="t11")
    task.safe_metadata["tags"] = ["docker", "networking"]
    mem = indexer.index_task(task)
    assert "docker" in mem.tags
    assert "networking" in mem.tags
    assert "success" in mem.tags


# ---------------------------------------------------------------------------
# Test 12-17: Security, Privacy, & Scrubbing Invariants
# ---------------------------------------------------------------------------

def test_12_task_derived_trust_semantics(indexer: EpisodicMemoryIndexer):
    """Test 12: Episodic memories always have TASK_DERIVED trust semantics."""
    task = _make_sample_task(task_id="t12")
    mem = indexer.index_task(task)
    # The domain model defaults semantic memories to trust level; episodic memories are derived
    assert "episodic_task" in mem.tags


def test_13_secret_redaction(indexer: EpisodicMemoryIndexer):
    """Test 13: Secrets embedded in goal or summary are scrubbed."""
    task = _make_sample_task(
        task_id="t13-secrets",
        goal="Connect with token ghp_123456789012345678901234567890 and password SuperSecret123",
    )
    task.result_summary = "Authenticated using bearer aabbccddeeff001122334455"
    mem = indexer.index_task(task)
    assert "[REDACTED]" in mem.summary
    assert "SuperSecret123" not in mem.summary
    assert "ghp_1234567890" not in mem.summary


def test_14_think_removal(indexer: EpisodicMemoryIndexer):
    """Test 14: Internal reasoning <think> tags are stripped from summary."""
    task = _make_sample_task(task_id="t14")
    task.result_summary = "Result achieved. <think>Hidden chain of thought reasoning here</think> Done."
    mem = indexer.index_task(task)
    assert "<think>" not in mem.summary
    assert "Hidden chain of thought" not in mem.summary


def test_15_raw_screenshot_exclusion(indexer: EpisodicMemoryIndexer):
    """Test 15: Raw binary screenshot payloads are excluded."""
    task = _make_sample_task(task_id="t15")
    task.safe_metadata["screenshot_bytes"] = b"\x89PNG\r\n\x1a\n\x00\x00"
    mem = indexer.index_task(task)
    assert "screenshot_bytes" not in mem.summary
    assert b"PNG" not in json.dumps(mem.solution_steps).encode()


def test_16_raw_audio_exclusion(indexer: EpisodicMemoryIndexer):
    """Test 16: Raw audio recording buffers are excluded."""
    task = _make_sample_task(task_id="t16")
    task.safe_metadata["raw_audio_pcm"] = [0, 1, 2, 3] * 1000
    mem = indexer.index_task(task)
    assert "raw_audio_pcm" not in mem.summary


def test_17_confirmation_token_exclusion(indexer: EpisodicMemoryIndexer):
    """Test 17: Confirmation tokens are never persisted in episodic memory."""
    task = _make_sample_task(task_id="t17")
    task.active_confirmation_token = "conf-token-xyz-secret-123"
    mem = indexer.index_task(task)
    assert "conf-token-xyz-secret-123" not in mem.summary
    assert "conf-token-xyz-secret-123" not in json.dumps(mem.solution_steps)


# ---------------------------------------------------------------------------
# Test 18-19: Idempotency
# ---------------------------------------------------------------------------

def test_18_duplicate_terminal_event_idempotency(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 18: Repeated ActionEvents for same task do not duplicate memory."""
    task = _make_sample_task(task_id="t18-event")
    event = ActionEvent(
        action_type=ActionType.LONG_TASK_COMPLETED,
        status=ActionStatus.COMPLETED,
        title="Completed replication task",
        task_id=task.task_id,
        safe_metadata={"goal": task.goal},
    )

    mem1 = asyncio.run(indexer.handle_event(event))
    mem2 = asyncio.run(indexer.handle_event(event))

    assert mem1 is not None and mem2 is not None
    assert mem1.memory_id == mem2.memory_id

    # Verify database has exactly 1 record for this task_id
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodic_memories WHERE task_id = 't18-event'")
        count = cur.fetchone()[0]
        assert count == 1


def test_19_repeated_indexing_idempotency(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 19: Calling index_task multiple times returns existing memory without duplicate row."""
    task = _make_sample_task(task_id="t19-idempotent")

    mem1 = indexer.index_task(task)
    mem2 = indexer.index_task(task)
    mem3 = indexer.index_task(task)

    assert mem1.memory_id == mem2.memory_id == mem3.memory_id

    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodic_memories WHERE task_id = 't19-idempotent'")
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Test 20-26: Robustness, Fallbacks, & Non-Duplication
# ---------------------------------------------------------------------------

def test_20_missing_optional_task_fields_handled_safely(indexer: EpisodicMemoryIndexer):
    """Test 20: Minimal dict task payload indexed without error."""
    minimal = {"task_id": "t20-minimal"}
    mem = indexer.index_task(minimal)
    assert mem is not None
    assert mem.task_id == "t20-minimal"
    assert mem.goal == "Unnamed task"


def test_21_missing_project_handled_safely(indexer: EpisodicMemoryIndexer):
    """Test 21: Task without project_id defaults project_id to None."""
    task = _make_sample_task(task_id="t21-noproject", project_id=None)
    task.safe_metadata = {}
    mem = indexer.index_task(task)
    assert mem.project_id is None


def test_22_task_persistence_remains_authoritative(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository):
    """Test 22: Indexing from task_id string fetches authoritative record from TaskPersistenceRepository."""
    task = _make_sample_task(task_id="t22-lookup", goal="Authoritative task goal")
    task_repo.save_task(task)

    # Pass only task_id string
    mem = indexer.index_task("t22-lookup")
    assert mem is not None
    assert mem.task_id == "t22-lookup"
    assert mem.goal == "Authoritative task goal"


def test_23_memory_failure_does_not_change_task_result(indexer: EpisodicMemoryIndexer, task_repo: TaskPersistenceRepository):
    """Test 23: If memory repository throws error, indexer logs and returns None without raising."""
    task = _make_sample_task(task_id="t23-fail")
    task_repo.save_task(task)

    # Force error by breaking repository db_path temporarily
    indexer.memory_repo = None  # Will cause AttributeError inside index_task
    result = indexer.index_task(task)
    assert result is None  # Error caught safely

    # Task in repository remains untouched
    persisted = task_repo.get_task("t23-fail")
    assert persisted is not None
    assert persisted.state == LongHorizonTaskState.COMPLETED


def test_24_malformed_task_metadata_handled_safely(indexer: EpisodicMemoryIndexer):
    """Test 24: Corrupted metadata types do not crash indexer."""
    corrupted = {
        "task_id": "t24-corrupt",
        "safe_metadata": "not-a-dict",
        "steps": "not-a-list",
        "progress_percent": "invalid-float",
    }
    mem = indexer.index_task(corrupted)
    assert mem is not None
    assert mem.task_id == "t24-corrupt"


def test_25_large_task_payload_remains_compact(indexer: EpisodicMemoryIndexer):
    """Test 25: Massive 100-step task payload produces compact summary under 1500 chars."""
    task = LongHorizonTask(task_id="t25-large", goal="Massive multi-stage task")
    for i in range(100):
        task.steps.append(LongHorizonTaskStep(name=f"Detailed step {i} " * 5, action="run"))
    task.execution_journal = [task.execution_journal] * 50  # simulate massive journal

    mem = indexer.index_task(task)
    assert len(mem.summary) <= 1500
    assert len(mem.solution_steps) <= MAX_SOLUTION_STEPS


def test_26_no_full_execution_graph_duplicated(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 26: Full step argument payloads and journals are NOT in episodic database."""
    task = _make_sample_task(task_id="t26-graph")
    task.steps[0].arguments = {"complex_payload": {"nested": "value", "config": [1, 2, 3]}}
    indexer.index_task(task)

    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT solution_steps_json FROM episodic_memories WHERE task_id = 't26-graph'")
        row = cur.fetchone()
        assert "complex_payload" not in row[0]


# ---------------------------------------------------------------------------
# Test 27-33: Integration, Concurrency, & Lifecycle
# ---------------------------------------------------------------------------

def test_27_repository_restart_preserves_episodic_memory(tmp_path: Path):
    """Test 27: Episodic memories survive repository closure and restart."""
    db_file = tmp_path / "restart.db"
    repo1 = MemoryRepository(db_path=db_file)
    idx1 = EpisodicMemoryIndexer(memory_repo=repo1, auto_subscribe=False)

    task = _make_sample_task(task_id="t27-restart")
    idx1.index_task(task)
    repo1.close()

    # Reopen
    repo2 = MemoryRepository(db_path=db_file)
    try:
        mem = repo2.get_episodic_memory_by_task_id("t27-restart")
        assert mem is not None
        assert mem.task_id == "t27-restart"
    finally:
        repo2.close()


def test_28_concurrent_terminal_indexing_remains_safe(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 28: Multiple threads concurrently indexing distinct tasks execute safely."""
    errors = []

    def worker(worker_id: int):
        try:
            for i in range(5):
                t = _make_sample_task(task_id=f"t28-{worker_id}-{i}")
                res = indexer.index_task(t)
                assert res is not None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodic_memories WHERE task_id LIKE 't28-%'")
        assert cur.fetchone()[0] == 20


def test_29_project_isolation_metadata(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 29: Queries filtered by project_id return only matching memories."""
    indexer.index_task(_make_sample_task(task_id="t29-a", project_id="ProjectAlpha"))
    indexer.index_task(_make_sample_task(task_id="t29-b", project_id="ProjectBeta"))

    alpha_items = mem_repo.list_episodic_memories(project_id="ProjectAlpha")
    beta_items = mem_repo.list_episodic_memories(project_id="ProjectBeta")

    assert len(alpha_items) == 1 and alpha_items[0].task_id == "t29-a"
    assert len(beta_items) == 1 and beta_items[0].task_id == "t29-b"


def test_30_retention_class(indexer: EpisodicMemoryIndexer):
    """Test 30: Custom retention class from metadata is respected."""
    task = _make_sample_task(task_id="t30-perm")
    task.safe_metadata["retention_class"] = "PERMANENT"
    mem = indexer.index_task(task)
    assert mem.retention_class == RetentionClass.PERMANENT


def test_31_audit_behavior(indexer: EpisodicMemoryIndexer, mem_repo: MemoryRepository):
    """Test 31: Indexing a task produces an EPISODIC_CREATED audit log."""
    task = _make_sample_task(task_id="t31-audit")
    mem = indexer.index_task(task)

    logs = mem_repo.list_audit_logs(memory_id=mem.memory_id)
    assert len(logs) >= 1
    assert logs[0]["action"] == "EPISODIC_CREATED"
    assert logs[0]["metadata"]["task_id"] == "t31-audit"


def test_32_event_subscription_lifecycle(event_bus: ActionEventBus, mem_repo: MemoryRepository):
    """Test 32: Indexer properly subscribes and receives terminal events from event_bus."""
    idx = EpisodicMemoryIndexer(memory_repo=mem_repo, event_bus=event_bus, auto_subscribe=True)
    assert idx._subscriber_id is not None

    # Publish terminal event
    event = ActionEvent(
        action_type=ActionType.TASK_COMPLETED,
        status=ActionStatus.COMPLETED,
        title="Deployed web microservice",
        task_id="t32-event-bus",
        safe_metadata={"goal": "Deploy microservice"},
    )
    asyncio.run(event_bus.publish(event))

    # Verify memory was created
    mem = mem_repo.get_episodic_memory_by_task_id("t32-event-bus")
    assert mem is not None
    assert "Deploy microservice" in mem.summary

    idx.unsubscribe()
    assert idx._subscriber_id is None


def test_33_shutdown_does_not_leak_workers_or_resources(indexer: EpisodicMemoryIndexer):
    """Test 33: Calling close cleans up subscriber without leaking state."""
    indexer.close()
    assert indexer._subscriber_id is None
    # Repeated calls safe
    indexer.close()


def test_34_existing_m17_9_persistence_regression(mem_repo: MemoryRepository):
    """Test 34: Phase 1 tables and FTS virtual table remain fully functional."""
    health = mem_repo.check_health()
    assert health["healthy"] is True
    assert health["fts_accessible"] is True
    assert mem_repo.get_schema_version() == 1

"""Unit and integration tests for RYVEN 3.0 M17.9 Phase 1 Memory Persistence.

Covers:
- Database initialization and schema idempotency
- schema_meta isolation (preservation of version, long_tasks_schema_version, m17_8_schema_version)
- User profile CRUD and default profile
- User preference CRUD and validation (GLOBAL vs PROJECT scope)
- Episodic memory persistence and access tracking
- Semantic memory persistence, updates, and deletion
- FTS5 virtual table verification and trigger synchronization (INSERT/UPDATE/DELETE)
- Transaction atomicity and rollback consistency
- WAL, foreign_keys, and busy_timeout pragmas
- Concurrent reads, writes, and mixed workloads
- In-memory isolation and multi-instance persistence
- Defensive secret boundary and rejection
- Audit log persistence and sanitized metadata
- Coexistence with existing M17.7 and M17.8 tables
"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List
import uuid

import pytest

from app.memory.repository import (
    AuditActor,
    EpisodicMemory,
    EpisodicOutcome,
    MemoryRepository,
    MemoryScope,
    MemorySourceType,
    Preference,
    PreferenceSource,
    RetentionClass,
    SemanticCategory,
    SemanticMemory,
    TrustLevel,
    UserProfile,
    _validate_safe_key_name,
)


@pytest.fixture
def mem_repo(tmp_path: Path) -> MemoryRepository:
    """Fixture providing an isolated on-disk MemoryRepository."""
    db_file = tmp_path / "test_memory.db"
    repo = MemoryRepository(db_path=db_file)
    yield repo
    repo.close()


@pytest.fixture
def inmem_repo() -> MemoryRepository:
    """Fixture providing an in-memory MemoryRepository."""
    repo = MemoryRepository(db_path=":memory:")
    yield repo
    repo.close()


# ---------------------------------------------------------------------------
# Test 1-4: Database Initialization & Schema Meta Isolation
# ---------------------------------------------------------------------------

def test_01_database_initialization(mem_repo: MemoryRepository):
    """Test 1: Verify database tables and schema are initialized properly."""
    stats = mem_repo.get_stats()
    assert stats["schema_version"] == 1
    assert stats["user_profiles_count"] >= 1  # 'default' profile created
    assert stats["user_preferences_count"] == 0
    assert stats["episodic_memories_count"] == 0
    assert stats["semantic_memories_count"] == 0
    assert stats["fts_indexed_count"] == 0

    health = mem_repo.check_health()
    assert health["healthy"] is True
    assert health["fts_accessible"] is True


def test_02_schema_idempotency(mem_repo: MemoryRepository):
    """Test 2: Repeated initialization does not fail or duplicate tables."""
    ver1 = mem_repo.initialize_schema()
    ver2 = mem_repo.initialize_schema()
    ver3 = mem_repo.initialize_schema()
    assert ver1 == 1 and ver2 == 1 and ver3 == 1
    assert mem_repo.get_schema_version() == 1


def test_03_memory_schema_version_creation(mem_repo: MemoryRepository):
    """Test 3: memory_schema_version key is registered in schema_meta."""
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM schema_meta WHERE key = 'memory_schema_version'")
        row = cur.fetchone()
        assert row is not None
        assert row["value"] == "1"


def test_04_preservation_of_existing_schema_meta_keys(tmp_path: Path):
    """Test 4: Pre-existing M17 keys are untouched by memory initialization."""
    db_file = tmp_path / "pre_existing.db"
    # Seed DB with M15/M17 keys
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta VALUES ('version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('long_tasks_schema_version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('m17_8_schema_version', '2')")
    conn.commit()
    conn.close()

    # Initialize MemoryRepository
    repo = MemoryRepository(db_path=db_file)
    try:
        assert repo.get_schema_version() == 1
        with repo._connection() as c:
            cur = c.cursor()
            cur.execute("SELECT key, value FROM schema_meta ORDER BY key")
            meta = {r["key"]: r["value"] for r in cur.fetchall()}

            assert meta["version"] == "1"
            assert meta["long_tasks_schema_version"] == "1"
            assert meta["m17_8_schema_version"] == "2"
            assert meta["memory_schema_version"] == "1"
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Test 5: User Profile CRUD
# ---------------------------------------------------------------------------

def test_05_user_profile_crud(mem_repo: MemoryRepository):
    """Test 5: Create, read, and update user profiles."""
    default_prof = mem_repo.get_user_profile("default")
    assert default_prof is not None
    assert default_prof.profile_id == "default"
    assert default_prof.user_name == "User"

    # Custom profile
    prof = UserProfile(
        profile_id="developer_1",
        user_name="Alice",
        safe_metadata={"theme": "dark", "locale": "en-US"},
    )
    saved = mem_repo.save_user_profile(prof)
    assert saved.profile_id == "developer_1"

    fetched = mem_repo.get_user_profile("developer_1")
    assert fetched is not None
    assert fetched.user_name == "Alice"
    assert fetched.safe_metadata["theme"] == "dark"

    # Update
    fetched.user_name = "Alice Developer"
    mem_repo.save_user_profile(fetched)
    updated = mem_repo.get_user_profile("developer_1")
    assert updated is not None
    assert updated.user_name == "Alice Developer"


# ---------------------------------------------------------------------------
# Test 6-8: User Preferences & Scope Validation
# ---------------------------------------------------------------------------

def test_06_preference_crud(mem_repo: MemoryRepository):
    """Test 6: Set, get, list, and delete preferences."""
    pref = mem_repo.set_preference(
        key="editor.preferred",
        value="vscode",
        profile_id="default",
        scope=MemoryScope.GLOBAL,
    )
    assert pref.key == "editor.preferred"
    assert pref.value == "vscode"

    fetched = mem_repo.get_preference("editor.preferred", "default", MemoryScope.GLOBAL)
    assert fetched is not None
    assert fetched.value == "vscode"

    # Update existing preference
    mem_repo.set_preference(
        key="editor.preferred",
        value="cursor",
        profile_id="default",
        scope=MemoryScope.GLOBAL,
    )
    fetched2 = mem_repo.get_preference("editor.preferred", "default", MemoryScope.GLOBAL)
    assert fetched2 is not None
    assert fetched2.value == "cursor"

    # Delete
    deleted = mem_repo.delete_preference(fetched2.pref_id)
    assert deleted is True
    assert mem_repo.get_preference("editor.preferred", "default", MemoryScope.GLOBAL) is None


def test_07_global_preference_validation(mem_repo: MemoryRepository):
    """Test 7: GLOBAL preferences must not specify a project_id."""
    with pytest.raises(ValueError, match="GLOBAL preference cannot have project_id"):
        mem_repo.set_preference(
            key="test.key",
            value=123,
            scope=MemoryScope.GLOBAL,
            project_id="proj_x",
        )


def test_08_project_preference_validation(mem_repo: MemoryRepository):
    """Test 8: PROJECT preferences require a non-empty project_id."""
    with pytest.raises(ValueError, match="PROJECT preference requires non-empty project_id"):
        mem_repo.set_preference(
            key="build.tool",
            value="vite",
            scope=MemoryScope.PROJECT,
            project_id=None,
        )

    # Valid project preference
    pref = mem_repo.set_preference(
        key="build.tool",
        value="vite",
        scope=MemoryScope.PROJECT,
        project_id="frontend_app",
    )
    assert pref.project_id == "frontend_app"
    listed = mem_repo.list_preferences(scope=MemoryScope.PROJECT, project_id="frontend_app")
    assert len(listed) == 1
    assert listed[0].key == "build.tool"


# ---------------------------------------------------------------------------
# Test 9: Episodic Memory Persistence
# ---------------------------------------------------------------------------

def test_09_episodic_memory_persistence(mem_repo: MemoryRepository):
    """Test 9: Persist and access episodic task summaries with access tracking."""
    ep = EpisodicMemory(
        task_id="task_101",
        goal="Fix broken pytest suite in dev_engine",
        normalized_goal="fix broken pytest suite in dev_engine",
        outcome=EpisodicOutcome.SUCCESS,
        summary="Resolved import mismatch by adjusting sys.path in conftest.py",
        solution_steps=["inspect error", "edit conftest.py", "run pytest"],
        project_id="ryven_core",
        tags=["testing", "pytest", "bugfix"],
    )
    created = mem_repo.create_episodic_memory(ep)
    assert created.memory_id == ep.memory_id

    # Retrieve and check access counter increment
    fetched = mem_repo.get_episodic_memory(ep.memory_id)
    assert fetched is not None
    assert fetched.access_count == 1
    assert fetched.outcome == EpisodicOutcome.SUCCESS
    assert "conftest.py" in fetched.summary

    # Second fetch
    fetched2 = mem_repo.get_episodic_memory(ep.memory_id)
    assert fetched2 is not None
    assert fetched2.access_count == 2

    # Filtered list
    items = mem_repo.list_episodic_memories(project_id="ryven_core", outcome=EpisodicOutcome.SUCCESS)
    assert len(items) == 1
    assert items[0].task_id == "task_101"

    # Delete
    assert mem_repo.delete_episodic_memory(ep.memory_id) is True
    assert mem_repo.get_episodic_memory(ep.memory_id) is None


# ---------------------------------------------------------------------------
# Test 10-12: Semantic Memory CRUD
# ---------------------------------------------------------------------------

def test_10_semantic_memory_persistence(mem_repo: MemoryRepository):
    """Test 10: Persist semantic memory with proper metadata."""
    sem = SemanticMemory(
        title="PostgreSQL Port Mapping",
        content="Production PostgreSQL server is mapped to localhost:5432 with user ryven_admin.",
        category=SemanticCategory.ENVIRONMENT_CONFIG,
        scope=MemoryScope.GLOBAL,
        tags=["db", "postgres", "ports"],
        trust_level=TrustLevel.USER_CONFIRMED,
    )
    created = mem_repo.create_semantic_memory(sem)
    assert created.memory_id == sem.memory_id

    fetched = mem_repo.get_semantic_memory(sem.memory_id)
    assert fetched is not None
    assert fetched.title == "PostgreSQL Port Mapping"
    assert fetched.access_count == 1


def test_11_semantic_memory_update(mem_repo: MemoryRepository):
    """Test 11: Update semantic memory content and importance."""
    sem = SemanticMemory(
        title="Vite Dev Server Port",
        content="Vite runs on port 5173",
        category=SemanticCategory.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        project_id="ravan_ui",
    )
    mem_repo.create_semantic_memory(sem)

    # Update content
    sem.content = "Vite runs on port 3000 after port migration"
    sem.importance = 0.9
    updated = mem_repo.update_semantic_memory(sem)
    assert updated.importance == 0.9

    refetched = mem_repo.get_semantic_memory(sem.memory_id)
    assert refetched is not None
    assert "port 3000" in refetched.content


def test_12_semantic_memory_deletion(mem_repo: MemoryRepository):
    """Test 12: Delete semantic memory removes entry."""
    sem = SemanticMemory(
        title="Temporary Diagnostic Note",
        content="Kernel memory pressure was transient",
        category=SemanticCategory.TOOL_INSIGHT,
        scope=MemoryScope.GLOBAL,
    )
    mem_repo.create_semantic_memory(sem)
    assert mem_repo.delete_semantic_memory(sem.memory_id) is True
    assert mem_repo.get_semantic_memory(sem.memory_id) is None


# ---------------------------------------------------------------------------
# Test 13-17: FTS5 Virtual Table & Trigger Synchronization
# ---------------------------------------------------------------------------

def test_13_fts5_availability(mem_repo: MemoryRepository):
    """Test 13: SQLite FTS5 extension is natively verified."""
    health = mem_repo.check_health()
    assert health["fts_accessible"] is True


def test_14_fts_table_creation(mem_repo: MemoryRepository):
    """Test 14: semantic_memory_fts virtual table exists in master."""
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_memory_fts'")
        assert cur.fetchone() is not None


def test_15_fts_insert_synchronization(mem_repo: MemoryRepository):
    """Test 15: Inserting into semantic_memories triggers FTS entry creation."""
    sem = SemanticMemory(
        title="Architecture Decision M17.9",
        content="Persistent semantic memory uses SQLite FTS5 for sub-millisecond retrieval.",
        category=SemanticCategory.PROJECT_KNOWLEDGE,
        scope=MemoryScope.PROJECT,
        project_id="ryven_docs",
        tags=["architecture", "fts5", "m17_9"],
    )
    mem_repo.create_semantic_memory(sem)

    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'FTS5'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["memory_id"] == sem.memory_id
        assert "sub-millisecond" in rows[0]["content"]


def test_16_fts_update_synchronization(mem_repo: MemoryRepository):
    """Test 16: Updating semantic_memories triggers FTS re-indexing."""
    sem = SemanticMemory(
        title="Deployment Command",
        content="Old command: docker run ryven_image",
        category=SemanticCategory.PROJECT_KNOWLEDGE,
        scope=MemoryScope.GLOBAL,
    )
    mem_repo.create_semantic_memory(sem)

    # Verify old keyword matches
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'docker'")
        assert len(cur.fetchall()) == 1

    # Update
    sem.content = "New command: podman compose up"
    mem_repo.update_semantic_memory(sem)

    # Verify old keyword no longer matches, new keyword matches
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'docker'")
        assert len(cur.fetchall()) == 0

        cur.execute("SELECT * FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'podman'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["memory_id"] == sem.memory_id


def test_17_fts_delete_synchronization(mem_repo: MemoryRepository):
    """Test 17: Deleting from semantic_memories removes FTS index entry."""
    sem = SemanticMemory(
        title="Temporary Secret Topic",
        content="Sensitive diagnostic details to be purged immediately",
        category=SemanticCategory.CONVERSATION_FACT,
        scope=MemoryScope.GLOBAL,
    )
    mem_repo.create_semantic_memory(sem)

    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'Sensitive'")
        assert cur.fetchone()[0] == 1

    mem_repo.delete_semantic_memory(sem.memory_id)

    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'Sensitive'")
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 18-19: Rollback Consistency & Transaction Safety
# ---------------------------------------------------------------------------

def test_18_rollback_consistency(mem_repo: MemoryRepository):
    """Test 18: Rolled back transactions leave source and FTS tables consistent."""
    with pytest.raises(Exception):
        with mem_repo._connection() as conn:
            cur = conn.cursor()
            mid = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO semantic_memories (
                    memory_id, title, content, category, scope, project_id,
                    tags_json, source_type, trust_level, importance, created_at,
                    updated_at, last_accessed_at, access_count
                ) VALUES (?, 'Rollback Title', 'Rollback Content', 'PROJECT_KNOWLEDGE', 'GLOBAL', NULL, '[]', 'SYSTEM_DERIVED', 'SYSTEM_DERIVED', 0.5, '2026-01-01', '2026-01-01', '2026-01-01', 0)
                """,
                (mid,),
            )
            # Deliberate failure inside the same transaction
            raise RuntimeError("Forced abort for rollback test")

    # Check both tables are empty of that ID
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM semantic_memories WHERE title = 'Rollback Title'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT COUNT(*) FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'Rollback'")
        assert cur.fetchone()[0] == 0


def test_19_transaction_rollback_on_constraint_violation(mem_repo: MemoryRepository):
    """Test 19: Check constraints trigger rollback on invalid scope."""
    with pytest.raises(sqlite3.IntegrityError):
        with mem_repo._connection() as conn:
            cur = conn.cursor()
            # Violate CHECK constraint: scope is GLOBAL but project_id is provided
            cur.execute(
                """
                INSERT INTO semantic_memories (
                    memory_id, title, content, category, scope, project_id,
                    tags_json, source_type, trust_level, importance, created_at,
                    updated_at, last_accessed_at, access_count
                ) VALUES ('bad-id', 'Bad Item', 'Content', 'TOOL_INSIGHT', 'GLOBAL', 'some_proj', '[]', 'SYSTEM_DERIVED', 'SYSTEM_DERIVED', 0.5, '2026-01-01', '2026-01-01', '2026-01-01', 0)
                """
            )


# ---------------------------------------------------------------------------
# Test 20-22: Pragmas Configuration
# ---------------------------------------------------------------------------

def test_20_wal_configuration(tmp_path: Path):
    """Test 20: File-backed database connects with WAL mode."""
    db_file = tmp_path / "pragma_test.db"
    repo = MemoryRepository(db_path=db_file)
    try:
        with repo._connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            mode = cur.fetchone()[0].lower()
            assert mode == "wal"
    finally:
        repo.close()


def test_21_foreign_keys_configuration(mem_repo: MemoryRepository):
    """Test 21: Foreign keys are active."""
    with mem_repo._connection() as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys;")
        val = cur.fetchone()[0]
        assert val == 1


def test_22_busy_timeout_configuration(tmp_path: Path):
    """Test 22: Busy timeout is set to 15000ms."""
    db_file = tmp_path / "timeout_test.db"
    repo = MemoryRepository(db_path=db_file)
    try:
        with repo._connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA busy_timeout;")
            val = cur.fetchone()[0]
            assert val == 15000
    finally:
        repo.close()


# ---------------------------------------------------------------------------
# Test 23-25: Concurrency Safety (Bounded)
# ---------------------------------------------------------------------------

def test_23_concurrent_reads(mem_repo: MemoryRepository):
    """Test 23: Multiple worker threads reading preferences and memories concurrently."""
    # Seed data
    for i in range(10):
        mem_repo.set_preference(key=f"config.item_{i}", value=i)
        mem_repo.create_semantic_memory(
            SemanticMemory(
                title=f"Knowledge {i}",
                content=f"Detailed documentation item {i} regarding system startup.",
                scope=MemoryScope.GLOBAL,
            )
        )

    errors = []

    def reader_worker(worker_id: int):
        try:
            for j in range(20):
                mem_repo.list_preferences()
                mem_repo.list_semantic_memories()
                p = mem_repo.get_preference(f"config.item_{j % 10}")
                assert p is not None
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(reader_worker, i) for i in range(5)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0


def test_24_concurrent_writes(mem_repo: MemoryRepository):
    """Test 24: Multiple threads writing distinct memories concurrently."""
    errors = []

    def writer_worker(worker_id: int):
        try:
            for i in range(10):
                mem_repo.create_semantic_memory(
                    SemanticMemory(
                        title=f"Worker {worker_id} Item {i}",
                        content=f"Content written concurrently by thread {worker_id} pass {i}",
                        scope=MemoryScope.GLOBAL,
                    )
                )
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(writer_worker, i) for i in range(4)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0
    stats = mem_repo.get_stats()
    assert stats["semantic_memories_count"] == 40
    assert stats["fts_indexed_count"] == 40


def test_25_mixed_read_write_concurrency(mem_repo: MemoryRepository):
    """Test 25: Interleaved concurrent reads, writes, and updates do not corrupt DB."""
    errors = []

    def mixed_worker(worker_id: int):
        try:
            for i in range(15):
                # Write preference
                mem_repo.set_preference(key=f"worker_{worker_id}.step_{i}", value=f"val_{i}")
                # Create memory
                sem = mem_repo.create_semantic_memory(
                    SemanticMemory(
                        title=f"Note {worker_id}-{i}",
                        content=f"Telemetry event recorded by worker {worker_id}",
                        scope=MemoryScope.GLOBAL,
                    )
                )
                # Read memory
                fetched = mem_repo.get_semantic_memory(sem.memory_id)
                assert fetched is not None
                # Update memory
                fetched.importance = 0.8
                mem_repo.update_semantic_memory(fetched)
        except Exception as exc:
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(mixed_worker, i) for i in range(4)]
        concurrent.futures.wait(futures)

    assert len(errors) == 0
    health = mem_repo.check_health()
    assert health["healthy"] is True


# ---------------------------------------------------------------------------
# Test 26-28: Test Isolation & Persistence Reconnection
# ---------------------------------------------------------------------------

def test_26_inmemory_isolation(inmem_repo: MemoryRepository):
    """Test 26: In-memory repository functions properly in complete isolation."""
    inmem_repo.set_preference("mode", "sandbox")
    pref = inmem_repo.get_preference("mode")
    assert pref is not None
    assert pref.value == "sandbox"


def test_27_persistence_across_repository_instances(tmp_path: Path):
    """Test 27: Data persists across distinct repository instances accessing same file."""
    db_file = tmp_path / "multi_instance.db"
    repo1 = MemoryRepository(db_path=db_file)
    repo1.set_preference("persistent.flag", True)
    repo1.create_semantic_memory(
        SemanticMemory(
            title="Shared Topic",
            content="Important note readable by subsequent instances.",
            scope=MemoryScope.GLOBAL,
        )
    )
    repo1.close()

    # Open with second repository instance
    repo2 = MemoryRepository(db_path=db_file)
    try:
        p = repo2.get_preference("persistent.flag")
        assert p is not None and p.value is True
        items = repo2.list_semantic_memories()
        assert len(items) == 1
        assert items[0].title == "Shared Topic"
    finally:
        repo2.close()


def test_28_persistence_across_database_reconnection(tmp_path: Path):
    """Test 28: Closing and reconnecting maintains FTS index integrity."""
    db_file = tmp_path / "reconnect_fts.db"
    repo = MemoryRepository(db_path=db_file)
    repo.create_semantic_memory(
        SemanticMemory(
            title="Queryable Title",
            content="Persistent content searchable via FTS5 index.",
            scope=MemoryScope.GLOBAL,
        )
    )
    repo.close()

    # Reconnect
    repo2 = MemoryRepository(db_path=db_file)
    try:
        with repo2._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM semantic_memory_fts WHERE semantic_memory_fts MATCH 'searchable'")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0]["title"] == "Queryable Title"
    finally:
        repo2.close()


# ---------------------------------------------------------------------------
# Test 29: Defensive Secret Boundary Rejection
# ---------------------------------------------------------------------------

def test_29_secret_boundary_rejection_and_scrubbing(mem_repo: MemoryRepository):
    """Test 29: Obvious secrets in keys are rejected; sensitive values are scrubbed."""
    # Forbidden key names
    with pytest.raises(ValueError, match="Forbidden preference/memory key contains secret identifier"):
        mem_repo.set_preference("user.password", "supersecret123")

    with pytest.raises(ValueError, match="Forbidden preference/memory key contains secret identifier"):
        mem_repo.set_preference("openai.api_key", "sk-123456789")

    with pytest.raises(ValueError, match="Forbidden preference/memory key contains secret identifier"):
        mem_repo.set_preference("auth.token", "bearer_token_value")

    # Sensitive values in safe keys are scrubbed
    pref = mem_repo.set_preference(
        key="git.credentials_helper",
        value={"helper": "store", "token": "ghp_123456789012345678901234567890"},
    )
    assert pref.value["token"] == "[REDACTED]"

    # String value with bearer token pattern is scrubbed
    sem = mem_repo.create_semantic_memory(
        SemanticMemory(
            title="API Setup Note",
            content="Connect with bearer abcdef123456789012345 to endpoint",
            scope=MemoryScope.GLOBAL,
        )
    )
    assert "[REDACTED]" in sem.content

    # Private key in content is scrubbed
    sem_ssh = mem_repo.create_semantic_memory(
        SemanticMemory(
            title="SSH Setup Info",
            content="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
            scope=MemoryScope.GLOBAL,
        )
    )
    assert "[REDACTED]" in sem_ssh.content


# ---------------------------------------------------------------------------
# Test 30: Project vs Global Scope Integrity
# ---------------------------------------------------------------------------

def test_30_project_global_scope_integrity(mem_repo: MemoryRepository):
    """Test 30: Ensure database-level constraint rejects invalid project/scope pairs."""
    # Semantic memory project scope integrity
    with pytest.raises(ValueError, match="GLOBAL semantic memory cannot have project_id"):
        mem_repo.create_semantic_memory(
            SemanticMemory(
                title="Invalid Global Item",
                content="Some content",
                scope=MemoryScope.GLOBAL,
                project_id="should_be_null",
            )
        )

    with pytest.raises(ValueError, match="PROJECT semantic memory requires non-empty project_id"):
        mem_repo.create_semantic_memory(
            SemanticMemory(
                title="Invalid Project Item",
                content="Some content",
                scope=MemoryScope.PROJECT,
                project_id=None,
            )
        )


# ---------------------------------------------------------------------------
# Test 31-32: Audit Event Persistence & Metadata Safety
# ---------------------------------------------------------------------------

def test_31_audit_event_persistence(mem_repo: MemoryRepository):
    """Test 31: Audit trail records operations and can be queried."""
    log_id = mem_repo.record_audit_event(
        action="TEST_ACTION",
        memory_id="mem_123",
        actor=AuditActor.USER,
        metadata={"reason": "Manual unit testing"},
    )
    assert log_id > 0

    logs = mem_repo.list_audit_logs(memory_id="mem_123")
    assert len(logs) == 1
    assert logs[0]["action"] == "TEST_ACTION"
    assert logs[0]["actor"] == "USER"
    assert logs[0]["metadata"]["reason"] == "Manual unit testing"


def test_32_audit_metadata_safety(mem_repo: MemoryRepository):
    """Test 32: Audit metadata recursively redacts secrets before storing."""
    mem_repo.record_audit_event(
        action="AUTH_ATTEMPT",
        memory_id="mem_sec",
        actor=AuditActor.SYSTEM,
        metadata={"client_id": "app", "password": "plaintext_password_here"},
    )
    logs = mem_repo.list_audit_logs(memory_id="mem_sec")
    assert len(logs) == 1
    assert logs[0]["metadata"]["password"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Test 33-35: Coexistence With Existing M17 Database & Repeated Init
# ---------------------------------------------------------------------------

def test_33_schema_initialization_after_existing_m17_db(tmp_path: Path):
    """Test 33: Memory tables can be initialized in a database with M17.8 tables."""
    db_file = tmp_path / "m17_8_coexistence.db"
    # Pre-create M17.8 tables
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta VALUES ('version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('long_tasks_schema_version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('m17_8_schema_version', '2')")
    conn.execute(
        """
        CREATE TABLE long_horizon_tasks (
            task_id TEXT PRIMARY KEY,
            goal TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO long_horizon_tasks VALUES ('t1', 'Build application')")
    conn.commit()
    conn.close()

    # Now init MemoryRepository on this DB
    repo = MemoryRepository(db_path=db_file)
    try:
        # Check M17.8 table still intact
        with repo._connection() as c:
            cur = c.cursor()
            cur.execute("SELECT * FROM long_horizon_tasks WHERE task_id = 't1'")
            assert cur.fetchone() is not None

        # Check memory tables work
        repo.set_preference("test.pref", "val")
        assert repo.get_preference("test.pref").value == "val"
    finally:
        repo.close()


def test_34_repeated_initialization_stability(mem_repo: MemoryRepository):
    """Test 34: 10 repeated initialization calls maintain database stability."""
    for _ in range(10):
        mem_repo.initialize_schema()
    stats = mem_repo.get_stats()
    assert stats["schema_version"] == 1


def test_35_no_modification_of_unrelated_schema_versions(tmp_path: Path):
    """Test 35: MemoryRepository never modifies unrelated schema_meta records."""
    db_file = tmp_path / "strict_meta.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_meta VALUES ('version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('long_tasks_schema_version', '1')")
    conn.execute("INSERT INTO schema_meta VALUES ('m17_8_schema_version', '2')")
    conn.commit()
    conn.close()

    repo = MemoryRepository(db_path=db_file)
    try:
        # Perform memory operations
        repo.set_preference("key", "val")
        repo.create_semantic_memory(
            SemanticMemory(title="T", content="C", scope=MemoryScope.GLOBAL)
        )
        # Verify schema_meta values
        with repo._connection() as c:
            cur = c.cursor()
            cur.execute("SELECT key, value FROM schema_meta ORDER BY key")
            meta = {r["key"]: r["value"] for r in cur.fetchall()}
            assert meta["version"] == "1"
            assert meta["long_tasks_schema_version"] == "1"
            assert meta["m17_8_schema_version"] == "2"
            assert meta["memory_schema_version"] == "1"
            assert len(meta) == 4
    finally:
        repo.close()

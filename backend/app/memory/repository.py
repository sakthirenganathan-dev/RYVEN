"""RYVEN 3.0 — Milestone 17.9 Phase 1 Memory Persistence Repository.

Durable, thread-safe, transactional SQLite storage for:
- User Profiles & Preferences (Global & Project scoped)
- Episodic Task History & Provenance
- Semantic Memories & Context (FTS5 indexed)
- Memory Audit Trail

Design & Security Invariants:
- PERSISTENCE ONLY. Never executes OS commands, subprocesses, or background agents.
- Pure local SQLite storage; zero cloud/external vector database dependencies.
- Shared database `.ryven/checkpoints.db` using isolated schema_meta key 'memory_schema_version'.
- Thread-safe execution via threading.RLock and short-lived connection scopes.
- Strict secret redaction on all payloads before persistence.
- Hard data isolation: GLOBAL memories require NULL project_id; PROJECT memories require project_id.
- Real-time SQLite FTS5 synchronization via transactional database triggers.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from pydantic import BaseModel, Field

from app.core.logging_config import logger


# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent / ".ryven"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "checkpoints.db"
MEMORY_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    """Helper returning current UTC ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Defensive Secret Boundary
# ---------------------------------------------------------------------------

_FORBIDDEN_KEY_PATTERNS: Set[str] = {
    "password", "passwd", "token", "secret", "api_key", "apikey",
    "access_token", "refresh_token", "bearer", "private_key", "privatekey",
    "ssh_key", "cookie", "authorization", "credential", "credentials",
    "confirmation_token", "auth_token", "client_secret",
}

_SENSITIVE_STRING_REGEXES = [
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"(?:bearer\s+[a-zA-Z0-9_\-\.]{15,})", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key[\s:=]+['\"]?[a-zA-Z0-9_\-\.]{10,}['\"]?)", re.IGNORECASE),
    re.compile(r"(?:password[\s:=]+['\"]?[^\s'\"]{6,}['\"]?)", re.IGNORECASE),
    re.compile(r"(?:confirmation[_-]?token[\s:=]+['\"]?[a-zA-Z0-9_\-\.]{8,}['\"]?)", re.IGNORECASE),
    re.compile(r"\b(?:tok|token|secret)_[a-zA-Z0-9_\-\.]{8,}\b", re.IGNORECASE),
]


def _scrub_value_defensive(val: Any) -> Any:
    """Recursively scrub secrets from dicts, lists, and strings."""
    if isinstance(val, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k).lower()
            if any(pat in k_str for pat in _FORBIDDEN_KEY_PATTERNS):
                cleaned[k] = "[REDACTED]" if v is not None else None
            else:
                cleaned[k] = _scrub_value_defensive(v)
        return cleaned
    elif isinstance(val, list):
        return [_scrub_value_defensive(item) for item in val]
    elif isinstance(val, str):
        result = val
        if "<think>" in result and "</think>" in result:
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL)
        for pattern in _SENSITIVE_STRING_REGEXES:
            result = pattern.sub("[REDACTED]", result)
        return result
    return val


def _validate_safe_key_name(key: str) -> None:
    """Reject keys explicitly attempting to store raw secrets."""
    k_lower = key.lower().strip()
    parts = set(re.split(r"[._\-\s/]+", k_lower))
    forbidden_tokens = {
        "password", "passwd", "token", "secret", "api_key", "apikey",
        "access_token", "refresh_token", "private_key", "privatekey",
        "ssh_key", "bearer", "auth_token", "client_secret",
        "confirmation_token",
    }
    if k_lower in forbidden_tokens or any(t in forbidden_tokens for t in parts):
        raise ValueError(f"Forbidden preference/memory key contains secret identifier: '{key}'")
    if "api_key" in k_lower or "private_key" in k_lower or "access_token" in k_lower:
        raise ValueError(f"Forbidden preference/memory key contains secret identifier: '{key}'")


# ---------------------------------------------------------------------------
# Domain Enums
# ---------------------------------------------------------------------------

class MemoryScope(str, Enum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"


class PreferenceSource(str, Enum):
    USER_EXPLICIT = "USER_EXPLICIT"
    SYSTEM_INFERRED = "SYSTEM_INFERRED"


class EpisodicOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"


class SemanticCategory(str, Enum):
    PROJECT_KNOWLEDGE = "PROJECT_KNOWLEDGE"
    CONVERSATION_FACT = "CONVERSATION_FACT"
    TOOL_INSIGHT = "TOOL_INSIGHT"
    ENVIRONMENT_CONFIG = "ENVIRONMENT_CONFIG"


class MemorySourceType(str, Enum):
    USER_CONFIRMED = "USER_CONFIRMED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    TASK_DERIVED = "TASK_DERIVED"
    DOCUMENT_DERIVED = "DOCUMENT_DERIVED"
    WEB_DERIVED = "WEB_DERIVED"


class TrustLevel(str, Enum):
    USER_CONFIRMED = "USER_CONFIRMED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    TASK_DERIVED = "TASK_DERIVED"
    DOCUMENT_DERIVED = "DOCUMENT_DERIVED"
    WEB_DERIVED = "WEB_DERIVED"


class RetentionClass(str, Enum):
    EPHEMERAL = "EPHEMERAL"
    STANDARD = "STANDARD"
    PERMANENT = "PERMANENT"


class AuditActor(str, Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    EXPIRATION = "EXPIRATION"


# ---------------------------------------------------------------------------
# Domain Models (Pydantic)
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    """User profile for persistent personalization."""
    profile_id: str = "default"
    user_name: str = "User"
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)


class Preference(BaseModel):
    """Durable user preference record."""
    pref_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile_id: str = "default"
    key: str
    value: Any
    scope: MemoryScope = MemoryScope.GLOBAL
    project_id: Optional[str] = None
    source: PreferenceSource = PreferenceSource.USER_EXPLICIT
    confidence: float = 1.0
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)


class EpisodicMemory(BaseModel):
    """Compact summary of a completed or failed task."""
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    goal: str
    normalized_goal: str
    outcome: EpisodicOutcome
    summary: str
    solution_steps: List[str] = Field(default_factory=list)
    failure_class: Optional[str] = None
    project_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    retention_class: RetentionClass = RetentionClass.STANDARD
    created_at: str = Field(default_factory=_utc_now_iso)
    last_accessed_at: str = Field(default_factory=_utc_now_iso)
    access_count: int = 0


class SemanticMemory(BaseModel):
    """Durable factual or contextual knowledge record."""
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    content: str
    category: SemanticCategory = SemanticCategory.PROJECT_KNOWLEDGE
    scope: MemoryScope = MemoryScope.GLOBAL
    project_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_type: MemorySourceType = MemorySourceType.USER_CONFIRMED
    trust_level: TrustLevel = TrustLevel.USER_CONFIRMED
    importance: float = 0.5
    expires_at: Optional[str] = None
    retention_class: RetentionClass = RetentionClass.STANDARD
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    last_accessed_at: str = Field(default_factory=_utc_now_iso)
    access_count: int = 0


class MemoryAuditRecord(BaseModel):
    """Audit log entry for memory mutations."""
    audit_id: Optional[int] = None
    action: str
    memory_id: Optional[str] = None
    actor: AuditActor = AuditActor.SYSTEM
    timestamp: str = Field(default_factory=_utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Memory Repository Implementation
# ---------------------------------------------------------------------------

class MemoryRepository:
    """Thread-safe, transactional SQLite repository for RYVEN M17.9 memory.
    
    Invariants:
    - Independent schema_meta key 'memory_schema_version' = 1.
    - Zero modifications to other schema_meta keys.
    - Defensive secret scrubbing on all payloads.
    - Full transaction safety with automatic FTS5 trigger sync.
    - Safe concurrency under WAL journal mode.
    """

    SCHEMA_VERSION = MEMORY_SCHEMA_VERSION

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = db_path if db_path and str(db_path) == ":memory:" else (Path(db_path) if db_path else DEFAULT_DB_PATH)
        self._lock = threading.RLock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._verify_fts5_support()
        self.initialize_schema()

    def _verify_fts5_support(self) -> None:
        """Fail fast if the SQLite runtime environment lacks FTS5 extension."""
        try:
            probe = sqlite3.connect(":memory:")
            try:
                probe.execute("CREATE VIRTUAL TABLE _probe_fts USING fts5(content);")
                probe.execute("DROP TABLE _probe_fts;")
            finally:
                probe.close()
        except Exception as exc:
            raise RuntimeError(
                f"[MEMORY_REPOSITORY] SQLite FTS5 extension is unavailable: {exc}. "
                "RYVEN M17.9 requires native FTS5 support for full-text memory retrieval."
            ) from exc

    @contextmanager
    def _connection(self):
        """Thread-safe database connection context manager."""
        if str(self.db_path) == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
                try:
                    self._mem_conn.execute("PRAGMA foreign_keys=ON;")
                except Exception:
                    pass
            with self._mem_conn:
                yield self._mem_conn
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=15000;")
            conn.execute("PRAGMA foreign_keys=ON;")
        except Exception:
            pass
        try:
            with conn:
                yield conn
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def initialize_schema(self) -> int:
        """Idempotently initialize all Phase 1 memory tables, indexes, and triggers."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()

                # 1. Ensure schema_meta exists without modifying existing keys
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )

                # Set memory_schema_version if not present
                cur.execute("SELECT value FROM schema_meta WHERE key = 'memory_schema_version'")
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO schema_meta (key, value) VALUES ('memory_schema_version', ?)",
                        (str(self.SCHEMA_VERSION),),
                    )

                # 2. User Profiles
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        profile_id TEXT PRIMARY KEY,
                        user_name TEXT NOT NULL,
                        safe_metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

                # 3. User Preferences
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_preferences (
                        pref_id TEXT PRIMARY KEY,
                        profile_id TEXT NOT NULL REFERENCES user_profiles(profile_id) ON DELETE CASCADE,
                        key TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        scope TEXT NOT NULL CHECK(scope IN ('GLOBAL', 'PROJECT')),
                        project_id TEXT,
                        source TEXT NOT NULL CHECK(source IN ('USER_EXPLICIT', 'SYSTEM_INFERRED')),
                        confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK(
                            (scope = 'GLOBAL' AND project_id IS NULL) OR
                            (scope = 'PROJECT' AND project_id IS NOT NULL AND length(project_id) > 0)
                        )
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_pref_global
                    ON user_preferences(profile_id, key) WHERE scope = 'GLOBAL'
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_pref_project
                    ON user_preferences(profile_id, key, project_id) WHERE scope = 'PROJECT'
                    """
                )

                # 4. Episodic Memories
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS episodic_memories (
                        memory_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        normalized_goal TEXT NOT NULL,
                        outcome TEXT NOT NULL CHECK(outcome IN ('SUCCESS', 'FAILURE', 'PARTIAL')),
                        summary TEXT NOT NULL,
                        solution_steps_json TEXT NOT NULL DEFAULT '[]',
                        failure_class TEXT,
                        project_id TEXT,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        retention_class TEXT NOT NULL DEFAULT 'STANDARD' CHECK(retention_class IN ('EPHEMERAL', 'STANDARD', 'PERMANENT')),
                        created_at TEXT NOT NULL,
                        last_accessed_at TEXT NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        CHECK(project_id IS NULL OR length(project_id) > 0)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodic_task_id ON episodic_memories(task_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodic_project_id ON episodic_memories(project_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodic_created_at ON episodic_memories(created_at)"
                )

                # 5. Semantic Memories
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS semantic_memories (
                        memory_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL CHECK(category IN ('PROJECT_KNOWLEDGE', 'CONVERSATION_FACT', 'TOOL_INSIGHT', 'ENVIRONMENT_CONFIG')),
                        scope TEXT NOT NULL CHECK(scope IN ('GLOBAL', 'PROJECT')),
                        project_id TEXT,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        source_type TEXT NOT NULL CHECK(source_type IN ('USER_CONFIRMED', 'SYSTEM_DERIVED', 'TASK_DERIVED', 'DOCUMENT_DERIVED', 'WEB_DERIVED')),
                        trust_level TEXT NOT NULL CHECK(trust_level IN ('USER_CONFIRMED', 'SYSTEM_DERIVED', 'TASK_DERIVED', 'DOCUMENT_DERIVED', 'WEB_DERIVED')),
                        importance REAL NOT NULL DEFAULT 0.5 CHECK(importance >= 0.0 AND importance <= 1.0),
                        expires_at TEXT,
                        retention_class TEXT NOT NULL DEFAULT 'STANDARD' CHECK(retention_class IN ('EPHEMERAL', 'STANDARD', 'PERMANENT')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_accessed_at TEXT NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        CHECK(
                            (scope = 'GLOBAL' AND project_id IS NULL) OR
                            (scope = 'PROJECT' AND project_id IS NOT NULL AND length(project_id) > 0)
                        )
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_semantic_scope_project ON semantic_memories(scope, project_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_semantic_category ON semantic_memories(category)"
                )

                # 6. Semantic Memory FTS5 Virtual Table
                cur.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS semantic_memory_fts USING fts5(
                        memory_id UNINDEXED,
                        title,
                        content,
                        tags,
                        project_id UNINDEXED,
                        category UNINDEXED,
                        tokenize='porter unicode61'
                    )
                    """
                )

                # 7. Triggers to maintain bidirectional sync between semantic_memories and semantic_memory_fts
                cur.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_semantic_ai AFTER INSERT ON semantic_memories
                    BEGIN
                        INSERT INTO semantic_memory_fts(memory_id, title, content, tags, project_id, category)
                        VALUES (new.memory_id, new.title, new.content, new.tags_json, new.project_id, new.category);
                    END;
                    """
                )
                cur.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_semantic_ad AFTER DELETE ON semantic_memories
                    BEGIN
                        DELETE FROM semantic_memory_fts WHERE memory_id = old.memory_id;
                    END;
                    """
                )
                cur.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_semantic_au AFTER UPDATE ON semantic_memories
                    BEGIN
                        DELETE FROM semantic_memory_fts WHERE memory_id = old.memory_id;
                        INSERT INTO semantic_memory_fts(memory_id, title, content, tags, project_id, category)
                        VALUES (new.memory_id, new.title, new.content, new.tags_json, new.project_id, new.category);
                    END;
                    """
                )

                # 8. Memory Audit Log
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_audit_log (
                        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action TEXT NOT NULL,
                        memory_id TEXT,
                        actor TEXT NOT NULL CHECK(actor IN ('USER', 'SYSTEM', 'EXPIRATION')),
                        timestamp TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_memory_id ON memory_audit_log(memory_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON memory_audit_log(timestamp)"
                )

                # Ensure default user profile exists
                cur.execute("SELECT profile_id FROM user_profiles WHERE profile_id = 'default'")
                if not cur.fetchone():
                    now = _utc_now_iso()
                    cur.execute(
                        """
                        INSERT INTO user_profiles (profile_id, user_name, safe_metadata_json, created_at, updated_at)
                        VALUES ('default', 'User', '{}', ?, ?)
                        """,
                        (now, now),
                    )

                conn.commit()
                return self.SCHEMA_VERSION

    def get_schema_version(self) -> int:
        """Fetch the current memory schema version from schema_meta."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM schema_meta WHERE key = 'memory_schema_version'")
                row = cur.fetchone()
                return int(row["value"]) if row else 0

    # -----------------------------------------------------------------------
    # User Profile Operations
    # -----------------------------------------------------------------------

    def get_user_profile(self, profile_id: str = "default") -> Optional[UserProfile]:
        """Retrieve a user profile by ID."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM user_profiles WHERE profile_id = ?", (profile_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return UserProfile(
                    profile_id=row["profile_id"],
                    user_name=row["user_name"],
                    safe_metadata=json.loads(row["safe_metadata_json"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    def save_user_profile(self, profile: UserProfile) -> UserProfile:
        """Upsert a user profile with sanitized metadata."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                profile.updated_at = now
                scrubbed_meta = _scrub_value_defensive(profile.safe_metadata)
                cur.execute(
                    """
                    INSERT INTO user_profiles (profile_id, user_name, safe_metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        user_name = excluded.user_name,
                        safe_metadata_json = excluded.safe_metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        profile.profile_id,
                        profile.user_name,
                        json.dumps(scrubbed_meta),
                        profile.created_at,
                        profile.updated_at,
                    ),
                )
                conn.commit()
                return profile

    # -----------------------------------------------------------------------
    # User Preference Operations
    # -----------------------------------------------------------------------

    def set_preference(
        self,
        key: str,
        value: Any,
        profile_id: str = "default",
        scope: MemoryScope = MemoryScope.GLOBAL,
        project_id: Optional[str] = None,
        source: PreferenceSource = PreferenceSource.USER_EXPLICIT,
        confidence: float = 1.0,
    ) -> Preference:
        """Set a user preference with defensive secret validation and scope integrity."""
        _validate_safe_key_name(key)
        if scope == MemoryScope.GLOBAL and project_id is not None:
            raise ValueError("GLOBAL preference cannot have project_id")
        if scope == MemoryScope.PROJECT and not project_id:
            raise ValueError("PROJECT preference requires non-empty project_id")

        scrubbed_val = _scrub_value_defensive(value)
        pref = Preference(
            profile_id=profile_id,
            key=key,
            value=scrubbed_val,
            scope=scope,
            project_id=project_id,
            source=source,
            confidence=max(0.0, min(1.0, float(confidence))),
        )

        with self._lock:
            # Ensure profile exists
            if not self.get_user_profile(profile_id):
                self.save_user_profile(UserProfile(profile_id=profile_id))

            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                pref.updated_at = now
                val_json = json.dumps(pref.value)

                # Upsert based on scope
                if pref.scope == MemoryScope.GLOBAL:
                    cur.execute(
                        """
                        INSERT INTO user_preferences (
                            pref_id, profile_id, key, value_json, scope, project_id,
                            source, confidence, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                        ON CONFLICT(profile_id, key) WHERE scope = 'GLOBAL' DO UPDATE SET
                            value_json = excluded.value_json,
                            source = excluded.source,
                            confidence = excluded.confidence,
                            updated_at = excluded.updated_at
                        """,
                        (
                            pref.pref_id,
                            pref.profile_id,
                            pref.key,
                            val_json,
                            pref.scope.value,
                            pref.source.value,
                            pref.confidence,
                            pref.created_at,
                            pref.updated_at,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO user_preferences (
                            pref_id, profile_id, key, value_json, scope, project_id,
                            source, confidence, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(profile_id, key, project_id) WHERE scope = 'PROJECT' DO UPDATE SET
                            value_json = excluded.value_json,
                            source = excluded.source,
                            confidence = excluded.confidence,
                            updated_at = excluded.updated_at
                        """,
                        (
                            pref.pref_id,
                            pref.profile_id,
                            pref.key,
                            val_json,
                            pref.scope.value,
                            pref.project_id,
                            pref.source.value,
                            pref.confidence,
                            pref.created_at,
                            pref.updated_at,
                        ),
                    )
                conn.commit()

                # Audit log
                self.record_audit_event(
                    action="PREFERENCE_SET",
                    memory_id=pref.pref_id,
                    actor=AuditActor.USER if source == PreferenceSource.USER_EXPLICIT else AuditActor.SYSTEM,
                    metadata={"key": key, "scope": scope.value, "project_id": project_id},
                )
                return pref

    def get_preference(
        self,
        key: str,
        profile_id: str = "default",
        scope: MemoryScope = MemoryScope.GLOBAL,
        project_id: Optional[str] = None,
    ) -> Optional[Preference]:
        """Fetch a specific preference by profile, key, scope, and project."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                if scope == MemoryScope.GLOBAL:
                    cur.execute(
                        """
                        SELECT * FROM user_preferences
                        WHERE profile_id = ? AND key = ? AND scope = 'GLOBAL'
                        """,
                        (profile_id, key),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM user_preferences
                        WHERE profile_id = ? AND key = ? AND scope = 'PROJECT' AND project_id = ?
                        """,
                        (profile_id, key, project_id),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                return Preference(
                    pref_id=row["pref_id"],
                    profile_id=row["profile_id"],
                    key=row["key"],
                    value=json.loads(row["value_json"]),
                    scope=MemoryScope(row["scope"]),
                    project_id=row["project_id"],
                    source=PreferenceSource(row["source"]),
                    confidence=row["confidence"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    def list_preferences(
        self,
        profile_id: str = "default",
        scope: Optional[MemoryScope] = None,
        project_id: Optional[str] = None,
    ) -> List[Preference]:
        """List preferences matching filter criteria."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                query = "SELECT * FROM user_preferences WHERE profile_id = ?"
                params: List[Any] = [profile_id]

                if scope:
                    query += " AND scope = ?"
                    params.append(scope.value)
                if project_id:
                    query += " AND project_id = ?"
                    params.append(project_id)

                query += " ORDER BY key ASC"
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                    Preference(
                        pref_id=r["pref_id"],
                        profile_id=r["profile_id"],
                        key=r["key"],
                        value=json.loads(r["value_json"]),
                        scope=MemoryScope(r["scope"]),
                        project_id=r["project_id"],
                        source=PreferenceSource(r["source"]),
                        confidence=r["confidence"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                    for r in rows
                ]

    def delete_preference(self, pref_id: str) -> bool:
        """Remove a preference record completely."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM user_preferences WHERE pref_id = ?", (pref_id,))
                conn.commit()
                deleted = cur.rowcount > 0
                if deleted:
                    self.record_audit_event(
                        action="PREFERENCE_DELETED",
                        memory_id=pref_id,
                        actor=AuditActor.USER,
                    )
                return deleted

    def delete_preference_by_key(
        self,
        key: str,
        profile_id: str = "default",
        project_id: Optional[str] = None,
    ) -> bool:
        """Remove a preference record by key, profile, and project."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                if project_id:
                    cur.execute(
                        "DELETE FROM user_preferences WHERE profile_id = ? AND key = ? AND project_id = ?",
                        (profile_id, key, project_id),
                    )
                else:
                    cur.execute(
                        "DELETE FROM user_preferences WHERE profile_id = ? AND key = ? AND scope = 'GLOBAL'",
                        (profile_id, key),
                    )
                conn.commit()
                deleted = cur.rowcount > 0
                if deleted:
                    self.record_audit_event(
                        action="PREFERENCE_FORGOTTEN",
                        memory_id=f"{profile_id}:{key}",
                        actor=AuditActor.USER,
                        metadata={"key": key, "project_id": project_id},
                    )
                return deleted

    # -----------------------------------------------------------------------
    # Episodic Memory Operations
    # -----------------------------------------------------------------------

    def create_episodic_memory(self, memory: EpisodicMemory) -> EpisodicMemory:
        """Persist a compact episodic task summary with provenance."""
        scrubbed_summary = _scrub_value_defensive(memory.summary)
        scrubbed_steps = _scrub_value_defensive(memory.solution_steps)
        scrubbed_tags = _scrub_value_defensive(memory.tags)

        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO episodic_memories (
                        memory_id, task_id, goal, normalized_goal, outcome,
                        summary, solution_steps_json, failure_class, project_id,
                        tags_json, retention_class, created_at, last_accessed_at, access_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.memory_id,
                        memory.task_id,
                        memory.goal,
                        memory.normalized_goal,
                        memory.outcome.value,
                        scrubbed_summary,
                        json.dumps(scrubbed_steps),
                        memory.failure_class,
                        memory.project_id,
                        json.dumps(scrubbed_tags),
                        memory.retention_class.value,
                        memory.created_at,
                        memory.last_accessed_at,
                        memory.access_count,
                    ),
                )
                conn.commit()
                memory.summary = scrubbed_summary
                memory.solution_steps = scrubbed_steps
                memory.tags = scrubbed_tags
                self.record_audit_event(
                    action="EPISODIC_CREATED",
                    memory_id=memory.memory_id,
                    actor=AuditActor.SYSTEM,
                    metadata={"task_id": memory.task_id, "outcome": memory.outcome.value},
                )
                return memory

    def get_episodic_memory(self, memory_id: str) -> Optional[EpisodicMemory]:
        """Fetch an episodic memory by ID and increment access counter."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM episodic_memories WHERE memory_id = ?", (memory_id,))
                row = cur.fetchone()
                if not row:
                    return None

                now = _utc_now_iso()
                new_count = row["access_count"] + 1
                cur.execute(
                    "UPDATE episodic_memories SET last_accessed_at = ?, access_count = ? WHERE memory_id = ?",
                    (now, new_count, memory_id),
                )
                conn.commit()

                return EpisodicMemory(
                    memory_id=row["memory_id"],
                    task_id=row["task_id"],
                    goal=row["goal"],
                    normalized_goal=row["normalized_goal"],
                    outcome=EpisodicOutcome(row["outcome"]),
                    summary=row["summary"],
                    solution_steps=json.loads(row["solution_steps_json"]),
                    failure_class=row["failure_class"],
                    project_id=row["project_id"],
                    tags=json.loads(row["tags_json"]),
                    retention_class=RetentionClass(row["retention_class"]),
                    created_at=row["created_at"],
                    last_accessed_at=now,
                    access_count=new_count,
                )

    def list_episodic_memories(
        self,
        project_id: Optional[str] = None,
        outcome: Optional[EpisodicOutcome] = None,
        limit: int = 50,
    ) -> List[EpisodicMemory]:
        """List episodic memories filtered by project and outcome."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                query = "SELECT * FROM episodic_memories WHERE 1=1"
                params: List[Any] = []

                if project_id:
                    query += " AND project_id = ?"
                    params.append(project_id)
                if outcome:
                    query += " AND outcome = ?"
                    params.append(outcome.value)

                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)

                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                    EpisodicMemory(
                        memory_id=r["memory_id"],
                        task_id=r["task_id"],
                        goal=r["goal"],
                        normalized_goal=r["normalized_goal"],
                        outcome=EpisodicOutcome(r["outcome"]),
                        summary=r["summary"],
                        solution_steps=json.loads(r["solution_steps_json"]),
                        failure_class=r["failure_class"],
                        project_id=r["project_id"],
                        tags=json.loads(r["tags_json"]),
                        retention_class=RetentionClass(r["retention_class"]),
                        created_at=r["created_at"],
                        last_accessed_at=r["last_accessed_at"],
                        access_count=r["access_count"],
                    )
                    for r in rows
                ]

    def delete_episodic_memory(self, memory_id: str) -> bool:
        """Remove an episodic memory record."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM episodic_memories WHERE memory_id = ?", (memory_id,))
                conn.commit()
                deleted = cur.rowcount > 0
                if deleted:
                    self.record_audit_event(
                        action="EPISODIC_DELETED",
                        memory_id=memory_id,
                        actor=AuditActor.USER,
                    )
                return deleted

    def delete_episodic_memories_by_task(self, task_id: str) -> int:
        """Delete all episodic memories associated with an authoritative task_id."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM episodic_memories WHERE task_id = ?", (task_id,))
                conn.commit()
                count = cur.rowcount
                if count > 0:
                    self.record_audit_event(
                        action="EPISODIC_BATCH_DELETED",
                        actor=AuditActor.USER,
                        metadata={"task_id": task_id, "deleted_count": count},
                    )
                return count

    def get_episodic_memory_by_task_id(self, task_id: str) -> Optional[EpisodicMemory]:
        """Fetch an episodic memory by authoritative task provenance ID."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM episodic_memories WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return EpisodicMemory(
                    memory_id=row["memory_id"],
                    task_id=row["task_id"],
                    goal=row["goal"],
                    normalized_goal=row["normalized_goal"],
                    outcome=EpisodicOutcome(row["outcome"]),
                    summary=row["summary"],
                    solution_steps=json.loads(row["solution_steps_json"]),
                    failure_class=row["failure_class"],
                    project_id=row["project_id"],
                    tags=json.loads(row["tags_json"]),
                    retention_class=RetentionClass(row["retention_class"]),
                    created_at=row["created_at"],
                    last_accessed_at=row["last_accessed_at"],
                    access_count=row["access_count"],
                )

    def update_episodic_memory(self, memory: EpisodicMemory) -> EpisodicMemory:
        """Update an existing episodic memory record."""
        scrubbed_summary = _scrub_value_defensive(memory.summary)
        scrubbed_steps = _scrub_value_defensive(memory.solution_steps)
        scrubbed_tags = _scrub_value_defensive(memory.tags)
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                memory.last_accessed_at = now
                cur.execute(
                    """
                    UPDATE episodic_memories SET
                        goal = ?,
                        normalized_goal = ?,
                        outcome = ?,
                        summary = ?,
                        solution_steps_json = ?,
                        failure_class = ?,
                        project_id = ?,
                        tags_json = ?,
                        retention_class = ?,
                        last_accessed_at = ?
                    WHERE memory_id = ?
                    """,
                    (
                        memory.goal,
                        memory.normalized_goal,
                        memory.outcome.value,
                        scrubbed_summary,
                        json.dumps(scrubbed_steps),
                        memory.failure_class,
                        memory.project_id,
                        json.dumps(scrubbed_tags),
                        memory.retention_class.value,
                        now,
                        memory.memory_id,
                    ),
                )
                conn.commit()
                memory.summary = scrubbed_summary
                memory.solution_steps = scrubbed_steps
                memory.tags = scrubbed_tags
                return memory

    # -----------------------------------------------------------------------
    # Semantic Memory Operations (with FTS5)
    # -----------------------------------------------------------------------

    def create_semantic_memory(self, memory: SemanticMemory) -> SemanticMemory:
        """Create a semantic memory record with automatic trigger-driven FTS5 indexing."""
        if memory.scope == MemoryScope.GLOBAL and memory.project_id is not None:
            raise ValueError("GLOBAL semantic memory cannot have project_id")
        if memory.scope == MemoryScope.PROJECT and not memory.project_id:
            raise ValueError("PROJECT semantic memory requires non-empty project_id")

        scrubbed_title = _scrub_value_defensive(memory.title)
        scrubbed_content = _scrub_value_defensive(memory.content)
        scrubbed_tags = _scrub_value_defensive(memory.tags)

        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO semantic_memories (
                        memory_id, title, content, category, scope, project_id,
                        tags_json, source_type, trust_level, importance, expires_at,
                        retention_class, created_at, updated_at, last_accessed_at, access_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.memory_id,
                        scrubbed_title,
                        scrubbed_content,
                        memory.category.value,
                        memory.scope.value,
                        memory.project_id,
                        json.dumps(scrubbed_tags),
                        memory.source_type.value,
                        memory.trust_level.value,
                        max(0.0, min(1.0, float(memory.importance))),
                        memory.expires_at,
                        memory.retention_class.value,
                        memory.created_at,
                        memory.updated_at,
                        memory.last_accessed_at,
                        memory.access_count,
                    ),
                )
                conn.commit()
                memory.title = scrubbed_title
                memory.content = scrubbed_content
                memory.tags = scrubbed_tags
                self.record_audit_event(
                    action="SEMANTIC_CREATED",
                    memory_id=memory.memory_id,
                    actor=AuditActor.USER if memory.source_type == MemorySourceType.USER_CONFIRMED else AuditActor.SYSTEM,
                    metadata={"title": scrubbed_title, "category": memory.category.value},
                )
                return memory

    def get_semantic_memory(self, memory_id: str) -> Optional[SemanticMemory]:
        """Fetch a semantic memory by ID and update access tracking."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM semantic_memories WHERE memory_id = ?", (memory_id,))
                row = cur.fetchone()
                if not row:
                    return None

                now = _utc_now_iso()
                new_count = row["access_count"] + 1
                cur.execute(
                    "UPDATE semantic_memories SET last_accessed_at = ?, access_count = ? WHERE memory_id = ?",
                    (now, new_count, memory_id),
                )
                conn.commit()

                return SemanticMemory(
                    memory_id=row["memory_id"],
                    title=row["title"],
                    content=row["content"],
                    category=SemanticCategory(row["category"]),
                    scope=MemoryScope(row["scope"]),
                    project_id=row["project_id"],
                    tags=json.loads(row["tags_json"]),
                    source_type=MemorySourceType(row["source_type"]),
                    trust_level=TrustLevel(row["trust_level"]),
                    importance=row["importance"],
                    expires_at=row["expires_at"],
                    retention_class=RetentionClass(row["retention_class"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    last_accessed_at=now,
                    access_count=new_count,
                )

    def update_semantic_memory(self, memory: SemanticMemory) -> SemanticMemory:
        """Update an existing semantic memory record with automatic FTS5 re-indexing."""
        if memory.scope == MemoryScope.GLOBAL and memory.project_id is not None:
            raise ValueError("GLOBAL semantic memory cannot have project_id")
        if memory.scope == MemoryScope.PROJECT and not memory.project_id:
            raise ValueError("PROJECT semantic memory requires non-empty project_id")

        scrubbed_title = _scrub_value_defensive(memory.title)
        scrubbed_content = _scrub_value_defensive(memory.content)
        scrubbed_tags = _scrub_value_defensive(memory.tags)

        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                memory.updated_at = now
                cur.execute(
                    """
                    UPDATE semantic_memories SET
                        title = ?,
                        content = ?,
                        category = ?,
                        scope = ?,
                        project_id = ?,
                        tags_json = ?,
                        source_type = ?,
                        trust_level = ?,
                        importance = ?,
                        expires_at = ?,
                        retention_class = ?,
                        updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (
                        scrubbed_title,
                        scrubbed_content,
                        memory.category.value,
                        memory.scope.value,
                        memory.project_id,
                        json.dumps(scrubbed_tags),
                        memory.source_type.value,
                        memory.trust_level.value,
                        max(0.0, min(1.0, float(memory.importance))),
                        memory.expires_at,
                        memory.retention_class.value,
                        memory.updated_at,
                        memory.memory_id,
                    ),
                )
                conn.commit()
                memory.title = scrubbed_title
                memory.content = scrubbed_content
                memory.tags = scrubbed_tags
                if cur.rowcount > 0:
                    self.record_audit_event(
                        action="SEMANTIC_UPDATED",
                        memory_id=memory.memory_id,
                        actor=AuditActor.USER if memory.source_type == MemorySourceType.USER_CONFIRMED else AuditActor.SYSTEM,
                        metadata={"title": scrubbed_title},
                    )
                return memory

    def delete_semantic_memory(self, memory_id: str) -> bool:
        """Delete a semantic memory record and trigger automatic FTS5 removal."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM semantic_memories WHERE memory_id = ?", (memory_id,))
                conn.commit()
                deleted = cur.rowcount > 0
                if deleted:
                    self.record_audit_event(
                        action="SEMANTIC_DELETED",
                        memory_id=memory_id,
                        actor=AuditActor.USER,
                    )
                return deleted

    def list_semantic_memories(
        self,
        scope: Optional[MemoryScope] = None,
        project_id: Optional[str] = None,
        category: Optional[SemanticCategory] = None,
        limit: int = 50,
    ) -> List[SemanticMemory]:
        """List semantic memories filtered by scope, project, or category."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                query = "SELECT * FROM semantic_memories WHERE 1=1"
                params: List[Any] = []

                if scope:
                    query += " AND scope = ?"
                    params.append(scope.value)
                if project_id:
                    query += " AND project_id = ?"
                    params.append(project_id)
                if category:
                    query += " AND category = ?"
                    params.append(category.value)

                query += " ORDER BY updated_at DESC LIMIT ?"
                params.append(limit)

                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                    SemanticMemory(
                        memory_id=r["memory_id"],
                        title=r["title"],
                        content=r["content"],
                        category=SemanticCategory(r["category"]),
                        scope=MemoryScope(r["scope"]),
                        project_id=r["project_id"],
                        tags=json.loads(r["tags_json"]),
                        source_type=MemorySourceType(r["source_type"]),
                        trust_level=TrustLevel(r["trust_level"]),
                        importance=r["importance"],
                        expires_at=r["expires_at"],
                        retention_class=RetentionClass(r["retention_class"]),
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                        last_accessed_at=r["last_accessed_at"],
                        access_count=r["access_count"],
                    )
                    for r in rows
                ]

    def delete_memories_by_project(self, project_id: str) -> Dict[str, int]:
        """Cascade delete all project-scoped memories and preferences for project_id."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM semantic_memories WHERE project_id = ?", (project_id,))
                semantic_count = cur.rowcount
                cur.execute("DELETE FROM episodic_memories WHERE project_id = ?", (project_id,))
                episodic_count = cur.rowcount
                cur.execute("DELETE FROM user_preferences WHERE project_id = ?", (project_id,))
                pref_count = cur.rowcount
                conn.commit()
                res = {
                    "semantic_deleted": semantic_count,
                    "episodic_deleted": episodic_count,
                    "preferences_deleted": pref_count,
                    "total": semantic_count + episodic_count + pref_count,
                }
                self.record_audit_event(
                    action="PROJECT_MEMORIES_PURGED",
                    actor=AuditActor.USER,
                    metadata={"project_id": project_id, **res},
                )
                return res

    def purge_expired_memories(self) -> Dict[str, int]:
        """Hard-delete all semantic memories whose expires_at timestamp has elapsed."""
        now = _utc_now_iso()
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM semantic_memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (now,),
                )
                deleted = cur.rowcount
                conn.commit()
                res = {"expired_semantic_deleted": deleted}
                if deleted > 0:
                    self.record_audit_event(
                        action="EXPIRED_MEMORIES_PURGED",
                        actor=AuditActor.EXPIRATION,
                        metadata=res,
                    )
                return res

    # -----------------------------------------------------------------------
    # Audit Logging & Health Diagnostics
    # -----------------------------------------------------------------------

    def record_audit_event(
        self,
        action: str,
        memory_id: Optional[str] = None,
        actor: AuditActor = AuditActor.SYSTEM,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Append an auditable event to the memory audit trail."""
        scrubbed_meta = _scrub_value_defensive(metadata or {})
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                now = _utc_now_iso()
                cur.execute(
                    """
                    INSERT INTO memory_audit_log (action, memory_id, actor, timestamp, metadata_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (action, memory_id, actor.value, now, json.dumps(scrubbed_meta)),
                )
                conn.commit()
                return cur.lastrowid or 0

    def list_audit_logs(self, memory_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch audit log records."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                if memory_id:
                    cur.execute(
                        "SELECT * FROM memory_audit_log WHERE memory_id = ? ORDER BY timestamp DESC LIMIT ?",
                        (memory_id, limit),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM memory_audit_log ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    )
                rows = cur.fetchall()
                return [
                    {
                        "audit_id": r["audit_id"],
                        "action": r["action"],
                        "memory_id": r["memory_id"],
                        "actor": r["actor"],
                        "timestamp": r["timestamp"],
                        "metadata": json.loads(r["metadata_json"]),
                    }
                    for r in rows
                ]

    def get_stats(self) -> Dict[str, Any]:
        """Return table row counts and diagnostic metrics."""
        with self._lock:
            with self._connection() as conn:
                cur = conn.cursor()
                stats: Dict[str, Any] = {
                    "schema_version": self.get_schema_version(),
                    "db_path": str(self.db_path),
                }
                for table in [
                    "user_profiles",
                    "user_preferences",
                    "episodic_memories",
                    "semantic_memories",
                    "memory_audit_log",
                ]:
                    cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                    stats[f"{table}_count"] = cur.fetchone()["cnt"]

                # Count FTS rows
                cur.execute("SELECT COUNT(*) as cnt FROM semantic_memory_fts")
                stats["fts_indexed_count"] = cur.fetchone()["cnt"]

                return stats

    def check_health(self) -> Dict[str, Any]:
        """Perform basic integrity verification."""
        try:
            with self._lock:
                with self._connection() as conn:
                    cur = conn.cursor()
                    cur.execute("PRAGMA quick_check;")
                    quick_check = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM semantic_memory_fts;")
                    fts_count = cur.fetchone()[0]
            return {
                "healthy": quick_check == "ok",
                "quick_check": quick_check,
                "fts_accessible": True,
                "fts_count": fts_count,
                "schema_version": self.get_schema_version(),
            }
        except Exception as exc:
            return {
                "healthy": False,
                "error": str(exc),
                "fts_accessible": False,
            }

    def close(self) -> None:
        """Release any in-memory SQLite connection."""
        with self._lock:
            if self._mem_conn is not None:
                try:
                    self._mem_conn.close()
                except Exception:
                    pass
                self._mem_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# Global singleton repository
memory_repository = MemoryRepository()

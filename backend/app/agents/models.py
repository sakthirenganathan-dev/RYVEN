"""RYVEN 3.0 — M16.0 Multi-Agent Coordination Data Models.

Defines the core data models for controlled multi-agent coordination:
- AgentRole, AgentStatus, AgentCapability enums
- AgentTask and AgentTaskGraph data structures
- AgentMessage and AgentHandoff structured communication
- AgentContext with budgeting and secret redaction
- Memory interface contracts (MemoryRead, MemoryContext, MemoryWriteCandidate)
- Concurrency and spawn boundary constants
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Spawn Limits & Bounded Execution Constants (Phase 16)
# ---------------------------------------------------------------------------
MAX_ACTIVE_AGENTS: int = 5
MAX_TASKS_PER_GRAPH: int = 20
MAX_AGENT_DEPTH: int = 3
MAX_HANDOFFS: int = 5
MAX_RETRIES: int = 2
MAX_GRAPH_RUNTIME_SEC: float = 300.0


# ---------------------------------------------------------------------------
# Redaction Helpers
# ---------------------------------------------------------------------------
_SECRET_KEY_PATTERNS = frozenset({
    "password", "token", "secret", "cookie", "authorization", "auth",
    "api_key", "apikey", "private_key", "privatekey", "access_token",
    "refresh_token", "client_secret", "bearer", "credential", "credentials",
    "passwd", "pwd", "env", ".env"
})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_secrets(data: Any) -> Any:
    """Recursively scrub secrets, sensitive tokens, and private keys."""
    if isinstance(data, dict):
        result: Dict[str, Any] = {}
        for k, v in data.items():
            if any(p in str(k).lower() for p in _SECRET_KEY_PATTERNS):
                result[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                result[k] = redact_secrets(v)
            elif isinstance(v, str):
                result[k] = _redact_string(v)
            else:
                result[k] = v
        return result
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    elif isinstance(data, str):
        return _redact_string(data)
    return data


def _redact_string(val: str) -> str:
    """Detect and redact common token patterns in raw string text."""
    # Bearer tokens
    val = re.sub(r'(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{16,}', r'\1[REDACTED]', val)
    # API key assignments
    val = re.sub(r'(?i)(api[_-]?key\s*[:=]\s*["\']?)[a-zA-Z0-9_\-\.]{12,}["\']?', r'\1[REDACTED]', val)
    # Private key blocks
    val = re.sub(r'-----BEGIN[ A-Z0-9_-]+PRIVATE KEY-----[\s\S]*?-----END[ A-Z0-9_-]+PRIVATE KEY-----', '[REDACTED_PRIVATE_KEY]', val)
    if len(val) > 2048:
        val = val[:1024] + "…[TRUNCATED]"
    return val


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AgentRole(str, Enum):
    """Role categories for specialized execution workers under central coordination."""
    COORDINATOR = "COORDINATOR"
    RESEARCH = "RESEARCH"
    DEVELOPER = "DEVELOPER"
    COMPUTER = "COMPUTER"
    BROWSER = "BROWSER"
    WINDOWS = "WINDOWS"
    MEMORY = "MEMORY"
    VERIFICATION = "VERIFICATION"


class AgentStatus(str, Enum):
    """Lifecycle state of an agent or individual agent task."""
    CREATED = "CREATED"
    READY = "READY"
    PLANNING = "PLANNING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RECOVERING = "RECOVERING"


class AgentCapability(str, Enum):
    """Granular execution capabilities mapping directly to ToolRegistry tools."""
    # Research / Internet
    WEB_SEARCH = "WEB_SEARCH"
    WEB_RESEARCH = "WEB_RESEARCH"
    PAGE_READING = "PAGE_READING"
    
    # Browser / Computer
    BROWSER_ACTION = "BROWSER_ACTION"
    WINDOWS_APP = "WINDOWS_APP"
    WINDOWS_FILES = "WINDOWS_FILES"
    CLIPBOARD = "CLIPBOARD"
    SYSTEM_STATUS = "SYSTEM_STATUS"

    # Developer
    PROJECT_SCAN = "PROJECT_SCAN"
    CODE_GENERATION = "CODE_GENERATION"
    CODE_MODIFICATION = "CODE_MODIFICATION"
    BUILD = "BUILD"
    TEST = "TEST"
    GIT = "GIT"
    DEPLOY = "DEPLOY"
    KNOWLEDGE_GRAPH = "KNOWLEDGE_GRAPH"

    # Health & Verification
    HEALTH_CHECK = "HEALTH_CHECK"
    VISION = "VISION"
    OCR = "OCR"
    VERIFICATION = "VERIFICATION"

    # Memory (Interface)
    MEMORY_READ = "MEMORY_READ"
    MEMORY_WRITE = "MEMORY_WRITE"

    # Coordinator
    TASK_DECOMPOSITION = "TASK_DECOMPOSITION"
    AGENT_ASSIGNMENT = "AGENT_ASSIGNMENT"
    TASK_COORDINATION = "TASK_COORDINATION"


class MessageType(str, Enum):
    """Structured message types for inter-agent communication."""
    TASK_REQUEST = "TASK_REQUEST"
    TASK_RESULT = "TASK_RESULT"
    FINDING = "FINDING"
    ARTIFACT = "ARTIFACT"
    STATUS = "STATUS"
    BLOCKED = "BLOCKED"
    HANDOFF = "HANDOFF"
    VERIFICATION = "VERIFICATION"
    ERROR = "ERROR"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


# ---------------------------------------------------------------------------
# Structured Models
# ---------------------------------------------------------------------------

class AgentMessage(BaseModel):
    """Typed, bounded, and redacted inter-agent message."""
    message_id: str = Field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    sender_role: AgentRole
    recipient_role: AgentRole
    message_type: MessageType
    task_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utc_now_iso)

    def model_post_init(self, __context: Any) -> None:
        self.payload = redact_secrets(self.payload)


class AgentHandoff(BaseModel):
    """Explicit structured handoff between roles with findings and constraints."""
    handoff_id: str = Field(default_factory=lambda: f"handoff-{uuid.uuid4().hex[:8]}")
    source_role: AgentRole
    target_role: AgentRole
    task_id: str
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    sources: List[Any] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    recommended_next_action: str = ""
    accepted: bool = True
    reason: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now_iso)

    def model_post_init(self, __context: Any) -> None:
        self.findings = redact_secrets(self.findings)
        self.artifacts = redact_secrets(self.artifacts)


class AgentContext(BaseModel):
    """Budgeted, task-specific context preventing global prompt saturation."""
    context_id: str = Field(default_factory=lambda: f"ctx-{uuid.uuid4().hex[:8]}")
    task_id: Optional[str] = None
    user_intent: str = ""
    task_objective: str = ""
    dependency_results: Dict[str, Any] = Field(default_factory=dict)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    project_context: Dict[str, Any] = Field(default_factory=dict)
    relevant_files: List[str] = Field(default_factory=list)
    tool_outputs: Dict[str, Any] = Field(default_factory=dict)
    security_state: Dict[str, Any] = Field(default_factory=dict)
    confirmation_state: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self.dependency_results = redact_secrets(self.dependency_results)
        self.findings = redact_secrets(self.findings)
        self.tool_outputs = redact_secrets(self.tool_outputs)

    def get_budgeted_summary(self, max_length: int = 1000) -> str:
        """Produce a concise string summary for consumption by downstream agents."""
        parts: List[str] = [f"Goal: {self.task_objective}"]
        if self.findings:
            parts.append(f"Findings: {len(self.findings)} item(s)")
        if self.relevant_files:
            parts.append(f"Files: {', '.join(self.relevant_files[:5])}")
        if self.dependency_results:
            parts.append(f"Dependencies completed: {len(self.dependency_results)}")
        summary = " | ".join(parts)
        if len(summary) > max_length:
            return summary[:max_length] + "…"
        return summary


class AgentTask(BaseModel):
    """A discrete, assigned, and tracked task executed by a specialized agent role."""
    task_id: str = Field(default_factory=lambda: f"atask-{uuid.uuid4().hex[:8]}")
    parent_task_id: Optional[str] = None
    graph_id: Optional[str] = None
    agent_id: Optional[str] = None
    role: AgentRole
    objective: str
    capability: AgentCapability
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    input_context: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    status: AgentStatus = AgentStatus.CREATED
    priority: int = 1
    created_at: str = Field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = MAX_RETRIES
    checkpoint_id: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_type: Optional[str] = None
    confirmed: bool = False

    def model_post_init(self, __context: Any) -> None:
        self.arguments = redact_secrets(self.arguments)
        self.input_context = redact_secrets(self.input_context)
        if self.result is not None:
            self.result = redact_secrets(self.result)


# ---------------------------------------------------------------------------
# Memory Integration Contracts (Phase 17)
# ---------------------------------------------------------------------------

class MemoryRead(BaseModel):
    """Contract for querying agent memory without exposing storage internals."""
    query: str
    keys: List[str] = Field(default_factory=list)
    role: Optional[AgentRole] = None
    limit: int = 5


class MemoryContext(BaseModel):
    """Immutable memory snippets returned to an agent for bounded context injection."""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_count: int = 0
    timestamp: str = Field(default_factory=_utc_now_iso)


class MemoryWriteCandidate(BaseModel):
    """Candidate memory entry subjected to security and sensitivity inspection."""
    key: str
    value: Any
    source_role: AgentRole
    confidence: float = 1.0
    sensitive: bool = False
    timestamp: str = Field(default_factory=_utc_now_iso)

    def model_post_init(self, __context: Any) -> None:
        self.value = redact_secrets(self.value)

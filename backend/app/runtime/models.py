"""RYVEN 3.0 — Runtime & Performance Data Models (M15.3.9).

Strongly typed models for runtime state, performance telemetry, resource pressure,
context budgeting, persistent checkpoints, and recovery decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Runtime & Resource Enums
# ---------------------------------------------------------------------------

class RuntimeState(str, Enum):
    """Overall state of the RYVEN backend runtime."""
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    RESOURCE_WARNING = "RESOURCE_WARNING"
    RESOURCE_CRITICAL = "RESOURCE_CRITICAL"
    RECOVERING = "RECOVERING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    READY = "READY"


class ResourceStatus(str, Enum):
    """Host hardware resource pressure status."""
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class ResourceDecision(str, Enum):
    """Operational gate decision based on current resource pressure."""
    ALLOW = "ALLOW"
    ALLOW_WITH_WARNING = "ALLOW_WITH_WARNING"
    DEFER = "DEFER"
    BLOCK = "BLOCK"


class ResourceCheckResult(BaseModel):
    """Result of a preflight resource pressure check."""
    decision: ResourceDecision = ResourceDecision.ALLOW
    ram_used_pct: float = 0.0
    cpu_used_pct: float = 0.0
    reason: str = "Resources within acceptable operating parameters."
    metrics: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Performance Telemetry Models
# ---------------------------------------------------------------------------

class OperationMetric(BaseModel):
    """Execution telemetry record for a single discrete operation."""
    metric_id: str = Field(default_factory=lambda: f"met-{uuid.uuid4().hex[:8]}")
    task_id: Optional[str] = None
    action_id: Optional[str] = None
    operation_name: str
    category: str = "general"  # "llm", "tool", "browser", "workflow", "build", "test"
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    retry_count: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None
    tool_name: Optional[str] = None


class AggregatePerformanceMetrics(BaseModel):
    """Aggregated latency and throughput metrics across operations."""
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    avg_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    llm_duration_ms: float = 0.0
    tool_duration_ms: float = 0.0
    browser_duration_ms: float = 0.0
    workflow_duration_ms: float = 0.0
    category_counts: Dict[str, int] = Field(default_factory=dict)
    sampled_window_size: int = 0


# ---------------------------------------------------------------------------
# Context Budget Models
# ---------------------------------------------------------------------------

class ContextBudgetReport(BaseModel):
    """Outcome of token budgeting and context priority pruning."""
    task_goal: str = ""
    estimated_input_chars: int = 0
    estimated_tokens: int = 0
    max_context_budget_tokens: int = 4096
    selected_context_tokens: int = 0
    optimization_ratio: float = 0.0  # (baseline - optimized) / baseline
    omitted_categories: List[str] = Field(default_factory=list)
    retained_categories: List[str] = Field(default_factory=list)
    security_invariants_preserved: bool = True
    timestamp: str = Field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Checkpoint & Recovery Models
# ---------------------------------------------------------------------------

class PersistedTaskCheckpoint(BaseModel):
    """Durable checkpoint record for crash/restart recovery."""
    schema_version: int = 1
    task_id: str
    workflow_id: Optional[str] = None
    project_name: str = ""
    task_type: str = "orchestration"
    user_goal: str = ""
    current_state: str = "READY"
    current_step_id: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    pending_steps: List[str] = Field(default_factory=list)
    step_details: List[Dict[str, Any]] = Field(default_factory=list)
    retry_counts: Dict[str, int] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    recovery_status: str = "PENDING"  # "PENDING", "RESUMED", "REQUIRES_CONFIRMATION", "FAILED", "IGNORED"
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


class RecoveryDecision(BaseModel):
    """Evaluation outcome for recovering an interrupted task."""
    task_id: str
    user_goal: str = ""
    is_resumable: bool = False
    requires_confirmation: bool = False
    safe_to_auto_resume: bool = False
    dangerous_step_detected: bool = False
    dangerous_step_type: Optional[str] = None
    reason: str = ""
    checkpoint: Optional[PersistedTaskCheckpoint] = None

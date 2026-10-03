"""Strongly typed data models and enums for RYVEN 2.0 Milestone 14
Autonomous Development Orchestrator.
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


class TaskState(str, Enum):
    """Lifecycle state of an autonomous orchestration task."""
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PAUSED = "PAUSED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class StepType(str, Enum):
    """Deterministic step categories within an orchestration plan."""
    UNDERSTAND_PROJECT = "UNDERSTAND_PROJECT"
    GRAPH_QUERY = "GRAPH_QUERY"
    SCAN_PROJECT = "SCAN_PROJECT"
    PLAN_MODIFICATION = "PLAN_MODIFICATION"
    APPLY_MODIFICATION = "APPLY_MODIFICATION"
    BUILD = "BUILD"
    TEST = "TEST"
    QUALITY_GATE = "QUALITY_GATE"
    GIT_STATUS = "GIT_STATUS"
    GIT_DIFF = "GIT_DIFF"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"
    DEPLOY_PREVIEW = "DEPLOY_PREVIEW"
    DEPLOY = "DEPLOY"
    DEPLOY_VERIFY = "DEPLOY_VERIFY"
    HEALTH_CHECK = "HEALTH_CHECK"
    FINAL_REPORT = "FINAL_REPORT"


class StepState(str, Enum):
    """Execution state of a single orchestration step."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class StepDependency(BaseModel):
    """Explicit dependency requirement for a step."""
    step_id: str
    required_state: StepState = StepState.SUCCESS


class OrchestrationStep(BaseModel):
    """A single deterministic, dependency-aware step in an orchestration plan."""
    step_id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    step_type: StepType
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)  # step_ids that must complete with SUCCESS
    depends_on: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_reason: Optional[str] = None
    status: StepState = StepState.PENDING
    state: StepState = StepState.PENDING
    confirmed: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.name and self.description:
            self.name = self.description
        if not self.description and self.name:
            self.description = self.name
        if not self.dependencies and self.depends_on:
            self.dependencies = list(self.depends_on)
        if not self.depends_on and self.dependencies:
            self.depends_on = list(self.dependencies)
        if self.status != StepState.PENDING and self.state == StepState.PENDING:
            self.state = self.status
        elif self.state != StepState.PENDING and self.status == StepState.PENDING:
            self.status = self.state


class ExecutionCheckpoint(BaseModel):
    """Point-in-time snapshot of orchestration progress."""
    checkpoint_id: str = Field(default_factory=lambda: f"chk-{uuid.uuid4().hex[:8]}")
    name: str = ""
    checkpoint_name: str = ""
    step_id: str = "step-none"
    timestamp: str = Field(default_factory=utc_now_iso)
    status: str = "SUCCESS"
    summary: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.name and self.checkpoint_name:
            self.name = self.checkpoint_name
        if not self.checkpoint_name and self.name:
            self.checkpoint_name = self.name


class OrchestrationPlan(BaseModel):
    """Validated, directed acyclic execution plan."""
    plan_id: str = Field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    user_goal: str
    project_name: str
    steps: List[OrchestrationStep] = Field(default_factory=list)
    valid: bool = True
    validation_error: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepState.SUCCESS)

    @property
    def failed_steps_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepState.FAILED)


class OrchestrationTask(BaseModel):
    """Complete runtime state of an orchestration task."""
    task_id: str = Field(default_factory=lambda: f"orch-{uuid.uuid4().hex[:8]}")
    user_goal: str = ""
    goal: str = ""
    project_name: str = ""
    status: TaskState = TaskState.PLANNING
    state: TaskState = TaskState.PLANNING
    plan: Optional[OrchestrationPlan] = None
    current_step_id: Optional[str] = None
    checkpoints: List[ExecutionCheckpoint] = Field(default_factory=list)
    context_state: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.user_goal and self.goal:
            self.user_goal = self.goal
        if not self.goal and self.user_goal:
            self.goal = self.user_goal
        if self.status != TaskState.PLANNING and self.state == TaskState.PLANNING:
            self.state = self.status
        elif self.state != TaskState.PLANNING and self.status == TaskState.PLANNING:
            self.status = self.state

    def transition_to(self, new_state: TaskState, reason: str = "") -> None:
        self.status = new_state
        self.state = new_state
        self.updated_at = utc_now_iso()

    def add_checkpoint(self, name: str, step_id: str = "step-none", status: str = "SUCCESS", summary: str = "", metadata: Optional[Dict[str, Any]] = None) -> ExecutionCheckpoint:
        chk = ExecutionCheckpoint(
            name=name,
            checkpoint_name=name,
            step_id=step_id,
            status=status,
            summary=summary,
            metadata=metadata or {},
        )
        self.checkpoints.append(chk)
        self.updated_at = utc_now_iso()

        try:
            from app.runtime.checkpoint_store import checkpoint_store
            completed = [s.step_id for s in (self.plan.steps if self.plan else []) if s.status.value == "SUCCESS"]
            pending = [s.step_id for s in (self.plan.steps if self.plan else []) if s.status.value == "PENDING"]
            step_details = [s.model_dump() for s in (self.plan.steps if self.plan else [])]
            retry_counts = {s.step_id: s.retry_count for s in (self.plan.steps if self.plan else [])}
            checkpoint_store.save_checkpoint(
                task_id=self.task_id,
                user_goal=self.user_goal,
                project_name=self.project_name,
                current_state=self.status.value,
                current_step_id=step_id,
                completed_steps=completed,
                pending_steps=pending,
                step_details=step_details,
                retry_counts=retry_counts,
                metadata={"checkpoint_name": name, "status": status, "summary": summary, **(metadata or {})},
            )
        except Exception:
            pass

        return chk

    def record_checkpoint(self, checkpoint_name: str, summary: str = "", step_id: str = "step-none", status: str = "SUCCESS") -> ExecutionCheckpoint:
        return self.add_checkpoint(checkpoint_name, step_id=step_id, status=status, summary=summary)


class OrchestrationResult(BaseModel):
    """Final sanitized outcome of an autonomous orchestration session."""
    task_id: str
    user_goal: str
    project_name: str
    status: TaskState
    success: bool
    message: str
    steps_total: int
    steps_completed: int
    steps_failed: int
    step_details: List[Dict[str, Any]] = Field(default_factory=list)
    checkpoints: List[ExecutionCheckpoint] = Field(default_factory=list)
    deployment_url: Optional[str] = None
    health_status: Optional[str] = None
    health_latency_ms: Optional[float] = None
    git_commit_hash: Optional[str] = None
    modified_files: List[str] = Field(default_factory=list)
    last_successful_checkpoint: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

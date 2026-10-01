"""Structured models and state definitions for the RYVEN Workflow Engine."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class StepState(str, Enum):
    """Execution state of a single workflow step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class WorkflowState(str, Enum):
    """Lifecycle state of an entire workflow."""

    PLANNING = "PLANNING"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


def utc_now_iso() -> str:
    """Helper returning current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


class WorkflowStep(BaseModel):
    """Specification and execution state of an individual workflow step."""

    step_id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:8]}")
    name: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    status: StepState = StepState.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class WorkflowDefinition(BaseModel):
    """Structured representation of a planned or executing workflow."""

    workflow_id: str = Field(default_factory=lambda: f"wf-{uuid.uuid4().hex[:8]}")
    name: str
    description: str
    requested_by: str = "user"
    status: WorkflowState = WorkflowState.PLANNING
    steps: List[WorkflowStep] = Field(default_factory=list)
    current_step_index: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    final_result: Optional[str] = None

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def steps_completed(self) -> int:
        return sum(1 for s in self.steps if s.status == StepState.SUCCESS)

    @property
    def steps_failed(self) -> int:
        return sum(1 for s in self.steps if s.status == StepState.FAILED)


class WorkflowExecutionResult(BaseModel):
    """Final output returned by the Workflow Engine after execution."""

    workflow_id: str
    name: str
    status: WorkflowState
    success: bool
    message: str
    steps_total: int
    steps_completed: int
    steps_failed: int
    step_details: List[Dict[str, Any]] = Field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None

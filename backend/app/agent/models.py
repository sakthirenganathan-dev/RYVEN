"""RYVEN 3.0 — Structured Agent State, Plan, and Task Models.

Defines the core data structures for the autonomous agent control plane:
- AgentState and StepStatus lifecycle states.
- CapabilityGroup classifications for tool isolation.
- TaskStep, AgentPlan, Observation, and AgentExecutionResult models.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class AgentStatus(str, Enum):
    """Lifecycle status of the autonomous agent task."""
    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    RETRYING = "RETRYING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class StepStatus(str, Enum):
    """Status of an individual task step in the agent plan."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    OBSERVING = "OBSERVING"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class CapabilityGroup(str, Enum):
    """Capability domains used by CapabilityRouter to isolate tool surfaces."""
    COMPUTER = "COMPUTER"
    BROWSER = "BROWSER"
    INTERNET = "INTERNET"
    FILES = "FILES"
    DEVELOPMENT = "DEVELOPMENT"
    GIT = "GIT"
    DEPLOYMENT = "DEPLOYMENT"
    HEALTH = "HEALTH"
    KNOWLEDGE = "KNOWLEDGE"
    SYSTEM = "SYSTEM"
    WORKFLOW = "WORKFLOW"
    SAFETY = "SAFETY"


class Observation(BaseModel):
    """Observation captured after executing an action or inspecting environment."""
    observation_id: str = Field(default_factory=lambda: f"obs-{uuid.uuid4().hex[:6]}")
    step_id: Optional[str] = None
    action_target: Optional[str] = None
    success: bool = True
    summary: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_utc_now_iso)


class TaskStep(BaseModel):
    """A discrete, validated action step in an AgentPlan."""
    step_id: str
    order: int
    name: str
    capability: CapabilityGroup
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    dependencies: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    status: StepStatus = StepStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    observation: Optional[Observation] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class AgentPlan(BaseModel):
    """The dependency-ordered sequence of steps to fulfill a user goal."""
    plan_id: str = Field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    goal: str
    capabilities_required: List[CapabilityGroup] = Field(default_factory=list)
    steps: List[TaskStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now_iso)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)


class AgentState(BaseModel):
    """Runtime state representation of the RYVEN 3.0 Agent Control Plane."""
    task_id: str = Field(default_factory=lambda: f"agent-task-{uuid.uuid4().hex[:8]}")
    user_goal: str = ""
    status: AgentStatus = AgentStatus.IDLE
    current_plan: Optional[AgentPlan] = None
    current_step_index: int = 0
    current_observation: Optional[Observation] = None
    retry_count: int = 0
    max_task_retries: int = 3
    last_action: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    confirmation_token: Optional[str] = None
    confirmation_message: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    @property
    def current_step(self) -> Optional[TaskStep]:
        if self.current_plan and 0 <= self.current_step_index < len(self.current_plan.steps):
            return self.current_plan.steps[self.current_step_index]
        return None

    def advance_step(self) -> None:
        self.current_step_index += 1
        self.updated_at = _utc_now_iso()


class AgentExecutionResult(BaseModel):
    """Normalized final response from the agent control plane."""
    task_id: str
    goal: str
    status: AgentStatus
    success: bool
    message: str
    steps_total: int
    steps_completed: int
    steps_failed: int
    observations: List[Observation] = Field(default_factory=list)
    final_output: Dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

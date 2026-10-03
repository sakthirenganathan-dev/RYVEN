"""RYVEN 3.0 — Milestone 16.1 Planning Models.

Defines data models for intelligent task decomposition, complexity analysis,
risk evaluation, plan validation, and repair.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.agents.models import AgentCapability, AgentRole, redact_secrets


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PlanningMode(str, enum.Enum):
    """Execution strategy chosen for planning the goal."""
    DIRECT = "DIRECT"                    # 1-step immediate execution (fast path)
    DETERMINISTIC = "DETERMINISTIC"      # Rule-based template decomposition
    LLM_ASSISTED = "LLM_ASSISTED"        # Cognitive LLM decomposition (Qwen 2.5 7B)
    HYBRID = "HYBRID"                    # Deterministic skeleton enriched by LLM


class GoalComplexity(str, enum.Enum):
    """Categorized difficulty and scope of the user goal."""
    SIMPLE = "SIMPLE"                    # 1 tool, 1 domain, no deps, no confirmation
    MODERATE = "MODERATE"                # 2-4 steps, deterministic dependencies
    COMPLEX = "COMPLEX"                  # Multi-domain, conditional, requires LLM/DAG


class PlanRisk(str, enum.Enum):
    """Overall safety and impact rating of the plan."""
    SAFE = "SAFE"                        # Read-only queries, telemetry, simple checks
    LOW = "LOW"                          # Reversible app open, simple file read
    MEDIUM = "MEDIUM"                    # Non-destructive project build/test
    HIGH = "HIGH"                        # Code generation, file modification
    CRITICAL = "CRITICAL"                # Git commit, deploy, external publish


class PlanStatus(str, enum.Enum):
    """Lifecycle state of a planned decomposition."""
    DRAFT = "DRAFT"                      # Initial rough task list
    VALIDATING = "VALIDATING"            # Undergoing safety/DAG validation
    VALID = "VALID"                      # Successfully validated
    INVALID = "INVALID"                  # Rejected by validator (unrepairable)
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"  # Gated by confirmation
    EXECUTABLE = "EXECUTABLE"            # Ready to hand off to AgentCoordinator
    FAILED = "FAILED"                    # Planning or execution failed


# ---------------------------------------------------------------------------
# Structured Intent Representation
# ---------------------------------------------------------------------------

class ExtractedIntent(BaseModel):
    """Structured extraction from normalized natural language goal."""
    raw_goal: str
    normalized_goal: str
    primary_action: str
    target_entity: Optional[str] = None
    target_project: Optional[str] = None
    domains: List[str] = Field(default_factory=list)
    requested_capabilities: List[AgentCapability] = Field(default_factory=list)
    is_consequential: bool = False
    requires_confirmation: bool = False
    parameters: Dict[str, Any] = Field(default_factory=dict)
    missing_critical_info: Optional[str] = None


# ---------------------------------------------------------------------------
# Task Draft for Planning
# ---------------------------------------------------------------------------

class PlanningTaskDraft(BaseModel):
    """A single proposed task step within a plan draft."""
    task_id: str = Field(default_factory=lambda: f"ptask-{uuid.uuid4().hex[:8]}")
    title: Optional[str] = None
    objective: str
    capability: AgentCapability
    preferred_role: Optional[AgentRole] = None
    tool_name: Optional[str] = None
    tools: List[str] = Field(default_factory=list)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_type: Optional[str] = None
    risk: PlanRisk = PlanRisk.SAFE
    estimated_duration_sec: float = 5.0

    def __init__(self, **data: Any):
        if "id" in data and "task_id" not in data:
            data["task_id"] = data.pop("id")
        if "role" in data and "preferred_role" not in data:
            data["preferred_role"] = data.pop("role")
        if "dependencies" in data and "depends_on" not in data:
            data["depends_on"] = data.pop("dependencies")
        if "tools" in data and data["tools"] and "tool_name" not in data:
            data["tool_name"] = data["tools"][0]
        super().__init__(**data)

    @property
    def id(self) -> str:
        return self.task_id

    @property
    def role(self) -> Optional[AgentRole]:
        return self.preferred_role

    @property
    def dependencies(self) -> List[str]:
        return self.depends_on

    def model_post_init(self, __context: Any) -> None:
        self.arguments = redact_secrets(self.arguments)
        if self.tool_name and not self.tools:
            self.tools = [self.tool_name]
        elif self.tools and not self.tool_name:
            self.tool_name = self.tools[0]


# ---------------------------------------------------------------------------
# Planning Request & Result
# ---------------------------------------------------------------------------

class PlanningRequest(BaseModel):
    """Input request specification for the planning engine."""
    request_id: str = Field(default_factory=lambda: f"preq-{uuid.uuid4().hex[:8]}")
    user_goal: str
    context: Dict[str, Any] = Field(default_factory=dict)
    preferred_mode: Optional[PlanningMode] = None
    deadline_sec: Optional[float] = None
    constraints: List[str] = Field(default_factory=list)
    project_name: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        self.context = redact_secrets(self.context)


class PlanValidationResult(BaseModel):
    """Detailed results from deterministic plan validation."""
    is_valid: bool = True
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    repair_suggestions: List[str] = Field(default_factory=list)
    repair_count: int = 0


class PlanningResult(BaseModel):
    """Complete, validated planning outcome ready for execution."""
    plan_id: str = Field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    raw_goal: str
    normalized_goal: str
    complexity: GoalComplexity
    planning_mode: PlanningMode
    tasks: List[PlanningTaskDraft] = Field(default_factory=list)
    dependencies: Dict[str, List[str]] = Field(default_factory=dict)  # task_id -> [dep_task_ids]
    parallel_groups: List[List[str]] = Field(default_factory=list)    # groups of task_ids that can run in parallel
    overall_risk: PlanRisk = PlanRisk.SAFE
    risk: PlanRisk = PlanRisk.SAFE
    requires_confirmation: bool = False
    confirmation_points: List[str] = Field(default_factory=list)     # task_ids requiring user confirmation
    estimated_steps: int = 0
    estimated_parallelism: int = 1
    explanation: str = ""
    warnings: List[str] = Field(default_factory=list)
    validation: PlanValidationResult = Field(default_factory=PlanValidationResult)
    is_valid: bool = True
    status: PlanStatus = PlanStatus.DRAFT

    def model_post_init(self, __context: Any) -> None:
        self.estimated_steps = len(self.tasks)
        self.risk = self.overall_risk
        self.is_valid = self.validation.is_valid and self.status != PlanStatus.INVALID


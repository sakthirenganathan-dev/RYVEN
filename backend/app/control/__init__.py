"""RYVEN 3.0 M17.0 — Unified Computer & Internet Control Plane Package."""

from app.control.models import (
    ControlRequest,
    ControlResult,
    ControlStatus,
    DiscoveredScopeItem,
    FailureClass,
    ObservationRecord,
    PermissionCategory,
    ScopeBoundary,
)
from app.control.permissions import (
    CapabilityPermissionManager,
    PermissionCheckResult,
    permission_manager,
)
def __getattr__(name: str):
    if name in ("ObserverEngine", "observer_engine"):
        import app.control.observer as obs
        return getattr(obs, name)
    if name in ("RyvenControlEngine", "ryven_control_engine"):
        import app.control.engine as eng
        return getattr(eng, name)
    if name in (
        "ComputerWorkflowEngine",
        "computer_workflow_engine",
        "ComputerWorkflowPlan",
        "ComputerWorkflowStep",
        "ComputerWorkflowState",
        "ComputerWorkflowResult",
    ):
        import app.control.workflow as wf
        return getattr(wf, name)
    if name in (
        "AdaptiveComputerUseController",
        "adaptive_controller",
        "ObservedComputerState",
        "StateDiffClassification",
        "ActionIdempotency",
        "AdaptiveExecutionResult",
        "StateEvaluationResult",
    ):
        import app.control.adaptive as adapt
        return getattr(adapt, name)
    if name in (
        "ReliabilityScenario",
        "ScenarioExecutionResult",
        "ReliabilityMetrics",
        "ReliabilityMetricsTracker",
        "ReliabilityScenarioRunner",
        "reliability_runner",
        "create_canonical_scenarios",
    ):
        import app.control.reliability as rel
        return getattr(rel, name)
    if name in (
        "TaskCapability",
        "UnifiedTaskStatus",
        "UnifiedTaskStep",
        "UnifiedTask",
        "UnifiedTaskResult",
        "TaskCapabilityRouter",
        "UnifiedTaskOrchestrator",
        "unified_task_orchestrator",
    ):
        import app.control.task as utask
        return getattr(utask, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ControlRequest",
    "ControlResult",
    "ControlStatus",
    "DiscoveredScopeItem",
    "FailureClass",
    "ObservationRecord",
    "PermissionCategory",
    "ScopeBoundary",
    "CapabilityPermissionManager",
    "PermissionCheckResult",
    "permission_manager",
    "ObserverEngine",
    "observer_engine",
    "RyvenControlEngine",
    "ryven_control_engine",
    "ComputerWorkflowEngine",
    "computer_workflow_engine",
    "ComputerWorkflowPlan",
    "ComputerWorkflowStep",
    "ComputerWorkflowState",
    "ComputerWorkflowResult",
    "AdaptiveComputerUseController",
    "adaptive_controller",
    "ObservedComputerState",
    "StateDiffClassification",
    "ActionIdempotency",
    "AdaptiveExecutionResult",
    "StateEvaluationResult",
    "ReliabilityScenario",
    "ScenarioExecutionResult",
    "ReliabilityMetrics",
    "ReliabilityMetricsTracker",
    "ReliabilityScenarioRunner",
    "reliability_runner",
    "create_canonical_scenarios",
    "TaskCapability",
    "UnifiedTaskStatus",
    "UnifiedTaskStep",
    "UnifiedTask",
    "UnifiedTaskResult",
    "TaskCapabilityRouter",
    "UnifiedTaskOrchestrator",
    "unified_task_orchestrator",
]

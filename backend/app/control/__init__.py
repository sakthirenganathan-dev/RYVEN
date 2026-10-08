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
    if name in (
        "CanonicalScenarioType",
        "CANONICAL_SCENARIOS",
        "ScenarioSpecification",
        "TaskHistoryEntry",
        "TaskHistoryStore",
        "task_history_store",
        "TaskResumptionManager",
        "format_user_friendly_failure",
        "TaskLatencyTracker",
        "verify_task_isolation",
        "E2EValidationFramework",
        "e2e_validation_framework",
    ):
        import app.control.e2e as e2e_mod
        return getattr(e2e_mod, name)
    if name in (
        "ConversationTurn",
        "ConversationContext",
        "ConversationManager",
        "ConversationalIntentType",
        "ConversationalResponse",
        "conversation_manager",
    ):
        import app.control.conversation as conv_mod
        return getattr(conv_mod, name)
    if name in (
        "MultimodalContext",
        "MultimodalContextEngine",
        "multimodal_context_engine",
    ):
        import app.control.multimodal as multi_mod
        return getattr(multi_mod, name)
    if name in (
        "LongHorizonTaskState",
        "LongHorizonTaskStep",
        "TaskExecutionJournalEntry",
        "TaskProgressSnapshot",
        "LongHorizonTask",
        "TaskPersistenceRepository",
        "LongHorizonTaskManager",
        "long_horizon_task_manager",
    ):
        import app.control.long_horizon as lh_mod
        return getattr(lh_mod, name)
    if name in (
        "TaskPriority",
        "SchedulerState",
        "ScheduledTask",
        "ScheduledQueueItem",
        "SchedulerStatus",
        "MultiTaskScheduler",
        "multi_task_scheduler",
    ):
        import app.control.scheduler as sched_mod
        return getattr(sched_mod, name)
    if name in (
        "ResourceType",
        "LockMode",
        "ResourceKey",
        "ResourceRequest",
        "ResourceDescriptor",
        "ResourceLease",
        "ResourceOwner",
        "ResourceState",
        "ResourceAllocationStatus",
        "LeaseState",
        "ResourceSnapshot",
        "ResourceManagerStatus",
        "ResourceConflict",
        "ResourceDecision",
        "ResourceAcquisitionTimeoutError",
        "TaskResourceManager",
        "ResourceManager",
        "resource_manager",
        "CANONICAL_RESOURCE_ORDER",
        "DEFAULT_RESOURCE_CAPACITIES",
    ):
        import app.control.resources as res_mod
        return getattr(res_mod, name)
    if name in (
        "ConcurrencyState",
        "PreemptionStatus",
        "ExecutionSlot",
        "ConcurrencyAdmissionDecision",
        "PreemptionDecision",
        "ConcurrencySnapshot",
        "SafeConcurrencyController",
        "concurrency_controller",
    ):
        import app.control.concurrency as conc_mod
        return getattr(conc_mod, name)
    if name in (
        "RecoveryState",
        "TaskRecoveryResult",
        "RecoveryRunSummary",
        "ShutdownReport",
        "MultiTaskRecoveryManager",
        "RecoveryManager",
        "recovery_manager",
        "multi_task_recovery_manager",
    ):
        import app.control.recovery as rec_mod
        return getattr(rec_mod, name)
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
    "CanonicalScenarioType",
    "CANONICAL_SCENARIOS",
    "ScenarioSpecification",
    "TaskHistoryEntry",
    "TaskHistoryStore",
    "task_history_store",
    "TaskResumptionManager",
    "format_user_friendly_failure",
    "TaskLatencyTracker",
    "verify_task_isolation",
    "E2EValidationFramework",
    "e2e_validation_framework",
    "ConversationTurn",
    "ConversationContext",
    "ConversationManager",
    "ConversationalIntentType",
    "ConversationalResponse",
    "conversation_manager",
    "MultimodalContext",
    "MultimodalContextEngine",
    "multimodal_context_engine",
    "LongHorizonTaskState",
    "LongHorizonTaskStep",
    "TaskExecutionJournalEntry",
    "TaskProgressSnapshot",
    "LongHorizonTask",
    "TaskPersistenceRepository",
    "LongHorizonTaskManager",
    "long_horizon_task_manager",
    "TaskPriority",
    "SchedulerState",
    "ScheduledTask",
    "ScheduledQueueItem",
    "SchedulerStatus",
    "MultiTaskScheduler",
    "multi_task_scheduler",
    "ResourceType",
    "LockMode",
    "ResourceKey",
    "ResourceRequest",
    "ResourceLease",
    "ResourceOwner",
    "ResourceState",
    "LeaseState",
    "ResourceSnapshot",
    "ResourceConflict",
    "ResourceDecision",
    "ResourceAcquisitionTimeoutError",
    "TaskResourceManager",
    "ResourceManager",
    "resource_manager",
    "ConcurrencyState",
    "PreemptionStatus",
    "ExecutionSlot",
    "ConcurrencyAdmissionDecision",
    "PreemptionDecision",
    "ConcurrencySnapshot",
    "SafeConcurrencyController",
    "concurrency_controller",
    "RecoveryState",
    "TaskRecoveryResult",
    "RecoveryRunSummary",
    "ShutdownReport",
    "MultiTaskRecoveryManager",
    "RecoveryManager",
    "recovery_manager",
    "multi_task_recovery_manager",
]

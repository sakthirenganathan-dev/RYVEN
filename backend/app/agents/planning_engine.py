"""RYVEN 3.0 — Milestone 16.1 Central Planning Engine.

Unifies goal normalization, complexity classification, intent analysis,
deterministic vs. LLM planning, dependency inference, parallelism, risk rating,
plan validation, and bounded plan repair into a single intelligent control surface.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.agents.capabilities import find_roles_for_capability, get_tools_for_capability
from app.agents.complexity import ComplexityClassifier
from app.agents.dependency_analyzer import DependencyAndRiskAnalyzer
from app.agents.intent import IntentAnalyzer
from app.agents.llm_planner import LLMPlanner
from app.agents.models import (
    MAX_GRAPH_RUNTIME_SEC,
    AgentCapability,
    AgentRole,
    AgentStatus,
    AgentTask,
    redact_secrets,
)
from app.agents.normalizer import GoalNormalizer
from app.agents.planning_models import (
    GoalComplexity,
    PlanRisk,
    PlanStatus,
    PlanValidationResult,
    PlanningMode,
    PlanningRequest,
    PlanningResult,
    PlanningTaskDraft,
)
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.task_graph import AgentTaskGraph
from app.agents.validator import PlanRepairEngine, PlanValidator
from app.core.logging_config import logger
from app.runtime.performance import runtime_performance_service
from app.tools.registry import ToolRegistry, create_default_registry


class PlanningEngine:
    """Intelligent, multi-stage task decomposition and planning engine."""

    def __init__(
        self,
        agent_reg: Optional[AgentRegistry] = None,
        tool_reg: Optional[ToolRegistry] = None,
        llm_planner: Optional[LLMPlanner] = None,
        event_bus: Optional[ActionEventBus] = None,
    ) -> None:
        self.agent_reg = agent_reg or agent_registry
        self.tool_reg = tool_reg or create_default_registry()
        self.llm_planner = llm_planner or LLMPlanner()
        self.validator = PlanValidator(agent_reg=self.agent_reg, tool_reg=self.tool_reg)
        self.repair_engine = PlanRepairEngine(validator=self.validator)
        self.event_bus = event_bus or action_bus

    async def create_plan(self, goal: Union[str, PlanningRequest], **kwargs: Any) -> PlanningResult:
        """Convenience method accepting a raw string goal or PlanningRequest."""
        if isinstance(goal, str):
            request = PlanningRequest(user_goal=goal, **kwargs)
        else:
            request = goal
        return await self.plan(request)

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        """Execute the full end-to-end intelligent planning pipeline."""
        t0 = time.monotonic()
        raw_goal = request.user_goal.strip()
        logger.info(f"[PLANNING_ENGINE] Planning requested for goal: {raw_goal!r}")

        # 1. Goal Normalization
        normalized_goal, params, missing_info = GoalNormalizer.normalize(raw_goal)
        if request.project_name and "project_name" not in params:
            params["project_name"] = request.project_name

        # 2. Complexity Classification
        complexity, mode, comp_reason = ComplexityClassifier.classify(normalized_goal)
        if request.preferred_mode:
            mode = request.preferred_mode

        # 3. Intent Extraction
        intent = IntentAnalyzer.analyze(normalized_goal, params)

        await self._emit_event(
            ActionType.PLAN_CREATED,
            ActionStatus.STARTED,
            f"Draft plan created for: {normalized_goal[:60]}",
            safe_metadata={"complexity": complexity.value, "mode": mode.value},
        )

        # 4. Generate Task Drafts based on chosen Mode
        tasks: List[PlanningTaskDraft] = []
        warnings: List[str] = []

        if missing_info:
            warnings.append(missing_info)

        if mode == PlanningMode.DIRECT or complexity == GoalComplexity.SIMPLE:
            tasks = self._plan_simple_direct(normalized_goal, intent, params)
        elif mode == PlanningMode.LLM_ASSISTED:
            tasks = await self.llm_planner.plan_goal(
                normalized_goal=normalized_goal,
                context=request.context,
                project_name=params.get("project_name"),
            )
            # If LLM returned empty (offline or malformed), fallback to deterministic
            if not tasks:
                logger.info("[PLANNING_ENGINE] LLM returned empty plan; falling back to deterministic template.")
                tasks = self._plan_deterministic(normalized_goal, intent, params)
                mode = PlanningMode.HYBRID
        else:
            # Deterministic / Hybrid
            tasks = self._plan_deterministic(normalized_goal, intent, params)

        # 5. Validate the raw task list before enrichment so repair events are emitted for
        # structurally-invalid plans rather than silently auto-corrected by dependency analysis.
        await self._emit_event(
            ActionType.PLAN_VALIDATION_STARTED,
            ActionStatus.STARTED,
            f"Validating plan for: {normalized_goal[:60]}",
            safe_metadata={"complexity": complexity.value, "task_count": len(tasks)},
        )

        validation = self.validator.validate(tasks)
        repair_count = 0

        if not validation.is_valid:
            await self._emit_event(
                ActionType.PLAN_REPAIR_STARTED,
                ActionStatus.STARTED,
                f"Attempting plan repair: {validation.errors}",
                safe_metadata={"errors": validation.errors, "repair_suggestions": validation.repair_suggestions},
            )
            tasks, validation = self.repair_engine.repair_plan(tasks)
            repair_count = validation.repair_count

            if validation.is_valid:
                await self._emit_event(
                    ActionType.PLAN_REPAIRED,
                    ActionStatus.COMPLETED,
                    f"Plan successfully repaired in {repair_count} iteration(s).",
                    safe_metadata={"repair_count": repair_count, "task_count": len(tasks)},
                )
            else:
                await self._emit_event(
                    ActionType.PLAN_REPAIR_FAILED,
                    ActionStatus.FAILED,
                    f"Plan repair failed: {validation.errors}",
                    safe_metadata={"errors": validation.errors},
                )

        # 6. Dependency, Parallelism & Risk Enrichment
        tasks, dep_map, parallel_groups, overall_risk, conf_points = (
            DependencyAndRiskAnalyzer.analyze_and_enrich(tasks)
        )

        # 7. Final validation pass after enrichment to ensure semantic safety and confirmation gates.
        validation = self.validator.validate(tasks)
        if not validation.is_valid:
            await self._emit_event(
                ActionType.PLAN_REPAIR_STARTED,
                ActionStatus.STARTED,
                f"Repairing enriched plan: {validation.errors}",
                safe_metadata={"errors": validation.errors, "repair_suggestions": validation.repair_suggestions},
            )
            tasks, validation = self.repair_engine.repair_plan(tasks)
            repair_count = validation.repair_count
            if validation.is_valid:
                await self._emit_event(
                    ActionType.PLAN_REPAIRED,
                    ActionStatus.COMPLETED,
                    f"Enriched plan successfully repaired in {repair_count} iteration(s).",
                    safe_metadata={"repair_count": repair_count, "task_count": len(tasks)},
                )
            else:
                await self._emit_event(
                    ActionType.PLAN_REPAIR_FAILED,
                    ActionStatus.FAILED,
                    f"Enriched plan repair failed: {validation.errors}",
                    safe_metadata={"errors": validation.errors},
                )

        # 7. Formulate Human-Readable Explanation
        explanation = self._generate_explanation(tasks, conf_points, overall_risk)

        # 8. Determine Final Status
        status = PlanStatus.VALID if validation.is_valid else PlanStatus.INVALID
        if status == PlanStatus.VALID and conf_points:
            status = PlanStatus.REQUIRES_CONFIRMATION

        dur_ms = (time.monotonic() - t0) * 1000
        logger.info(
            f"[PLANNING_ENGINE] Plan generated in {dur_ms:.2f}ms. "
            f"Status: {status.value} (Tasks: {len(tasks)}, Complexity: {complexity.value}, Mode: {mode.value})"
        )

        plan_result = PlanningResult(
            raw_goal=raw_goal,
            normalized_goal=normalized_goal,
            complexity=complexity,
            planning_mode=mode,
            tasks=tasks,
            dependencies=dep_map,
            parallel_groups=parallel_groups,
            overall_risk=overall_risk,
            requires_confirmation=len(conf_points) > 0,
            confirmation_points=conf_points,
            estimated_steps=len(tasks),
            estimated_parallelism=max(1, max((len(g) for g in parallel_groups), default=1)),
            explanation=explanation,
            warnings=warnings + validation.warnings,
            validation=validation,
            status=status,
        )

        # Emit Completion Events
        if plan_result.status != PlanStatus.INVALID:
            await self._emit_event(
                ActionType.PLAN_VALIDATED,
                ActionStatus.COMPLETED,
                f"Plan validated: {len(tasks)} tasks ready for execution.",
                safe_metadata={
                    "plan_id": plan_result.plan_id,
                    "complexity": complexity.value,
                    "risk": overall_risk.value,
                    "steps": len(tasks),
                },
            )
            await self._emit_event(
                ActionType.PLAN_EXECUTION_READY,
                ActionStatus.COMPLETED,
                f"Plan execution ready with {len(tasks)} steps.",
                safe_metadata={"plan_id": plan_result.plan_id},
            )
        else:
            await self._emit_event(
                ActionType.PLAN_INVALID,
                ActionStatus.FAILED,
                f"Plan validation rejected: {validation.errors}",
                safe_metadata={"plan_id": plan_result.plan_id, "errors": validation.errors},
            )

        return plan_result

    def to_agent_task_graph(self, plan: PlanningResult) -> AgentTaskGraph:
        """Convert a validated PlanningResult into an executable AgentTaskGraph."""
        graph = AgentTaskGraph(goal=plan.normalized_goal)

        for draft in plan.tasks:
            assigned_agent = None
            if draft.preferred_role:
                agents = self.agent_reg.get_agents_by_role(draft.preferred_role)
                if agents:
                    assigned_agent = agents[0].agent_id

            task = AgentTask(
                task_id=draft.task_id,
                workflow_id=graph.graph_id,
                agent_id=assigned_agent,
                role=draft.preferred_role or AgentRole.COORDINATOR,
                objective=draft.objective,
                capability=draft.capability,
                tool_name=draft.tool_name,
                arguments=draft.arguments,
                depends_on=list(draft.depends_on),
                requires_confirmation=draft.requires_confirmation,
                confirmation_type=draft.confirmation_type,
            )
            graph.add_task(task)

        graph.validate_graph()
        return graph

    def build_task_graph(self, plan: PlanningResult) -> AgentTaskGraph:
        """Alias for to_agent_task_graph."""
        return self.to_agent_task_graph(plan)

    def revise_plan(self, plan: PlanningResult, failed_task_id: str, failure_reason: str) -> PlanningResult:
        """Revise an existing plan after a task execution failure."""
        revised = plan.model_copy(deep=True)
        for t in revised.tasks:
            if t.task_id == failed_task_id:
                t.arguments["failure_reason"] = failure_reason
        revised.warnings.append(f"Task '{failed_task_id}' failed: {failure_reason}. Revised plan accordingly.")
        revised.status = PlanStatus.VALID
        return revised


    # -----------------------------------------------------------------------
    # Internal Task Generators
    # -----------------------------------------------------------------------

    def _plan_simple_direct(
        self, normalized_goal: str, intent: Any, params: Dict[str, Any]
    ) -> List[PlanningTaskDraft]:
        """Fast path: Generates 1 single task for direct operations."""
        lower = normalized_goal.lower()

        # Desktop App
        if "open visual studio code" in lower:
            return [
                PlanningTaskDraft(
                    objective="Launch Visual Studio Code",
                    capability=AgentCapability.WINDOWS_APP,
                    preferred_role=AgentRole.COMPUTER,
                    tool_name="open_application",
                    arguments={"app_name": "Visual Studio Code"},
                )
            ]
        if "open google chrome" in lower:
            return [
                PlanningTaskDraft(
                    objective="Launch Google Chrome",
                    capability=AgentCapability.WINDOWS_APP,
                    preferred_role=AgentRole.COMPUTER,
                    tool_name="open_application",
                    arguments={"app_name": "Google Chrome"},
                )
            ]

        # Web Search
        if "search the web" in lower:
            query = params.get("query") or normalized_goal.replace("Search the web for", "").strip()
            return [
                PlanningTaskDraft(
                    objective=f"Search web for: {query}",
                    capability=AgentCapability.WEB_SEARCH,
                    preferred_role=AgentRole.RESEARCH,
                    tool_name="internet_search",
                    arguments={"query": query},
                )
            ]

        # System Status
        if "system status" in lower or "telemetry" in lower:
            return [
                PlanningTaskDraft(
                    objective="Inspect system status and telemetry",
                    capability=AgentCapability.SYSTEM_STATUS,
                    preferred_role=AgentRole.COMPUTER,
                    tool_name="system_status",
                    arguments={},
                )
            ]

        # Cancellation
        if "cancel" in lower or "stop" in lower:
            return [
                PlanningTaskDraft(
                    objective="Cancel active tasks",
                    capability=AgentCapability.TASK_COORDINATION,
                    preferred_role=AgentRole.COORDINATOR,
                    tool_name=None,
                    arguments={"action": "cancel"},
                )
            ]

        # General single capability task
        cap = intent.requested_capabilities[0] if intent.requested_capabilities else AgentCapability.TASK_COORDINATION
        roles = find_roles_for_capability(cap)
        role = roles[0] if roles else AgentRole.COORDINATOR
        tools = get_tools_for_capability(cap)
        tool = tools[0] if tools else None

        return [
            PlanningTaskDraft(
                objective=normalized_goal,
                capability=cap,
                preferred_role=role,
                tool_name=tool,
                arguments=params,
            )
        ]

    def _plan_deterministic(
        self, normalized_goal: str, intent: Any, params: Dict[str, Any]
    ) -> List[PlanningTaskDraft]:
        """Template-based decomposition for structured multi-step goals."""
        lower = normalized_goal.lower()
        pname = params.get("project_name") or "demo-app"

        # Pattern 1: Research and Build
        if "research" in lower and any(w in lower for w in ("build", "create", "demo")):
            t1 = PlanningTaskDraft(
                objective=f"Research technical concepts and libraries for: {normalized_goal}",
                capability=AgentCapability.WEB_RESEARCH,
                preferred_role=AgentRole.RESEARCH,
                tool_name="web_research",
                arguments={"topic": normalized_goal, "query": normalized_goal},
            )
            t2 = PlanningTaskDraft(
                objective=f"Plan project implementation for {pname}",
                capability=AgentCapability.CODE_GENERATION,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="plan_project_modifications",
                arguments={"project_name": pname, "goal": normalized_goal},
                depends_on=[t1.task_id],
            )
            t3 = PlanningTaskDraft(
                objective=f"Apply code modifications to {pname}",
                capability=AgentCapability.CODE_MODIFICATION,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="apply_project_modification",
                arguments={"project_name": pname},
                depends_on=[t2.task_id],
            )
            t4 = PlanningTaskDraft(
                objective=f"Build project {pname}",
                capability=AgentCapability.BUILD,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="build_project",
                arguments={"project_name": pname},
                depends_on=[t3.task_id],
            )
            t5 = PlanningTaskDraft(
                objective=f"Run test suite on {pname}",
                capability=AgentCapability.TEST,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="test_project",
                arguments={"project_name": pname},
                depends_on=[t4.task_id],
            )
            t6 = PlanningTaskDraft(
                objective=f"Verify quality gate for {pname}",
                capability=AgentCapability.VERIFICATION,
                preferred_role=AgentRole.VERIFICATION,
                tool_name="quality_gate",
                arguments={"project_name": pname},
                depends_on=[t5.task_id],
            )
            return [t1, t2, t3, t4, t5, t6]

        # Pattern 2: Project Creation & Build Pipeline
        if any(w in lower for w in ("create a", "scaffold a")) and "project" in lower:
            t1 = PlanningTaskDraft(
                objective=f"Create project directory and files for {pname}",
                capability=AgentCapability.CODE_GENERATION,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="create_project_folder",
                arguments={"project_name": pname},
            )
            t2 = PlanningTaskDraft(
                objective=f"Build project {pname}",
                capability=AgentCapability.BUILD,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="build_project",
                arguments={"project_name": pname},
                depends_on=[t1.task_id],
            )
            t3 = PlanningTaskDraft(
                objective=f"Run tests for {pname}",
                capability=AgentCapability.TEST,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="test_project",
                arguments={"project_name": pname},
                depends_on=[t2.task_id],
            )
            # Add Git / Deploy if requested
            tasks = [t1, t2, t3]
            if "deploy" in lower:
                t_deploy = PlanningTaskDraft(
                    objective=f"Deploy project {pname}",
                    capability=AgentCapability.DEPLOY,
                    preferred_role=AgentRole.DEVELOPER,
                    tool_name="deployment_deploy",
                    arguments={"project_name": pname},
                    depends_on=[t3.task_id],
                    requires_confirmation=True,
                    confirmation_type="DEPLOYMENT_CONFIRMATION",
                )
                tasks.append(t_deploy)
            return tasks

        # Pattern 3: Diagnostics & Fix
        if any(w in lower for w in ("diagnose", "why build fails", "errors", "fix")):
            t1 = PlanningTaskDraft(
                objective=f"Scan project workspace for {pname}",
                capability=AgentCapability.PROJECT_SCAN,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="scan_existing_project",
                arguments={"project_name": pname},
            )
            t2 = PlanningTaskDraft(
                objective=f"Execute build to capture error diagnostics for {pname}",
                capability=AgentCapability.BUILD,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="build_project",
                arguments={"project_name": pname},
                depends_on=[t1.task_id],
            )
            t3 = PlanningTaskDraft(
                objective=f"Inspect project knowledge graph for {pname}",
                capability=AgentCapability.KNOWLEDGE_GRAPH,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="graph_status",
                arguments={"project_name": pname},
                depends_on=[t2.task_id],
            )
            return [t1, t2, t3]

        # Pattern 4: Desktop App + Open Project
        if ("open visual studio code" in lower or "open vs code" in lower or "launch visual studio code" in lower) and ("project" in lower or "folder" in lower):
            t1 = PlanningTaskDraft(
                objective="Launch Visual Studio Code",
                capability=AgentCapability.WINDOWS_APP,
                preferred_role=AgentRole.COMPUTER,
                tool_name="open_application",
                arguments={"app_name": "Visual Studio Code"},
            )
            t2 = PlanningTaskDraft(
                objective="Open project folder in editor",
                capability=AgentCapability.WINDOWS_FILES,
                preferred_role=AgentRole.COMPUTER,
                tool_name="open_folder",
                arguments={"path": params.get("path") or "."},
                depends_on=[t1.task_id],
            )
            return [t1, t2]

        # Default fallback
        return self._plan_simple_direct(normalized_goal, intent, params)

    def _generate_explanation(
        self, tasks: List[PlanningTaskDraft], conf_points: List[str], overall_risk: PlanRisk
    ) -> str:
        """Formulates clear, non-technical explanation for user."""
        lines = [f"I have analyzed the request and prepared an execution plan with {len(tasks)} step(s):"]
        for idx, task in enumerate(tasks, start=1):
            conf_tag = " [Requires Confirmation]" if task.task_id in conf_points else ""
            lines.append(f"{idx}. {task.objective}{conf_tag}")

        if conf_points:
            lines.append(f"\nNote: Safe execution will pause before consequential operations to await your approval.")
        return "\n".join(lines)

    async def _emit_event(
        self, action_type: ActionType, status: ActionStatus, message: str, safe_metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Safely broadcast planning action events to the ActionEventBus."""
        event = ActionEvent(
            action_type=action_type,
            status=status,
            title=message,
            description=message,
            safe_metadata=redact_secrets(safe_metadata or {}),
        )
        try:
            await self.event_bus.publish(event)
        except Exception as exc:
            logger.debug(f"[PLANNING_ENGINE] Event emission notice: {exc}")



# Global singleton instance
planning_engine = PlanningEngine()

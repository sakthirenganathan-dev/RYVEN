"""RYVEN 3.0 — Milestone 16.1 Dependency, Parallelism & Risk Analyzer.

Infers semantic dependencies, identifies parallel task clusters, evaluates
risk tiers, and predicts confirmation gates before execution.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from app.agents.models import AgentCapability
from app.agents.planning_models import PlanRisk, PlanningTaskDraft


class DependencyAndRiskAnalyzer:
    """Analyzes task dependencies, concurrency boundaries, and risk/confirmation profiles."""

    # Explicit precedence rules: capability A must run BEFORE capability B if both exist
    PRECEDENCE_RULES = [
        (AgentCapability.WEB_SEARCH, AgentCapability.PAGE_READING),
        (AgentCapability.WEB_RESEARCH, AgentCapability.CODE_GENERATION),
        (AgentCapability.PROJECT_SCAN, AgentCapability.CODE_GENERATION),
        (AgentCapability.CODE_GENERATION, AgentCapability.CODE_MODIFICATION),
        (AgentCapability.CODE_MODIFICATION, AgentCapability.BUILD),
        (AgentCapability.BUILD, AgentCapability.TEST),
        (AgentCapability.TEST, AgentCapability.GIT),
        (AgentCapability.GIT, AgentCapability.DEPLOY),
        (AgentCapability.DEPLOY, AgentCapability.HEALTH_CHECK),
    ]

    # Capabilities that require explicit confirmation gates
    CONFIRMATION_CAPABILITIES = {
        AgentCapability.GIT,
        AgentCapability.DEPLOY,
    }

    CONFIRMATION_TOOLS = {
        "git_commit",
        "git_push",
        "deployment_deploy",
        "deploy",
        "form_submit",
        "browser_download",
    }

    @classmethod
    def analyze_and_enrich(
        cls, tasks: List[PlanningTaskDraft]
    ) -> Tuple[List[PlanningTaskDraft], Dict[str, List[str]], List[List[str]], PlanRisk, List[str]]:
        """Analyze dependencies, detect parallelism, assess risk, and flag confirmation gates.

        Returns:
            Tuple of (enriched_tasks, dependency_map, parallel_groups, overall_risk, confirmation_point_ids)
        """
        task_by_id = {t.task_id: t for t in tasks}
        dep_map: Dict[str, List[str]] = {t.task_id: list(t.depends_on) for t in tasks}
        confirmation_points: List[str] = []
        highest_risk = PlanRisk.SAFE

        # 1. Infer Missing Semantic Dependencies
        for i, task in enumerate(tasks):
            # Check confirmation requirements
            is_destructive = any(
                w in (task.objective + " " + (task.title or "")).lower()
                for w in ("delete", "force push", "push", "drop", "destroy", "truncate")
            )
            is_conf = (
                task.capability in cls.CONFIRMATION_CAPABILITIES
                or (task.tool_name and task.tool_name.lower() in cls.CONFIRMATION_TOOLS)
                or task.requires_confirmation
                or is_destructive
            )
            if is_conf:
                task.requires_confirmation = True
                task.confirmation_type = "USER_APPROVAL"
                if task.task_id not in confirmation_points:
                    confirmation_points.append(task.task_id)

            # Evaluate task risk
            task_risk = cls._evaluate_task_risk(task)
            task.risk = task_risk
            if cls._risk_level_value(task_risk) > cls._risk_level_value(highest_risk):
                highest_risk = task_risk

            # Semantic precedence linking
            for prior_task in tasks[:i]:
                for cap_before, cap_after in cls.PRECEDENCE_RULES:
                    if prior_task.capability == cap_before and task.capability == cap_after:
                        if prior_task.task_id not in dep_map[task.task_id]:
                            dep_map[task.task_id].append(prior_task.task_id)
                            task.depends_on.append(prior_task.task_id)

        # 2. Parallelism Detection
        # Tasks with 0 dependencies can execute in parallel initially.
        # Tasks sharing identical dependencies and non-conflicting domains can execute in parallel.
        parallel_groups: List[List[str]] = []
        root_tasks = [t.task_id for t in tasks if not dep_map[t.task_id]]
        if len(root_tasks) > 1:
            parallel_groups.append(root_tasks)

        # Find sibling tasks that share the same non-empty dependency set
        dep_signatures: Dict[str, List[str]] = {}
        for t in tasks:
            if dep_map[t.task_id]:
                sig = ",".join(sorted(dep_map[t.task_id]))
                dep_signatures.setdefault(sig, []).append(t.task_id)

        for sig, sibling_ids in dep_signatures.items():
            if len(sibling_ids) > 1:
                # Only parallelize if none of the siblings modify the same codebase
                has_mutating = any(
                    task_by_id[tid].capability in (AgentCapability.CODE_MODIFICATION, AgentCapability.BUILD)
                    for tid in sibling_ids
                )
                if not has_mutating:
                    parallel_groups.append(sibling_ids)

        return tasks, dep_map, parallel_groups, highest_risk, confirmation_points

    @classmethod
    def _evaluate_task_risk(cls, task: PlanningTaskDraft) -> PlanRisk:
        if task.capability in (AgentCapability.GIT, AgentCapability.DEPLOY):
            return PlanRisk.CRITICAL
        if task.capability in (AgentCapability.CODE_MODIFICATION, AgentCapability.CODE_GENERATION):
            return PlanRisk.HIGH
        if task.capability in (AgentCapability.BUILD, AgentCapability.TEST):
            return PlanRisk.MEDIUM
        if task.capability in (AgentCapability.WINDOWS_APP, AgentCapability.WINDOWS_FILES, AgentCapability.BROWSER_ACTION):
            return PlanRisk.LOW
        return PlanRisk.SAFE

    @classmethod
    def _risk_level_value(cls, risk: PlanRisk) -> int:
        order = {
            PlanRisk.SAFE: 1,
            PlanRisk.LOW: 2,
            PlanRisk.MEDIUM: 3,
            PlanRisk.HIGH: 4,
            PlanRisk.CRITICAL: 5,
        }
        return order.get(risk, 1)

    @classmethod
    def infer_dependencies(cls, tasks: List[PlanningTaskDraft]) -> List[PlanningTaskDraft]:
        enriched, _, _, _, _ = cls.analyze_and_enrich(tasks)
        return enriched

    @classmethod
    def calculate_parallelism(cls, tasks: List[PlanningTaskDraft]) -> int:
        _, _, groups, _, _ = cls.analyze_and_enrich(tasks)
        if not groups:
            return 1
        return max(len(g) for g in groups)

    @classmethod
    def evaluate_risk(cls, tasks: List[PlanningTaskDraft]) -> PlanRisk:
        _, _, _, risk, _ = cls.analyze_and_enrich(tasks)
        return risk

    @classmethod
    def predict_confirmation(cls, tasks: List[PlanningTaskDraft]) -> Tuple[bool, List[str]]:
        _, _, _, _, conf_points = cls.analyze_and_enrich(tasks)
        return len(conf_points) > 0, conf_points


dependency_risk_analyzer = DependencyAndRiskAnalyzer()


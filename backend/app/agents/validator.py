"""RYVEN 3.0 — Milestone 16.1 Plan Validator & Bounded Repair Engine.

Enforces 15 strict safety, structural, and security rules on all generated plans.
Attempts bounded repair (up to 3 iterations) for recoverable structural flaws.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from app.agents.capabilities import CAPABILITY_TOOL_MAP, get_tools_for_capability
from app.agents.models import (
    MAX_ACTIVE_AGENTS,
    MAX_TASKS_PER_GRAPH,
    AgentCapability,
    AgentRole,
    redact_secrets,
)
from app.agents.planning_models import PlanValidationResult, PlanningTaskDraft
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.security import FORBIDDEN_SHELL_TOOLS
from app.core.logging_config import logger
from app.tools.registry import ToolRegistry, create_default_registry


MAX_REPAIR_ATTEMPTS = 3


class PlanValidator:
    """Validates plan drafts against 15 strict safety, DAG, and security rules."""

    def __init__(
        self,
        agent_reg: Optional[AgentRegistry] = None,
        tool_reg: Optional[ToolRegistry] = None,
    ) -> None:
        self.agent_reg = agent_reg or agent_registry
        self.tool_reg = tool_reg or create_default_registry()

    def validate(
        self,
        tasks: List[PlanningTaskDraft],
        deadline_sec: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
    ) -> PlanValidationResult:
        """Run all 15 validation rules against a list of draft tasks."""
        errors: List[str] = []
        warnings: List[str] = []
        repair_suggestions: List[str] = []

        if not tasks:
            return PlanValidationResult(is_valid=False, errors=["Plan has zero tasks."])

        # Rule 11: Deadline Constraints
        effective_deadline = deadline_sec if deadline_sec is not None else deadline_seconds
        if effective_deadline is not None and effective_deadline < 0:
            errors.append("Deadline constraint cannot be negative.")

        # Rule 12: Task Count Limits
        if len(tasks) > MAX_TASKS_PER_GRAPH:
            errors.append(f"Task count ({len(tasks)}) exceeds safety maximum ({MAX_TASKS_PER_GRAPH}).")

        # Rule 1 & 7: Unique & Non-empty Task IDs
        seen_ids: Set[str] = set()
        active_roles: Set[AgentRole] = set()

        tool_aliases = {
            "web_search": "internet_search",
            "page_reader": "read_page",
            "npm_build": "build_project",
            "run_tests": "test_project",
        }

        for task in tasks:
            if not task.task_id or not task.task_id.strip():
                errors.append("Encountered task with empty task_id.")
            elif task.task_id in seen_ids:
                errors.append(f"Duplicate task ID detected: '{task.task_id}'.")
            seen_ids.add(task.task_id)

            # Rule 2: Valid Capabilities
            if not isinstance(task.capability, AgentCapability):
                errors.append(f"Task '{task.task_id}' has invalid capability: '{task.capability}'.")

            # Rule 3: Valid Roles
            if task.preferred_role:
                if not isinstance(task.preferred_role, AgentRole):
                    errors.append(f"Task '{task.task_id}' has invalid preferred_role: '{task.preferred_role}'.")
                else:
                    active_roles.add(task.preferred_role)

            # Rule 4 & 8: Valid Tools in ToolRegistry
            tool_list = list(task.tools)
            if task.tool_name and task.tool_name not in tool_list:
                tool_list.append(task.tool_name)

            for raw_tool in tool_list:
                clean_tool = raw_tool.strip()
                # Rule 9: Forbidden shell actions
                if clean_tool.lower() in FORBIDDEN_SHELL_TOOLS:
                    errors.append(f"Forbidden tool '{clean_tool}' in task '{task.task_id}'.")
                else:
                    canonical_tool = tool_aliases.get(clean_tool.lower(), clean_tool)
                    if not self.tool_reg.has_tool(canonical_tool) and not self.tool_reg.has_tool(clean_tool):
                        errors.append(f"Unregistered tool '{clean_tool}' in task '{task.task_id}'.")
                        repair_suggestions.append(f"Replace unknown tool '{clean_tool}' with standard capability tool.")

            # Rule 10: Confirmation Requirements for Consequential Tools and Deploy
            is_deploy = task.capability in (AgentCapability.DEPLOY, AgentCapability.GIT)
            is_consequential_tool = task.tool_name and task.tool_name.lower() in {
                "git_commit", "git_push", "deployment_deploy", "deploy", "form_submit", "delete_file"
            }
            if (is_deploy or is_consequential_tool) and not task.requires_confirmation:
                errors.append(f"Task '{task.task_id}' with capability '{task.capability.value}' must require user confirmation.")
                repair_suggestions.append(f"Flag task '{task.task_id}' for confirmation.")

            # Rule 15: Secret Scrubbing Verification
            args_str = str(task.arguments)
            if any(k in args_str for k in ("BEGIN PRIVATE KEY", "sk_live_", "Bearer eyJ")):
                errors.append(f"Credential leakage detected in task '{task.task_id}' arguments.")

        # Rule 13: Agent Count Limits
        if len(active_roles) > MAX_ACTIVE_AGENTS:
            errors.append(f"Active role count ({len(active_roles)}) exceeds limit ({MAX_ACTIVE_AGENTS}).")

        # Rule 5: Dependency Existence
        for task in tasks:
            for dep_id in task.depends_on:
                if dep_id not in seen_ids:
                    errors.append(f"Task '{task.task_id}' depends on unknown task '{dep_id}'.")
                    repair_suggestions.append(f"Remove dangling dependency '{dep_id}' from task {task.task_id}.")
                if dep_id == task.task_id:
                    errors.append(f"Task '{task.task_id}' cannot depend on itself.")
                    repair_suggestions.append(f"Remove self-dependency from task {task.task_id}.")

        # Rule 6: Cycle Detection (Kahn's Algorithm)
        has_cycle, cycle_err = self._check_cycles(tasks, seen_ids)
        if has_cycle:
            errors.append(f"Cyclic dependency cycle detected: {cycle_err}")
            repair_suggestions.append("Reorder tasks topologically or remove circular dependencies.")

        is_valid = len(errors) == 0
        return PlanValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            repair_suggestions=repair_suggestions,
        )

    def _check_cycles(self, tasks: List[PlanningTaskDraft], valid_ids: Set[str]) -> Tuple[bool, Optional[str]]:
        """Return True if a cycle exists."""
        in_degree: Dict[str, int] = {t.task_id: 0 for t in tasks}
        adj: Dict[str, List[str]] = {t.task_id: [] for t in tasks}

        for task in tasks:
            for dep in task.depends_on:
                if dep in in_degree:
                    adj[dep].append(task.task_id)
                    in_degree[task.task_id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited_count = 0

        while queue:
            node = queue.pop(0)
            visited_count += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count != len(tasks):
            return True, f"Only {visited_count} of {len(tasks)} tasks could be scheduled without cycle."
        return False, None


class PlanRepairEngine:
    """Attempts bounded automated repairs on invalid plan drafts."""

    def __init__(self, validator: Optional[PlanValidator] = None) -> None:
        self.validator = validator or PlanValidator()

    def repair(
        self,
        tasks: List[PlanningTaskDraft],
        errors: Optional[Union[List[str], PlanValidationResult]] = None,
        max_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> Tuple[List[PlanningTaskDraft], int]:
        """Convenience method for repairing tasks given an error list, returning (repaired_tasks, count)."""
        current_tasks = [task.model_copy(deep=True) for task in tasks]
        attempts = 0

        # Perform targeted heuristic repairs
        for task in current_tasks:
            # 1. Strip forbidden tools if present
            task.tools = [t for t in task.tools if t.lower() not in FORBIDDEN_SHELL_TOOLS]
            if task.tool_name and task.tool_name.lower() in FORBIDDEN_SHELL_TOOLS:
                task.tool_name = task.tools[0] if task.tools else None

            # 2. Add confirmation flag if DEPLOY or consequential
            if task.capability in (AgentCapability.DEPLOY, AgentCapability.GIT) or (
                task.tool_name and task.tool_name.lower() in ("deploy", "git_commit", "git_push", "deployment_deploy")
            ):
                task.requires_confirmation = True

        # 3. Check for inverted scaffold / build dependency
        scaffold_tasks = [t for t in current_tasks if t.capability in (AgentCapability.CODE_MODIFICATION, AgentCapability.CODE_GENERATION)]
        build_tasks = [t for t in current_tasks if t.capability == AgentCapability.BUILD]
        if scaffold_tasks and build_tasks:
            for s_task in scaffold_tasks:
                for b_task in build_tasks:
                    if b_task.task_id in s_task.depends_on:
                        s_task.depends_on.remove(b_task.task_id)
                        if s_task.task_id not in b_task.depends_on:
                            b_task.depends_on.append(s_task.task_id)

        attempts = 1
        return current_tasks, attempts

    def repair_plan(self, tasks: List[PlanningTaskDraft]) -> Tuple[List[PlanningTaskDraft], PlanValidationResult]:
        """Iteratively repair tasks up to MAX_REPAIR_ATTEMPTS times."""
        current_tasks = [task.model_copy(deep=True) for task in tasks]
        attempts = 0

        while attempts < MAX_REPAIR_ATTEMPTS:
            attempts += 1
            validation = self.validator.validate(current_tasks)
            validation.repair_count = attempts

            if validation.is_valid:
                return current_tasks, validation

            # Unrepairable security violations cannot be auto-repaired
            has_forbidden_tool = any("Forbidden tool" in err for err in validation.errors)
            has_leaked_creds = any("Credential leakage" in err for err in validation.errors)
            if has_forbidden_tool or has_leaked_creds:
                logger.warning(f"[PLAN_REPAIR] Refusing auto-repair of security violation: {validation.errors}")
                return current_tasks, validation

            # Attempt automated fixes
            repaired_tasks: List[PlanningTaskDraft] = []
            seen_ids = {t.task_id for t in current_tasks}

            for task in current_tasks:
                # 1. Strip dangling or self dependencies
                clean_deps = [d for d in task.depends_on if d in seen_ids and d != task.task_id]
                task.depends_on = clean_deps

                # 2. Fix unknown tools: replace with standard capability tool
                if task.tool_name and not self.validator.tool_reg.has_tool(task.tool_name):
                    candidates = get_tools_for_capability(task.capability)
                    task.tool_name = candidates[0] if candidates else None

                task.tools = [t for t in task.tools if self.validator.tool_reg.has_tool(t)]
                if task.tool_name and task.tool_name not in task.tools:
                    task.tools.append(task.tool_name)

                # 3. Ensure confirmation on consequential tools
                if task.capability in (AgentCapability.DEPLOY, AgentCapability.GIT) or (
                    task.tool_name and task.tool_name.lower() in ("git_commit", "git_push", "deployment_deploy", "deploy")
                ):
                    task.requires_confirmation = True

                repaired_tasks.append(task)

            # 4. Resolve cyclic dependencies by reordering based on task index
            has_cycle, _ = self.validator._check_cycles(repaired_tasks, seen_ids)
            if has_cycle:
                # Break backward edges
                id_to_idx = {t.task_id: idx for idx, t in enumerate(repaired_tasks)}
                for t in repaired_tasks:
                    curr_idx = id_to_idx[t.task_id]
                    t.depends_on = [dep for dep in t.depends_on if id_to_idx.get(dep, 9999) < curr_idx]

            current_tasks = repaired_tasks

        # Final validation pass
        final_validation = self.validator.validate(current_tasks)
        final_validation.repair_count = attempts
        return current_tasks, final_validation


plan_validator = PlanValidator()
plan_repair_engine = PlanRepairEngine()

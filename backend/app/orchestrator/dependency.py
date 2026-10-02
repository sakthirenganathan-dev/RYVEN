"""Dependency Graph & DAG Validation Engine for RYVEN Milestone 14 Orchestrator."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

from app.orchestrator.models import OrchestrationPlan, OrchestrationStep, StepState


class DependencyGraph:
    """Directed Acyclic Graph (DAG) validator and scheduler for orchestration steps."""

    def __init__(self, steps: List[OrchestrationStep]) -> None:
        self.steps = steps
        self.steps_by_id: Dict[str, OrchestrationStep] = {s.step_id: s for s in steps}
        self.adj_list: Dict[str, List[str]] = defaultdict(list)     # parent -> children
        self.rev_adj_list: Dict[str, List[str]] = defaultdict(list) # child -> parents (dependencies)

        for step in steps:
            for dep_id in step.dependencies:
                self.adj_list[dep_id].append(step.step_id)
                self.rev_adj_list[step.step_id].append(dep_id)

    def validate_plan(self) -> Tuple[bool, str]:
        """Validate that all dependencies exist, no self-loops, and no circular dependencies."""
        all_ids = set(self.steps_by_id.keys())

        # 1. Missing dependency check
        for step in self.steps:
            for dep_id in step.dependencies:
                if dep_id not in all_ids:
                    return False, f"Step '{step.name}' ({step.step_id}) depends on non-existent step '{dep_id}'."
                if dep_id == step.step_id:
                    return False, f"Step '{step.name}' ({step.step_id}) has a self-dependency."

        # 2. Cycle detection via Kahn's algorithm
        in_degree: Dict[str, int] = {s.step_id: len(s.dependencies) for s in self.steps}
        queue = deque([s_id for s_id, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            node = queue.popleft()
            visited_count += 1
            for child in self.adj_list.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited_count < len(self.steps):
            cycle_nodes = [s_id for s_id, deg in in_degree.items() if deg > 0]
            return False, f"Circular dependency detected involving steps: {', '.join(cycle_nodes)}."

        return True, ""

    def validate(self) -> Tuple[bool, str]:
        """Alias for validate_plan."""
        return self.validate_plan()

    def get_ready_steps(self, completed: Optional[Set[str]] = None) -> List[OrchestrationStep]:
        """Return all steps currently in PENDING state whose parent dependencies are all satisfied."""
        ready: List[OrchestrationStep] = []
        for step in self.steps:
            if completed is not None and step.step_id in completed:
                continue
            if step.status != StepState.PENDING:
                continue
            deps_satisfied = True
            for dep_id in step.dependencies:
                if completed is not None:
                    if dep_id not in completed:
                        deps_satisfied = False
                        break
                else:
                    parent = self.steps_by_id.get(dep_id)
                    if not parent or parent.status != StepState.SUCCESS:
                        deps_satisfied = False
                        break
            if deps_satisfied:
                ready.append(step)
        return ready

    def get_topological_order(self) -> List[str]:
        """Return step IDs ordered by dependency topological sort."""
        in_degree = {s.step_id: len(s.dependencies) for s in self.steps}
        queue = deque([s_id for s_id, deg in in_degree.items() if deg == 0])
        order: List[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for child in self.adj_list.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return order

    def topological_sort(self) -> List[str]:
        """Alias for get_topological_order."""
        return self.get_topological_order()

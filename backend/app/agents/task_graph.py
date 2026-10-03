"""RYVEN 3.0 — Agent Task Graph (DAG Engine).

Provides deterministic Directed Acyclic Graph (DAG) scheduling, validation,
cycle detection (Kahn's algorithm), ready-task resolution, and failure cascade handling.
Enforces spawn bounds (MAX_TASKS_PER_GRAPH) and prevents uncontrolled recursive execution.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agents.models import (
    MAX_RETRIES,
    MAX_TASKS_PER_GRAPH,
    AgentStatus,
    AgentTask,
    _utc_now_iso,
    redact_secrets,
)
from app.core.logging_config import logger


class AgentTaskGraph:
    """Directed Acyclic Graph (DAG) representing an executable multi-agent plan."""

    def __init__(
        self,
        goal: str = "",
        graph_id: Optional[str] = None,
    ) -> None:
        self.graph_id: str = graph_id or f"graph-{uuid.uuid4().hex[:8]}"
        self.goal: str = goal
        self.status: AgentStatus = AgentStatus.CREATED
        self.tasks: Dict[str, AgentTask] = {}
        self.adj_list: Dict[str, List[str]] = defaultdict(list)     # parent -> children
        self.rev_adj_list: Dict[str, List[str]] = defaultdict(list) # child -> parents (dependencies)
        self.created_at: str = _utc_now_iso()
        self.updated_at: str = _utc_now_iso()
        self.completed_at: Optional[str] = None
        self.error: Optional[str] = None
        self.context_data: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Graph Construction
    # -----------------------------------------------------------------------

    def add_task(
        self,
        task: AgentTask,
        depends_on: Optional[List[str]] = None,
    ) -> AgentTask:
        """Add an AgentTask to the graph and record dependency edges."""
        if len(self.tasks) >= MAX_TASKS_PER_GRAPH:
            raise ValueError(
                f"Cannot add task '{task.objective}': graph exceeds MAX_TASKS_PER_GRAPH ({MAX_TASKS_PER_GRAPH})."
            )

        task.graph_id = self.graph_id
        deps = list(depends_on) if depends_on else list(task.dependencies)
        task.dependencies = deps

        self.tasks[task.task_id] = task
        self.rev_adj_list[task.task_id] = list(deps)

        for dep_id in deps:
            self.adj_list[dep_id].append(task.task_id)

        self.updated_at = _utc_now_iso()
        return task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Retrieve a task by ID."""
        return self.tasks.get(task_id)

    # -----------------------------------------------------------------------
    # DAG Validation & Cycle Detection
    # -----------------------------------------------------------------------

    def validate_graph(self) -> Tuple[bool, str]:
        """Validate DAG: check missing dependencies, self-dependencies, and cycles."""
        all_ids = set(self.tasks.keys())

        # 1. Missing dependency and self-dependency checks
        for task_id, task in self.tasks.items():
            for dep_id in task.dependencies:
                if dep_id not in all_ids:
                    return False, f"Task '{task.objective}' ({task_id}) depends on unknown task '{dep_id}'."
                if dep_id == task_id:
                    return False, f"Task '{task.objective}' ({task_id}) has a self-dependency."

        # 2. Cycle detection via Kahn's algorithm
        in_degree: Dict[str, int] = {t_id: len(t.dependencies) for t_id, t in self.tasks.items()}
        queue = deque([t_id for t_id, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            node = queue.popleft()
            visited_count += 1
            for child in self.adj_list.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited_count < len(self.tasks):
            cycle_nodes = [t_id for t_id, deg in in_degree.items() if deg > 0]
            return False, f"Circular dependency detected involving tasks: {', '.join(cycle_nodes)}."

        return True, ""

    def get_topological_order(self) -> List[str]:
        """Return task IDs sorted in topological dependency order."""
        in_degree = {t_id: len(t.dependencies) for t_id, t in self.tasks.items()}
        queue = deque([t_id for t_id, deg in in_degree.items() if deg == 0])
        order: List[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for child in self.adj_list.get(node, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        return order

    # -----------------------------------------------------------------------
    # Execution State & Scheduler Helpers
    # -----------------------------------------------------------------------

    def get_ready_tasks(self) -> List[AgentTask]:
        """Return tasks that are ready to run (all parent dependencies are COMPLETED)."""
        ready: List[AgentTask] = []
        for task in self.tasks.values():
            if task.status not in (AgentStatus.CREATED, AgentStatus.READY, AgentStatus.WAITING):
                continue

            deps_satisfied = True
            for dep_id in task.dependencies:
                parent = self.tasks.get(dep_id)
                if not parent or parent.status != AgentStatus.COMPLETED:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                ready.append(task)

        return ready

    def mark_task_started(self, task_id: str) -> None:
        """Mark a task as actively running."""
        task = self.tasks.get(task_id)
        if task:
            task.status = AgentStatus.RUNNING
            task.started_at = _utc_now_iso()
            self.updated_at = _utc_now_iso()
            if self.status != AgentStatus.RUNNING:
                self.status = AgentStatus.RUNNING

    def mark_task_completed(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """Mark a task as successfully completed and store sanitized results."""
        task = self.tasks.get(task_id)
        if not task:
            return

        task.status = AgentStatus.COMPLETED
        task.completed_at = _utc_now_iso()
        task.result = redact_secrets(result or {})
        self.updated_at = _utc_now_iso()

        # Update overall graph status if all tasks finished
        if self.is_completed():
            self.status = AgentStatus.COMPLETED
            self.completed_at = _utc_now_iso()

    def mark_task_failed(self, task_id: str, error: str) -> bool:
        """Handle task failure with retry evaluation or downstream blocking.

        Returns True if task was requeued for retry, False if marked permanently FAILED.
        """
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.error = error
        self.updated_at = _utc_now_iso()

        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = AgentStatus.READY
            logger.info(
                f"[TASK_GRAPH] Task '{task.objective}' ({task_id}) failed; retrying ({task.retry_count}/{task.max_retries})."
            )
            return True

        # Permanently failed
        task.status = AgentStatus.FAILED
        task.completed_at = _utc_now_iso()
        self._cascade_block_downstream(task_id)

        # Check if entire graph should fail
        self.status = AgentStatus.FAILED
        self.error = f"Task '{task.objective}' failed: {error}"
        self.completed_at = _utc_now_iso()
        return False

    def _cascade_block_downstream(self, failed_task_id: str) -> None:
        """Recursively mark all downstream child tasks as BLOCKED."""
        queue = deque(self.adj_list.get(failed_task_id, []))
        visited: Set[str] = set()

        while queue:
            child_id = queue.popleft()
            if child_id in visited:
                continue
            visited.add(child_id)

            child = self.tasks.get(child_id)
            if child and child.status in (AgentStatus.CREATED, AgentStatus.READY, AgentStatus.WAITING):
                child.status = AgentStatus.BLOCKED
                child.error = f"Blocked by failed dependency '{failed_task_id}'."
                logger.warning(f"[TASK_GRAPH] Task '{child.objective}' blocked by failed dependency '{failed_task_id}'.")

            for grandchild in self.adj_list.get(child_id, []):
                queue.append(grandchild)

    def cancel_graph(self, reason: str = "User cancelled execution") -> None:
        """Cancel graph execution and all active/pending tasks."""
        self.status = AgentStatus.CANCELLED
        self.error = reason
        self.completed_at = _utc_now_iso()
        self.updated_at = _utc_now_iso()

        for task in self.tasks.values():
            if task.status in (AgentStatus.CREATED, AgentStatus.READY, AgentStatus.WAITING, AgentStatus.RUNNING):
                task.status = AgentStatus.CANCELLED
                task.completed_at = _utc_now_iso()
                task.error = reason

    # -----------------------------------------------------------------------
    # Status Inspection
    # -----------------------------------------------------------------------

    def is_completed(self) -> bool:
        """True if every task in the graph has reached COMPLETED status."""
        return bool(self.tasks) and all(t.status == AgentStatus.COMPLETED for t in self.tasks.values())

    def is_failed(self) -> bool:
        """True if the graph or any unrecovered task has failed."""
        return self.status == AgentStatus.FAILED or any(t.status == AgentStatus.FAILED for t in self.tasks.values())

    def is_cancelled(self) -> bool:
        return self.status == AgentStatus.CANCELLED

    def is_terminal(self) -> bool:
        return self.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED)

    def get_progress(self) -> Dict[str, Any]:
        """Calculate high-level task graph execution stats."""
        total = len(self.tasks)
        if total == 0:
            return {"total": 0, "completed": 0, "pct": 100.0, "status": self.status.value}

        completed = sum(1 for t in self.tasks.values() if t.status == AgentStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == AgentStatus.FAILED)
        blocked = sum(1 for t in self.tasks.values() if t.status == AgentStatus.BLOCKED)
        running = sum(1 for t in self.tasks.values() if t.status == AgentStatus.RUNNING)
        pending = total - completed - failed - blocked - running

        return {
            "graph_id": self.graph_id,
            "goal": self.goal,
            "status": self.status.value,
            "total_tasks": total,
            "completed": completed,
            "failed": failed,
            "blocked": blocked,
            "running": running,
            "pending": pending,
            "progress_pct": round((completed / total) * 100.0, 1),
        }

    # -----------------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize graph to dictionary for persistence/API responses."""
        return {
            "graph_id": self.graph_id,
            "goal": self.goal,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "tasks": {t_id: t.model_dump() for t_id, t in self.tasks.items()},
            "dependencies": {k: list(v) for k, v in self.rev_adj_list.items()},
            "progress": self.get_progress(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentTaskGraph:
        """Reconstruct graph from dictionary."""
        graph = cls(goal=data.get("goal", ""), graph_id=data.get("graph_id"))
        graph.status = AgentStatus(data.get("status", AgentStatus.CREATED.value))
        graph.created_at = data.get("created_at", _utc_now_iso())
        graph.updated_at = data.get("updated_at", _utc_now_iso())
        graph.completed_at = data.get("completed_at")
        graph.error = data.get("error")

        raw_tasks = data.get("tasks", {})
        for t_id, t_dict in raw_tasks.items():
            task = AgentTask(**t_dict)
            graph.tasks[t_id] = task

        raw_deps = data.get("dependencies", {})
        for child_id, deps in raw_deps.items():
            graph.rev_adj_list[child_id] = list(deps)
            for parent_id in deps:
                graph.adj_list[parent_id].append(child_id)

        return graph

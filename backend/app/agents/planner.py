"""RYVEN 3.0 — Task Decomposer and Capability-Based Planner.

Transforms high-level natural language user goals into structured AgentTaskGraphs.
Enforces the principle: simple requests yield single tasks, complex goals yield
minimal deterministic DAGs. Assigns roles based on registered capabilities.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.agents.capabilities import (
    CAPABILITY_TOOL_MAP,
    find_roles_for_capability,
    get_tools_for_capability,
)
from app.agents.models import (
    MAX_TASKS_PER_GRAPH,
    AgentCapability,
    AgentRole,
    AgentStatus,
    AgentTask,
)
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.task_graph import AgentTaskGraph
from app.core.logging_config import logger
from app.tools.registry import ToolRegistry
from app.workflows.confirmation import ConfirmationManager


class TaskDecomposer:
    """Decomposes user intentions into validated, dependency-ordered AgentTaskGraphs."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        tool_registry: Optional[ToolRegistry] = None,
        confirmation_manager: Optional[ConfirmationManager] = None,
    ) -> None:
        self.registry = registry or agent_registry
        self.tool_registry = tool_registry
        self.confirmation_mgr = confirmation_manager or ConfirmationManager()

    def decompose(
        self,
        goal: str,
        project_name: Optional[str] = None,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> AgentTaskGraph:
        """Analyze user goal and construct an AgentTaskGraph.

        Simple requests stay simple (1 task). Complex multi-domain requests
        decompose into structured, dependency-ordered stages.
        """
        graph = AgentTaskGraph(goal=goal)
        normalized = goal.strip().lower()

        # -------------------------------------------------------------------
        # 1. Simple Single-Task Heuristics
        # -------------------------------------------------------------------

        # A. Pure Web Search / Query
        if self._is_simple_web_search(normalized):
            query = self._extract_search_query(goal)
            self._add_task_to_graph(
                graph=graph,
                objective=f"Search web for: {query}",
                capability=AgentCapability.WEB_SEARCH,
                preferred_role=AgentRole.RESEARCH,
                tool_name="internet_search",
                arguments={"query": query},
            )
            graph.validate_graph()
            return graph

        # B. Pure App Launch / Desktop Action
        if self._is_simple_app_launch(normalized):
            app_name = self._extract_app_name(goal)
            self._add_task_to_graph(
                graph=graph,
                objective=f"Open application: {app_name}",
                capability=AgentCapability.WINDOWS_APP,
                preferred_role=AgentRole.COMPUTER,
                tool_name="open_application",
                arguments={"app_name": app_name},
            )
            graph.validate_graph()
            return graph

        # C. Pure Browser Open / URL Navigation
        if self._is_simple_browser_nav(normalized):
            url = self._extract_url(goal)
            self._add_task_to_graph(
                graph=graph,
                objective=f"Open website: {url}",
                capability=AgentCapability.BROWSER_ACTION,
                preferred_role=AgentRole.BROWSER,
                tool_name="open_browser",
                arguments={"url": url},
            )
            graph.validate_graph()
            return graph

        # D. Pure Health Check
        if self._is_simple_health_check(normalized):
            target = self._extract_url(goal) or "http://localhost:8000"
            self._add_task_to_graph(
                graph=graph,
                objective=f"Check health status of {target}",
                capability=AgentCapability.HEALTH_CHECK,
                preferred_role=AgentRole.VERIFICATION,
                tool_name="health_check",
                arguments={"target": target},
            )
            graph.validate_graph()
            return graph

        # -------------------------------------------------------------------
        # 2. Multi-Stage Complex Task Decomposition
        # -------------------------------------------------------------------

        p_name = project_name or self._extract_project_name(goal)

        # Pattern A: Research + Development Pipeline ("Research X and build demo")
        if any(w in normalized for w in ("research", "search", "investigate", "explore")) and \
           any(w in normalized for w in ("build", "create", "demo", "implement", "project")):
            return self._build_research_and_dev_graph(graph, goal, p_name)

        # Pattern B: Diagnostic & Fix ("Check my project and tell me why build fails")
        if any(w in normalized for w in ("why", "error", "fail", "broken", "diagnose", "fix", "inspect")):
            return self._build_diagnostic_graph(graph, goal, p_name)

        # Pattern C: Full Development, Test, and Deploy Pipeline
        if any(w in normalized for w in ("create", "add", "implement", "build", "modify", "deploy")):
            return self._build_dev_pipeline_graph(graph, goal, p_name)

        # Default fallback: General Coordinator Task
        self._add_task_to_graph(
            graph=graph,
            objective=goal,
            capability=AgentCapability.TASK_COORDINATION,
            preferred_role=AgentRole.COORDINATOR,
            arguments={"goal": goal},
        )
        graph.validate_graph()
        return graph

    # -----------------------------------------------------------------------
    # Graph Building Helpers
    # -----------------------------------------------------------------------

    def _build_research_and_dev_graph(
        self, graph: AgentTaskGraph, goal: str, project_name: str
    ) -> AgentTaskGraph:
        """Research -> Synthesize -> Create Project -> Build -> Test -> Quality Gate."""
        # 1. Research
        t_research = self._add_task_to_graph(
            graph=graph,
            objective=f"Research technical concepts and libraries for: {goal}",
            capability=AgentCapability.WEB_RESEARCH,
            preferred_role=AgentRole.RESEARCH,
            tool_name="web_research",
            arguments={"topic": goal, "query": goal},
        )

        # 2. Project Plan & Implementation
        t_plan = self._add_task_to_graph(
            graph=graph,
            objective=f"Plan project implementation for {project_name}",
            capability=AgentCapability.CODE_GENERATION,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="plan_project_modifications",
            arguments={"project_name": project_name, "goal": goal},
            depends_on=[t_research.task_id],
        )

        # 3. Apply Code Modification
        t_code = self._add_task_to_graph(
            graph=graph,
            objective=f"Apply code modifications to {project_name}",
            capability=AgentCapability.CODE_MODIFICATION,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="apply_project_modification",
            arguments={"project_name": project_name},
            depends_on=[t_plan.task_id],
        )

        # 4. Build
        t_build = self._add_task_to_graph(
            graph=graph,
            objective=f"Build project {project_name}",
            capability=AgentCapability.BUILD,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="build_project",
            arguments={"project_name": project_name},
            depends_on=[t_code.task_id],
        )

        # 5. Test
        t_test = self._add_task_to_graph(
            graph=graph,
            objective=f"Run automated tests for {project_name}",
            capability=AgentCapability.TEST,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="test_project",
            arguments={"project_name": project_name},
            depends_on=[t_build.task_id],
        )

        # 6. Quality Gate
        self._add_task_to_graph(
            graph=graph,
            objective=f"Verify quality gate for {project_name}",
            capability=AgentCapability.VERIFICATION,
            preferred_role=AgentRole.VERIFICATION,
            tool_name="quality_gate",
            arguments={"project_name": project_name},
            depends_on=[t_test.task_id],
        )

        graph.validate_graph()
        return graph

    def _build_diagnostic_graph(
        self, graph: AgentTaskGraph, goal: str, project_name: str
    ) -> AgentTaskGraph:
        """Scan Project -> Query Knowledge Graph -> Build / Test -> Diagnose."""
        t_scan = self._add_task_to_graph(
            graph=graph,
            objective=f"Scan project structure for {project_name}",
            capability=AgentCapability.PROJECT_SCAN,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="scan_existing_project",
            arguments={"project_name": project_name},
        )

        t_graph = self._add_task_to_graph(
            graph=graph,
            objective=f"Inspect symbol relationships in {project_name}",
            capability=AgentCapability.KNOWLEDGE_GRAPH,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="graph_query",
            arguments={"project_name": project_name, "query": goal},
            depends_on=[t_scan.task_id],
        )

        t_build = self._add_task_to_graph(
            graph=graph,
            objective=f"Run diagnostic build on {project_name}",
            capability=AgentCapability.BUILD,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="build_project",
            arguments={"project_name": project_name},
            depends_on=[t_graph.task_id],
        )

        self._add_task_to_graph(
            graph=graph,
            objective=f"Analyze test and build output for {project_name}",
            capability=AgentCapability.VERIFICATION,
            preferred_role=AgentRole.VERIFICATION,
            tool_name="quality_gate",
            arguments={"project_name": project_name},
            depends_on=[t_build.task_id],
        )

        graph.validate_graph()
        return graph

    def _build_dev_pipeline_graph(
        self, graph: AgentTaskGraph, goal: str, project_name: str
    ) -> AgentTaskGraph:
        """Scan -> Code -> Build -> Test -> Quality -> Git/Deploy (Confirmation Gated)."""
        t_scan = self._add_task_to_graph(
            graph=graph,
            objective=f"Scan project {project_name}",
            capability=AgentCapability.PROJECT_SCAN,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="scan_existing_project",
            arguments={"project_name": project_name},
        )

        t_code = self._add_task_to_graph(
            graph=graph,
            objective=f"Plan and implement changes for {project_name}",
            capability=AgentCapability.CODE_MODIFICATION,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="apply_project_modification",
            arguments={"project_name": project_name, "goal": goal},
            depends_on=[t_scan.task_id],
        )

        t_build = self._add_task_to_graph(
            graph=graph,
            objective=f"Build {project_name}",
            capability=AgentCapability.BUILD,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="build_project",
            arguments={"project_name": project_name},
            depends_on=[t_code.task_id],
        )

        t_test = self._add_task_to_graph(
            graph=graph,
            objective=f"Run test suite on {project_name}",
            capability=AgentCapability.TEST,
            preferred_role=AgentRole.DEVELOPER,
            tool_name="test_project",
            arguments={"project_name": project_name},
            depends_on=[t_build.task_id],
        )

        t_quality = self._add_task_to_graph(
            graph=graph,
            objective=f"Quality gate audit for {project_name}",
            capability=AgentCapability.VERIFICATION,
            preferred_role=AgentRole.VERIFICATION,
            tool_name="quality_gate",
            arguments={"project_name": project_name},
            depends_on=[t_test.task_id],
        )

        # Check if git or deploy was requested
        lowered = goal.lower()
        if "commit" in lowered or "push" in lowered or "git" in lowered:
            t_git = self._add_task_to_graph(
                graph=graph,
                objective=f"Commit changes for {project_name}",
                capability=AgentCapability.GIT,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="git_commit",
                arguments={"message": f"feat: {goal[:60]}"},
                depends_on=[t_quality.task_id],
                requires_confirmation=True,
                confirmation_type="GIT_COMMIT",
            )
            last_step_id = t_git.task_id
        else:
            last_step_id = t_quality.task_id

        if "deploy" in lowered or "ship" in lowered:
            self._add_task_to_graph(
                graph=graph,
                objective=f"Deploy project {project_name}",
                capability=AgentCapability.DEPLOY,
                preferred_role=AgentRole.DEVELOPER,
                tool_name="deployment_deploy",
                arguments={"project_name": project_name},
                depends_on=[last_step_id],
                requires_confirmation=True,
                confirmation_type="DEPLOY",
            )

        graph.validate_graph()
        return graph

    # -----------------------------------------------------------------------
    # Task Creation & Assignment Validation
    # -----------------------------------------------------------------------

    def _add_task_to_graph(
        self,
        graph: AgentTaskGraph,
        objective: str,
        capability: AgentCapability,
        preferred_role: AgentRole,
        tool_name: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None,
        requires_confirmation: bool = False,
        confirmation_type: Optional[str] = None,
    ) -> AgentTask:
        """Assign role, validate tool/capability, evaluate confirmation gate, and add to graph."""
        # 1. Resolve agent assignment
        assigned_role = preferred_role
        agent_desc = self.registry.get_agent(preferred_role)
        if not agent_desc or not agent_desc.has_capability(capability):
            # Fall back to alternative agent with capability
            alt_agent = self.registry.find_best_agent_for_capability(capability)
            if alt_agent:
                assigned_role = alt_agent.role
            else:
                assigned_role = AgentRole.COORDINATOR

        # 2. Resolve default tool if not specified
        t_name = tool_name
        if not t_name:
            tools = get_tools_for_capability(capability)
            t_name = tools[0] if tools else None

        # 3. Enforce confirmation policy
        is_consequential = requires_confirmation
        if t_name:
            if t_name in ("git_commit", "git_push", "deploy", "deployment_deploy", "delete_file"):
                is_consequential = True
                confirmation_type = confirmation_type or t_name.upper()

        task = AgentTask(
            role=assigned_role,
            objective=objective,
            capability=capability,
            tool_name=t_name,
            arguments=arguments or {},
            dependencies=depends_on or [],
            status=AgentStatus.CREATED,
            requires_confirmation=is_consequential,
            confirmation_type=confirmation_type,
        )

        return graph.add_task(task, depends_on=depends_on)

    # -----------------------------------------------------------------------
    # Heuristic Pattern Matchers
    # -----------------------------------------------------------------------

    def _is_simple_web_search(self, text: str) -> bool:
        return text.startswith(("search for ", "search ", "google ", "find info on ")) and \
               not any(w in text for w in ("create", "build", "deploy", "code", "modify", "then"))

    def _is_simple_app_launch(self, text: str) -> bool:
        return text.startswith(("open app ", "open ", "launch ", "start ")) and \
               any(app in text for app in ("code", "vscode", "vs code", "notepad", "calculator", "terminal", "explorer")) and \
               not any(w in text for w in ("then", "and build", "and test", "and check why"))

    def _is_simple_browser_nav(self, text: str) -> bool:
        return ("open website" in text or "navigate to" in text or "open url" in text or "browse to" in text) and \
               not any(w in text for w in ("then", "create", "build"))

    def _is_simple_health_check(self, text: str) -> bool:
        return any(p in text for p in ("check health", "ping", "is working", "health check", "is alive")) and \
               not any(w in text for w in ("build", "create", "deploy", "then"))

    def _extract_search_query(self, goal: str) -> str:
        for prefix in ("search for ", "search the web for ", "search ", "google "):
            if goal.lower().startswith(prefix):
                return goal[len(prefix):].strip()
        return goal.strip()

    def _extract_app_name(self, goal: str) -> str:
        match = re.search(r'(?:open|launch|start)\s+([a-zA-Z0-9\s_-]+)', goal, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return "notepad"

    def _extract_url(self, goal: str) -> str:
        match = re.search(r'https?://[^\s]+', goal)
        if match:
            return match.group(0)
        return ""

    def _extract_project_name(self, goal: str) -> str:
        match = re.search(r'(?:project|app|demo)\s+(?:called|named)\s+([a-zA-Z0-9_-]+)', goal, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return "project"

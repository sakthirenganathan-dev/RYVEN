"""RYVEN 3.0 — Agent Goal Planner.

Decomposes compound, multi-step natural language goals into ordered,
dependency-linked TaskSteps across capability domains.
"""

from __future__ import annotations

import re
import uuid
from typing import List, Optional, Tuple
from app.agent.capability_router import CapabilityRouter
from app.agent.models import AgentPlan, CapabilityGroup, StepStatus, TaskStep
from app.internet.search_service import SearchService


class AgentPlanner:
    """Intelligent, deterministic goal decomposition planner for RYVEN 3.0."""

    @classmethod
    def is_composite_goal(cls, text: str) -> bool:
        """Detect if an instruction contains multiple sequential or coordinated actions."""
        clean = text.strip()

        # Questions and informational queries are NOT composite goals
        if re.match(r"^(?:what|why|how\s+do|how\s+does|who|where|when|explain|describe|write\s+a\s+poem)\b", clean, re.IGNORECASE):
            if not re.search(r"\b(?:open|build|deploy)\b.+?\band\s+(?:search|test|commit|deploy)\b", clean, re.IGNORECASE):
                return False

        # Coordinating action pattern: connects action verbs or explicit sequences
        # e.g. "open chrome and search...", "open vs code then open...", "build project and test it"
        coordination_pattern = re.compile(
            r"\b(?:and\s+(?:then\s+)?(?:open|launch|start|run|search|navigate|go\s+to|visit|browse|read|build|test|commit|push|deploy|check|inspect|find|modify|create)|and\s+then|then|after\s+that|followed\s+by)\b",
            re.IGNORECASE,
        )
        if coordination_pattern.search(clean):
            return True

        # Check multi-action verb clauses: e.g. "open X in chrome", "open X using vscode"
        if re.search(r"\bopen\s+.+?\s+(?:in|using|with)\s+(?:chrome|browser|vscode|code)\b", clean, re.IGNORECASE):
            return True

        # Check project audit / inspection requests
        if re.search(r"\b(?:inspect|audit)\s+(?:my\s+|the\s+)?project\b", clean, re.IGNORECASE):
            return True

        return False

    def plan(self, user_goal: str) -> AgentPlan:
        """Create a validated, dependency-ordered AgentPlan for the given user goal."""
        capabilities = CapabilityRouter.classify_capabilities(user_goal)
        clean_goal = user_goal.strip()

        steps: List[TaskStep] = []

        # 1. Computer + Browser composite workflows (e.g. "open chrome and search youtube for X")
        browser_plan = self._try_plan_browser_flow(clean_goal)
        if browser_plan:
            steps.extend(browser_plan)

        # 2. Desktop app + project workflow (e.g. "open VS Code and open my project")
        elif self._try_plan_desktop_project_flow(clean_goal):
            steps.extend(self._try_plan_desktop_project_flow(clean_goal))

        # 3. Development + Git + Deploy composite workflow
        elif self._try_plan_dev_composite_flow(clean_goal):
            steps.extend(self._try_plan_dev_composite_flow(clean_goal))

        # 4. Project Inspection & Audit workflow
        elif self._try_plan_project_inspection_flow(clean_goal):
            steps.extend(self._try_plan_project_inspection_flow(clean_goal))

        # 4. Fallback generic single-goal or sequential split
        if not steps:
            steps = self._plan_generic_steps(clean_goal, capabilities)

        plan = AgentPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            goal=user_goal,
            capabilities_required=capabilities,
            steps=steps,
        )
        return plan

    def _try_plan_browser_flow(self, goal: str) -> Optional[List[TaskStep]]:
        """Decompose browser search and navigation commands."""
        steps: List[TaskStep] = []
        order = 1

        # Check for: "open chrome/browser ... and search youtube/google for X"
        # or "search youtube for X" or "open youtube and search X"
        m_yt = re.search(
            r"\b(?:search\s+(?:on\s+)?youtube(?:\s+for)?|youtube(?:\s+and)?\s+search(?:\s+for)?)\s+['\"]?(.+?)['\"]?$",
            goal,
            re.IGNORECASE,
        )
        if not m_yt:
            # Check pattern: "...search youtube (for) X"
            m_yt = re.search(r"\bsearch\s+youtube\s+(?:for\s+)?(.+)$", goal, re.IGNORECASE)

        m_google = re.search(
            r"\b(?:search\s+(?:on\s+)?google(?:\s+for)?|google(?:\s+and)?\s+search(?:\s+for)?)\s+['\"]?(.+?)['\"]?$",
            goal,
            re.IGNORECASE,
        )

        wants_app_open = bool(re.search(r"\b(?:open|launch|start)\s+(?:the\s+)?(?:chrome|google\s+chrome|browser)\b", goal, re.IGNORECASE))

        if m_yt:
            raw_query = m_yt.group(1).strip()
            # Clean up trailing punctuation
            query = re.sub(r"[.?!]+$", "", raw_query).strip()
            yt_url = SearchService.build_youtube_url(query)

            # Step 1: Open Chrome application if requested
            if wants_app_open:
                step_app = TaskStep(
                    step_id=f"step-{order:02d}-open-app",
                    order=order,
                    name="Open Google Chrome",
                    capability=CapabilityGroup.COMPUTER,
                    tool_name="open_application",
                    arguments={"application": "chrome"},
                    description="Launch Google Chrome desktop application",
                    requires_confirmation=False,
                )
                steps.append(step_app)
                order += 1

            # Step 2: Navigate controlled browser to YouTube search results
            step_nav = TaskStep(
                step_id=f"step-{order:02d}-navigate-yt",
                order=order,
                name=f"Navigate YouTube Search for '{query}'",
                capability=CapabilityGroup.BROWSER,
                tool_name="navigate_browser",
                arguments={"url": yt_url},
                description=f"Load YouTube search results for '{query}'",
                dependencies=[steps[-1].step_id] if steps else [],
                requires_confirmation=False,
            )
            steps.append(step_nav)
            order += 1

            # Step 3: Observe page and extract visible results
            step_read = TaskStep(
                step_id=f"step-{order:02d}-observe-results",
                order=order,
                name="Observe Search Results",
                capability=CapabilityGroup.BROWSER,
                tool_name="read_page",
                arguments={},
                description="Extract visible search results text from YouTube page",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_read)
            return steps

        if m_google:
            raw_query = m_google.group(1).strip()
            query = re.sub(r"[.?!]+$", "", raw_query).strip()
            google_url = SearchService.build_google_url(query)

            if wants_app_open:
                step_app = TaskStep(
                    step_id=f"step-{order:02d}-open-app",
                    order=order,
                    name="Open Google Chrome",
                    capability=CapabilityGroup.COMPUTER,
                    tool_name="open_application",
                    arguments={"application": "chrome"},
                    description="Launch Google Chrome desktop application",
                    requires_confirmation=False,
                )
                steps.append(step_app)
                order += 1

            step_nav = TaskStep(
                step_id=f"step-{order:02d}-navigate-google",
                order=order,
                name=f"Search Google for '{query}'",
                capability=CapabilityGroup.BROWSER,
                tool_name="navigate_browser",
                arguments={"url": google_url},
                description=f"Load Google search results for '{query}'",
                dependencies=[steps[-1].step_id] if steps else [],
                requires_confirmation=False,
            )
            steps.append(step_nav)
            order += 1

            step_read = TaskStep(
                step_id=f"step-{order:02d}-observe-google",
                order=order,
                name="Observe Search Results",
                capability=CapabilityGroup.BROWSER,
                tool_name="read_page",
                arguments={},
                description="Extract visible search results from Google page",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_read)
            return steps

        # Check for generic website navigation: "open chrome and go to https://..."
        m_web = re.search(r"\b(?:go\s+to|visit|open|navigate\s+to)\s+(https?://[^\s]+|[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}[^\s]*)", goal, re.IGNORECASE)
        if m_web and wants_app_open:
            dest_url = m_web.group(1).strip()
            step_app = TaskStep(
                step_id=f"step-{order:02d}-open-app",
                order=order,
                name="Open Google Chrome",
                capability=CapabilityGroup.COMPUTER,
                tool_name="open_application",
                arguments={"application": "chrome"},
                description="Launch Google Chrome desktop application",
                requires_confirmation=False,
            )
            steps.append(step_app)
            order += 1

            step_nav = TaskStep(
                step_id=f"step-{order:02d}-navigate-url",
                order=order,
                name=f"Navigate to '{dest_url}'",
                capability=CapabilityGroup.BROWSER,
                tool_name="navigate_browser",
                arguments={"url": dest_url},
                description=f"Navigate controlled browser to '{dest_url}'",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_nav)
            order += 1

            step_snap = TaskStep(
                step_id=f"step-{order:02d}-snapshot",
                order=order,
                name="Capture Browser Snapshot",
                capability=CapabilityGroup.BROWSER,
                tool_name="take_browser_snapshot",
                arguments={},
                description="Capture page snapshot after navigation",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_snap)
            return steps

        # Check for GitHub tasks: "open github and inspect repository issues" / "open github"
        if re.search(r"\bopen\s+github\b", goal, re.IGNORECASE):
            step_nav = TaskStep(
                step_id=f"step-{order:02d}-navigate-github",
                order=order,
                name="Navigate to GitHub",
                capability=CapabilityGroup.INTERNET,
                tool_name="navigate_browser",
                arguments={"url": "https://github.com"},
                description="Navigate to GitHub homepage",
                requires_confirmation=False,
            )
            steps.append(step_nav)
            order += 1

            step_read = TaskStep(
                step_id=f"step-{order:02d}-inspect-github",
                order=order,
                name="Inspect GitHub Page",
                capability=CapabilityGroup.INTERNET,
                tool_name="read_page",
                arguments={},
                description="Extract page content and check for authentication status or repository issues",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_read)
            return steps

        # Check for deployed website inspection: "open my deployed website and inspect the homepage" / "verify the homepage"
        if re.search(r"\bopen\s+(?:my\s+)?(?:deployed\s+)?(?:website|site|app|application)\s+and\s+(?:inspect|verify|check)\b", goal, re.IGNORECASE):
            target_url = "https://my-app.vercel.app"
            step_nav = TaskStep(
                step_id=f"step-{order:02d}-navigate-site",
                order=order,
                name="Navigate to Website",
                capability=CapabilityGroup.INTERNET,
                tool_name="navigate_browser",
                arguments={"url": target_url},
                description=f"Load application homepage at '{target_url}'",
                requires_confirmation=False,
            )
            steps.append(step_nav)
            order += 1

            step_verify = TaskStep(
                step_id=f"step-{order:02d}-verify-page",
                order=order,
                name="Verify Homepage",
                capability=CapabilityGroup.INTERNET,
                tool_name="web_verify",
                arguments={"query": "app"},
                description="Verify that homepage rendered successfully",
                dependencies=[steps[-1].step_id],
                requires_confirmation=False,
            )
            steps.append(step_verify)
            return steps

        # Check for web research: "research FastAPI documentation" / "research a technical topic and summarize it"
        m_research = re.search(r"\bresearch\s+(?:the\s+)?(?:latest\s+)?(.+?)(?:\s+and\s+summarize\s+it)?$", goal, re.IGNORECASE)
        if m_research:
            topic = m_research.group(1).strip()
            topic = re.sub(r"[.?!]+$", "", topic).strip()
            step_res = TaskStep(
                step_id=f"step-{order:02d}-research",
                order=order,
                name=f"Research '{topic}'",
                capability=CapabilityGroup.INTERNET,
                tool_name="web_research",
                arguments={"topic": topic, "max_sources": 3},
                description=f"Perform multi-source web research on '{topic}' and synthesize cited report",
                requires_confirmation=False,
            )
            steps.append(step_res)
            return steps

        return None

    def _try_plan_desktop_project_flow(self, goal: str) -> Optional[List[TaskStep]]:
        """Decompose desktop app opening combined with workspace/file exploration."""
        if not re.search(r"\b(?:vscode|vs\s+code|code)\b", goal, re.IGNORECASE):
            return None

        if not re.search(r"\b(?:and|then)\s+(?:open|show|inspect)\s+(?:my\s+)?(?:project|folder|files?)\b", goal, re.IGNORECASE):
            return None

        steps: List[TaskStep] = [
            TaskStep(
                step_id="step-01-open-vscode",
                order=1,
                name="Launch Visual Studio Code",
                capability=CapabilityGroup.COMPUTER,
                tool_name="open_application",
                arguments={"application": "vscode"},
                description="Open Visual Studio Code editor",
                requires_confirmation=False,
            ),
            TaskStep(
                step_id="step-02-open-workspace",
                order=2,
                name="Open Project Workspace",
                capability=CapabilityGroup.FILES,
                tool_name="open_folder",
                arguments={"folder": "project workspace"},
                description="Open project workspace folder in explorer",
                dependencies=["step-01-open-vscode"],
                requires_confirmation=False,
            ),
        ]
        return steps

    def _try_plan_project_inspection_flow(self, goal: str) -> Optional[List[TaskStep]]:
        """Decompose project inspection and improvement requests into observational steps."""
        if not re.search(r"\b(?:inspect|audit|check|analyze)\s+(?:my\s+|the\s+)?project\b", goal, re.IGNORECASE):
            return None

        steps: List[TaskStep] = [
            TaskStep(
                step_id="step-01-git-status",
                order=1,
                name="Inspect Git Status",
                capability=CapabilityGroup.GIT,
                tool_name="git_status",
                arguments={"project_name": "M11_Test_Git_Project"},
                description="Check local Git repository status and active branch",
                requires_confirmation=False,
            ),
            TaskStep(
                step_id="step-02-health-check",
                order=2,
                name="Check System and Project Health",
                capability=CapabilityGroup.HEALTH,
                tool_name="health_check",
                arguments={"url": "http://127.0.0.1:8000/api/health"},
                description="Run real-time diagnostics on system components",
                dependencies=["step-01-git-status"],
                requires_confirmation=False,
            ),
            TaskStep(
                step_id="step-03-graph-status",
                order=3,
                name="Analyze Knowledge Graph",
                capability=CapabilityGroup.KNOWLEDGE,
                tool_name="graph_status",
                arguments={"project_path": "."},
                description="Evaluate project architecture and Knowledge Graph topology",
                dependencies=["step-02-health-check"],
                requires_confirmation=False,
            ),
        ]
        return steps

    def _try_plan_dev_composite_flow(self, goal: str) -> Optional[List[TaskStep]]:
        """Decompose multi-stage dev pipeline if multiple dev phases are mentioned."""
        goal_lower = goal.lower()
        has_build = "build" in goal_lower
        has_test = "test" in goal_lower
        has_commit = "commit" in goal_lower
        has_deploy = "deploy" in goal_lower

        # At least two dev lifecycle verbs
        dev_signals = sum([has_build, has_test, has_commit, has_deploy])
        if dev_signals < 2:
            return None

        steps: List[TaskStep] = []
        order = 1
        last_id = None

        if has_build:
            s_build = TaskStep(
                step_id=f"step-{order:02d}-build",
                order=order,
                name="Build Project",
                capability=CapabilityGroup.DEVELOPMENT,
                tool_name="build_project",
                arguments={},
                description="Compile and build project artifacts",
                requires_confirmation=False,
            )
            steps.append(s_build)
            last_id = s_build.step_id
            order += 1

        if has_test:
            s_test = TaskStep(
                step_id=f"step-{order:02d}-test",
                order=order,
                name="Run Test Suite",
                capability=CapabilityGroup.DEVELOPMENT,
                tool_name="test_project",
                arguments={},
                description="Execute automated test suite",
                dependencies=[last_id] if last_id else [],
                requires_confirmation=False,
            )
            steps.append(s_test)
            last_id = s_test.step_id
            order += 1

        if has_commit:
            s_commit = TaskStep(
                step_id=f"step-{order:02d}-commit",
                order=order,
                name="Commit Project Changes",
                capability=CapabilityGroup.GIT,
                tool_name="git_commit",
                arguments={"message": "Update project files"},
                description="Commit modified files to local Git repository",
                dependencies=[last_id] if last_id else [],
                requires_confirmation=True,
            )
            steps.append(s_commit)
            last_id = s_commit.step_id
            order += 1

        if has_deploy:
            s_deploy = TaskStep(
                step_id=f"step-{order:02d}-deploy",
                order=order,
                name="Deploy Project",
                capability=CapabilityGroup.DEPLOYMENT,
                tool_name="deployment_deploy",
                arguments={},
                description="Deploy project to hosting platform",
                dependencies=[last_id] if last_id else [],
                requires_confirmation=True,
            )
            steps.append(s_deploy)

        return steps if len(steps) >= 2 else None

    def _plan_generic_steps(self, goal: str, capabilities: List[CapabilityGroup]) -> List[TaskStep]:
        """Construct fallback task steps based on capability."""
        steps: List[TaskStep] = []
        if CapabilityGroup.BROWSER in capabilities:
            steps.append(
                TaskStep(
                    step_id="step-01-browse",
                    order=1,
                    name=f"Browse for '{goal[:40]}'",
                    capability=CapabilityGroup.BROWSER,
                    tool_name="navigate_browser",
                    arguments={"url": goal},
                    description="Navigate browser to target destination",
                    requires_confirmation=False,
                )
            )
        elif CapabilityGroup.DEVELOPMENT in capabilities:
            steps.append(
                TaskStep(
                    step_id="step-01-scan",
                    order=1,
                    name="Scan Project Structure",
                    capability=CapabilityGroup.DEVELOPMENT,
                    tool_name="scan_existing_project",
                    arguments={},
                    description="Analyze workspace project files",
                    requires_confirmation=False,
                )
            )
        else:
            steps.append(
                TaskStep(
                    step_id="step-01-system",
                    order=1,
                    name="Inspect System Status",
                    capability=CapabilityGroup.SYSTEM,
                    tool_name="system_status",
                    arguments={},
                    description="Check system status telemetry",
                    requires_confirmation=False,
                )
            )
        return steps

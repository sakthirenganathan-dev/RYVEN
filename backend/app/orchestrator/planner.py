"""Deterministic Task Planner for RYVEN Milestone 14 Autonomous Development Orchestrator."""

from __future__ import annotations

import re
from typing import List, Optional

from app.orchestrator.dependency import DependencyGraph
from app.orchestrator.models import (
    OrchestrationPlan,
    OrchestrationStep,
    StepType,
)


class TaskPlanner:
    """Plans deterministic, dependency-linked orchestration workflows from natural language goals."""

    # Keywords indicating stages
    COMMIT_KEYWORDS = ["commit", "git commit", "save changes", "version control"]
    PUSH_KEYWORDS = ["push", "git push", "push to github", "push to remote", "push changes"]
    DEPLOY_KEYWORDS = ["deploy", "ship", "publish", "put online", "host", "release"]
    HEALTH_KEYWORDS = ["health", "healthy", "verify health", "monitor", "ping", "reachability", "working"]

    @classmethod
    def extract_project_name(cls, query: str) -> str:
        """Extract referenced project name from query or fallback to default."""
        m = re.search(r"\b(?:in|for|of|project|to\s+my)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?\b", query, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate.lower() not in ("git", "github", "the", "my", "this", "these", "project", "app", "site"):
                return candidate
        # Generic project fallback
        return "portfolio"

    @classmethod
    def extract_commit_message(cls, query: str) -> str:
        """Extract or generate appropriate commit message from query."""
        m = re.search(r"""(?:message|with\s+message|-m)\s*[:=]?\s*['"]([^'"]+)['"]""", query, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Derive from query
        clean = re.sub(r"\b(?:commit|push|deploy|and|test|verify|health|my|the|project|it)\b", "", query, flags=re.IGNORECASE).strip()
        return clean.capitalize() if len(clean) > 3 else "Update project files"

    def plan(self, user_goal: str, project_name_hint: Optional[str] = None) -> OrchestrationPlan:
        """Construct a validated dependency-ordered plan for the given development goal."""
        project_name = project_name_hint or self.extract_project_name(user_goal)
        goal_lower = user_goal.lower()

        # Determine stages requested
        wants_commit = any(k in goal_lower for k in self.COMMIT_KEYWORDS)
        wants_push = any(k in goal_lower for k in self.PUSH_KEYWORDS)
        wants_deploy = any(k in goal_lower for k in self.DEPLOY_KEYWORDS)
        wants_health = any(k in goal_lower for k in self.HEALTH_KEYWORDS) or wants_deploy

        # If user asked for push, commit is required prerequisite
        if wants_push:
            wants_commit = True

        steps: List[OrchestrationStep] = []
        last_step_id: Optional[str] = None

        # 1. Project Understanding & Scanning
        step_scan = OrchestrationStep(
            step_id="step-01-scan",
            name="Scan Project Structure",
            step_type=StepType.SCAN_PROJECT,
            tool_name="scan_existing_project",
            arguments={"project_name": project_name},
            dependencies=[],
            requires_confirmation=False,
        )
        steps.append(step_scan)
        last_step_id = step_scan.step_id

        # 2. Knowledge Graph Context Query
        step_graph = OrchestrationStep(
            step_id="step-02-graph",
            name="Query Knowledge Graph Context",
            step_type=StepType.GRAPH_QUERY,
            tool_name="graph_query",
            arguments={"project_path": project_name, "query": user_goal},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_graph)
        last_step_id = step_graph.step_id

        # 3. Plan Modification
        step_plan_mod = OrchestrationStep(
            step_id="step-03-plan-mod",
            name="Plan Targeted Modification",
            step_type=StepType.PLAN_MODIFICATION,
            tool_name="plan_project_modifications",
            arguments={"project_name": project_name, "request": user_goal},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_plan_mod)
        last_step_id = step_plan_mod.step_id

        # 4. Apply Modification (Protected confirmation boundary)
        step_apply_mod = OrchestrationStep(
            step_id="step-04-apply-mod",
            name="Apply Code Modification",
            step_type=StepType.APPLY_MODIFICATION,
            tool_name="apply_project_modification",
            arguments={"project_name": project_name},
            dependencies=[last_step_id],
            requires_confirmation=True,
            confirmation_reason=f"Applying targeted code modifications to project '{project_name}'.",
        )
        steps.append(step_apply_mod)
        last_step_id = step_apply_mod.step_id

        # 5. Build Project in Sandbox
        step_build = OrchestrationStep(
            step_id="step-05-build",
            name="Build Project",
            step_type=StepType.BUILD,
            tool_name="build_project",
            arguments={"project_name": project_name},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_build)
        last_step_id = step_build.step_id

        # 6. Run Test Suite
        step_test = OrchestrationStep(
            step_id="step-06-test",
            name="Run Automated Tests",
            step_type=StepType.TEST,
            tool_name="test_project",
            arguments={"project_name": project_name},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_test)
        last_step_id = step_test.step_id

        # 7. Quality Gate (Hard progression boundary)
        step_qg = OrchestrationStep(
            step_id="step-07-quality-gate",
            name="Evaluate Quality Gate",
            step_type=StepType.QUALITY_GATE,
            tool_name="quality_gate",
            arguments={"project_name": project_name},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_qg)
        last_step_id = step_qg.step_id

        # 8. Git Operations (if requested)
        if wants_commit:
            step_git_status = OrchestrationStep(
                step_id="step-08-git-status",
                name="Inspect Git Status & Diff",
                step_type=StepType.GIT_DIFF,
                tool_name="git_diff",
                arguments={"project_name": project_name},
                dependencies=[last_step_id],
                requires_confirmation=False,
            )
            steps.append(step_git_status)
            last_step_id = step_git_status.step_id

            commit_msg = self.extract_commit_message(user_goal)
            step_git_commit = OrchestrationStep(
                step_id="step-09-git-commit",
                name="Commit Changes",
                step_type=StepType.GIT_COMMIT,
                tool_name="git_commit",
                arguments={"project_name": project_name, "message": commit_msg},
                dependencies=[last_step_id],
                requires_confirmation=True,
                confirmation_reason=f"Create Git commit with message '{commit_msg}'.",
            )
            steps.append(step_git_commit)
            last_step_id = step_git_commit.step_id

        if wants_push:
            step_git_push = OrchestrationStep(
                step_id="step-10-git-push",
                name="Push to Remote Repository",
                step_type=StepType.GIT_PUSH,
                tool_name="git_push",
                arguments={"project_name": project_name},
                dependencies=[last_step_id],
                requires_confirmation=True,
                confirmation_reason=f"Push committed changes for '{project_name}' to remote repository.",
            )
            steps.append(step_git_push)
            last_step_id = step_git_push.step_id

        # 9. Deployment Operations (if requested)
        if wants_deploy:
            provider = "VERCEL"
            if "railway" in goal_lower:
                provider = "RAILWAY"
            elif "render" in goal_lower:
                provider = "RENDER"

            step_deploy_preview = OrchestrationStep(
                step_id="step-11-deploy-preview",
                name="Generate Deployment Preview",
                step_type=StepType.DEPLOY_PREVIEW,
                tool_name="deployment_preview",
                arguments={"project_name": project_name, "provider": provider},
                dependencies=[last_step_id],
                requires_confirmation=False,
            )
            steps.append(step_deploy_preview)
            last_step_id = step_deploy_preview.step_id

            step_deploy = OrchestrationStep(
                step_id="step-12-deploy",
                name="Deploy to Remote Infrastructure",
                step_type=StepType.DEPLOY,
                tool_name="deployment_deploy",
                arguments={"project_name": project_name, "provider": provider},
                dependencies=[last_step_id],
                requires_confirmation=True,
                confirmation_reason=f"Deploy '{project_name}' to remote {provider} infrastructure.",
            )
            steps.append(step_deploy)
            last_step_id = step_deploy.step_id

            step_deploy_verify = OrchestrationStep(
                step_id="step-13-deploy-verify",
                name="Verify Deployment Endpoint",
                step_type=StepType.DEPLOY_VERIFY,
                tool_name="deployment_verify",
                arguments={"project_name": project_name},
                dependencies=[last_step_id],
                requires_confirmation=False,
            )
            steps.append(step_deploy_verify)
            last_step_id = step_deploy_verify.step_id

        # 10. Health Check (if requested or deployed)
        if wants_health:
            step_health = OrchestrationStep(
                step_id="step-14-health-check",
                name="Perform Endpoint Health Check",
                step_type=StepType.HEALTH_CHECK,
                tool_name="health_check",
                arguments={"project_name": project_name},
                dependencies=[last_step_id],
                requires_confirmation=False,
            )
            steps.append(step_health)
            last_step_id = step_health.step_id

        # 11. Final Report
        step_report = OrchestrationStep(
            step_id="step-15-final-report",
            name="Generate Final Execution Report",
            step_type=StepType.FINAL_REPORT,
            arguments={"project_name": project_name},
            dependencies=[last_step_id],
            requires_confirmation=False,
        )
        steps.append(step_report)

        plan = OrchestrationPlan(
            user_goal=user_goal,
            project_name=project_name,
            steps=steps,
        )

        # Validate DAG
        dag = DependencyGraph(steps)
        is_valid, err = dag.validate_plan()
        plan.valid = is_valid
        plan.validation_error = err if not is_valid else None
        if not is_valid:
            raise ValueError(f"Generated orchestration plan is invalid: {err}")

        return plan

    def create_plan(self, goal: str = "", project_path: str = "", project_name: Optional[str] = None) -> OrchestrationPlan:
        """Alias for plan()."""
        return self.plan(user_goal=goal, project_name_hint=project_name)

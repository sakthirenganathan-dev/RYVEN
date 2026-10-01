"""Workflow planning engine converting user intent into safe, structured workflow definitions."""

import re
from typing import Optional
from app.core.logging_config import logger
from app.tools.registry import ToolRegistry
from app.workflows.models import WorkflowDefinition, WorkflowStep


class WorkflowPlanner:
    """Produces structured, validated workflow plans from supported high-level user goals."""

    # Regex patterns identifying workspace preparation requests
    WORKSPACE_PREP_PATTERNS = [
        r"\b(?:prepare(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:set(?:\s+)?up(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:open(?:\s+my)?(?:\s+(?:development|dev))\s+workspace)\b",
        r"\b(?:initialize(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
    ]

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry
        self._compiled_workspace_prep = [
            re.compile(p, re.IGNORECASE) for p in self.WORKSPACE_PREP_PATTERNS
        ]

    def can_plan(self, query: str) -> bool:
        """Check if query matches a recognized high-level workflow."""
        clean = query.strip()
        # Strip conversational prefix
        clean = re.sub(r"^(?:ryven|jarvis)[,\s:]+", "", clean, flags=re.IGNORECASE).strip()
        return any(pattern.search(clean) for pattern in self._compiled_workspace_prep)

    def plan_workspace_preparation(self, query: str = "", requested_by: str = "user") -> Optional[WorkflowDefinition]:
        """Generate the Workspace Preparation Workflow plan using approved Phase 3 tools."""
        steps = [
            WorkflowStep(
                name="Inspect System Environment",
                tool_name="system_info",
                arguments={},
            ),
            WorkflowStep(
                name="Open Project Workspace",
                tool_name="open_folder",
                arguments={"folder": "workspace"},
            ),
            WorkflowStep(
                name="Launch Visual Studio Code",
                tool_name="open_application",
                arguments={"application": "vscode"},
            ),
        ]

        # Verify all tools exist in registry before generating the plan
        for step in steps:
            if not self.registry.has(step.tool_name):
                logger.error(
                    f"Cannot plan workspace preparation: required tool '{step.tool_name}' is not registered."
                )
                return None

        workflow = WorkflowDefinition(
            name="Prepare Development Workspace",
            description="Inspect system diagnostics, open the approved workspace folder, and launch Visual Studio Code.",
            requested_by=requested_by,
            steps=steps,
        )
        logger.info(f"Planned workflow '{workflow.name}' with {len(steps)} steps.")
        return workflow

    def plan(self, query: str, requested_by: str = "user") -> Optional[WorkflowDefinition]:
        """Convert a user request into a supported workflow plan, if recognized."""
        if not self.can_plan(query):
            return None

        return self.plan_workspace_preparation(query, requested_by=requested_by)

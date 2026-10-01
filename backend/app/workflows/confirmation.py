"""Confirmation manager for evaluating and enforcing user approvals on workflow actions."""

from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.workflows.models import WorkflowStep


class ConfirmationManager:
    """Manages approvals for safe vs. potentially impactful workflow actions."""

    # Set of tool names considered safe for automated sequence execution in Phase 4 v1
    SAFE_AUTO_EXECUTE_TOOLS = {
        "system_info",
        "system_status",
        "time",
        "open_folder",
        "open_application",
        "open_website",
        "search_files",
        "open_file",
        "get_clipboard",
    }

    # Categories requiring explicit user confirmation
    IMPACTFUL_TOOL_CATEGORIES = {
        "create_file",
        "modify_file",
        "delete_file",
        "git_commit",
        "package_install",
        "deploy",
    }

    def requires_confirmation(self, step: WorkflowStep) -> bool:
        """Determine whether a step must pause for user confirmation before executing."""
        if step.requires_confirmation:
            return True

        tool_name = step.tool_name.lower().strip()
        if tool_name in self.SAFE_AUTO_EXECUTE_TOOLS:
            return False

        if tool_name in self.IMPACTFUL_TOOL_CATEGORIES:
            return True

        # Any unfamiliar tool requires confirmation by default
        return True

    def evaluate_step(self, step: WorkflowStep) -> bool:
        """Inspect and tag step if confirmation is needed."""
        needs_confirm = self.requires_confirmation(step)
        step.requires_confirmation = needs_confirm
        if needs_confirm:
            logger.info(f"Workflow step '{step.name}' ({step.tool_name}) flagged for user confirmation.")
        return needs_confirm

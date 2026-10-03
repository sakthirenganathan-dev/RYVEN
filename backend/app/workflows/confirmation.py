"""Confirmation manager for evaluating and enforcing user approvals on workflow actions."""

from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.workflows.models import WorkflowStep


class ConfirmationManager:
    """Manages approvals for safe vs. potentially impactful workflow actions."""

    # Set of tool names considered safe for automated sequence execution
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
        "set_clipboard",
        "validate_project_files",
        "git_status",
        "git_diff",
        "git_branch",
        "git_remote",
        "git_log",
        "git_stage",
        "git_unstage",
        "deployment_detect",
        "deployment_preflight",
        "deployment_preview",
        "deployment_status",
        "deployment_verify",
        # M11.5 Knowledge Graph tools
        "graph_status",
        "graph_build",
        "graph_update",
        "graph_query",
        "graph_find_symbol",
        "graph_find_dependencies",
        "graph_find_dependents",
        "graph_find_callers",
        "graph_explain",
        "graph_path",
        # M13 Health & Verification tools
        "health_check",
        "health_status",
        "health_history",
        "health_monitor_start",
        "health_monitor_stop",
        # M14 Autonomous Development Orchestrator tools
        "orchestrate_task",
        "get_orchestration_status",
        "confirm_orchestration",
        "pause_orchestration",
        "resume_orchestration",
        "cancel_orchestration",
        # M14.3 Controlled Computer & Browser Control tools
        "open_browser",
        "navigate_browser",
        "get_current_page",
        "read_page",
        "find_element",
        "click_element",
        "type_text",
        "press_key",
        "scroll_page",
        "go_back",
        "go_forward",
        "refresh_page",
        "take_browser_snapshot",
        "close_browser",
        # M15 Internet & Web Inspection tools (safe read-only)
        "internet_search",
        "web_research",
        "web_verify",
        "browser_tabs",
        "browser_snapshot",
        "browser_screenshot",
        "page_ocr",
    }

    # Categories requiring explicit user confirmation
    IMPACTFUL_TOOL_CATEGORIES = {
        "create_project_folder",
        "create_project_file",
        "create_file",
        "modify_file",
        "delete_file",
        "git_commit",
        "git_push",
        "package_install",
        "deploy",
        "deployment_deploy",
        "browser_submit_form",
        "browser_download",
        "browser_delete",
    }


    def requires_confirmation(self, step_or_tool: Any, arguments: Optional[Dict[str, Any]] = None) -> bool:
        """Determine whether a step or tool must pause for user confirmation before executing."""
        if hasattr(step_or_tool, "requires_confirmation") and step_or_tool.requires_confirmation:
            return True

        if hasattr(step_or_tool, "tool_name"):
            tool_name = step_or_tool.tool_name.lower().strip()
        elif isinstance(step_or_tool, str):
            tool_name = step_or_tool.lower().strip()
        else:
            return True

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

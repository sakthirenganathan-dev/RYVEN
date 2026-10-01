"""Pre-execution validation engine for RYVEN workflows."""

from typing import Optional, Tuple
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.tools.registry import ToolRegistry
from app.workflows.models import WorkflowDefinition, WorkflowState


class WorkflowValidator:
    """Validates the complete workflow structure and security constraints prior to execution."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guard: Optional[SafetyGuard] = None,
    ) -> None:
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry
        self.safety_guard = guard or safety_guard

    def validate(self, workflow: WorkflowDefinition) -> Tuple[bool, Optional[str]]:
        """Perform comprehensive pre-execution checks on all workflow steps.

        Returns:
            (is_valid, error_reason)
        """
        if not workflow.steps:
            reason = "Workflow contains no steps to execute."
            logger.warning(f"Workflow '{workflow.name}' validation failed: {reason}")
            workflow.status = WorkflowState.FAILED
            workflow.error = reason
            return False, reason

        for idx, step in enumerate(workflow.steps, start=1):
            tool_name = step.tool_name.strip()

            # 1. Verify tool existence in ToolRegistry
            if not self.registry.has(tool_name):
                reason = f"Step {idx} ({step.name}) references unregistered tool: '{tool_name}'"
                logger.warning(f"Workflow validation rejected: {reason}")
                workflow.status = WorkflowState.BLOCKED
                workflow.error = reason
                return False, reason

            # 2. Inspect step arguments for security violations via SafetyGuard
            perm_result = self.safety_guard.validate_action(
                tool_name=tool_name,
                arguments=step.arguments,
                raw_query=f"{workflow.name} {step.name}",
            )

            if not perm_result.allowed:
                reason = (
                    f"Step {idx} ({step.name}) failed security validation: {perm_result.reason}"
                )
                logger.warning(f"Workflow security validation BLOCKED: {reason}")
                workflow.status = WorkflowState.BLOCKED
                workflow.error = reason
                return False, reason

            # 3. Tool-specific argument safety inspection
            tool_instance = self.registry.get(tool_name)
            if tool_name == "open_folder":
                folder_arg = step.arguments.get("folder", "")
                if hasattr(tool_instance, "_resolve_folder_path") and not tool_instance._resolve_folder_path(folder_arg):
                    reason = f"Step {idx} ({step.name}) rejected: unauthorized directory '{folder_arg}'"
                    logger.warning(f"Workflow validation BLOCKED: {reason}")
                    workflow.status = WorkflowState.BLOCKED
                    workflow.error = reason
                    return False, reason

            elif tool_name == "open_application":
                app_arg = step.arguments.get("application") or step.arguments.get("app") or ""
                if hasattr(tool_instance, "_resolve_app") and not tool_instance._resolve_app(app_arg):
                    reason = f"Step {idx} ({step.name}) rejected: unauthorized application '{app_arg}'"
                    logger.warning(f"Workflow validation BLOCKED: {reason}")
                    workflow.status = WorkflowState.BLOCKED
                    workflow.error = reason
                    return False, reason

            elif tool_name == "open_website":
                url_arg = step.arguments.get("url", "")
                if hasattr(tool_instance, "_validate_url") and not tool_instance._validate_url(url_arg):
                    reason = f"Step {idx} ({step.name}) rejected: unsafe URL '{url_arg}'"
                    logger.warning(f"Workflow validation BLOCKED: {reason}")
                    workflow.status = WorkflowState.BLOCKED
                    workflow.error = reason
                    return False, reason

            elif tool_name == "create_project_folder":
                from app.tools.project_tool import sanitize_project_name, resolve_project_path
                p_name = step.arguments.get("project_name", "")
                if not sanitize_project_name(p_name) or not resolve_project_path(p_name):
                    reason = f"Step {idx} ({step.name}) rejected: invalid or escaping project name '{p_name}'"
                    logger.warning(f"Workflow validation BLOCKED: {reason}")
                    workflow.status = WorkflowState.BLOCKED
                    workflow.error = reason
                    return False, reason

            elif tool_name == "create_project_file":
                from app.tools.project_tool import resolve_project_file_path
                p_name = step.arguments.get("project_name", "")
                r_path = step.arguments.get("relative_path", "")
                if not resolve_project_file_path(p_name, r_path):
                    reason = f"Step {idx} ({step.name}) rejected: unsafe file path '{r_path}' for project '{p_name}'"
                    logger.warning(f"Workflow validation BLOCKED: {reason}")
                    workflow.status = WorkflowState.BLOCKED
                    workflow.error = reason
                    return False, reason

        logger.info(f"Workflow '{workflow.name}' ({len(workflow.steps)} steps) successfully validated.")
        return True, None

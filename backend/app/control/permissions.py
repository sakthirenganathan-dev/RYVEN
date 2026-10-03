"""RYVEN 3.0 M17.0 — Centralized Capability Permission Engine.

Authorization Layer for Personal Computer & Internet Control:
- Evaluates actions against PermissionCategory boundaries.
- Safe read-only and reversible operations proceed without redundant prompts.
- Consequential operations strictly enforce ConfirmationManager tokens.
- Permanent prohibition of arbitrary shell, raw terminal commands, or credential exposure.
- Fully preserves and delegates to SafetyGuard and ConfirmationManager.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.control.models import PermissionCategory
from app.core.logging_config import logger
from app.core.permissions import SafetyGuard, safety_guard
from app.workflows.confirmation import ConfirmationManager


# ---------------------------------------------------------------------------
# Consequential Actions and Permanent Prohibitions
# ---------------------------------------------------------------------------

CONSEQUENTIAL_TOOLS: frozenset[str] = frozenset(
    {
        "git_commit",
        "git_push",
        "deployment_deploy",
        "deploy",
        "form_submit",
        "close_application",
        "apply_project_modification",
        "web_download",  # When executable or script
        "delete_file",
    }
)

FORBIDDEN_OPERATIONS: frozenset[str] = frozenset(
    {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "bash",
        "sh",
        "wscript",
        "cscript",
        "reg",
        "regedit",
        "format",
        "rmdir /s",
        "del /f",
        "drop table",
        "purchase",
        "payment",
    }
)
PROHIBITED_SYSTEM_COMMANDS: frozenset[str] = FORBIDDEN_OPERATIONS

SAFE_READ_TOOLS: frozenset[str] = frozenset(
    {
        "time",
        "system_status",
        "system_info",
        "open_folder",
        "search_files",
        "open_file",
        "get_clipboard",
        "scan_existing_project",
        "resolve_existing_project",
        "validate_project_files",
        "validate_modification_plan",
        "plan_project_modifications",
        "quality_gate",
        "git_status",
        "git_diff",
        "git_branch",
        "git_remote",
        "git_log",
        "deployment_detect",
        "deployment_preflight",
        "deployment_preview",
        "deployment_status",
        "deployment_verify",
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
        "health_check",
        "health_status",
        "health_history",
        "get_current_page",
        "read_page",
        "find_element",
        "take_browser_snapshot",
        "internet_search",
        "web_research",
        "web_verify",
        "browser_tabs",
        "browser_snapshot",
        "browser_screenshot",
        "page_ocr",
        "web_verify_download",
        "web_verify_upload",
        "inspect_applications",
        "read_file",
        "web_search",
        "fetch_web_content",
    }
)


# ---------------------------------------------------------------------------
# Tool to Category Mapping
# ---------------------------------------------------------------------------

TOOL_CATEGORY_MAP: Dict[str, PermissionCategory] = {
    # System / Desktop
    "time": PermissionCategory.READ,
    "system_status": PermissionCategory.READ,
    "system_info": PermissionCategory.READ,
    "open_application": PermissionCategory.SYSTEM,
    "focus_application": PermissionCategory.SYSTEM,
    "close_application": PermissionCategory.SYSTEM,
    "inspect_applications": PermissionCategory.SYSTEM,
    "open_website": PermissionCategory.BROWSER,
    "open_folder": PermissionCategory.READ,
    "open_file": PermissionCategory.READ,
    "read_file": PermissionCategory.READ,
    "search_files": PermissionCategory.READ,
    "web_search": PermissionCategory.NETWORK,
    "fetch_web_content": PermissionCategory.READ,
    "get_clipboard": PermissionCategory.READ,
    "set_clipboard": PermissionCategory.WRITE,
    # Projects
    "create_project_folder": PermissionCategory.PROJECT,
    "create_project_file": PermissionCategory.PROJECT,
    "validate_project_files": PermissionCategory.READ,
    "scan_existing_project": PermissionCategory.READ,
    "resolve_existing_project": PermissionCategory.READ,
    "plan_project_modifications": PermissionCategory.READ,
    "validate_modification_plan": PermissionCategory.READ,
    "apply_project_modification": PermissionCategory.WRITE,
    "build_project": PermissionCategory.PROJECT,
    "test_project": PermissionCategory.PROJECT,
    "quality_gate": PermissionCategory.READ,
    # Git
    "git_status": PermissionCategory.READ,
    "git_diff": PermissionCategory.READ,
    "git_branch": PermissionCategory.READ,
    "git_remote": PermissionCategory.READ,
    "git_log": PermissionCategory.READ,
    "git_stage": PermissionCategory.GIT,
    "git_unstage": PermissionCategory.GIT,
    "git_commit": PermissionCategory.GIT,
    "git_push": PermissionCategory.GIT,
    # Deployment
    "deployment_detect": PermissionCategory.READ,
    "deployment_preflight": PermissionCategory.READ,
    "deployment_preview": PermissionCategory.READ,
    "deployment_deploy": PermissionCategory.DEPLOY,
    "deployment_status": PermissionCategory.READ,
    "deployment_verify": PermissionCategory.READ,
    # Health & Knowledge Graph
    "health_check": PermissionCategory.NETWORK,
    "health_status": PermissionCategory.READ,
    "health_history": PermissionCategory.READ,
    "health_monitor_start": PermissionCategory.NETWORK,
    "health_monitor_stop": PermissionCategory.NETWORK,
    "graph_status": PermissionCategory.READ,
    "graph_build": PermissionCategory.PROJECT,
    "graph_update": PermissionCategory.PROJECT,
    "graph_query": PermissionCategory.READ,
    "graph_find_symbol": PermissionCategory.READ,
    "graph_find_dependencies": PermissionCategory.READ,
    "graph_find_dependents": PermissionCategory.READ,
    "graph_find_callers": PermissionCategory.READ,
    "graph_explain": PermissionCategory.READ,
    "graph_path": PermissionCategory.READ,
    # Browser & Internet
    "open_browser": PermissionCategory.BROWSER,
    "navigate_browser": PermissionCategory.BROWSER,
    "get_current_page": PermissionCategory.READ,
    "read_page": PermissionCategory.READ,
    "find_element": PermissionCategory.READ,
    "click_element": PermissionCategory.BROWSER,
    "type_text": PermissionCategory.BROWSER,
    "press_key": PermissionCategory.BROWSER,
    "scroll_page": PermissionCategory.BROWSER,
    "go_back": PermissionCategory.BROWSER,
    "go_forward": PermissionCategory.BROWSER,
    "refresh_page": PermissionCategory.BROWSER,
    "take_browser_snapshot": PermissionCategory.READ,
    "close_browser": PermissionCategory.BROWSER,
    "internet_search": PermissionCategory.NETWORK,
    "web_research": PermissionCategory.NETWORK,
    "web_task": PermissionCategory.BROWSER,
    "web_verify": PermissionCategory.READ,
    "browser_tabs": PermissionCategory.BROWSER,
    "browser_snapshot": PermissionCategory.READ,
    "browser_screenshot": PermissionCategory.READ,
    "page_ocr": PermissionCategory.READ,
    "form_field_fill": PermissionCategory.BROWSER,
    "form_option_select": PermissionCategory.BROWSER,
    "form_checkbox_toggle": PermissionCategory.BROWSER,
    "form_radio_select": PermissionCategory.BROWSER,
    "form_submit": PermissionCategory.BROWSER,
    "web_download": PermissionCategory.NETWORK,
    "web_upload": PermissionCategory.NETWORK,
    "web_verify_download": PermissionCategory.READ,
    "web_verify_upload": PermissionCategory.READ,
}


class PermissionCheckResult(BaseModel):
    """Result of an authorization check."""
    allowed: bool
    category: PermissionCategory = PermissionCategory.EXECUTE_TOOL
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    confirmation_type: Optional[str] = None
    reason: str = ""
    risk_level: str = "low"


CapabilityAuthResult = PermissionCheckResult


class CapabilityPermissionManager:
    """Centralized authorization layer enforcing capability boundaries and confirmation gates."""

    def __init__(
        self,
        guard: Optional[SafetyGuard] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
    ) -> None:
        self.guard = guard or safety_guard
        self.confirmation_mgr = confirmation_mgr or ConfirmationManager()

    def get_category_for_tool(self, tool_name: str) -> PermissionCategory:
        """Resolve PermissionCategory for a tool name."""
        return TOOL_CATEGORY_MAP.get(tool_name.lower(), PermissionCategory.EXECUTE_TOOL)

    def get_permission_catalog(self) -> Dict[str, Any]:
        """Return categorized tool permissions and consequential boundary rules."""
        return {
            "categories": [c.value for c in set(TOOL_CATEGORY_MAP.values())],
            "tool_categories": {k: v.value for k, v in TOOL_CATEGORY_MAP.items()},
            "consequential_tools": sorted(list(CONSEQUENTIAL_TOOLS)),
            "safe_read_tools": sorted(list(SAFE_READ_TOOLS)),
            "prohibited_commands": sorted(list(FORBIDDEN_OPERATIONS)),
        }

    def is_consequential(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Tuple[bool, Optional[str]]:
        """Determine whether an action is consequential and requires explicit user confirmation."""
        clean_name = (tool_name or "").lower()
        args = arguments or {}

        if clean_name in CONSEQUENTIAL_TOOLS:
            conf_type = clean_name.upper()
            if clean_name == "web_download" and args:
                # Only dangerous file extensions require confirmation for download
                filename = str(args.get("filename") or args.get("url") or "")
                if any(filename.lower().endswith(ext) for ext in (".exe", ".bat", ".ps1", ".msi", ".vbs")):
                    return True, "EXECUTABLE_DOWNLOAD"
                return False, None
            return True, conf_type

        # Destructive file mutations
        if clean_name in ("write_file", "delete_file", "apply_project_modification"):
            action = str(args.get("action", "")).lower()
            if action in ("delete", "remove", "truncate") or clean_name == "delete_file":
                return True, "DESTRUCTIVE_FILE_MUTATION"

        return False, None

    def authorize(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        auto_confirm: bool = False,
        confirmed: bool = False,
    ) -> PermissionCheckResult:
        """Evaluate capability authorization for tool execution.

        Flow:
        1. Permanent Prohibition Check (arbitrary shell, raw format, rm -rf).
        2. Resolve PermissionCategory.
        3. Consequential Action & Confirmation Boundary.
        4. Delegate to SafetyGuard for security/path containment/SSRF validation.
        """
        clean_tool = (tool_name or "").strip().lower()
        args = arguments or {}

        # 1. Hard stop on prohibited operations
        if clean_tool in FORBIDDEN_OPERATIONS or any(clean_tool.startswith(p) for p in ("cmd", "powershell", "bash", "sh")):
            return PermissionCheckResult(
                allowed=False,
                category=PermissionCategory.SYSTEM,
                reason=f"Security violation: Unrestricted shell or execution '{tool_name}' is permanently prohibited.",
                risk_level="CRITICAL",
            )

        category = self.get_category_for_tool(clean_tool)

        # 2. Consequential action check
        is_conseq, conf_type = self.is_consequential(clean_tool, args)
        token = None
        if is_conseq:
            token = self.confirmation_mgr.request_confirmation(
                action_name=clean_tool,
                parameters=args,
            )
            if auto_confirm or confirmed:
                self.confirmation_mgr.confirm(token)
            else:
                return PermissionCheckResult(
                    allowed=False,
                    category=category,
                    requires_confirmation=True,
                    confirmation_token=token,
                    confirmation_type=conf_type,
                    reason=f"Consequential action '{clean_tool}' requires explicit user confirmation.",
                    risk_level="high",
                )

        # 3. Delegate to SafetyGuard for path containment, SSRF, injection defense
        guard_res = self.guard.validate_action(
            tool_name=clean_tool,
            arguments=args,
        )
        if not guard_res.allowed:
            return PermissionCheckResult(
                allowed=False,
                category=category,
                reason=guard_res.reason,
                risk_level=guard_res.risk_level,
            )

        # 4. Safe or confirmed action allowed
        return PermissionCheckResult(
            allowed=True,
            category=category,
            requires_confirmation=False,
            confirmation_token=token,
            confirmation_type=conf_type,
            reason="Authorized",
            risk_level="low" if clean_tool in SAFE_READ_TOOLS else "medium",
        )


permission_manager = CapabilityPermissionManager()

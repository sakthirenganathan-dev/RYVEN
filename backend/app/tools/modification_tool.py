"""RYVEN DEV ENGINE — M8: ApplyProjectModificationTool.

This is the ONLY authorized path for writing modifications to an existing project.

Architecture:
  WorkflowExecutor → ToolRegistry → ApplyProjectModificationTool → ApplyEngine → disk

The LLM NEVER directly calls this tool.
All calls flow through the WorkflowEngine confirmation gate.

Security enforced:
  - Confirmation required.
  - Path containment via ApplyEngine → resolve_project_file_path.
  - Original hash verification before every MODIFY write.
  - Protected files rejected.
  - File size limits enforced by ModificationValidator (pre-registered).
  - Rollback on any failure.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.core.logging_config import logger
from app.dev_engine.build_engine import BuildEngine, BuildRequest
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_models import (
    ExistingProjectModel,
    FilePatch,
    ModificationPlan,
    ModificationRequest,
    ModificationResult,
    ModificationStatus,
    PatchOperation,
    RiskLevel,
)
from app.dev_engine.m8_planner import ModificationPlanner
from app.dev_engine.m8_resolver import ProjectResolver
from app.dev_engine.m8_scanner import ProjectScanner
from app.dev_engine.m8_validator import ModificationValidator
from app.dev_engine.quality_gate import QualityGate
from app.dev_engine.test_engine import TestEngine, TestRequest
from app.tools.base import BaseTool
from app.tools.project_tool import resolve_project_file_path, resolve_project_path


class ApplyProjectModificationTool(BaseTool):
    """Applies a validated modification plan to an existing project.

    Required arguments:
      - project_name (str)
      - patches (list of dicts):
          Each dict: {relative_path, operation, proposed_content, reason, original_hash}
      - objective (str): Human-readable description of what is being changed
      - confirmed (bool): Must be True
      - protected_files (list of str, optional)
    """

    name = "apply_project_modification"
    description = (
        "Applies a validated, confirmed set of file modifications to an existing project. "
        "All writes are path-contained, hash-verified, and rolled back on failure. "
        "Requires explicit user confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the existing project to modify",
            },
            "objective": {
                "type": "string",
                "description": "Description of the modification being applied",
            },
            "patches": {
                "type": "array",
                "description": "List of file patch specifications",
                "items": {
                    "type": "object",
                    "properties": {
                        "relative_path": {"type": "string"},
                        "operation": {"type": "string", "enum": ["MODIFY", "CREATE"]},
                        "proposed_content": {"type": "string"},
                        "reason": {"type": "string"},
                        "original_hash": {"type": "string"},
                    },
                    "required": ["relative_path", "operation", "proposed_content"],
                },
            },
            "confirmed": {
                "type": "boolean",
                "description": "Must be true — user has explicitly confirmed this modification",
            },
            "protected_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files that must never be modified (optional)",
            },
        },
        "required": ["project_name", "objective", "patches", "confirmed"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    def __init__(self) -> None:
        self._apply_engine = ApplyEngine()
        self._validator = ModificationValidator()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name: str = kwargs.get("project_name", "")
        objective: str = kwargs.get("objective", "")
        raw_patches: List[dict] = kwargs.get("patches", [])
        confirmed: bool = bool(kwargs.get("confirmed", False))
        protected_files: List[str] = kwargs.get("protected_files", [])

        if not confirmed:
            return {
                "success": False,
                "tool": self.name,
                "message": "Modification blocked: explicit user confirmation is required before applying changes.",
                "security_blocked": True,
            }

        if not project_name:
            return {
                "success": False,
                "tool": self.name,
                "message": "project_name is required.",
            }

        if not raw_patches:
            return {
                "success": False,
                "tool": self.name,
                "message": "No patches provided.",
            }

        # Build FilePatch objects
        patches: List[FilePatch] = []
        for p in raw_patches:
            op_str = (p.get("operation") or "MODIFY").upper()
            try:
                op = PatchOperation(op_str)
            except ValueError:
                return {
                    "success": False,
                    "tool": self.name,
                    "message": f"Unsupported operation '{op_str}'. Only MODIFY and CREATE are allowed.",
                    "security_blocked": True,
                }
            patches.append(FilePatch(
                relative_path=p.get("relative_path", ""),
                operation=op,
                original_hash=p.get("original_hash", ""),
                proposed_content=p.get("proposed_content", ""),
                reason=p.get("reason", ""),
            ))

        # Build plan
        plan = ModificationPlan(
            project_name=project_name,
            objective=objective,
            patches=patches,
            protected_files=[],
            requires_confirmation=False,   # already confirmed
        )

        # Validate plan
        is_valid, err = self._validator.validate(plan, protected_files=protected_files)
        if not is_valid:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Modification validation failed: {err}",
                "security_blocked": True,
            }

        # Apply
        result: ModificationResult = self._apply_engine.apply(plan, confirmed=True)

        return {
            "success": result.success,
            "tool": self.name,
            "message": result.message,
            "status": result.status.value,
            "applied_changes": [c.model_dump() for c in result.applied_changes],
            "was_rolled_back": result.was_rolled_back,
            "errors": result.errors,
            "duration_ms": result.duration_ms,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: ResolveExistingProjectTool
# ─────────────────────────────────────────────────────────────────────────────

class ResolveExistingProjectTool(BaseTool):
    """Safely resolves an existing project name or hint to a verified, canonical project.

    Enforces strict path containment, rejects traversal and system paths.
    Read-only inspection tool.
    """

    name = "resolve_existing_project"
    description = (
        "Safely resolves an existing project name or hint to a verified, canonical project in the approved workspace. "
        "Enforces strict path containment, rejects traversal and system paths. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name_hint": {
                "type": "string",
                "description": "Name or hint of the existing project",
            },
            "user_request": {
                "type": "string",
                "description": "The user request or objective (optional)",
            },
        },
        "required": ["project_name_hint"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self) -> None:
        self._resolver = ProjectResolver()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        hint = kwargs.get("project_name_hint") or kwargs.get("project_name") or ""
        resolved = self._resolver.resolve(hint)
        return {
            "success": resolved.found,
            "tool": self.name,
            "found": resolved.found,
            "project_name": resolved.project_name,
            "absolute_path": resolved.absolute_path,
            "reason": resolved.reason,
            "security_blocked": resolved.security_blocked,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: ScanExistingProjectTool
# ─────────────────────────────────────────────────────────────────────────────

class ScanExistingProjectTool(BaseTool):
    """Safely inspects an existing project structure, metadata, source files, and dependencies.

    Sensitive files (.env, keys) are protected and never read.
    Read-only inspection tool.
    """

    name = "scan_existing_project"
    description = (
        "Safely inspects an existing project structure, metadata, source files, and dependencies without modifying anything. "
        "Sensitive files (.env, keys) are protected and never read. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the project to scan",
            },
            "user_request": {
                "type": "string",
                "description": "The user request (optional)",
            },
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self) -> None:
        self._scanner = ProjectScanner()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        if not project_name:
            return {
                "success": False,
                "tool": self.name,
                "message": "project_name is required.",
            }
        model = self._scanner.scan(project_name)
        if not model:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Project '{project_name}' could not be scanned or was not found.",
            }
        return {
            "success": True,
            "tool": self.name,
            "project_name": model.project_name,
            "root_path": model.root_path,
            "framework": model.framework,
            "language": model.language,
            "project_type": model.project_type.value,
            "package_manager": model.package_manager,
            "entry_points": model.entry_points,
            "config_files": model.config_files,
            "test_files": model.test_files,
            "protected_files": model.protected_files,
            "total_files_scanned": model.total_files_scanned,
            "architecture_summary": model.architecture_summary,
            "source_files": [f.model_dump() for f in model.source_files],
            "relevant_files": model.relevant_files,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: PlanProjectModificationsTool
# ─────────────────────────────────────────────────────────────────────────────

class PlanProjectModificationsTool(BaseTool):
    """Analyzes an existing project and user request to produce a structured modification plan.

    Follows the Minimal Diff Principle. File deletion is permanently blocked.
    Read-only planning tool.
    """

    name = "plan_project_modifications"
    description = (
        "Analyzes an existing project and user request to produce a structured, validated modification plan "
        "with minimal targeted patches. File deletion is permanently blocked. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the project to modify",
            },
            "user_request": {
                "type": "string",
                "description": "Natural language request describing desired modifications",
            },
            "proposed_patches": {
                "type": "array",
                "description": "Optional list of patch specifications",
                "items": {"type": "object"},
            },
        },
        "required": ["project_name", "user_request"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self) -> None:
        self._scanner = ProjectScanner()
        self._planner = ModificationPlanner()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        user_request = kwargs.get("user_request") or ""
        raw_patches = kwargs.get("proposed_patches") or []

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        model = self._scanner.scan(project_name)
        if not model:
            return {"success": False, "tool": self.name, "message": f"Project '{project_name}' not found."}

        req = ModificationRequest(
            project_name=project_name,
            project_path=model.root_path,
            user_request=user_request,
        )

        # If no raw patches provided, generate minimal targeted patches based on user request
        if not raw_patches:
            raw_patches = self._generate_targeted_patches(model, user_request)

        plan = self._planner.plan(model, req, raw_patches)
        if not plan:
            return {
                "success": False,
                "tool": self.name,
                "message": "Failed to generate valid modification plan. Patches were rejected or unsafe.",
            }

        return {
            "success": True,
            "tool": self.name,
            "plan_id": plan.plan_id,
            "project_name": plan.project_name,
            "objective": plan.objective,
            "patches": [p.model_dump() for p in plan.patches],
            "files_to_modify": plan.files_to_modify,
            "files_to_create": plan.files_to_create,
            "risk_level": plan.risk_level.value,
            "requires_confirmation": plan.requires_confirmation,
            "summary": plan.summary,
        }

    def _generate_targeted_patches(self, model: ExistingProjectModel, user_request: str) -> List[dict]:
        """Synthesize minimal, targeted patches based on user request keywords."""
        lower = user_request.lower()
        patches: List[dict] = []
        if "dark mode" in lower or "theme" in lower:
            css_file = next((f.relative_path for f in model.source_files if f.relative_path.endswith(".css")), None)
            if css_file:
                target_path = resolve_project_file_path(model.project_name, css_file)
                existing = ""
                if target_path and os.path.isfile(target_path):
                    with open(target_path, "r", encoding="utf-8") as f:
                        existing = f.read(100_000)
                dark_css = "\n\n/* Dark mode styles added by RYVEN */\n[data-theme='dark'], body.dark {\n  background-color: #0b0f19;\n  color: #f0f6fc;\n}\n"
                patches.append({
                    "relative_path": css_file,
                    "operation": "MODIFY",
                    "proposed_content": existing + dark_css if dark_css not in existing else existing,
                    "reason": "Add dark mode theme variables and styles",
                })
            else:
                patches.append({
                    "relative_path": "theme.css",
                    "operation": "CREATE",
                    "proposed_content": "/* Dark mode theme by RYVEN */\nbody.dark { background-color: #0b0f19; color: #f0f6fc; }\n",
                    "reason": "Add theme styles",
                })
        elif "login" in lower or "auth" in lower:
            patches.append({
                "relative_path": "src/components/Login.tsx" if "react_ts" in model.project_type.value else "login.html",
                "operation": "CREATE",
                "proposed_content": "/* Login component added by RYVEN */\nexport function Login() {\n  return (\n    <div className='login-box'>\n      <h2>Sign In</h2>\n      <input type='text' placeholder='Username' />\n      <input type='password' placeholder='Password' />\n      <button type='submit'>Login</button>\n    </div>\n  );\n}\n",
                "reason": "Add authentication login component",
            })
        elif "readme" in lower or "doc" in lower:
            patches.append({
                "relative_path": "README.md",
                "operation": "CREATE" if "README.md" not in model.source_file_paths else "MODIFY",
                "proposed_content": f"# {model.project_name}\n\nUpdated by RYVEN Existing Project Engine.\nObjective: {user_request}\n",
                "reason": "Update project documentation",
            })
        else:
            target = "README.md"
            patches.append({
                "relative_path": target,
                "operation": "CREATE" if target not in model.source_file_paths else "MODIFY",
                "proposed_content": f"# {model.project_name}\n\nUpdated by RYVEN Existing Project Engine.\nObjective: {user_request}\n",
                "reason": f"Apply requested change: {user_request[:60]}",
            })
        return patches


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: ValidateModificationPlanTool
# ─────────────────────────────────────────────────────────────────────────────

class ValidateModificationPlanTool(BaseTool):
    """Validates a modification plan against security boundaries, syntax rules, and size limits.

    Read-only validation tool.
    """

    name = "validate_modification_plan"
    description = (
        "Validates a modification plan against security boundaries, syntax rules, size limits, "
        "and protected file restrictions before write. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "patches": {"type": "array", "items": {"type": "object"}},
            "protected_files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["project_name", "patches"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self) -> None:
        self._validator = ModificationValidator()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or ""
        raw_patches = kwargs.get("patches") or []
        protected_files = kwargs.get("protected_files") or []

        patches: List[FilePatch] = []
        for p in raw_patches:
            op_str = (p.get("operation") or "MODIFY").upper()
            try:
                op = PatchOperation(op_str)
            except ValueError:
                return {
                    "success": False,
                    "tool": self.name,
                    "valid": False,
                    "error": f"Invalid operation: {op_str}",
                    "security_blocked": True,
                }
            patches.append(FilePatch(
                relative_path=p.get("relative_path", ""),
                operation=op,
                original_hash=p.get("original_hash", ""),
                proposed_content=p.get("proposed_content", ""),
                reason=p.get("reason", ""),
            ))

        plan = ModificationPlan(
            project_name=project_name,
            objective="Validation check",
            patches=patches,
            requires_confirmation=False,
        )

        is_valid, err = self._validator.validate(plan, protected_files=protected_files)
        return {
            "success": is_valid,
            "tool": self.name,
            "valid": is_valid,
            "error": err,
            "security_blocked": not is_valid,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: BuildProjectTool
# ─────────────────────────────────────────────────────────────────────────────

class BuildProjectTool(BaseTool):
    """Builds a project inside the approved sandbox using pre-approved commands per project type.

    Never runs arbitrary commands. Requires confirmation.
    """

    name = "build_project"
    description = (
        "Builds a project inside the approved sandbox using pre-approved commands per project type. "
        "Never runs arbitrary commands. Requires confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "project_type": {"type": "string"},
            "entry_file": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = True

    def __init__(self) -> None:
        self._engine = BuildEngine()
        self._scanner = ProjectScanner()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or ""
        project_type = kwargs.get("project_type") or ""
        raw_confirmed = kwargs.get("confirmed", False)
        if isinstance(raw_confirmed, str):
            confirmed = raw_confirmed.lower() in ("true", "1", "yes")
        else:
            confirmed = bool(raw_confirmed)

        if not confirmed:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "security_blocked": True,
                "message": "Build operation requires prior user confirmation.",
            }

        if not project_type:
            model = self._scanner.scan(project_name)
            if model:
                project_type = model.project_type.value
            else:
                project_type = "python_app"

        req = BuildRequest(
            project_name=project_name,
            project_type=project_type,
            entry_file=kwargs.get("entry_file", "main.py"),
            confirmed=confirmed,
        )
        res = await self._engine.build(req)
        return {
            "success": res.success,
            "tool": self.name,
            "project_name": res.project_name,
            "project_type": res.project_type,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "duration_ms": res.duration_ms,
            "warnings": res.warnings,
            "errors": [e.model_dump() for e in res.errors],
            "security_blocked": res.security_blocked,
            "message": res.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: TestProjectTool
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectTool(BaseTool):
    """Runs tests for a project in the sandbox. Returns structured results or TESTS_NOT_AVAILABLE.

    Never executes arbitrary commands. Requires confirmation.
    """

    __test__ = False

    name = "test_project"
    description = (
        "Runs tests for a project in the sandbox. Returns structured results or TESTS_NOT_AVAILABLE. "
        "Never executes arbitrary commands. Requires confirmation."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "project_type": {"type": "string"},
            "test_path": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = True

    def __init__(self) -> None:
        self._engine = TestEngine()
        self._scanner = ProjectScanner()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or ""
        project_type = kwargs.get("project_type") or ""
        raw_confirmed = kwargs.get("confirmed", False)
        if isinstance(raw_confirmed, str):
            confirmed = raw_confirmed.lower() in ("true", "1", "yes")
        else:
            confirmed = bool(raw_confirmed)

        if not confirmed:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "security_blocked": True,
                "message": "Testing requires explicit user confirmation.",
            }

        model = self._scanner.scan(project_name)
        if not project_type and model:
            project_type = model.project_type.value

        has_tests = False
        if model:
            has_tests = bool(model.test_files)

        if not has_tests:
            return {
                "success": True,
                "tool": self.name,
                "project_name": project_name,
                "project_type": project_type or "unknown",
                "status": "TESTS_NOT_AVAILABLE",
                "tests_total": 0,
                "tests_passed": 0,
                "tests_failed": 0,
                "tests_skipped": 0,
                "failures": [],
                "message": "No test suite found in project. Returning TESTS_NOT_AVAILABLE.",
            }

        req = TestRequest(
            project_name=project_name,
            project_type=project_type or "python_app",
            test_path=kwargs.get("test_path", "."),
            confirmed=confirmed,
        )
        res = await self._engine.run_tests(req)
        return {
            "success": res.success,
            "tool": self.name,
            "project_name": res.project_name,
            "project_type": res.project_type,
            "status": "TESTS_PASSED" if res.success else "TESTS_FAILED",
            "tests_total": res.tests_total,
            "tests_passed": res.tests_passed,
            "tests_failed": res.tests_failed,
            "tests_skipped": res.tests_skipped,
            "failures": [f.model_dump() for f in res.failures],
            "security_blocked": res.security_blocked,
            "message": res.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# M8.5: QualityGateTool
# ─────────────────────────────────────────────────────────────────────────────

class QualityGateTool(BaseTool):
    """Evaluates project quality across modification validation, build status, tests, and security.

    Returns QUALITY_PASS, QUALITY_FAIL, or QUALITY_BLOCKED. Read-only.
    """

    name = "quality_gate"
    description = (
        "Evaluates project quality across modification validation, build status, tests, and security. "
        "Returns QUALITY_PASS, QUALITY_FAIL, or QUALITY_BLOCKED. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "expected_files": {"type": "array", "items": {"type": "string"}},
            "build_success": {"type": "boolean"},
            "test_success": {"type": "boolean"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self) -> None:
        self._gate = QualityGate()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or ""
        expected_files = kwargs.get("expected_files") or []
        build_success = kwargs.get("build_success", None)
        test_success = kwargs.get("test_success", None)

        from app.dev_engine.build_engine import BuildResult
        from app.dev_engine.test_engine import TestResult

        b_res = (
            BuildResult(success=bool(build_success), project_name=project_name, project_type="")
            if build_success is not None
            else None
        )
        t_res = (
            TestResult(success=bool(test_success), project_name=project_name, project_type="")
            if test_success is not None
            else None
        )

        res = self._gate.evaluate(
            project_name=project_name,
            expected_files=expected_files,
            build_result=b_res,
            test_result=t_res,
        )
        state_str = f"QUALITY_{res.state}"
        return {
            "success": res.passed,
            "tool": self.name,
            "state": state_str,
            "passed": res.passed,
            "checks": [c.model_dump() for c in res.checks],
            "critical_errors": [e.model_dump() for e in res.critical_errors],
            "message": res.message,
        }


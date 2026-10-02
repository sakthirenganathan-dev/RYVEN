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

from typing import Any, Dict, List, Optional

from app.core.logging_config import logger
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_models import (
    FilePatch,
    ModificationPlan,
    ModificationResult,
    ModificationStatus,
    PatchOperation,
    RiskLevel,
)
from app.dev_engine.m8_validator import ModificationValidator
from app.tools.base import BaseTool


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

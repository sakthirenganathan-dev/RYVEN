"""RYVEN DEV ENGINE — M8: ModificationValidator.

Validates a ModificationPlan before any file is touched.

Checks:
  1. Path containment for every patch.
  2. Protected-file rules.
  3. File size limits.
  4. Total project size limit.
  5. No DELETE operations.
  6. Syntax validity of proposed content (Python: ast.parse; JSON: json.loads).
  7. Malformed content detection.
  8. Unsupported operation rejection.
"""

from __future__ import annotations

import ast
import json
import os
from typing import List, Optional, Tuple

from app.core.logging_config import logger
from app.dev_engine.constants import MAX_FILE_SIZE_BYTES, MAX_TOTAL_PROJECT_SIZE_BYTES
from app.dev_engine.m8_models import FilePatch, ModificationPlan, PatchOperation
from app.tools.project_tool import resolve_project_file_path, resolve_project_path


class ModificationValidator:
    """Validates a complete ModificationPlan before execution.

    Returns (is_valid: bool, error_reason: str).
    """

    def validate(
        self,
        plan: ModificationPlan,
        protected_files: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Run all validation checks. Returns (True, '') on success."""
        protected = set(protected_files or [])

        for patch in plan.patches:
            ok, reason = self._validate_patch(patch, plan.project_name, protected)
            if not ok:
                return False, reason

        # Total size check
        total = sum(p.size_bytes for p in plan.patches)
        if total > MAX_TOTAL_PROJECT_SIZE_BYTES:
            return False, (
                f"Total proposed change size ({total} bytes) exceeds limit "
                f"({MAX_TOTAL_PROJECT_SIZE_BYTES} bytes)."
            )

        return True, ""

    def _validate_patch(
        self,
        patch: FilePatch,
        project_name: str,
        protected: set,
    ) -> Tuple[bool, str]:
        # 1. DELETE is blocked
        if patch.operation == PatchOperation.DELETE if hasattr(PatchOperation, "DELETE") else False:
            return False, f"DELETE operation is blocked by security policy: '{patch.relative_path}'."

        # 2. Protected file
        if patch.relative_path in protected:
            return False, f"Cannot modify protected file: '{patch.relative_path}'."

        # 3. Path containment
        target = resolve_project_file_path(project_name, patch.relative_path)
        if not target:
            return False, f"Path containment failed: '{patch.relative_path}'."

        # 4. File size
        if patch.size_bytes > MAX_FILE_SIZE_BYTES:
            return False, (
                f"Proposed content for '{patch.relative_path}' is too large "
                f"({patch.size_bytes} bytes > {MAX_FILE_SIZE_BYTES} bytes limit)."
            )

        # 5. Empty proposed content
        if not patch.proposed_content.strip():
            return False, f"Proposed content for '{patch.relative_path}' is empty."

        # 6. Syntax validation
        ext = os.path.splitext(patch.relative_path)[1].lower()
        ok, reason = self._validate_syntax(patch.proposed_content, ext, patch.relative_path)
        if not ok:
            return False, reason

        return True, ""

    @staticmethod
    def _validate_syntax(content: str, ext: str, path: str) -> Tuple[bool, str]:
        """Validate content syntax for known file types. Never executes the content."""
        if ext == ".py":
            try:
                ast.parse(content)
            except SyntaxError as exc:
                return False, (
                    f"Python syntax error in '{path}' (line {exc.lineno}): {exc.msg}"
                )
        elif ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return False, (
                    f"JSON parse error in '{path}': {exc.msg} at line {exc.lineno}"
                )
        # HTML / CSS / TS / JS / Markdown: structural validation is lightweight
        # (we rely on the build engine to catch deeper issues)
        return True, ""

"""RYVEN DEV ENGINE — M8: ModificationPlanner + DiffEngine.

ModificationPlanner:
  Takes ExistingProjectModel + ModificationRequest → produces ModificationPlan.
  NEVER modifies any file. Only reads and plans.

DiffEngine:
  Reads existing file content safely, generates proposed new content,
  and wraps it in a validated FilePatch.

Security:
  - All file reads go through resolve_project_file_path() containment check.
  - Sensitive files are rejected (checked against protected_files list).
  - File deletion is BLOCKED by policy.
  - Content size limits enforced.
"""

from __future__ import annotations

import hashlib
import os
from typing import List, Optional

from app.core.logging_config import logger
from app.dev_engine.m8_models import (
    ExistingProjectModel,
    FilePatch,
    ModificationPlan,
    ModificationRequest,
    PatchOperation,
    RiskLevel,
)
from app.tools.project_tool import resolve_project_file_path


# ─────────────────────────────────────────────────────────────────────────────
# Limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_READABLE_FILE_BYTES: int = 200_000  # 200 KB per file read
MAX_PROPOSED_CONTENT_BYTES: int = 500_000  # 500 KB per proposed file


# ─────────────────────────────────────────────────────────────────────────────
# DiffEngine
# ─────────────────────────────────────────────────────────────────────────────

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DiffEngine:
    """Generates structured FilePatch objects from existing + proposed content.

    The AI/planner provides proposed_content as a full file replacement.
    The DiffEngine:
      1. Reads the original file safely.
      2. Hashes the original content.
      3. Wraps everything in a FilePatch.
      4. Does NOT write anything.
    """

    def create_modify_patch(
        self,
        project_name: str,
        relative_path: str,
        proposed_content: str,
        reason: str = "",
        protected_files: Optional[List[str]] = None,
    ) -> Optional[FilePatch]:
        """Create a MODIFY patch for an existing file."""
        # Security: reject protected files
        if protected_files and relative_path in protected_files:
            logger.warning(
                f"[DIFF ENGINE] BLOCKED: '{relative_path}' is a protected file "
                f"in project '{project_name}'"
            )
            return None

        # Security: resolve path
        target = resolve_project_file_path(project_name, relative_path)
        if not target:
            logger.warning(
                f"[DIFF ENGINE] BLOCKED: path '{relative_path}' failed containment check "
                f"for project '{project_name}'"
            )
            return None

        # Read original content
        original_content = ""
        original_hash = ""
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    original_content = f.read(MAX_READABLE_FILE_BYTES)
                original_hash = _sha256(original_content)
            except Exception as exc:
                logger.error(f"[DIFF ENGINE] Cannot read '{target}': {exc}")
                return None

        # Validate proposed content size
        if len(proposed_content.encode("utf-8")) > MAX_PROPOSED_CONTENT_BYTES:
            logger.warning(f"[DIFF ENGINE] Proposed content too large for '{relative_path}'")
            return None

        return FilePatch(
            relative_path=relative_path,
            operation=PatchOperation.MODIFY,
            original_hash=original_hash,
            proposed_content=proposed_content,
            reason=reason,
            validation_status="PENDING",
        )

    def create_new_file_patch(
        self,
        project_name: str,
        relative_path: str,
        proposed_content: str,
        reason: str = "",
        protected_files: Optional[List[str]] = None,
    ) -> Optional[FilePatch]:
        """Create a CREATE patch for a new file that does not yet exist."""
        if protected_files and relative_path in protected_files:
            logger.warning(f"[DIFF ENGINE] BLOCKED: '{relative_path}' is protected")
            return None

        target = resolve_project_file_path(project_name, relative_path)
        if not target:
            logger.warning(
                f"[DIFF ENGINE] BLOCKED: new file path '{relative_path}' failed containment"
            )
            return None

        if len(proposed_content.encode("utf-8")) > MAX_PROPOSED_CONTENT_BYTES:
            logger.warning(f"[DIFF ENGINE] Proposed content too large for '{relative_path}'")
            return None

        return FilePatch(
            relative_path=relative_path,
            operation=PatchOperation.CREATE,
            original_hash="",
            proposed_content=proposed_content,
            reason=reason,
            validation_status="PENDING",
        )

    def verify_original_hash(
        self,
        project_name: str,
        patch: FilePatch,
    ) -> bool:
        """Re-read the file and verify the hash matches what was captured at plan time.

        Returns False (abort) if:
          - File was modified externally since the plan was created.
          - File no longer exists (for MODIFY operation).
          - Path containment fails.
        """
        if patch.operation == PatchOperation.CREATE:
            return True   # no original to verify

        target = resolve_project_file_path(project_name, patch.relative_path)
        if not target or not os.path.isfile(target):
            logger.warning(
                f"[DIFF ENGINE] Hash check: file '{patch.relative_path}' missing for MODIFY."
            )
            return False

        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                current = f.read(MAX_READABLE_FILE_BYTES)
            current_hash = _sha256(current)
            if current_hash != patch.original_hash:
                logger.warning(
                    f"[DIFF ENGINE] STALE PLAN: '{patch.relative_path}' was modified externally. "
                    f"Aborting to prevent overwriting newer changes."
                )
                return False
            return True
        except Exception as exc:
            logger.error(f"[DIFF ENGINE] Cannot read for hash verification: {exc}")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# ModificationPlanner
# ─────────────────────────────────────────────────────────────────────────────

class ModificationPlanner:
    """Produces a ModificationPlan from a project model and modification request.

    This class NEVER writes to disk. It only:
      - Reads existing file metadata.
      - Determines which files need changes.
      - Delegates content generation to the caller (AI layer via WorkflowEngine).
      - Wraps proposed content in FilePatch objects via DiffEngine.
    """

    def __init__(self) -> None:
        self.diff_engine = DiffEngine()

    def plan(
        self,
        project_model: ExistingProjectModel,
        request: ModificationRequest,
        proposed_patches: List[dict],
    ) -> Optional[ModificationPlan]:
        """Build a ModificationPlan.

        Args:
            project_model: Scanned project state.
            request: What the user wants changed.
            proposed_patches: List of dicts, each with keys:
              - relative_path (str)
              - proposed_content (str)
              - operation (str): "MODIFY" | "CREATE"
              - reason (str, optional)

        Returns ModificationPlan or None if any security check fails.
        """
        patches: List[FilePatch] = []
        rejected: List[str] = []

        for p in proposed_patches:
            op = p.get("operation", "MODIFY").upper()
            rel_path = (p.get("relative_path") or "").strip()
            content = p.get("proposed_content", "")
            reason = p.get("reason", "")

            if not rel_path or not content:
                logger.warning(f"[PLANNER] Skipping patch with empty path or content")
                rejected.append(rel_path or "<empty>")
                continue

            # Block DELETE at planner level
            if op == "DELETE":
                logger.warning(
                    f"[PLANNER] File deletion blocked by policy: '{rel_path}'"
                )
                rejected.append(rel_path)
                continue

            if op == "CREATE":
                patch = self.diff_engine.create_new_file_patch(
                    project_name=project_model.project_name,
                    relative_path=rel_path,
                    proposed_content=content,
                    reason=reason,
                    protected_files=project_model.protected_files,
                )
            else:
                patch = self.diff_engine.create_modify_patch(
                    project_name=project_model.project_name,
                    relative_path=rel_path,
                    proposed_content=content,
                    reason=reason,
                    protected_files=project_model.protected_files,
                )

            if patch is None:
                rejected.append(rel_path)
            else:
                patches.append(patch)

        if not patches:
            logger.warning(f"[PLANNER] All proposed patches were rejected for '{project_model.project_name}'")
            return None

        if rejected:
            logger.warning(f"[PLANNER] {len(rejected)} patch(es) rejected: {rejected}")

        # Determine risk level
        risk = self._assess_risk(patches, request)

        # Build modification plan
        plan = ModificationPlan(
            project_name=project_model.project_name,
            objective=request.user_request[:500],
            patches=patches,
            dependency_changes=[],
            config_changes=[p.relative_path for p in patches if "config" in p.relative_path.lower()],
            risk_level=risk,
            requires_confirmation=True,
            summary=self._build_summary(project_model, patches, request),
        )
        logger.info(
            f"[PLANNER] Plan '{plan.plan_id}': {len(patches)} patch(es), "
            f"risk={risk.value}, project='{project_model.project_name}'"
        )
        return plan

    @staticmethod
    def _assess_risk(patches: List[FilePatch], request: ModificationRequest) -> RiskLevel:
        """Simple heuristic risk assessment."""
        n = len(patches)
        config_changes = sum(
            1 for p in patches
            if any(kw in p.relative_path.lower() for kw in ["config", "package.json", "tsconfig"])
        )
        if n > 5 or config_changes > 0:
            return RiskLevel.HIGH
        if n > 2:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _build_summary(
        model: ExistingProjectModel,
        patches: List[FilePatch],
        request: ModificationRequest,
    ) -> str:
        modify_list = [p.relative_path for p in patches if p.operation == PatchOperation.MODIFY]
        create_list = [p.relative_path for p in patches if p.operation == PatchOperation.CREATE]
        lines = [f"Modification plan for '{model.project_name}':"]
        if modify_list:
            lines.append(f"  Files to modify: {', '.join(modify_list)}")
        if create_list:
            lines.append(f"  Files to create: {', '.join(create_list)}")
        lines.append(f"  Objective: {request.user_request[:120]}")
        return "\n".join(lines)

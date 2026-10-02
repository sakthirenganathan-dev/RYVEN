"""RYVEN DEV ENGINE — M8: Controlled File Apply + Rollback Engine.

ApplyEngine:
  Applies a validated ModificationPlan atomically to the project.
  For each file:
    1. Re-verify original hash (abort if stale).
    2. Capture pre-modification snapshot (RollbackRecord).
    3. Write new content through safe path resolution.
    4. Verify write succeeded.

RollbackEngine:
  Restores all files in a RollbackRecord to their pre-modification state.
  Runs when:
    - Post-apply validation fails.
    - Build fails (and caller requests rollback).
    - User cancels.

Security:
  - Never uses os.system, subprocess, shell=True.
  - All writes confined to projects/<name>/ via resolve_project_file_path().
  - LLM never calls this directly — only WorkflowEngine/WorkflowExecutor does.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from app.core.logging_config import logger
from app.dev_engine.m8_models import (
    AppliedChange,
    FilePatch,
    ModificationPlan,
    ModificationResult,
    ModificationStatus,
    PatchOperation,
    RollbackEntry,
    RollbackRecord,
)
from app.dev_engine.m8_planner import DiffEngine, MAX_READABLE_FILE_BYTES
from app.tools.project_tool import resolve_project_file_path


class ApplyEngine:
    """Applies a validated ModificationPlan atomically.

    Must only be called AFTER:
      1. ModificationValidator.validate() passed.
      2. User has confirmed.
      3. DiffEngine.verify_original_hash() has been called for every MODIFY patch.
    """

    def __init__(self) -> None:
        self.diff_engine = DiffEngine()

    def apply(
        self,
        plan: ModificationPlan,
        confirmed: bool = False,
    ) -> ModificationResult:
        """Apply all patches. Returns ModificationResult with applied changes."""
        start = time.monotonic()

        if not confirmed:
            return ModificationResult(
                project_name=plan.project_name,
                status=ModificationStatus.BLOCKED,
                success=False,
                message="Modification requires explicit user confirmation.",
                errors=["confirmation_required"],
                duration_ms=0.0,
            )

        rollback_record = RollbackRecord(
            project_name=plan.project_name,
            plan_id=plan.plan_id,
        )
        applied: List[AppliedChange] = []
        failed = False

        for patch in plan.patches:
            # Re-verify hash before each write (detect external changes)
            if patch.operation == PatchOperation.MODIFY:
                if not self.diff_engine.verify_original_hash(plan.project_name, patch):
                    logger.warning(
                        f"[APPLY] Aborting: stale plan detected for '{patch.relative_path}'"
                    )
                    # Roll back anything already written
                    self._do_rollback(rollback_record)
                    return ModificationResult(
                        project_name=plan.project_name,
                        status=ModificationStatus.ROLLED_BACK,
                        success=False,
                        message=f"Aborted: '{patch.relative_path}' was modified externally since the plan was created. "
                                f"All previously applied changes have been rolled back.",
                        applied_changes=applied,
                        rollback_available=False,
                        was_rolled_back=True,
                        errors=[f"stale_file:{patch.relative_path}"],
                        duration_ms=(time.monotonic() - start) * 1000,
                    )

            # Capture pre-modification state (for rollback)
            entry = self._capture_rollback_entry(plan.project_name, patch)
            if entry:
                rollback_record.entries.append(entry)

            # Write the file
            change = self._write_patch(plan.project_name, patch)
            applied.append(change)

            if not change.success:
                logger.error(
                    f"[APPLY] Write failed for '{patch.relative_path}': {change.error}. "
                    f"Rolling back."
                )
                self._do_rollback(rollback_record)
                return ModificationResult(
                    project_name=plan.project_name,
                    status=ModificationStatus.ROLLED_BACK,
                    success=False,
                    message=f"Write failed for '{patch.relative_path}'. All changes rolled back.",
                    applied_changes=applied,
                    rollback_available=False,
                    was_rolled_back=True,
                    errors=[change.error],
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        rollback_record.committed = True
        logger.info(
            f"[APPLY] All {len(applied)} patches applied for '{plan.project_name}'."
        )
        return ModificationResult(
            project_name=plan.project_name,
            status=ModificationStatus.COMPLETED,
            success=True,
            message=(
                f"Successfully applied {len(applied)} modification(s) to '{plan.project_name}'."
            ),
            applied_changes=applied,
            rollback_available=True,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _capture_rollback_entry(
        self,
        project_name: str,
        patch: FilePatch,
    ) -> Optional[RollbackEntry]:
        """Read current file content for rollback snapshot."""
        target = resolve_project_file_path(project_name, patch.relative_path)
        if not target:
            return None
        if not os.path.isfile(target):
            return RollbackEntry(
                relative_path=patch.relative_path,
                original_content="",
                original_hash="",
                absolute_path=target,
                existed_before=False,
            )
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(MAX_READABLE_FILE_BYTES)
            return RollbackEntry(
                relative_path=patch.relative_path,
                original_content=content,
                original_hash=patch.original_hash,
                absolute_path=target,
                existed_before=True,
            )
        except Exception as exc:
            logger.error(f"[APPLY] Cannot capture rollback for '{patch.relative_path}': {exc}")
            return None

    @staticmethod
    def _write_patch(project_name: str, patch: FilePatch) -> AppliedChange:
        """Write a single patch to disk. Returns AppliedChange."""
        target = resolve_project_file_path(project_name, patch.relative_path)
        if not target:
            return AppliedChange(
                relative_path=patch.relative_path,
                operation=patch.operation.value,
                success=False,
                error="Path containment check failed — write refused.",
            )
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            encoded = patch.proposed_content.encode("utf-8")
            with open(target, "w", encoding="utf-8") as f:
                f.write(patch.proposed_content)
            logger.info(
                f"[APPLY] Wrote {len(encoded)} bytes → '{patch.relative_path}' "
                f"(op={patch.operation.value})"
            )
            return AppliedChange(
                relative_path=patch.relative_path,
                operation=patch.operation.value,
                bytes_written=len(encoded),
                success=True,
            )
        except Exception as exc:
            logger.error(f"[APPLY] Write error '{patch.relative_path}': {exc}", exc_info=True)
            return AppliedChange(
                relative_path=patch.relative_path,
                operation=patch.operation.value,
                success=False,
                error=str(exc)[:200],
            )

    @staticmethod
    def _do_rollback(record: RollbackRecord) -> None:
        """Restore all captured pre-modification snapshots."""
        for entry in record.entries:
            if not entry.existed_before:
                # File was created — delete it during rollback
                try:
                    if os.path.isfile(entry.absolute_path):
                        os.remove(entry.absolute_path)
                        logger.info(f"[ROLLBACK] Removed newly created file: '{entry.relative_path}'")
                except Exception as exc:
                    logger.error(f"[ROLLBACK] Cannot remove '{entry.relative_path}': {exc}")
            else:
                try:
                    with open(entry.absolute_path, "w", encoding="utf-8") as f:
                        f.write(entry.original_content)
                    logger.info(f"[ROLLBACK] Restored: '{entry.relative_path}'")
                except Exception as exc:
                    logger.error(f"[ROLLBACK] Cannot restore '{entry.relative_path}': {exc}")
        record.rolled_back = True


class RollbackEngine:
    """Standalone rollback engine — can restore from a RollbackRecord after the fact."""

    def rollback(self, record: RollbackRecord) -> bool:
        """Restore all files in the record. Returns True if all restorations succeeded."""
        if record.rolled_back:
            logger.warning(f"[ROLLBACK] Record '{record.record_id}' already rolled back.")
            return True

        success = True
        for entry in record.entries:
            if not entry.existed_before:
                try:
                    if os.path.isfile(entry.absolute_path):
                        os.remove(entry.absolute_path)
                except Exception as exc:
                    logger.error(f"[ROLLBACK] Cannot remove '{entry.relative_path}': {exc}")
                    success = False
            else:
                try:
                    with open(entry.absolute_path, "w", encoding="utf-8") as f:
                        f.write(entry.original_content)
                except Exception as exc:
                    logger.error(f"[ROLLBACK] Cannot restore '{entry.relative_path}': {exc}")
                    success = False

        record.rolled_back = True
        logger.info(f"[ROLLBACK] Completed rollback for '{record.project_name}'. success={success}")
        return success

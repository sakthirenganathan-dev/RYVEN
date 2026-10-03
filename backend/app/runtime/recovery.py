"""RYVEN 3.0 — Crash & Restart Recovery Service (M15.3.9).

Evaluates interrupted tasks on backend startup or on-demand recovery sweeps.
Enforces non-negotiable safety rules:
- Dangerous actions (GIT_COMMIT, GIT_PUSH, DEPLOY, form submission, destructive file ops,
  or any step requiring confirmation) NEVER automatically re-execute after restart.
- Dangerous tasks transition to RECOVERY_REQUIRES_CONFIRMATION.
- Safe read-only or idempotent operations (SCAN_PROJECT, UNDERSTAND_PROJECT, GRAPH_QUERY,
  HEALTH_CHECK, QUALITY_GATE) are marked resumable.
"""

from __future__ import annotations

from typing import List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger
from app.runtime.checkpoint_store import CheckpointStore, checkpoint_store as default_checkpoint_store
from app.runtime.models import PersistedTaskCheckpoint, RecoveryDecision


# Dangerous Step Types and Keywords that MUST NOT auto-resume
DANGEROUS_STEP_TYPES = frozenset(
    {
        "GIT_COMMIT",
        "GIT_PUSH",
        "DEPLOY",
        "DEPLOY_PREVIEW",
        "FORM_SUBMIT",
        "SUBMIT_FORM",
        "WEB_UPLOAD",
        "DELETE_FILE",
        "DROP_TABLE",
        "PAYMENT",
        "PURCHASE",
        "MESSAGE_SEND",
    }
)

SAFE_IDEMPOTENT_STEP_TYPES = frozenset(
    {
        "UNDERSTAND_PROJECT",
        "GRAPH_QUERY",
        "SCAN_PROJECT",
        "PLAN_MODIFICATION",
        "HEALTH_CHECK",
        "QUALITY_GATE",
        "GIT_STATUS",
        "GIT_DIFF",
        "READ_PAGE",
        "INSPECT_PAGE_STATE",
        "FINAL_REPORT",
    }
)


class RuntimeRecoveryService:
    """Evaluates and manages task state restoration after crashes or process restarts."""

    def __init__(
        self,
        store: Optional[CheckpointStore] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
    ) -> None:
        self.store = store or checkpoint_store or default_checkpoint_store

    def evaluate_task(self, checkpoint: PersistedTaskCheckpoint) -> RecoveryDecision:
        """Inspect task checkpoint to determine if it is safe to resume or requires confirmation."""
        # 1. Check version integrity
        if checkpoint.schema_version != CheckpointStore.CURRENT_SCHEMA_VERSION:
            return RecoveryDecision(
                task_id=checkpoint.task_id,
                user_goal=checkpoint.user_goal,
                is_resumable=False,
                requires_confirmation=False,
                safe_to_auto_resume=False,
                dangerous_step_detected=False,
                reason=f"Schema version mismatch ({checkpoint.schema_version} vs current {CheckpointStore.CURRENT_SCHEMA_VERSION}).",
                checkpoint=checkpoint,
            )

        # 2. Inspect pending steps and current step for dangerous operations
        dangerous_step: Optional[str] = None

        # Check current step
        if checkpoint.current_step_id:
            for step in checkpoint.step_details:
                if step.get("step_id") == checkpoint.current_step_id:
                    stype = str(step.get("step_type", "")).upper()
                    if stype in DANGEROUS_STEP_TYPES or step.get("requires_confirmation"):
                        dangerous_step = stype or "CONFIRMATION_REQUIRED_STEP"
                        break

        # Check remaining pending steps
        if not dangerous_step:
            for step in checkpoint.step_details:
                step_id = step.get("step_id")
                if step_id in checkpoint.pending_steps:
                    stype = str(step.get("step_type", "")).upper()
                    if stype in DANGEROUS_STEP_TYPES or step.get("requires_confirmation"):
                        dangerous_step = stype
                        break

        # 3. Decision routing
        if dangerous_step:
            reason = (
                f"Task contains consequential step ({dangerous_step}) or requires explicit confirmation. "
                "Automatic recovery blocked. Explicit user confirmation required to resume."
            )
            return RecoveryDecision(
                task_id=checkpoint.task_id,
                user_goal=checkpoint.user_goal,
                is_resumable=True,
                requires_confirmation=True,
                safe_to_auto_resume=False,
                dangerous_step_detected=True,
                dangerous_step_type=dangerous_step,
                reason=reason,
                checkpoint=checkpoint,
            )

        # 4. Safe / Idempotent task
        return RecoveryDecision(
            task_id=checkpoint.task_id,
            user_goal=checkpoint.user_goal,
            is_resumable=True,
            requires_confirmation=False,
            safe_to_auto_resume=True,
            dangerous_step_detected=False,
            reason="Task contains only safe, idempotent steps. Safe for automated resumption.",
            checkpoint=checkpoint,
        )

    def scan_for_recoverable_tasks(self) -> List[RecoveryDecision]:
        """Scan SQLite checkpoint store for all interrupted tasks and classify recovery paths."""
        try:
            action_bus.emit(
                ActionEvent(
                    action_type=ActionType.RUNTIME_RECOVERY_STARTED,
                    status=ActionStatus.STARTED,
                    title="Recovery Scan Started",
                    description="Scanning persistent checkpoint storage for incomplete tasks.",
                )
            )
        except Exception:
            pass

        incomplete = self.store.list_incomplete_tasks()
        decisions: List[RecoveryDecision] = []

        for chk in incomplete:
            decision = self.evaluate_task(chk)
            decisions.append(decision)

            if decision.requires_confirmation:
                self.store.update_recovery_status(
                    task_id=chk.task_id,
                    status="REQUIRES_CONFIRMATION",
                    current_state="WAITING_CONFIRMATION",
                )
                try:
                    action_bus.emit(
                        ActionEvent(
                            action_type=ActionType.RUNTIME_RECOVERY_REQUIRES_CONFIRMATION,
                            status=ActionStatus.WAITING_CONFIRMATION,
                            title=f"Recovery Requires Confirmation: {chk.task_id}",
                            task_id=chk.task_id,
                            description=decision.reason,
                            confirmation_required=True,
                            safe_metadata={
                                "task_id": chk.task_id,
                                "dangerous_step_type": decision.dangerous_step_type,
                            },
                        )
                    )
                except Exception:
                    pass

        try:
            action_bus.emit(
                ActionEvent(
                    action_type=ActionType.RUNTIME_RECOVERY_COMPLETED,
                    status=ActionStatus.COMPLETED,
                    title="Recovery Scan Completed",
                    description=f"Evaluated {len(decisions)} tasks ({sum(1 for d in decisions if d.requires_confirmation)} require confirmation).",
                    safe_metadata={"evaluated_count": len(decisions)},
                )
            )
        except Exception:
            pass

        return decisions

    def resume_task(self, task_id: str, confirmed: bool = False) -> RecoveryDecision:
        """Mark task as resumed if permitted by safety boundaries."""
        chk = self.store.get_checkpoint(task_id)
        if not chk:
            return RecoveryDecision(
                task_id=task_id,
                is_resumable=False,
                reason=f"Task '{task_id}' not found in checkpoint store.",
            )

        decision = self.evaluate_task(chk)
        if decision.requires_confirmation and not confirmed:
            return decision

        # Confirmed or safe: mark resumed in store
        self.store.update_recovery_status(task_id=task_id, status="RESUMED", current_state="READY")
        decision.is_resumable = True
        decision.requires_confirmation = False
        decision.reason = "Task successfully marked ready for execution resumption."
        return decision


# Global default singleton
runtime_recovery_service = RuntimeRecoveryService()
recovery_service = runtime_recovery_service

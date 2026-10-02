"""RYVEN M14.2 — ActionTracker: lifecycle wrapper for observable RYVEN operations.

The ActionTracker is a thin observability wrapper.
It does NOT contain business logic.
It does NOT execute tools, git commands, or deployments.
It ONLY emits structured ActionEvents into the ActionEventBus.

If event publishing fails for any reason, the failure is logged and
the actual RYVEN operation continues unaffected.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType, _utc_now_iso
from app.core.logging_config import logger


class ActionTracker:
    """Emits structured lifecycle events for RYVEN tool and orchestrator operations.

    All methods are fire-and-forget from a safety perspective:
    exceptions during event bus publishing are suppressed so that
    the underlying tool/orchestration execution is NEVER interrupted.
    """

    def __init__(self, bus=None) -> None:
        # Allow injection for testing; fall back to global singleton
        self._bus = bus or action_bus

    # -----------------------------------------------------------------------
    # Lifecycle helpers
    # -----------------------------------------------------------------------

    def _make_event(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        description: str = "",
        task_id: Optional[str] = None,
        action_id: Optional[str] = None,
        parent_action_id: Optional[str] = None,
        step_index: Optional[int] = None,
        total_steps: Optional[int] = None,
        progress: Optional[float] = None,
        confirmation_required: bool = False,
        confirmation_status: Optional[str] = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        duration_ms: Optional[float] = None,
    ) -> ActionEvent:
        return ActionEvent(
            action_type=action_type,
            status=status,
            title=title,
            description=description,
            task_id=task_id,
            action_id=action_id or str(uuid.uuid4()),
            parent_action_id=parent_action_id,
            step_index=step_index,
            total_steps=total_steps,
            progress=progress,
            confirmation_required=confirmation_required,
            confirmation_status=confirmation_status,
            safe_metadata=safe_metadata or {},
            error_code=error_code,
            started_at=started_at or _utc_now_iso(),
            completed_at=completed_at,
            duration_ms=duration_ms,
        )

    async def _publish(self, event: ActionEvent) -> None:
        """Safely publish an event; never raises."""
        try:
            await self._bus.publish(event)
        except Exception as exc:
            logger.warning(f"[ACTION_TRACKER] Event publish error (non-fatal): {exc}")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    async def tool_started(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> str:
        """Emit TOOL_EXECUTE / STARTED event. Returns the action_id."""
        aid = action_id or str(uuid.uuid4())
        event = self._make_event(
            action_type=ActionType.TOOL_EXECUTE,
            status=ActionStatus.STARTED,
            title=f"Tool: {tool_name}",
            description=f"Executing tool '{tool_name}'",
            task_id=task_id,
            action_id=aid,
            safe_metadata={"tool": tool_name, "arguments": arguments or {}},
        )
        await self._publish(event)
        return aid

    async def tool_completed(
        self,
        tool_name: str,
        action_id: str,
        result_summary: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        task_id: Optional[str] = None,
    ) -> None:
        """Emit TOOL_EXECUTE / COMPLETED event."""
        event = self._make_event(
            action_type=ActionType.TOOL_EXECUTE,
            status=ActionStatus.COMPLETED,
            title=f"Tool: {tool_name}",
            description=f"Tool '{tool_name}' completed successfully",
            task_id=task_id,
            action_id=action_id,
            completed_at=_utc_now_iso(),
            duration_ms=duration_ms,
            safe_metadata={"tool": tool_name, **(result_summary or {})},
        )
        await self._publish(event)

    async def tool_failed(
        self,
        tool_name: str,
        action_id: str,
        error: str,
        duration_ms: Optional[float] = None,
        task_id: Optional[str] = None,
    ) -> None:
        """Emit TOOL_EXECUTE / FAILED event."""
        event = self._make_event(
            action_type=ActionType.TOOL_EXECUTE,
            status=ActionStatus.FAILED,
            title=f"Tool: {tool_name}",
            description=f"Tool '{tool_name}' failed",
            task_id=task_id,
            action_id=action_id,
            completed_at=_utc_now_iso(),
            duration_ms=duration_ms,
            safe_metadata={"tool": tool_name, "error": error[:256]},
            error_code="TOOL_EXECUTION_FAILED",
        )
        await self._publish(event)

    # -----------------------------------------------------------------------
    # Orchestration step events
    # -----------------------------------------------------------------------

    async def step_started(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: Optional[str] = None,
        step_index: Optional[int] = None,
        total_steps: Optional[int] = None,
    ) -> str:
        """Emit orchestration step STARTED event. Returns action_id."""
        aid = action_id or str(uuid.uuid4())
        try:
            atype = ActionType(step_type)
        except ValueError:
            atype = ActionType.UNKNOWN
        event = self._make_event(
            action_type=atype,
            status=ActionStatus.STARTED,
            title=step_name,
            description=f"Starting step: {step_name}",
            task_id=task_id,
            action_id=aid,
            step_index=step_index,
            total_steps=total_steps,
            safe_metadata={"step_type": step_type},
        )
        await self._publish(event)
        return aid

    async def step_completed(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: str,
        duration_ms: float,
        result_summary: Optional[Dict[str, Any]] = None,
        step_index: Optional[int] = None,
        total_steps: Optional[int] = None,
    ) -> None:
        """Emit orchestration step COMPLETED event."""
        try:
            atype = ActionType(step_type)
        except ValueError:
            atype = ActionType.UNKNOWN
        event = self._make_event(
            action_type=atype,
            status=ActionStatus.COMPLETED,
            title=step_name,
            description=f"Step completed: {step_name}",
            task_id=task_id,
            action_id=action_id,
            completed_at=_utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            step_index=step_index,
            total_steps=total_steps,
            safe_metadata={"step_type": step_type, **(result_summary or {})},
        )
        await self._publish(event)

    async def step_failed(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: str,
        error: str,
        duration_ms: float,
        retry_count: int = 0,
        step_index: Optional[int] = None,
        total_steps: Optional[int] = None,
    ) -> None:
        """Emit orchestration step FAILED event."""
        try:
            atype = ActionType(step_type)
        except ValueError:
            atype = ActionType.UNKNOWN
        event = self._make_event(
            action_type=atype,
            status=ActionStatus.FAILED,
            title=step_name,
            description=f"Step failed: {step_name}",
            task_id=task_id,
            action_id=action_id,
            completed_at=_utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            step_index=step_index,
            total_steps=total_steps,
            safe_metadata={"step_type": step_type, "error": error[:256], "retry_count": retry_count},
            error_code="STEP_EXECUTION_FAILED",
        )
        await self._publish(event)

    async def step_retrying(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: str,
        retry_count: int,
        max_retries: int,
    ) -> None:
        """Emit RETRY / PROGRESS event."""
        try:
            atype = ActionType(step_type)
        except ValueError:
            atype = ActionType.RETRY
        event = self._make_event(
            action_type=ActionType.RETRY,
            status=ActionStatus.PROGRESS,
            title=f"Retrying: {step_name}",
            description=f"Retry attempt {retry_count}/{max_retries} for step '{step_name}'",
            task_id=task_id,
            action_id=action_id,
            safe_metadata={"step_type": step_type, "retry_count": retry_count, "max_retries": max_retries},
        )
        await self._publish(event)

    async def confirmation_requested(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: str,
        reason: str = "",
    ) -> None:
        """Emit WAITING_CONFIRMATION event (never auto-approves)."""
        event = self._make_event(
            action_type=ActionType.CONFIRMATION_REQUESTED,
            status=ActionStatus.WAITING_CONFIRMATION,
            title=f"Awaiting confirmation: {step_name}",
            description=reason or f"User confirmation required for {step_name}",
            task_id=task_id,
            action_id=action_id,
            confirmation_required=True,
            confirmation_status="PENDING",
            safe_metadata={"step_type": step_type, "reason": reason[:256]},
        )
        await self._publish(event)

    async def confirmation_received(
        self,
        task_id: str,
        step_type: str,
        step_name: str,
        action_id: str,
        confirmed: bool,
    ) -> None:
        """Emit CONFIRMATION_RECEIVED event with actual user decision."""
        status = ActionStatus.PROGRESS if confirmed else ActionStatus.CANCELLED
        event = self._make_event(
            action_type=ActionType.CONFIRMATION_RECEIVED,
            status=status,
            title=f"Confirmation {'approved' if confirmed else 'denied'}: {step_name}",
            description=f"User {'approved' if confirmed else 'denied'} {step_name}",
            task_id=task_id,
            action_id=action_id,
            confirmation_required=True,
            confirmation_status="APPROVED" if confirmed else "DENIED",
            safe_metadata={"step_type": step_type, "confirmed": confirmed},
        )
        await self._publish(event)

    async def task_started(
        self,
        task_id: str,
        goal: str,
        project_name: str = "",
        step_count: int = 0,
    ) -> None:
        """Emit TASK_STARTED event."""
        event = self._make_event(
            action_type=ActionType.TASK_STARTED,
            status=ActionStatus.STARTED,
            title=f"Orchestration started: {goal[:80]}",
            description=f"Task initiated for goal: {goal}",
            task_id=task_id,
            total_steps=step_count,
            safe_metadata={"goal": goal[:256], "project_name": project_name, "step_count": step_count},
        )
        await self._publish(event)

    async def task_planning(self, task_id: str, goal: str, step_count: int) -> None:
        """Emit TASK_PLANNING / PROGRESS event."""
        event = self._make_event(
            action_type=ActionType.TASK_PLANNING,
            status=ActionStatus.PROGRESS,
            title="Planning task execution",
            description=f"Generated {step_count}-step execution plan",
            task_id=task_id,
            total_steps=step_count,
            safe_metadata={"step_count": step_count, "goal": goal[:256]},
        )
        await self._publish(event)

    async def task_completed(
        self,
        task_id: str,
        success: bool,
        duration_ms: float,
        steps_completed: int,
    ) -> None:
        """Emit TASK_COMPLETED or TASK_FAILED event."""
        atype = ActionType.TASK_COMPLETED if success else ActionType.TASK_FAILED
        status = ActionStatus.COMPLETED if success else ActionStatus.FAILED
        event = self._make_event(
            action_type=atype,
            status=status,
            title="Task completed" if success else "Task failed",
            description=f"{'Successfully completed' if success else 'Failed after'} {steps_completed} step(s)",
            task_id=task_id,
            completed_at=_utc_now_iso(),
            duration_ms=round(duration_ms, 2),
            safe_metadata={
                "success": success,
                "steps_completed": steps_completed,
                "duration_ms": round(duration_ms, 2),
            },
        )
        await self._publish(event)

    async def task_paused(self, task_id: str, reason: str = "") -> None:
        """Emit TASK_PAUSED event."""
        event = self._make_event(
            action_type=ActionType.TASK_PAUSED,
            status=ActionStatus.WAITING_CONFIRMATION,
            title="Task paused",
            description=reason or "Task execution paused",
            task_id=task_id,
            safe_metadata={"reason": reason[:256]},
        )
        await self._publish(event)

    async def task_cancelled(self, task_id: str) -> None:
        """Emit TASK_CANCELLED event."""
        event = self._make_event(
            action_type=ActionType.TASK_CANCELLED,
            status=ActionStatus.CANCELLED,
            title="Task cancelled",
            description="Task execution was cancelled",
            task_id=task_id,
            completed_at=_utc_now_iso(),
        )
        await self._publish(event)

    async def task_resumed(self, task_id: str) -> None:
        """Emit TASK_RESUMED event."""
        event = self._make_event(
            action_type=ActionType.TASK_RESUMED,
            status=ActionStatus.PROGRESS,
            title="Task resumed",
            description="Task execution resumed",
            task_id=task_id,
        )
        await self._publish(event)


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

action_tracker = ActionTracker()

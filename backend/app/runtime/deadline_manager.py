"""RYVEN 3.0 — Deadline & Timeout Management Service (M15.3.9).

Provides deadline propagation across workflow steps and deterministic timeout cancellation.
Emits RUNTIME_TIMEOUT ActionEvents and prevents tasks from hanging in ambiguous states.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger


T = TypeVar("T")

DEFAULT_STEP_TIMEOUT_SECONDS = 60.0
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_TASK_DEADLINE_SECONDS = 600.0  # 10 minutes


class DeadlineManager:
    """Manages hierarchical timeouts and deadline propagation."""

    def __init__(self, default_task_deadline_seconds: float = DEFAULT_TASK_DEADLINE_SECONDS) -> None:
        self.default_task_deadline_seconds = default_task_deadline_seconds
        # task_id -> (start_monotonic, deadline_monotonic)
        self._task_deadlines: Dict[str, Tuple[float, float]] = {}

    def register_task_deadline(self, task_id: str, deadline_seconds: Optional[float] = None) -> float:
        """Register the start and maximum deadline for an overall task."""
        seconds = deadline_seconds or self.default_task_deadline_seconds
        now = time.monotonic()
        deadline = now + seconds
        self._task_deadlines[task_id] = (now, deadline)
        return seconds

    def get_remaining_task_time(self, task_id: str) -> Optional[float]:
        """Return remaining seconds before task deadline expires."""
        if task_id not in self._task_deadlines:
            return None
        _, deadline = self._task_deadlines[task_id]
        remaining = deadline - time.monotonic()
        return max(0.0, remaining)

    def calculate_effective_timeout(
        self,
        task_id: Optional[str] = None,
        requested_timeout_seconds: Optional[float] = None,
        default_seconds: float = DEFAULT_STEP_TIMEOUT_SECONDS,
    ) -> float:
        """Propagate deadline: clamp requested timeout to remaining task deadline."""
        base_timeout = requested_timeout_seconds if requested_timeout_seconds is not None else default_seconds

        if task_id and task_id in self._task_deadlines:
            remaining = self.get_remaining_task_time(task_id)
            if remaining is not None:
                # Clamp timeout to remaining task deadline
                return max(0.1, min(base_timeout, remaining))

        return max(0.1, base_timeout)

    async def execute_with_timeout(
        self,
        coro: Awaitable[T],
        timeout_seconds: Optional[float] = None,
        task_id: Optional[str] = None,
        operation_name: str = "operation",
        default_timeout: float = DEFAULT_STEP_TIMEOUT_SECONDS,
    ) -> Tuple[bool, Optional[T], Optional[str]]:
        """Execute a coroutine within an enforced timeout.
        
        Returns:
            (success, result, error_message)
        """
        effective_timeout = self.calculate_effective_timeout(
            task_id=task_id,
            requested_timeout_seconds=timeout_seconds,
            default_seconds=default_timeout,
        )

        try:
            result = await asyncio.wait_for(coro, timeout=effective_timeout)
            return True, result, None
        except asyncio.TimeoutError:
            err = f"Operation '{operation_name}' timed out after {effective_timeout:.2f}s."
            logger.warning(f"[DEADLINE_MANAGER] {err}")
            self._emit_timeout_event(task_id, operation_name, effective_timeout)
            return False, None, err
        except Exception as exc:
            return False, None, str(exc)

    def clear_task(self, task_id: str) -> None:
        """Remove task deadline tracking upon completion."""
        self._task_deadlines.pop(task_id, None)

    def _emit_timeout_event(self, task_id: Optional[str], operation_name: str, timeout_seconds: float) -> None:
        """Emit RUNTIME_TIMEOUT ActionEvent."""
        try:
            event = ActionEvent(
                action_type=ActionType.RUNTIME_TIMEOUT,
                status=ActionStatus.FAILED,
                task_id=task_id,
                title=f"Timeout: {operation_name}",
                description=f"Exceeded timeout limit of {timeout_seconds:.2f}s.",
                safe_metadata={
                    "operation_name": operation_name,
                    "timeout_seconds": timeout_seconds,
                    "task_id": task_id,
                },
            )
            action_bus.emit(event)
        except Exception as exc:
            logger.debug(f"[DEADLINE_MANAGER] Failed to emit timeout event: {exc}")


# Global default singleton
deadline_manager = DeadlineManager()

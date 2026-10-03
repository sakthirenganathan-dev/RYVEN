"""RYVEN M14.2 — Bounded async in-process Action Event Bus.

Design principles:
- Pure in-memory, no database, no Redis, no external broker.
- Bounded deque prevents unbounded memory growth (max 1000 events globally).
- Event delivery NEVER blocks tool execution.
- Failed delivery or subscriber errors do not crash RYVEN operations.
- Disconnect-safe: dead subscribers are cleaned up automatically.
"""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from typing import Any, Callable, Coroutine, Dict, List, Optional

from app.actions.models import ActionEvent, ActionStatus, TaskSummary, _utc_now_iso
from app.core.logging_config import logger

# Maximum number of events retained globally in the ring buffer.
_DEFAULT_MAX_EVENTS = 1000

# Subscriber type: an async callable that receives an ActionEvent.
SubscriberFn = Callable[[ActionEvent], Coroutine[Any, Any, None]]


class ActionEventBus:
    """Lightweight, bounded, async in-process event bus for action observability.

    Usage:
        bus = ActionEventBus()

        # Subscribe
        async def on_event(event: ActionEvent):
            print(event.title, event.status)

        subscriber_id = bus.subscribe(on_event)

        # Publish
        await bus.publish(ActionEvent(title="Running tool", status=ActionStatus.STARTED))

        # Unsubscribe
        bus.unsubscribe(subscriber_id)
    """

    def __init__(self, max_events: int = _DEFAULT_MAX_EVENTS) -> None:
        self._max_events = max_events
        # Global bounded ring buffer — oldest events are dropped when full.
        self._history: deque[ActionEvent] = deque(maxlen=max_events)
        # Active subscribers keyed by unique ID.
        self._subscribers: Dict[str, SubscriberFn] = {}
        self._counter = 0

    # -----------------------------------------------------------------------
    # Publishing
    # -----------------------------------------------------------------------

    async def publish(self, event: ActionEvent) -> None:
        """Publish a single ActionEvent to all active subscribers.

        This method is safe to call from any async context.
        Subscriber failures are caught and logged — they NEVER bubble up.
        """
        self._history.append(event)
        logger.debug(
            f"[ACTION_BUS] {event.action_type.value} · {event.status.value} · {event.title!r}"
        )

        dead: List[str] = []
        for sid, fn in list(self._subscribers.items()):
            try:
                await fn(event)
            except Exception as exc:
                # Dead or erroring subscribers are marked for removal.
                logger.warning(f"[ACTION_BUS] Subscriber '{sid}' error (removing): {exc}")
                dead.append(sid)

        for sid in dead:
            self._subscribers.pop(sid, None)

    def emit(self, event: ActionEvent) -> None:
        """Synchronously enqueue and record event in history, broadcasting to subscribers if loop running."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            self._history.append(event)

    # -----------------------------------------------------------------------
    # Subscription management
    # -----------------------------------------------------------------------

    def subscribe(self, fn: Callable) -> str:
        """Register a subscriber callback (sync or async).

        Returns a subscriber ID that can be used to unsubscribe.
        Sync callables are automatically wrapped to satisfy the async interface.
        """
        self._counter += 1
        sid = f"sub-{self._counter}"
        if inspect.iscoroutinefunction(fn):
            self._subscribers[sid] = fn
        else:
            # Wrap sync callable so it satisfies the async subscriber interface
            async def _sync_wrapper(evt: "ActionEvent", _fn: Callable = fn) -> None:
                _fn(evt)
            self._subscribers[sid] = _sync_wrapper
        logger.debug(f"[ACTION_BUS] New subscriber: {sid} (total={len(self._subscribers)})")
        return sid

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Unregister a subscriber by ID. Returns True if removed."""
        removed = self._subscribers.pop(subscriber_id, None) is not None
        if removed:
            logger.debug(f"[ACTION_BUS] Removed subscriber: {subscriber_id}")
        return removed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def get_recent_events(self, limit: int = 50) -> List[ActionEvent]:
        """Return the most recent N events (newest last)."""
        events = list(self._history)
        return events[-limit:]

    def get_task_events(self, task_id: str) -> List[ActionEvent]:
        """Return all events for a specific task_id."""
        return [e for e in self._history if e.task_id == task_id]

    def get_all_events(self) -> List[ActionEvent]:
        """Return all events in the ring buffer (oldest first)."""
        return list(self._history)

    def clear(self) -> None:
        """Clear all event history (intended for tests only)."""
        self._history.clear()

    @property
    def event_count(self) -> int:
        return len(self._history)

    # -----------------------------------------------------------------------
    # Task Summary Generation
    # -----------------------------------------------------------------------

    def build_task_summary(self, task_id: str, goal: str = "") -> TaskSummary:
        """Build a structured TaskSummary from stored events for a task.

        All values come from actual events — nothing is fabricated.
        """
        events = self.get_task_events(task_id)
        if not events:
            return TaskSummary(
                task_id=task_id,
                goal=goal,
                status=ActionStatus.PENDING,
            )

        total = len(events)
        successful = sum(1 for e in events if e.status == ActionStatus.COMPLETED)
        failed = sum(1 for e in events if e.status == ActionStatus.FAILED)
        cancelled = sum(1 for e in events if e.status == ActionStatus.CANCELLED)
        confirmations = sum(1 for e in events if e.confirmation_required)

        # Infer overall status from events
        statuses = {e.status for e in events}
        if ActionStatus.FAILED in statuses:
            overall = ActionStatus.FAILED
        elif ActionStatus.CANCELLED in statuses:
            overall = ActionStatus.CANCELLED
        elif ActionStatus.WAITING_CONFIRMATION in statuses:
            overall = ActionStatus.WAITING_CONFIRMATION
        elif all(e.status == ActionStatus.COMPLETED for e in events if e.action_type.value not in ("TASK_STARTED",)):
            overall = ActionStatus.COMPLETED
        else:
            overall = ActionStatus.PROGRESS

        started = events[0].started_at
        last_completed = next(
            (e.completed_at for e in reversed(events) if e.completed_at),
            None,
        )

        # Extract actual results from safe_metadata — never fabricate
        build_res = next(
            (e.safe_metadata.get("build_result") for e in events
             if e.action_type == ActionStatus.COMPLETED and "build_result" in e.safe_metadata),
            None,
        )
        test_res = next(
            (e.safe_metadata.get("test_result") for e in events
             if "test_result" in e.safe_metadata),
            None,
        )
        qg_res = next(
            (e.safe_metadata.get("quality_gate_result") for e in events
             if "quality_gate_result" in e.safe_metadata),
            None,
        )
        deploy_res = next(
            (e.safe_metadata.get("deployment_result") for e in events
             if "deployment_result" in e.safe_metadata),
            None,
        )
        health_res = next(
            (e.safe_metadata.get("health_result") for e in events
             if "health_result" in e.safe_metadata),
            None,
        )

        return TaskSummary(
            task_id=task_id,
            goal=goal,
            status=overall,
            started_at=started,
            completed_at=last_completed,
            total_actions=total,
            successful_actions=successful,
            failed_actions=failed,
            cancelled_actions=cancelled,
            confirmations_required=confirmations,
            build_result=str(build_res) if build_res is not None else None,
            test_result=str(test_res) if test_res is not None else None,
            quality_gate_result=str(qg_res) if qg_res is not None else None,
            deployment_result=str(deploy_res) if deploy_res is not None else None,
            health_result=str(health_res) if health_res is not None else None,
        )


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

action_bus = ActionEventBus(max_events=_DEFAULT_MAX_EVENTS)

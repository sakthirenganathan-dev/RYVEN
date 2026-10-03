"""RYVEN 3.0 — Runtime Performance Profiler & Metrics Service (M15.3.9).

Provides lightweight, bounded in-memory tracking of operation latencies, success/failure rates,
and percentile distributions across LLM inference, tool execution, browser actions, and workflows.

Zero secrets, prompts, tokens, cookies, or sensitive payloads are ever stored.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import time
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger
from app.runtime.models import AggregatePerformanceMetrics, OperationMetric, utc_now_iso


_DEFAULT_MAX_METRICS = 1000


class RuntimePerformanceService:
    """Lightweight, bounded in-memory performance profiling service."""

    def __init__(self, max_metrics: int = _DEFAULT_MAX_METRICS) -> None:
        self._max_metrics = max_metrics
        self._metrics: Deque[OperationMetric] = deque(maxlen=max_metrics)
        self._lock = asyncio.Lock()
        self._subscribed = False

    def start_event_listener(self) -> None:
        """Subscribe to ActionEventBus to passively ingest operation metrics."""
        if self._subscribed:
            return
        try:
            action_bus.subscribe(self._on_action_event)
            self._subscribed = True
            logger.info("[PERFORMANCE] Performance profiler subscribed to ActionEventBus.")
        except Exception as exc:
            logger.warning(f"[PERFORMANCE] Failed to subscribe to ActionEventBus: {exc}")

    async def _on_action_event(self, event: ActionEvent) -> None:
        """Process ActionEvent to extract duration telemetry without logging secrets."""
        # Only record completed or failed events with valid duration
        if event.duration_ms is None or event.duration_ms <= 0:
            return

        category = "general"
        ev_type = event.action_type.value

        if "MODEL" in ev_type or "INFERENCE" in ev_type or "VISION" in ev_type:
            category = "llm"
        elif "TOOL" in ev_type:
            category = "tool"
        elif "WEB" in ev_type or "BROWSER" in ev_type or "PAGE" in ev_type or "DOWNLOAD" in ev_type or "UPLOAD" in ev_type:
            category = "browser"
        elif "TASK" in ev_type or "STEP" in ev_type or "BUILD" in ev_type or "TEST" in ev_type:
            category = "workflow"

        success = event.status not in (ActionStatus.FAILED, ActionStatus.CANCELLED)
        error_msg = event.error_code or (event.description if not success else None)

        # Extract non-sensitive metadata only
        meta = getattr(event, "safe_metadata", None) or getattr(event, "metadata", None) or {}
        provider = meta.get("provider") if isinstance(meta.get("provider"), str) else None
        model = meta.get("model") or meta.get("selected_model") if isinstance(meta.get("model") or meta.get("selected_model"), str) else None
        tool_name = meta.get("tool_name") if isinstance(meta.get("tool_name"), str) else None

        metric = OperationMetric(
            task_id=event.task_id,
            action_id=event.action_id,
            operation_name=event.title or ev_type,
            category=category,
            started_at=event.started_at or utc_now_iso(),
            completed_at=event.completed_at or utc_now_iso(),
            duration_ms=round(event.duration_ms, 2),
            success=success,
            error=error_msg[:120] if error_msg else None,
            provider=provider,
            model=model,
            tool_name=tool_name,
        )

        async with self._lock:
            self._metrics.append(metric)

    async def record_operation(
        self,
        name: str,
        category: str = "general",
        duration_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        task_id: Optional[str] = None,
        action_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tool_name: Optional[str] = None,
        retry_count: int = 0,
    ) -> OperationMetric:
        """Explicitly record an operation duration."""
        metric = OperationMetric(
            task_id=task_id,
            action_id=action_id,
            operation_name=name,
            category=category,
            duration_ms=round(max(0.0, duration_ms), 2),
            success=success,
            error=error[:120] if error else None,
            provider=provider,
            model=model,
            tool_name=tool_name,
            retry_count=retry_count,
            completed_at=utc_now_iso(),
        )

        async with self._lock:
            self._metrics.append(metric)
        return metric

    @asynccontextmanager
    async def profile(
        self,
        name: str,
        category: str = "general",
        task_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async context manager for timing an execution block."""
        t0 = time.monotonic()
        context: Dict[str, Any] = {"success": True, "error": None}
        try:
            yield context
        except Exception as exc:
            context["success"] = False
            context["error"] = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - t0) * 1000
            await self.record_operation(
                name=name,
                category=category,
                duration_ms=duration_ms,
                success=context.get("success", True),
                error=context.get("error"),
                task_id=task_id,
                provider=provider,
                model=model,
                tool_name=tool_name,
            )

    async def get_metrics(self, limit: int = 50, category: Optional[str] = None) -> List[OperationMetric]:
        """Retrieve recent discrete operation metrics."""
        async with self._lock:
            items = list(self._metrics)
        if category:
            items = [m for m in items if m.category == category]
        return items[-limit:]

    async def get_aggregate_metrics(self) -> AggregatePerformanceMetrics:
        """Calculate summary performance statistics across the bounded window."""
        async with self._lock:
            items = list(self._metrics)

        if not items:
            return AggregatePerformanceMetrics()

        total = len(items)
        successes = sum(1 for m in items if m.success)
        failures = total - successes

        durations = sorted([m.duration_ms for m in items])
        avg_lat = round(sum(durations) / total, 2)
        min_lat = round(durations[0], 2)
        max_lat = round(durations[-1], 2)

        # Percentile calculations
        p50_idx = int(0.50 * (total - 1))
        p95_idx = int(0.95 * (total - 1))
        p50_lat = round(durations[p50_idx], 2)
        p95_lat = round(durations[p95_idx], 2)

        # Category duration totals & counts
        category_counts: Dict[str, int] = {}
        category_durations: Dict[str, float] = {}

        for m in items:
            cat = m.category or "general"
            category_counts[cat] = category_counts.get(cat, 0) + 1
            category_durations[cat] = category_durations.get(cat, 0.0) + m.duration_ms

        return AggregatePerformanceMetrics(
            total_operations=total,
            successful_operations=successes,
            failed_operations=failures,
            avg_latency_ms=avg_lat,
            min_latency_ms=min_lat,
            max_latency_ms=max_lat,
            p50_latency_ms=p50_lat,
            p95_latency_ms=p95_lat,
            llm_duration_ms=round(category_durations.get("llm", 0.0), 2),
            tool_duration_ms=round(category_durations.get("tool", 0.0), 2),
            browser_duration_ms=round(category_durations.get("browser", 0.0), 2),
            workflow_duration_ms=round(category_durations.get("workflow", 0.0), 2),
            category_counts=category_counts,
            sampled_window_size=total,
        )

    def clear(self) -> None:
        """Clear recorded in-memory metrics."""
        self._metrics.clear()


# Global singleton instance
runtime_performance_service = RuntimePerformanceService()
performance_service = runtime_performance_service
# Auto-start listener safely
runtime_performance_service.start_event_listener()

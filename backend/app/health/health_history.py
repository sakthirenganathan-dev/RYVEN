"""Bounded in-memory runtime health history for RYVEN Health Engine (M13)."""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from app.health.health_models import (
    HealthCheckResult,
    HealthHistorySummary,
    HealthStatus,
)


class HealthHistoryManager:
    """Maintains bounded historical health probe results without requiring a database."""

    def __init__(self, max_records_per_target: int = 100):
        self.max_records = max_records_per_target
        self._history: Dict[str, deque[HealthCheckResult]] = {}

    def _normalize_key(self, url: str) -> str:
        return url.strip().rstrip("/").lower()

    def record(self, result: HealthCheckResult) -> None:
        """Add a health check result to bounded memory history."""
        key = self._normalize_key(result.url)
        if key not in self._history:
            self._history[key] = deque(maxlen=self.max_records)
        self._history[key].append(result)

    def get_history(self, url: str, limit: int = 20) -> List[HealthCheckResult]:
        """Retrieve recent health check records for an endpoint, most recent first."""
        key = self._normalize_key(url)
        records = self._history.get(key, deque())
        # Return reversed list up to limit
        return list(reversed(records))[:limit]

    def get_summary(self, url: str) -> HealthHistorySummary:
        """Calculate historical uptime percentage and latency summary."""
        key = self._normalize_key(url)
        records = list(self._history.get(key, deque()))

        if not records:
            return HealthHistorySummary(
                url=url,
                total_records=0,
                current_status=HealthStatus.UNKNOWN,
                uptime_percentage=100.0,
                average_response_time_ms=0.0,
                recent_checks=[],
            )

        latest = records[-1]
        healthy_count = sum(1 for r in records if r.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED))
        uptime_pct = round((healthy_count / len(records)) * 100.0, 2)

        valid_latencies = [r.response_time_ms for r in records if r.response_time_ms > 0]
        avg_latency = round(sum(valid_latencies) / len(valid_latencies), 2) if valid_latencies else 0.0

        return HealthHistorySummary(
            url=url,
            total_records=len(records),
            current_status=latest.status,
            uptime_percentage=uptime_pct,
            average_response_time_ms=avg_latency,
            recent_checks=list(reversed(records))[:10],
        )

    def clear(self, url: Optional[str] = None) -> None:
        """Clear history for a specific URL or all endpoints."""
        if url:
            key = self._normalize_key(url)
            self._history.pop(key, None)
        else:
            self._history.clear()


# Global singleton instance
health_history_manager = HealthHistoryManager()

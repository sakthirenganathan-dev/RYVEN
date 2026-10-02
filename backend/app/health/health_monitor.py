"""Runtime Background Health Monitor for RYVEN Health Engine (M13)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.logging_config import logger
from app.health.health_checker import HealthChecker
from app.health.health_models import (
    FailureCode,
    HealthStatus,
    MonitorConfig,
    MonitorStatus,
)
from app.health.health_telemetry import HealthTelemetry


class HealthMonitor:
    """Manages background polling monitors for active endpoints with clean task cancellation."""

    MIN_INTERVAL_SECONDS = 5.0

    def __init__(self, checker: Optional[HealthChecker] = None):
        self.checker = checker or HealthChecker()
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._statuses: Dict[str, MonitorStatus] = {}
        self._configs: Dict[str, MonitorConfig] = {}

    def _normalize_key(self, url: str) -> str:
        return url.strip().rstrip("/").lower()

    async def _monitor_loop(self, url: str, config: MonitorConfig):
        """Async polling loop for a single monitored URL."""
        key = self._normalize_key(url)
        logger.info(f"Health monitor loop started for '{url}' every {config.interval_seconds}s")
        HealthTelemetry.monitor_started(url, config.interval_seconds)

        try:
            while True:
                # 1. Execute check
                result = await self.checker.check(
                    url=url,
                    timeout_seconds=config.timeout_seconds,
                    expected_statuses=config.expected_statuses,
                )

                # 2. Update status tracking
                status_obj = self._statuses.get(key)
                if status_obj:
                    status_obj.total_checks += 1
                    status_obj.last_check_time = datetime.now(timezone.utc).isoformat()
                    status_obj.last_response_time_ms = result.response_time_ms
                    status_obj.last_http_status = result.http_status
                    status_obj.last_failure_code = result.failure_code
                    status_obj.current_status = result.status

                    if result.success:
                        status_obj.consecutive_successes += 1
                        status_obj.consecutive_failures = 0
                    else:
                        status_obj.consecutive_failures += 1
                        status_obj.consecutive_successes = 0

                # 3. Sleep for interval
                await asyncio.sleep(config.interval_seconds)

        except asyncio.CancelledError:
            logger.info(f"Health monitor loop cleanly cancelled for '{url}'")
            HealthTelemetry.monitor_stopped(url)
            raise
        except Exception as e:
            logger.error(f"Unexpected error in health monitor for '{url}': {e}")
            HealthTelemetry.monitor_stopped(url)

    def start(
        self,
        url: str,
        interval_seconds: float = 30.0,
        timeout_seconds: float = 5.0,
    ) -> MonitorStatus:
        """Start a background monitoring task for target URL."""
        key = self._normalize_key(url)

        # Enforce minimum interval
        eff_interval = max(self.MIN_INTERVAL_SECONDS, interval_seconds)

        # Stop existing monitor if already running
        self.stop(url)

        config = MonitorConfig(
            url=url,
            interval_seconds=eff_interval,
            timeout_seconds=timeout_seconds,
        )
        self._configs[key] = config

        status_obj = MonitorStatus(
            url=url,
            is_running=True,
            interval_seconds=eff_interval,
            current_status=HealthStatus.CHECKING,
            consecutive_successes=0,
            consecutive_failures=0,
            total_checks=0,
        )
        self._statuses[key] = status_obj

        # Launch background loop
        task = asyncio.create_task(self._monitor_loop(url, config))
        self._active_tasks[key] = task

        return status_obj

    def stop(self, url: str) -> bool:
        """Stop an active monitoring task cleanly."""
        key = self._normalize_key(url)
        task = self._active_tasks.pop(key, None)
        stopped = False

        if task and not task.done():
            task.cancel()
            stopped = True

        status_obj = self._statuses.get(key)
        if status_obj:
            status_obj.is_running = False

        return stopped

    def get_status(self, url: str) -> Optional[MonitorStatus]:
        """Get current monitor status for target URL."""
        key = self._normalize_key(url)
        return self._statuses.get(key)

    def list_active(self) -> List[MonitorStatus]:
        """List all active monitors."""
        return [s for s in self._statuses.values() if s.is_running]

    def stop_all(self) -> None:
        """Stop all running monitors cleanly (for application shutdown)."""
        for url in list(self._active_tasks.keys()):
            self.stop(url)


# Global singleton instance
health_monitor = HealthMonitor()

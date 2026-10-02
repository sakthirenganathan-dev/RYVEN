"""Unified Health & Deployment Verification Service for RYVEN (Milestone 13)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.health.health_checker import HealthChecker
from app.health.health_history import health_history_manager
from app.health.health_models import (
    HealthCheckResult,
    HealthHistorySummary,
    HealthStatus,
    MonitorStatus,
)
from app.health.health_monitor import health_monitor


class HealthService:
    """High-level service coordinating health checks, history, and monitoring."""

    def __init__(self, checker: Optional[HealthChecker] = None):
        self.checker = checker or HealthChecker()
        self.history = health_history_manager
        self.monitor = health_monitor

    async def check(
        self,
        url: str,
        timeout_seconds: float = 10.0,
        max_attempts: int = 2,
        expected_statuses: Optional[List[int]] = None,
        deployment_id: Optional[str] = None,
        project_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> HealthCheckResult:
        """Perform on-demand health check against any URL."""
        return await self.checker.check(
            url=url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            expected_statuses=expected_statuses,
            deployment_id=deployment_id,
            project_id=project_id,
            provider=provider,
        )

    async def check_deployment(self, deployment_result: Any) -> HealthCheckResult:
        """Check health of a deployment from DeploymentResult object or dict."""
        url = getattr(deployment_result, "deployment_url", None)
        if not url and isinstance(deployment_result, dict):
            url = deployment_result.get("deployment_url")

        deployment_id = getattr(deployment_result, "deployment_id", None)
        if not deployment_id and isinstance(deployment_result, dict):
            deployment_id = deployment_result.get("deployment_id")

        project_name = getattr(deployment_result, "project_name", None)
        if not project_name and isinstance(deployment_result, dict):
            project_name = deployment_result.get("project_name")

        provider = getattr(deployment_result, "provider", None)
        if hasattr(provider, "value"):
            provider = provider.value
        elif not provider and isinstance(deployment_result, dict):
            provider = deployment_result.get("provider")

        if not url:
            from app.health.health_models import FailureCode
            return HealthCheckResult(
                url="",
                status=HealthStatus.UNKNOWN,
                success=False,
                http_status=None,
                response_time_ms=0.0,
                checked_at="",
                failure_code=FailureCode.INVALID_URL_FORMAT,
                safe_error_message="Deployment has no valid deployment_url to verify.",
                deployment_id=deployment_id,
                project_id=project_name,
                provider=provider,
            )

        return await self.check(
            url=url,
            deployment_id=deployment_id,
            project_id=project_name,
            provider=provider,
        )

    def get_status(self, url: str) -> HealthStatus:
        """Get the most recent health status for a URL from history."""
        records = self.history.get_history(url, limit=1)
        if records:
            return records[0].status
        return HealthStatus.UNKNOWN

    def get_history(self, url: str, limit: int = 20) -> List[HealthCheckResult]:
        """Retrieve recent health check history for a target."""
        return self.history.get_history(url, limit=limit)

    def get_summary(self, url: str) -> HealthHistorySummary:
        """Retrieve historical uptime and latency summary for a target."""
        return self.history.get_summary(url)

    def start_monitoring(
        self,
        url: str,
        interval_seconds: float = 30.0,
        timeout_seconds: float = 5.0,
    ) -> MonitorStatus:
        """Start background polling monitor for target URL."""
        return self.monitor.start(
            url=url,
            interval_seconds=interval_seconds,
            timeout_seconds=timeout_seconds,
        )

    def stop_monitoring(self, url: str) -> bool:
        """Stop background monitor for target URL."""
        return self.monitor.stop(url)

    def get_monitor_status(self, url: str) -> Optional[MonitorStatus]:
        """Get current monitor status."""
        return self.monitor.get_status(url)

    def list_monitors(self) -> List[MonitorStatus]:
        """List active monitors."""
        return self.monitor.list_active()


# Global service singleton instance
health_service = HealthService()

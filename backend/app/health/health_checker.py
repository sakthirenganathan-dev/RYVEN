"""Core Health Checker coordinating probing, retries, and failure detection for M13."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from app.core.logging_config import logger
from app.health.failure_detector import FailureDetector
from app.health.health_history import health_history_manager
from app.health.health_models import (
    FailureCode,
    HealthCheckResult,
    HealthStatus,
)
from app.health.health_security import HealthSecurityValidator
from app.health.health_telemetry import HealthTelemetry
from app.health.http_probe import HttpProbe


class HealthChecker:
    """Executes safe health checks against endpoints with deterministic classification and retries."""

    def __init__(self, probe: Optional[HttpProbe] = None):
        self.probe = probe or HttpProbe(timeout_seconds=10.0)

    async def check(
        self,
        url: str,
        timeout_seconds: float = 10.0,
        max_attempts: int = 2,
        expected_statuses: Optional[List[int]] = None,
        deployment_id: Optional[str] = None,
        project_id: Optional[str] = None,
        provider: Optional[str] = None,
        environment: str = "production",
    ) -> HealthCheckResult:
        """Perform full health check with SSRF validation, optional retry, and failure classification."""
        checked_at = datetime.now(timezone.utc).isoformat()
        HealthTelemetry.check_started(url)

        # 1. SSRF & Scheme Validation
        is_safe, sanitized_url, sec_err = HealthSecurityValidator.validate_url(url)
        if not is_safe:
            result = HealthCheckResult(
                url=url,
                status=HealthStatus.BLOCKED,
                success=False,
                http_status=None,
                response_time_ms=0.0,
                checked_at=checked_at,
                failure_code=FailureCode.SSRF_BLOCKED,
                safe_error_message=sec_err,
                deployment_id=deployment_id,
                project_id=project_id,
                provider=provider,
                environment=environment,
                attempts=1,
            )
            health_history_manager.record(result)
            HealthTelemetry.check_completed(url, result.status.value, None, 0.0, result.failure_code.value)
            return result

        # 2. Probe with transient retry policy (max 2 attempts)
        attempts_run = 0
        last_probe_res = None

        for attempt in range(1, max_attempts + 1):
            attempts_run = attempt
            last_probe_res = await self.probe.probe(sanitized_url, timeout=timeout_seconds)

            # If success or client error (4xx) or server error (5xx), do not retry — response is deterministic
            if last_probe_res.get("success") or last_probe_res.get("http_status") is not None:
                break

            # If transient timeout/network error and more attempts remain, small backoff
            if attempt < max_attempts:
                await asyncio.sleep(0.2)

        # 3. Classify outcome deterministically
        http_status = last_probe_res.get("http_status")
        resp_time_ms = last_probe_res.get("response_time_ms", 0.0)
        probe_fail_code = last_probe_res.get("failure_code", FailureCode.NONE)
        probe_err = last_probe_res.get("error_message", "")

        status, failure_code, diagnostic_msg = FailureDetector.classify(
            http_status=http_status,
            response_time_ms=resp_time_ms,
            probe_failure_code=probe_fail_code,
            probe_error=probe_err,
            expected_statuses=expected_statuses,
        )

        latency_tier = FailureDetector.classify_latency(resp_time_ms)
        is_success = status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

        result = HealthCheckResult(
            url=sanitized_url,
            status=status,
            success=is_success,
            http_status=http_status,
            response_time_ms=resp_time_ms,
            latency_tier=latency_tier,
            checked_at=checked_at,
            failure_code=failure_code,
            safe_error_message=diagnostic_msg,
            deployment_id=deployment_id,
            project_id=project_id,
            provider=provider,
            environment=environment,
            content_type=last_probe_res.get("content_type"),
            headers_checked=last_probe_res.get("headers", {}),
            attempts=attempts_run,
        )

        # 4. Save to bounded history & emit telemetry
        health_history_manager.record(result)
        HealthTelemetry.check_completed(
            sanitized_url,
            result.status.value,
            result.http_status,
            result.response_time_ms,
            result.failure_code.value,
        )

        return result

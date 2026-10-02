"""Deterministic Failure and Health Status Classifier for RYVEN Health Engine (M13)."""

from __future__ import annotations

from typing import List, Optional, Tuple
from app.health.health_models import FailureCode, HealthStatus, LatencyTier


class FailureDetector:
    """Classifies raw probe results into deterministic health statuses and failure codes."""

    # Latency thresholds (configurable)
    FAST_LATENCY_MAX_MS = 500.0
    MODERATE_LATENCY_MAX_MS = 2000.0

    @classmethod
    def classify_latency(cls, response_time_ms: float) -> LatencyTier:
        """Classify latency tier based on response duration in milliseconds."""
        if response_time_ms <= 0:
            return LatencyTier.UNKNOWN
        if response_time_ms < cls.FAST_LATENCY_MAX_MS:
            return LatencyTier.FAST
        if response_time_ms <= cls.MODERATE_LATENCY_MAX_MS:
            return LatencyTier.MODERATE
        return LatencyTier.SLOW

    @classmethod
    def classify(
        cls,
        http_status: Optional[int],
        response_time_ms: float,
        probe_failure_code: FailureCode,
        probe_error: str = "",
        expected_statuses: Optional[List[int]] = None,
    ) -> Tuple[HealthStatus, FailureCode, str]:
        """Deterministically determine HealthStatus, FailureCode, and safe diagnostic message.
        
        Returns:
            (status: HealthStatus, failure_code: FailureCode, diagnostic_message: str)
        """
        allowed_statuses = expected_statuses or [200, 201, 204, 301, 302, 307, 308]

        # 1. Security / SSRF blocks
        if probe_failure_code == FailureCode.SSRF_BLOCKED:
            return (
                HealthStatus.BLOCKED,
                FailureCode.SSRF_BLOCKED,
                f"Health check blocked by security policy: {probe_error}",
            )

        # 2. Timeout
        if probe_failure_code == FailureCode.CONNECTION_TIMEOUT:
            return (
                HealthStatus.TIMEOUT,
                FailureCode.CONNECTION_TIMEOUT,
                f"Endpoint connection timed out ({probe_error}).",
            )

        # 3. Network unreachable / DNS failure / Refused
        if probe_failure_code in (
            FailureCode.CONNECTION_REFUSED,
            FailureCode.DNS_RESOLUTION_FAILED,
            FailureCode.UNREACHABLE,
        ):
            return (
                HealthStatus.UNREACHABLE,
                probe_failure_code,
                f"Endpoint is unreachable: {probe_error}",
            )

        # 4. HTTP Status Classification
        if http_status is not None:
            # 2xx Success
            if 200 <= http_status < 300:
                latency = cls.classify_latency(response_time_ms)
                if latency == LatencyTier.SLOW:
                    return (
                        HealthStatus.DEGRADED,
                        FailureCode.NONE,
                        f"Endpoint is healthy but response is slow (HTTP {http_status} in {response_time_ms:.1f}ms).",
                    )
                return (
                    HealthStatus.HEALTHY,
                    FailureCode.NONE,
                    f"Endpoint is healthy (HTTP {http_status} in {response_time_ms:.1f}ms).",
                )

            # 3xx Redirects
            if 300 <= http_status < 400:
                if http_status in allowed_statuses:
                    return (
                        HealthStatus.HEALTHY,
                        FailureCode.NONE,
                        f"Endpoint is reachable via redirect (HTTP {http_status} in {response_time_ms:.1f}ms).",
                    )
                return (
                    HealthStatus.DEGRADED,
                    FailureCode.HTTP_REDIRECT_LOOP,
                    f"Endpoint returned unhandled redirect status HTTP {http_status}.",
                )

            # 4xx Client Errors
            if 400 <= http_status < 500:
                # 401 Unauthorized / 403 Forbidden indicate service is live and reachable, but protected
                if http_status in (401, 403):
                    return (
                        HealthStatus.DEGRADED,
                        FailureCode.HTTP_4XX_CLIENT_ERROR,
                        f"Endpoint is reachable but requires authentication (HTTP {http_status}).",
                    )
                return (
                    HealthStatus.UNHEALTHY,
                    FailureCode.HTTP_4XX_CLIENT_ERROR,
                    f"Endpoint returned client error status HTTP {http_status}.",
                )

            # 5xx Server Errors
            if 500 <= http_status < 600:
                return (
                    HealthStatus.UNHEALTHY,
                    FailureCode.HTTP_5XX_SERVER_ERROR,
                    f"Endpoint returned server error status HTTP {http_status}.",
                )

        # 5. Generic Probe Exception Fallback
        return (
            HealthStatus.FAILED,
            FailureCode.PROBE_EXCEPTION,
            f"Health check probe failed: {probe_error or 'Unknown probe error'}",
        )

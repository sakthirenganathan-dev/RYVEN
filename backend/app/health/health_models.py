"""Strongly typed models and enums for RYVEN 2.0 Milestone 13 Health & Verification Engine."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    """Lifecycle and operational health status of an endpoint."""
    UNKNOWN = "UNKNOWN"
    CHECKING = "CHECKING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNREACHABLE = "UNREACHABLE"
    TIMEOUT = "TIMEOUT"
    INVALID_URL = "INVALID_URL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class FailureCode(str, Enum):
    """Deterministic failure classification codes."""
    NONE = "NONE"
    HTTP_4XX_CLIENT_ERROR = "HTTP_4XX_CLIENT_ERROR"
    HTTP_5XX_SERVER_ERROR = "HTTP_5XX_SERVER_ERROR"
    HTTP_REDIRECT_LOOP = "HTTP_REDIRECT_LOOP"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"
    CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
    READ_TIMEOUT = "READ_TIMEOUT"
    UNREACHABLE = "UNREACHABLE"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    INVALID_SCHEME = "INVALID_SCHEME"
    INVALID_URL_FORMAT = "INVALID_URL_FORMAT"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    PROBE_EXCEPTION = "PROBE_EXCEPTION"


class LatencyTier(str, Enum):
    """Latency tier classification."""
    FAST = "FAST"          # < 500 ms
    MODERATE = "MODERATE"  # 500 ms - 2000 ms
    SLOW = "SLOW"          # > 2000 ms
    UNKNOWN = "UNKNOWN"


class HealthCheckResult(BaseModel):
    """Structured result of a single endpoint health probe."""
    url: str
    status: HealthStatus = HealthStatus.UNKNOWN
    success: bool = False
    http_status: Optional[int] = None
    response_time_ms: float = 0.0
    latency_tier: LatencyTier = LatencyTier.UNKNOWN
    checked_at: str
    failure_code: FailureCode = FailureCode.NONE
    safe_error_message: str = ""
    deployment_id: Optional[str] = None
    project_id: Optional[str] = None
    provider: Optional[str] = None
    environment: str = "production"
    verification_source: str = "health_probe"
    content_type: Optional[str] = None
    headers_checked: Dict[str, str] = Field(default_factory=dict)
    attempts: int = 1


class MonitorConfig(BaseModel):
    """Configuration for background runtime health monitoring."""
    url: str
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    max_history_entries: int = 100
    healthy_threshold: int = 2
    unhealthy_threshold: int = 3
    expected_statuses: List[int] = Field(default_factory=lambda: [200, 201, 204, 301, 302, 307, 308])


class MonitorStatus(BaseModel):
    """Current state of a running health monitor."""
    url: str
    is_running: bool
    interval_seconds: float
    current_status: HealthStatus
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    total_checks: int = 0
    last_check_time: Optional[str] = None
    last_response_time_ms: float = 0.0
    last_http_status: Optional[int] = None
    last_failure_code: FailureCode = FailureCode.NONE


class HealthHistorySummary(BaseModel):
    """Historical health summary for an endpoint."""
    url: str
    total_records: int
    current_status: HealthStatus
    uptime_percentage: float = 100.0
    average_response_time_ms: float = 0.0
    recent_checks: List[HealthCheckResult] = Field(default_factory=list)
